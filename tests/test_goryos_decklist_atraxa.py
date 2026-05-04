"""Goryo's Vengeance decklist construction — Atraxa, Grand Unifier as
the canonical second legendary reanimation target.

Diagnosis (Phase K combo audit, 2026-05-04):
docs/diagnostics/2026-05-04_goryos_vengeance_audit.md

The original Goryo's Vengeance mainboard (post-PR #194 fix) included
4× Griselbrand + 3× Archon of Cruelty = 7 legendary reanimation
targets. Canonical Modern Goryo's lists run 4× Atraxa, Grand Unifier
+ 4× Griselbrand + 1-2× Archon = 9-10 legendary targets, because
Atraxa is the deck's strongest target in the post-MH3 metagame:

- CMC 7 (legal Goryo's target)
- 7/7 with flying, vigilance, lifelink, deathtouch
- ETB triggers a "look at top 10, reveal cards of each card type
  to your hand" — typical haul: 4-7 cards, including lands

Without Atraxa, Goryo's wins via Griselbrand's 7/7 lifelink (good)
but has no card-advantage backup if the first reanimation is
answered. The post-MH3 metagame is too disruptive (Solitude,
Subtlety, Stubborn Denial, Force of Negation everywhere) for a
single-payoff strategy.

Counter-balance: −4 Solitude. Solitude is a 5-mana evoke creature
that conflicts with reanimator's mana plan (T2-T3 mana is reserved
for Faithful Mending → Goryo's Vengeance). It's also nonlegendary,
so Goryo's cannot reanimate it. The deck retains Inquisition of
Kozilek (3) + Thoughtseize (4) for early disruption.

Net change:
- 4× Solitude → 0 (removed)
- 0× Atraxa, Grand Unifier → 4 (canonical reanimation target)
- Mainboard total unchanged: 60

This is a deck-construction fix, not an AI fix. No code in `ai/*`
or `engine/*` is modified — only data in `decks/modern_meta.py`.

Sister-fix to PR #221 (Unburial Rites as second-line reanimator):
both target Goryo's WR by widening the reanimation target pool.
This PR adds the missing canonical legendary target; the prior PR
added a non-Goryo's reanimation card.
"""
from __future__ import annotations

import pytest

from decks.modern_meta import MODERN_DECKS
from engine.card_database import CardDatabase
from engine.cards import Supertype


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


class TestGoryosDecklistAtraxa:
    """The Goryo's Vengeance decklist must include Atraxa, Grand
    Unifier as the canonical second legendary reanimation target.
    Without Atraxa the deck is single-payoff (Griselbrand only) and
    folds to any disruption that hits the first reanimation."""

    def test_atraxa_count_at_least_three(self):
        """Atraxa, Grand Unifier must be present at >= 3 copies in
        the Goryo's Vengeance mainboard. The canonical Modern
        Goryo's list runs 4 Atraxa as a card-advantage finisher
        target. At 3 copies, the deck reliably draws or tutors one
        in the late game."""
        deck = MODERN_DECKS["Goryo's Vengeance"]["mainboard"]
        count = deck.get("Atraxa, Grand Unifier", 0)
        assert count >= 3, (
            f"Atraxa, Grand Unifier count is {count}; expected >= 3.  "
            f"This is the canonical second legendary reanimation "
            f"target for Modern Goryo's Vengeance.  Without it, the "
            f"deck has only Griselbrand + Archon as targets and "
            f"folds to single-target disruption (Surgical Extraction, "
            f"Stubborn Denial, Force of Negation)."
        )

    def test_atraxa_is_legendary_creature_cmc_7(self, card_db):
        """Verify Atraxa exists in the card database as a legendary
        creature of CMC 7 (i.e. legal Goryo's Vengeance target).
        This guards against an upstream MTGJSON regression that
        would silently drop Atraxa from the legal-target pool
        (similar to the Archon of Cruelty regression — see
        engine/card_database.py:973 SUPERTYPE_CORRECTIONS)."""
        atraxa = card_db.cards.get("Atraxa, Grand Unifier")
        assert atraxa is not None, (
            "Atraxa, Grand Unifier missing from card DB. Re-run "
            "update_modern_atomic.py."
        )
        assert atraxa.cmc == 7, (
            f"Atraxa CMC is {atraxa.cmc}; expected 7. Verify "
            f"ModernAtomic.json entry."
        )
        assert Supertype.LEGENDARY in atraxa.supertypes, (
            f"Atraxa supertypes are {atraxa.supertypes}; "
            f"LEGENDARY required for Goryo's Vengeance to target."
        )

    def test_legendary_reanimation_target_count_at_least_8(self):
        """The deck's combined legendary reanimation pool (Atraxa +
        Griselbrand + Archon) must total >= 8 to absorb opponent
        disruption (1-2 Surgicals / Endurance triggers / counter
        spells) and still reliably resolve a payoff. Below 8, the
        deck folds to any 2-piece disruption package."""
        deck = MODERN_DECKS["Goryo's Vengeance"]["mainboard"]
        targets = (
            deck.get("Atraxa, Grand Unifier", 0)
            + deck.get("Griselbrand", 0)
            + deck.get("Archon of Cruelty", 0)
        )
        assert targets >= 8, (
            f"Only {targets} legendary reanimation targets in deck; "
            f"need >= 8 (4 Atraxa + 4 Griselbrand canonical, with "
            f"Archon as overlap)."
        )

    def test_solitude_removed_or_reduced(self):
        """Solitude conflicts with reanimator's mana plan (5 mana
        evoke creature competing with T3 Faithful Mending → Goryo's)
        and is nonlegendary (cannot be Goryo's target). Reduce
        below 4 to free slots for Atraxa."""
        deck = MODERN_DECKS["Goryo's Vengeance"]["mainboard"]
        count = deck.get("Solitude", 0)
        assert count < 4, (
            f"Solitude count is {count}; expected < 4. Solitude is "
            f"a 5-mana evoke creature that is nonlegendary (cannot "
            f"be reanimated by Goryo's Vengeance) and competes for "
            f"the T3-T5 mana plan. Reduce to free slots for Atraxa."
        )

    def test_mainboard_total_is_60(self):
        """Modern decklists must have exactly 60 mainboard cards.
        The Solitude-to-Atraxa swap must be balanced."""
        deck = MODERN_DECKS["Goryo's Vengeance"]["mainboard"]
        total = sum(deck.values())
        assert total == 60, (
            f"Goryo's Vengeance mainboard total is {total}, "
            f"expected 60.  The decklist edit must preserve total "
            f"card count."
        )

    def test_no_card_exceeds_four_copies(self):
        """Modern legality: no more than 4 copies of any
        nonbasic-land card."""
        deck = MODERN_DECKS["Goryo's Vengeance"]["mainboard"]
        BASIC_LANDS = {"Mountain", "Plains", "Island", "Swamp",
                       "Forest", "Wastes"}
        for name, count in deck.items():
            if name in BASIC_LANDS:
                continue
            assert count <= 4, (
                f"{name} has {count} copies in Goryo's mainboard; "
                f"Modern legality cap is 4 for non-basic-land cards."
            )
