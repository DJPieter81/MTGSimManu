"""Link/smoke + derivation test: ai/outcome_ev.py uses the same
constants advertised in ai/scoring_constants.py, and the lookahead
window matches the canonical short-horizon used elsewhere in the
codebase (BHI's `p_higher_threat_in_n_turns(turns=2)` and the
spot-removal-deferral branch in `ai/ev_player.py`).

When `ai/outcome_ev.py` was first refactored, the
``p_finisher_reachable`` hypergeometric used a bare ``n_draws=2``.
Centralising it in ``ai/scoring_constants`` removed the magic number;
this test asserts:

1. The constant exists in ``ai.scoring_constants`` with the expected
   rule-encoded value (2 draws).
2. ``ai.outcome_ev`` imports and uses it (not a re-introduced literal).
3. Two distinct combo archetypes that exercise different code paths
   in ``build_combo_distribution`` (storm-class hypergeometric draw
   when no finisher is in hand, and reanimate-class graveyard target
   readiness) produce well-formed distributions consistent with the
   centralised lookahead.
"""
from __future__ import annotations

import inspect

import pytest

from ai import outcome_ev, scoring_constants


# ─── Constant value + linkage ─────────────────────────────────────


def test_finisher_reachable_lookahead_value():
    """``FINISHER_REACHABLE_LOOKAHEAD_DRAWS`` is 2 — matches the
    canonical short-horizon lookahead used by BHI and ev_player."""
    assert scoring_constants.FINISHER_REACHABLE_LOOKAHEAD_DRAWS == 2


def test_outcome_ev_imports_lookahead_constant():
    """``ai.outcome_ev`` imports the lookahead constant — if a future
    refactor re-introduces a literal `n_draws=2` this guard catches
    it (the import will be removed alongside)."""
    assert hasattr(outcome_ev, "FINISHER_REACHABLE_LOOKAHEAD_DRAWS")
    assert (outcome_ev.FINISHER_REACHABLE_LOOKAHEAD_DRAWS
            == scoring_constants.FINISHER_REACHABLE_LOOKAHEAD_DRAWS)


def test_lookahead_matches_bhi_default_turns():
    """Same rule: ``HandBeliefs.p_higher_threat_in_n_turns`` defaults
    to ``turns=2``. If one is re-tuned without the other, both
    branches drift apart — re-tuning is intentionally single-point
    via this constant."""
    from ai.bhi import HandBeliefs
    sig = inspect.signature(HandBeliefs.p_higher_threat_in_n_turns)
    bhi_default = sig.parameters["turns"].default
    assert bhi_default == scoring_constants.FINISHER_REACHABLE_LOOKAHEAD_DRAWS


def test_no_inline_n_draws_2_in_outcome_ev():
    """The bare literal ``n_draws=2`` used to appear inline in
    ``build_combo_distribution``. After centralisation, all call sites
    must pass the named constant. Any reintroduction is a regression
    flagged here."""
    src = inspect.getsource(outcome_ev)
    # Strip docstrings line-by-line: drop comment-only lines and lines
    # inside triple-quoted blocks. Doc references to "n=2" / "n_draws=2"
    # are allowed (we're guarding code, not narrative).
    code_lines = []
    in_triple = False
    triple_marker = None
    for ln in src.splitlines():
        stripped = ln.strip()
        if in_triple:
            if triple_marker and triple_marker in stripped:
                in_triple = False
                triple_marker = None
            code_lines.append("")
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            triple_marker = stripped[:3]
            # Single-line triple-quoted string — toggle off if it
            # closes on the same line and isn't just the opener.
            if stripped.count(triple_marker) >= 2:
                code_lines.append("")
                triple_marker = None
                continue
            in_triple = True
            code_lines.append("")
            continue
        if stripped.startswith("#"):
            code_lines.append("")
            continue
        code_lines.append(ln)
    code = "\n".join(code_lines)
    assert "n_draws=2" not in code, (
        "ai/outcome_ev.py reintroduced a bare `n_draws=2` literal. "
        "Use FINISHER_REACHABLE_LOOKAHEAD_DRAWS from ai.scoring_constants."
    )


# ─── Behavioural derivations: 2 archetypes (storm + reanimate) ────


