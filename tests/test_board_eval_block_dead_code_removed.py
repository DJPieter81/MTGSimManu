"""Phase 2c (docs/design/rules-foundation-sweep-tracker.md) — regression
guard for the ``ai.board_eval`` block-scoring dead-code removal.

Rule pinned: ``ai.board_eval``'s ``ActionType.BLOCK`` / ``_eval_block`` —
a THIRD, independently-maintained "should I block this attacker with
this blocker" algorithm (CMC-weighted, distinct from both
``ai.ev_player.EVPlayer.decide_blockers``'s real joint-assignment
decision and ``ai.turn_planner.CombatPlanner._predict_blocks``'s
prediction of it) — was investigated per this program's "verify,
don't assume" discipline and found to have ZERO callers anywhere in
``engine/``, ``ai/``, or ``tests/``: nothing anywhere in the tree ever
constructs ``Action(ActionType.BLOCK, ...)``, unlike ``ActionType.
EVOKE``/``DASH``/``COMBO_NOW``, which are real, live, called decision
paths (``engine.game_runner.DefaultCallbacks.should_evoke``/
``should_dash``, and ``ActionType.COMBO_NOW`` respectively).

Rather than building unification machinery for unreachable code (the
whole point of Phase 2c is a SINGLE owner for the joint block-
assignment algorithm — leaving a duplicate, unreachable copy sitting
in source contradicts that even though nothing can ever execute it),
``ActionType.BLOCK`` and ``_eval_block`` were deleted. This test pins
the deletion so a future re-introduction doesn't silently resurrect
the CMC-weighted duplicate without routing through ``ai.block_
assignment`` (the shared primitive both live callers now use) instead.

Card names do not appear — this rule has none; it is about the
enum/dispatch surface of ``ai.board_eval`` itself.
"""
from __future__ import annotations

import ai.board_eval as board_eval


def test_action_type_has_no_block_member():
    """``ActionType`` must not carry a dead ``BLOCK`` member — every
    remaining member (EVOKE, DASH, COMBO_NOW) has a real production
    caller; BLOCK never did."""
    names = {member.name for member in board_eval.ActionType}
    assert "BLOCK" not in names, (
        f"ActionType.BLOCK was re-introduced ({names}) — if a new "
        f"caller genuinely needs a per-pair block-evaluation action, "
        f"it should score via ai.block_assignment's shared primitive "
        f"(the same one ai.ev_player.decide_blockers and ai.turn_"
        f"planner.CombatPlanner._predict_blocks now both use), not a "
        f"reintroduced CMC-weighted duplicate."
    )


def test_eval_block_function_removed():
    """The dead ``_eval_block`` implementation itself must be gone,
    not just unreachable via the enum."""
    assert not hasattr(board_eval, "_eval_block"), (
        "ai.board_eval._eval_block was re-introduced as a duplicate "
        "block-scoring algorithm — route any new per-pair block "
        "evaluation through ai.block_assignment instead."
    )


def test_evaluate_action_dispatch_has_no_block_branch():
    """``evaluate_action``'s dispatch body must not reference a BLOCK
    branch — a structural guard in case a future edit re-adds the
    dispatch arm without re-adding the enum member (or vice versa)."""
    import inspect
    src = inspect.getsource(board_eval.evaluate_action)
    assert "BLOCK" not in src, (
        f"evaluate_action still dispatches on a BLOCK action type — "
        f"source:\n{src}"
    )
