"""Grammar witnesses that left every registered deck at a list refresh.

Several pool tests ("registered-deck witnesses") pin the grammar on cards a
registered deck plays, and assert that premise. A list refresh can drop a
witness from every deck. Its grammar assertions are still true and still
cover the mechanic, so it stays a witness -- a pool card, named here with
the refresh that retired it -- rather than being deleted. Every other
witness keeps the deck-membership premise.
"""
from __future__ import annotations

# 2026-11-01 metagame refresh (data/tier1_decklists/2026-11-01/).
RETIRED_2026_11_01 = frozenset({
    "Beza, the Bounding Spring",
    "Consult the Star Charts",
    "Elvish Reclaimer",
    "Into the Flood Maw",
    "Mana Tithe",
    "Oblivion Stone",
    "Requiting Hex",
    "Snapcaster Mage",
    "Starfield Shepherd",
    "Static Prison",
    "The Filigree Sylex",
    "Untimely Malfunction",
    "Vandalblast",
    "Violent Urge",
})

RETIRED = RETIRED_2026_11_01
