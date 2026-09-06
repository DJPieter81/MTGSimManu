"""Shared resolver for the "destroy/exile target <permanent> [with mana value
N/X or less]" targeted-removal mechanic (CR 701.7 destroy / 701.19 exile).

# Mechanic under test

The single most common removal shape in Magic — a spell whose entire resolution
is destroying or exiling one chosen permanent of a stated type, optionally
gated by a printed mana-value ceiling. ~100 Modern instants/sorceries share it,
yet only ~6 had per-card EFFECT_REGISTRY handlers; the rest silently resolved
to NOTHING, because no generic destroy/exile-target branch existed. So making
the shape typed is a large correctness fix as well as a consolidation.

`oracle_parser.parse_targeted_removal` classifies the shape at DB load into
`CardTemplate.targeted_removal_data` ({action, types, mv}), and
`resolve_spell_from_oracle` dispatches off that typed field (no oracle
inspection at resolve time) into the single shared owner
`card_effects._resolve_nonland_permanent_removal`, targeting the opponent (the
sim's removal convention). Abrupt Decay (destroy nonland, MV<=3) and March of
Otherworldly Light (exile artifact/creature/enchantment, MV<=X) had the two
cleanly-generic registered handlers; both are deleted, verified redundant with
the typed path here. Conditions the plain field cannot carry keep their
handlers: Assassin's Trophy (search rider), Fatal Push (revolt "if it has"),
Prismatic Ending (Converge colors-spent), Leyline Binding (ETB linked exile).

Card names are fixture carriers; the mechanic under test is the removal shape,
not any card.
"""
from __future__ import annotations

import random

import pytest

from engine.card_effects import EFFECT_REGISTRY, EffectTiming
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState
from engine.oracle_parser import parse_targeted_removal
from engine.oracle_resolver import resolve_spell_from_oracle


class TestParseTargetedRemoval:
    @pytest.mark.parametrize("oracle,expected", [
        ("Kill It deals nothing.\nDestroy target creature.",
         {"action": "destroy", "types": ["creature"], "mv": None}),
        ("Exile target creature or planeswalker.",
         {"action": "exile", "types": ["creature", "planeswalker"], "mv": None}),
        ("Destroy target nonland permanent with mana value 3 or less.",
         {"action": "destroy", "types": ["permanent_nonland"], "mv": 3}),
        ("Exile target artifact, creature, or enchantment with mana value X or less.",
         {"action": "exile", "types": ["artifact", "creature", "enchantment"], "mv": "x"}),
        ("This spell can't be countered.\nDestroy target permanent.",
         {"action": "destroy", "types": ["permanent"], "mv": None}),
    ])
    def test_removal_shape_is_classified(self, oracle, expected):
        assert parse_targeted_removal(oracle) == expected

    @pytest.mark.parametrize("oracle,why", [
        ("Destroy target permanent an opponent controls. Its controller may "
         "search their library for a basic land card.", "search rider"),
        ("Destroy target creature. You draw a card.", "draw rider"),
        ("Destroy target creature if it has mana value 2 or less.",
         "conditional 'if it has', not 'with'"),
        ("Destroy all creatures.", "sweep, not targeted"),
        ("Return target creature to its owner's hand.", "bounce, not destroy/exile"),
        ("Destroy target land.", "land destruction (own typed path)"),
    ])
    def test_non_clean_removal_is_refused(self, oracle, why):
        assert parse_targeted_removal(oracle) is None, why


def _game_with_opp_permanent(name_types, power=2, toughness=2, cmc=2):
    game = GameState(rng=random.Random(0))
    tmpl = CardTemplate(
        name="Target", card_types=name_types, mana_cost=ManaCost(generic=cmc),
        supertypes=[], subtypes=[], power=power, toughness=toughness,
        loyalty=None, keywords=set(), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False, oracle_text="", tags=set(),
    )
    perm = CardInstance(template=tmpl, owner=1, controller=1,
                        instance_id=game.next_instance_id(), zone="battlefield")
    game.players[1].battlefield.append(perm)
    return game, perm


def _cast(game, card_db, name, targets, x_value=0):
    spell = CardInstance(template=card_db.get_card(name), owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="stack")
    return resolve_spell_from_oracle(game, spell, 0, targets=targets, x_value=x_value)


class TestRegisteredRemovalHandlersRetired:
    @pytest.mark.parametrize("card_name", ["Abrupt Decay",
                                           "March of Otherworldly Light"])
    def test_no_dedicated_handler_remains(self, card_name):
        assert not EFFECT_REGISTRY.has_handler(
            card_name, EffectTiming.SPELL_RESOLVE), (
            f"{card_name!r} still has a dedicated SPELL_RESOLVE handler")

    def test_abrupt_decay_destroys_low_mv_nonland_via_generic_path(self, card_db):
        game, perm = _game_with_opp_permanent([CardType.CREATURE], cmc=2)
        handled = _cast(game, card_db, "Abrupt Decay", [perm.instance_id])
        assert handled is True
        game.check_state_based_actions()
        assert perm not in game.players[1].battlefield

    def test_abrupt_decay_spares_high_mv_permanent(self, card_db):
        game, perm = _game_with_opp_permanent([CardType.CREATURE], cmc=5)
        handled = _cast(game, card_db, "Abrupt Decay", [perm.instance_id])
        assert handled is True
        game.check_state_based_actions()
        assert perm in game.players[1].battlefield, "MV 5 > 3 must survive"


class TestPreviouslyNoOpRemovalNowResolves:
    """The correctness half: unregistered removal of this shape used to resolve
    to nothing. It now destroys/exiles the chosen target."""

    @pytest.mark.parametrize("card_name", ["Murder", "Hero's Downfall"])
    def test_generic_creature_removal_kills_target(self, card_db, card_name):
        game, perm = _game_with_opp_permanent([CardType.CREATURE])
        handled = _cast(game, card_db, card_name, [perm.instance_id])
        assert handled is True
        game.check_state_based_actions()
        assert perm not in game.players[1].battlefield
