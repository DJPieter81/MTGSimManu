"""A card brought from outside the game is one its owner chooses from its
sideboard (CR 400.11, 400.11a, 400.11b).

Outside the game is not a zone (CR 400.11). In a sanctioned game the cards
a player owns outside the game are its sideboard (CR 400.11a, 100.4a), and
a card brought into the game stays in it (CR 400.11b):
- "You may play a card you own from outside the game this turn" lets its
  controller play ONE such card this turn -- any of them, chosen as it is
  played; the permission is spent by that play and ends with the turn.
- "You may reveal a <description> card you own from outside the game and
  put it into your hand": its controller chooses one card the description
  admits, and it goes to their hand.
- Never a card from the library.

Typed at load by the effect grammar: the filter leaf reads "from outside
the game" as the sideboard zone; the permission payload reads a
description. Performed by the card-flow executors: the permission
(`effect_executors.outside_permit`, a one-play `permit_play` over the
sideboard, spent by `ContinuousEffectsManager.spend_play`) and the reveal
(`execute_reveal`) with its move (`revealed_to_hand`). The cast reads a
sideboard card a permission names (`CastManager.can_cast`, the cast path,
`rules_query.permitted_cards`). Auditors `400.11a/play_from_outside_the_game`
and `400.11a/brought_from_the_sideboard` restate the rule from raw fields.

The defect (docs/diagnostics/2026-10-15_ruby_storm_goldfish_speed.md): the
name-keyed Wish handler chose the card in the engine from a list of card
names and literals, fell back to the LIBRARY, and put the card into the
hand for good; a reveal from outside the game was not performed at all.

Card names are fixture carriers: 40 pool cards bring a card from outside
the game; the play-this-turn and reveal-into-hand shapes are typed here.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
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
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for p in (0, 1):
        for _ in range(5):
            _add(game, card_db, "Island", p, "library")
    return game


def _resolve(game):
    while not game.stack.is_empty:
        game.resolve_stack()


def _wished(card_db, lands=9):
    """Player 0 casts Wish with Grapeshot and Empty the Warrens in its
    sideboard and a Grapeshot in its library."""
    game = _game(card_db)
    for _ in range(lands):
        _add(game, card_db, "Mountain", 0)
    sb_shot = _add(game, card_db, "Grapeshot", 0, "sideboard")
    sb_empty = _add(game, card_db, "Empty the Warrens", 0, "sideboard")
    lib_shot = _add(game, card_db, "Grapeshot", 0, "library")
    wish = _add(game, card_db, "Wish", 0, "hand")
    assert game.cast_spell(0, wish, [])
    _resolve(game)
    return game, sb_shot, sb_empty, lib_shot


# ── Typed at load ─────────────────────────────────────────────────────

def test_from_outside_the_game_is_the_sideboard_zone(card_db):
    from engine.effect_model import DurationKind, ModKind
    from engine.effect_spec import Verb, iter_specs
    (s,) = list(iter_specs(card_db.get_card("Wish").effects.spell(0).specs))
    assert s.verb is Verb.CONTINUOUS and s.payload.kind is ModKind.PERMIT
    assert (s.filter.zone, s.filter.owner) == ("sideboard", "you")
    assert s.amount.n == 1 and s.duration.kind is DurationKind.THIS_TURN
    vivien = card_db.get_card("Vivien, Arkbow Ranger")
    reveal, move = list(iter_specs(vivien.effects.loyalty("ult").specs))
    assert reveal.verb is Verb.REVEAL and reveal.filter.zone == "sideboard"
    assert reveal.filter.types == {"creature"}
    assert move.verb is Verb.MOVE and move.dest.zone == "hand"


def test_the_spell_resolves_from_its_typed_host_not_a_card_name_handler(
        card_db):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    from engine.effect_carrier import spell_family
    assert spell_family(card_db.get_card("Wish")) == "card_flow"
    assert not EFFECT_REGISTRY.has_handler("Wish", EffectTiming.SPELL_RESOLVE)


# ── "You may play a card you own from outside the game this turn" ─────

def test_its_controller_may_play_a_sideboard_card_this_turn(card_db):
    game, sb_shot, sb_empty, lib_shot = _wished(card_db)
    assert sb_shot.zone == sb_empty.zone == "sideboard", "not moved anywhere"
    assert game.can_cast(0, sb_shot) and game.can_cast(0, sb_empty)
    assert lib_shot.zone == "library", "never a card from the library"
    assert not game.can_cast(0, lib_shot)


def test_one_play_spends_the_permission(card_db):
    game, sb_shot, sb_empty, _ = _wished(card_db)
    assert game.cast_spell(0, sb_shot, [-2])
    assert sb_shot.zone == "stack"
    assert sb_empty.zone == "sideboard"
    assert not game.can_cast(0, sb_empty), "a card: one play"


def test_the_cards_a_one_play_permission_names_share_its_play(card_db):
    """Playing either spends the play the other needs: the AI's plan reads
    them as alternatives (`rules_query.share_one_play`)."""
    from engine.rules_query import share_one_play
    game, sb_shot, sb_empty, _ = _wished(card_db)
    assert share_one_play(game, 0, sb_shot, sb_empty)
    in_hand = _add(game, card_db, "Grapeshot", 0, "hand")
    assert not share_one_play(game, 0, sb_shot, in_hand), "a hand card needs no play"
    game.cleanup_step()
    assert not share_one_play(game, 0, sb_shot, sb_empty), "no permission left"


def test_the_permission_ends_with_the_turn(card_db):
    game, sb_shot, _, _ = _wished(card_db)
    game.cleanup_step()
    assert sb_shot.zone == "sideboard"
    assert not game.can_cast(0, sb_shot)


def test_with_nothing_outside_the_game_nothing_is_permitted(card_db):
    game = _game(card_db)
    for _ in range(3):
        _add(game, card_db, "Mountain", 0)
    lib_shot = _add(game, card_db, "Grapeshot", 0, "library")
    wish = _add(game, card_db, "Wish", 0, "hand")
    assert game.cast_spell(0, wish, [])
    _resolve(game)
    assert lib_shot.zone == "library" and not game.can_cast(0, lib_shot)


# ── "Reveal a <description> card ... and put it into your hand" ───────

def _vivien(card_db):
    game = _game(card_db)
    vivien = _add(game, card_db, "Vivien, Arkbow Ranger", 0)
    vivien.loyalty_counters = 6
    return game, vivien


def test_a_revealed_card_the_description_admits_goes_to_its_owners_hand(
        card_db):
    game, vivien = _vivien(card_db)
    bear = _add(game, card_db, "Grizzly Bears", 0, "sideboard")
    bolt = _add(game, card_db, "Lightning Bolt", 0, "sideboard")
    assert game.activate_planeswalker(0, vivien, "ult")
    _resolve(game)
    assert bear.zone == "hand" and bear in game.players[0].hand
    assert bolt.zone == "sideboard", "a creature card: the bolt is not one"


def test_a_reveal_takes_nothing_from_the_library(card_db):
    game, vivien = _vivien(card_db)
    lib_bear = _add(game, card_db, "Grizzly Bears", 0, "library")
    game.activate_planeswalker(0, vivien, "ult")
    _resolve(game)
    assert lib_bear.zone == "library"


# ── The auditors (CR 400.11a) ─────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit, rule):
    return [f for f in audit.drain() if f["rule"] == rule]


def test_the_audits_are_silent_when_the_card_comes_from_the_sideboard(
        card_db, audit):
    game, sb_shot, _, _ = _wished(card_db)
    game.cast_spell(0, sb_shot, [-2])
    game2, vivien = _vivien(card_db)
    _add(game2, card_db, "Grizzly Bears", 0, "sideboard")
    game2.activate_planeswalker(0, vivien, "ult")
    _resolve(game2)
    findings = audit.drain()
    assert not [f for f in findings if f["rule"].startswith("400.11a/")]


def test_the_cast_audit_records_a_sideboard_card_no_permission_names(
        card_db, audit, monkeypatch):
    """Broken the way a cast path that skipped the permission would be: a
    sideboard card cast though no Wish resolved."""
    from engine import rules_query
    monkeypatch.setattr(rules_query, "play_permitted",
                        lambda game, idx, card, permitted=None: True)
    game = _game(card_db)
    for _ in range(3):
        _add(game, card_db, "Mountain", 0)
    sb_shot = _add(game, card_db, "Grapeshot", 0, "sideboard")
    game.cast_spell(0, sb_shot, [-2])
    assert _findings(audit, "400.11a/play_from_outside_the_game")


def test_the_reveal_audit_records_a_card_brought_from_the_library(
        card_db, audit, monkeypatch):
    """Broken the way the old handler was: the pool read the library."""
    from engine import effect_executors as ex
    real = ex.outside_the_game_cards

    def _library_too(game, player, f):
        return real(game, player, f) + [
            c for c in game.players[player].library
            if c.template.is_creature]
    monkeypatch.setattr(ex, "outside_the_game_cards", _library_too)
    game, vivien = _vivien(card_db)
    _add(game, card_db, "Grizzly Bears", 0, "library")
    game.activate_planeswalker(0, vivien, "ult")
    _resolve(game)
    assert _findings(audit, "400.11a/brought_from_the_sideboard")


# ── The AI: a tutor reaches its own pool ──────────────────────────────

def test_a_tutor_from_outside_the_game_reaches_only_the_sideboard(card_db):
    """The AI's finisher access follows the tutor's typed pool: a card
    from outside the game is a sideboard card (CR 400.11a), never a
    library card."""
    from ai.combo_calc import _tutor_has_payoff_access
    game = _game(card_db)
    wish = _add(game, card_db, "Wish", 0, "hand")
    _add(game, card_db, "Grapeshot", 0, "library")
    me = game.players[0]
    assert not _tutor_has_payoff_access(wish, me), \
        "a Grapeshot in the library is no target of a card from outside"
    _add(game, card_db, "Grapeshot", 0, "sideboard")
    assert _tutor_has_payoff_access(wish, me)
