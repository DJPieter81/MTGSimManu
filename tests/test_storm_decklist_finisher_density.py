"""Ruby Storm decklist construction — finisher density.

Diagnosis (Phase K combo audit, 2026-05-04):
docs/diagnostics/2026-05-04_ruby_storm_audit.md

The original Ruby Storm mainboard ran 1 Grapeshot + 2 Wish (which
can tutor the 1 SB Grapeshot or 1 SB Empty the Warrens). With only
3 cards in the 60-card deck that constitute "finisher access," the
probability of having a finisher in the opening 7 is ~32%; the
remaining 68% of games rely on natural draws to find one.

Trace evidence (run_meta.py --verbose "Ruby Storm" dimir -s 50000):
on T3-T4 the deck chained 11 spells (3× Past in Flames, 4× rituals,
4× cantrips) but **never cast Grapeshot** because no Grapeshot was
in hand or library top, and Wish was not in hand either. Storm count
peaked at ~11 — lethally lethal had Grapeshot been castable.

Canonical Modern Storm runs 3-4 Grapeshot mainboard OR 4 Wish + 1
Grapeshot SB. The current ratio (1 + 2) split the difference and
landed below both alternatives.

Fix: +2 Grapeshot MB (1 → 3), -1 Glimpse the Impossible (3 → 2),
-1 Past in Flames (3 → 2; flashback means 2 is enough since the
second copy can be re-bought from grave).

Net change:
- 1× Grapeshot → 3
- 3× Glimpse the Impossible → 2
- 3× Past in Flames → 2
- Mainboard total unchanged: 60.

This is a deck-construction fix, not an AI fix. The complementary
AI fix (Wish-as-finisher EV gate) is deferred to AI-fix dispatch
(see docs/diagnostics/2026-04-28_storm_wasted_enablers.md).
"""
from __future__ import annotations

import pytest

from decks.modern_meta import MODERN_DECKS
from engine.card_database import CardDatabase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


class TestStormDecklistFinisherDensity:
    """Ruby Storm must run enough Grapeshot copies in the mainboard
    to reliably find one in the opening 7 / first few draws. Storm
    chains end abruptly when no finisher is available; finisher
    density must be high enough to bridge the gap until the AI's
    Wish-tutor EV is fixed."""

    def test_grapeshot_mainboard_count_at_least_three(self):
        """Grapeshot is the deck's primary finisher (storm count + 1
        damage to any target). At 3+ copies in mainboard, the
        probability of having one in opening 7 is ~30%; at 1 copy
        it's ~12%."""
        deck = MODERN_DECKS["Ruby Storm"]["mainboard"]
        count = deck.get("Grapeshot", 0)
        assert count >= 3, (
            f"Grapeshot mainboard count is {count}; expected >= 3.  "
            f"At 1-2 copies, the deck chains storm spells but cannot "
            f"close because the finisher is not drawn."
        )

    def test_finisher_access_density_at_least_5(self):
        """Combined finisher access (Grapeshot MB + Wish MB +
        Empty the Warrens MB) must be at least 5 cards out of 60.
        At 5+, the probability of having a finisher in opening 7 is
        ~50% — bridges the AI's current Wish-tutor scoring weakness."""
        deck = MODERN_DECKS["Ruby Storm"]["mainboard"]
        finisher_access = (
            deck.get("Grapeshot", 0)
            + deck.get("Wish", 0)
            + deck.get("Empty the Warrens", 0)
        )
        assert finisher_access >= 5, (
            f"Storm finisher-access count is {finisher_access}; "
            f"expected >= 5 (Grapeshot + Wish + Empty the Warrens)."
        )

    def test_mainboard_total_is_60(self):
        deck = MODERN_DECKS["Ruby Storm"]["mainboard"]
        total = sum(deck.values())
        assert total == 60, (
            f"Ruby Storm mainboard total is {total}, expected 60."
        )

    def test_no_card_exceeds_four_copies(self):
        deck = MODERN_DECKS["Ruby Storm"]["mainboard"]
        BASIC_LANDS = {"Mountain", "Plains", "Island", "Swamp",
                       "Forest", "Wastes"}
        for name, count in deck.items():
            if name in BASIC_LANDS:
                continue
            assert count <= 4, (
                f"{name} has {count} copies in Ruby Storm mainboard; "
                f"Modern legality cap is 4 for non-basic-land cards."
            )

    def test_grapeshot_oracle_is_storm_finisher(self, card_db):
        """Guard: Grapeshot must have storm and deal 1 damage. If
        the oracle text changes (errata, MTGJSON corruption), this
        deck-construction fix is meaningless."""
        gs = card_db.cards.get("Grapeshot")
        assert gs is not None, "Grapeshot missing from card DB."
        oracle = (gs.oracle_text or "").lower()
        assert "storm" in oracle, (
            f"Grapeshot oracle missing 'storm': {gs.oracle_text!r}"
        )
        assert "1 damage" in oracle, (
            f"Grapeshot oracle missing '1 damage': "
            f"{gs.oracle_text!r}"
        )
