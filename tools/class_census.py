#!/usr/bin/env python3
"""Size a mechanic class before writing any engine/AI change.

Counts the cards whose oracle text (any face) matches a regex, across the
whole card pool and across the registered decks, and optionally checks a
typed CardTemplate field against the same class. This is step 1 of the
`abstract-first` skill (.claude/skills/abstract-first/SKILL.md): a change
that fewer than ~10 pool cards can reach is a patch, not a mechanic.

    python tools/class_census.py "can't cast (noncreature )?spells this turn"
    python tools/class_census.py "draws? seven cards" --field hand_refill
    python tools/class_census.py "gains double strike until end of turn" --show 20

Output: pool count, registered-deck count (copies and which decks), sample
names, and — with --field — how many matches the typed field populates and
which matching cards it misses (the parser's coverage gap).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _faces_text(entry) -> str:
    faces = entry if isinstance(entry, list) else [entry]
    return "\n".join((f.get("text") or "") for f in faces).lower()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pattern", help="regex matched against lower-cased oracle text")
    ap.add_argument("--field", help="CardTemplate field that should type this class")
    ap.add_argument("--show", type=int, default=10, help="sample names to print")
    args = ap.parse_args()

    rx = re.compile(args.pattern)
    with open(os.path.join(ROOT, "ModernAtomic.json")) as fh:
        data = json.load(fh)["data"]
    pool = sorted(name for name, entry in data.items() if rx.search(_faces_text(entry)))

    from decks.modern_meta import MODERN_DECKS
    in_decks = {}
    for deck, lists in MODERN_DECKS.items():
        for zone in ("mainboard", "sideboard"):
            for card, n in (lists.get(zone) or {}).items():
                if card in pool:
                    in_decks.setdefault(card, []).append(f"{deck} ({zone[0].upper()}B x{n})")

    print(f"pattern: {args.pattern!r}")
    print(f"pool: {len(pool)} card(s)")
    for name in pool[:args.show]:
        print(f"  {name}")
    if len(pool) > args.show:
        print(f"  … {len(pool) - args.show} more")
    copies = sum(int(s.rsplit('x', 1)[1].rstrip(')')) for v in in_decks.values() for s in v)
    print(f"registered decks: {len(in_decks)} card(s), {copies} copies")
    for name, where in sorted(in_decks.items()):
        print(f"  {name}: {', '.join(where)}")

    if args.field:
        from engine.card_database import CardDatabase
        db = CardDatabase()
        typed = [n for n in pool if db.get_card(n) is not None
                 and getattr(db.get_card(n), args.field, None)]
        missed = [n for n in pool if n not in typed and db.get_card(n) is not None]
        print(f"field {args.field}: populated on {len(typed)} of {len(pool)} matches")
        for name in missed[:args.show]:
            print(f"  missed: {name}")

    verdict = "mechanic-sized" if len(pool) >= 10 else "BELOW the class-size rule (<10) — find the wider mechanic"
    print(f"verdict: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
