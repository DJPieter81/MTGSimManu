"""Tests for `ai/predicates.py` — centralized card predicates.

The test design explicitly covers BOTH the positive case (predicate
fires for a card that should match) AND the negative case (predicate
does NOT fire for cards that shouldn't match).  Predicates designed
only for the positive case were the source of F4.1 ("always pay
shock"), F5.1 ("no signal for cost reducer"), and F2.1 ("count
every non-storm spell as fuel") in the 2026-04-26 audit.

Each predicate gets a small property-style suite:
- empty / minimal input returns expected baseline
- canonical positive example returns True/expected count
- canonical negative example returns False/zero
- adversarial example (looks similar but shouldn't match) returns
  the negative answer
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set

import pytest

from ai.predicates import (
    CHAIN_FUEL_TAGS,
    DRAW_ENGINE_TAGS,
    is_chain_fuel,
    is_ritual,
    is_draw_engine,
    count_lands,
    count_gy_creatures,
)


# ─── Mock helpers ─────────────────────────────────────────────────

@dataclass
class MockTemplate:
    name: str = "Test"
    tags: Set[str] = field(default_factory=set)
    is_land: bool = False
    is_creature: bool = False


@dataclass
class MockCard:
    template: MockTemplate = field(default_factory=MockTemplate)


# ─── Constants ────────────────────────────────────────────────────

class TestConstants:
    """The CHAIN_FUEL_TAGS / DRAW_ENGINE_TAGS sets define the
    predicate's contract — guard them against accidental changes."""

    def test_chain_fuel_tags_is_frozenset(self):
        """Frozen so callers can't mutate the canonical set."""
        assert isinstance(CHAIN_FUEL_TAGS, frozenset)

    def test_chain_fuel_tags_canonical_contents(self):
        """The four tags that matter for storm chains."""
        assert CHAIN_FUEL_TAGS == frozenset(
            {'ritual', 'cantrip', 'draw', 'card_advantage'})

    def test_draw_engine_tags_subset_of_chain_fuel(self):
        """Draw engines are a strict subset of chain fuel — every
        draw engine extends the chain (by drawing more spells)."""
        assert DRAW_ENGINE_TAGS < CHAIN_FUEL_TAGS

    def test_draw_engine_tags_excludes_ritual(self):
        """Pure mana rituals are chain fuel but not draw engines."""
        assert 'ritual' not in DRAW_ENGINE_TAGS
        assert 'ritual' in CHAIN_FUEL_TAGS


# ─── is_chain_fuel ────────────────────────────────────────────────

class TestIsChainFuel:
    """`is_chain_fuel(card)` returns True iff the card has at least
    one tag in CHAIN_FUEL_TAGS."""

    def test_card_with_no_tags_returns_false(self):
        """Empty tag set — card is unclassified — not fuel."""
        card = MockCard(template=MockTemplate(name="Vanilla", tags=set()))
        assert is_chain_fuel(card) is False

    def test_ritual_only_card_returns_true(self):
        """Pyretic Ritual pattern — pure mana-positive."""
        card = MockCard(template=MockTemplate(
            name="Pyretic Ritual", tags={'ritual'}))
        assert is_chain_fuel(card) is True

    def test_cantrip_only_card_returns_true(self):
        """Reckless Impulse pattern."""
        card = MockCard(template=MockTemplate(
            name="Reckless Impulse", tags={'cantrip'}))
        assert is_chain_fuel(card) is True

    def test_draw_only_card_returns_true(self):
        """Plain 'draw a card' spell."""
        card = MockCard(template=MockTemplate(
            name="Generic Draw", tags={'draw'}))
        assert is_chain_fuel(card) is True

    def test_card_advantage_only_returns_true(self):
        """Past in Flames pattern (tagged card_advantage)."""
        card = MockCard(template=MockTemplate(
            name="Past in Flames", tags={'card_advantage', 'flashback'}))
        assert is_chain_fuel(card) is True

    def test_creature_with_no_chain_tags_returns_false(self):
        """Ral, Monsoon Mage pattern — creature with cost_reducer +
        mana_source tags but NO chain-extension tag.  This is the
        F2.1 trace state: Ral was being counted as fuel, holding
        Wish from firing.  Must return False."""
        card = MockCard(template=MockTemplate(
            name="Ral, Monsoon Mage",
            tags={'creature', 'cost_reducer', 'mana_source', 'early_play'},
        ))
        assert is_chain_fuel(card) is False

    def test_combo_only_tag_returns_false(self):
        """A card tagged only 'combo' (without ritual/cantrip/draw)
        is a finisher or marker, not chain fuel."""
        card = MockCard(template=MockTemplate(
            name="Some finisher", tags={'combo'}))
        assert is_chain_fuel(card) is False

    def test_tutor_only_tag_returns_false(self):
        """Wish pattern — tutor is a chain extender via the fetched
        payoff, but the tutor card itself is not fuel under this
        predicate.  The tutor branch in combo_calc.py handles
        tutor scoring separately."""
        card = MockCard(template=MockTemplate(
            name="Wish", tags={'tutor', 'combo'}))
        assert is_chain_fuel(card) is False

    def test_multiple_chain_tags_returns_true(self):
        """Manamorphose: ritual + cantrip + card_advantage."""
        card = MockCard(template=MockTemplate(
            name="Manamorphose",
            tags={'ritual', 'cantrip', 'card_advantage', 'mana_source'},
        ))
        assert is_chain_fuel(card) is True


