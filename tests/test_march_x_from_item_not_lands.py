"""S-8 rider — March of Otherworldly Light reads X from land count
instead of the actual X paid (also closes audit P1 bug B).

Survey: docs/diagnostics/2026-04-20_latent_bug_survey.md §S-8
Precedent: engine/card_effects.py:615 — Wrath of the Skies already
reads `x_val = item.x_value if item and hasattr(item, 'x_value') else 0`.

Oracle (March of Otherworldly Light): "Exile target artifact,
creature, or enchantment with mana value X or less."

Bug: `engine/card_effects.py:675` uses
  `x_val = len(game.players[controller].lands)`
which ignores the X actually paid.  With 5 lands on the battlefield,
casting March for X=0 still exiles permanents with CMC ≤ 5 instead
of the declared ≤ 0 — a strict oracle violation, and a free-X removal
spell against any mid-range creature on the battlefield.

These tests exercise the resolver with the stack item's x_value
pinned manually after cast:
  (1) X=0 → CMC ≤ 0 only — Ragavan (CMC 1) must survive;
  (2) X=1 → CMC ≤ 1, picks highest-threat target (Ragavan),
      Ornithopter stays because the spell exiles one target.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.card_effects import march_otherworldly_light_resolve
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


def _setup_main_phase(game):
    game.players[0].deck_name = "Azorius Control"
    game.players[1].deck_name = "Affinity"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    game.players[0].lands_played_this_turn = 1


class TestMarchXFromItemNotLands:
    """March of Otherworldly Light's X must come from the mana actually
    paid (stack_item.x_value), not from len(battlefield lands)."""

    def test_x_zero_exiles_only_cmc_zero_permanents(self, card_db):
        """Player has 5 Plains.  Cast March with X pinned to 0.  Opp
        has Ornithopter (CMC 0) and Ragavan (CMC 1).  Oracle: "exile
        target ... with mana value X or less" → at X=0 the only valid
        target is Ornithopter.  Ragavan must survive."""
        game = GameState(rng=random.Random(0))
        for _ in range(5):
            _add_to_battlefield(game, card_db, "Plains", controller=0)
        march = _add_to_hand(game, card_db, "March of Otherworldly Light",
                             controller=0)
        ornithopter = _add_to_battlefield(game, card_db, "Ornithopter",
                                           controller=1)
        ragavan = _add_to_battlefield(game, card_db,
                                       "Ragavan, Nimble Pilferer",
                                       controller=1)
        _setup_main_phase(game)

        cast_ok = game.cast_spell(0, march, targets=[])
        assert cast_ok, "March cast should succeed with 5 Plains"
        assert game.stack.items, "Stack should contain March"

        # Pin the stack item's X to 0 — simulates the AI casting March
        # for X=0 specifically. With the bug, the resolver ignores this
        # and reads len(lands)=5 instead.
        stack_item = game.stack.items[-1]
        stack_item.x_value = 0

        march_otherworldly_light_resolve(
            game, march, controller=0, targets=None, item=stack_item
        )

        opp_bf_names = [c.name for c in game.players[1].battlefield]
        assert ragavan.name in opp_bf_names, (
            f"Ragavan (CMC 1) was exiled even though March was cast for "
            f"X=0.  Oracle: 'Exile target ... with mana value X or less' "
            f"means at X=0, only CMC-0 permanents are valid targets — "
            f"Ragavan (CMC 1) must survive.\n"
            f"Resolver read x_val from len(lands)=5 instead of "
            f"item.x_value=0, so any CMC ≤ 5 permanent became a valid "
            f"target and the highest-threat one was picked (Ragavan).\n"
            f"Fix engine/card_effects.py:675 to mirror Wrath's pattern "
            f"at line 615: "
            f"`x_val = item.x_value if item and hasattr(item, 'x_value') "
            f"else 0`.\n"
            f"Opp battlefield after resolve: {opp_bf_names}"
        )
        assert ornithopter.name not in opp_bf_names, (
            f"Regression: X=0 should still exile the one valid target "
            f"(Ornithopter, CMC 0).\n"
            f"Opp battlefield: {opp_bf_names}"
        )

    def test_x_one_exiles_highest_threat_cmc_one_permanent(self, card_db):
        """Regression anchor: when X=1 is paid, March must still
        function as one-shot removal on the highest-value CMC ≤ 1
        target.  With Ornithopter (CMC 0) + Ragavan (CMC 1) on opp
        board, X=1 should pick Ragavan (higher threat value) and leave
        Ornithopter alive."""
        game = GameState(rng=random.Random(0))
        for _ in range(5):
            _add_to_battlefield(game, card_db, "Plains", controller=0)
        march = _add_to_hand(game, card_db, "March of Otherworldly Light",
                             controller=0)
        ornithopter = _add_to_battlefield(game, card_db, "Ornithopter",
                                           controller=1)
        ragavan = _add_to_battlefield(game, card_db,
                                       "Ragavan, Nimble Pilferer",
                                       controller=1)
        _setup_main_phase(game)

        cast_ok = game.cast_spell(0, march, targets=[])
        assert cast_ok
        stack_item = game.stack.items[-1]
        stack_item.x_value = 1

        march_otherworldly_light_resolve(
            game, march, controller=0, targets=None, item=stack_item
        )

        opp_bf_names = [c.name for c in game.players[1].battlefield]
        assert ragavan.name not in opp_bf_names, (
            f"Regression: March at X=1 did not exile Ragavan.\n"
            f"Opp battlefield: {opp_bf_names}"
        )
        assert ornithopter.name in opp_bf_names, (
            f"Regression: March at X=1 should exile exactly one "
            f"permanent (the highest-threat CMC ≤ 1 target, Ragavan), "
            f"leaving Ornithopter alive.\n"
            f"Opp battlefield: {opp_bf_names}"
        )
