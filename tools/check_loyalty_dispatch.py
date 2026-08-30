"""Ratchet: the number of loyalty abilities the engine cannot execute may
only SHRINK.

`engine/planeswalker_manager.py::activate_planeswalker` used to dispatch a
planeswalker's loyalty effect through a hand-written chain of substring
tests against invented vocabulary — "bounce", "brainstorm", "cast sorceries
as flash", "exile opponent library" — none of which occurs on any card in
the pool.  576 of 696 parsed loyalty abilities (82.8%) matched no branch:
the loyalty was paid and NOTHING resolved.  Root cause, census and the
measured A/B:
`docs/diagnostics/2026-08-30_azorius_planeswalker_loyalty_noop_root_cause.md`.

The replacement classifies each printed loyalty ability ONCE at DB load
(`engine.oracle_parser.parse_loyalty_abilities` →
`CardTemplate.loyalty_abilities`) into a closed `LoyaltyEffectKind` set
with an explicit `UNCLASSIFIED` escape hatch.  Unclassified abilities are
refused before the loyalty is paid, so they are visible-but-inert rather
than silently no-op — but they are still missing mechanics, and this
ratchet keeps the count moving in one direction only:

    count >  baseline  -> regression: a mechanic stopped classifying, or a
                          new refusal was introduced.  Fix the parser.
    count <  baseline  -> a family was implemented (the intended direction).
                          Lower the baseline in the same commit.
    count == baseline  -> pass.

Usage:
    python tools/check_loyalty_dispatch.py [--list] [--families]
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
DEFAULT_BASELINE = ROOT / "tools" / "loyalty_dispatch_baseline.json"


def census(db=None) -> dict:
    """Classify every printed loyalty ability in the card pool.

    Returns ``{'total': int, 'unclassified': int, 'by_kind': Counter,
    'unclassified_texts': [(card_name, slot, text), ...]}``.

    ``db`` lets the pytest wrapper hand in the suite's shared
    ``CardDatabase`` instead of paying a second ~7s load.
    """
    sys.path.insert(0, str(ROOT))
    from engine.cards import LoyaltyEffectKind

    if db is None:
        from engine.card_database import CardDatabase
        db = CardDatabase()
    cards = getattr(db, "cards", None) or getattr(db, "templates")

    by_kind: collections.Counter = collections.Counter()
    unclassified_texts: list[tuple[str, str, str]] = []
    total = 0
    for name, template in cards.items():
        abilities = template.loyalty_abilities or {}
        for slot, ability in abilities.items():
            total += 1
            by_kind[ability.effect_kind.value] += 1
            if ability.effect_kind is LoyaltyEffectKind.UNCLASSIFIED:
                unclassified_texts.append((name, slot, ability.text))

    return {
        "total": total,
        "unclassified": by_kind[LoyaltyEffectKind.UNCLASSIFIED.value],
        "by_kind": by_kind,
        "unclassified_texts": unclassified_texts,
    }


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    result = census()
    total = result["total"]
    unclassified = result["unclassified"]

    if "--families" in args or "--list" in args:
        print(f"parsed loyalty abilities: {total}")
        for kind, count in result["by_kind"].most_common():
            print(f"  {count:5d}  {kind}")
    if "--list" in args:
        print("\nunclassified (refused before the loyalty is paid):")
        for name, slot, text in sorted(result["unclassified_texts"]):
            print(f"  [{slot}] {name}: {text}")

    with DEFAULT_BASELINE.open() as f:
        baseline = json.load(f)
    allowed = int(baseline["unclassified"])

    if unclassified > allowed:
        print(
            f"FAIL: unclassified loyalty abilities grew — {unclassified} "
            f"(baseline {allowed}).\nA loyalty mechanic stopped "
            f"classifying, or a new refusal was added.  Every ability in "
            f"this set pays no loyalty and does nothing; the count must "
            f"only shrink.\n"
            f"To see them: python tools/check_loyalty_dispatch.py --list"
        )
        return 1

    if unclassified < allowed:
        print(
            f"FAIL: baseline is stale — {unclassified} unclassified "
            f"loyalty abilities, baseline says {allowed}.\nYou implemented "
            f"a loyalty family (good, that is the intended direction).  "
            f"Record it: set \"unclassified\" to {unclassified} in "
            f"{DEFAULT_BASELINE.name} in this same commit."
        )
        return 1

    print(
        f"Loyalty-dispatch ratchet OK — {unclassified} unclassified of "
        f"{total} parsed (baseline = {allowed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
