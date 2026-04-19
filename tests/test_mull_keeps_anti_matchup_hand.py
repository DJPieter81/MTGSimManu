"""Bug F — Rigid mulligan heuristic ships anti-matchup hands back.

Evidence: `replays/boros_rarakkyo_vs_affinity_s63000_bo3.txt` G2 —
Boros on the draw vs Affinity shipped a 7-card hand of

    3 lands + Phlage × 2 + Wear // Tear + Galvanic Discharge

back to the mulligan.  Rule fired: "no creature with CMC ≤ 2."  The
replacement 6-card hand contained zero removal AND zero artifact hate.

This hand is arguably the best possible keep against Affinity in the
75: artifact hate (Wear // Tear), burn removal (Galvanic Discharge),
and a closer that doubles as spot removal (Phlage).  The only missing
ingredient is a 1- or 2-drop creature — which is a mostly-irrelevant
curve concern on the draw into an explosive Affinity deck.

Root cause (see `docs/design/ev_correctness_overhaul.md` §2 Bug F):
`ai/mulligan.py` uses rigid rules like "must have a creature with
CMC ≤ 2" (driven by `mulligan_require_creature_cmc` in the gameplan
JSON).  The rule is archetype-sensible for Boros-as-curve-out-aggro
in a vacuum but blind to matchup context.

Fix direction: mulligan decisions must be EV-comparisons that use the
same signal machinery as `compute_play_ev` (deferrability, this-turn
payoff signals) projected over the first ~4 turns.  An opening hand
that fires high-value signals against THIS opponent (removal vs
artifact creatures, artifact hate vs artifact-synergy board) keeps,
even without a strict curve card.  The "must have 2-drop" rule should
emerge from the math where relevant, not be enforced as a hard gate.

Regression anchor: a clearly unkeepable hand (6 lands + 1 spell) must
still mulligan — the EV-based decider must not devolve into "keep
everything."
"""
from __future__ import annotations

import pytest

from ai.gameplan import create_goal_engine
from ai.mulligan import MulliganDecider
from ai.strategy_profile import ArchetypeStrategy
from engine.card_database import CardDatabase
from engine.cards import CardInstance


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _make_card(card_db, name, instance_id):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return CardInstance(
        template=tmpl,
        owner=0,
        controller=0,
        instance_id=instance_id,
        zone="hand",
    )


class TestMulliganKeepsAntiMatchupHand:
    """A hand loaded with matchup-specific answers must be kept even
    without a strict curve 2-drop."""

    def test_boros_keeps_removal_and_artifact_hate_vs_affinity(
            self, card_db):
        """Exact hand from the design doc Bug F trace: 3 lands + 2
        Phlage + Wear//Tear + Galvanic Discharge.  Every non-land card
        is an answer to Affinity:
          - Galvanic Discharge: burn removal, scales vs small artifact
            creatures, also clocks.
          - Wear // Tear: destroys two artifacts/enchantments (Plating
            blowout), modal so it's live vs every Affinity deck.
          - Phlage × 2: flashback closer that doubles as 3-damage spot
            removal / life gain on cast and on each flashback.

        Against an aggressive artifact-dense deck, THIS is a T1-keep
        even though it has no 2-drop creature.  The current rigid
        rule ships it to a 6-card hand with fewer answers."""
        hand = [
            _make_card(card_db, "Mountain", 1),
            _make_card(card_db, "Plains", 2),
            _make_card(card_db, "Sacred Foundry", 3),
            _make_card(card_db, "Phlage, Titan of Fire's Fury", 4),
            _make_card(card_db, "Phlage, Titan of Fire's Fury", 5),
            _make_card(card_db, "Wear // Tear", 6),
            _make_card(card_db, "Galvanic Discharge", 7),
        ]

        goal_engine = create_goal_engine("Boros Energy")
        decider = MulliganDecider(ArchetypeStrategy.AGGRO, goal_engine)

        keep = decider.decide(hand, 7)
        assert keep is True, (
            f"Boros 7-card hand [3 lands + 2 Phlage + Wear//Tear + "
            f"Galvanic Discharge] should be KEPT vs Affinity. Every "
            f"non-land card is an answer to the deck archetype: "
            f"artifact hate (Wear//Tear), burn removal (Discharge), "
            f"and a flashback closer/removal (Phlage). The rigid "
            f"'must have CMC ≤ 2 creature' rule ships it for a 6-card "
            f"hand that's strictly worse in this matchup. Got mull "
            f"with reason: '{decider.last_reason}'."
        )

    def test_boros_still_mulls_clearly_unkeepable_hand(self, card_db):
        """Regression: 6 lands + 1 Phlage is a clear mulligan.  The
        EV-based decider must still reject flooded hands — this is
        not a "keep anything with a key card" blanket."""
        hand = [
            _make_card(card_db, "Mountain", 11),
            _make_card(card_db, "Mountain", 12),
            _make_card(card_db, "Plains", 13),
            _make_card(card_db, "Plains", 14),
            _make_card(card_db, "Sacred Foundry", 15),
            _make_card(card_db, "Arid Mesa", 16),
            _make_card(card_db, "Phlage, Titan of Fire's Fury", 17),
        ]

        goal_engine = create_goal_engine("Boros Energy")
        decider = MulliganDecider(ArchetypeStrategy.AGGRO, goal_engine)

        keep = decider.decide(hand, 7)
        assert keep is False, (
            f"6 lands + 1 spell is an unambiguous mulligan — 85% of "
            f"future draws just produce more lands off an already "
            f"flooded hand. The EV-based decider must still reject "
            f"this. Got keep=True with reason: '{decider.last_reason}'."
        )