# ─── is_ritual ────────────────────────────────────────────────────

class TestIsRitual:
    def test_ritual_tagged_returns_true(self):
        card = MockCard(template=MockTemplate(
            name="Pyretic Ritual", tags={'ritual'}))
        assert is_ritual(card) is True

    def test_no_ritual_tag_returns_false(self):
        card = MockCard(template=MockTemplate(
            name="Reckless Impulse", tags={'cantrip', 'card_advantage'}))
        assert is_ritual(card) is False

    def test_empty_tags_returns_false(self):
        card = MockCard(template=MockTemplate(name="Vanilla"))
        assert is_ritual(card) is False


# ─── is_draw_engine ───────────────────────────────────────────────

class TestIsDrawEngine:
    def test_cantrip_returns_true(self):
        card = MockCard(template=MockTemplate(
            name="Reckless Impulse", tags={'cantrip'}))
        assert is_draw_engine(card) is True

    def test_draw_returns_true(self):
        card = MockCard(template=MockTemplate(
            name="Concentrate", tags={'draw'}))
        assert is_draw_engine(card) is True

    def test_card_advantage_returns_true(self):
        card = MockCard(template=MockTemplate(
            name="Past in Flames", tags={'card_advantage', 'flashback'}))
        assert is_draw_engine(card) is True

    def test_pure_ritual_returns_false(self):
        """A ritual that doesn't draw is chain fuel but NOT a draw
        engine.  Pyretic Ritual is the canonical case."""
        card = MockCard(template=MockTemplate(
            name="Pyretic Ritual", tags={'ritual'}))
        assert is_draw_engine(card) is False

    def test_creature_returns_false(self):
        card = MockCard(template=MockTemplate(
            name="Ragavan", tags={'creature'}))
        assert is_draw_engine(card) is False

    def test_empty_tags_returns_false(self):
        card = MockCard(template=MockTemplate(name="Vanilla"))
        assert is_draw_engine(card) is False


# ─── count_lands ──────────────────────────────────────────────────

class TestCountLands:
    def test_empty_collection_returns_zero(self):
        assert count_lands([]) == 0

    def test_only_lands_counted(self):
        cards = [
            MockCard(template=MockTemplate(name="Mountain", is_land=True)),
            MockCard(template=MockTemplate(name="Mountain", is_land=True)),
            MockCard(template=MockTemplate(name="Bolt", is_land=False)),
        ]
        assert count_lands(cards) == 2

    def test_no_lands_returns_zero(self):
        cards = [
            MockCard(template=MockTemplate(name="Bolt", is_land=False)),
            MockCard(template=MockTemplate(name="Counterspell", is_land=False)),
        ]
        assert count_lands(cards) == 0

    def test_all_lands_returns_count(self):
        cards = [
            MockCard(template=MockTemplate(name=f"L{i}", is_land=True))
            for i in range(5)
        ]
        assert count_lands(cards) == 5

    def test_works_on_generator(self):
        """Iterable contract — should work on any iterable, not
        just lists."""
        gen = (
            MockCard(template=MockTemplate(name="Mountain", is_land=True))
            for _ in range(3)
        )
        assert count_lands(gen) == 3


# ─── count_gy_creatures ───────────────────────────────────────────

class TestCountGYCreatures:
    def test_empty_graveyard_returns_zero(self):
        assert count_gy_creatures([]) == 0

    def test_only_creatures_counted(self):
        gy = [
            MockCard(template=MockTemplate(name="Griselbrand", is_creature=True)),
            MockCard(template=MockTemplate(name="Bolt", is_creature=False)),
            MockCard(template=MockTemplate(name="Atraxa", is_creature=True)),
        ]
        assert count_gy_creatures(gy) == 2

    def test_no_creatures_returns_zero(self):
        gy = [
            MockCard(template=MockTemplate(name="Bolt", is_creature=False)),
            MockCard(template=MockTemplate(name="Manamorphose", is_creature=False)),
        ]
        assert count_gy_creatures(gy) == 0

    def test_lands_in_graveyard_not_counted(self):
        """Lands in graveyard (Wasteland triggers, etc.) shouldn't
        count as creatures even if their other flags are weird."""
        gy = [
            MockCard(template=MockTemplate(
                name="Mountain", is_creature=False, is_land=True)),
            MockCard(template=MockTemplate(name="Atraxa", is_creature=True)),
        ]
        assert count_gy_creatures(gy) == 1
