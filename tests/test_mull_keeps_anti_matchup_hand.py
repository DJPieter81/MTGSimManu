"""Bug F — Rigid mulligan heuristic ships anti-matchup hand back.

Design: docs/design/ev_correctness_overhaul.md §2.F

Boros Energy's gameplan sets `mulligan_require_creature_cmc = 2`,
producing a rigid gate: "must have a creature with CMC ≤ 2 or
mulligan."  The rule is reasonable for a curve-out aggro deck in a
typical matchup, but blind to matchup context.

Observed in replays/boros_rarakkyo_vs_affinity_s63000_bo3.txt G2:
Boros shipped a hand of {3 lands, 2× Phlage, Wear // Tear, Galvanic
Discharge} back — a hand with two 3-CMC threats, artifact hate,
and removal.  That is arguably the best possible keep vs Affinity.

The fix makes mulligan decisions EV-comparative (reusing the same
signal framework as the rest of the design) so that a hand with
enough interaction + threats + lands passes the keep bar even
without a ≤2-CMC creature when the threats-plus-interaction shape
matches the matchup need.

Regression: the rigid checks that catch obvious mulls (7 lands,
0 lands, 6 lands + 1 spell) must still fire.
"""
from __future__ import annotations

import random

import pytest

from ai.gameplan import create_goal_engine
from ai.mulligan import MulliganDecider
from ai.strategy_profile import ArchetypeStrategy
from engine.card_database import CardDatabase
from engine.cards import CardInstance


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _make_card_in_hand(card_db, name, iid):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=iid, zone="hand",
    )


def _boros_decider():
    goal = create_goal_engine("Boros Energy")
    return MulliganDecider(ArchetypeStrategy.AGGRO, goal)


class TestMullKeepsAntiMatchupHand:
    """Strong anti-matchup hand must keep, even without a ≤2-CMC
    creature.  Obvious mulls must still be caught."""

    def test_boros_keeps_phlage_wear_discharge_hand(self, card_db):
        """Seven-card Boros hand: 3 lands + 2 Phlage + Wear // Tear +
        Galvanic Discharge.  No creature with CMC ≤ 2, but: 2 threats
        (Phlages at 3), one instant removal (Discharge), and one
        artifact/enchantment answer (Wear // Tear).  That is a very
        reasonable keep — maximum interaction + on-curve finishers.

        Current behaviour: decide() returns False with reason
        "no creature with CMC ≤ 2" — a rigid rule override.  Under
        the fix the decision must use signal-based hand evaluation
        (threats + interaction + lands) and return True."""
        hand = [
            _make_card_in_hand(card_db, "Mountain", iid=1),
            _make_card_in_hand(card_db, "Plains", iid=2),
            _make_card_in_hand(card_db, "Sacred Foundry", iid=3),
            _make_card_in_hand(card_db, "Phlage, Titan of Fire's Fury",
                               iid=4),
            _make_card_in_hand(card_db, "Phlage, Titan of Fire's Fury",
                               iid=5),
            _make_card_in_hand(card_db, "Wear // Tear", iid=6),
            _make_card_in_hand(card_db, "Galvanic Discharge", iid=7),
        ]
        decider = _boros_decider()
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep, (
            f"Boros shipped a {len(hand)}-card hand of 3 lands + 2× "
            f"Phlage + Wear//Tear + Discharge back to mulligan.  "
            f"Reason logged: '{decider.last_reason}'.  This is the "
            f"rigid-CMC-2 rule firing blindly — the hand has two "
            f"on-curve finishers, a removal spell, and artifact hate.  "
            f"Under the EV-baseline fix, mulligan must use signal-"
            f"based hand evaluation (threats + interaction + lands) "
            f"and keep this hand."
        )

    def test_boros_mulls_land_flood_six_lands_one_phlage(
            self, card_db):
        """Regression: the obvious mull case must still fire.  Six
        lands + one Phlage is too many lands; the hand will struggle
        to develop.  decide() should return False regardless of the
        looser keep criteria."""
        hand = [
            _make_card_in_hand(card_db, "Mountain", iid=10),
            _make_card_in_hand(card_db, "Mountain", iid=11),
            _make_card_in_hand(card_db, "Plains", iid=12),
            _make_card_in_hand(card_db, "Plains", iid=13),
            _make_card_in_hand(card_db, "Sacred Foundry", iid=14),
            _make_card_in_hand(card_db, "Arid Mesa", iid=15),
            _make_card_in_hand(card_db, "Phlage, Titan of Fire's Fury",
                               iid=16),
        ]
        decider = _boros_decider()
        keep = decider.decide(hand, cards_in_hand=7)
        assert not keep, (
            f"Regression: six lands + one Phlage kept as a 7-card "
            f"hand.  Reason: '{decider.last_reason}'.  The fix must "
            f"still catch land-flood hands — this one has too much "
            f"mana and too few spells."
        )

    def test_boros_mulls_zero_land_hand(self, card_db):
        """Regression: the 0-lands hard floor still fires."""
        hand = [
            _make_card_in_hand(card_db, "Phlage, Titan of Fire's Fury",
                               iid=20),
            _make_card_in_hand(card_db, "Galvanic Discharge", iid=21),
            _make_card_in_hand(card_db, "Guide of Souls", iid=22),
            _make_card_in_hand(card_db, "Wear // Tear", iid=23),
            _make_card_in_hand(card_db, "Ragavan, Nimble Pilferer",
                               iid=24),
            _make_card_in_hand(card_db, "Ocelot Pride", iid=25),
            _make_card_in_hand(card_db, "Ajani, Nacatl Pariah // "
                               "Ajani, Nacatl Avenger", iid=26),
        ]
        decider = _boros_decider()
        keep = decider.decide(hand, cards_in_hand=7)
        assert not keep, (
            f"Regression: zero-lands hand should always mulligan "
            f"(reason logged: '{decider.last_reason}').  The fix "
            f"must not relax this hard floor."
        )
