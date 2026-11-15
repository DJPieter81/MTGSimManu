"""A card changing zones triggers the abilities that watch for it.

CR 603.2, 603.6: "whenever <a | one or more> <type> card(s) is/are put into
<whose> graveyard [from <zone>]", "... leave(s) <whose> graveyard" and "...
put into exile" trigger on the event. CR 700.14: "one or more" triggers once
per event however many cards it moved; "a card" once per card. One
instruction a spell or ability performs is one event, as is one
state-based-action pass (CR 704.3) and one cost payment; a move made apart
from them is an event by itself (`engine.zone_events`). A token is not a
card (CR 111.7).

The grammar typed these heads as event OTHER and no carrier took them:
Moonshadow ("remove a -1/-1 counter from it" whenever permanent cards are
put into your graveyard) never grew, and Murktide Regent never grew when
an instant or sorcery card left the graveyard. The class is the 57 pool
heads of the family, 31 typed by the closed table
(`structure._zone_change_event`), 26 cards the carrier takes
(`effect_carrier.dispatch_zone_change_triggers`). Card names are fixture
carriers.
"""
from __future__ import annotations

import random

import pytest

from engine import rules_audit
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    yield rules_audit
    rules_audit.reset()


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


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


def _perform(game, source, text, family="card_flow"):
    """Resolve printed `text` as an ability of `source` (CR 608.2)."""
    from engine import effect_resolver as er
    from engine.effect_grammar import parse_text_effects
    host = parse_text_effects(text).spell(0)
    assert er.resolve_ability(game, er.handle_of(source), source.controller,
                              host, (), family=family, source_object=source)


def _library(game, card_db, names, controller=0):
    for n in names:
        _add(game, card_db, n, "library", controller)


def _discard(game, card):
    game.zone_mgr.move_card(game, card, "hand", "graveyard", cause="discard")


# ── One or more: once per event ──────────────────────────────────────────

def test_one_or_more_triggers_once_for_an_event_moving_several_cards(card_db):
    game = _game()
    shadow = _add(game, card_db, "Moonshadow", "battlefield")
    _library(game, card_db, ["Swamp", "Grizzly Bears", "Swamp",
                             "Lightning Bolt"])
    _perform(game, shadow, "Mill four cards.")
    assert shadow.counter_count("-1/-1") == 5


def test_each_move_made_apart_is_its_own_event(card_db):
    game = _game()
    shadow = _add(game, card_db, "Moonshadow", "battlefield")
    for _ in range(2):
        _discard(game, _add(game, card_db, "Swamp", "hand"))
    assert shadow.counter_count("-1/-1") == 4


def test_one_state_based_action_pass_is_one_event(card_db):
    game = _game()
    shadow = _add(game, card_db, "Moonshadow", "battlefield")
    for _ in range(2):
        _add(game, card_db, "Grizzly Bears", "battlefield").damage_marked = 2
    game.check_state_based_actions()
    assert shadow.counter_count("-1/-1") == 5


# ── A card: once per card ────────────────────────────────────────────────

def test_a_card_triggers_once_for_each_card_it_names(card_db):
    game = _game()
    crusher = _add(game, card_db, "Countryside Crusher", "battlefield")
    _library(game, card_db, ["Forest", "Mountain", "Forest"])
    _perform(game, crusher, "Mill three cards.")
    assert crusher.counter_count("+1/+1") == 3


# ── What the head names ──────────────────────────────────────────────────

def test_the_card_types_the_head_names(card_db):
    game = _game()
    shadow = _add(game, card_db, "Moonshadow", "battlefield")
    _discard(game, _add(game, card_db, "Lightning Bolt", "hand"))
    assert shadow.counter_count("-1/-1") == 6


