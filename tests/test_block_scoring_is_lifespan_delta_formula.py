"""M12-AI — block scoring is a single lifespan-delta formula.

Wave-1b W1b-12.  M12-engine (merged) refactored
``ai.clock.score_block_assignment`` into a single buffer-differential
formula and wired the *selection* path in ``ev_player.decide_blockers``
through it.  This module pins the AI-SIDE contract that the residual
chump / trade / favorable-trade enum has been stripped from
``ai/ev_player.py``.

Rules pinned (no card names, no deck gates, no magic numbers):

  1. The block scorer returns a single numeric — never a string label
     or enum member.
  2. At PANIC life-phase with a chumpable token, the lifespan-delta
     score for the chump block is strictly positive (chump extends
     lifespan).
  3. When a trade and a chump have equal life-saving effect, the
     trade scores strictly higher because it leaves opp_power lower
     (longer my-buffer next turn).
  4. The grep guardrail: the string-literal labels ``"chump block"``,
     ``"trade (chump)"``, and ``"favorable trade"`` have been deleted
     from ``ai/ev_player.py``.  If they reappear they are gating a
     decision branch the contract bans.

These rules describe the *mechanic* (block selection by buffer
differential), not any specific card.  Every deck that ever blocks —
Dimir, Jeskai Blink, 4c Omnath, Living End, Goryo's, etc. — is the
lift-check.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────
# Rule 1 — single numeric return (no enum)
# ─────────────────────────────────────────────────────────────


def test_block_returns_lifespan_delta_not_enum():
    """``score_block_assignment`` must return a float — never a
    ``str`` / ``Enum`` / ``tuple``.  Pins the single-formula contract
    end-to-end: any caller that wants to rank options does so by
    comparing numbers, not by branching on a label.
    """
    from ai.clock import score_block_assignment
    from ai.ev_evaluator import EVSnapshot

    snap = EVSnapshot(
        my_life=10, opp_life=20,
        my_power=2, opp_power=3,
        my_creature_count=1, opp_creature_count=1,
        turn_number=4,
    )
    out = score_block_assignment(
        snap,
        my_life_after=10,
        opp_power_after=3,
        my_power_after=2,
    )
    # Single-numeric contract: float (or int) only.  No tuples, no
    # strings, no enum members.
    assert isinstance(out, (int, float)), (
        f"score_block_assignment must return a number, got "
        f"{type(out).__name__} = {out!r}"
    )
    assert not isinstance(out, bool), (
        "bool is a subclass of int — reject it explicitly; the formula "
        "is a continuous score, not a boolean"
    )


# ─────────────────────────────────────────────────────────────
# Rule 2 — chump at PANIC scores positive (extends lifespan)
# ─────────────────────────────────────────────────────────────


def test_chump_at_panic_scores_positive():
    """Life 4, attacker 5/5, 1/1 token defender.

    Post-chump-block state vs post-no-block state:

      - Block: my_life=4 (chump ate it), opp_power=5 (attacker
        survives), my_power=0 (chump died).  Buffer ≈ life_as_resource
        (4, 5) > 0.
      - No block: my_life=-1 (dead), opp_power=5, my_power=1.  Buffer
        ≈ life_as_resource(-1, 5) == -100 (the dead-sentinel).

    The chump option's buffer differential strictly dominates no-block.
    Pinned without naming a card: PANIC + chumpable creature ⇒
    chump_score - no_block_score > 0.
    """
    from ai.clock import score_block_assignment
    from ai.ev_evaluator import EVSnapshot

    snap = EVSnapshot(
        my_life=4, opp_life=20,
        my_power=1, opp_power=5,
        my_creature_count=1, opp_creature_count=1,
        turn_number=5,
    )
    chump_score = score_block_assignment(
        snap,
        my_life_after=4,
        opp_power_after=5,
        my_power_after=0,
    )
    no_block_score = score_block_assignment(
        snap,
        my_life_after=-1,
        opp_power_after=5,
        my_power_after=1,
    )
    delta = chump_score - no_block_score
    assert delta > 0, (
        f"PANIC + chumpable token must extend lifespan: chump="
        f"{chump_score:.3f}, no_block={no_block_score:.3f}, "
        f"delta={delta:.3f} (must be > 0).  life_as_resource(-1, 5) "
        f"is the dead-sentinel; chump moves us into a positive buffer."
    )


# ─────────────────────────────────────────────────────────────
# Rule 3 — trade beats chump when life-saving effect ties
# ─────────────────────────────────────────────────────────────


def test_trade_preferred_over_chump_when_lifespan_equal():
    """Defender has 3/3 (deathtouch) + 1/1, attacker is a 5/5.

      - Trade (deathtouch 3/3 blocks): both die.  opp_power_after =
        opp_power - 5 (the 5/5 is gone), my_power_after = my_power -
        3 (trader died).
      - Chump (1/1 blocks): only the chump dies.  opp_power_after =
        opp_power (5/5 survives, can swing again next turn);
        my_power_after = my_power - 1.

    Both choices save the same 5 face damage.  The trade clears the
    attacker for good; the chump leaves it on the board.  In the
    ``life / opp_power`` regime of ``life_as_resource``, the trade's
    lower opp_power_after gives me a longer next-turn buffer — the
    formula must prefer it.

    Scenario uses a large defending board so the my_power_after
    deltas don't dominate (otherwise losing my best blocker tips the
    opp-buffer arm of the differential).  The principle being pinned
    is "trade beats chump on equal face-damage saved when board
    economy is otherwise balanced," which the formula's buffer
    differential should derive without any chump/trade enum.
    """
    from ai.clock import score_block_assignment
    from ai.ev_evaluator import EVSnapshot

    # Wide defending board (lots of small attackers in reserve) so
    # the post-block opp_buffer term isn't dominated by a single
    # blocker's loss.  my_power=20, opp_power=5 (single 5/5 attacker).
    snap = EVSnapshot(
        my_life=8, opp_life=20,
        my_power=20, opp_power=5,
        my_creature_count=4, opp_creature_count=1,
        turn_number=5,
    )
    # Trade — 3/3 deathtouch blocks 5/5; both die.
    trade_score = score_block_assignment(
        snap,
        my_life_after=8,
        opp_power_after=0,   # 5/5 attacker gone
        my_power_after=17,   # 20 - 3 (the trader died)
    )
    # Chump — 1/1 blocks 5/5; only chump dies.  5/5 still around.
    chump_score = score_block_assignment(
        snap,
        my_life_after=8,
        opp_power_after=5,   # 5/5 still on board
        my_power_after=19,   # 20 - 1 (the chump died)
    )
    assert trade_score > chump_score, (
        f"Equal life saved, but trade clears opp_power so my next-turn "
        f"buffer is longer: trade={trade_score:.3f}, "
        f"chump={chump_score:.3f}.  The formula must prefer the trade."
    )


# ─────────────────────────────────────────────────────────────
# Rule 4 — string-literal enum has been deleted from ev_player.py
# ─────────────────────────────────────────────────────────────


def test_no_chump_trade_favorable_enum_remains():
    """Grep guardrail: the string-literal block-reason labels are not
    present in ``ai/ev_player.py``.  The reasons existed only to gate
    block decisions through an if-chain; the lifespan-delta formula
    has subsumed every branch.

    Any reintroduction is a regression to the enum-of-reasons pattern
    that this refactor deleted.
    """
    src = (Path(__file__).resolve().parents[1]
           / "ai" / "ev_player.py").read_text()

    # Exact string-literal labels that lived in the block-log lines.
    banned = [
        '"favorable trade"',
        "'favorable trade'",
        '"trade (chump)"',
        "'trade (chump)'",
        '"chump block"',
        "'chump block'",
    ]
    found = [lit for lit in banned if lit in src]
    assert not found, (
        f"Block-reason enum strings must not appear in ai/ev_player.py "
        f"(found: {found}).  The single-formula refactor (M12-AI) "
        f"removed the chump/trade/favorable-trade if-chain; any new "
        f"occurrence is a regression to the enum-of-reasons pattern."
    )
