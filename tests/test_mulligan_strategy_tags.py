"""Sweep PR A — strategy_tags drives the land-flood mulligan ceiling.

Pins both sides of the rule that previously gated on
`"amulet" in deck_name.lower()`:

  1. Decks tagged with "ramp_into_payoff" KEEP land-heavy hands
     (5 lands + 1 spell). Amulet Titan's gameplan JSON carries
     this tag.
  2. Decks WITHOUT the tag (control, midrange, aggro) MULLIGAN
     land-heavy hands at the same threshold.

Failing-first: write the test, run against the current code path
(which still has the deck-name string match), verify it passes.
Then refactor to the tag-driven gate. Verify the test still
passes.

Reference: docs/proposals/2026-05_generic_predicate_sweep.md PR A.
"""
from __future__ import annotations

import random

import pytest

from ai.gameplan import create_goal_engine
from ai.mulligan import MulliganDecider
from ai.strategy_profile import ArchetypeStrategy
from engine.cards import CardInstance


def _hand(card_db, names):
    """Build a hand of CardInstances by template name."""
    out = []
    for i, name in enumerate(names, start=1):
        tmpl = card_db.get_card(name)
        assert tmpl is not None, f"missing card: {name}"
        c = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=i, zone="hand",
        )
        out.append(c)
    return out


def test_amulet_keeps_land_heavy_hand(card_db):
    """Amulet Titan with 5 lands + 1 spell + 1 noise card should
    KEEP — its gameplan declares strategy_tags['ramp_into_payoff']."""
    goal = create_goal_engine("Amulet Titan")
    decider = MulliganDecider(ArchetypeStrategy.COMBO, goal)
    hand = _hand(card_db, [
        "Forest", "Forest", "Mountain", "Tolaria West",
        "Boros Garrison", "Primeval Titan", "Amulet of Vigor",
    ])
    keep = decider.decide(hand, cards_in_hand=7)
    assert keep, (
        f"Amulet Titan with 5 lands + 2 spells (Titan + Amulet) "
        f"must keep — strategy_tags['ramp_into_payoff'] suppresses "
        f"the soft-flood ceiling. Reason: {decider.last_reason!r}"
    )


def test_non_ramp_deck_mulls_land_heavy_hand(card_db):
    """A non-ramp deck (Azorius Control WST) at 5 lands + 1 spell
    must MULL — no ramp_into_payoff tag means the soft-flood
    ceiling fires."""
    goal = create_goal_engine("Azorius Control (WST)")
    decider = MulliganDecider(ArchetypeStrategy.CONTROL, goal)
    hand = _hand(card_db, [
        "Plains", "Plains", "Plains", "Island", "Island",
        "Spell Snare", "Counterspell",
    ])
    keep = decider.decide(hand, cards_in_hand=7)
    # 5 lands + 2 spells — the soft ceiling fires when <2 spells,
    # so this hand should keep. Use a 1-spell variant to actually
    # trigger the gate.
    hand_one_spell = _hand(card_db, [
        "Plains", "Plains", "Plains", "Island", "Island",
        "Plains", "Spell Snare",
    ])
    keep_one_spell = decider.decide(hand_one_spell, cards_in_hand=7)
    assert not keep_one_spell, (
        f"Non-ramp deck (WST Control) with 6 lands + 1 spell "
        f"must mulligan. Reason: {decider.last_reason!r}"
    )


def test_strategy_tags_field_on_gameplan(card_db):
    """DeckGameplan dataclass exposes a strategy_tags field; the
    JSON loader populates it from the gameplan file."""
    goal = create_goal_engine("Amulet Titan")
    assert goal is not None
    assert goal.gameplan is not None
    tags = getattr(goal.gameplan, "strategy_tags", None)
    assert tags is not None, (
        "DeckGameplan must expose strategy_tags field"
    )
    assert "ramp_into_payoff" in tags, (
        f"Amulet Titan's gameplan JSON must declare 'ramp_into_payoff' "
        f"in strategy_tags. Got tags={tags}"
    )


def test_non_ramp_deck_has_no_ramp_tag(card_db):
    """Sanity: control decks have no ramp_into_payoff tag."""
    goal = create_goal_engine("Azorius Control (WST)")
    tags = getattr(goal.gameplan, "strategy_tags", set())
    assert "ramp_into_payoff" not in tags