def test_whose_graveyard_the_head_names(card_db):
    game = _game()
    zombie = _add(game, card_db, "Vulturous Zombie", "battlefield")
    _library(game, card_db, ["Island", "Island"], controller=0)
    _perform(game, zombie, "Mill two cards.")
    assert zombie.counter_count("+1/+1") == 0
    _library(game, card_db, ["Island", "Island"], controller=1)
    _perform(game, zombie, "Target opponent mills two cards.".replace(
        "Target opponent", "Each opponent"))
    assert zombie.counter_count("+1/+1") == 2


def test_from_a_library_is_not_from_anywhere(card_db):
    game = _game()
    sidisi = _add(game, card_db, "Sidisi, Brood Tyrant", "battlefield")
    tokens = lambda: [c for c in game.players[0].battlefield
                      if getattr(c, "is_token", False)]
    _discard(game, _add(game, card_db, "Grizzly Bears", "hand"))
    assert tokens() == []
    _library(game, card_db, ["Grizzly Bears"])
    _perform(game, sidisi, "Mill a card.")
    assert len(tokens()) == 1


def test_a_card_leaving_a_graveyard(card_db):
    game = _game()
    regent = _add(game, card_db, "Murktide Regent", "battlefield")
    bolt = _add(game, card_db, "Lightning Bolt", "graveyard")
    bears = _add(game, card_db, "Grizzly Bears", "graveyard")
    game.zone_mgr.move_card(game, bears, "graveyard", "exile", cause="exile")
    assert regent.counter_count("+1/+1") == 0
    game.zone_mgr.move_card(game, bolt, "graveyard", "exile", cause="exile")
    assert regent.counter_count("+1/+1") == 1


def test_a_token_is_not_a_card(card_db):
    game = _game()
    shadow = _add(game, card_db, "Moonshadow", "battlefield")
    token = _add(game, card_db, "Grizzly Bears", "battlefield")
    token.is_token = True
    token.damage_marked = 2
    game.check_state_based_actions()
    assert shadow.counter_count("-1/-1") == 6


def test_the_source_counter_rider_is_read_as_the_event_happens(card_db):
    """"While ~ has a -1/-1 counter on it": with none, it does not
    trigger at all (no removal is attempted)."""
    game = _game()
    shadow = _add(game, card_db, "Moonshadow", "battlefield")
    shadow.adjust_counters("-1/-1", -6)
    before = len(game.log)
    _discard(game, _add(game, card_db, "Swamp", "hand"))
    assert not any("counter" in line and "Moonshadow" in line
                   for line in game.log[before:])


# ── The auditors ─────────────────────────────────────────────────────────

def test_the_auditor_sees_one_or_more_trigger_per_card(audit, monkeypatch,
                                                      card_db):
    """Re-create the defect: the batch rule read as once per card."""
    from engine import effect_carrier
    monkeypatch.setattr(effect_carrier, "fires_per_event",
                        lambda z, matched: len(matched))
    game = _game()
    shadow = _add(game, card_db, "Moonshadow", "battlefield")
    _library(game, card_db, ["Swamp", "Swamp"])
    _perform(game, shadow, "Mill two cards.")
    rules = {f["rule"] for f in audit.drain() if f["kind"] == "violation"}
    assert "700.14/one_or_more_once" in rules


def test_the_auditor_is_silent_when_the_batch_rule_holds(audit, card_db):
    game = _game()
    shadow = _add(game, card_db, "Moonshadow", "battlefield")
    _library(game, card_db, ["Swamp", "Swamp"])
    _perform(game, shadow, "Mill two cards.")
    rules = {f["rule"] for f in audit.drain() if f["kind"] == "violation"}
    assert "700.14/one_or_more_once" not in rules


def test_a_head_no_carrier_takes_is_counted_not_silent(audit, card_db):
    game = _game()
    _add(game, card_db, "Ark of Hunger", "battlefield")
    card = _add(game, card_db, "Grizzly Bears", "graveyard")
    game.zone_mgr.move_card(game, card, "graveyard", "exile", cause="exile")
    census = {(f["rule"], f["key"]) for f in audit.drain()
              if f["kind"] == "census"}
    assert ("603.2/zone_change_trigger_unresolved", "Ark of Hunger") in census
