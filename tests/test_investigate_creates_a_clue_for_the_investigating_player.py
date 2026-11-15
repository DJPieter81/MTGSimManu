"""Investigate means "create a Clue token" (CR 701.16a): the player who
investigates creates a Clue -- the artifact CR 111.10f defines -- once for
each time the instruction says.

The defect: the grammar typed "investigate" as a keyword action whose
expansion nothing performed, so no engine path ever investigated. The
legacy interpreter read the Clue's reminder text ("{2}, Sacrifice this
token: Draw a card") as the card's own draw, which CR 207.2 forbids: Jace's
Scrutiny drew a card and made no Clue, and Thraben Inspector's enter
trigger made nothing.

The rule is typed once, where the grammar freezes a host: an investigate
action is the instruction its CR 701 expansion parses to, performed by the
same player under the same condition and as often as its count says. The
clause grammar still types the action itself.

Class: 79 pool cards print "investigate"; the grammar types it on 72 hosts
of 71 cards. Registered: Tamiyo, Inquisitive Student (Dimir Midrange x3;
its attack host waits for the attack carrier) and Tireless Tracker (Amulet
Titan SB; landfall has no carrier). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.effect_grammar import parse_effects
from engine.effect_spec import (AmountKind, RefKind, SelectorKind, TokenSpec,
                                Verb)
from engine.game_state import GameState, Phase
from engine.stack import StackItem, StackItemType

_CLUE = TokenSpec(predefined="clue")


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    if zone != "stack":
        getattr(game.players[idx], zone).append(c)
    return c


def _clues(game, idx=0):
    return [c for c in game.players[idx].battlefield
            if c.is_token and c.template.subtypes == ["Clue"]]


def test_investigate_is_typed_as_the_clue_its_player_creates():
    [s] = parse_effects("Investigate.")
    assert s.verb is Verb.CREATE_TOKEN and s.payload == _CLUE
    assert (s.amount.kind, s.amount.n) == (AmountKind.LITERAL, 1)
    assert s.actor is None and s.raw == "investigate"


def test_a_counted_investigate_creates_that_many_clues_when_typed():
    [twice] = parse_effects("Investigate twice.")
    assert twice.payload == _CLUE and twice.amount.n == 2
    [per] = parse_effects(
        "Investigate for each opponent who lost life this turn.")
    assert per.payload == _CLUE and per.amount.kind is AmountKind.FOR_EACH


def test_the_investigating_player_is_the_one_who_creates_the_clue():
    [target] = parse_effects("Target player investigates.")
    assert target.verb is Verb.CREATE_TOKEN
    assert target.actor.kind is RefKind.TARGET
    [each] = parse_effects("Each opponent investigates.")
    assert each.actor.kind is SelectorKind.OPPONENTS


def test_the_clause_grammar_still_types_the_keyword_action():
    from engine.effect_grammar.patterns import match_clause
    spec = match_clause("investigate").spec
    assert spec.verb is Verb.KEYWORD_ACTION
    assert spec.payload.name == "investigate"


def test_an_enter_trigger_that_investigates_creates_a_clue(card_db):
    game = _game()
    inspector = _put(game, card_db, "Thraben Inspector", "battlefield")
    game._handle_permanent_etb(inspector, 0)
    [clue] = _clues(game)
    assert clue.controller == clue.owner == 0
    assert clue.template.activated_abilities


def test_a_counted_enter_trigger_creates_each_clue(card_db):
    game = _game()
    sifter = _put(game, card_db, "Wavesifter", "battlefield")
    game._handle_permanent_etb(sifter, 0)
    assert len(_clues(game)) == 2


def test_a_spell_that_investigates_makes_a_clue_and_performs_no_reminder_text(
        card_db):
    game = _game()
    for _ in range(3):
        _put(game, card_db, "Island", "library")
    bear = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    scrutiny = _put(game, card_db, "Jace's Scrutiny", "stack")
    hand = len(game.players[0].hand)
    game._execute_spell_effects(StackItem(
        item_type=StackItemType.SPELL, source=scrutiny, controller=0,
        targets=[bear.instance_id]))
    assert bear.power == bear.template.power - 4
    assert len(_clues(game)) == 1
    assert len(game.players[0].hand) == hand, "reminder text draws nothing"


# ── Auditor (CR 701.16a), restated from the instruction's printed text ─

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def _probe(game, text):
    """A fresh creature printing `text` (no parse memoised on it yet)."""
    from engine.cards import CardTemplate, CardType
    from engine.mana import ManaCost
    tmpl = CardTemplate(name="Investigate Probe",
                        card_types=[CardType.CREATURE], mana_cost=ManaCost(),
                        power=1, toughness=2, oracle_text=text)
    c = CardInstance(template=tmpl, owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    game.players[0].battlefield.append(c)
    return c


def test_the_audit_records_an_investigate_that_made_no_clue(audit,
                                                            monkeypatch):
    import engine.effect_grammar as grammar
    from engine.effect_grammar import link
    real = link._expanded_action

    def food(spec):                                          # the defect
        out = real(spec)
        return out if out is spec else link._copy_with(
            out, payload=TokenSpec(predefined="food"))
    monkeypatch.setattr(link, "_expanded_action", food)
    grammar.clear_caches()
    try:
        game = _game()
        probe = _probe(game, "When this creature enters, investigate.")
        game._handle_permanent_etb(probe, 0)
        assert "701.16a/investigate_creates_a_clue" in _rules(audit)
    finally:
        monkeypatch.undo()
        grammar.clear_caches()


def test_the_audit_is_silent_when_investigate_makes_its_clue(card_db, audit):
    game = _game()
    for name in ("Thraben Inspector", "Wavesifter"):
        card = _put(game, card_db, name, "battlefield")
        game._handle_permanent_etb(card, 0)
    assert len(_clues(game)) == 3
    assert "701.16a/investigate_creates_a_clue" not in _rules(audit)
