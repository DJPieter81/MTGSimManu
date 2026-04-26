"""Goryo's Vengeance decklist construction fix — Unburial Rites as
the second reanimation path.

Diagnosis (2026-04-26): The original Goryo's Vengeance decklist had
two structural inconsistencies:

 1. **Gameplan/decklist mismatch.** `decks/gameplans/goryos_vengeance.json`
    declares Unburial Rites as a primary payoff (`card_priorities:
    Unburial Rites: 18.0`, listed in `card_roles.payoffs` and
    `critical_pieces`).  But the mainboard included only 1× Unburial
    Rites — far below the gameplan's expectation that the AI could
    rely on it as a backup reanimator.

 2. **Dead Unmarked Grave slot.**  Unmarked Grave's oracle is
    "Search your library for a NONLEGENDARY card, put that card in
    your graveyard, then shuffle."  The only nonlegendary creatures
    in the deck were 4× Solitude (CMC 5).  Solitude cannot be the
    target of Goryo's Vengeance (legendary-only restriction), and
    Persist requires a creature in the graveyard which Solitude
    technically satisfies but at CMC 5 it's a 4-mana exchange for a
    2/3 evoke creature — strictly inefficient.

    Audit log evidence (Goryo's vs Dimir s=50500): 8 combo-piece
    casts across the game, 1 successful Goryo's reanimate, no
    Unburial Rites available because only 1 was in the deck.

Fix: replace 4× Unmarked Grave with effective copies of Unburial
Rites (the second reanimation path).  Mainboard balance held by
+1× Archon of Cruelty (more legendary reanimation targets, valid
for Goryo's AND Unburial Rites).

Net change:
- 4× Unmarked Grave → 0 (removed)
- 1× Unburial Rites → 4 (gameplan-declared payoff now playable)
- 2× Archon of Cruelty → 3 (more reanimation targets)
- Mainboard total unchanged: 60

This is a deck-construction fix, not an AI fix.  No code in `ai/*`
or `engine/*` is modified — only data in `decks/modern_meta.py`.

Sister-fix to PR #194 (Storm cost-reducer signal): both target
combo-deck WR floors but via different layers (decklist data vs
AI signal).  Today this fix benefits Goryo's only; future
reanimator decks could re-use the same Unburial Rites + legendary
target pattern.
"""
from __future__ import annotations

import pytest

from decks.modern_meta import MODERN_DECKS
from engine.card_database import CardDatabase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


class TestGoryosDecklistConstruction:
    """The Goryo's Vengeance decklist must include enough Unburial
    Rites copies to act as a reliable second reanimation path,
    matching the gameplan's declaration.  Unmarked Grave (which only
    finds nonlegendary creatures and produces a dead-end Persist
    line in this deck) must be removed."""

    def test_unmarked_grave_removed(self):
        """Unmarked Grave can ONLY find nonlegendary cards; the only
        nonlegendary creature in this deck is Solitude (CMC 5), and
        the resulting Solitude-in-graveyard cannot be Goryo's
        Vengeance target.  The slot is dead and must be removed."""
        deck = MODERN_DECKS["Goryo's Vengeance"]["mainboard"]
        assert "Unmarked Grave" not in deck, (
            f"Unmarked Grave is still in the Goryo's Vengeance "
            f"mainboard.  This is a near-dead slot in this deck: "
            f"the only legal target is 4× Solitude (CMC 5, "
            f"nonlegendary), and Solitude cannot then be reanimated "
            f"by Goryo's Vengeance (legendary-only restriction).  "
            f"Replace with Unburial Rites copies (any creature, "
            f"including Griselbrand and Archon)."
        )

    def test_unburial_rites_count_at_least_three(self):
        """The gameplan declares Unburial Rites as a payoff card
        (`decks/gameplans/goryos_vengeance.json` card_priorities +
        critical_pieces).  The mainboard must include at least 3 to
        make it a reliably-drawn second reanimation path."""
        deck = MODERN_DECKS["Goryo's Vengeance"]["mainboard"]
        count = deck.get("Unburial Rites", 0)
        assert count >= 3, (
            f"Unburial Rites count is {count}; expected ≥ 3.  "
            f"The gameplan at "
            f"decks/gameplans/goryos_vengeance.json declares "
            f"Unburial Rites as a payoff and critical_piece — the "
            f"mainboard must include enough copies to draw it "
            f"reliably."
        )

    def test_mainboard_total_is_60(self):
        """Modern decklists must have exactly 60 mainboard cards.
        The Unmarked-Grave-to-Unburial-Rites swap must be balanced."""
        deck = MODERN_DECKS["Goryo's Vengeance"]["mainboard"]
        total = sum(deck.values())
        assert total == 60, (
            f"Goryo's Vengeance mainboard total is {total}, "
            f"expected 60.  The decklist edit must preserve total "
            f"card count."
        )

    def test_no_card_exceeds_four_copies(self):
        """Modern legality: no more than 4 copies of any
        nonbasic-land card.  Verify the swap didn't push any card
        above the 4-of cap."""
        deck = MODERN_DECKS["Goryo's Vengeance"]["mainboard"]
        BASIC_LANDS = {"Mountain", "Plains", "Island", "Swamp",
                       "Forest", "Wastes"}
        for name, count in deck.items():
            if name in BASIC_LANDS:
                continue
            assert count <= 4, (
                f"{name} has {count} copies in Goryo's mainboard; "
                f"Modern legality cap is 4 for non-basic-land "
                f"cards."
            )

    def test_unburial_rites_can_target_griselbrand(self, card_db):
        """The whole point of the swap: Unburial Rites returns ANY
        creature card (no legendary restriction), so it can
        reanimate the deck's primary win condition (Griselbrand)
        when drawn alongside fuel.  Verify both cards exist and
        Unburial Rites' oracle has no legendary restriction."""
        rites = card_db.get_card("Unburial Rites")
        griselbrand = card_db.get_card("Griselbrand")
        assert rites is not None, "Unburial Rites missing from card DB"
        assert griselbrand is not None, "Griselbrand missing from card DB"
        oracle = (rites.oracle_text or "").lower()
        assert "target creature card" in oracle, (
            f"Unburial Rites oracle '{rites.oracle_text}' does not "
            f"contain 'target creature card'; expected "
            f"'Return target creature card from your graveyard' "
            f"with no legendary restriction."
        )
        # Sanity: no "nonlegendary" qualifier.
        assert "nonlegendary" not in oracle, (
            f"Unburial Rites unexpectedly restricted to "
            f"nonlegendary creatures: {rites.oracle_text}"
        )

    def test_decklist_supports_second_reanimation_path(self):
        """Integration: the deck must have BOTH a legendary target
        (Griselbrand or Archon) AND a non-Goryo's reanimation card
        (Unburial Rites or Persist) in the mainboard, so the
        secondary line is castable when Goryo's Vengeance itself is
        countered or absent."""
        deck = MODERN_DECKS["Goryo's Vengeance"]["mainboard"]
        legendary_targets = (
            deck.get("Griselbrand", 0) + deck.get("Archon of Cruelty", 0)
        )
        backup_reanimate = (
            deck.get("Unburial Rites", 0) + deck.get("Persist", 0)
        )
        assert legendary_targets >= 4, (
            f"Only {legendary_targets} legendary creature targets in "
            f"deck; need ≥4 for the deck's primary win condition."
        )
        assert backup_reanimate >= 4, (
            f"Only {backup_reanimate} backup reanimation cards in "
            f"deck.  When Goryo's Vengeance is countered or not "
            f"drawn, the deck needs a viable B-line."
        )
