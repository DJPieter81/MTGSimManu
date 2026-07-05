#!/usr/bin/env python3
"""Classifier-coverage gate — no silent tag-family misses in the deck pool.

Context (M1/R1 follow-through, 2026-07-05): the impulse-reveal engine
split is gated on `Tag.IMPULSE_DRAW` from the committed classifier
cache.  Wrenn's Resolve (4× in a registered deck) was MISSING from the
cache, so it silently fell through to the real-draw path and kept
firing Bowmasters — the exact audited bug, half-fixed.  Tag-gated
architecture is only sound if cache coverage is ENFORCED.

The gate: for every card in every registered deck whose oracle text
matches a tag-family CANDIDATE regex, the cache must contain an entry
for that card (any tag verdict, including a classified-negative
`"tags": []`), and the entry's oracle sha256 must match the current
oracle text (stale entries fail hard — text drift means the verdict
is unverified).

Candidate regexes are deliberately BROAD nets: matching one only
demands *a verdict exists*, not that the tag is granted.  Add a new
family here whenever a new tag starts gating engine behavior.

Exit 1 with a per-family miss list on failure.  Wired into CI next to
check_abstraction.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CACHE_PATH = ROOT / "decks" / "gameplans" / "_oracle_classifier.json"

# tag family → candidate net (broad on purpose; see module doc)
CANDIDATE_FAMILIES = {
    "IMPULSE_DRAW": re.compile(
        r"exile the top .{0,60}?you may (?:play|cast)", re.IGNORECASE),
}


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def collect_deck_pool_oracles():
    """{card_name: oracle_text} across every registered deck (main +
    sideboard), via the card DB."""
    from decks.modern_meta import MODERN_DECKS
    from engine.card_database import CardDatabase
    db = CardDatabase()
    pool = {}
    for deck in MODERN_DECKS.values():
        for zone in ("mainboard", "sideboard"):
            for name in (deck.get(zone) or {}):
                if name in pool:
                    continue
                tmpl = db.get_card(name)
                pool[name] = tmpl.oracle_text if tmpl else ""
    return pool


def find_coverage_misses(pool=None, cache=None):
    """Pure check (unit-test surface).

    Returns list of (family, card_name, reason) where reason is
    'missing' or 'stale_sha'.
    """
    if pool is None:
        pool = collect_deck_pool_oracles()
    if cache is None:
        cache = json.loads(CACHE_PATH.read_text())
    cards = cache.get("cards", {})
    misses = []
    for family, rx in CANDIDATE_FAMILIES.items():
        for name, oracle in sorted(pool.items()):
            if not oracle or not rx.search(oracle):
                continue
            entry = cards.get(name)
            if entry is None:
                misses.append((family, name, "missing"))
            elif entry.get("oracle_text_sha256") != _sha(oracle):
                misses.append((family, name, "stale_sha"))
    return misses


def main() -> int:
    misses = find_coverage_misses()
    if not misses:
        print("Classifier coverage OK — every deck-pool tag-family "
              "candidate has a fresh verdict.")
        return 0
    print("Classifier coverage FAILED — tag-gated engine behavior is "
          "silently disabled for:")
    for family, name, reason in misses:
        print(f"  [{family}] {name}: {reason}")
    print("Fix: tools/build_oracle_classifier_cache.py --cards "
          "\"Name1,Name2\"  (or a manual:<reason> entry per the "
          "existing precedent).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
