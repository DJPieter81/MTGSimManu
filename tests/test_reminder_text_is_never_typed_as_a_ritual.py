"""Reminder text is not rules text (CR 207.2): a spell is a ritual only when
its own rules text adds mana.

CR 207.2: reminder text, in parentheses, summarises a rule that applies to
the object; it has no effect of its own. A Treasure's reminder text quotes
the token's ability -- "(It's an artifact with "{T}, Sacrifice this token:
Add one mana of any color.")" -- and that ability belongs to the token, not
to the spell that creates it.

The defect: the load-time ritual parser read reminder text. Every spell
that creates a Treasure was typed as a ritual of one mana of any colour, and
the ritual branch, which resolves first and returns, added {R}{R} and did
nothing else. So Deadly Dispute and Big Score drew no cards, and no such
spell ever made its Treasure.

Class: 139 pool cards are typed with ritual mana only from reminder text, 34
of them instants and sorceries (every one a Treasure maker); no registered
card. Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import parse_ritual_mana
from engine.stack import StackItem, StackItemType

_TREASURE_SPELL = ('Create a Treasure token. (It\'s an artifact with "{T}, '
                   'Sacrifice this token: Add one mana of any color.")')


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    return game


def _card(game, template, zone="stack", idx=0):
    c = CardInstance(template=template, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone not in ("stack",):
        getattr(game.players[idx], zone).append(c)
    return c


def _resolve(game, card, idx=0):
    game._execute_spell_effects(StackItem(
        item_type=StackItemType.SPELL, source=card, controller=idx))


def test_mana_named_only_in_reminder_text_is_not_a_ritual(card_db):
    assert parse_ritual_mana(_TREASURE_SPELL) is None
    for name in ("Strike It Rich", "Big Score", "Deadly Dispute",
                 "Unexpected Windfall"):
        assert card_db.get_card(name).ritual_mana is None, name


def test_a_ritual_its_rules_text_prints_is_still_a_ritual(card_db):
    assert card_db.get_card("Pyretic Ritual").ritual_mana == ("R", 3)
    assert card_db.get_card("Desperate Ritual").ritual_mana == ("R", 3)
    assert card_db.get_card("Manamorphose").ritual_mana == ("any", 2)
    assert parse_ritual_mana("Add {R}{R}{R}. (This is a ritual.)") == ("R", 3)


def test_a_spell_that_creates_a_treasure_creates_it_and_adds_no_mana(card_db):
    game = _game()
    _resolve(game, _card(game, card_db.get_card("Strike It Rich")))
    me = game.players[0]
    assert me.mana_pool.total() == 0
    [treasure] = [c for c in me.battlefield if c.is_token]
    assert treasure.template.subtypes == ["Treasure"]
    assert game.tap_lands_for_mana(0, ManaCost(generic=1))


def test_a_spell_beside_its_treasure_performs_its_other_instructions(card_db):
    """The ritual branch resolves first and returns: typed as a ritual,
    the spell drew nothing. (Its Treasures are a separate gap: a spell
    host that both draws and creates tokens is in no one landed family.)"""
    game = _game()
    for _ in range(4):
        _card(game, card_db.get_card("Island"), zone="library")
    _resolve(game, _card(game, card_db.get_card("Big Score")))
    me = game.players[0]
    assert len(me.hand) == 2, "draw two cards"
    assert me.mana_pool.total() == 0


# ── Auditor (CR 207.2), restated from the spell's printed text ────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_ritual_read_from_reminder_text(card_db, audit):
    import copy
    game = _game()
    template = copy.copy(card_db.get_card("Strike It Rich"))
    template.ritual_mana = ("any", 1)                         # the defect
    _resolve(game, _card(game, template))
    assert "207.2/ritual_from_rules_text" in _rules(audit)


def test_the_audit_is_silent_for_a_printed_ritual(card_db, audit):
    game = _game()
    for name in ("Pyretic Ritual", "Manamorphose"):
        _resolve(game, _card(game, card_db.get_card(name)))
    assert "207.2/ritual_from_rules_text" not in _rules(audit)
