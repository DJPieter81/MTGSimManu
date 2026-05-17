"""Failing-test contract for M13: midrange role-flip vs combo opponents.

5-panel Bo3 audit Decision 4 (`docs/history/audits/2026-05-16_5panel_bo3_audit.md`):
Dimir Midrange defaults to `grind_value` against combo opponents (Ruby Storm)
when it should `clock_opponent`. The decision was originally encoded as a
conditional in the AI (or was simply missing); M13 wires `GoalEngine` to
consult the matchup-role table introduced by W0-E.

Rule-phrased contract:
  1. midrange vs combo  -> "clock_opponent"  (audit Decision 4 — Dimir vs Storm)
  2. midrange vs aggro  -> "stabilise"       (per _matchup_roles.json)
  3. aggro vs *         -> "clock_opponent"  (every aggro matchup, table-locked)
  4. no inline archetype-pair conditionals remain in `current_role`/`current_goal`
     (the role is data, not code)

These tests assert behaviour at the **GoalEngine layer** because the role flip
must be observable at the level the EV player consumes (one call away from
the role being applied to play scoring).
"""
from __future__ import annotations

import inspect
import pathlib
import re

import pytest


# ──────────────────────────────────────────────────────────────────────
# Rule 1 — audit Decision 4 headline case
# ──────────────────────────────────────────────────────────────────────

def test_dimir_vs_storm_returns_clock_opponent():
    """Dimir Midrange against Ruby Storm must flip role to clock_opponent.

    The audit Decision 4 documents the exact failure: Dimir kept playing
    grind_value vs Storm, which loses to the faster clock. The table cell
    (midrange, combo) -> clock_opponent is the structural fix.
    """
    from ai.gameplan import create_goal_engine

    dimir = create_goal_engine("Dimir Midrange")
    assert dimir is not None, "Dimir Midrange must have a registered gameplan"

    # mid-game snap parameter is reserved (per matchup_role signature); not
    # required for the role lookup itself. Storm archetype string is the
    # source of truth — we don't need a live snapshot.
    role = dimir.current_role(opp_archetype="combo")
    assert role == "clock_opponent", (
        f"midrange vs combo must be clock_opponent (audit Decision 4); "
        f"got {role!r}"
    )


# ──────────────────────────────────────────────────────────────────────
# Rule 2 — midrange vs aggro is stabilise (per the JSON table)
# ──────────────────────────────────────────────────────────────────────

def test_dimir_vs_aggro_returns_stabilise():
    """Midrange against aggro plays stabilise per `_matchup_roles.json`.

    Aggro is on a faster clock than midrange; survive and trade resources
    before grinding. This is the inverse of the combo case: against combo
    midrange must race, against aggro midrange must defend.
    """
    from ai.gameplan import create_goal_engine

    dimir = create_goal_engine("Dimir Midrange")
    assert dimir is not None

    role = dimir.current_role(opp_archetype="aggro")
    assert role == "stabilise", (
        f"midrange vs aggro must be stabilise (table); got {role!r}"
    )


# ──────────────────────────────────────────────────────────────────────
# Rule 3 — aggro plays clock_opponent against every archetype
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("opp_archetype", [
    "combo", "aggro", "control", "midrange", "tempo", "ramp",
])
def test_aggro_vs_anything_returns_clock_opponent(opp_archetype):
    """Aggro presses the clock against every archetype.

    Every aggro matchup follows the table — there is no matchup where aggro
    should be doing something other than racing. Locks down that the
    table doesn't silently drift to per-matchup tuning where none is wanted.
    """
    from ai.gameplan import create_goal_engine

    boros = create_goal_engine("Boros Energy")
    assert boros is not None, "Boros Energy must have a registered gameplan"
    assert boros.gameplan.archetype == "aggro"

    role = boros.current_role(opp_archetype=opp_archetype)
    assert role == "clock_opponent", (
        f"aggro vs {opp_archetype} must be clock_opponent (table); "
        f"got {role!r}"
    )


# ──────────────────────────────────────────────────────────────────────
# Rule 4 — no inline archetype-pair conditionals remain in current_role
#           or in current_goal. The role is data, not code.
# ──────────────────────────────────────────────────────────────────────

def test_no_inline_archetype_conditionals_remain_in_current_goal():
    """Source-level guard: neither `current_role` nor `current_goal` may
    contain inline (self_archetype, opp_archetype) if-chains.

    The pair-keyed role is data in `decks/gameplans/_matchup_roles.json`
    and must be looked up via `matchup_role(...)`. Any future regression
    to inline conditionals — e.g., adding ``if archetype == 'midrange'
    and opp_arch == 'combo': ...`` — re-creates the structural debt this
    migration removed and is blocked here.
    """
    from ai.gameplan import GoalEngine

    # The role-lookup method must delegate to matchup_role; the goal
    # selector must not re-encode pair-keyed decisions inline.
    role_src = inspect.getsource(GoalEngine.current_role)
    goal_src = inspect.getsource(GoalEngine.current_goal.fget)

    # Pattern: any `if self.<attr> == "<archetype-string>" and opp` chain.
    # We look for the conjunction of an archetype string and an `opp`
    # reference on the same line — that's the structural shape of the
    # if-chain we're forbidding.
    bad_pattern = re.compile(
        r"if\s+.*archetype\s*==\s*[\"'](?:midrange|aggro|control|combo|tempo|ramp)[\"'].*opp",
        re.IGNORECASE,
    )
    for src, name in [(role_src, "current_role"), (goal_src, "current_goal")]:
        assert not bad_pattern.search(src), (
            f"{name} contains an inline archetype-pair conditional; "
            f"move it to decks/gameplans/_matchup_roles.json instead."
        )

    # Additional check: current_role must call matchup_role(...) — the
    # delegation is the entire point of the method.
    assert "matchup_role(" in role_src, (
        "current_role must delegate to matchup_role(...) — that's the "
        "table-lookup migration M13 introduced."
    )
