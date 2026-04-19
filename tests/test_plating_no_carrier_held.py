"""Bug B — Cranial Plating cast with no creature to equip this turn.

Design: docs/design/ev_correctness_overhaul.md §2.B

Cranial Plating is Equipment — its value is realised only when
equipped to a creature.  Slapping it onto the battlefield with no
creature on board (or no equip mana available) delivers zero same-
turn impact: no +N/+N bonus applied, no damage through evasion,
just a permanent sitting unattached exposed to removal.

Under the EV-baseline fix, such casts have no this-turn signal and
must score below pass_threshold so they defer until a carrier is
available.  Regression anchor: with a valid equip target on board
AND enough mana to equip this turn, the cast is correct and EV
should remain positive.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import compute_play_ev, snapshot_from_game
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _setup_affinity_main(game, turn=3):
    game.players[0].deck_name = "Affinity"
    game.players[1].deck_name = "Dimir Midrange"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = turn
    game.players[0].lands_played_this_turn = 1


class TestPlatingNoCarrierHeld:
    """Cranial Plating with no creature to equip this turn has no
    this-turn value — the cast should be deferred."""

    def test_plating_not_cast_on_empty_board(self, card_db):
        """Empty board, Plating in hand, enough mana to cast (2) but
        no carrier to equip.  AI should not cast Plating."""
        game = GameState(rng=random.Random(0))
        # 3 lands = 3 mana available (enough to cast Plating at 2 and
        # still have mana for other plays if any).
        _add_to_battlefield(game, card_db, "Darksteel Citadel", controller=0)
        _add_to_battlefield(game, card_db, "Darksteel Citadel", controller=0)
        _add_to_battlefield(game, card_db, "Mountain", controller=0)
        _add_to_hand(game, card_db, "Cranial Plating", controller=0)
        # A creature in hand (not on battlefield) — no same-turn carrier.
        _add_to_hand(game, card_db, "Ornithopter", controller=0)
        _setup_affinity_main(game, turn=3)

        player = EVPlayer(player_idx=0, deck_name="Affinity",
                          rng=random.Random(0))
        decision = player.decide_main_phase(game)

        cast_plating = (
            decision is not None
            and decision[0] == "cast_spell"
            and decision[1].name == "Cranial Plating"
        )
        assert not cast_plating, (
            f"AI chose {decision!r} — casting Cranial Plating with "
            f"no creature on battlefield to equip.  Plating delivers "
            f"zero same-turn value with no carrier, and the post-cast "
            f"state is identical to next turn's at the same mana cost, "
            f"so the cast should defer until a creature is in play."
        )

    def test_plating_ev_negative_without_carrier(self, card_db):
        """Tight spec: Plating's compute_play_ev with no carrier on
        the battlefield must score below the most permissive archetype
        pass_threshold (MIDRANGE at -3.0).  Current behaviour returns
        EV ≈ -2.0 which exceeds this bound, so the cast fires."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Darksteel Citadel", controller=0)
        _add_to_battlefield(game, card_db, "Darksteel Citadel", controller=0)
        _add_to_battlefield(game, card_db, "Mountain", controller=0)
        plating = _add_to_hand(game, card_db, "Cranial Plating",
                                controller=0)
        _setup_affinity_main(game, turn=3)

        snap = snapshot_from_game(game, 0)
        ev = compute_play_ev(plating, snap, archetype="combo",
                             game=game, player_idx=0)

        PASS_THRESHOLD_UPPER_BOUND = -3.0
        assert ev < PASS_THRESHOLD_UPPER_BOUND, (
            f"Cranial Plating with no creature on battlefield scored "
            f"EV={ev:.3f}, above the most permissive pass_threshold "
            f"({PASS_THRESHOLD_UPPER_BOUND}).  Under the EV-baseline "
            f"fix, a cast with no this-turn signal (no equip target, "
            f"no sacrifice outlet) must score below every archetype's "
            f"pass_threshold so it defers until a carrier exists."
        )

    def test_plating_cast_when_creature_and_equip_mana_present(
            self, card_db):
        """Regression: when a carrier IS on the battlefield AND equip
        mana is available this turn, Plating is a correct cast.  Its
        EV should remain reasonable (> most permissive pass_threshold)
        so the fix doesn't break legit Plating deployment."""
        game = GameState(rng=random.Random(0))
        # 5 lands = 5 mana: 2 to cast + {4} to equip this turn.
        for _ in range(5):
            _add_to_battlefield(game, card_db, "Darksteel Citadel",
                                 controller=0)
        _add_to_battlefield(game, card_db, "Ornithopter", controller=0)
        plating = _add_to_hand(game, card_db, "Cranial Plating",
                                controller=0)
        _setup_affinity_main(game, turn=5)

        snap = snapshot_from_game(game, 0)
        ev = compute_play_ev(plating, snap, archetype="combo",
                             game=game, player_idx=0)

        # With a carrier and equip mana both available, there's a
        # same-turn signal (equip), so EV must remain above
        # pass_threshold for any archetype.  Tightest lower bound:
        # above AGGRO/COMBO/STORM threshold of -5.0.
        assert ev > -5.0, (
            f"Regression: Plating with on-board Ornithopter + 5 mana "
            f"(2 cast, 4 equip) scored EV={ev:.3f} but must remain "
            f"castable (EV > -5.0) because a legit same-turn equip "
            f"signal is present."
        )
