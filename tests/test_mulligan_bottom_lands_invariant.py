"""Mulligan bottoming must preserve the deck's min-land floor.

Bug: replays/affinity_vs_boros_energy_s60200.txt:32-37
================================================================
Boros mulled twice on land-light hands ("too few lands (1<2)"),
then on the down-to-5 redraw drew a 0-land 7-spell hand and
"kept" it.  The AI bottomed two creatures and committed a 5-card
hand with ZERO lands — a guaranteed loss (Boros never played a
land for 3 turns and lost on T3).

Class-wide invariant
--------------------
The mulligan trigger declares a min-land threshold per gameplan
(``mulligan_min_lands``: e.g. Boros=2, Storm=1, Living End=1).
The bottoming policy must respect the same threshold: a kept
hand should contain ``min(min_lands, lands_in_hand)`` lands.
When the hand has zero lands and the gameplan requires lands,
the AI must NOT keep — it must reflect that as a mulligan
decision so the engine can either redraw or signal the loss.
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


def _mk(card_db, name, iid):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=iid, zone="hand",
    )


def _boros_decider():
    ge = create_goal_engine("Boros Energy")
    return MulliganDecider(ArchetypeStrategy.AGGRO, ge), ge


class TestBottomingPreservesMinLands:
    """The bottoming policy must enforce ``min(min_lands, lands_in_hand)``
    lands in the kept hand for any gameplan that declares ``min_lands``.
    """

    def test_boros_one_land_six_spells_bottom_two_keeps_land(
            self, card_db):
        """Boros gameplan says ``mulligan_min_lands = 2``.  Hand has
        1 land + 6 spells, count=2 (mull-to-5 step).  Bottoming must
        preserve min(2, 1) = 1 land in the kept-5.
        """
        hand = [
            _mk(card_db, "Sacred Foundry", 1),
            _mk(card_db, "Ragavan, Nimble Pilferer", 2),
            _mk(card_db, "Ragavan, Nimble Pilferer", 3),
            _mk(card_db, "Guide of Souls", 4),
            _mk(card_db, "Guide of Souls", 5),
            _mk(card_db, "Ajani, Nacatl Pariah // "
                         "Ajani, Nacatl Avenger", 6),
            _mk(card_db, "Seasoned Pyromancer", 7),
        ]
        dec, ge = _boros_decider()
        bottom = dec.choose_cards_to_bottom(hand[:], 2)
        kept = [c for c in hand if c not in bottom]
        kept_lands = sum(1 for c in kept if c.template.is_land)
        floor = min(ge.gameplan.mulligan_min_lands or 2,
                    sum(1 for c in hand if c.template.is_land))
        assert kept_lands >= floor, (
            f"Bottoming dropped land floor: kept {kept_lands} "
            f"land(s) in {[c.name for c in kept]}, expected >= "
            f"{floor}.  Hand had {sum(1 for c in hand if c.template.is_land)} "
            f"land, gameplan min_lands={ge.gameplan.mulligan_min_lands}."
        )

    def test_boros_zero_land_seven_spells_decide_refuses_keep(
            self, card_db):
        """The s60200 case verbatim: a 7-card hand with ZERO lands at
        the down-to-5 step.  The bottoming layer cannot conjure lands,
        so the only correct response is for ``decide()`` to refuse to
        keep regardless of hand_size.  The hard-floor at line 41-54 of
        ``MulliganDecider.decide`` already encodes this; the test
        anchors the rule so future short-circuits cannot bypass it.

        This is the bug exposed by replays/affinity_vs_boros_energy_s60200.txt
        where the AI committed a 0-land 5-card hand and lost on T3.
        """
        hand = [
            _mk(card_db, "Ragavan, Nimble Pilferer", 10),
            _mk(card_db, "Seasoned Pyromancer", 11),
            _mk(card_db, "Seasoned Pyromancer", 12),
            _mk(card_db, "Ragavan, Nimble Pilferer", 13),
            _mk(card_db, "Guide of Souls", 14),
            _mk(card_db, "Guide of Souls", 15),
            _mk(card_db, "Ajani, Nacatl Pariah // "
                         "Ajani, Nacatl Avenger", 16),
        ]
        dec, ge = _boros_decider()
        keep = dec.decide(hand, cards_in_hand=5)
        assert not keep, (
            f"MulliganDecider.decide kept a 0-land hand at "
            f"cards_in_hand=5.  Reason: '{dec.last_reason}'.  "
            f"The hard floor must take precedence over any "
            f"'always keep at small hand sizes' policy."
        )

    def test_evplayer_decide_mulligan_zero_land_at_five_refuses(
            self, card_db):
        """End-to-end coverage: the EVPlayer-level decision (which the
        engine actually calls in the mulligan loop) must also refuse
        to keep a 0-land hand at cards_in_hand=5.  The bug in
        replays/affinity_vs_boros_energy_s60200.txt was that
        ``EVPlayer.decide_mulligan`` short-circuited via
        ``mulligan_always_keep=5`` BEFORE the 0-lands hard floor,
        forcing a guaranteed-loss keep.

        The fix collapses the duplicated policy so the decider is the
        single source of truth.
        """
        from ai.ev_player import EVPlayer
        ai = EVPlayer(player_idx=0, deck_name="Boros Energy",
                      rng=random.Random(0))
        hand = [
            _mk(card_db, "Ragavan, Nimble Pilferer", 20),
            _mk(card_db, "Seasoned Pyromancer", 21),
            _mk(card_db, "Seasoned Pyromancer", 22),
            _mk(card_db, "Ragavan, Nimble Pilferer", 23),
            _mk(card_db, "Guide of Souls", 24),
            _mk(card_db, "Guide of Souls", 25),
            _mk(card_db, "Ajani, Nacatl Pariah // "
                         "Ajani, Nacatl Avenger", 26),
        ]
        keep = ai.decide_mulligan(hand, cards_in_hand=5)
        assert not keep, (
            f"EVPlayer kept 0-land hand at cards_in_hand=5.  "
            f"Reason: '{getattr(ai, 'mulligan_reason', '')}'.  "
            f"This is the s60200 bug: the always-keep short-circuit "
            f"bypassed the 0-lands hard floor."
        )

    def test_storm_one_land_min_lands_one_keeps_land(self, card_db):
        """Generalisation check (per CLAUDE.md "Generalization-first
        fixes" rule): the same invariant must hold for a deck with a
        different ``min_lands``.  Ruby Storm declares ``min_lands=1``;
        bottoming a hand with 1 land must keep that land regardless of
        spell scores.
        """
        hand = [
            _mk(card_db, "Steam Vents", 1),
            _mk(card_db, "Ruby Medallion", 2),
            _mk(card_db, "Desperate Ritual", 3),
            _mk(card_db, "Desperate Ritual", 4),
            _mk(card_db, "Pyretic Ritual", 5),
            _mk(card_db, "Wish", 6),
            _mk(card_db, "Grapeshot", 7),
        ]
        ge = create_goal_engine("Ruby Storm")
        dec = MulliganDecider(ArchetypeStrategy.COMBO, ge)
        bottom = dec.choose_cards_to_bottom(hand[:], 2)
        kept = [c for c in hand if c not in bottom]
        kept_lands = sum(1 for c in kept if c.template.is_land)
        floor = min(ge.gameplan.mulligan_min_lands or 1,
                    sum(1 for c in hand if c.template.is_land))
        assert kept_lands >= floor, (
            f"Storm bottoming dropped its sole land.  Kept "
            f"{kept_lands} lands in {[c.name for c in kept]}, "
            f"expected >= {floor}."
        )
