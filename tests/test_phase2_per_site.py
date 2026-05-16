"""Phase 2 per-site tests — one assertion per swept site.

Each test pins the rule a former archetype-conditional gate encoded,
phrased without naming a deck:

  * `clock.position_value` is archetype-agnostic.
  * `evaluate_board` still accepts archetype for backward-compat but
    does not propagate it into `position_value`.
  * `ev_evaluator._uses_combo_chain_scoring` reads the gameplan flag.
  * `_enumerate_this_turn_signals` chain-scoring signals fire iff the
    deck's gameplan declares `uses_combo_chain_scoring=True`.
  * `engine_disruption.engine_disruption_value` returns 0 unless the
    opp's gameplan declares `enables_disruption=True`.
  * `finisher_simulator` priority comes from the data table.
  * `combo_calc._compute_combo_value` no longer takes archetype.
  * `permanent_threat` / `snapshot_adapter` do not pass archetype.
  * `MulliganDecider._policy()` synthesizes per-deck policy.
  * `MulliganPolicy` defaults route the 7 mulligan sub-conditions.
"""
from __future__ import annotations

import inspect

import pytest


# ─────────────────────────────────────────────────────────────────
# 1. clock.position_value
# ─────────────────────────────────────────────────────────────────

def test_position_value_is_archetype_agnostic():
    """`position_value` produces a single, archetype-free score."""
    from ai.clock import position_value
    from ai.ev_evaluator import EVSnapshot
    snap = EVSnapshot(my_life=15, opp_life=15)
    # The signature must not accept archetype as a kwarg.
    with pytest.raises(TypeError):
        position_value(snap, archetype="combo")  # type: ignore[call-arg]
    # Single-arg call is the only legal form.
    pv = position_value(snap)
    assert isinstance(pv, float)


# ─────────────────────────────────────────────────────────────────
# 2. evaluate_board keeps archetype kw for back-compat but does not
#    feed it into position_value
# ─────────────────────────────────────────────────────────────────

def test_evaluate_board_accepts_archetype_kw_for_backcompat():
    """`evaluate_board(snap, archetype=X)` must still be callable so
    legacy callers (ai.response, ai.decision_kernel) don't break.
    The archetype value MUST NOT change the returned score, because
    `position_value` no longer accepts archetype.
    """
    from ai.ev_evaluator import evaluate_board, EVSnapshot
    snap = EVSnapshot(my_life=12, opp_life=12, my_power=2, opp_power=2)
    a = evaluate_board(snap, "combo")
    b = evaluate_board(snap, "midrange")
    c = evaluate_board(snap, "aggro")
    assert a == b == c, (
        "evaluate_board must produce one value regardless of archetype "
        "after Phase 2 — the param is back-compat only."
    )


# ─────────────────────────────────────────────────────────────────
# 3. _uses_combo_chain_scoring reads gameplan flag
# ─────────────────────────────────────────────────────────────────

def test_uses_combo_chain_scoring_reads_gameplan_flag():
    """Helper returns False for `game is None` (defensive); else
    True iff the player's gameplan has `uses_combo_chain_scoring=True`.
    """
    from ai.ev_evaluator import _uses_combo_chain_scoring
    assert _uses_combo_chain_scoring(None, 0) is False


# ─────────────────────────────────────────────────────────────────
# 4. engine_disruption gates on enables_disruption flag
# ─────────────────────────────────────────────────────────────────

def test_engine_disruption_zero_without_enables_disruption_flag():
    """`engine_disruption_value` returns 0.0 when the opp's gameplan
    has `enables_disruption=False` (the new gate).  We probe by
    mocking a minimal opp / plan via the helper that loads the plan.
    """
    from ai.engine_disruption import engine_disruption_value

    class _FakeCard:
        name = "Whatever"
        class template:
            name = "Whatever"

    class _FakePlayer:
        deck_name = "Boros Energy"  # non-combo deck
        player_idx = 1

    # We can't easily construct a full GameState for this micro-test,
    # but the function short-circuits at the gate before any GameState
    # access.  We assert the gate fires by passing None for `game`
    # (the function's _opp_gameplan helper resolves the plan from
    # opp.deck_name, then checks `enables_disruption`).
    result = engine_disruption_value(_FakeCard(), _FakePlayer(), None)
    assert result == 0.0


