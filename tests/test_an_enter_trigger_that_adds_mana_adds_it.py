"""An enter trigger that adds mana adds it to its controller's mana pool
(CR 106.1, 106.4, 603.6a, 605.5a).

"When this creature enters, add {R}{G}." and "When this land enters, add
one mana of any color." are triggered abilities of the entering permanent,
not mana abilities (CR 605.5a). As they resolve, the mana goes into the
controller's pool, and it empties at the end of the step or phase (CR
106.4). The colour of "one mana of any color" is the controller's choice:
the engine asks the colour callback every entry colour choice uses
(`callbacks.choose_mana_color`).

The defect: the enter trigger types as ADD_MANA, but no carrier took it, so
Burning-Tree Emissary and Crumbling Vestige (Amulet Titan main deck x4)
added nothing.

Refused, and recorded:
- a trigger with an intervening "if" (Coal Stoker: "if you cast it from
  your hand");
- mana with a spending restriction (Boommobile).

Card names are fixture carriers: 14 pool cards add mana from an enter
trigger.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, zone, controller=0):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, forests=2):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(forests):
        _add(game, card_db, "Forest", "battlefield")
    for _ in range(3):
        _add(game, card_db, "Island", "library")
    return game


def _pool(game):
    return game.players[0].mana_pool


def _cast(game, card_db, name):
    card = _add(game, card_db, name, "hand")
    assert game.cast_spell(0, card, [])
    while not game.stack.is_empty:
        game.resolve_stack()
    return card


# ── The mana ──────────────────────────────────────────────────────────

def test_a_creatures_enter_trigger_adds_its_mana(card_db):
    """Burning-Tree Emissary: its {R/G}{R/G} is paid by two Forests; its
    enter trigger adds {R}{G}."""
    game = _game(card_db)
    _cast(game, card_db, "Burning-Tree Emissary")
    pool = _pool(game)
    assert (pool.red, pool.green, pool.total()) == (1, 1, 2)


def test_one_mana_of_any_color_is_the_controllers_choice(card_db):
    game = _game(card_db)
    asked = []

    def choose(game_, player_idx, source, options):
        asked.append(tuple(options))
        return "U"
    game.callbacks.choose_mana_color = choose
    vestige = _add(game, card_db, "Crumbling Vestige", "hand")
    game.play_land(0, vestige)
    assert asked and set(asked[0]) == set("WUBRG")
    assert (_pool(game).blue, _pool(game).total()) == (1, 1)


def test_a_land_put_onto_the_battlefield_adds_its_mana_too(card_db):
    """Its enter trigger triggers however it enters (CR 603.6a): a
    Sakura-Tribe Scout puts it."""
    from engine.activated_effects import resolve_activated_ability
    game = _game(card_db)
    _add(game, card_db, "Crumbling Vestige", "hand")
    scout = _add(game, card_db, "Sakura-Tribe Scout", "battlefield")
    ability = scout.template.activated_abilities[0]
    assert resolve_activated_ability(game, scout, 0, [], ability=ability)
    assert _pool(game).total() == 1


# ── Refused, never half-applied ───────────────────────────────────────

@pytest.mark.parametrize("name", ["Coal Stoker", "Boommobile"])
def test_a_conditional_or_restricted_mana_trigger_is_refused(card_db, name):
    from engine.effect_carrier import etb_plan
    assert etb_plan(card_db.get_card(name).effects.front()) is None


# ── The AI spends it ──────────────────────────────────────────────────

def test_the_ai_casts_with_the_mana_its_land_added(card_db):
    """One Forest and Crumbling Vestige: the Vestige enters tapped, and its
    trigger's mana with the Forest casts a two-mana creature this turn."""
    from ai.ev_player import EVPlayer
    game = _game(card_db, forests=1)
    game.callbacks.choose_mana_color = lambda *a: "G"
    vestige = _add(game, card_db, "Crumbling Vestige", "hand")
    bears = _add(game, card_db, "Grizzly Bears", "hand")
    game.play_land(0, vestige)
    ai = EVPlayer(player_idx=0, deck_name="Amulet Titan",
                  rng=random.Random(0))
    decision = ai.decide_main_phase(game)
    assert decision is not None and decision[0] == "cast_spell"
    assert decision[1] is bears


# ── The auditor (CR 106.1) ────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "106.1/mana_added"]


def test_the_audit_records_mana_that_did_not_reach_the_pool(card_db, audit,
                                                           monkeypatch):
    from engine.mana import ManaPool
    game = _game(card_db)
    card = _add(game, card_db, "Burning-Tree Emissary", "hand")
    assert game.cast_spell(0, card, [])               # paid before the defect
    monkeypatch.setattr(ManaPool, "add",
                        lambda self, color, amount=1: None)   # the defect
    while not game.stack.is_empty:
        game.resolve_stack()
    assert _findings(audit)


def test_the_audit_is_silent_when_the_mana_is_added(card_db, audit):
    game = _game(card_db)
    _cast(game, card_db, "Burning-Tree Emissary")
    assert not _findings(audit)
