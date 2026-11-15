#!/usr/bin/env python3
"""Owner-proxy ratchet — blind-spot class B1
(docs/design/2026-11-15_root_cause_blindspot_analysis.md).

A question the engine owns in one place is re-answered elsewhere with a
cheaper proxy. The proxy is right on the common board and wrong at the
edge. In 2026-11, six defects shared this shape: a colour set for pips, a
creature count for "has a legal target", a mana total for colours,
`produces_mana` for the colours a land taps for, `trigger_etb` for
entering, and hand-written zone moves. This ratchet counts each known proxy
per file and fails when a count GROWS over
`tools/owner_proxy_baseline.json`.

  target_count     `opp_creature_count == 0` / `not opp.creatures` used as
                   "has a target" in ai/. Owner: target_solver.can_be_targeted
                   (through ai.board_eval._legal_removal_targets).
  tap_colours      `produces_mana` read in ai/. Owner: ManaPayment.
                   land_mana_units for what a source taps for (ai.
                   deferred_costs.tap_colours). Printed data also lists an
                   enter-only colour.
  mana_total_pay   `available_mana_estimate <` / `>=` compared against a cost
                   in engine/. Owner: CastManager.mana_payable (quantity and
                   colour).
  enter_queue      `.trigger_etb(` outside the enter owner. Owner:
                   GameState._handle_permanent_etb (CR 603.6a).

A legitimate use (a blocker count in combat, a board wipe that does not
target, a colour-identity read for deck planning) carries
`# owner-proxy-allow: <reason>` on its line. Reducing a count lowers the
baseline in the same commit (`--update`); growing one fails.

    python tools/check_owner_proxies.py            # check (CI + pytest)
    python tools/check_owner_proxies.py --list     # every hit
    python tools/check_owner_proxies.py --update   # rewrite the baseline
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = REPO_ROOT / "tools" / "owner_proxy_baseline.json"
ALLOW = "owner-proxy-allow:"

# category -> (directories scanned, regex, files that ARE the owner)
CATEGORIES = {
    "target_count": (("ai",),
                     re.compile(r"opp_creature_count\s*==\s*0|not\s+opp\.creatures\b"),
                     ()),
    "tap_colours": (("ai",), re.compile(r"\bproduces_mana\b"),
                    ("ai/deferred_costs.py",)),
    "mana_total_pay": (("engine",),
                       re.compile(r"available_mana_estimate\s*(<|>=)"),
                       ("engine/cast_manager.py",)),
    "enter_queue": (("engine", "ai"), re.compile(r"\.trigger_etb\("),
                    ("engine/spell_resolution.py", "engine/game_state.py",
                     "engine/triggers.py")),
}


def hits():
    out = {}
    for cat, (dirs, rx, owners) in CATEGORIES.items():
        found = []
        for d in dirs:
            for path in sorted((REPO_ROOT / d).rglob("*.py")):
                rel = path.relative_to(REPO_ROOT).as_posix()
                if rel in owners:
                    continue
                for n, line in enumerate(path.read_text().splitlines(), 1):
                    if rx.search(line) and ALLOW not in line:
                        found.append(f"{rel}:{n}: {line.strip()}")
        out[cat] = found
    return out


def counts(found):
    return {cat: len(v) for cat, v in found.items()}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    found = hits()
    now = counts(found)
    if "--list" in args:
        for cat, lines in found.items():
            print(f"{cat}: {len(lines)}")
            for line in lines:
                print(f"    {line}")
        return 0
    if "--update" in args:
        BASELINE_FILE.write_text(json.dumps(
            {"description": "Owner-proxy ratchet (blind-spot class B1): "
                            "counts may only fall. See "
                            "tools/check_owner_proxies.py.", **now},
            indent=2, sort_keys=True) + "\n")
        print(f"wrote {BASELINE_FILE}: {now}")
        return 0
    base = json.loads(BASELINE_FILE.read_text())
    grew = {c: (base.get(c, 0), n) for c, n in now.items()
            if n > base.get(c, 0)}
    if grew:
        print("Owner-proxy ratchet FAILED (a second path re-answers an owned "
              "question; route it to the owner or annotate "
              f"'# {ALLOW} <reason>'):")
        for c, (b, n) in grew.items():
            print(f"  {c}: {b} -> {n}")
            for line in found[c]:
                print(f"      {line}")
        return 1
    stale = {c: (base.get(c, 0), n) for c, n in now.items()
             if n < base.get(c, 0)}
    if stale:
        print("Owner-proxy ratchet: counts fell -- lower the baseline in "
              f"this commit (--update): {stale}")
        return 1
    print(f"Owner-proxy ratchet OK -- {now}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