def _make_bhi_safe_mock():
    """BHI mock with `_initialized=False` so combo_calc's
    `_compute_risk_discount` returns the no-info default (1.0)
    without exercising the BHI numeric paths (whose comparisons
    fail on plain MagicMocks)."""
    from unittest.mock import MagicMock
    bhi = MagicMock()
    bhi._initialized = False
    bhi.beliefs = None
    return bhi


def _make_snap(opp_life=20, my_mana=1):
    """Minimal real EVSnapshot — `dataclasses.replace` is used by
    `build_combo_distribution` to project outcome states, so we need
    an actual dataclass instance (not a MagicMock)."""
    from ai.ev_evaluator import EVSnapshot
    return EVSnapshot(
        opp_life=opp_life,
        my_mana=my_mana,
        my_power=0,
        my_creature_count=0,
        cards_drawn_this_turn=0,
    )


@pytest.fixture
def storm_distribution():
    """Storm-class fixture: empty hand of finishers + small library
    population, exercising the hypergeometric branch keyed off
    FINISHER_REACHABLE_LOOKAHEAD_DRAWS."""
    from unittest.mock import MagicMock

    # Card under test: a ritual (mana acceleration) with the 'ritual'
    # tag — keeps us out of the storm-finisher fast-path so we hit the
    # generic chain logic.
    card = MagicMock()
    card.template.name = "TestRitual"
    card.template.tags = {"ritual"}
    card.template.keywords = set()
    card.template.cmc = 1

    # Snapshot — must be a real EVSnapshot dataclass (build_combo_distribution
    # uses dataclasses.replace to project outcome states).
    snap = _make_snap(opp_life=20, my_mana=1)

    # Players: empty hand-of-finishers, small library with finishers,
    # so reachability is purely hypergeometric from
    # FINISHER_REACHABLE_LOOKAHEAD_DRAWS draws.
    me = MagicMock()
    me.hand = []
    me.battlefield = []
    me.graveyard = []
    me.library = [MagicMock() for _ in range(20)]
    for c in me.library[:3]:
        c.template.name = "TestFinisher"
    for c in me.library[3:]:
        c.template.name = "Filler"
    me.spells_cast_this_turn = 0
    me.deck_name = None  # skip goal-engine path

    opp = MagicMock()
    opp.deck_name = None
    bhi = _make_bhi_safe_mock()

    return outcome_ev.build_combo_distribution(
        card, snap, MagicMock(), me, opp, bhi,
        archetype="combo", profile=None,
    )


def test_storm_distribution_well_formed(storm_distribution):
    """Storm fixture's distribution is normalised and inside
    probability bounds. Exercises the hypergeometric finisher-
    reachable branch driven by FINISHER_REACHABLE_LOOKAHEAD_DRAWS."""
    assert storm_distribution is not None
    assert storm_distribution.is_well_formed()


@pytest.fixture
def reanimate_distribution():
    """Reanimate-class fixture: target sits in graveyard, no
    chain-solver readiness needed. The reanimate override sets
    ``p_finisher_reachable = 1.0`` and bypasses the hypergeometric;
    it must still produce a valid distribution shape."""
    from unittest.mock import MagicMock

    card = MagicMock()
    card.template.name = "TestReanimate"
    card.template.tags = {"reanimate"}
    card.template.keywords = set()
    card.template.cmc = 2

    snap = _make_snap(opp_life=20, my_mana=2)

    me = MagicMock()
    me.hand = []
    me.battlefield = []
    # One real creature card in graveyard → reanimate target ready.
    gy_card = MagicMock()
    gy_card.template.is_creature = True
    gy_card.template.power = 4
    me.graveyard = [gy_card]
    me.library = [MagicMock() for _ in range(40)]
    for c in me.library:
        c.template.name = "Filler"
    me.spells_cast_this_turn = 0
    me.deck_name = None

    opp = MagicMock()
    opp.deck_name = None
    bhi = _make_bhi_safe_mock()

    return outcome_ev.build_combo_distribution(
        card, snap, MagicMock(), me, opp, bhi,
        archetype="combo", profile=None,
    )


def test_reanimate_distribution_well_formed(reanimate_distribution):
    """Reanimate fixture's distribution is normalised — the override
    branch must produce a valid distribution even when the lookahead
    hypergeometric would have returned 0 (no finishers in library)."""
    assert reanimate_distribution is not None
    assert reanimate_distribution.is_well_formed()
