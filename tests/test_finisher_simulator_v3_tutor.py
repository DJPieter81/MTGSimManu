"""Failing-first tests for `_tutor_access_contribution` (sim v3 PR1).

Pre-fix the function at ``ai/finisher_simulator_v3.py:490`` raises
``NotImplementedError``. The fallback wrapper ``_safe_tutor_resolve_p``
catches that and returns the floor constant ``CHAIN_TUTOR_MIN_RESOLVE``.

Post-fix the function returns ``(best_tutor, extra_cost, p_resolves)``
per design §4.2:

  - ``best_tutor`` is the lowest-CMC tutor in hand whose payoff is
    reachable in the sideboard or in the library composition.
  - ``extra_cost`` is the tutor's CMC.
  - ``p_resolves = max(CHAIN_TUTOR_MIN_RESOLVE, 1 - p_counter)``.

Returns ``(None, 0, 0.0)`` when no tutor-with-access exists.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai.finisher_simulator_v3 import (
    LibraryComposition,
    _tutor_access_contribution,
)
from ai.scoring_constants import CHAIN_TUTOR_MIN_RESOLVE
from engine.card_database import CardDatabase
from engine.cards import CardInstance


@pytest.fixture(scope="module")
def card_db() -> CardDatabase:
    return CardDatabase()


def _instance(card_db: CardDatabase, name: str, instance_id: int = 0) -> CardInstance:
    template = card_db.cards.get(name)
    if template is None:
        pytest.skip(f"Card not in DB: {name}")
    return CardInstance(
        template=template,
        owner=0,
        controller=0,
        instance_id=instance_id,
        zone="hand",
    )


def _bhi(p_counter: float) -> MagicMock:
    """Mocked BayesianHandTracker that returns a fixed counter
    probability. The function must NOT depend on any other BHI
    method, so the mock is intentionally narrow."""
    bhi = MagicMock()
    bhi.get_counter_probability.return_value = p_counter
    return bhi


def _snap() -> MagicMock:
    """Minimal EVSnapshot stub. _tutor_access_contribution does not
    read snap fields directly in the design §4.2 algorithm — the
    multi-turn rollout caller passes its own future_snap when the
    rollout amortises tutor cost across turns. The stub is here so
    the call signature matches."""
    return MagicMock(spec=["my_mana", "opp_clock_discrete"])


def _empty_lib() -> LibraryComposition:
    return LibraryComposition(total=0, by_tag={}, closer_count=0,
                              closer_categories=())


def _lib_with_storm_closer(count: int = 4) -> LibraryComposition:
    return LibraryComposition(
        total=50,
        by_tag={"ritual": 8, "cantrip": 6, "tutor": 2,
                "storm_closer": count},
        closer_count=count,
        closer_categories=("storm_closer",),
    )


# ──────────────────────────────────────────────────────────────────
# Positive cases — tutor with access returns non-None / non-zero
# ──────────────────────────────────────────────────────────────────

def test_tutor_in_hand_with_sb_storm_closer_returns_nonzero_p_resolves(card_db):
    """Hand has Wish (tutor tag), SB has a storm-keyword
    finisher (Grapeshot). Must return Wish + nonzero
    p_resolves."""
    db = card_db
    if "Wish" not in db.cards or "Grapeshot" not in db.cards:
        pytest.skip("Wish or Grapeshot not in DB")
    hand = [_instance(db, "Wish", 1)]
    sideboard = [_instance(db, "Grapeshot", 2)]
    bhi = _bhi(p_counter=0.0)
    tutor, extra_cost, p_resolves = _tutor_access_contribution(
        hand, sideboard, _empty_lib(), _snap(), bhi)
    assert tutor is not None, "Tutor with payoff in SB must be returned"
    assert tutor.template.name == "Wish"
    assert extra_cost == tutor.template.cmc
    assert p_resolves >= CHAIN_TUTOR_MIN_RESOLVE


def test_tutor_with_library_only_payoff_is_returned(card_db):
    """Tutor in hand, no SB closer, library composition reports
    storm closers — function must use library_composition path."""
    db = card_db
    if "Wish" not in db.cards:
        pytest.skip("Wish not in DB")
    hand = [_instance(db, "Wish", 1)]
    bhi = _bhi(p_counter=0.0)
    tutor, extra_cost, p_resolves = _tutor_access_contribution(
        hand, [], _lib_with_storm_closer(count=4), _snap(), bhi)
    assert tutor is not None, (
        "Library composition with storm closers must enable tutor "
        "even when sideboard is empty"
    )


# ──────────────────────────────────────────────────────────────────
# Negative cases — no tutor / no payoff returns sentinel
# ──────────────────────────────────────────────────────────────────

def test_no_tutor_in_hand_returns_none_zero_zero(card_db):
    """Hand has no tutor-tagged card."""
    db = card_db
    if "Lightning Bolt" not in db.cards:
        pytest.skip("Lightning Bolt not in DB")
    hand = [_instance(db, "Lightning Bolt", 1)]
    bhi = _bhi(p_counter=0.0)
    tutor, extra_cost, p_resolves = _tutor_access_contribution(
        hand, [], _empty_lib(), _snap(), bhi)
    assert tutor is None
    assert extra_cost == 0
    assert p_resolves == 0.0


def test_tutor_with_no_payoff_anywhere_returns_none_zero_zero(card_db):
    """Tutor in hand, no payoff in SB, no closer in library
    composition. Function must return the (None, 0, 0.0) sentinel."""
    db = card_db
    if "Wish" not in db.cards:
        pytest.skip("Wish not in DB")
    hand = [_instance(db, "Wish", 1)]
    bhi = _bhi(p_counter=0.0)
    tutor, extra_cost, p_resolves = _tutor_access_contribution(
        hand, [], _empty_lib(), _snap(), bhi)
    assert tutor is None
    assert extra_cost == 0
    assert p_resolves == 0.0


# ──────────────────────────────────────────────────────────────────
# Resolution probability — counter pressure dampens p_resolves
# but never below CHAIN_TUTOR_MIN_RESOLVE
# ──────────────────────────────────────────────────────────────────

def test_p_resolves_floored_at_min_resolve_under_max_counters(card_db):
    """When BHI reports certain counter (p_counter=1.0), p_resolves
    must clamp to CHAIN_TUTOR_MIN_RESOLVE — not zero."""
    db = card_db
    if "Wish" not in db.cards or "Grapeshot" not in db.cards:
        pytest.skip("DB cards missing")
    hand = [_instance(db, "Wish", 1)]
    sideboard = [_instance(db, "Grapeshot", 2)]
    bhi = _bhi(p_counter=1.0)
    _, _, p_resolves = _tutor_access_contribution(
        hand, sideboard, _empty_lib(), _snap(), bhi)
    assert p_resolves == CHAIN_TUTOR_MIN_RESOLVE


def test_p_resolves_higher_when_counter_probability_is_low(card_db):
    """Low counter probability → high p_resolves (close to 1.0).
    Pin the monotonicity: lower p_counter → higher p_resolves."""
    db = card_db
    if "Wish" not in db.cards or "Grapeshot" not in db.cards:
        pytest.skip("DB cards missing")
    hand = [_instance(db, "Wish", 1)]
    sideboard = [_instance(db, "Grapeshot", 2)]
    _, _, p_low = _tutor_access_contribution(
        hand, sideboard, _empty_lib(), _snap(),
        _bhi(p_counter=0.05))
    _, _, p_high = _tutor_access_contribution(
        hand, sideboard, _empty_lib(), _snap(),
        _bhi(p_counter=0.4))
    assert p_low > p_high
    assert p_low >= CHAIN_TUTOR_MIN_RESOLVE
    assert p_high >= CHAIN_TUTOR_MIN_RESOLVE
