"""Damage whose target the AI declares goes where it is worth the most.

CR 602.2b: an activated "deals N damage to any target" declares its target
when it is activated. One AI owner (`ai/damage_targets`) chooses it in the
attack chooser's currency, the opponent's position value lost: N damage to
the face (`face_damage_value`) against each opposing creature or
planeswalker the damage destroys (`permanent_threat`). Lethal damage goes to
the face. A permanent the source may not target, whose ward its controller
would not pay, or that the damage does not destroy is never the target.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

from engine.cards import ActivationEffectKind, CardInstance, Keyword
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.game_state import GameState, Phase

FACE = PLAYER_TARGET_OPPONENT


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


def _put(game, card_db, idx, name):
    c = CardInstance(template=card_db.get_card(name), owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[idx].battlefield.append(c)
    return c


def _declared(game, source):
    """The targets the activation candidate for `source` declares, or None
    when no activation of it is offered."""
    from ai.activation_ev import activation_candidates
    from ai.ev_evaluator import snapshot_from_game
    for perm, _i, targets, _ev, _r in activation_candidates(
            game, 0, snapshot_from_game(game, 0)):
        if perm is source:
            return list(targets)
    return None


def _pinger_vs(card_db, creature, life=20):
    game = _game()
    pinger = _put(game, card_db, 0, "Prodigal Pyromancer")
    victim = _put(game, card_db, 1, creature)
    game.players[1].life = life
    return game, pinger, victim


def test_a_ping_destroys_a_creature_exactly_when_its_threat_exceeds_the_face_damage(card_db):
    from ai.damage_targets import face_damage_value
    from ai.permanent_threat import permanent_threat
    aimed_at = set()
    for creature in ("Ragavan, Nimble Pilferer", "Signal Pest"):
        game, pinger, victim = _pinger_vs(card_db, creature)
        worth = permanent_threat(victim, game.players[1], game)
        face = face_damage_value(game, 1, 1)
        expected = [victim.instance_id] if worth > face else [FACE]
        assert _declared(game, pinger) == expected, (creature, worth, face)
        aimed_at.add(worth > face)
    assert aimed_at == {True, False}, "both sides of the rule are exercised"


def test_lethal_damage_goes_to_the_face(card_db):
    game, pinger, _victim = _pinger_vs(card_db, "Ragavan, Nimble Pilferer",
                                       life=1)
    assert _declared(game, pinger) == [FACE]


def test_a_creature_its_source_may_not_target_is_never_the_target(card_db):
    game, pinger, victim = _pinger_vs(card_db, "Ragavan, Nimble Pilferer")
    victim.temp_keywords.add(Keyword.HEXPROOF)
    assert _declared(game, pinger) == [FACE]


def test_a_ward_its_controller_would_not_pay_rules_the_creature_out(card_db, monkeypatch):
    """Armguard Familiar has ward {2}: unpaid, it counters the ability (CR
    702.21a). With no mana left the ping goes to the face; with the mana and
    a controller who pays, the creature is the target."""
    game, pinger, victim = _pinger_vs(card_db, "Armguard Familiar")
    assert _declared(game, pinger) == [FACE]
    for _ in range(2):
        _put(game, card_db, 0, "Mountain")
    monkeypatch.setattr(game.callbacks, "decide_optional_cost",
                        lambda *a, **k: True)
    assert _declared(game, pinger) == [victim.instance_id]


def test_damage_that_does_not_destroy_a_creature_never_aims_at_it(card_db):
    game, pinger, victim = _pinger_vs(card_db, "Thraben Inspector")   # a 1/2
    assert _declared(game, pinger) == [FACE]
    victim.damage_marked = 1
    assert _declared(game, pinger) == [victim.instance_id]


def test_damage_destroys_at_lethal_or_from_deathtouch_never_an_indestructible_creature(card_db):
    from ai.damage_targets import damage_destroys
    game = _game()
    plain = SimpleNamespace(has_deathtouch=False)
    deadly = SimpleNamespace(has_deathtouch=True)
    one_toughness = _put(game, card_db, 1, "Ragavan, Nimble Pilferer")
    two_toughness = _put(game, card_db, 1, "Thraben Inspector")
    assert damage_destroys(one_toughness, 1, plain)
    assert not damage_destroys(two_toughness, 1, plain)
    assert damage_destroys(two_toughness, 1, deadly)          # CR 702.2b
    assert not damage_destroys(two_toughness, 0, deadly)      # CR 120.8
    one_toughness.temp_keywords.add(Keyword.INDESTRUCTIBLE)   # CR 702.12b
    assert not damage_destroys(one_toughness, 1, plain)
    assert not damage_destroys(one_toughness, 1, deadly)
    walker = _put(game, card_db, 1, "Wrenn and Six")
    loyalty = walker.loyalty_counters
    assert damage_destroys(walker, loyalty, plain)            # CR 704.5i
    assert not damage_destroys(walker, loyalty - 1, plain)


def test_an_any_target_slot_admits_players_creatures_and_planeswalkers_within_its_scope(card_db):
    """CR 115.4: "any target" is a creature, a planeswalker or a player,
    read from current types; a controller scope narrows it. One owner
    (`target_solver`) answers for the AI's aim and the resolution binding."""
    from engine.target_solver import (TargetRequirement, slot_admits_permanent,
                                      slot_admits_player)
    game = _game()
    any_target = TargetRequirement(zone="any", types=frozenset({"any"}))
    theirs = TargetRequirement(zone="any", types=frozenset({"any"}),
                               owner_scope="opponent")
    creature = _put(game, card_db, 1, "Memnite")
    walker = _put(game, card_db, 1, "Wrenn and Six")
    land = _put(game, card_db, 1, "Mountain")
    mine = _put(game, card_db, 0, "Memnite")
    assert slot_admits_permanent(any_target, creature, 0)
    assert slot_admits_permanent(any_target, walker, 0)
    assert not slot_admits_permanent(any_target, land, 0)
    assert slot_admits_permanent(any_target, mine, 0)
    assert not slot_admits_permanent(theirs, mine, 0)
    assert slot_admits_player(any_target, 0, 0)
    assert slot_admits_player(theirs, 1, 0) and not slot_admits_player(theirs, 0, 0)
    creature_only = TargetRequirement(zone="battlefield",
                                      types=frozenset({"creature"}))
    assert not slot_admits_player(creature_only, 1, 0)
    assert not slot_admits_permanent(creature_only, walker, 0)


def test_a_sacrifice_outlet_destroys_a_threat_rather_than_chip_the_face(card_db):
    game = _game()
    outlet = _put(game, card_db, 0, "Goblin Bombardment")
    _put(game, card_db, 0, "Memnite")
    threat = _put(game, card_db, 1, "Ragavan, Nimble Pilferer")
    assert _declared(game, outlet) == [threat.instance_id]


def test_an_aimed_activation_resolves_on_its_chosen_creature(card_db):
    from engine.activation import ActivationManager
    game, pinger, victim = _pinger_vs(card_db, "Ragavan, Nimble Pilferer")
    targets = _declared(game, pinger)
    assert targets == [victim.instance_id]
    ability = next(a for a in pinger.template.activated_abilities
                   if a.effect_kind is ActivationEffectKind.DAMAGE_ANY_TARGET)
    assert ActivationManager.activate(game, 0, pinger, ability, targets)
    while not game.stack.is_empty:
        game.resolve_stack()
    game.check_state_based_actions()
    assert victim.zone == "graveyard"
    assert game.players[1].life == 20
