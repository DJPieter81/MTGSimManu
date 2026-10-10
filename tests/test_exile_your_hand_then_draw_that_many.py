"""Exile a hand, draw that many, play the cards exiled this way (CR 406,
121.1, 601.2a, 608.2c).

"Exile all the cards from your hand, then draw that many cards. Until the
end of your next turn, you may play cards exiled this way." The effect
grammar types each part: an exile of the controller's whole hand, a draw
whose count is the number of cards the exile moved ("that many", CR
608.2c: the result of an earlier instruction), and a permission over the
cards that exile produced ("cards exiled this way" names the exile, not
the nearest instruction). The card-flow family performs them through the
owners: the zone funnel, the one draw owner, the rule-effect store.

The grammar refused two phrasings: the determiner "all the" ("all the
cards from your hand" is "all cards from your hand") and the bare
reference "cards exiled this way". The spell resolved to nothing: Ruby
Storm's four Hex Magic were blank, and the main phase deferred them.

A plain "draw N cards" stays on its legacy carrier: the family takes a
draw only when its count is an earlier switched exile's result (A38).

Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _specs(text):
    from engine.effect_grammar import parse_effects
    return list(parse_effects(text))


# ── The grammar ────────────────────────────────────────────────────────

def test_all_the_cards_is_all_cards():
    """The article after "all" changes nothing: the same exile of the
    controller's hand, then a draw of that many."""
    def shape(text):
        return [(s.verb, s.filter and (s.filter.zone, s.filter.owner),
                 s.amount and s.amount.kind) for s in _specs(text)]
    assert shape("Exile all the cards from your hand, then draw that many "
                 "cards.") == shape("Exile all cards from your hand, then "
                                    "draw that many cards.")


@pytest.mark.parametrize("text", [
    "Exile the top two cards of your library. You may play cards exiled "
    "this way until the end of your next turn.",
    "Exile the top two cards of your library. Until the end of your next "
    "turn, you may play cards exiled this way.",
])
def test_cards_exiled_this_way_is_a_permission_over_what_the_exile_moved(text):
    from engine.effect_model import DurationKind, ModKind
    from engine.effect_spec import RefKind, Verb
    specs = _specs(text)
    exile = next(s for s in specs if s.verb is Verb.EXILE)
    permit = next(s for s in specs if s.verb is Verb.CONTINUOUS)
    assert permit.payload.kind is ModKind.PERMIT
    assert permit.payload.action == "play"
    assert permit.ref.kind is RefKind.RESULT and permit.ref.index == exile.seq
    assert permit.duration.kind is DurationKind.UNTIL_END_OF_YOUR_NEXT_TURN


def test_this_way_names_the_exile_not_the_nearest_instruction(card_db):
    """Between the exile and the permission stands the draw: "cards
    exiled this way" still names the exile."""
    from engine.effect_spec import RefKind, Verb, iter_specs
    host = card_db.get_card("Hex Magic").effects.spell(0)
    specs = list(iter_specs(host.specs))
    exile = next(s for s in specs if s.verb is Verb.EXILE)
    draw = next(s for s in specs if s.verb is Verb.DRAW)
    permit = next(s for s in specs if s.verb is Verb.CONTINUOUS)
    assert (exile.filter.zone, exile.filter.owner) == ("hand", "you")
    assert draw.amount.ref.kind is RefKind.RESULT
    assert draw.amount.ref.index == exile.seq
    assert permit.ref.index == exile.seq


# ── The engine ─────────────────────────────────────────────────────────

def _game():
    g = GameState(rng=random.Random(0))
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    g.turn_number = 5
    return g


def _put(game, card_db, name, zone, idx=0):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _cast_hex(game, card_db, hand, library=("Island",) * 6):
    """Cast Hex Magic from a hand holding `hand` too, with the mana for it;
    resolve it through the real stack. Returns (hex, held cards, library)."""
    for _ in range(3):
        _put(game, card_db, "Mountain", "battlefield")
    lib = [_put(game, card_db, n, "library") for n in library]
    held = [_put(game, card_db, n, "hand") for n in hand]
    hexm = _put(game, card_db, "Hex Magic", "hand")
    assert game.cast_spell(0, hexm)
    game.resolve_stack()
    return hexm, held, lib


def test_exiling_your_hand_moves_every_card_and_draws_that_many(card_db):
    game = _game()
    hexm, held, lib = _cast_hex(game, card_db,
                                ["Lightning Bolt", "Grapeshot", "Mountain"])
    p = game.players[0]
    assert all(c.zone == "exile" and c in p.exile for c in held)
    assert p.hand == lib[:3]
    assert p.cards_drawn_this_turn == 3
    assert hexm in p.graveyard


def test_the_cards_exiled_this_way_may_be_played_until_your_next_turn_ends(
        card_db):
    from engine import rules_query
    from engine.turn_clock import Clock, ClockEvent, emit
    game = _game()
    _hexm, held, _lib = _cast_hex(game, card_db,
                                  ["Lightning Bolt", "Mountain"])
    permitted = set(rules_query.permitted_objects(game, 0))
    assert permitted == {c.instance_id for c in held}
    assert set(rules_query.permitted_objects(game, 1)) == set()
    for turn, player in ((5, 0), (6, 1)):
        game.turn_number = turn
        emit(game, ClockEvent(Clock.CLEANUP, player))
        assert set(rules_query.permitted_objects(game, 0)) == permitted
    game.turn_number = 7
    emit(game, ClockEvent(Clock.CLEANUP, 0))
    assert set(rules_query.permitted_objects(game, 0)) == set()


def test_an_empty_hand_exiles_nothing_and_draws_nothing(card_db):
    game = _game()
    hexm, _held, lib = _cast_hex(game, card_db, [])
    p = game.players[0]
    assert p.hand == [] and p.cards_drawn_this_turn == 0
    assert p.library == lib and hexm in p.graveyard


# ── The family boundary (A38) ──────────────────────────────────────────

def test_the_hand_exile_spell_reaches_the_card_flow_family(card_db):
    from engine.effect_carrier import spell_family
    assert spell_family(card_db.get_card("Hex Magic")) == "card_flow"


@pytest.mark.parametrize("name", ["Divination", "Consider"])
def test_a_plain_draw_spell_stays_on_its_legacy_carrier(card_db, name):
    from engine.effect_carrier import spell_family
    assert spell_family(card_db.get_card(name)) is None


# ── Auditor (CR 406) ───────────────────────────────────────────────────

def test_the_audit_records_a_card_left_behind_by_exile_all_cards_from_your_hand(
        card_db, monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    game = _game()
    real = game.zone_mgr.move_card
    skipped = []

    def move(g, card, src, dst, **kw):
        if src == "hand" and dst == "exile" and not skipped:
            skipped.append(card)            # the defect: one card stays
            return False
        return real(g, card, src, dst, **kw)
    monkeypatch.setattr(game.zone_mgr, "move_card", move)
    _cast_hex(game, card_db, ["Lightning Bolt", "Mountain"])
    assert "406/exile_all_cards_from_hand" in [
        f["rule"] for f in rules_audit.drain()]


def test_the_audit_is_silent_when_the_whole_hand_is_exiled(card_db,
                                                          monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    game = _game()
    _cast_hex(game, card_db, ["Lightning Bolt", "Mountain"])
    assert "406/exile_all_cards_from_hand" not in [
        f["rule"] for f in rules_audit.drain()]
