"""Counter fires on a cost-reduced threat when held with mana available.

Rule under test (P0-A): when the opponent casts a spell whose printed
mana value is high but whose actual paid mana is low (because of a
cost-reducer mechanic such as affinity for artifacts, delve, or
domain), the response decider must value the threat by what the
opponent *actually* gets — NOT by the printed CMC scaled through
mana_clock_impact, which mis-reports discounted threats as
"expensive low-density spells" not worth countering.

Two failure modes the fix must close:

  1. Cost-reduced *spell* (Thoughtcast, draws 2 for ~1 mana via
     affinity). evaluate_stack_threat credits the spell via
     `card_clock_impact(snap)`, but `snap.my_mana` is the opponent's
     untapped-land count (often 0 right after they tap out for a
     splashy turn). card_clock_impact then collapses to ~0.5 per
     branch and the legacy gate (1.5 / 3.0 hardcoded thresholds)
     fails — even though a held cheap counter is essentially free
     to spend on a 2-card swing.

  2. Cost-reduced *creature* (Sojourner's Companion at printed CMC 7,
     paid 0 via affinity). evaluate_stack_threat for incoming
     creatures should route through `creature_threat_value()` so the
     resolved body is scored by what hits the battlefield, not by
     the printed mana value scaled through mana_clock_impact.

The fix: derive both gate thresholds from `held_counter_floor_ev`
(the EV cost of holding the counter, computed from
`card_clock_impact`) instead of magic numbers, AND route incoming
creature spells through `creature_threat_value()`.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_zone(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    return card


def _make_stack_item(card, controller):
    return StackItem(
        item_type=StackItemType.SPELL,
        source=card,
        controller=controller,
    )


def _make_response_decider(defender_idx):
    from ai.response import ResponseDecider
    from ai.turn_planner import TurnPlanner
    return ResponseDecider(defender_idx, TurnPlanner(), strategic_logger=None)


# ─────────────────────────────────────────────────────────────────────
# Failure mode 1 — cost-reduced noncreature engine spell
# ─────────────────────────────────────────────────────────────────────


def test_counter_fires_on_affinity_discounted_card_draw(card_db):
    """Counter must fire on a draw-2 whose paid cost is ~0 via affinity.

    Setup mirrors a real Affinity sequence: opp has a few artifacts
    on the board so affinity discount makes Thoughtcast (CMC 5) cost
    1 generic mana to draw two cards. The defender holds Counterspell
    and has UU open — the counter is cheap to fire.

    Pre-fix: evaluate_stack_threat scored Thoughtcast through
    card_clock_impact(snap), which uses opp's untapped-land mana.
    With opp tapped out (snap.my_mana == 0) card_clock_impact
    collapses to ~0.025 per call, threat ≈ 1.0, and the legacy gate
    (1.5 / 3.0 hardcoded thresholds) fails — the counter passes on a
    2-for-1 swing.

    Post-fix: gate thresholds derive from held_counter_floor_ev so a
    cheap counter (Counterspell at UU) fires for any threat above
    the floor EV of holding the card, which a draw-2 always exceeds.
    """
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx  # opponent's turn

    # Attacker board: 4 artifacts → Thoughtcast costs {1} (5 - 4).
    _add_to_zone(game, card_db, "Memnite", attacker_idx, "battlefield")
    _add_to_zone(game, card_db, "Memnite", attacker_idx, "battlefield")
    _add_to_zone(game, card_db, "Mox Opal", attacker_idx, "battlefield")
    _add_to_zone(game, card_db, "Mox Opal", attacker_idx, "battlefield")

    # Defender: 2 untapped Islands (UU for Counterspell), no creatures.
    _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    threat = _add_to_zone(
        game, card_db, "Thoughtcast", attacker_idx, "stack")
    item = _make_stack_item(threat, attacker_idx)
    game.stack.push(item)

    decider = _make_response_decider(defender_idx)
    result = decider.decide_response(game, item)

    assert result is not None, (
        "Counter must fire on an affinity-discounted draw-2: paid "
        "cost is ~1, the draw is a 2-for-1 swing. Pre-fix the gate "
        "ignored that the held counter is cheap and the threat is "
        "card-advantage, scoring it ~1.0 below the legacy 1.5 floor."
    )
    chosen, _targets = result
    assert "counterspell" in chosen.template.tags, (
        f"Expected a counterspell to be chosen; got {chosen.name}."
    )


# ─────────────────────────────────────────────────────────────────────
# Failure mode 2 — cost-reduced creature threat
# ─────────────────────────────────────────────────────────────────────


def test_counter_fires_on_affinity_discounted_creature(card_db):
    """Counter must fire on a creature whose paid cost is ~0 via affinity.

    Sojourner's Companion: printed CMC 7, paid 0-3 with metalcraft
    live. The resolved body is a 4/4 with affinity-engine
    implications. Pre-fix, evaluate_stack_threat valued the spell
    through printed-CMC scaling — Change A routes incoming creature
    spells through creature_threat_value() instead, scoring the
    resolved body by oracle and clock semantics, so it doesn't
    matter whether opp paid 0 or 7.
    """
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx

    # Wide artifact board so affinity discount is large.
    for _ in range(3):
        _add_to_zone(game, card_db, "Memnite", attacker_idx, "battlefield")
    for _ in range(4):
        _add_to_zone(game, card_db, "Mox Opal", attacker_idx, "battlefield")

    _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    threat = _add_to_zone(
        game, card_db, "Sojourner's Companion", attacker_idx, "stack")
    item = _make_stack_item(threat, attacker_idx)
    game.stack.push(item)

    decider = _make_response_decider(defender_idx)
    result = decider.decide_response(game, item)

    assert result is not None, (
        "Counter must fire on an affinity-discounted creature body."
    )
    chosen, _targets = result
    assert "counterspell" in chosen.template.tags, (
        f"Expected a counterspell to be chosen; got {chosen.name}."
    )
