"""Central pydantic schemas for every LLM-driven tool in the project.

Phase H of the abstraction-cleanup pass.  PR #258 introduced a one-off
`SynthesizedGameplan` for offline gameplan synthesis.  Before adding G-2
(doc-freshness audit), G-3 (replay diagnosis), and G-4 (handler audit),
we centralize every LLM-output schema in this module so every agent
factory in `ai/llm_agents.py` shares one shape contract.

Design rules:
- Every schema inherits from `_LLMBase`, which is `strict`, `frozen`,
  and forbids extra fields.  Bad LLM output fails fast at validation,
  not at runtime.
- Closed-set fields are `Literal[...]` — the model can't invent new
  enum values, and the prompt builder can enumerate them mechanically.
- Every `Field` has a `description=` — pydantic-ai uses these
  descriptions when prompting the model, so they substitute for
  long inline schema docs in the system prompt.
- All schemas are frozen — once a model returns a value, it's
  immutable; downstream code that needs to "tweak" a result must
  call `.model_copy(update=...)`.

Card-name handling: card names appear as DATA in these schemas
(e.g. `card_name: str` on `HandlerGapReport`).  They never appear as
Python branches on a card-name string, which is the pattern
prohibited by the ABSTRACTION CONTRACT.

Every schema in this module has at least one round-trip test in
`tests/test_llm_schemas_roundtrip.py` and (for the existing migrated
schemas) at least one consumer-side round-trip test from PR #258's
test suite.
"""
from __future__ import annotations

from typing import Annotated, Dict, List, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field


# ─── Base class ──────────────────────────────────────────────────────

class _LLMBase(BaseModel):
    """Strict, frozen, no-extras base for every LLM-output schema.

    `strict=True` rejects type coercions silently; `extra="forbid"`
    rejects unknown keys (catches hallucinated fields); `frozen=True`
    makes instances hashable and prevents downstream mutation."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


T = TypeVar("T", bound=_LLMBase)


# ─── Shared closed-set enums ────────────────────────────────────────

Subsystem = Literal[
    "engine.card_effects",
    "engine.game_state",
    "engine.game_runner",
    "ai.response",
    "ai.ev_player",
    "ai.ev_evaluator",
    "ai.permanent_threat",
    "ai.gameplan",
    "ai.combo_calc",
    "ai.combo_evaluator",
    "ai.finisher_simulator",
    "ai.mulligan",
    "decks.gameplan_loader",
    "other",
]
"""Module that owns a Magic rule.  Used by the bug-hypothesis and
handler-audit agents to point at exactly one subsystem per finding —
spanning two means the boundary is wrong (per ABSTRACTION CONTRACT
question #2)."""

Severity = Literal["P0", "P1", "P2"]
"""Bug / gap severity for triage.  P0 = T1-deck mainboard, P1 = T1/T2
sideboard, P2 = no current deck.  Same scale as PROJECT_STATUS.md §7."""

HandlerTiming = Literal[
    "ETB", "ATTACK", "DEATH", "CAST", "TRIGGER", "RESOLVE", "ACTIVATED"
]
"""Effect-registry timing slot.  Mirrors `engine.card_effects.EffectTiming`
enum names exactly so the audit agent can cross-reference."""

DocStatus = Literal["active", "superseded", "falsified", "archived"]
"""Lifecycle marker on a docs/ frontmatter block.  Matches the
CLAUDE.md frontmatter convention.  Used by the doc-freshness agent."""


# ─── Synth-gameplan schemas (migrated from ai/gameplan_schemas.py) ──
#
# These were the entire payload of `ai/gameplan_schemas.py` before
# Phase H.  They keep their original names for consumer compatibility;
# the shape is identical save for inheriting from `_LLMBase`.

GoalTypeStr = Literal[
    "DEPLOY_ENGINE", "FILL_RESOURCE", "RAMP",
    "EXECUTE_PAYOFF", "CURVE_OUT", "PUSH_DAMAGE",
    "DISRUPT", "PROTECT", "INTERACT",
    "GRIND_VALUE", "CLOSE_GAME",
]


class SynthesizedGoal(_LLMBase):
    """Typed mirror of `ai.gameplan.Goal` for synth output.

    The synthesizer emits one of these per strategic phase.  Card-name
    lists live in `card_roles` exclusively — `card_priorities` is the
    legacy hint surface that newer synthesis paths leave empty."""

    goal_type: GoalTypeStr = Field(
        ...,
        description="Strategic phase tag.  One of: DEPLOY_ENGINE, FILL_RESOURCE, "
        "RAMP, EXECUTE_PAYOFF, CURVE_OUT, PUSH_DAMAGE, DISRUPT, PROTECT, "
        "INTERACT, GRIND_VALUE, CLOSE_GAME.",
    )
    description: str = Field(
        default="",
        description="One-sentence human-readable description of this goal.",
    )
    card_priorities: Dict[str, float] = Field(
        default_factory=dict,
        description="Legacy per-card priority weights.  Newer synthesis paths "
        "leave this empty and put names in `card_roles` instead.",
    )
    card_roles: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Mechanic-bucket → card-name list.  Buckets: enablers, "
        "payoffs, interaction, fillers, protection.",
    )
    transition_check: Optional[str] = Field(
        default=None,
        description="Optional registered check name that gates transition out of "
        "this goal.",
    )
    min_turns: int = Field(
        default=0,
        description="Minimum turns to spend in this goal before transitioning.",
    )
    min_mana_for_payoff: int = Field(
        default=0,
        description="Minimum lands in play before the payoff is castable.",
    )
    prefer_cycling: bool = Field(
        default=False,
        description="If True, the goal prefers cycling/loot effects over "
        "deploying.",
    )
    hold_mana: bool = Field(
        default=False,
        description="If True, the goal prefers holding mana up for responses.",
    )
    resource_target: int = Field(
        default=0,
        description="Target count in the resource zone before transitioning out.",
    )
    resource_zone: str = Field(
        default="graveyard",
        description="Zone the resource counter watches: graveyard, exile, etc.",
    )
    resource_min_cmc: int = Field(
        default=0,
        description="Minimum CMC for resource-zone cards to count toward target.",
    )


