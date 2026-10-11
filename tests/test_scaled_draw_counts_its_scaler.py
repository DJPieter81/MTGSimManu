"""A scaled draw — "draw N card(s) for each <X>" — draws N × count(X)
(CR 608.2: the effect does what its text says, counted as it resolves).

The card-flow handler read "draw a card" and ignored the "for each …"
scaler, so every such clause drew a flat card. Found when loyalty lines
became clauses: a "surveil 2, then draw a card for each opponent who lost
life this turn" line drew a card every turn whether or not anyone lost life
(Dimir Midrange +11pp in one step). Class: 82 pool cards print a scaled
draw; spells had the same over-draw.

Countable scalers: "<type or subtype> you control" and "opponent who lost
life this turn". Any other scaler is refused — the draw does not happen —
rather than drawing a flat card. Card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from engine.cards import CardInstance
from engine.clause_resolver import clause_is_executable, resolve_clause
from engine.game_state import GameState, Phase


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for idx in (0, 1):
        for _ in range(10):
            _put(game, card_db, "Island", idx, "library")
    return game


def _put(game, card_db, name, controller, zone):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller], zone).append(c)
    return c


def _clause(card_db, text):
    tpl = copy.copy(card_db.get_card("Opt"))
    tpl.oracle_text = text
    tpl.has_scry = False
    tpl.has_surveil = False
    tpl.loot_data = None
    return CardInstance(template=tpl, owner=0, controller=0, instance_id=999,
                        zone="stack")


def test_a_draw_for_each_opponent_who_lost_life_counts_those_opponents(card_db):
    text = "Draw a card for each opponent who lost life this turn."
    game = _game(card_db)
    hand = len(game.players[0].hand)
    resolve_clause(game, _clause(card_db, text), 0, [])
    assert len(game.players[0].hand) == hand          # nobody lost life
    game.players[1].life_lost_this_turn = 3
    resolve_clause(game, _clause(card_db, text), 0, [])
    assert len(game.players[0].hand) == hand + 1


def test_a_draw_for_each_permanent_type_you_control_draws_that_many(card_db):
    game = _game(card_db)
    for _ in range(3):
        _put(game, card_db, "Grizzly Bears", 0, "battlefield")
    _put(game, card_db, "Grizzly Bears", 1, "battlefield")   # not yours
    hand = len(game.players[0].hand)
    resolve_clause(game, _clause(card_db, "Draw a card for each creature you control."), 0, [])
    assert len(game.players[0].hand) == hand + 3


def test_an_uncountable_scaler_draws_nothing_instead_of_a_flat_card(card_db):
    text = "Draw a card for each creature that died this turn."
    game = _game(card_db)
    hand = len(game.players[0].hand)
    resolve_clause(game, _clause(card_db, text), 0, [])
    assert len(game.players[0].hand) == hand
    assert not clause_is_executable(_clause(card_db, text))


def test_an_unscaled_draw_is_unchanged(card_db):
    game = _game(card_db)
    hand = len(game.players[0].hand)
    resolve_clause(game, _clause(card_db, "Draw two cards."), 0, [])
    assert len(game.players[0].hand) == hand + 2


def test_an_other_scaler_excludes_the_source_permanent(card_db):
    game = _game(card_db)
    src = _put(game, card_db, "Grizzly Bears", 0, "battlefield")
    _put(game, card_db, "Grizzly Bears", 0, "battlefield")
    clause = _clause(card_db, "Draw a card for each other creature you control.")
    clause.instance_id = src.instance_id
    hand = len(game.players[0].hand)
    resolve_clause(game, clause, 0, [])
    assert len(game.players[0].hand) == hand + 1


def test_an_uncountable_scaler_is_recorded_in_the_audit_census(card_db, monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    game = _game(card_db)
    resolve_clause(game, _clause(card_db, "Draw a card for each creature that died this turn."), 0, [])
    rows = rules_audit.drain()
    rules_audit.reset()
    assert any(r["rule"] == "608.2/uncountable_scaler" for r in rows)
