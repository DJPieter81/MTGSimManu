"""A land put onto the battlefield enters as a land played does (CR 305.4,
614.1d, 614.12, 603.6a, 714.3a).

A land an effect puts onto the battlefield is not played (CR 305.4), but it
enters like any land. Whatever puts it (a search, a hand put, a fetch, a
mass search), these apply:
- **Its own entry rules** (CR 614.1d, 614.12), each a replacement that
  applies however the land enters:
  - "This land enters tapped";
  - "As this land enters, you may pay 2 life. If you don't, it enters
    tapped";
  - "... enters tapped unless you control two or fewer other lands".
- **The instruction's "tapped".** It adds to those rules: a land put tapped
  enters tapped, and an untap payment that could buy nothing is not
  offered.
- **Its enter abilities** trigger (CR 603.6a): "When this land enters,
  surveil 1". A Saga gets its first lore counter and chapter I (CR 714.3a).

The defect: one owner (`LandManager.play_land`) applied these to a land
played, and four other entry paths each applied a part. The instruction
put (a search, unit SP's hand put) overwrote the land's own tapped state
with the instruction's and fired no enter trigger. So a Sakura-Tribe Scout
put a Gruul Turf untapped, and a Saga put by Primeval Titan or Scapeshift
got no lore counter. Found replaying Amulet Titan vs Eldrazi Tron s54500
(the seeded digest).

Card names are fixture carriers: every land put by an effect enters this
way.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, battlefield_lands=3):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(battlefield_lands):
        _add(game, card_db, "Forest", 0, "battlefield")
    for _ in range(3):
        _add(game, card_db, "Island", 0, "library")
    return game


def _scout_puts(game, card_db, land_name):
    """Sakura-Tribe Scout's "{T}: You may put a land card from your hand
    onto the battlefield." -- an untapped put -- with `land_name` the one
    land in hand."""
    from engine.activated_effects import resolve_activated_ability
    land = _add(game, card_db, land_name, 0, "hand")
    scout = _add(game, card_db, "Sakura-Tribe Scout", 0, "battlefield")
    ability = scout.template.activated_abilities[0]
    assert resolve_activated_ability(game, scout, 0, [], ability=ability)
    assert land.zone == "battlefield"
    return land


def _pay(game, answer):
    game.callbacks.decide_optional_cost = lambda *_a, **_k: answer


# ── Its own entry rules ───────────────────────────────────────────────

@pytest.mark.parametrize("name", ["Gruul Turf", "Underground Mortuary"])
def test_a_land_that_enters_tapped_is_tapped_however_it_is_put(card_db, name):
    game = _game(card_db)
    land = _scout_puts(game, card_db, name)
    assert land.tapped


def test_an_untap_payment_is_offered_to_a_land_put_untapped(card_db):
    game = _game(card_db)
    _pay(game, True)
    land = _scout_puts(game, card_db, "Stomping Ground")
    assert not land.tapped and game.players[0].life == 18


def test_a_declined_untap_payment_leaves_the_land_tapped(card_db):
    game = _game(card_db)
    _pay(game, False)
    land = _scout_puts(game, card_db, "Stomping Ground")
    assert land.tapped and game.players[0].life == 20


def test_a_land_put_tapped_is_offered_no_untap_payment(card_db):
    """Arboreal Grazer puts its land tapped: paying 2 life could buy
    nothing, so the payment is not offered."""
    game = _game(card_db)
    _pay(game, True)
    land = _add(game, card_db, "Stomping Ground", 0, "hand")
    grazer = _add(game, card_db, "Arboreal Grazer", 0, "hand")
    assert game.cast_spell(0, grazer, [])
    while not game.stack.is_empty:
        game.resolve_stack()
    assert land.zone == "battlefield"
    assert land.tapped and game.players[0].life == 20


@pytest.mark.parametrize("others,tapped", [(2, False), (3, True)])
def test_a_land_count_condition_is_read_as_the_land_enters(card_db, others,
                                                           tapped):
    """"This land enters tapped unless you control two or fewer other
    lands" (the Scout is no land)."""
    game = _game(card_db, battlefield_lands=others)
    land = _scout_puts(game, card_db, "Blackcleave Cliffs")
    assert land.tapped is tapped


