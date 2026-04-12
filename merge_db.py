#!/usr/bin/env python3
"""Merge ModernAtomic_part*.json into ModernAtomic.json.

Run this after git pull, before any sim or dashboard work.
Usage: python3 merge_db.py
"""
import json, glob, os, sys

base = "ModernAtomic.json"
parts = sorted(glob.glob("ModernAtomic_part*.json"))

if not parts:
    print("No part files found — nothing to merge.")
    sys.exit(0)

# On fresh clone, ModernAtomic.json doesn't exist yet — start from an empty
# MTGJSON-shaped skeleton so merge works end-to-end without a prerequisite.
if os.path.exists(base):
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
    raw["data"] = cards
    with open(base, "w") as f:
        json.dump(raw, f)
else:
    with open(base, "w") as f:
        json.dump(cards, f)

print(f"\nMerged: {before} → {len(cards)} cards in {base}")
