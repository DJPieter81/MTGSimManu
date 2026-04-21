"""Regression: `_last_candidates` must be invalidated at the top of every
`decide_main_phase` call, so trace/debug consumers never read stale
candidates from a prior call (e.g. `cast_spell: Ajani` showing up after
Ajani has already resolved to battlefield).

Evidence: replays/boros_vs_affinity_trace_s65000.txt T3 call #3 — Ajani
appears as a cast_spell candidate despite being on the battlefield (not
in `legal = get_legal_plays`). Root cause: `decide_main_phase` had two
early-return paths (`if not legal: return None`) that skipped clearing
`_last_candidates`, so the previous call's scored candidates persisted.

Holistic fix: clear `self._last_candidates = []` at entry, so *every*
return path leaves the field consistent with the current decision.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def test_last_candidates_cleared_when_no_legal_plays(card_db):
    """When `decide_main_phase` returns early because `legal = []`,
    `_last_candidates` must reflect the current (empty) decision, not
    retain whatever was scored on the previous call."""
    game = GameState(rng=random.Random(0))
    # Empty hand, zero mana, no battlefield → get_legal_plays(0) returns [].
    player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                      rng=random.Random(0))

    # Seed a stale candidate list to simulate a prior call's state.
    sentinel = ["STALE_FROM_PRIOR_CALL"]
    player._last_candidates = sentinel

    result = player.decide_main_phase(game)

    assert result is None, (
        f"Expected None when legal plays is empty, got {result}"
    )
    assert player._last_candidates == [], (
        f"_last_candidates should be cleared at entry so stale data from "
        f"prior calls never leaks to trace/debug consumers. "
        f"Got {player._last_candidates!r} — still holds the sentinel."
    )
    assert player._last_candidates is not sentinel, (
        "Same list object — was reassigned to [] but previous reference "
        "would still see stale data. The fix should reassign, not mutate."
    )