def test_a_land_from_the_library_enters_by_its_own_rules_too(card_db):
    from engine.land_manager import LandManager
    game = _game(card_db)
    land = _add(game, card_db, "Gruul Turf", 0, "library")
    LandManager.put_land_from_library(game, land, 0, False, "search")
    assert land.zone == "battlefield" and land.tapped


# ── Its enter abilities ───────────────────────────────────────────────

def test_a_put_lands_enter_trigger_triggers(card_db):
    """Underground Mortuary: "When this land enters, surveil 1."."""
    game = _game(card_db)
    log = len(game.log)
    _scout_puts(game, card_db, "Underground Mortuary")
    assert any("surveil" in line for line in game.log[log:])


def _lore(card):
    from engine.saga import LORE_COUNTER
    return (card.other_counters or {}).get(LORE_COUNTER, 0)


def test_a_saga_put_from_hand_gets_its_first_lore_counter(card_db):
    game = _game(card_db)
    saga = _scout_puts(game, card_db, "Urza's Saga")
    assert _lore(saga) == 1


def test_a_saga_a_search_puts_gets_its_first_lore_counter(card_db):
    """Primeval Titan: "... search your library for up to two land cards,
    put them onto the battlefield tapped"."""
    game = _game(card_db)
    for card in list(game.players[0].library):
        game.players[0].library.remove(card)
    sagas = [_add(game, card_db, "Urza's Saga", 0, "library")
             for _ in range(2)]
    titan = _add(game, card_db, "Primeval Titan", 0, "hand")
    for _ in range(3):
        _add(game, card_db, "Forest", 0, "battlefield")
    assert game.cast_spell(0, titan, [])
    while not game.stack.is_empty:
        game.resolve_stack()
    put = [s for s in sagas if s.zone == "battlefield"]
    assert put and all(_lore(s) == 1 for s in put)


def test_lands_a_mass_search_puts_get_their_enter_triggers(card_db):
    """Scapeshift: "... put them onto the battlefield tapped": each land
    enters tapped and each Saga gets its first lore counter."""
    game = _game(card_db, battlefield_lands=4)
    for card in list(game.players[0].library):
        game.players[0].library.remove(card)
    for _ in range(4):
        _add(game, card_db, "Urza's Saga", 0, "library")
    for _ in range(4):
        _add(game, card_db, "Forest", 0, "battlefield")   # mana to cast
    shift = _add(game, card_db, "Scapeshift", 0, "hand")
    assert game.cast_spell(0, shift, [])
    while not game.stack.is_empty:
        game.resolve_stack()
    sagas = [c for c in game.players[0].battlefield
             if c.name == "Urza's Saga"]
    assert sagas
    assert all(c.tapped for c in sagas)
    assert all(_lore(c) == 1 for c in sagas)


# ── The auditor (CR 614.1d) ───────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "614.1d/land_entry"]


def test_the_audit_records_a_land_that_entered_untapped_against_its_text(
        card_db, audit, monkeypatch):
    from engine.land_manager import LandManager
    monkeypatch.setattr(LandManager, "settle_own_entry_tapped",
                        staticmethod(lambda game, card, idx:   # the defect
                                     setattr(card, "tapped", False)))
    game = _game(card_db)
    _scout_puts(game, card_db, "Gruul Turf")
    assert _findings(audit)


@pytest.mark.parametrize("name", ["Gruul Turf", "Forest"])
def test_the_audit_is_silent_when_the_land_enters_by_its_text(card_db, audit,
                                                              name):
    _scout_puts(_game(card_db), card_db, name)
    assert not _findings(audit)
