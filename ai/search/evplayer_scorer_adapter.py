"""Production EVPlayer scorer adapter for the A/B harness — Phase 5 step 1.

Bridges ``ai/search/snapshot_adapter.py``'s synthetic ``SearchState`` /
``ActionToken`` representation to the production scoring path used by
``ai/ev_player.py`` (``compute_play_ev`` / ``estimate_spell_ev``).

Why this module exists
----------------------
``ai/search/ab_compare.py``'s ``compare_planners`` accepts a callable
``heuristic_picker: state -> action`` to compare against the ISMCTS
pick. The Phase-4A acceptance gate (12-fixture corpus, see
``tests/test_ismcts_acceptance_real.py``) currently uses
``snapshot_adapter.heuristic_rollout`` for that callable — a synthetic
1-ply scorer that picks the action maximising the post-state's
``ai.clock.position_value``.

That synthetic scorer is engine-agnostic but does NOT exercise the
production scoring formulation:

  EV = evaluate_board(after_snap) - evaluate_board(current_snap)

with archetype awareness, urgency-factor discounting for non-immediate
effects, and the same value-delta sign convention used by
``ai.ev_evaluator.compute_play_ev``. A heuristic baseline that mirrors
that production formulation makes the A/B comparison apples-to-apples
against the ISMCTS path (whose rollout policy can also be wired through
this module if desired).

What this adapter does NOT do
-----------------------------
The 12-fixture corpus is snapshot-only — fixtures construct an
``EVSnapshot`` plus a list of ``ActionToken`` deltas. Production
scoring for *real* ``CardInstance`` plays additionally pulls in:

  - Bayesian Hand Inference for opponent counter / removal probability
  - Per-card oracle text (``_score_spell``'s deck-knowledge hooks)
  - Goal engine state (``GoalEngine.current_goal`` × per-goal weights)
  - Combo-chain assessment (``_estimate_combo_chain`` with full hand)

Those production paths require ``CardInstance`` and ``GameState`` —
neither of which the snapshot fixtures carry. Wiring the *full*
production scorer is the multi-day Phase-5 scope; this thin adapter is
step 1 of that work and is restricted to what the snapshot-only
fixtures can express:

  - ``evaluate_board`` value-delta (replaces the synthetic
    ``position_value`` of the post-state).
  - ``EVSnapshot.urgency_factor`` discount on non-immediate-effect
    actions (mirrors ``compute_play_ev`` line ~2626).
  - ``archetype_subtype`` propagation so combo decks get the
    combo-clock override in ``position_value``.

The follow-up PR (full Phase 5 scope) will build a richer adapter that
constructs synthetic ``CardInstance`` proxies from the action tokens
and routes through ``compute_play_ev`` directly. The thin adapter
proven here keeps the 12-fixture acceptance gate apples-to-apples in
the meantime: the same scoring primitive the production path uses
(``evaluate_board``) drives the heuristic baseline, instead of the
synthetic ``position_value`` proxy.

Reference: ``docs/research/2026-05_phase_4a_ismcts_scoping.md``;
``docs/handoff/2026-05_session_summary.md`` § "Phase 5 — production
scorer wiring".
"""
from __future__ import annotations

import random
from typing import List, Optional, Tuple

from ai.ev_evaluator import EVSnapshot, evaluate_board
from ai.search.snapshot_adapter import (
    ActionToken,
    SearchState,
    apply_action,
    enumerate_actions,
)


# Action kinds whose effect materialises immediately when resolved
# (combat damage / mana / draw / removal-equivalent on opp_life).
# Non-immediate kinds get discounted by ``EVSnapshot.urgency_factor``,
# mirroring the production ``compute_play_ev`` discount applied via
# ``_has_immediate_effect`` on real CardInstances.
#
# This is a token-kind classifier, NOT a card-name classifier — the
# six kinds defined in ``snapshot_adapter.ActionToken`` (cast_creature,
# cast_artifact, play_land, burn, draw, pass) map cleanly to immediate
# vs. delayed without naming any card. ``cast_artifact`` is the one
# borderline: a Mox is immediate (mana now), an Urza's Saga construct
# is delayed (tokens-over-time). Given the synthetic fixtures, we
# treat ``cast_artifact`` as immediate-by-default — matching the
# production default of ``_has_immediate_effect`` returning True for
# unrecognised cards. The production path handles the
# delayed-artifact case via oracle text on real CardInstances; the
# adapter cannot do that on snapshot-only fixtures.
_IMMEDIATE_KINDS = frozenset({
    "cast_creature",   # body blocks/attacks immediately
    "cast_artifact",   # default-immediate (see above)
    "play_land",       # mana now
    "burn",            # damage now
    "draw",            # card advantage now
    "pass",            # turn-end damage tick
})


