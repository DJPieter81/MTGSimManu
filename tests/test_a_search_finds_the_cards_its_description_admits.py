"""A search finds, in its searcher's library, the cards its description
admits -- as many as its instruction names, the ones the searcher chooses
-- and the instruction after it puts them where it says (CR 701.19a-c,
701.20a).

"Whenever this creature enters or attacks, you may search your library for
up to two land cards, put them onto the battlefield tapped, then shuffle."
The searcher looks through their library and finds up to two land cards.
A search for cards with a stated quality may find fewer of them, even none
(CR 701.19b); a search for a number of cards alone finds that many while
the library holds them (CR 701.19c). Which cards are found is the
searcher's choice. The cards found go where the next instruction puts
them -- onto the battlefield (tapped), into the hand, into the graveyard
-- and the library is shuffled (CR 701.20a).

The defect: the search executor read two shapes, "a basic land card" (unit
LA) and the cards named like a chosen object (unit SX); every other search
was refused. So the attack carrier refused Primeval Titan's trigger, whose
enter and attack searches ran through a name-keyed handler that chose the
lands in the engine by fixed priorities, and Ranger-Captain of Eos's enter
search was a name-keyed handler too.

Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.callbacks import DefaultCallbacks
from engine.cards import CardInstance
from engine.combat_manager import CombatManager
from engine.game_state import GameState, Phase

TITAN = "Primeval Titan"
RANGER = "Ranger-Captain of Eos"


def _game(callbacks=None, phase=Phase.MAIN1):
    game = GameState(rng=random.Random(0), callbacks=callbacks)
    game.active_player = game.priority_player = 0
    game.current_phase = phase
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    c = CardInstance(template=tmpl, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _library(game, card_db, names, idx=0):
    return [_put(game, card_db, n, "library", idx) for n in names]


def _cast(game, card_db, name, lands, idx=0):
    for land in lands:
        _put(game, card_db, land, "battlefield", idx)
    card = _put(game, card_db, name, "hand", idx)
    assert game.cast_spell(idx, card, targets=[])
    while game.stack.items:
        game.resolve_stack()
    return card


def _titan(game, card_db):
    return _cast(game, card_db, TITAN, ["Forest"] * 6)


def _ranger(game, card_db):
    # "When this creature enters, you may search your library for a
    # creature card with mana value 1 or less, reveal it, put it into your
    # hand, then shuffle."
    return _cast(game, card_db, RANGER, ["Plains"] * 3)


# ── The rule ───────────────────────────────────────────────────────────

def test_a_search_finds_as_many_cards_as_it_names_and_puts_them_where_it_says(
        card_db):
    game = _game()
    lib = _library(game, card_db, ["Forest", "Lightning Bolt", "Island",
                                   "Mountain"])
    before = game.players[0].library_searches_this_game
    _titan(game, card_db)
    found = [c for c in lib if c.zone == "battlefield"]
    assert len(found) == 2
    assert all(c.template.is_land and c.tapped for c in found)
    assert lib[1].zone == "library"
    assert game.players[0].library_searches_this_game == before + 1


def test_an_attack_trigger_searches_as_attackers_are_declared(card_db):
    game = _game(phase=Phase.DECLARE_ATTACKERS)
    lib = _library(game, card_db, ["Forest", "Island", "Mountain"])
    titan = _put(game, card_db, TITAN, "battlefield")
    CombatManager().declare_attackers(game, [titan], 0)
    while game.stack.items:
        game.resolve_stack()
    assert sum(1 for c in lib if c.zone == "battlefield") == 2


def test_a_search_finds_only_the_cards_its_description_admits(card_db):
    game = _game()
    bears, memnite, forest = _library(game, card_db, [
        "Grizzly Bears", "Memnite", "Forest"])
    _ranger(game, card_db)
    # A creature card with mana value 1 or less: Memnite (0), never the
    # Bears (2) or the land.
    assert memnite.zone == "hand"
    assert (bears.zone, forest.zone) == ("library", "library")


def test_a_search_for_a_quality_may_find_none_and_still_counts(card_db):
    game = _game()
    lib = _library(game, card_db, ["Grizzly Bears", "Forest"])
    before = game.players[0].library_searches_this_game
    _ranger(game, card_db)
    assert all(c.zone == "library" for c in lib)
    assert game.players[0].library_searches_this_game == before + 1


class _LastCards(DefaultCallbacks):
    """Finds the last cards of the pool the engine offers."""

    def choose_cards(self, ctx, spec, pool, n):
        return list(pool)[-n:]


def test_the_searcher_chooses_the_cards_it_finds(card_db):
    game = _game(_LastCards())
    forest, island, mountain = _library(game, card_db, [
        "Forest", "Island", "Mountain"])
    _titan(game, card_db)
    assert forest.zone == "library"
    assert (island.zone, mountain.zone) == ("battlefield", "battlefield")


def test_the_ai_finds_the_lands_its_land_valuation_delivers(card_db):
    # The AI's pick is its one land valuation, one land at a time with the
    # picks already made (`ai.activation_ev._choose_land_delivery`).
    from ai.activation_ev import _choose_land_delivery
    from engine.game_runner import AICallbacks
    game = _game(AICallbacks())
    lib = _library(game, card_db, ["Forest", "Simic Growth Chamber",
                                   "Boros Garrison", "Island"])
    lands = list(lib)
    want = []
    for _ in range(2):
        land = _choose_land_delivery(game, 0, lands, batch=tuple(want))
        want.append(land)
        lands = [c for c in lands if c is not land]
    _titan(game, card_db)
    assert {c.instance_id for c in lib if c.zone == "battlefield"} \
        == {c.instance_id for c in want}


def test_a_search_another_player_makes_is_refused(card_db):
    # "When this creature enters, each opponent may search their library
    # for a creature card and put it onto the battlefield": the searcher
    # is not the controller, whose choice the search executor asks.
    from engine.effect_carrier import etb_plan
    template = card_db.get_card("Boldwyr Heavyweights")
    assert etb_plan(template.effects.faces[0]) is None


def test_a_search_for_either_of_two_subtypes_is_refused(card_db):
    # "Search your library for a Forest or Island card": the description
    # owner reads every subtype it names as required, and the grammar
    # types the union the same way, so the search would find only a land
    # with both. It stays with the owner that reads the union.
    from engine.effect_executors import library_search
    from engine.effect_spec import HostKind, Verb, iter_specs
    host = next(h for h in card_db.get_card("Misty Rainforest").effects.faces[0]
                if h.kind is HostKind.ACTIVATED)
    (search,) = [s for s in iter_specs(host.specs) if s.verb is Verb.SEARCH]
    assert search.filter.subtypes == frozenset({"forest", "island"})
    assert not library_search(search)


def test_a_put_of_found_cards_names_a_search_never_another_instruction(
        card_db):
    # "Return target permanent card ... from your graveyard to your hand.
    # Return that card to the battlefield instead if you control a Mount."
    # The second instruction has the shape of a put of found cards ("that
    # card", onto the battlefield), but the result it names is a move out
    # of a graveyard: the searched-card put would look for it in the
    # library and leave it where it is. Its host stays with the owner that
    # reads it.
    from engine import effect_resolver as er
    from engine.effect_carrier import etb_plan
    from engine.effect_spec import HostKind
    face = card_db.get_card("Shepherd of the Clouds").effects.faces[0]
    (host,) = [h for h in face if h.kind is HostKind.TRIGGERED]
    assert not er.can_execute(host, "card_flow")
    assert etb_plan(face) is None
    titan = card_db.get_card(TITAN).effects.faces[0]
    assert all(er.can_execute(h, "card_flow") for h in titan
               if h.kind is HostKind.TRIGGERED)


def test_the_registered_member_resolves_from_its_typed_host(card_db):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    from engine.effect_carrier import attack_plan, etb_plan
    for name in (TITAN, RANGER):
        assert not EFFECT_REGISTRY.has_handler(name, EffectTiming.ETB)
        assert etb_plan(card_db.get_card(name).effects.faces[0]) is not None
    assert attack_plan(card_db.get_card(TITAN).effects.faces[0]) is not None


# ── Auditor (CR 701.19a-b) ─────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f["rule"] for f in audit.drain() if f["kind"] == "violation"]


def test_the_audit_records_a_card_the_description_does_not_admit(
        card_db, audit, monkeypatch):
    from engine import effect_conditions
    monkeypatch.setattr(effect_conditions, "permanent_described",
                        lambda perm, f, source=None, bounds=None: True)
    game = _game()
    _library(game, card_db, ["Lightning Bolt", "Shock"])
    _titan(game, card_db)
    assert "701.19/search_found" in _violations(audit)


def test_the_audit_is_silent_when_the_search_holds(card_db, audit):
    game = _game()
    _library(game, card_db, ["Forest", "Lightning Bolt", "Island"])
    _titan(game, card_db)
    assert "701.19/search_found" not in _violations(audit)