class SynthesizedGameplan(_LLMBase):
    """Typed mirror of `ai.gameplan.DeckGameplan` for synth output.

    Fields the loader can DERIVE are omitted on purpose — see Phase 3
    (`mulligan_keys` derived from goal `card_roles`).  The synthesizer
    may still emit them when it has a non-obvious override to record;
    in that case the loader's override semantics apply."""

    deck_name: str = Field(
        ...,
        description="Human-readable deck name.  Round-trips into the loader "
        "and JSON file basename.",
    )
    archetype: Literal["aggro", "midrange", "control", "combo", "tempo", "ramp"] = Field(
        default="midrange",
        description="High-level archetype tag.  Drives mulligan defaults and "
        "weighted matchup scoring.",
    )
    archetype_subtype: Optional[str] = Field(
        default=None,
        description="Optional refinement, e.g. 'reanimator' for combo decks.",
    )
    goals: List[SynthesizedGoal] = Field(
        ...,
        description="Strategic-phase plan in priority order.  The first goal "
        "active when the GoalEngine starts.",
    )
    fallback_goals: Optional[List[SynthesizedGoal]] = Field(
        default=None,
        description="Optional alternate plan when the primary chain breaks "
        "(e.g. cascade fizzle).",
    )

    mulligan_min_lands: int = Field(
        default=2,
        description="Lower bound on lands in a keepable seven.",
    )
    mulligan_max_lands: int = Field(
        default=4,
        description="Upper bound on lands in a keepable seven.",
    )
    mulligan_require_creature_cmc: int = Field(
        default=0,
        description="If non-zero, require at least one creature with CMC ≤ this "
        "value in the keep.",
    )
    mulligan_effective_cmc: Dict[str, int] = Field(
        default_factory=dict,
        description="Per-card effective-CMC overrides for mulligan curve checks.",
    )

    mulligan_keys: List[str] = Field(
        default_factory=list,
        description="Override list of cards that strongly bias keep decisions. "
        "Empty means derive from goal card_roles (Phase 3).",
    )
    mulligan_combo_sets: List[List[str]] = Field(
        default_factory=list,
        description="Legacy combo-set surface.  Each inner list is one set the "
        "mulligan engine looks for whole.",
    )
    mulligan_combo_paths: List[Dict[str, List[str]]] = Field(
        default_factory=list,
        description="Modern combo-paths surface.  Each dict is role-bucket → "
        "card list; a hand with one card from each bucket of any path is "
        "keepable.",
    )
    mulligan_cmc_profile: Dict[str, int] = Field(
        default_factory=dict,
        description="Per-bucket count requirements at virtual hand sizes.",
    )

    always_early: List[str] = Field(
        default_factory=list,
        description="Cards to deploy on the first available turn (e.g. "
        "1-mana hasty creatures).",
    )
    reactive_only: List[str] = Field(
        default_factory=list,
        description="Cards held for responses, never proactively deployed "
        "(e.g. counterspells).",
    )
    critical_pieces: List[str] = Field(
        default_factory=list,
        description="Cards whose presence in hand strongly biases keeps.",
    )
    land_priorities: Dict[str, float] = Field(
        default_factory=dict,
        description="Per-land priority weights for the land-drop selector.",
    )

    combo_readiness_check: Optional[Literal["generic_combo_readiness"]] = Field(
        default=None,
        description="Registered combo-readiness check name.  None means archetype "
        "default applies.",
    )


