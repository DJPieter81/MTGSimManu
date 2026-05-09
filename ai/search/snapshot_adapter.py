"""EVSnapshot adapter for ISMCTS — Phase 4A Week 2.

The Week-1 ISMCTS skeleton (``ai/search/ismcts.py``) is engine-
agnostic: it takes four callables (action enumerator, rollout
policy, terminal evaluator, transition) and runs UCT on whatever
state the caller wires up. Week 2 plugs in EVSnapshot.

Why EVSnapshot, not GameState?
------------------------------
- EVSnapshot is a typed pydantic model with ``fast_replace`` for
  ~3.5 µs cloning. Cloning full GameState would dominate rollout
  cost (orders of magnitude slower).
- All EV-relevant fields are already captured: life, power,
  toughness, creature count, hand size, mana availability,
  artifact count, graveyard size, storm count, ...
- ``ai.clock.position_value`` already evaluates a snapshot to a
  single scalar — that's the terminal evaluator.
- Snapshot deltas (cast a Bolt, deploy a creature, equip a
  Plating) can be encoded as ``EVSnapshot.fast_replace(...)``
  calls without ever touching a CardInstance.

Limitations of the Week-2 deliverable
-------------------------------------
- The action token doesn't fire ETB / triggered abilities
  beyond what EVSnapshot's count fields capture.
- The transition is deterministic (no stochastic opponent
  draws). Determinization happens at the search-level via
  ``bhi`` sampling (Week 3).
- Rollout policy is simplified — picks the action with the
  largest immediate position-value delta (greedy 1-ply heuristic).

These limitations are acceptable for the Week-2 demo target:
ISMCTS converges to the action with the highest 4-turn
projected position-value delta on synthetic snapshots. Week 3
upgrades the rollout to use the full heuristic scorer and adds
determinization sampling.

Reference: docs/research/2026-05_phase_4a_ismcts_scoping.md
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ai.ev_evaluator import EVSnapshot
from ai.clock import position_value


# ─────────────────────────────────────────────────────────────
# Action token — engine-agnostic representation
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ActionToken:
    """A discrete action representable as snapshot deltas.

    Attributes:
      kind: action category. One of:
        - "cast_creature"   — adds my_power / my_toughness / count
        - "cast_artifact"   — adds my_artifact_count, optionally mana
        - "play_land"       — adds my_mana, my_total_lands
        - "burn"            — subtracts opp_life
        - "draw"            — increments my_hand_size
        - "pass"            — no delta (turn passes)
      label: human-readable description (used in logs / tests).
      delta: dict of EVSnapshot field names → numeric delta. The
        adapter applies via ``snap.fast_replace(field=snap.field+delta)``.
      cost: mana required to play this action (paid from
        ``my_mana``). Lands and free spells have cost 0.
    """

    kind: str
    label: str
    delta: dict = field(default_factory=dict, hash=False, compare=False)
    cost: int = 0


# ─────────────────────────────────────────────────────────────
# Search state — snapshot + per-turn play log
# ─────────────────────────────────────────────────────────────


@dataclass
class SearchState:
    """Wraps an EVSnapshot with a turn-step counter and a list of
    actions already taken this turn (for "limit one land per turn"
    and similar constraints).

    fast_replace is used for cheap cloning during search; the
    plays_this_turn list is recreated fresh on each clone (small,
    fast).
    """

    snapshot: EVSnapshot
    plays_this_turn: List[ActionToken] = field(default_factory=list)
    available: List[ActionToken] = field(default_factory=list)
    """Actions still available this turn — drawn from the hand
    + battlefield plus the always-present 'pass' action."""

    def clone(self) -> "SearchState":
        return SearchState(
            snapshot=self.snapshot.fast_replace(),
            plays_this_turn=list(self.plays_this_turn),
            available=list(self.available),
        )


# ─────────────────────────────────────────────────────────────
# Callables: enumerate / rollout / terminal / transition
# ─────────────────────────────────────────────────────────────


def enumerate_actions(state: SearchState) -> List[ActionToken]:
    """Return all legal actions from this state.

    Constraints applied:
      - lands limited to one per turn
      - actions whose cost > available mana are filtered out
      - "pass" is always available (terminates the turn)

    The "pass" token has kind="pass". Once selected during the
    rollout, the turn-step counter advances; the search treats
    deeper plies as "next turn" decisions.
    """
    snap = state.snapshot
    legal: List[ActionToken] = []

    lands_played = sum(
        1 for a in state.plays_this_turn if a.kind == "play_land"
    )

    for action in state.available:
        if action.kind == "play_land" and lands_played >= 1:
            continue
        if action.cost > snap.my_mana:
            continue
        legal.append(action)

    legal.append(ActionToken(kind="pass", label="pass turn"))
    return legal


def apply_action(
    state: SearchState, action: ActionToken, rng: random.Random,
) -> SearchState:
    """Apply ``action`` to ``state``. Returns a new SearchState.

    For "pass", the turn ends — opp_life ticks down by my_power
    (combat damage), my_mana resets, turn_number increments. This
    is a coarse simulation of an end-of-turn rollover; the full
    engine has untap, upkeep, draw, end-step triggers, etc., but
    those don't affect the search-relevant fields enough to
    justify the complexity in a Week-2 deliverable.

    For non-pass actions, the snapshot delta is applied via
    fast_replace and the action is added to plays_this_turn.
    """
    snap = state.snapshot
    if action.kind == "pass":
        # End-of-turn: deal combat damage, reset mana, advance turn.
        new_opp_life = max(0, snap.opp_life - snap.my_power)
        new_snap = snap.fast_replace(
            opp_life=new_opp_life,
            my_mana=snap.my_total_lands,
            turn_number=snap.turn_number + 1,
        )
        return SearchState(
            snapshot=new_snap,
            plays_this_turn=[],
            available=list(state.available),
        )

    # Non-pass: apply delta to the snapshot, deduct cost, log
    # the play.
    updates: dict = {}
    for field_name, delta in action.delta.items():
        cur = getattr(snap, field_name, 0)
        updates[field_name] = cur + delta
    if action.cost > 0:
        updates["my_mana"] = snap.my_mana - action.cost

    new_snap = snap.fast_replace(**updates)
    new_plays = list(state.plays_this_turn) + [action]
    # Remove the action from `available` if it was a one-shot
    # (cards in hand are consumed when cast).
    new_available = [a for a in state.available if a is not action]
    return SearchState(
        snapshot=new_snap,
        plays_this_turn=new_plays,
        available=new_available,
    )


def evaluate_terminal(state: SearchState) -> float:
    """Map a SearchState to a [0, 1] reward.

    Uses ``ai.clock.position_value`` as the underlying scorer, then
    squashes via a sigmoid to fit the [0, 1] convention UCB1
    expects. Win = 1.0 (opp_life <= 0). Loss = 0.0 (my_life <= 0).
    """
    snap = state.snapshot
    if snap.opp_life <= 0:
        return 1.0
    if snap.my_life <= 0:
        return 0.0
    pv = position_value(snap)
    # Sigmoid: pv ~ [-100, 100] → [0, 1]. Scale by 25 so the
    # natural game-end positions saturate (a +25-position-value
    # advantage already maps to ~0.73).
    import math
    return 1.0 / (1.0 + math.exp(-pv / 25.0))


def heuristic_rollout(state: SearchState, rng: random.Random) -> ActionToken:
    """Rollout policy: pick the action that maximizes immediate
    position-value delta. Ties broken randomly.

    For "pass" comparison: ending the turn yields combat damage
    via the apply_action transition; we score that hypothetical
    transition via position_value(post-pass snapshot).
    """
    actions = enumerate_actions(state)
    if not actions:
        return ActionToken(kind="pass", label="pass turn")

    scored: List[Tuple[float, ActionToken]] = []
    for a in actions:
        next_state = apply_action(state, a, rng)
        scored.append((position_value(next_state.snapshot), a))

    # Add small jitter so ties don't always pick the first.
    rng_jitter = rng.random() * 0.001
    scored.sort(key=lambda x: x[0] + rng_jitter, reverse=True)
    return scored[0][1]


# ─────────────────────────────────────────────────────────────
# Helpers for tests — synthesize action tokens from a list of
# (kind, label, delta, cost) tuples
# ─────────────────────────────────────────────────────────────


def make_search_state(
    snapshot: EVSnapshot,
    available_actions: Optional[List[ActionToken]] = None,
) -> SearchState:
    """Convenience constructor for tests: wrap a snapshot with an
    optional pre-built action list."""
    return SearchState(
        snapshot=snapshot,
        plays_this_turn=[],
        available=list(available_actions or []),
    )
