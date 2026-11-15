"""An ability that moves its own card out of the graveyard works from the
graveyard, and only there (CR 113.6k, 603.2, 400.7).

CR 113.6k: an ability whose cost or effect moves the object it is on out of
a zone functions only in that zone, unless its trigger condition puts the
object into that zone ("when ~ dies, return ~ from your graveyard ..." is a
leaves-the-battlefield ability and looks back from the battlefield, CR
603.10a).

The engine's rule:
- The grammar types the host's home zone (`AbilityEffects.from_zone`):
  "graveyard" for a triggered ability whose effect returns "~ from your
  graveyard", unless its head puts the card there.
- The step and cast carriers read a card in a graveyard for the triggered
  abilities homed there, and a permanent only for those homed on the
  battlefield. The controller of a graveyard card's ability is the card's
  owner (CR 113.8).
- "Return ~ from your graveyard to the battlefield [tapped] / to your hand"
  moves the card through the zone funnel, and a card put onto the
  battlefield runs its enter effects through the one enter owner. A card
  that left the graveyard before the ability resolves is a new object, and
  the ability does nothing (CR 400.7).

The defect: every such host was homed on the battlefield, and every carrier
scanned only the battlefield, so no graveyard ability ever functioned: a
Phoenix never returned, an Eidolon never came back to hand.

Class: 72 pool cards print a triggered ability returning their own card
from the graveyard; 23 of them on a step or cast head (this cut). Card
names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState, Phase
from engine.mana import ManaCost


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    return game


def _place(game, tmpl, zone, idx=0):
    c = CardInstance(template=tmpl, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _put(game, card_db, name, zone, idx=0):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return _place(game, tmpl, zone, idx)


def _synthetic(oracle, name="Home Probe"):
    return CardTemplate(
        name=name, card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=2), supertypes=[], subtypes=[], power=2,
        toughness=2, loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text=oracle, tags=set())


def _step(game, step, active=0):
    from engine.turn_clock import Clock, ClockEvent, emit
    game.active_player = game.priority_player = active
    game.current_phase = Phase(step)
    emit(game, ClockEvent(Clock.STEP_BEGINS, active, step=step))


def _home(template):
    (h,) = [h for h in template.effects.faces[0]
            if h.kind.value == "triggered"]
    return h.from_zone


# ── The grammar: the host's home zone ─────────────────────────────────

@pytest.mark.parametrize("name", ["Arclight Phoenix", "Silversmote Ghoul",
                                  "Verdant Eidolon", "Squee, Goblin Nabob"])
def test_a_trigger_returning_its_own_card_from_the_graveyard_is_homed_there(
        card_db, name):
    assert _home(card_db.get_card(name)) == "graveyard"


def test_a_dies_trigger_returning_the_card_is_homed_on_the_battlefield():
    # CR 113.6k's exception: the head puts the card into the graveyard, so
    # the ability looks back from the battlefield (CR 603.10a).
    t = _synthetic("When Home Probe dies, return Home Probe from your "
                   "graveyard to your hand.")
    assert _home(t) == "battlefield"


# ── The step carrier ──────────────────────────────────────────────────

def test_a_graveyard_step_trigger_returns_its_card_when_its_condition_holds(
        card_db):
    game = _game()
    ghoul = _put(game, card_db, "Silversmote Ghoul", "graveyard")
    game.players[0].life_gained_this_turn = 3
    _step(game, "end_step")
    assert ghoul.zone == "battlefield" and ghoul in game.players[0].battlefield
    assert ghoul.tapped, "returned to the battlefield tapped, as printed"


def test_a_graveyard_step_trigger_does_nothing_when_its_condition_fails(
        card_db):
    game = _game()
    ghoul = _put(game, card_db, "Silversmote Ghoul", "graveyard")
    game.players[0].life_gained_this_turn = 2
    _step(game, "end_step")
    assert ghoul.zone == "graveyard"


def test_a_graveyard_step_trigger_waits_for_its_owners_turn(card_db):
    game = _game()
    ghoul = _put(game, card_db, "Silversmote Ghoul", "graveyard")
    game.players[0].life_gained_this_turn = 3
    _step(game, "end_step", active=1)
    assert ghoul.zone == "graveyard"


def test_the_permanent_does_not_perform_its_graveyard_ability(card_db):
    # Guard (not red first): the battlefield copy has no ability to return
    # itself "from your graveyard", and the graveyard copy is untouched.
    game = _game()
    on_board = _put(game, card_db, "Silversmote Ghoul", "battlefield")
    game.players[0].life_gained_this_turn = 3
    _step(game, "end_step")
    assert on_board.zone == "battlefield" and not on_board.tapped


def test_a_returned_card_runs_its_enter_effects(card_db, monkeypatch):
    game = _game()
    ghoul = _put(game, card_db, "Silversmote Ghoul", "graveyard")
    game.players[0].life_gained_this_turn = 3
    entered = []
    real = game._handle_permanent_etb
    monkeypatch.setattr(game, "_handle_permanent_etb",
                        lambda card, ctl, *a, **k: (entered.append(card),
                                                    real(card, ctl, *a, **k)))
    _step(game, "end_step")
    assert entered == [ghoul]


# ── The cast carrier ──────────────────────────────────────────────────

def _cast_board(card_db):
    game = _game()
    for _ in range(3):
        _put(game, card_db, "Mountain", "battlefield")
        _put(game, card_db, "Plains", "battlefield")
    return game


def test_a_graveyard_cast_trigger_returns_its_card_to_hand(card_db):
    game = _cast_board(card_db)
    eidolon = _put(game, card_db, "Verdant Eidolon", "graveyard")
    game.callbacks.choose_optional_effect = lambda *a, **k: True
    helix = _put(game, card_db, "Lightning Helix", "hand")
    assert game.cast_spell(0, helix, [-2])
    assert eidolon.zone == "hand" and eidolon in game.players[0].hand


def test_a_spell_outside_the_head_does_not_return_it(card_db):
    game = _cast_board(card_db)
    eidolon = _put(game, card_db, "Verdant Eidolon", "graveyard")
    game.callbacks.choose_optional_effect = lambda *a, **k: True
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    assert game.cast_spell(0, bolt, [-2])
    assert eidolon.zone == "graveyard"


def test_an_opponents_cast_does_not_return_your_card(card_db):
    game = _cast_board(card_db)
    eidolon = _put(game, card_db, "Verdant Eidolon", "graveyard", idx=1)
    game.callbacks.choose_optional_effect = lambda *a, **k: True
    helix = _put(game, card_db, "Lightning Helix", "hand")
    assert game.cast_spell(0, helix, [-2])
    assert eidolon.zone == "graveyard"


# ── The rules audit (CR 113.6k) ───────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    from engine import rules_audit
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _home_violations(audit):
    return [f for f in audit.drain()
            if f["rule"] == "113.6k/ability_home_zone"
            and f["kind"] == "violation"]


def test_the_audit_is_silent_when_an_ability_functions_from_its_home(
        card_db, audit):
    game = _game()
    _put(game, card_db, "Silversmote Ghoul", "graveyard")
    game.players[0].life_gained_this_turn = 3
    _step(game, "end_step")
    assert not _home_violations(audit)


def test_the_audit_fires_when_an_ability_functions_outside_its_home(
        card_db, audit, monkeypatch):
    from engine import effect_carrier
    # The defect, restored: every host read whatever zone its card is in.
    monkeypatch.setattr(effect_carrier, "_homed_hosts",
                        lambda hosts, zone: tuple(hosts))
    game = _game()
    _put(game, card_db, "Silversmote Ghoul", "battlefield")
    game.players[0].life_gained_this_turn = 3
    _step(game, "end_step")
    assert _home_violations(audit)
