"""Shared resolver for the symmetric "destroy all creatures" board-sweep
mechanic (CR 701.7 destroy / CR 704 SBA cleanup of the resulting deaths).

# Mechanic under test

`engine/card_effects.py` registered per-card SPELL_RESOLVE handlers (Damnation,
Supreme Verdict) whose bodies were a byte-identical
`_resolve_board_sweep(action="destroy", types={"creature"})` call. Per
CLAUDE.md's ABSTRACTION CONTRACT that duplication is the same smell the ratchet
targets for `card.name ==` checks, even though `EFFECT_REGISTRY.register(...)`
is invisible to `tools/check_abstraction.py`'s regex.

The symmetric "destroy all creatures" shape is now classified once at DB load
by `oracle_parser.parse_board_sweep` into the typed field
`CardTemplate.board_sweep_data` and dispatched (no oracle inspection at resolve
time) through the single shared owner `card_effects._resolve_board_sweep`. Six
DB cards populate the field — a genuine mechanic class. Having proven the two
registered wrath handlers redundant with the typed path (the integration tests
below), their registrations are deleted.

Conditional/asymmetric sweeps (a color filter, an MV-gated energy wipe, an
opponents-only or power-gated scope) carry resolution-time parameters the plain
typed field does not model and keep their own handlers. Card names appear only
as fixture carriers; the mechanic under test is "a spell whose entire
resolution is destroying every creature", not any specific card.
"""
from __future__ import annotations

import random

import pytest

from engine.card_effects import EFFECT_REGISTRY, EffectTiming
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState
from engine.oracle_parser import parse_board_sweep
from engine.oracle_resolver import resolve_spell_from_oracle


class TestParseBoardSweep:
    @pytest.mark.parametrize("oracle", [
        "Destroy all creatures.",
        "Destroy all creatures. They can't be regenerated.",
        "This spell can't be countered.\nDestroy all creatures.",
    ])
    def test_symmetric_destroy_all_creatures_is_recognised(self, oracle):
        assert parse_board_sweep(oracle) == {"action": "destroy",
                                             "types": ["creature"]}

    @pytest.mark.parametrize("oracle,why", [
        ("Destroy all creatures your opponents control.", "asymmetric scope"),
        ("Destroy all creatures with power 4 or greater.", "power condition"),
        ("Destroy all nonland permanents.", "different type"),
        ("Destroy all creatures. You draw a card for each destroyed this way.",
         "extra resolution rider"),
        ("Destroy all artifacts and enchantments.", "not creatures"),
    ])
    def test_conditional_or_other_sweep_is_refused(self, oracle, why):
        assert parse_board_sweep(oracle) is None, why


def _game_with_creatures(n_each=2):
    game = GameState(rng=random.Random(0))
    ct = CardTemplate(
        name="Bear", card_types=[CardType.CREATURE], mana_cost=ManaCost(generic=2),
        supertypes=[], subtypes=[], power=2, toughness=2, loyalty=None,
        keywords=set(), abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text="", tags=set(),
    )
    for pl in (0, 1):
        for _ in range(n_each):
            c = CardInstance(template=ct, owner=pl, controller=pl,
                             instance_id=game.next_instance_id(),
                             zone="battlefield")
            game.players[pl].battlefield.append(c)
    return game


class TestRegisteredWrathHandlersRetired:
    @pytest.mark.parametrize("card_name", ["Damnation", "Supreme Verdict"])
    def test_no_dedicated_handler_remains(self, card_name):
        assert not EFFECT_REGISTRY.has_handler(
            card_name, EffectTiming.SPELL_RESOLVE), (
            f"{card_name!r} still has a dedicated SPELL_RESOLVE handler")

    @pytest.mark.parametrize("card_name", ["Damnation", "Supreme Verdict"])
    def test_wrath_destroys_all_creatures_via_generic_path(
            self, card_db, card_name):
        game = _game_with_creatures(n_each=2)
        spell = CardInstance(template=card_db.get_card(card_name), owner=0,
                             controller=0, instance_id=game.next_instance_id(),
                             zone="stack")
        handled = resolve_spell_from_oracle(game, spell, 0, targets=None)
        assert handled is True
        game.check_state_based_actions()
        assert not game.players[0].creatures, "own creatures should be swept"
        assert not game.players[1].creatures, "opponent creatures should be swept"
