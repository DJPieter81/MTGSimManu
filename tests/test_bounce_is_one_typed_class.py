"""Returning a permanent to its owner's hand is one mechanic (CR 608.2b /
400.3 / 110.2): "return [up to one] target <types> [an opponent controls |
you control] to its owner's hand" — every carrier (spells, channel and
loyalty lines) targets through the one target solver and moves the chosen
object to its OWNER's hand.

Rules pinned:
* a target type list of any length is the union of its types
  ("artifact, creature, enchantment, or planeswalker");
* the bounce requirement is typed once (`CardTemplate.bounce_target`);
* a creature-only bounce resolves (it did nothing before: the gate knew
  only "nonland permanent");
* the chosen target is honoured, and an illegal one (hexproof, wrong
  type) is never bounced;
* the object goes to its owner's hand even when another player controls it;
* spells and loyalty lines share the resolution owner.
Card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from engine import rules_audit
from engine.cards import CardInstance, Keyword
from engine.game_state import GameState, Phase
from engine.oracle_resolver import resolve_spell_from_oracle
from engine.target_solver import parse


def test_a_type_list_of_any_length_is_the_union_of_its_types():
    reqs = parse("Return target artifact, creature, enchantment, or planeswalker "
                 "to its owner's hand.")
    assert reqs[0].types == frozenset({"artifact", "creature", "enchantment", "planeswalker"})
    assert parse("Exile target artifact or enchantment.")[0].types == frozenset(
        {"artifact", "enchantment"})
    assert parse("Destroy target artifact, creature, or enchantment.")[0].types == frozenset(
        {"artifact", "creature", "enchantment"})


def test_a_compound_type_list_keeps_its_controller_scope():
    req = parse("Return target artifact or creature an opponent controls to its owner's hand.")[0]
    assert req.types == frozenset({"artifact", "creature"})
    assert req.owner_scope == "opponent"


def test_the_bounce_requirement_is_typed_at_load(card_db):
    t = card_db.get_card("Unsummon")
    assert t.bounce_target is not None
    assert t.bounce_target.types == frozenset({"creature"})
    assert card_db.get_card("Lightning Bolt").bounce_target is None


# ── fixtures ─────────────────────────────────────────────────────────

def _put(game, card_db, name, controller, owner=None):
    c = CardInstance(template=card_db.get_card(name),
                     owner=controller if owner is None else owner,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    game.players[controller].battlefield.append(c)
    return c


def _cast(game, card_db, name, targets, controller=0):
    s = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone="stack")
    s._game_state = game
    return resolve_spell_from_oracle(game, s, controller, targets)


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    return game


def test_a_creature_bounce_returns_the_creature(card_db):
    game = _game()
    bear = _put(game, card_db, "Grizzly Bears", 1)
    _cast(game, card_db, "Unsummon", [bear.instance_id])
    assert bear.zone == "hand" and bear in game.players[1].hand


def test_the_chosen_target_is_honoured(card_db):
    game = _game()
    small = _put(game, card_db, "Grizzly Bears", 1)
    _put(game, card_db, "Tarmogoyf", 1)
    _cast(game, card_db, "Into the Roil", [small.instance_id])
    assert small.zone == "hand"


def test_a_hexproof_permanent_is_never_bounced(card_db):
    game = _game()
    shy = _put(game, card_db, "Grizzly Bears", 1)
    shy.template = copy.copy(shy.template)
    shy.template.keywords = set(shy.template.keywords) | {Keyword.HEXPROOF}
    _cast(game, card_db, "Into the Roil", [])
    assert shy.zone == "battlefield"


def test_the_object_goes_to_its_owners_hand(card_db):
    game = _game()
    stolen = _put(game, card_db, "Grizzly Bears", 1, owner=0)   # P0 owns, P1 controls
    _cast(game, card_db, "Unsummon", [stolen.instance_id])
    assert stolen in game.players[0].hand and stolen not in game.players[1].hand


def test_spells_and_loyalty_lines_share_the_bounce_owner():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "engine"
           / "planeswalker_manager.py").read_text()
    assert "resolve_bounce" in src


def test_bounce_audit_sees_an_object_left_in_the_wrong_hand(card_db, monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    game = _game()
    stolen = _put(game, card_db, "Grizzly Bears", 1, owner=0)

    def _wrong_hand(card):
        game.players[card.controller].battlefield.remove(card)
        card.zone = "hand"
        game.players[card.controller].hand.append(card)
    monkeypatch.setattr(game, "_bounce_permanent", _wrong_hand)
    _cast(game, card_db, "Unsummon", [stolen.instance_id])
    rules = {f["rule"] for f in rules_audit.drain() if f["kind"] == "violation"}
    rules_audit.reset()
    assert "400.3/bounced_to_owners_hand" in rules


def test_bounce_audit_is_silent_when_the_owner_gets_it(card_db, monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    game = _game()
    stolen = _put(game, card_db, "Grizzly Bears", 1, owner=0)
    _cast(game, card_db, "Unsummon", [stolen.instance_id])
    rules = {f["rule"] for f in rules_audit.drain() if f["kind"] == "violation"}
    rules_audit.reset()
    assert "400.3/bounced_to_owners_hand" not in rules


def test_a_spell_or_permanent_target_bounces_its_permanent_half():
    from engine.oracle_parser import parse_bounce_target
    req = parse_bounce_target(
        "Return target spell or nonland permanent an opponent controls to its owner's hand.")
    assert req is not None and req.zone == "battlefield"
    assert req.types == frozenset({"permanent_nonland"})