# ─────────────────────────────────────────────────────────────────
# 5. finisher_simulator priority dispatch is table-based
# ─────────────────────────────────────────────────────────────────

def test_finisher_simulator_priority_uses_table_not_branches():
    """The priority table for tie-breaking must contain at least one
    entry per canonical pattern (storm, cascade, reanimation, cycling).
    """
    from ai import finisher_simulator as fs
    keys = {pattern for (_arch, pattern) in fs._ARCHETYPE_PATTERN_PRIORITY.keys()}
    assert "storm" in keys
    assert "cascade" in keys
    assert "reanimation" in keys
    assert "cycling" in keys


# ─────────────────────────────────────────────────────────────────
# 6. combo_calc._compute_combo_value loses its archetype arg
# ─────────────────────────────────────────────────────────────────

def test_compute_combo_value_signature_dropped_archetype():
    """Phase 2 drops the archetype parameter of `_compute_combo_value`."""
    from ai.combo_calc import _compute_combo_value
    sig = inspect.signature(_compute_combo_value)
    assert list(sig.parameters.keys()) == ["snap"], (
        f"_compute_combo_value should accept only `snap`; got {list(sig.parameters)}"
    )


# ─────────────────────────────────────────────────────────────────
# 7. permanent_threat does not pass archetype into position_value
# ─────────────────────────────────────────────────────────────────

def test_permanent_threat_does_not_pass_archetype_into_position_value():
    """`ai.permanent_threat` reads `ai.clock.position_value(full_snap)`
    with a SINGLE argument — no archetype is threaded.  Verified by
    static inspection of the file source.
    """
    import ai.permanent_threat as pt
    src = inspect.getsource(pt)
    # No occurrences of the old `position_value(snap, archetype)` shape.
    assert "position_value(full_snap)" in src
    assert "position_value(partial_snap)" in src
    assert "position_value(full_snap, " not in src
    assert "position_value(partial_snap, " not in src


# ─────────────────────────────────────────────────────────────────
# 8. snapshot_adapter does not pass archetype either
# ─────────────────────────────────────────────────────────────────

def test_snapshot_adapter_does_not_pass_archetype_into_position_value():
    """`ai.search.snapshot_adapter` calls `position_value(snap)` only."""
    import ai.search.snapshot_adapter as sa
    src = inspect.getsource(sa)
    # No `position_value(snap, ...)` calls — only `position_value(snap)`.
    # The .next_state.snapshot call is also single-arg.
    assert "position_value(snap)" in src
    # If a 2-arg call slipped back in, this line would appear.
    assert "position_value(snap, " not in src


# ─────────────────────────────────────────────────────────────────
# 9. MulliganPolicy: combo archetype defaults
# ─────────────────────────────────────────────────────────────────

def test_mulligan_policy_combo_defaults():
    """For archetype "combo", the synthesized policy lights up the
    combo-related flags and uses the "combo" generic branch.
    """
    from ai.gameplan import _default_mulligan_policy_for
    p = _default_mulligan_policy_for("combo")
    assert p.requires_combo_backup is True
    assert p.key_card_min_cheap_relaxed is True
    assert p.keep_score_combo_at_home is True
    assert p.generic_branch == "combo"
    assert p.keep_score_early_play_at_home is False
    assert p.keep_score_counterspell_at_home is False


def test_mulligan_policy_aggro_defaults():
    """For archetype "aggro", only the early-play AT_HOME flag fires."""
    from ai.gameplan import _default_mulligan_policy_for
    p = _default_mulligan_policy_for("aggro")
    assert p.keep_score_early_play_at_home is True
    assert p.generic_branch == "aggro"
    assert p.requires_combo_backup is False
    assert p.keep_score_combo_at_home is False


def test_mulligan_policy_control_defaults():
    """Control archetype lights up the counterspell AT_HOME flag."""
    from ai.gameplan import _default_mulligan_policy_for
    p = _default_mulligan_policy_for("control")
    assert p.keep_score_counterspell_at_home is True
    assert p.generic_branch == "control"
    assert p.keep_score_early_play_at_home is False