def _post_action_snapshot(
    state: SearchState, action: ActionToken, rng: random.Random,
) -> EVSnapshot:
    """Return the EVSnapshot that would result from applying ``action``.

    Delegates to ``apply_action`` so the same forward-simulation
    primitive that ``ab_compare._forward_sim_reward`` and the ISMCTS
    rollouts use is the one driving the picker — no divergent state
    semantics between the heuristic baseline and the search.
    """
    next_state = apply_action(state, action, rng)
    return next_state.snapshot


def _score_action_production(
    state: SearchState,
    action: ActionToken,
    archetype: str,
    rng: random.Random,
) -> float:
    """Compute the production-style EV-delta for an action.

    Mirrors the formulation used in ``ai.ev_evaluator.compute_play_ev``
    for the snapshot-only path:

        ev = evaluate_board(after) - evaluate_board(before)
        if not immediate-effect and ev > 0:
            ev *= snap.urgency_factor

    The archetype string flows through ``evaluate_board`` →
    ``position_value`` → ``combo_clock`` so combo / storm fixtures get
    the combo-clock override the production path uses for those
    archetypes. For midrange / aggro / control fixtures the override
    is a no-op (combat clock dominates).
    """
    before_snap = state.snapshot
    after_snap = _post_action_snapshot(state, action, rng)

    before = evaluate_board(before_snap, archetype)
    after = evaluate_board(after_snap, archetype)
    ev = after - before

    # Urgency-factor discount on non-immediate effects, only for
    # positive EV. Mirrors the production guard at
    # ``compute_play_ev`` line 2625-6: discounting a negative EV would
    # make a bad play look better, which the production path
    # explicitly avoids.
    if action.kind not in _IMMEDIATE_KINDS and ev > 0:
        ev *= before_snap.urgency_factor

    return ev


def _resolve_archetype(state: SearchState, archetype: Optional[str]) -> str:
    """Pick the archetype to feed ``evaluate_board``.

    Precedence (mirrors how production code resolves archetype):
      1. Caller-supplied ``archetype`` (e.g. fixture metadata).
      2. ``snapshot.archetype_subtype`` (set by ``snapshot_from_game``
         from the deck's gameplan).
      3. ``"midrange"`` default (matches ``position_value``'s default).
    """
    if archetype:
        return archetype
    sub = getattr(state.snapshot, "archetype_subtype", None)
    if sub:
        return sub
    return "midrange"


def production_scorer_picker(
    state: SearchState,
    rng: random.Random,
    archetype: Optional[str] = None,
) -> ActionToken:
    """Drop-in replacement for ``snapshot_adapter.heuristic_rollout``.

    Picks the legal action with the highest production-style EV-delta
    score. Ties broken by a small rng jitter (same convention as the
    synthetic rollout) so determinism follows the caller's seeded
    ``rng`` rather than action ordering.

    Signature is intentionally compatible with the
    ``RolloutPolicy`` and ``heuristic_picker`` callable types defined
    in ``ai/search/ismcts.py`` and ``ai/search/ab_compare.py`` — the
    third ``archetype`` parameter is keyword-only at the call site
    (callers pin it via ``functools.partial`` or a closure), so the
    public 2-arg shape is preserved.
    """
    archetype_to_use = _resolve_archetype(state, archetype)
    actions = enumerate_actions(state)
    if not actions:
        return ActionToken(kind="pass", label="pass turn")

    scored: List[Tuple[float, ActionToken]] = []
    for a in actions:
        ev = _score_action_production(state, a, archetype_to_use, rng)
        scored.append((ev, a))

    # Tie-break with a small rng jitter so seed determines tie order
    # rather than enumeration order. Mirrors the convention in
    # ``snapshot_adapter.heuristic_rollout``.
    jitter = rng.random() * 0.001
    scored.sort(key=lambda pair: pair[0] + jitter, reverse=True)
    return scored[0][1]


def make_production_picker(archetype: Optional[str] = None):
    """Factory for an A/B-harness-compatible heuristic picker.

    Returns a ``state -> ActionToken`` callable closing over the
    archetype and an internal rng. The ``ab_compare`` harness expects
    a 1-argument callable; this factory bridges to the 2-arg
    ``production_scorer_picker`` signature.

    Usage in the acceptance test::

        picker = make_production_picker(archetype=fixture.get("archetype"))
        # picker(state) -> ActionToken
    """
    def _picker(state: SearchState) -> ActionToken:
        # Per-call rng so the picker is deterministic given a fresh
        # state (the A/B harness rebuilds state each call).
        rng = random.Random(0)
        return production_scorer_picker(state, rng, archetype=archetype)
    return _picker
