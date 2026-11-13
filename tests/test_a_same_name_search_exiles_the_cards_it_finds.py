"""A search for the cards with the same name as an object exiles the cards
it finds from the zones it names; then that player shuffles (CR 701.19,
701.20, 608.2c).

"Search <player>'s graveyard, hand, and library for <any number of | all>
cards with the same name as that <card> and exile them. Then that player
shuffles." is one search (CR 701.19a) of the named player's zones for the
cards that share a name with the object the instruction names (its target,
or the object an earlier instruction moved -- last known information, CR
608.2h). In a hidden zone the searcher may fail to find some of them (CR
701.19b); the searcher here finds every one. The cards found are exiled
(CR 406), and the player whose library was searched shuffles it (CR
701.20a).

The defect: the grammar refused the multi-zone search ("search_zone"), so
Surgical Extraction (six registered sideboards) and its class resolved as
nothing at all: the spell was cast, resolved and changed no zone.

Card names are fixture carriers: 18 pool cards search a player's zones for
the cards named like an object.
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


def _game(card_db):
    """Player 0 with a Swamp; player 1 with a Lightning Bolt in each of
    graveyard, hand and library (two in the library), plus Islands."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(3):
        _add(game, card_db, "Swamp", 0, "battlefield")
        _add(game, card_db, "Island", 0, "library")
    bolts = {"graveyard": [_add(game, card_db, "Lightning Bolt", 1,
                                "graveyard")],
             "hand": [_add(game, card_db, "Lightning Bolt", 1, "hand")],
             "library": [_add(game, card_db, "Lightning Bolt", 1, "library")
                         for _ in range(2)]}
    others = [_add(game, card_db, "Island", 1, "library") for _ in range(4)]
    others.append(_add(game, card_db, "Counterspell", 1, "hand"))
    others.append(_add(game, card_db, "Counterspell", 1, "graveyard"))
    return game, bolts, others


def _resolve(game):
    while not game.stack.is_empty:
        game.resolve_stack()


# ── The parse ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["Surgical Extraction", "Extirpate"])
def test_the_search_names_the_owners_zones_and_the_objects_name(card_db,
                                                                 name):
    from engine.effect_spec import RefKind, Verb
    host = card_db.get_card(name).effects.spell(0)
    verbs = [s.verb for s in host.specs]
    assert Verb.UNMODELLED not in verbs, [s.raw for s in host.specs]
    search = next(s for s in host.specs if s.verb is Verb.SEARCH)
    f = search.filter
    assert f.zones == frozenset({"graveyard", "hand", "library"})
    assert f.owner.kind is RefKind.OWNER_OF
    assert f.named is not None and f.named.kind is RefKind.TARGET
    shuffle = next(s for s in host.specs if s.verb is Verb.SHUFFLE)
    assert shuffle.actor.kind is RefKind.OWNER_OF


# ── Resolution ────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["Surgical Extraction", "Extirpate"])
def test_every_card_with_that_name_is_exiled_from_the_owners_zones(card_db,
                                                                   name):
    game, bolts, others = _game(card_db)
    spell = _add(game, card_db, name, 0, "hand")
    target = bolts["graveyard"][0]
    assert game.cast_spell(0, spell, [target.instance_id])
    _resolve(game)
    found = [c for cs in bolts.values() for c in cs]
    assert all(c.zone == "exile" for c in found), \
        [(c.zone) for c in found]
    assert all(c.zone != "exile" for c in others)


def test_the_owner_shuffles_the_library_that_was_searched(card_db,
                                                          monkeypatch):
    game, bolts, others = _game(card_db)
    shuffled = []
    real = game.shuffle_library
    monkeypatch.setattr(game, "shuffle_library",
                        lambda idx: (shuffled.append(idx), real(idx)))
    spell = _add(game, card_db, "Surgical Extraction", 0, "hand")
    assert game.cast_spell(0, spell, [bolts["graveyard"][0].instance_id])
    _resolve(game)
    assert shuffled == [1]


def test_with_its_target_gone_the_spell_does_nothing(card_db):
    """CR 608.2b: the target left the graveyard before resolution."""
    game, bolts, others = _game(card_db)
    spell = _add(game, card_db, "Surgical Extraction", 0, "hand")
    target = bolts["graveyard"][0]
    assert game.cast_spell(0, spell, [target.instance_id])
    game.zone_mgr.move_card(game, target, "graveyard", "exile",
                            cause="exiled in response")
    _resolve(game)
    assert bolts["hand"][0].zone == "hand"
    assert all(c.zone == "library" for c in bolts["library"])


# ── The auditor (CR 701.19, 608.2c: "exile them") ─────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "701.19/found_exiled"]


def test_the_audit_records_a_found_card_left_in_its_zone(card_db, audit,
                                                         monkeypatch):
    game, bolts, others = _game(card_db)
    real = game.zone_mgr.move_card

    def drops_hand(g, card, src, dst, **kw):          # the defect
        if src == "hand" and dst == "exile":
            return False
        return real(g, card, src, dst, **kw)
    monkeypatch.setattr(game.zone_mgr, "move_card", drops_hand)
    spell = _add(game, card_db, "Surgical Extraction", 0, "hand")
    assert game.cast_spell(0, spell, [bolts["graveyard"][0].instance_id])
    _resolve(game)
    assert bolts["hand"][0].zone == "hand"
    assert _findings(audit)


def test_the_audit_is_silent_when_every_card_found_is_exiled(card_db, audit):
    game, bolts, others = _game(card_db)
    spell = _add(game, card_db, "Surgical Extraction", 0, "hand")
    assert game.cast_spell(0, spell, [bolts["graveyard"][0].instance_id])
    _resolve(game)
    assert not _findings(audit)