# ─── New schemas for Phase H tools ──────────────────────────────────

class BugHypothesis(_LLMBase):
    """One ranked hypothesis emitted by the replay-diagnose agent.

    The diagnose-replay agent reads a Bo3 verbose log and emits a
    ranked list of these — the highest-confidence cause first.  Each
    hypothesis must point at exactly one `Subsystem` and phrase the
    failing test in mechanic terms (per ABSTRACTION CONTRACT)."""

    observed_symptom: str = Field(
        ...,
        description="One-sentence symptom from the replay log "
        "(e.g. 'AI casts Wrath on T3 with no X for sweep').",
    )
    suspected_subsystem: Subsystem = Field(
        ...,
        description="Module that owns the rule the symptom violates.  Must be "
        "exactly one — spanning two means the boundary is wrong.",
    )
    failing_test_rule: str = Field(
        ...,
        description="Rule-phrased test name per ABSTRACTION CONTRACT — name the "
        "mechanic, not the card.  ≤ 120 chars.",
        max_length=120,
    )
    confidence: Annotated[
        float,
        Field(
            ge=0.0,
            le=1.0,
            description="Model's confidence this is the cause, 0..1.",
        ),
    ]


class HandlerGapReport(_LLMBase):
    """One audit row from the handler-audit agent.

    The handler-audit agent compares an oracle-text card to the
    `engine.card_effects` registry handler for that card.  Each row
    flags missing or fabricated modes, with severity for triage."""

    card_name: str = Field(
        ...,
        description="Exact printed card name as in MTGJSON.",
    )
    timing: HandlerTiming = Field(
        ...,
        description="Effect-registry timing slot the handler is registered for.",
    )
    printed_modes: List[str] = Field(
        ...,
        description="Modes literally on the oracle text, in printed order.",
    )
    handler_modes: List[str] = Field(
        ...,
        description="Modes the engine handler implements, in source order.",
    )
    missing_modes: List[str] = Field(
        ...,
        description="Subset of printed_modes the handler doesn't implement.",
    )
    fabricated_modes: List[str] = Field(
        ...,
        description="Subset of handler_modes that don't appear in oracle text.",
    )
    severity: Severity = Field(
        ...,
        description="P0 = T1-deck mainboard, P1 = T1/T2 sideboard, "
        "P2 = no current deck.",
    )


class DocFreshnessReport(_LLMBase):
    """One audit row from the doc-freshness agent.

    The doc-freshness agent reads docs/ frontmatter, recent commits,
    and the latest matrix WRs to flag docs whose `status` field is
    stale (e.g. `active` for a hypothesis already disproven, or
    `superseded` without a `superseded_by` pointer)."""

    doc_path: str = Field(
        ...,
        description="Path relative to repo root, e.g. "
        "'docs/diagnostics/2026-04-21_x.md'.",
    )
    current_status: DocStatus = Field(
        ...,
        description="The doc's current frontmatter status field.",
    )
    should_change_to: Optional[DocStatus] = Field(
        default=None,
        description="None means leave as-is.  Otherwise the recommended new "
        "status given recent evidence.",
    )
    replacement_doc: Optional[str] = Field(
        default=None,
        description="Path to the doc that supersedes this one, if any.",
    )
    reason: str = Field(
        ...,
        description="≤ 240 chars; cite specific evidence (matrix WR, commit sha, "
        "newer doc).",
        max_length=240,
    )


