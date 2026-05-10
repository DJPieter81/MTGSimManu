"""Phase 4A — opt-in MTGSIM_USE_MCTS feature flag wiring.

Smoke tests that:

1. With the env var unset (or set to a falsy token), ``EVPlayer``
   constructs a vanilla ``TurnPlanner``. This is the production
   matrix-sim path; default behavior must be unchanged.

2. With ``MTGSIM_USE_MCTS=1`` (or any truthy token), ``EVPlayer``
   constructs an ``ISMCTSPlanner`` configured with a heuristic
   ``TurnPlanner`` as its fallback. Method delegation routes
   ``evaluate_response``-style calls back to the heuristic.

3. The MCTS-enabled path can plan-turn end-to-end on a minimal
   ``VirtualBoard`` fixture without raising. We make NO claim about
   plan quality — this is purely a wiring smoke test. Acceptance
   gate (≥ 4 of 12 strict wins, 0 regressions) lives separately in
   ``test_ismcts_acceptance_real.py``.

Reference:
- docs/research/2026-05_phase_4a_ismcts_scoping.md (§Risks: opt-in
  only, default path unchanged).
- ai/ev_player.py::_build_turn_planner — the factory under test.
"""
from __future__ import annotations

import os
import random

import pytest

from ai.ev_player import EVPlayer, _build_turn_planner, _mcts_flag_enabled
from ai.search.ismcts import ISMCTSPlanner
from ai.turn_planner import TurnPlanner, VirtualBoard


# ─── Fixture: deterministic env-var management ───────────────────────


@pytest.fixture
def mcts_env(monkeypatch):
    """Yield a context manager that sets/unsets MTGSIM_USE_MCTS.

    Using monkeypatch ensures the env var is restored after the
    test no matter what — tests run after this one see the
    pre-test value (typically unset, which is the default
    matrix-sim path).
    """
    def _set(value):
        if value is None:
            monkeypatch.delenv("MTGSIM_USE_MCTS", raising=False)
        else:
            monkeypatch.setenv("MTGSIM_USE_MCTS", value)
    return _set


# ─── Tier 1: flag-detection unit ─────────────────────────────────────


class TestMCTSFlagDetection:
    """The flag-detection helper drives the factory below; verify
    its truthy/falsy semantics directly so factory tests can focus
    on the construction, not the parsing."""

    def test_flag_unset_is_falsy(self, mcts_env):
        mcts_env(None)
        assert _mcts_flag_enabled() is False

    def test_flag_empty_string_is_falsy(self, mcts_env):
        mcts_env("")
        assert _mcts_flag_enabled() is False

    @pytest.mark.parametrize("token", ["0", "false", "no", "off", "FALSE", "Off"])
    def test_off_tokens_are_falsy(self, mcts_env, token):
        mcts_env(token)
        assert _mcts_flag_enabled() is False, (
            f"{token!r} should be treated as off (composability "
            f"with shells passing MTGSIM_USE_MCTS=0)"
        )

    @pytest.mark.parametrize("token", ["1", "true", "yes", "on", "anything"])
    def test_truthy_tokens_enable_flag(self, mcts_env, token):
        mcts_env(token)
        assert _mcts_flag_enabled() is True, (
            f"{token!r} should be treated as truthy (any non-empty "
            f"non-off-token enables MCTS)"
        )


# ─── Tier 2: planner factory ─────────────────────────────────────────


class TestBuildTurnPlannerFactory:
    """Tests that ``_build_turn_planner`` honors the env var."""

    def test_default_returns_heuristic_turn_planner(self, mcts_env):
        """Flag unset → heuristic TurnPlanner. This is the
        production matrix-sim path; default behavior must be
        preserved bit-for-bit."""
        mcts_env(None)
        planner = _build_turn_planner()
        assert isinstance(planner, TurnPlanner)
        assert not isinstance(planner, ISMCTSPlanner)

    def test_flag_off_token_returns_heuristic_turn_planner(self, mcts_env):
        """Explicit off token also yields the heuristic — composes
        with shells passing ``MTGSIM_USE_MCTS=0``."""
        mcts_env("0")
        planner = _build_turn_planner()
        assert isinstance(planner, TurnPlanner)
        assert not isinstance(planner, ISMCTSPlanner)

    def test_flag_set_returns_ismcts_with_heuristic_fallback(self, mcts_env):
        """Truthy flag → ISMCTSPlanner whose fallback is a
        TurnPlanner. The fallback is what makes the swap safe — any
        ResponseDecider call routes through the heuristic via
        delegation."""
        mcts_env("1")
        planner = _build_turn_planner()
        assert isinstance(planner, ISMCTSPlanner)
        assert isinstance(planner.fallback, TurnPlanner)


