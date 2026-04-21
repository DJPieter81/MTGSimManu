"""Phase 4 — RC-5 of the block strategy audit.

When our clock is faster than opp's post-combat clock, blocking wastes
creatures that could attack for lethal. The existing gate only covered
"lethal on board this turn"; RC-5 broadens it to clock-vs-clock.

Reference: audits/BLOCK_STRATEGY_AUDIT.md §Phase 4.
"""
from __future__ import annotations

import random
import pytest

from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _mk_player():
    return EVPlayer(player_idx=0, deck_name="Boros Energy",
                    rng=random.Random(0))


class TestRaceWhenClockFavourable:
    def test_life_20_vs_opp_life_5_with_6_onboard_power_races(self, card_db):
        """Me: life=20, 6 on-board power (3x Watchwolf 3/3 = 9 actually;
        use 3x Memnite (1/1) + Ragavan stand-in → 3 power... simpler: use
        2x Watchwolf = 6 power). Opp: life=5, single big attacker swinging.

        my_clock = 5/6 → 1 turn to kill (next turn we swing).
        opp_clock = (20 − incoming) / opp_on_board_power_after.

        With a 10-power attacker coming in, my_life_after=10.
        opp_on_board_power_after = 10 (the attacker untaps next turn).
        opp_clock = 10/10 = 1.

        my_clock (0.83) ≤ opp_clock (1.0) → race.
        s65000 T5 anchor."""
        game = GameState(rng=random.Random(0))
        game.players[0].life = 20
        game.players[1].life = 5

        # Me: 2x Watchwolf (3/3 each) = 6 on-board power
        _add_to_battlefield(game, card_db, "Watchwolf", controller=0)
        _add_to_battlefield(game, card_db, "Watchwolf", controller=0)

        attacker = _add_to_battlefield(game, card_db,
                                        "Sojourner's Companion",
                                        controller=1)
        attacker.temp_power_mod = 6
        attacker.temp_toughness_mod = 6
        assert attacker.power == 10

        player = _mk_player()
        me = game.players[0]
        opp = game.players[1]
        assert player._racing_to_win(game, me, opp, [attacker]), (
            "_racing_to_win: my_clock (5/6≈0.83) ≤ opp_clock (10/10=1.0); "
            "must race."
        )

        blocks = player.decide_blockers(game, [attacker])
        assert blocks == {}, (
            f"expected empty block map — we should race opp's life=5 "
            f"rather than chump. Got {blocks}."
        )


class TestNoRaceWhenLethalIncoming:
    def test_life_5_with_strong_offense_still_blocks(self, card_db):
        """Me: life=5, 2x Watchwolf (6 power). Opp: attacker would deal
        10 damage — lethal overrides race."""
        game = GameState(rng=random.Random(0))
        game.players[0].life = 5
        game.players[1].life = 5

        _add_to_battlefield(game, card_db, "Watchwolf", controller=0)
        _add_to_battlefield(game, card_db, "Watchwolf", controller=0)

        attacker = _add_to_battlefield(game, card_db,
                                        "Sojourner's Companion",
                                        controller=1)
        attacker.temp_power_mod = 6
        attacker.temp_toughness_mod = 6
        assert attacker.power == 10

        player = _mk_player()
        me = game.players[0]
        opp = game.players[1]
        assert not player._racing_to_win(game, me, opp, [attacker]), (
            "_racing_to_win: incoming 10 >= life 5, cannot race"
        )

        blocks = player.decide_blockers(game, [attacker])
        # Lethal — emergency path should fire and assign a blocker
        assert blocks, (
            f"life=5 vs lethal 10-power: must block (emergency fires). "
            f"Got {blocks}."
        )


class TestNoRaceWhenClockUnfavourable:
    def test_life_20_vs_10_10_with_only_one_weak_creature_blocks(self, card_db):
        """Me: life=20, single Memnite (1/1). Opp: life=20, 10-power
        attacker. my_clock = 20/1 = 20 turns. opp_clock = 10/10 = 1 turn.
        Clearly don't race."""
        game = GameState(rng=random.Random(0))
        game.players[0].life = 20
        game.players[1].life = 20

        _add_to_battlefield(game, card_db, "Memnite", controller=0)

        attacker = _add_to_battlefield(game, card_db,
                                        "Sojourner's Companion",
                                        controller=1)
        attacker.temp_power_mod = 6
        attacker.temp_toughness_mod = 6

        player = _mk_player()
        me = game.players[0]
        opp = game.players[1]
        assert not player._racing_to_win(game, me, opp, [attacker]), (
            "_racing_to_win must reject: my_clock=20 > opp_clock=1"
        )
