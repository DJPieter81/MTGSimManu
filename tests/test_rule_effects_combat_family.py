"""Attack prohibitions and combat-damage prevention are continuous effects
(CR 509.4 / 615 / 611.2a): a resolved "creatures can't attack [you] this
turn" or "prevent all combat damage this turn" is an Effect whose Duration
is the game turn, read through `rules_query`.

Rules pinned:
* a "this turn" combat effect ends as the game turn ends — whoever's turn
  it was cast in (the defect: a Fog cast during the opponent's combat
  also prevented the caster's own attacks on the next turn, because the
  per-player flag was cleared only at each player's own untap);
* the three shapes register PROHIBIT attack / PROHIBIT be_attacked /
  PREVENT_DAMAGE effects with THIS_TURN duration;
* the legacy attribute setters register the same effect.
Oracle strings are fixture carriers; the class must hold for unseen cards.
"""
from __future__ import annotations

import random

from engine import rules_query
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.combat_manager import CombatManager
from engine.effect_model import DurationKind, ModKind
from engine.game_state import GameState, Phase
from engine.oracle_resolver import resolve_spell_from_oracle


def _creature(game, controller, power=3, toughness=3):
    t = CardTemplate(name=f"Bear{game.next_instance_id()}",
                     card_types=[CardType.CREATURE], mana_cost=ManaCost(generic=2),
                     supertypes=[], subtypes=[], power=power, toughness=toughness,
                     loyalty=None, keywords=set(), abilities=[], color_identity=set(),
                     produces_mana=[], enters_tapped=False, oracle_text="", tags=set())
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game; c.enter_battlefield(); c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _resolve(game, controller, oracle):
    t = CardTemplate(name="Clause", card_types=[CardType.INSTANT], mana_cost=ManaCost(generic=1),
                     supertypes=[], subtypes=[], power=None, toughness=None, loyalty=None,
                     keywords=set(), abilities=[], color_identity=set(), produces_mana=[],
                     enters_tapped=False, oracle_text=oracle, tags=set())
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="stack")
    c._game_state = game
    resolve_spell_from_oracle(game, c, controller, [])


def _combat_effects(game):
    return [e for e in game.continuous_effects.rule_effects(game)
            if e.modification.kind in (ModKind.PROHIBIT, ModKind.PREVENT_DAMAGE)
            and e.modification.action in ("attack", "be_attacked", "combat")]


def test_a_fog_cast_in_the_opponents_combat_does_not_prevent_the_casters_next_attack():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    attacker = _creature(game, 0)
    game.active_player = 1
    _resolve(game, 0, "Prevent all combat damage that would be dealt this turn.")
    assert rules_query.combat_damage_prevented(game)
    # P1's turn ends; P0's turn begins.
    game.cleanup_step(); game.active_player = 0; game.untap_step(0)
    assert not rules_query.combat_damage_prevented(game)
    life1 = game.players[1].life
    cm = CombatManager()
    cm.declare_attackers(game, [attacker], active_player=0)
    cm.declare_blockers(game, {})
    cm.resolve_combat_damage(game)
    assert game.players[1].life == life1 - 3


def test_the_three_shapes_are_this_turn_effects():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    _resolve(game, 0, "Creatures can't attack this turn.")
    _resolve(game, 0, "Creatures can't attack you this turn.")
    _resolve(game, 0, "Prevent all combat damage that would be dealt this turn.")
    effs = _combat_effects(game)
    assert {(e.modification.kind, e.modification.action) for e in effs} == {
        (ModKind.PROHIBIT, "attack"), (ModKind.PROHIBIT, "be_attacked"),
        (ModKind.PREVENT_DAMAGE, "combat")}
    assert all(e.duration.kind is DurationKind.THIS_TURN for e in effs)
    assert rules_query.attack_prohibited(game, 0) and rules_query.attack_prohibited(game, 1)
    assert rules_query.attacking_player_prohibited(game, 0)
    assert not rules_query.attacking_player_prohibited(game, 1)


def test_an_attack_lock_ends_with_the_turn_it_was_cast_in():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    _resolve(game, 0, "Creatures can't attack this turn.")
    game.cleanup_step(); game.active_player = 1; game.untap_step(1)
    assert not rules_query.attack_prohibited(game, 0)
    assert not rules_query.attack_prohibited(game, 1)
    assert not _combat_effects(game)


def test_the_legacy_setters_register_this_turn_effects():
    game = GameState(rng=random.Random(0))
    game.players[1].cannot_attack_this_turn = True
    game.players[0].cannot_be_attacked_this_turn = True
    game.players[0].combat_damage_prevented_this_turn = True
    assert rules_query.attack_prohibited(game, 1) and not rules_query.attack_prohibited(game, 0)
    assert rules_query.attacking_player_prohibited(game, 0)
    assert rules_query.combat_damage_prevented(game)
    assert len(_combat_effects(game)) == 3