# ─── Tier 3: EVPlayer wiring ─────────────────────────────────────────


class TestEVPlayerHonorsFlag:
    """The flag must propagate through ``EVPlayer.__init__`` to the
    turn planner held by the response decider. Without this, the
    factory is dead code."""

    def test_default_player_uses_heuristic(self, mcts_env):
        mcts_env(None)
        player = EVPlayer(
            player_idx=0,
            deck_name="Dimir Midrange",
            rng=random.Random(0),
        )
        assert isinstance(player.turn_planner, TurnPlanner)
        assert not isinstance(player.turn_planner, ISMCTSPlanner)
        # Response decider must hold the same instance — the env
        # var is the only switch, no second copy.
        assert player._response_decider.turn_planner is player.turn_planner

    def test_flag_set_player_uses_ismcts(self, mcts_env):
        mcts_env("1")
        player = EVPlayer(
            player_idx=0,
            deck_name="Dimir Midrange",
            rng=random.Random(0),
        )
        assert isinstance(player.turn_planner, ISMCTSPlanner)
        assert isinstance(player.turn_planner.fallback, TurnPlanner)
        assert player._response_decider.turn_planner is player.turn_planner


# ─── Tier 4: end-to-end plan_turn smoke ──────────────────────────────


def _empty_virtual_board():
    """Build a minimal VirtualBoard fixture: empty creatures on both
    sides, opening life, no hand, baseline mana. Sufficient for the
    smoke that ``plan_turn`` runs end-to-end through the fallback."""
    return VirtualBoard(
        my_creatures=[],
        opp_creatures=[],
        my_life=20,
        opp_life=20,
        my_hand=[],
        my_mana=0,
        opp_mana=0,
    )


class TestMCTSPathRunsEndToEnd:
    """Wiring smoke — no claim about quality.

    The acceptance gate (MCTS strictly dominates on ≥ 4 of 12
    fixtures) lives in ``test_ismcts_acceptance_real.py``. Here we
    only verify the opt-in code path runs without crashing.
    """

    def test_plan_turn_returns_a_plan_via_fallback(self, mcts_env):
        """``ISMCTSPlanner.plan_turn`` delegates to the fallback
        until the GameState→MCTS wiring lands (Week-3). This test
        documents that contract: the path runs end-to-end and
        yields a plan object with the heuristic ``TurnPlan`` shape.

        Rule encoded: ``MTGSIM_USE_MCTS=1`` MUST not regress the
        ability to produce a turn plan. If the wiring breaks, this
        test goes red before any matrix run does.
        """
        mcts_env("1")
        planner = _build_turn_planner()
        board = _empty_virtual_board()
        plan = planner.plan_turn(board)
        # The fallback's TurnPlan dataclass — duck-typed check via
        # the documented attributes from ai.turn_planner.TurnPlan.
        assert hasattr(plan, "expected_score")
        assert hasattr(plan, "attack_config")
        assert hasattr(plan, "reasoning")

    def test_method_delegation_to_fallback(self, mcts_env):
        """Methods other than ``plan_turn`` reach the heuristic via
        ``__getattr__``. Verify with a real attribute on
        ``TurnPlanner`` — ``combat_planner`` is set in
        ``TurnPlanner.__init__`` and is exactly the kind of legacy
        access pattern callers use."""
        mcts_env("1")
        planner = _build_turn_planner()
        # Through delegation: TurnPlanner has a combat_planner.
        from ai.turn_planner import CombatPlanner
        assert isinstance(planner.combat_planner, CombatPlanner)

    def test_ismcts_without_fallback_raises_on_plan_turn(self):
        """Defensive: an MCTS planner constructed with no fallback
        and no GameState→MCTS wiring must fail loudly on
        ``plan_turn`` rather than silently returning a bogus plan.

        Rule encoded: the production opt-in path must always pair
        ISMCTSPlanner with a heuristic fallback. ``_build_turn_planner``
        enforces this — this test pins the behavior of the bare
        constructor too.
        """
        bare = ISMCTSPlanner()  # no fallback configured
        with pytest.raises(RuntimeError, match="fallback"):
            bare.plan_turn(_empty_virtual_board())
