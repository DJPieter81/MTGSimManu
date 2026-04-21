"""Audit every [BLOCK] / [BLOCK-EMERGENCY] event across the batch.

For each block, captures:
  - seed, turn, blocker side
  - blocker, attacker (with P/T)
  - my_life, opp_life at the point of block (best-effort from nearest turn header)
  - my_board, opp_board at the point of block
  - reason tag emitted by the block logic
  - classification: chump_type bucket

Outputs a markdown table + summary by bucket.
"""
from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

SEEDS = [63500, 64000, 64500, 65000, 65500, 66000]
REPLAY_DIR = Path("replays")

BLOCK_RE = re.compile(
    r"T(?P<turn>\d+) P(?P<side>\d+):\s+"
    r"\[(?P<mode>BLOCK|BLOCK-EMERGENCY)\]\s+"
    r"(?P<blocker>.+?)\s+\((?P<bp>\d+)/(?P<bt>\d+)\)\s+"
    r"blocks\s+"
    r"(?P<attacker>.+?)\s+\((?P<ap>\d+)/(?P<at>\d+)\)\s+"
    r"[—-]+\s+(?P<reason>.+)"
)

TURN_HEADER_RE = re.compile(
    r"TURN (?P<turn>\d+)\s+[—-]+\s+(?P<player>.+?)\s+\(P(?P<side>\d+)\)"
)
LIFE_LINE_RE = re.compile(r"║ Life:\s+(?P<p1name>.+?)\s+(?P<p1life>\d+)\s+\|\s+(?P<p2name>.+?)\s+(?P<p2life>\d+)")


@dataclass
class BlockEvent:
    seed: int
    turn: int
    side: int  # 1 or 2
    mode: str
    blocker: str
    bp: int
    bt: int
    attacker: str
    ap: int
    at: int
    reason: str
    my_life: Optional[int] = None
    opp_life: Optional[int] = None
    line_no: int = 0


def parse_file(seed: int) -> list[BlockEvent]:
    path = REPLAY_DIR / f"boros_vs_affinity_trace_s{seed}.txt"
    lines = path.read_text().splitlines()
    events = []

    # Track last-seen life totals by side
    p1_life, p2_life = 20, 20

    for idx, line in enumerate(lines):
        m = LIFE_LINE_RE.search(line)
        if m:
            p1_life = int(m.group("p1life"))
            p2_life = int(m.group("p2life"))
            continue

        bm = BLOCK_RE.search(line)
        if bm:
            side = int(bm.group("side"))
            my_life = p1_life if side == 1 else p2_life
            opp_life = p2_life if side == 1 else p1_life
            events.append(BlockEvent(
                seed=seed,
                turn=int(bm.group("turn")),
                side=side,
                mode=bm.group("mode"),
                blocker=bm.group("blocker"),
                bp=int(bm.group("bp")),
                bt=int(bm.group("bt")),
                attacker=bm.group("attacker"),
                ap=int(bm.group("ap")),
                at=int(bm.group("at")),
                reason=bm.group("reason").strip(),
                my_life=my_life,
                opp_life=opp_life,
                line_no=idx + 1,
            ))
    return events


def classify(ev: BlockEvent) -> str:
    """Bucket each block into a root-cause category."""
    kills = ev.bp >= ev.at
    survives = ev.ap < ev.bt
    dmg_saved = ev.ap
    life_after_if_no_block = (ev.my_life or 20) - dmg_saved

    # Bucket A: pure chump (blocker dies, doesn't kill) where damage alone wasn't near-lethal
    if not kills and not survives:
        if life_after_if_no_block > 10 and ev.ap < (ev.my_life or 20) // 2:
            return "A. Premature chump (no lethal threat, blocker dies for ~{}-turn delay)".format(
                1 if ev.ap > 0 else 0
            )
        if ev.bp == 0 and ev.ap > 0:
            # 0-power blocker blocking a non-lethal = pure wasted card
            return "B. 0-power blocker burned for {}-dmg delay".format(ev.ap)
        if ev.ap >= 10 and ev.bp < ev.at:
            # Scaled attacker (likely plating): blocker dies to single swing, attacker still 1-shots next turn
            return "C. Chump into scaled attacker (P>=10) — delays 1 turn, attacker re-swings next"
        return "D. Emergency chump (genuine near-lethal defense)"

    # Bucket E: valid trade where blocker kills attacker but also dies
    if kills and not survives:
        # Is this a loss of tempo? If blocker_value > attacker_value, it's bad.
        # Heuristic: tokens are cheap; named creatures with ETB/triggers are expensive.
        blocker_is_token = "Token" in ev.blocker
        attacker_is_token = "Token" in ev.attacker
        if not blocker_is_token and attacker_is_token:
            return "E. Named blocker traded into token (bad value)"
        return "F. 1-for-1 trade"

    # Favorable (blocker kills + survives)
    if kills and survives:
        return "G. Favorable trade (good)"

    # Survives but doesn't kill (deathless wall-style)
    if survives and not kills:
        return "H. Tarpit block (survives, doesn't kill)"

    return "Z. Unclassified"


def main():
    all_events: list[BlockEvent] = []
    for seed in SEEDS:
        all_events.extend(parse_file(seed))

    # Emit structured table
    print(f"# Block audit across {len(SEEDS)} replays — {len(all_events)} block events\n")

    buckets = {}
    for ev in all_events:
        b = classify(ev)
        buckets.setdefault(b, []).append(ev)

    print("## Summary by bucket\n")
    print("| Bucket | Count | % |")
    print("|--------|-------|---|")
    for b in sorted(buckets, key=lambda x: -len(buckets[x])):
        n = len(buckets[b])
        pct = 100 * n / len(all_events)
        print(f"| {b} | {n} | {pct:.0f}% |")

    print("\n## Detail by bucket\n")
    for b in sorted(buckets, key=lambda x: -len(buckets[x])):
        print(f"### {b}  ({len(buckets[b])})\n")
        print("| seed | T | side | blocker (P/T) | attacker (P/T) | my life | opp life | mode | reason |")
        print("|------|---|------|---------------|----------------|---------|----------|------|--------|")
        for ev in buckets[b]:
            side_name = "Boros" if ev.side == 1 else "Affinity"
            print(f"| s{ev.seed} | T{ev.turn} | {side_name} | "
                  f"{ev.blocker} ({ev.bp}/{ev.bt}) | {ev.attacker} ({ev.ap}/{ev.at}) | "
                  f"{ev.my_life} | {ev.opp_life} | {ev.mode} | {ev.reason} |")
        print()


if __name__ == "__main__":
    main()