class FailingTestSpec(_LLMBase):
    """Reserved for G-5 (deferred). Defined now so the schema surface
    is complete for `ai/llm_agents.py`."""

    test_file: str = Field(
        ...,
        description="tests/test_<rule>.py — file path.",
    )
    rule_name: str = Field(
        ...,
        description="Rule-phrased; no card names.",
    )
    fixture_setup: str = Field(
        ...,
        description="Pseudocode for the test fixture.",
    )
    assertion: str = Field(
        ...,
        description="Pseudocode for the assertion.",
    )
    expected_status_before_fix: Literal["fail"] = Field(
        default="fail",
        description="Always 'fail' — failing-test-first per ABSTRACTION CONTRACT.",
    )


# ─── Module-level helpers ───────────────────────────────────────────

# Keys whose empty defaults are stripped from the JSON dump.  Used by
# `to_json_dict` for SynthesizedGameplan (loader treats missing keys as
# "not specified").  Other schemas don't need this — their fields are
# all required or have None defaults.
_SYNTH_STRIPPABLE_EMPTY = (
    "mulligan_keys",
    "mulligan_combo_sets",
    "mulligan_combo_paths",
    "mulligan_cmc_profile",
    "always_early",
    "reactive_only",
    "critical_pieces",
    "land_priorities",
    "mulligan_effective_cmc",
    "fallback_goals",
)


def to_json_dict(obj: _LLMBase) -> dict:
    """Serialize an LLM schema instance to a plain JSON-ready dict.

    For `SynthesizedGameplan` we additionally strip empty-default
    override keys so the loader falls through to its derivation /
    default behaviour (matches the original `gameplan_schemas`
    contract).  For other schemas, `model_dump(mode="json",
    exclude_none=True)` is the full transform."""

    data = obj.model_dump(mode="json", exclude_none=True)
    if isinstance(obj, SynthesizedGameplan):
        for key in _SYNTH_STRIPPABLE_EMPTY:
            if key in data and not data[key]:
                del data[key]
    return data


def from_json_dict(cls: type[T], d: dict) -> T:
    """Validate-cast a plain dict into the requested LLM schema class.

    Wraps `cls.model_validate(d)` so callers don't have to import
    pydantic directly.  Validation errors propagate as
    `pydantic.ValidationError`."""

    return cls.model_validate(d)


def to_prompt_section(obj: _LLMBase, header: str = "") -> str:
    """Render a schema instance as a prompt-section string.

    Used when one agent's output becomes another agent's input
    (e.g. a `BugHypothesis` fed into a `FailingTestSpec` agent).  The
    header is rendered as a Markdown subsection if non-empty.

    The rendering is JSON for round-trip safety — free-text rendering
    is ambiguous when fields contain quotes, newlines, or list values."""

    import json as _json
    body = _json.dumps(to_json_dict(obj), indent=2, sort_keys=True)
    if header:
        return f"## {header}\n\n```json\n{body}\n```"
    return f"```json\n{body}\n```"


def merge_hypotheses(
    a: List[BugHypothesis], b: List[BugHypothesis]
) -> List[BugHypothesis]:
    """Merge two ranked hypothesis lists into one, deduplicated by
    (suspected_subsystem, failing_test_rule), keeping the
    higher-confidence entry.  Result is sorted by descending
    confidence.

    Used when two passes (e.g. early-turn + late-turn) of the
    diagnose-replay agent produce overlapping hypotheses."""

    by_key: Dict[tuple, BugHypothesis] = {}
    for h in list(a) + list(b):
        key = (h.suspected_subsystem, h.failing_test_rule)
        existing = by_key.get(key)
        if existing is None or h.confidence > existing.confidence:
            by_key[key] = h
    return sorted(by_key.values(), key=lambda h: -h.confidence)


__all__ = [
    "_LLMBase",
    "Subsystem",
    "Severity",
    "HandlerTiming",
    "DocStatus",
    "GoalTypeStr",
    "SynthesizedGoal",
    "SynthesizedGameplan",
    "BugHypothesis",
    "HandlerGapReport",
    "DocFreshnessReport",
    "FailingTestSpec",
    "to_json_dict",
    "from_json_dict",
    "to_prompt_section",
    "merge_hypotheses",
]
