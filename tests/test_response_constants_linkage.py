"""Link/smoke test: ai/response.py uses the same constants advertised
in ai/scoring_constants.py.

When `ai/response.py` was first refactored, several inline literals
(``1.5`` / ``0.5`` gate multipliers, ``20.0`` clock→life scaling,
``3.0`` proactive-removal floor, ``3`` equipment residency, ``2``
default equipment power, ``2`` cheap-trade thresholds, ``1.0``
held-counter floor) were duplicated between this module and the
constant table.  Centralising them in `ai/scoring_constants.py`
removed the duplication; this test asserts:

1. Each rule-encoding constant exists in `ai.scoring_constants`.
2. Its value matches the expected rule (the value it had as a literal
   in `ai/response.py`'s pre-refactor code path).

If a future re-tune changes a value in scoring_constants.py without a
matching update to the rule it encodes, this test names the rule that
broke.  If the literal is reintroduced in response.py, the value
mismatch in this test (or the abstraction-baseline ratchet) will
surface it.
"""
from __future__ import annotations

import inspect

import pytest

from ai import response, scoring_constants


# ─── Constants advertised in scoring_constants.py ────────────────────


def test_clock_impact_life_scaling_value():
    """``CLOCK_IMPACT_LIFE_SCALING`` is 20.0 — converts clock-impact
    (cards/turn / opp_life) into life-point units."""
    assert scoring_constants.CLOCK_IMPACT_LIFE_SCALING == 20.0


def test_held_counter_floor_min_ev_value():
    """``HELD_COUNTER_FLOOR_MIN_EV`` is 1.0 — minimum held-counter EV
    so an empty snapshot doesn't collapse the gate to zero."""
    assert scoring_constants.HELD_COUNTER_FLOOR_MIN_EV == 1.0


def test_counter_gate_high_multiplier_value():
    """``COUNTER_GATE_HIGH_MULTIPLIER`` is 1.5 — threat must clear
    floor EV by 50%+ to fire a non-cheap counter."""
    assert scoring_constants.COUNTER_GATE_HIGH_MULTIPLIER == 1.5


def test_counter_gate_low_multiplier_value():
    """``COUNTER_GATE_LOW_MULTIPLIER`` is 0.5 — cheap-trade scenarios
    drop the gate to half the floor EV."""
    assert scoring_constants.COUNTER_GATE_LOW_MULTIPLIER == 0.5


def test_cheap_counter_paid_threshold_value():
    """``CHEAP_COUNTER_PAID_THRESHOLD`` is 2 — counters at effective
    cost ≤ 2 qualify as cheap-trade for the LOW gate."""
    assert scoring_constants.CHEAP_COUNTER_PAID_THRESHOLD == 2


def test_cheap_threat_paid_threshold_value():
    """``CHEAP_THREAT_PAID_THRESHOLD`` is 2 — opp spells whose effective
    paid cost ≤ 2 qualify the LOW gate from the threat side."""
    assert scoring_constants.CHEAP_THREAT_PAID_THRESHOLD == 2


def test_proactive_removal_min_value_value():
    """``PROACTIVE_REMOVAL_MIN_VALUE`` is 3.0 — "worth a card" floor
    for reactive instant-speed removal."""
    assert scoring_constants.PROACTIVE_REMOVAL_MIN_VALUE == 3.0


def test_equipment_residency_turns_value():
    """``EQUIPMENT_RESIDENCY_TURNS`` is 3 — typical equipment residency
    window in Modern."""
    assert scoring_constants.EQUIPMENT_RESIDENCY_TURNS == 3


def test_equipment_default_power_bonus_value():
    """``EQUIPMENT_DEFAULT_POWER_BONUS`` is 2 — default +P/+T pump
    when oracle text doesn't specify."""
    assert scoring_constants.EQUIPMENT_DEFAULT_POWER_BONUS == 2


# ─── Linkage: ai/response.py imports the constants ───────────────────


REQUIRED_IMPORTS = (
    "CLOCK_IMPACT_LIFE_SCALING",
    "HELD_COUNTER_FLOOR_MIN_EV",
    "COUNTER_GATE_HIGH_MULTIPLIER",
    "COUNTER_GATE_LOW_MULTIPLIER",
    "CHEAP_COUNTER_PAID_THRESHOLD",
    "CHEAP_THREAT_PAID_THRESHOLD",
    "PROACTIVE_REMOVAL_MIN_VALUE",
    "EQUIPMENT_RESIDENCY_TURNS",
    "EQUIPMENT_DEFAULT_POWER_BONUS",
)


@pytest.mark.parametrize("name", REQUIRED_IMPORTS)
def test_response_module_imports_constant(name: str):
    """Each centralized constant must be importable from ai.response —
    we re-export via `from ai.scoring_constants import …` so a future
    inline-literal regression shows up as a missing reference here."""
    assert hasattr(response, name), (
        f"ai.response is expected to import `{name}` from "
        f"ai.scoring_constants. If it was removed, the corresponding "
        f"literal probably crept back inline."
    )
    assert getattr(response, name) == getattr(scoring_constants, name)


# ─── Source-level no-magic-number sanity ─────────────────────────────


def test_no_inline_clock_life_scaling_literal_in_response():
    """The ×20.0 clock→life scaling factor used to appear inline in
    `evaluate_stack_threat` and `_held_counter_floor_ev`. After
    centralisation, it lives in scoring_constants.CLOCK_IMPACT_LIFE_SCALING.
    Any reintroduction of `* 20.0` outside string/comment context is a
    regression — guard via a coarse source-level check that only flags
    actual code (not docstrings).
    """
    src = inspect.getsource(response)
    # We allow the literal to appear inside triple-quoted docstrings or
    # comments — any other use is the regression we care about.
    # Strip docstrings line-by-line: heuristic — drop lines that begin
    # with `#` (comment) or live inside a triple-quoted block.
    code_lines = []
    in_triple = False
    triple_marker = None
    for ln in src.splitlines():
        stripped = ln.strip()
        if in_triple:
            code_lines.append("")  # blank — nothing to scan
            if triple_marker in ln:
                in_triple = False
                triple_marker = None
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            triple_marker = stripped[:3]
            # single-line triple? Toggle off if it closes on the same line.
            rest = stripped[3:]
            if triple_marker in rest:
                pass  # opens and closes on same line
            else:
                in_triple = True
            code_lines.append("")
            continue
        if stripped.startswith("#"):
            code_lines.append("")
            continue
        code_lines.append(ln)
    code = "\n".join(code_lines)
    assert "* 20.0" not in code, (
        "ai/response.py contains an inline `* 20.0` literal. The clock→"
        "life-point scaling factor lives in CLOCK_IMPACT_LIFE_SCALING. "
        "Replace the inline literal with the constant."
    )
