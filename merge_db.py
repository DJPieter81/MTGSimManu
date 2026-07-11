#!/usr/bin/env python3
"""Merge ModernAtomic_part*.json into ModernAtomic.json.

Run this after git pull, before any sim or dashboard work.
Usage: python3 merge_db.py [--incremental]

Default is a FRESH REBUILD: the output is a pure function of the part
files, so keys removed from the parts disappear from the merged DB.
Pass --incremental to merge INTO an existing ModernAtomic.json
(the pre-2026-07 behaviour, which lets stale keys survive).
"""
import argparse, json, glob, os, re, sys

base = "ModernAtomic.json"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--incremental", action="store_true",
    help="merge into an existing ModernAtomic.json instead of "
         "rebuilding from parts alone (stale keys survive)")
args = parser.parse_args()


def _part_number(path):
    # Later parts carry UPDATED versions of cards from earlier parts, so
    # merge order is load-bearing: numeric suffix order, never
    # lexicographic ("part10" < "part2" would let stale part2 entries
    # overwrite part10's updated cards). Mirrors tests/conftest.py.
    m = re.search(r"part(\d+)", path)
    return int(m.group(1)) if m else 0


parts = sorted(glob.glob("ModernAtomic_part*.json"), key=_part_number)

if not parts:
    print("No part files found — nothing to merge.")
    sys.exit(0)

# Default: fresh rebuild from an empty MTGJSON-shaped skeleton, so the
# result contains exactly what the parts provide — no stale survivors.
# --incremental: start from the existing merged file (old behaviour).
if args.incremental and os.path.exists(base):
    with open(base) as f:
        raw = json.load(f)
else:
    raw = {"meta": {}, "data": {}}

cards = raw.get("data", raw)
before = len(cards)

for part in parts:
    with open(part) as f:
        pd = json.load(f)
    chunk = pd.get("data", {k: v for k, v in pd.items() if k != "meta"})
    cards.update(chunk)
    print(f"  {part}: +{len(chunk)} cards")

if "data" in raw:
    # Provenance stamp: record what this merge actually saw, so the
    # loader can detect a truncated/hand-edited/out-of-band merged file
    # (engine/card_database.py cross-checks card_count on load).
    if not isinstance(raw.get("meta"), dict):
        raw["meta"] = {}
    raw["meta"]["merged_from"] = {
        "part_count": len(parts),
        "card_count": len(cards),
        "part_names": [os.path.basename(p) for p in parts],
    }
    raw["data"] = cards
    with open(base, "w") as f:
        json.dump(raw, f)
else:
    with open(base, "w") as f:
        json.dump(cards, f)

print(f"\nMerged: {before} → {len(cards)} cards in {base}")
