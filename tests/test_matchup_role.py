"""Failing-test contract for `ai.gameplan.matchup_role`.

W0-E (structural-refactor plan, 2026-05-16 audit synthesis):
matchup role is a property of the pair (self_archetype, opp_archetype),
declared as data in `decks/gameplans/_matchup_roles.json`. Replaces the
conditional role-flip logic that the 5-panel Bo3 audit (Decision 4)
flagged across midrange/control/aggro reviews — most notably Dimir
Midrange failing to flip to "clock_opponent" mode vs Storm.

Rule-phrased contract:
  1. midrange vs combo  -> "clock_opponent"  (audit Decision 4)
  2. aggro  vs *        -> "clock_opponent"  (aggro is consistent)
  3. unknown archetype  -> ValueError       (no silent fallback)
  4. JSON schema loads  -> no error         (data must be valid)
  5. every declared role is a member of the "roles" array (schema sanity)
"""
from __future__ import annotations

import json
import pathlib

import pytest


# Path to the data file — checked here so a schema-load failure
# shows up as a clear test failure rather than an import error.
ROLES_JSON = pathlib.Path(__file__).parent.parent / "decks" / "gameplans" / "_matchup_roles.json"


# ──────────────────────────────────────────────────────────────────────
# Rule 1 — audit Decision 4 headline case
# ──────────────────────────────────────────────────────────────────────

def test_midrange_vs_combo_returns_clock_opponent():
    """Midrange against combo must flip to clock_opponent.

    From `docs/history/audits/2026-05-16_5panel_bo3_audit.md` Decision 4:
    Dimir Midrange kept playing `grind_value` vs Storm and lost games
    where a faster, more pressuring line would have won. The table
    declares the structural answer.
    """
    from ai.gameplan import matchup_role

    role = matchup_role("midrange", "combo", snap=None)
    assert role == "clock_opponent"


# ──────────────────────────────────────────────────────────────────────
# Rule 2 — aggro plays the same role regardless of opp
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("opp_archetype", ["combo", "aggro", "control", "midrange"])
def test_aggro_vs_anything_returns_clock_opponent(opp_archetype):
    """Aggro presses the clock against every archetype.

    Aggro is the cleanest case: there is no matchup where aggro should
    be doing something other than racing. This test locks in that the
    table doesn't drift to per-matchup tuning where none is wanted.
    """
    from ai.gameplan import matchup_role

    role = matchup_role("aggro", opp_archetype, snap=None)
    assert role == "clock_opponent"


# ──────────────────────────────────────────────────────────────────────
# Rule 3 — unknown archetype is a structural error, not a silent miss
# ──────────────────────────────────────────────────────────────────────

def test_unknown_archetype_raises_value_error():
    """An unrecognised archetype must raise ValueError.

    The structural contract is that archetype strings are declared in the
    JSON. A caller passing a typo or an unrecognised archetype is a bug
    we want to surface, not paper over with a default role.
    """
    from ai.gameplan import matchup_role

    with pytest.raises(ValueError):
        matchup_role("not_a_real_archetype", "combo", snap=None)

    with pytest.raises(ValueError):
        matchup_role("midrange", "not_a_real_archetype", snap=None)


# ──────────────────────────────────────────────────────────────────────
# Rule 4 — the JSON schema must load without error
# ──────────────────────────────────────────────────────────────────────

def test_schema_loads_without_error():
    """The matchup_roles JSON file must parse and have the expected top-level keys."""
    assert ROLES_JSON.exists(), f"missing data file: {ROLES_JSON}"
    data = json.loads(ROLES_JSON.read_text())
    assert "schema_version" in data
    assert "roles" in data
    assert "matchups" in data
    assert isinstance(data["roles"], list) and len(data["roles"]) > 0
    assert isinstance(data["matchups"], dict) and len(data["matchups"]) > 0


# ──────────────────────────────────────────────────────────────────────
# Rule 5 — every declared role string is in the roles array
# ──────────────────────────────────────────────────────────────────────

def test_all_declared_roles_are_valid_strings():
    """Every cell in matchups[self][opp]["role"] must be a member of roles[].

    Without this check, a typo in any cell (e.g., "clock_oponent") would
    flow through to the AI silently. The roles array is the schema; the
    cells must conform.
    """
    data = json.loads(ROLES_JSON.read_text())
    valid_roles = set(data["roles"])
    for self_arch, opp_table in data["matchups"].items():
        for opp_arch, entry in opp_table.items():
            assert isinstance(entry, dict), (
                f"matchups[{self_arch}][{opp_arch}] must be a dict, got {type(entry)}"
            )
            assert "role" in entry, (
                f"matchups[{self_arch}][{opp_arch}] missing 'role' key"
            )
            role = entry["role"]
            assert role in valid_roles, (
                f"matchups[{self_arch}][{opp_arch}].role = {role!r} "
                f"not in declared roles {sorted(valid_roles)}"
            )