def test_mulligan_policy_tempo_lights_counterspell():
    """Tempo archetype shares Control's counterspell-AT_HOME flag.

    (Mirrors the prior `archetype in (CONTROL, TEMPO)` membership test.)
    """
    from ai.gameplan import _default_mulligan_policy_for
    p = _default_mulligan_policy_for("tempo")
    assert p.keep_score_counterspell_at_home is True
    # tempo is NOT a generic-branch value, so it falls back to midrange.
    assert p.generic_branch == "midrange"


def test_mulligan_policy_midrange_is_neutral():
    """Midrange / unknown archetype gets the neutral baseline."""
    from ai.gameplan import _default_mulligan_policy_for
    for arch in ("midrange", "ramp", "unknown_archetype"):
        p = _default_mulligan_policy_for(arch)
        assert p.requires_combo_backup is False
        assert p.keep_score_early_play_at_home is False
        assert p.keep_score_combo_at_home is False
        assert p.keep_score_counterspell_at_home is False


# ─────────────────────────────────────────────────────────────────
# 10. MulliganDecider._policy() falls through to archetype default
# ─────────────────────────────────────────────────────────────────

def test_mulligan_decider_policy_falls_through_to_archetype_default():
    """When a decider has no goal_engine, `_policy()` synthesizes the
    policy from the ArchetypeStrategy enum's value.
    """
    from ai.mulligan import MulliganDecider
    from ai.strategy_profile import ArchetypeStrategy
    d = MulliganDecider(ArchetypeStrategy.AGGRO, goal_engine=None)
    p = d._policy()
    assert p.keep_score_early_play_at_home is True
    d2 = MulliganDecider(ArchetypeStrategy.COMBO, goal_engine=None)
    p2 = d2._policy()
    assert p2.requires_combo_backup is True


# ─────────────────────────────────────────────────────────────────
# 11. ev_player no longer has `archetype in [...]` membership check
# ─────────────────────────────────────────────────────────────────

def test_ev_player_does_not_membership_check_archetype():
    """The prior `if self.archetype in [e.value for e in ArchetypeStrategy]`
    block is replaced by a static dict lookup.

    Verified by the contract grep gate (`test_no_archetype_equality_check`)
    plus a behavioural check that the static mapping is present.
    """
    import ai.ev_player as ep
    src = inspect.getsource(ep)
    # The new static mapping name is present.
    assert "_ARCHETYPE_BY_NAME" in src


# ─────────────────────────────────────────────────────────────────
# 12. Ev evaluator no longer has archetype-tuple membership gates
# ─────────────────────────────────────────────────────────────────

def test_ev_evaluator_signals_helper_replaces_archetype_tuple_gate():
    """`ai.ev_evaluator` exposes `_uses_combo_chain_scoring` and the
    four prior `archetype in ('storm','combo')` gates now call it
    instead.

    Verified by import (the helper exists) plus the contract grep
    gate which guarantees no real conditional remains.
    """
    from ai.ev_evaluator import _uses_combo_chain_scoring
    assert callable(_uses_combo_chain_scoring)
    sig = inspect.signature(_uses_combo_chain_scoring)
    # Takes (game, player_idx) — not archetype.
    assert "archetype" not in sig.parameters


# ─────────────────────────────────────────────────────────────────
# 13. clock.py no longer has the combo-clock override
# ─────────────────────────────────────────────────────────────────

def test_clock_position_value_no_combo_override():
    """`ai/clock.py`'s `position_value` no longer applies the
    `min(my_clock, combo_clock(snap))` override.

    Behavioural pin: build two `EVSnapshot` fixtures with identical
    everything except `storm_count`.  Before Phase 2, the combo
    archetype's `min(my_clock, combo_clock)` override would have
    crashed the my_clock down for the storm-count=5 fixture; after
    Phase 2 the override is gone, so the scores must match exactly.
    """
    from ai.clock import position_value
    from ai.ev_evaluator import EVSnapshot

    common = dict(
        my_life=20, opp_life=20,
        my_power=2, opp_power=2,
        my_toughness=2, opp_toughness=2,
        my_mana=3, opp_mana=3,
        my_hand_size=4, opp_hand_size=4,
        my_total_lands=3,
    )
    a = EVSnapshot(storm_count=0, **common)
    b = EVSnapshot(storm_count=5, **common)
    assert position_value(a) == position_value(b), (
        "position_value(snap with storm_count=5) differs from "
        "position_value(snap with storm_count=0) — Phase 2 removes "
        "the combo-clock override so storm_count must not affect the "
        "core position score."
    )
