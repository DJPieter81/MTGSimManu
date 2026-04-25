"""Replay snapshot parser — extracts per-turn snapshots from
`replays/*.txt` for use in calibrating the universal P_win evaluator.

Each replay file is a Bo3 (or Bo1) match log produced by
`run_meta.py --bo3`.  Per-game structure:

    GAME N: <DeckA> (P1) vs <DeckB> (P2)  -  seed S
    ...
    ╔══ TURN K — <DeckName> (P<idx>) ══...
    ║ Life: <active> X  |  <opp> Y
    ║ Hand: H1 cards  |  Opp hand: H2 cards
    ║ Lands: L1  |  Opp lands: L2
    ║ Library: LB  |  Graveyard: GY
    ║ <ActiveDeck> board: ...
    ║ <OppDeck>  board: ...
    ...
    >>> <DeckName> wins Game N on turn T via <method>

This module reads those headers (no engine introspection needed) and
yields per-snapshot dicts keyed in P1/P2 order.  P1 is the player on
the play (per the GAME header), so values are stored absolutely —
not relative to the active player of that turn.

The schema is versioned via REPLAY_FORMAT_V; bumping it signals the
calibrator to retrain.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional


REPLAY_FORMAT_V = 1


# ─────────────────────────────────────────────────────────────
# Regexes — anchored to the actual log shape
# ─────────────────────────────────────────────────────────────
_GAME_HDR = re.compile(
    r"^\s*GAME\s+(\d+):\s+(.+?)\s+\(P1\)\s+vs\s+(.+?)\s+\(P2\)\s+(?:—|-)\s+seed"
)
_TURN_HDR = re.compile(
    r"^╔══\s*TURN\s+(\d+)\s*(?:—|-)\s*(.+?)\s*\(P([12])\)"
)
_LIFE = re.compile(r"^║\s*Life:\s+(.+?)\s+(-?\d+)\s*\|\s*(.+?)\s+(-?\d+)")
_HAND = re.compile(r"^║\s*Hand:\s+(\d+)\s+cards\s*\|\s*Opp\s+hand:\s+(\d+)\s+cards")
_LANDS = re.compile(r"^║\s*Lands:\s+(\d+)\s*\|\s*Opp\s+lands:\s+(\d+)")
_LIBGY = re.compile(r"^║\s*Library:\s+(\d+)\s*\|\s*Graveyard:\s+(\d+)")
# Opp library/graveyard line is optional in current logs — we don't rely on it.

_WIN = re.compile(r"^>>>\s+(.+?)\s+wins\s+Game\s+(\d+)\b")


def _parse_game_block(
    file_name: str,
    game_idx: int,
    p1_deck: str,
    p2_deck: str,
    lines: List[str],
) -> Iterator[Dict]:
    """Yield raw snapshot dicts for one game block.  Game winner is
    determined from a trailing ">>> <deck> wins Game N" line in `lines`.
    """
    # Determine winner of this game
    p1_won: Optional[bool] = None
    for line in lines:
        m = _WIN.match(line)
        if m and int(m.group(2)) == game_idx:
            winner = m.group(1).strip()
            if winner == p1_deck:
                p1_won = True
            elif winner == p2_deck:
                p1_won = False
            else:
                # Unknown winner string (shouldn't happen); skip game
                return
            break
    if p1_won is None:
        # No win line in this block — partial game, skip
        return

    # Walk the block emitting one snapshot per ╔══ TURN ... header.
    n = len(lines)
    i = 0
    while i < n:
        m = _TURN_HDR.match(lines[i])
        if not m:
            i += 1
            continue
        turn = int(m.group(1))
        active_deck = m.group(2).strip()
        active_pidx = int(m.group(3))  # 1 or 2

        # Pull the next 4 attribute lines (║ Life / Hand / Lands / Library).
        life = hand = lands = libgy = None
        j = i + 1
        # Scan up to 8 lines forward for the four attribute lines.
        end = min(n, i + 12)
        while j < end:
            ln = lines[j]
            if life is None and _LIFE.match(ln):
                life = _LIFE.match(ln)
            elif hand is None and _HAND.match(ln):
                hand = _HAND.match(ln)
            elif lands is None and _LANDS.match(ln):
                lands = _LANDS.match(ln)
            elif libgy is None and _LIBGY.match(ln):
                libgy = _LIBGY.match(ln)
                # Library/Graveyard is the last attribute we need.
                break
            j += 1

        if not (life and hand and lands and libgy):
            i += 1
            continue

        # The Life line shows "<active_name> X  |  <opp_name> Y".
        active_name = life.group(1).strip()
        active_life = int(life.group(2))
        other_name = life.group(3).strip()
        other_life = int(life.group(4))

        # Sanity: which side is the active deck?
        if active_name == p1_deck:
            life_p1, life_p2 = active_life, other_life
        elif active_name == p2_deck:
            life_p1, life_p2 = other_life, active_life
        else:
            # Couldn't reconcile — skip this snapshot
            i = j + 1
            continue

        # Hand, Lands, Library, Graveyard are reported from the ACTIVE
        # player's perspective: "Hand: <my> | Opp hand: <opp>".
        active_hand = int(hand.group(1))
        opp_hand = int(hand.group(2))
        active_lands = int(lands.group(1))
        opp_lands = int(lands.group(2))
        active_lib = int(libgy.group(1))
        active_gy = int(libgy.group(2))

        if active_pidx == 1:
            hand_p1, hand_p2 = active_hand, opp_hand
            lands_p1, lands_p2 = active_lands, opp_lands
            lib_p1, lib_p2 = active_lib, 0
            gy_p1, gy_p2 = active_gy, 0
        else:
            hand_p1, hand_p2 = opp_hand, active_hand
            lands_p1, lands_p2 = opp_lands, active_lands
            lib_p1, lib_p2 = 0, active_lib
            gy_p1, gy_p2 = 0, active_gy

        yield {
            "file": file_name,
            "game_idx": game_idx,
            "turn": turn,
            "p1_arch": p1_deck,
            "p2_arch": p2_deck,
            "life_p1": life_p1,
            "life_p2": life_p2,
            "hand_p1": hand_p1,
            "hand_p2": hand_p2,
            "lands_p1": lands_p1,
            "lands_p2": lands_p2,
            "lib_p1": lib_p1,
            "lib_p2": lib_p2,
            "gy_p1": gy_p1,
            "gy_p2": gy_p2,
            "p1_won": p1_won,
        }
        i = j + 1


def parse_replay_file(path) -> Iterator[Dict]:
    """Yield turn-snapshot dicts for one replay file."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    lines = text.splitlines()

    # Find game blocks: each starts at a "GAME N:" header and runs
    # through to (but not including) the next GAME header.
    game_starts: List[int] = []
    for idx, line in enumerate(lines):
        if _GAME_HDR.match(line):
            game_starts.append(idx)
    if not game_starts:
        return
    game_starts.append(len(lines))  # sentinel

    file_name = p.name
    for k in range(len(game_starts) - 1):
        start, end = game_starts[k], game_starts[k + 1]
        block = lines[start:end]
        m = _GAME_HDR.match(block[0])
        if not m:
            continue
        game_idx = int(m.group(1))
        p1_deck = m.group(2).strip()
        p2_deck = m.group(3).strip()
        yield from _parse_game_block(file_name, game_idx, p1_deck, p2_deck, block)


def parse_replays_dir(dir_path) -> Iterator[Dict]:
    """Iterate snapshot dicts across every *.txt in a directory."""
    d = Path(dir_path)
    for f in sorted(d.glob("*.txt")):
        yield from parse_replay_file(f)


def _cli(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Parse replay snapshots into CSV")
    ap.add_argument("dir", help="Directory containing *.txt replay files")
    ap.add_argument("--out", default="snapshots.csv", help="Output CSV path")
    args = ap.parse_args(argv)

    fields = [
        "file", "game_idx", "turn",
        "p1_arch", "p2_arch",
        "life_p1", "life_p2",
        "hand_p1", "hand_p2",
        "lands_p1", "lands_p2",
        "lib_p1", "lib_p2",
        "gy_p1", "gy_p2",
        "p1_won",
    ]
    rows = list(parse_replays_dir(args.dir))
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"Wrote {len(rows)} snapshots → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
