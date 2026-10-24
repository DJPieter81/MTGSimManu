"""A target's mana-value qualifier is part of the target's requirement, read
at the object's mana value (CR 115.1, 601.2c, 202.3e).

"Counter target spell with mana value 2" admits only a spell whose mana value
is 2; "... 4 or greater" only one of 4 or more; "... 1 or less" only one of
1 or less; "... less than or equal to the number of cards in its
controller's graveyard" only one within that count, read off the graveyard
of the object's controller. On the stack a spell's mana value counts the
value chosen for its X (CR 202.3e): {X}{U}{U} cast for X=6 has mana value 8.

The defect: the stack-target parser read "target <type> spell" and dropped
the qualifier, so Spell Snare countered any spell -- the Dimir vs Azorius
Control (WST v2) trace, s50000 game 1, T9: Wan Shi Tong, Librarian cast for
X=6. The AI's counter check read no qualifier either, and Drown in the
Loch's graveyard bound was read on neither of its targets.

Class: 15 pool cards target a spell by mana value (Spell Snare, Drown in the
Loch, Disdainful Stroke, Spell Blast, Minor Misstep, Horribly Awry, ...).
Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType
from engine.target_solver import has_legal_target, parse

SNARE = "Counter target spell with mana value 2."
STROKE = "Counter target spell with mana value 4 or greater."
MISSTEP = "Counter target spell with mana value 1 or less."
DROWN = ("Choose one —\n"
         "• Counter target spell with mana value less than or equal to the "
         "number of cards in its controller's graveyard.\n"
         "• Destroy target creature with mana value less than or equal to "
         "the number of cards in its controller's graveyard.")


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 1
    return game


def _card(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
    if zone != "stack":
        getattr(game.players[controller], zone).append(card)
    return card


def _cast(game, card_db, name, controller=1, x=0):
    spell = _card(game, card_db, name, controller, "stack")
    item = StackItem(item_type=StackItemType.SPELL, source=spell,
                     controller=controller, x_value=x)
    game.stack.push(item)
    return item


def _stack_req(text):
    return next(r for r in parse(text) if r.zone == "stack")


# ── A printed number: exact, a floor, a ceiling ───────────────────────

@pytest.mark.parametrize("spell, admitted", [
    ("Counterspell", True),             # {U}{U}: 2
    ("Lightning Bolt", False),          # 1
    ("Lightning Helix", True),          # {R}{W}: 2
    ("Kolaghan's Command", False),      # 3
])
def test_an_exact_mana_value_admits_only_a_spell_of_that_mana_value(
        card_db, spell, admitted):
    game = _game()
    _cast(game, card_db, spell)
    assert has_legal_target(game, 0, _stack_req(SNARE)) is admitted


def test_a_floor_admits_only_a_spell_at_or_above_it(card_db):
    game = _game()
    _cast(game, card_db, "Kolaghan's Command")            # 3
    assert not has_legal_target(game, 0, _stack_req(STROKE))
    _cast(game, card_db, "Wrath of the Skies", x=2)       # {X}{W}{W}: 4
    assert has_legal_target(game, 0, _stack_req(STROKE))


def test_a_ceiling_admits_only_a_spell_at_or_below_it(card_db):
    game = _game()
    _cast(game, card_db, "Counterspell")                  # 2
    assert not has_legal_target(game, 0, _stack_req(MISSTEP))
    _cast(game, card_db, "Lightning Bolt")                # 1
    assert has_legal_target(game, 0, _stack_req(MISSTEP))


# ── X on the stack (CR 202.3e) ────────────────────────────────────────

def test_on_the_stack_a_spells_mana_value_counts_the_x_chosen_for_it(
        card_db):
    game = _game()
    _cast(game, card_db, "Wan Shi Tong, Librarian", x=6)  # {X}{U}{U}: 8
    assert not has_legal_target(game, 0, _stack_req(SNARE))
    game.stack.items.clear()
    _cast(game, card_db, "Wan Shi Tong, Librarian", x=0)  # 2
    assert has_legal_target(game, 0, _stack_req(SNARE))


# ── A count of the object's controller's graveyard ────────────────────

def test_a_bound_of_its_controllers_graveyard_reads_the_spells_controller(
        card_db):
    game = _game()
    _cast(game, card_db, "Kolaghan's Command", controller=1)   # 3
    for _ in range(2):
        _card(game, card_db, "Lightning Bolt", 1, "graveyard")
    _card(game, card_db, "Lightning Bolt", 0, "graveyard")     # not its
    req = _stack_req(DROWN)
    assert not has_legal_target(game, 0, req)
    _card(game, card_db, "Lightning Bolt", 1, "graveyard")     # now 3
    assert has_legal_target(game, 0, req)


def test_the_bound_applies_to_a_permanent_target_too(card_db):
    from engine.target_solver import enumerate_legal_targets
    game = _game()
    creature = _card(game, card_db, "Tarmogoyf", 1, "battlefield")  # 2
    req = next(r for r in parse(DROWN) if r.zone == "battlefield")
    _card(game, card_db, "Lightning Bolt", 1, "graveyard")
    assert creature not in enumerate_legal_targets(game, 0, req)
    _card(game, card_db, "Lightning Bolt", 1, "graveyard")
    assert creature in enumerate_legal_targets(game, 0, req)


# ── The AI's counter check reads the same requirement ─────────────────

def test_the_ai_counters_only_a_spell_its_counter_admits(card_db):
    from ai.response import ResponseDecider
    game = _game()
    snare = _card(game, card_db, "Spell Snare", 0, "hand")
    big = _cast(game, card_db, "Wan Shi Tong, Librarian", x=6)
    assert not ResponseDecider._counter_can_target(snare, big)
    small = _cast(game, card_db, "Counterspell")
    assert ResponseDecider._counter_can_target(snare, small)


# ── Auditor (CR 202.3e) ───────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def test_the_audit_records_a_counter_cast_at_a_spell_its_bound_refuses(
        card_db, audit):
    from engine.cast_manager import _audit_spell_targets_mana_value
    game = _game()
    snare = _card(game, card_db, "Spell Snare", 0, "hand")
    big = _cast(game, card_db, "Wan Shi Tong, Librarian", x=6)
    _audit_spell_targets_mana_value(game, snare, [big.source.instance_id])
    assert "202.3e/spell_target_mana_value" in [f["rule"]
                                                for f in audit.drain()]


def test_the_audit_is_silent_for_a_spell_within_the_bound(card_db, audit):
    from engine.cast_manager import _audit_spell_targets_mana_value
    game = _game()
    snare = _card(game, card_db, "Spell Snare", 0, "hand")
    small = _cast(game, card_db, "Wan Shi Tong, Librarian", x=0)
    _audit_spell_targets_mana_value(game, snare, [small.source.instance_id])
    assert "202.3e/spell_target_mana_value" not in [f["rule"]
                                                    for f in audit.drain()]
