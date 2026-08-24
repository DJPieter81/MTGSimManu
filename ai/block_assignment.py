"""
Shared joint block-assignment algorithm.

Phase 2c (docs/design/rules-foundation-sweep-tracker.md): Phase 2b
built a two-pass joint block-assignment algorithm INSIDE
``ai.ev_player.EVPlayer.decide_blockers`` to fix audited bug #4 (two
jointly-lethal attackers, only one gets blocked). Two OTHER call
sites independently re-implemented "given these attackers and these
potential blockers, what's the joint-optimal assignment" with their
own heuristics:

  - ``ai.turn_planner.CombatPlanner._predict_blocks`` — predicts how
    the OPPONENT will block MY attacks, consumed by turn-planning EV
    comparisons (``plan_attack``'s per-config scoring).
  - ``ai.board_eval.py``'s ``ActionType.BLOCK`` / ``_eval_block`` —
    investigated and found to have ZERO callers anywhere in
    ``engine/``, ``ai/``, or ``tests/`` (confirmed by grep across the
    whole tree before touching anything, per this program's "verify,
    don't assume" discipline). Dead code, not unified here — see the
    tracker doc's Phase 2c write-up for the full investigation.

This module extracts the CORE two-pass algorithm out of
``decide_blockers`` into a pure, side-effect-free pair of functions so
every LIVE caller that needs a joint block assignment uses the SAME
algorithm, whether it's committing a real block (``decide_blockers``)
or projecting a hypothetical one (``CombatPlanner._predict_blocks``).

Neither function here touches a ``GameState``, logs anything, or
knows about ``CardInstance`` vs ``VirtualCreature`` — every mechanic-
specific judgment (legality, cost ranking, pairwise scoring, soft
protected-piece preference) is injected by the caller as a callable,
so a real block decision (real cost via ``ai.clock.opportunity_cost``,
real oracle-driven protected-piece preference) and a lightweight
turn-planning projection (a pre-computed ``.value``, no oracle access)
can both drive the identical assignment shape.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

# Duck-typed: any object exposing .instance_id / .power (int or None).
Blockable = Any


def coverage_pass(
    sorted_attackers: Sequence[Blockable],
    blockers: Sequence[Blockable],
    *,
    my_life: float,
    can_block_fn: Callable[[Blockable, Blockable], bool],
    cost_fn: Callable[[Blockable], float],
    stabilize_margin: float = 0.0,
    skip_fn: Optional[Callable[[Blockable, float], bool]] = None,
    min_blockers_fn: Optional[Callable[[Blockable], int]] = None,
    overflow_fn: Optional[Callable[[Blockable, List[Blockable]], float]] = None,
) -> Tuple[Dict[int, List[int]], Set[int]]:
    """PASS 1 — coverage.

    Force-block attackers (``sorted_attackers``, caller-sorted biggest
    power first) with the CHEAPEST available blocker (``cost_fn``
    ascending) until the JOINT remaining damage from attackers NOT yet
    covered is survivable. ``unblocked_damage`` is recomputed each
    iteration directly from the actual current coverage set, never
    guessed per-attacker — this is the audited bug #4 fix
    (``ai.ev_player.EVPlayer.decide_blockers``'s original docstring
    has the full failure-mode writeup) generalized to any caller.

    ``skip_fn(attacker, unblocked_damage) -> bool``, if given, is
    consulted right after computing ``unblocked_damage`` for the
    current attacker and BEFORE picking a candidate — lets a caller
    skip force-covering a specific attacker for a reason outside this
    algorithm's own model (``decide_blockers``'s RC-2 "equipment
    rebinds, chumping is futile" gate) while still contributing that
    attacker's damage to every LATER iteration's ``unblocked_damage``.

    ``overflow_fn(attacker, chosen_blockers) -> float``, if given, is
    the trample-overflow (CR 702.19) accounting: a *blocked* attacker
    still contributes its through-damage to ``unblocked_damage`` (power
    beyond its blockers' combined toughness), instead of dropping out of
    the survival sum entirely. Without it, a blocked trampler counts as
    zero incoming — the pass stabilizes one attacker too early and the
    defender dies to overflow it never accounted for. Non-trample
    blocked attackers return 0 and so still drop out, unchanged.

    Returns ``(blocks, used)`` — a fresh dict/set, never mutates the
    inputs.
    """
    blocks: Dict[int, List[int]] = {}
    used: Set[int] = set()
    id_to_blocker = {b.instance_id: b for b in blockers}

    def _incoming() -> float:
        total = 0.0
        for a in sorted_attackers:
            if a.instance_id not in blocks:
                total += (a.power or 0)
            elif overflow_fn is not None:
                chosen = [
                    id_to_blocker[bid] for bid in blocks[a.instance_id]
                    if bid in id_to_blocker
                ]
                total += overflow_fn(a, chosen)
        return total

    for attacker in sorted_attackers:
        unblocked_damage = _incoming()
        still_lethal = unblocked_damage >= my_life
        if not still_lethal and (
                my_life - unblocked_damage > stabilize_margin
                or unblocked_damage == 0):
            break  # already safe — no further forced coverage needed

        if skip_fn is not None and skip_fn(attacker, unblocked_damage):
            continue

        candidates = [
            b for b in blockers
            if b.instance_id not in used and can_block_fn(attacker, b)
        ]
        # Minimum legal blockers for this attacker (menace ⇒ 2, CR 509.1c).
        # If we can't field that many, do NOT partially block — a single
        # blocker on a menace attacker is an illegal declaration the engine
        # drops in its entirety, so the "block" would evaporate and the
        # attacker would connect for full damage. Leave it uncovered instead.
        need = min_blockers_fn(attacker) if min_blockers_fn else 1
        if len(candidates) < need:
            continue  # can't legally cover it — forced to eat it

        chosen = sorted(candidates, key=cost_fn)[:need]
        blocks[attacker.instance_id] = [c.instance_id for c in chosen]
        for c in chosen:
            used.add(c.instance_id)

    return blocks, used


def optimize_pass(
    sorted_attackers: Sequence[Blockable],
    blockers: Sequence[Blockable],
    blocks: Dict[int, List[int]],
    used: Set[int],
    *,
    can_block_fn: Callable[[Blockable, Blockable], bool],
    score_fn: Callable[[Blockable, Blockable], float],
    bounded_to_covered: bool,
    protected_fn: Optional[Callable[[Blockable], bool]] = None,
    on_assigned: Optional[
        Callable[[Blockable, List[int], Set[int]], None]
    ] = None,
) -> Tuple[Dict[int, List[int]], Set[int]]:
    """PASS 2 — optimization.

    For each ELIGIBLE attacker, pick the single blocker that scores
    the best pairwise ``score_fn`` (a lifespan-delta-shaped formula;
    positive means the block helps) — swapping in a strictly better
    option than whatever's currently assigned, or assigning a fresh
    one if none is assigned yet.

    ``bounded_to_covered=True`` (``decide_blockers``'s emergency mode):
    only attackers PASS 1 already committed a blocker to are
    reconsidered, and only for a SWAP — never new coverage. Verified
    by this program's own precedent (``tests/
    test_decide_blockers_emergency_gate.py``) that extending this to
    attackers PASS 1 left deliberately unblocked breaks the portfolio-
    cap/stabilize behaviour.

    ``bounded_to_covered=False`` (``decide_blockers``'s non-emergency
    mode, and ``CombatPlanner._predict_blocks``'s "consider a
    profitable trade for whatever coverage left uncommitted" phase):
    every attacker NOT already in ``blocks`` is freely optimized
    against a neutral 0.0 threshold (no block beats a non-positive
    delta).

    ``protected_fn``, if given, is a SOFT preference (candidates
    failing it are tried first; fall back to the full pool only if
    none remain) — never a hard exclusion.

    ``on_assigned(attacker, blocker_id_list, used)``, if given, fires
    immediately after a FRESH (non-bounded) assignment is made,
    passing the live, mutable ``blocker_id_list`` (== ``blocks[attacker
    .instance_id]``) and ``used`` set so a caller can extend the
    assignment in place (``decide_blockers``'s non-emergency double-
    block-if-needed step) with the SAME live bookkeeping the next
    attacker's candidate pool will see — preserving decide_blockers's
    original per-attacker interleaving exactly.

    Mutates ``blocks``/``used`` in place and also returns them.
    """
    id_to_blocker = {b.instance_id: b for b in blockers}

    def _candidate_pool(attacker, excl):
        cands = [
            b for b in blockers
            if b.instance_id not in excl and can_block_fn(attacker, b)
        ]
        if protected_fn is None:
            return cands
        unprotected = [b for b in cands if not protected_fn(b)]
        return unprotected if unprotected else cands

    for attacker in sorted_attackers:
        already_covered = attacker.instance_id in blocks
        if bounded_to_covered and not already_covered:
            continue
        if not bounded_to_covered and already_covered:
            continue

        if already_covered:
            current_id = blocks[attacker.instance_id][0]
            current_blocker = id_to_blocker[current_id]
            pool = _candidate_pool(attacker, used - {current_id})
            if not any(b.instance_id == current_id for b in pool):
                pool = pool + [current_blocker]
            best_blocker = current_blocker
            best_val = score_fn(attacker, current_blocker)
            skip_id = current_id
        else:
            pool = _candidate_pool(attacker, used)
            best_blocker = None
            best_val = 0.0
            skip_id = None

        for cand in pool:
            if cand.instance_id == skip_id:
                continue
            val = score_fn(attacker, cand)
            if val > best_val:
                best_val = val
                best_blocker = cand

        if best_blocker is None:
            continue

        if already_covered:
            current_id = blocks[attacker.instance_id][0]
            if best_blocker.instance_id != current_id:
                used.discard(current_id)
                used.add(best_blocker.instance_id)
                blocks[attacker.instance_id] = [best_blocker.instance_id]
        else:
            blocks[attacker.instance_id] = [best_blocker.instance_id]
            used.add(best_blocker.instance_id)
            if on_assigned is not None:
                on_assigned(attacker, blocks[attacker.instance_id], used)

    return blocks, used
