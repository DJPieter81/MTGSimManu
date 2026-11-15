"""A token enters with the status its creator states (CR 110.5b, 111.2):
"create two tapped 2/2 Zombie tokens" puts them onto the battlefield
tapped. A token put onto the battlefield attacking was never declared as an
attacker (CR 508.4); the engine does not model that, so the instruction is
refused rather than half-performed.

The defect: the payload leaf read the entry status ("tapped", "tapped and
attacking") off the token phrase, and the clause grammar then dropped it.
Every such token entered untapped: Army of the Damned's thirteen Zombies
could block at once, and Long-Bodied Grey Dog's tapped Treasure was mana
the turn it arrived.

Class: 82 CREATE_TOKEN specs in the pool say "tapped", 22 "tapped and
attacking". Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.effect_grammar import parse_effects
from engine.effect_spec import TokenSpec
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.stack import StackItem, StackItemType


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


def _tokens(game, idx=0):
    return [c for c in game.players[idx].battlefield if c.is_token]


def test_a_token_phrase_types_its_entry_status():
    [prefix] = parse_effects(
        "Create two tapped 1/1 white Spirit creature tokens with flying.")
    assert prefix.payload.tapped and not prefix.payload.attacking
    [treasure] = parse_effects("Create a tapped Treasure token.")
    assert treasure.payload.predefined == "treasure"
    assert treasure.payload.tapped
    [suffix] = parse_effects("Create a 1/1 white Human creature token "
                             "that's tapped and attacking.")
    assert suffix.payload.tapped and suffix.payload.attacking
    [plain] = parse_effects("Create a Treasure token.")
    assert plain.payload == TokenSpec(predefined="treasure")


def test_a_token_its_creator_puts_onto_the_battlefield_tapped_enters_tapped(
        card_db):
    game = _game()
    army = _put(game, card_db, "Army of the Damned", "stack")
    game._execute_spell_effects(StackItem(
        item_type=StackItemType.SPELL, source=army, controller=0))
    zombies = _tokens(game)
    assert len(zombies) == 13
    assert all(z.tapped for z in zombies)


def test_a_predefined_token_put_onto_the_battlefield_tapped_enters_tapped(
        card_db):
    game = _game()
    dog = _put(game, card_db, "Long-Bodied Grey Dog", "battlefield")
    game._handle_permanent_etb(dog, 0)
    [treasure] = _tokens(game)
    assert treasure.template.subtypes == ["Treasure"] and treasure.tapped
    # its {T} cannot be paid while it is tapped (CR 107.5)
    assert game.players[0].untapped_mana_capacity() == 0
    assert not game.tap_lands_for_mana(0, ManaCost(generic=1))


def test_a_token_its_creator_does_not_tap_enters_untapped(card_db):
    game = _game()
    keeper = _put(game, card_db, "Prosperous Innkeeper", "battlefield")
    game._handle_permanent_etb(keeper, 0)
    [treasure] = _tokens(game)
    assert not treasure.tapped


def test_a_token_put_onto_the_battlefield_attacking_is_refused():
    from engine.effect_executors import _create_token_supported
    [attacking] = parse_effects("Create a 1/1 white Human creature token "
                                "that's tapped and attacking.")
    assert not _create_token_supported(attacking)
    [tapped] = parse_effects("Create a tapped Treasure token.")
    assert _create_token_supported(tapped)


# ── Auditor (CR 110.5b), restated from the instruction's printed text ─

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_tapped_token_that_entered_untapped(card_db, audit,
                                                               monkeypatch):
    from engine import permanent_effects
    real = permanent_effects.PermanentEffects._put_tokens

    def untapped(game, controller, count, built, tapped=False):  # the defect
        return real(game, controller, count, built)
    monkeypatch.setattr(permanent_effects.PermanentEffects, "_put_tokens",
                        staticmethod(untapped))
    game = _game()
    dog = _put(game, card_db, "Long-Bodied Grey Dog", "battlefield")
    game._handle_permanent_etb(dog, 0)
    assert "110.5b/token_entry_status" in _rules(audit)


def test_the_audit_is_silent_when_each_token_enters_as_stated(card_db, audit):
    game = _game()
    for name in ("Long-Bodied Grey Dog", "Prosperous Innkeeper"):
        card = _put(game, card_db, name, "battlefield")
        game._handle_permanent_etb(card, 0)
    assert "110.5b/token_entry_status" not in _rules(audit)


def test_the_audit_reads_a_tapped_count_as_no_entry_status(audit):
    """"a Treasure token for each tapped Pirate you control" counts tapped
    permanents; the Treasure it makes enters untapped."""
    from engine.permanent_effects import audit_token_instruction
    game = _game()
    [treasure] = game.create_token(0, "treasure")
    audit_token_instruction(
        game, "create a treasure token for each tapped pirate you control",
        {0: (treasure,)}, "?")
    assert "110.5b/token_entry_status" not in _rules(audit)
    audit_token_instruction(game, "create a tapped treasure token",
                            {0: (treasure,)}, "?")
    assert "110.5b/token_entry_status" in _rules(audit)
