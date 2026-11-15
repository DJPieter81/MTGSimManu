""""Whenever a creature attacks you [or a planeswalker you control], <effect>"
is a triggered ability observing the attack declaration (CR 508.1 / 603.2),
modelled as an OBSERVE effect: a permanent's printed observer is a static
effect of that permanent (CR 611.3a); a resolved "until your next turn,
whenever …" is a stored effect with that duration (CR 611.2b).

Rules pinned:
* the observer shape is typed once: scope (you | you or a planeswalker
  you control), effect (P/T modification of the attacker until end of
  turn | the attacker's controller loses N life [and you gain M]),
  duration; other effects are refused;
* it fires once per creature attacking the covered player (or, with the
  wider scope, a planeswalker that player controls);
* a resolved observer lasts through the opponent's turn and ends as its
  controller's turn begins; a static one lasts while its source is there;
* life loss goes through the one life-loss owner (engine/damage.lose_life).
Card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from engine import rules_audit
from engine.cards import CardInstance
from engine.clause_resolver import resolve_clause
from engine.combat_manager import CombatManager
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_attack_observer


_TAMIYO = ("Until your next turn, whenever a creature attacks you or a planeswalker "
           "you control, it gets -1/-0 until end of turn.")
_MIASMA = "Whenever a creature attacks you, its controller loses 1 life."
_RAVENS = ("Whenever a creature attacks you or a planeswalker you control, that "
           "creature's controller loses 1 life and you gain 1 life.")


def test_the_observer_shapes_are_typed():
    assert parse_attack_observer(_TAMIYO) == {
        'scope': 'you_or_pw', 'duration': 'until_next_turn',
        'effect': {'kind': 'pt_mod', 'power': -1, 'toughness': 0}}
    assert parse_attack_observer(_MIASMA) == {
        'scope': 'you', 'duration': 'static',
        'effect': {'kind': 'drain', 'loss': 1, 'gain': 0}}
    assert parse_attack_observer(_RAVENS)['effect'] == {'kind': 'drain', 'loss': 1, 'gain': 1}
    # A loyalty line's text carries no trailing period.
    assert parse_attack_observer(_TAMIYO.rstrip('.')) == parse_attack_observer(_TAMIYO)


def test_other_observer_effects_are_refused():
    assert parse_attack_observer(
        "Whenever a creature attacks you or a planeswalker you control, investigate.") is None
    assert parse_attack_observer(
        "Whenever a creature attacks you or a planeswalker you control, you may draw a card.") is None
    assert parse_attack_observer("Whenever this creature attacks, draw a card.") is None


# ── fixtures ─────────────────────────────────────────────────────────

def _put(game, card_db, name, controller, text=None):
    tpl = card_db.get_card(name)
    if text is not None:
        tpl = copy.copy(tpl)
        tpl.oracle_text = text
        tpl.attack_observer = parse_attack_observer(text)
    c = CardInstance(template=tpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 1
    game.current_phase = Phase.MAIN1
    return game


def _resolve(game, card_db, text, controller=0):
    tpl = copy.copy(card_db.get_card("Opt"))
    tpl.oracle_text = text
    tpl.has_scry = False
    tpl.attack_observer = parse_attack_observer(text)
    card = CardInstance(template=tpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="stack")
    card._game_state = game
    return resolve_clause(game, card, controller, [])


def _attack(game, attackers, active, targets=None):
    cm = CombatManager()
    cm.declare_attackers(game, attackers, active_player=active, attack_targets=targets)
    game.continuous_effects.recalculate(game)
    return cm


def _next_turn(game, idx):
    game.cleanup_step()
    game.active_player = idx
    game.untap_step(idx)
    game.continuous_effects.recalculate(game)


# ── resolved observer (until your next turn) ─────────────────────────

def test_a_resolved_observer_shrinks_each_creature_attacking_its_controller(card_db):
    game = _game()
    game.active_player = 0
    assert _resolve(game, card_db, _TAMIYO)
    _next_turn(game, 1)
    a = _put(game, card_db, "Grizzly Bears", 1)
    b = _put(game, card_db, "Grizzly Bears", 1)
    _attack(game, [a, b], active=1)
    assert (a.power, b.power) == (1, 1)
    assert a.toughness == 2


def test_a_resolved_observer_ends_as_its_controllers_turn_begins(card_db):
    game = _game()
    game.active_player = 0
    _resolve(game, card_db, _TAMIYO)
    _next_turn(game, 1)
    _next_turn(game, 0)
    _next_turn(game, 1)
    a = _put(game, card_db, "Grizzly Bears", 1)
    _attack(game, [a], active=1)
    assert a.power == 2


# ── static observer (a permanent's printed ability) ──────────────────

def test_a_static_drain_observer_fires_per_attacker_while_its_source_is_there(card_db):
    game = _game()
    miasma = _put(game, card_db, "Grizzly Bears", 0, text=_MIASMA)
    a = _put(game, card_db, "Grizzly Bears", 1)
    b = _put(game, card_db, "Grizzly Bears", 1)
    life = game.players[1].life
    _attack(game, [a, b], active=1)
    assert game.players[1].life == life - 2
    game.players[0].battlefield.remove(miasma)
    _attack(game, [a], active=1)
    assert game.players[1].life == life - 2


def test_a_drain_observer_with_a_gain_rider_gains_its_controller_life(card_db):
    game = _game()
    _put(game, card_db, "Grizzly Bears", 0, text=_RAVENS)
    a = _put(game, card_db, "Grizzly Bears", 1)
    mine = game.players[0].life
    _attack(game, [a], active=1)
    assert game.players[0].life == mine + 1


def test_the_you_scope_does_not_fire_on_an_attack_at_a_planeswalker(card_db):
    game = _game()
    _put(game, card_db, "Grizzly Bears", 0, text=_MIASMA)
    pw = _put(game, card_db, "Karn, the Great Creator", 0)
    a = _put(game, card_db, "Grizzly Bears", 1)
    life = game.players[1].life
    _attack(game, [a], active=1, targets={a.instance_id: pw})
    assert game.players[1].life == life


def test_the_wider_scope_fires_on_an_attack_at_a_planeswalker(card_db):
    game = _game()
    _put(game, card_db, "Grizzly Bears", 0, text=_RAVENS)
    pw = _put(game, card_db, "Karn, the Great Creator", 0)
    a = _put(game, card_db, "Grizzly Bears", 1)
    life = game.players[1].life
    _attack(game, [a], active=1, targets={a.instance_id: pw})
    assert game.players[1].life == life - 1


# ── auditor ──────────────────────────────────────────────────────────

def _audited_attack(game, card_db, monkeypatch, broken):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    _put(game, card_db, "Grizzly Bears", 0, text=_MIASMA)
    a = _put(game, card_db, "Grizzly Bears", 1)
    if broken:
        monkeypatch.setattr(CombatManager, "_fire_attack_observers",
                            lambda self, game, attackers: 0)
    _attack(game, [a], active=1)
    rules = {f["rule"] for f in rules_audit.drain() if f["kind"] == "violation"}
    rules_audit.reset()
    return rules


def test_attack_observer_audit_sees_an_observer_that_did_not_fire(card_db, monkeypatch):
    assert "603.2/attack_observer_fired" in _audited_attack(_game(), card_db, monkeypatch, True)


def test_attack_observer_audit_is_silent_when_observers_fire(card_db, monkeypatch):
    assert "603.2/attack_observer_fired" not in _audited_attack(_game(), card_db, monkeypatch, False)


def test_a_loyalty_line_carrying_the_shape_resolves_as_a_clause(card_db):
    from engine.cards import LoyaltyEffectKind
    walkers = [t for t in card_db.cards.values()
               if any(a.effect_kind is LoyaltyEffectKind.CLAUSE
                      and a.clause is not None and a.clause.attack_observer
                      for a in (t.back_face_loyalty_abilities or {}).values())
               or any(a.effect_kind is LoyaltyEffectKind.CLAUSE
                      and a.clause is not None and a.clause.attack_observer
                      for a in (getattr(t, 'loyalty_abilities', None) or {}).values())]
    assert walkers, "no loyalty line with an attack observer became executable"
