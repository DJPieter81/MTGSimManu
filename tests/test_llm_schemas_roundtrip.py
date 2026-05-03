"""Round-trip + strictness contract for every schema in `ai.llm_schemas`.

Phase H of the abstraction-cleanup pass.  The contract this test
locks in:

  1. Every schema round-trips through `to_json_dict` →
     `from_json_dict` losslessly.
  2. Every schema rejects extra/unknown fields (`extra="forbid"`).
  3. Every schema is frozen — instances are hashable and immutable.

The schemas back four LLM-driven tools (synth_gameplan, diagnose_replay,
audit_doc_freshness, handler_audit) plus a deferred fifth
(failing_test_spec).  If any of these contracts breaks, downstream
agent factories in `ai/llm_agents.py` see silent shape drift and the
LLM responses start failing pydantic validation in production.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai.llm_schemas import (
    BugHypothesis,
    DocFreshnessReport,
    FailingTestSpec,
    HandlerGapReport,
    SynthesizedGameplan,
    SynthesizedGoal,
    from_json_dict,
    merge_hypotheses,
    to_json_dict,
    to_prompt_section,
)


# ─── Per-schema fixtures: at least 2 distinct examples per schema ───

def _synth_gameplan_minimal() -> SynthesizedGameplan:
    return SynthesizedGameplan(
        deck_name="Mini Deck",
        archetype="aggro",
        goals=[
            SynthesizedGoal(
                goal_type="CURVE_OUT",
                description="Deploy creatures",
                card_roles={"enablers": ["A", "B"]},
            ),
        ],
    )


def _synth_gameplan_full() -> SynthesizedGameplan:
    return SynthesizedGameplan(
        deck_name="Full Deck",
        archetype="combo",
        archetype_subtype="reanimator",
        goals=[
            SynthesizedGoal(
                goal_type="FILL_RESOURCE",
                description="Fill graveyard",
                card_roles={"enablers": ["E1", "E2"]},
                resource_target=4,
                resource_zone="graveyard",
                resource_min_cmc=3,
            ),
            SynthesizedGoal(
                goal_type="EXECUTE_PAYOFF",
                description="Reanimate the threat",
                card_roles={"payoffs": ["P1"]},
            ),
        ],
        fallback_goals=[
            SynthesizedGoal(goal_type="GRIND_VALUE", description="Stall"),
        ],
        mulligan_keys=["E1", "P1"],
        mulligan_combo_paths=[{"enabler": ["E1"], "payoff": ["P1"]}],
        always_early=["E1"],
        critical_pieces=["P1"],
        combo_readiness_check="generic_combo_readiness",
    )


def _bug_hypothesis_high_conf() -> BugHypothesis:
    return BugHypothesis(
        observed_symptom="AI casts sweeper with no X for full board",
        suspected_subsystem="ai.ev_player",
        failing_test_rule="sweeper x value optimizes for top reachable threat cmc",
        confidence=0.85,
    )


def _bug_hypothesis_low_conf() -> BugHypothesis:
    return BugHypothesis(
        observed_symptom="Combo enabler held instead of cast",
        suspected_subsystem="ai.combo_calc",
        failing_test_rule="ritual ev exceeds discount when chain depth >= 3",
        confidence=0.4,
    )


def _handler_gap_p0() -> HandlerGapReport:
    return HandlerGapReport(
        card_name="Example Modal Card",
        timing="ETB",
        printed_modes=["Mode A", "Mode B", "Mode C"],
        handler_modes=["Mode A", "Mode B"],
        missing_modes=["Mode C"],
        fabricated_modes=[],
        severity="P0",
    )


def _handler_gap_p2_with_fabrications() -> HandlerGapReport:
    return HandlerGapReport(
        card_name="Other Card",
        timing="TRIGGER",
        printed_modes=["Real Mode"],
        handler_modes=["Real Mode", "Made-up Mode"],
        missing_modes=[],
        fabricated_modes=["Made-up Mode"],
        severity="P2",
    )


def _doc_freshness_should_supersede() -> DocFreshnessReport:
    return DocFreshnessReport(
        doc_path="docs/diagnostics/2026-04-21_x.md",
        current_status="active",
        should_change_to="superseded",
        replacement_doc="docs/diagnostics/2026-04-23_x_consolidated.md",
        reason="Newer consolidated doc exists; current matrix WR matches its prediction.",
    )


def _doc_freshness_no_change() -> DocFreshnessReport:
    return DocFreshnessReport(
        doc_path="docs/design/some_design.md",
        current_status="active",
        should_change_to=None,
        replacement_doc=None,
        reason="Doc is current; no superseder, hypothesis still standing.",
    )


def _failing_test_spec_basic() -> FailingTestSpec:
    return FailingTestSpec(
        test_file="tests/test_some_rule.py",
        rule_name="some mechanic enforces correct ordering",
        fixture_setup="game = make_game(); game.battlefield.add(...)",
        assertion="assert engine.do_thing() == expected",
    )


def _failing_test_spec_with_default_status() -> FailingTestSpec:
    return FailingTestSpec(
        test_file="tests/test_other.py",
        rule_name="another mechanic",
        fixture_setup="...",
        assertion="...",
        expected_status_before_fix="fail",
    )


ALL_FIXTURES = [
    _synth_gameplan_minimal(),
    _synth_gameplan_full(),
    _bug_hypothesis_high_conf(),
    _bug_hypothesis_low_conf(),
    _handler_gap_p0(),
    _handler_gap_p2_with_fabrications(),
    _doc_freshness_should_supersede(),
    _doc_freshness_no_change(),
    _failing_test_spec_basic(),
    _failing_test_spec_with_default_status(),
]


# ─── Round-trip ─────────────────────────────────────────────────────


@pytest.mark.parametrize("obj", ALL_FIXTURES, ids=lambda o: type(o).__name__ + "/" + str(id(o)))
def test_every_schema_round_trips_via_json_helpers(obj):
    """`from_json_dict(cls, to_json_dict(obj)) == obj` for every schema.

    `to_json_dict` strips empty-default keys for SynthesizedGameplan
    (loader compatibility), but the loader applies the same defaults
    on parse so the round-trip is still equality-preserving when we
    re-validate."""
    cls = type(obj)
    serialized = to_json_dict(obj)
    restored = from_json_dict(cls, serialized)
    # Frozen models implement __eq__ on field values, so this is a
    # full structural equality check.
    assert restored == obj


# ─── Extra-fields rejection ────────────────────────────────────────


@pytest.mark.parametrize(
    "cls,base_kwargs",
    [
        (BugHypothesis, dict(
            observed_symptom="x", suspected_subsystem="other",
            failing_test_rule="rule", confidence=0.5,
        )),
        (HandlerGapReport, dict(
            card_name="C", timing="ETB", printed_modes=[], handler_modes=[],
            missing_modes=[], fabricated_modes=[], severity="P2",
        )),
        (DocFreshnessReport, dict(
            doc_path="d", current_status="active", reason="r",
        )),
        (FailingTestSpec, dict(
            test_file="t", rule_name="r", fixture_setup="s", assertion="a",
        )),
        (SynthesizedGameplan, dict(
            deck_name="D", goals=[{"goal_type": "CURVE_OUT"}],
        )),
        (SynthesizedGoal, dict(goal_type="CURVE_OUT")),
    ],
)
def test_every_schema_rejects_unknown_fields(cls, base_kwargs):
    """`extra="forbid"` is on `_LLMBase`, so any unknown key crashes
    `model_validate`.  This catches LLM hallucinations of extra
    fields at parse time rather than letting them silently pass
    through."""
    bad = dict(base_kwargs)
    bad["__hallucinated_extra__"] = "should be rejected"
    with pytest.raises(ValidationError):
        cls.model_validate(bad)


# ─── Frozen / immutability ──────────────────────────────────────────


@pytest.mark.parametrize("obj", ALL_FIXTURES, ids=lambda o: type(o).__name__)
def test_every_schema_is_frozen(obj):
    """Frozen models reject attribute assignment.  This protects against
    downstream `output.field = ...` mutations that would silently
    diverge from the round-tripped JSON."""
    with pytest.raises((ValidationError, TypeError, AttributeError)):
        obj.deck_name = "should-not-mutate"  # arbitrary field — caller picks per type


# ─── Closed-set enum enforcement ────────────────────────────────────


def test_subsystem_literal_rejects_invented_module():
    with pytest.raises(ValidationError):
        BugHypothesis(
            observed_symptom="x",
            suspected_subsystem="ai.does_not_exist",  # not in Subsystem literal
            failing_test_rule="r",
            confidence=0.5,
        )


def test_severity_literal_rejects_invented_grade():
    with pytest.raises(ValidationError):
        HandlerGapReport(
            card_name="C", timing="ETB",
            printed_modes=[], handler_modes=[],
            missing_modes=[], fabricated_modes=[],
            severity="P3",  # not in Severity literal (P0/P1/P2 only)
        )


def test_handler_timing_literal_rejects_invented_slot():
    with pytest.raises(ValidationError):
        HandlerGapReport(
            card_name="C", timing="LANDFALL",  # not in HandlerTiming
            printed_modes=[], handler_modes=[],
            missing_modes=[], fabricated_modes=[],
            severity="P2",
        )


def test_doc_status_literal_rejects_invented_status():
    with pytest.raises(ValidationError):
        DocFreshnessReport(
            doc_path="d", current_status="in_progress",  # not in DocStatus
            reason="r",
        )


def test_confidence_must_be_between_zero_and_one():
    with pytest.raises(ValidationError):
        BugHypothesis(
            observed_symptom="x", suspected_subsystem="other",
            failing_test_rule="r", confidence=1.5,
        )
    with pytest.raises(ValidationError):
        BugHypothesis(
            observed_symptom="x", suspected_subsystem="other",
            failing_test_rule="r", confidence=-0.1,
        )


def test_failing_test_rule_max_length_enforced():
    long_rule = "x" * 121  # 120 char limit
    with pytest.raises(ValidationError):
        BugHypothesis(
            observed_symptom="x", suspected_subsystem="other",
            failing_test_rule=long_rule, confidence=0.5,
        )


# ─── to_prompt_section helper ───────────────────────────────────────


def test_to_prompt_section_renders_json_block():
    """`to_prompt_section` renders a fenced JSON block with a Markdown
    heading when a header is supplied — used when chaining one
    agent's output into the next agent's prompt."""
    obj = _bug_hypothesis_high_conf()
    rendered = to_prompt_section(obj, header="Hypothesis 1")
    assert rendered.startswith("## Hypothesis 1")
    assert "```json" in rendered
    assert "ai.ev_player" in rendered  # field value present


def test_to_prompt_section_no_header_skips_heading():
    obj = _bug_hypothesis_high_conf()
    rendered = to_prompt_section(obj)
    assert not rendered.startswith("## ")
    assert "```json" in rendered


# ─── merge_hypotheses helper ────────────────────────────────────────


def test_merge_hypotheses_dedups_by_subsystem_plus_rule():
    a = _bug_hypothesis_high_conf()
    # Same subsystem + rule, lower confidence — should be discarded.
    b = a.model_copy(update={"confidence": 0.3})
    merged = merge_hypotheses([a], [b])
    assert len(merged) == 1
    assert merged[0].confidence == 0.85


def test_merge_hypotheses_keeps_distinct_entries_sorted_by_confidence():
    a = _bug_hypothesis_high_conf()
    b = _bug_hypothesis_low_conf()
    merged = merge_hypotheses([b], [a])
    # Sorted by descending confidence regardless of input order.
    assert merged[0] == a
    assert merged[1] == b
