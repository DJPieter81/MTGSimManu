"""A spell's cost reduction for a target applies when a chosen target fits
its description (CR 601.2c, 601.2f).

"This spell costs {N} less to cast if it targets <description>": the
targets are chosen (CR 601.2c) before the total cost is determined (CR
601.2f), so the discount reads them. The cast is legal when the discounted
cost can be paid with a fitting legal target to choose; it is paid N less
when a chosen target fits, and in full when none does. A reduction never
touches coloured pips.

The description is typed at load by the effect grammar's filter leaf
(`CardTemplate.target_cost_reduction`); one owner decides the discount
(`oracle_resolver.target_cost_reduction`, `best_target_cost_reduction`),
read by the cast's legality, its payment and the AI's counter cost. The
auditor `601.2f/target_cost_reduction` restates the rule from raw fields.

The defect (docs/diagnostics/2026-10-14_dimir_midrange_root_cause.md,
subsystem 4): no part of the engine read the discount. Mystical Dispute
(nine registered sideboards) cost {2}{U} against a blue spell.

Card names are fixture carriers: 38 pool spells print the discount; 34
descriptions are typed, 4 refused (damage history, graveyard counts, a
card in a graveyard, a spell or ability that targets).
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
    game.active_player = 1
    game.current_phase = Phase.MAIN1
    for p in (0, 1):
        for _ in range(5):
            _add(game, card_db, "Island", p, "library")
    return game


def _on_the_stack(game, card_db, name, land):
    """Player 1 casts `name` (one land of its colour pays it)."""
    _add(game, card_db, land, 1)
    spell = _add(game, card_db, name, 1, "hand")
    targets = [] if name != "Lightning Bolt" else [-1]
    assert game.cast_spell(1, spell, targets)
    return spell


def _untapped(game, idx):
    return sum(1 for l in game.players[idx].lands if not l.tapped)


# ── Typed at load ─────────────────────────────────────────────────────

def test_a_discount_for_a_target_is_typed_from_its_text(card_db):
    n, f = card_db.get_card("Mystical Dispute").target_cost_reduction
    assert n == 2 and f.zone == "stack" and f.colors == {"U"}
    n, f = card_db.get_card("Ride's End").target_cost_reduction
    assert n == 3 and f.state == {"tapped"}
    assert card_db.get_card("Bitter Downfall").target_cost_reduction is None, \
        "a description the owner cannot read is refused"
    assert card_db.get_card("Counterspell").target_cost_reduction is None


# ── Legality and payment ──────────────────────────────────────────────

def test_a_spell_is_castable_for_its_discounted_cost_with_a_fitting_target(
        card_db):
    game = _game(card_db)
    opt = _on_the_stack(game, card_db, "Opt", "Island")
    _add(game, card_db, "Island", 0)
    dispute = _add(game, card_db, "Mystical Dispute", 0, "hand")
    assert game.can_cast(0, dispute)
    assert game.cast_spell(0, dispute, [opt.instance_id])
    assert _untapped(game, 0) == 0


def test_a_spell_whose_targets_do_not_fit_pays_in_full(card_db):
    game = _game(card_db)
    _on_the_stack(game, card_db, "Lightning Bolt", "Mountain")
    _add(game, card_db, "Island", 0)
    dispute = _add(game, card_db, "Mystical Dispute", 0, "hand")
    assert not game.can_cast(0, dispute), "{2}{U} against a red spell"
    for _ in range(2):
        _add(game, card_db, "Island", 0)
    bolt = next(i.source for i in game.stack.items)
    assert game.can_cast(0, dispute)
    assert game.cast_spell(0, dispute, [bolt.instance_id])
    assert _untapped(game, 0) == 0, "all three paid"


def test_a_permanents_state_earns_its_discount(card_db):
    """Ride's End ({4}{W}, 3 less against a tapped permanent)."""
    game = _game(card_db)
    game.active_player = 0
    bears = _add(game, card_db, "Grizzly Bears", 1)
    for _ in range(2):
        _add(game, card_db, "Plains", 0)
    ride = _add(game, card_db, "Ride's End", 0, "hand")
    assert not game.can_cast(0, ride)
    bears.tapped = True
    assert game.can_cast(0, ride)
    assert game.cast_spell(0, ride, [bears.instance_id])
    assert _untapped(game, 0) == 0


# ── The AI's counter cost ─────────────────────────────────────────────

def test_the_ai_counts_the_discount_in_a_counters_cost(card_db):
    from ai.response import ResponseDecider
    game = _game(card_db)
    _on_the_stack(game, card_db, "Opt", "Island")
    dispute = _add(game, card_db, "Mystical Dispute", 0, "hand")
    item = game.stack.items[-1]
    decider = ResponseDecider(0)
    assert decider._effective_counter_cost(game, dispute, item) == 1
    game2 = _game(card_db)
    _on_the_stack(game2, card_db, "Lightning Bolt", "Mountain")
    dispute2 = _add(game2, card_db, "Mystical Dispute", 0, "hand")
    assert decider._effective_counter_cost(
        game2, dispute2, game2.stack.items[-1]) == 3


# ── The auditor (CR 601.2f) ───────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain()
            if f["rule"] == "601.2f/target_cost_reduction"]


def _counter_a_blue_spell(card_db, lands):
    game = _game(card_db)
    opt = _on_the_stack(game, card_db, "Opt", "Island")
    for _ in range(lands):
        _add(game, card_db, "Island", 0)
    dispute = _add(game, card_db, "Mystical Dispute", 0, "hand")
    game.cast_spell(0, dispute, [opt.instance_id])
    return game


def test_the_audit_is_silent_when_the_discount_is_paid(card_db, audit):
    _counter_a_blue_spell(card_db, lands=1)
    assert not _findings(audit)


def test_the_audit_records_a_fitting_target_paid_in_full(card_db, audit,
                                                          monkeypatch):
    from engine import oracle_resolver
    monkeypatch.setattr(oracle_resolver, "target_cost_reduction",
                        lambda game, idx, template, targets: 0)
    _counter_a_blue_spell(card_db, lands=3)
    assert _findings(audit)
