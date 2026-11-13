"""A target's zone is named by its own sentence (CR 115.1, 601.2c).

"Target creature" is a creature on the battlefield unless its own phrase
names another zone ("target creature card in your graveyard"). A graveyard
mentioned by another sentence of the card -- a delirium or threshold
condition, "cards in your graveyard" -- does not move the target there.

The cast-time requirements (`target_solver.parse`) are what casting checks
legality against and what the AI aims at, so a mis-zoned requirement makes
a burn spell uncastable at a creature and castable at a graveyard card.
"""
from __future__ import annotations

import random

import pytest

from engine import rules_audit
from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.target_solver import parse


def test_a_later_sentences_graveyard_does_not_move_a_battlefield_target():
    text = ("Unholy ~ deals 2 damage to target creature or planeswalker.\n"
            "Delirium — ~ deals 6 damage instead if there are four or more "
            "card types among cards in your graveyard.")
    reqs = parse(text)
    assert [r.zone for r in reqs] == ["battlefield"]
    assert set(reqs[0].types) >= {"creature"}


def test_a_graveyard_target_with_a_clause_between_type_and_zone_still_parses():
    text = ("Return target creature card with power 2 or less from your "
            "graveyard to the battlefield.")
    reqs = parse(text)
    assert [(r.zone, r.owner_scope) for r in reqs] == [("graveyard", "you")]


def _put(game, card_db, idx, name, zone):
    c = CardInstance(template=card_db.get_card(name), owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        game.players[idx].battlefield.append(c)
    else:
        getattr(game.players[idx], zone).append(c)
    return c


def test_a_burn_spell_with_a_graveyard_condition_targets_a_creature_on_the_battlefield(card_db):
    from engine.target_solver import has_legal_target_for_spell
    game = GameState(rng=random.Random(0))
    heat = _put(game, card_db, 0, "Unholy Heat", "hand")
    _put(game, card_db, 1, "Ragavan, Nimble Pilferer", "battlefield")
    reqs = parse(heat.template.oracle_text)
    assert has_legal_target_for_spell(game, 0, reqs, source=heat)


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    yield rules_audit
    rules_audit.reset()


def test_the_target_zone_audit_sees_a_permanent_spell_cast_at_a_graveyard_card(audit, card_db, monkeypatch):
    """CR 601.2c, restated from the card's other parse (the effect
    grammar's typed slot): a spell whose one target slot is a permanent
    is never cast at a card in a graveyard. Silent when the cast is
    right; it fires when the requirement parse is broken back."""
    from engine.cast_manager import CastManager
    from engine import target_solver

    def cast_at(target_zone):
        game = GameState(rng=random.Random(0))
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        _put(game, card_db, 0, "Mountain", "battlefield")
        heat = _put(game, card_db, 0, "Unholy Heat", "hand")
        tgt = _put(game, card_db, 1 if target_zone == "battlefield" else 0,
                   "Ragavan, Nimble Pilferer", target_zone)
        CastManager.cast_spell(game, 0, heat, [tgt.instance_id])

    cast_at("battlefield")
    assert "601.2c/target_zone" not in {f["rule"] for f in rules_audit.drain()
                                        if f["kind"] == "violation"}
    gy = target_solver.TargetRequirement(zone="graveyard",
                                         types=frozenset({"creature"}),
                                         owner_scope="you")
    monkeypatch.setattr(target_solver, "parse", lambda text: [gy])
    cast_at("graveyard")
    assert "601.2c/target_zone" in {f["rule"] for f in rules_audit.drain()
                                    if f["kind"] == "violation"}
