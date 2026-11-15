"""Typed input schema + user-prompt renderer for every LLM task.

The agent layer's contract per task is three artefacts, each with one
owner:

  * the **output schema** — `ai.llm_schemas` (what the model returns);
  * the **system prompt** — `ai/llm_prompts/<task>_v<N>.md` (versioned,
    append-only; what the model is told the job is);
  * the **user prompt** — this module: a strict pydantic input model
    per task whose `render()` produces the exact text the model sees.

Why the input is typed: a renderer that takes loose dicts drifts from
its callers silently, and the eval goldens (`tests/eval/golden/<task>`)
can only exercise the production prompt if their `input` block
validates against the same schema the tools build.  So the tools build
these models (doing their own I/O — reading a doc, loading a card
database) and the renderer is pure: same model in, same bytes out.

The rendered text is byte-identical to what each tool sent before this
module existed (pinned by `tests/test_llm_inputs.py`), so the response
cache's string-keyed rows stay valid.  Structured inputs reach the
response cache as their `model_dump()` — the same key a plain dict of
the same fields produces (`ai.llm_cache.cache_key`) — and the model
receives `render()` (`ai.llm_agents.render_user_prompt`).

Card names appear here only as DATA (a decklist row, the card being
classified); nothing branches on them.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from ai.llm_schemas import BugHypothesis, to_prompt_section


class TaskInput(BaseModel):
    """Strict, frozen, no-extras base for every task input."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    def render(self) -> str:  # pragma: no cover — every subclass overrides
        raise NotImplementedError


# ─── synth_gameplan ─────────────────────────────────────────────────


class DecklistCardRow(TaskInput):
    """One mainboard card as the gameplan synthesiser sees it: the
    deterministic feature surface of `ai.card_features`, not raw
    oracle text (Phase I-3 compression)."""

    name: str = Field(..., description="Card name (data, never a branch).")
    quantity: int = Field(..., description="Copies in the mainboard.")
    cmc: Optional[int] = Field(
        None, description="Mana value; None only for a database miss.",
    )
    flags: List[str] = Field(
        default_factory=list,
        description="Mechanic flags from `ai.card_features` (e.g. removal).",
    )
    types: List[str] = Field(default_factory=list, description="Card types.")
    oracle_excerpt: str = Field(
        "", description="Opening oracle text, already capped by the builder.",
    )
    db_miss: bool = Field(
        False, description="True when the card database had no entry.",
    )

    def render(self) -> str:
        if self.db_miss:
            return f"- {self.quantity}× {self.name}  (NO FEATURES — DB miss)"
        cost = f"{{{self.cmc}}}"
        if self.flags:
            cost = f"{cost}|{','.join(self.flags)}"
        type_line = "/".join(self.types) if self.types else "?"
        line = f"- {self.quantity}× {self.name} {cost} | {type_line}"
        if self.oracle_excerpt:
            line += f'\n    "{self.oracle_excerpt}"'
        return line


class SynthGameplanInput(TaskInput):
    """A decklist, compressed to features — built by
    `ai.llm_compression.decklist_input`."""

    deck_name: str = Field(..., description="Deck name as registered.")
    cards: List[DecklistCardRow] = Field(
        ..., description="Mainboard rows, sorted by card name.",
    )

    def render(self) -> str:
        total = sum(row.quantity for row in self.cards)
        lines = [f"Deck name: {self.deck_name}", "",
                 f"Mainboard ({total} cards):"]
        lines.extend(row.render() for row in self.cards)
        lines.append("")
        lines.append(
            "Emit a SynthesizedGameplan JSON object describing this deck's "
            "goals, role assignments, and mulligan keys."
        )
        return "\n".join(lines)


# ─── diagnose_replay ────────────────────────────────────────────────


class DiagnoseReplayInput(TaskInput):
    """A Bo3 replay excerpt plus the diagnostic doc it is read against."""

    diagnostic_doc: str = Field(
        ..., description="Path of the diagnostic doc framing the outlier.",
    )
    log_excerpt: str = Field(
        ..., description="Replay log lines (compressed by "
        "`ai.llm_compression.compress_replay` for a full log).",
    )

    def render(self) -> str:
        return "\n".join([
            f"# Diagnostic doc: {self.diagnostic_doc}",
            "",
            "## Replay log excerpt",
            self.log_excerpt,
            "",
            "Emit a ranked list of BugHypothesis objects, most likely "
            "cause first.",
        ])


# ─── audit_doc_freshness ────────────────────────────────────────────


class SiblingDoc(TaskInput):
    """A newer doc in the same domain as the doc under audit."""

    path: str = Field(..., description="Repository-relative doc path.")
    age_days: int = Field(..., description="Days since its last commit.")
    title: str = Field("", description="Its frontmatter title.")


class DocFreshnessInput(TaskInput):
    """One doc's frontmatter, age, body and newer siblings — built by
    `tools.audit_doc_freshness.freshness_input`."""

    doc_path: str = Field(..., description="Repository-relative doc path.")
    status: str = Field(..., description="Frontmatter status, or <missing>.")
    priority: str = Field(..., description="Frontmatter priority, or <missing>.")
    age_days: int = Field(..., description="Days since its last commit.")
    siblings: List[SiblingDoc] = Field(
        default_factory=list, description="Newer same-domain docs.",
    )
    body: str = Field(..., description="Doc body, already truncated.")

    def render(self) -> str:
        parts: List[str] = [
            f"# Doc to assess: {self.doc_path}",
            f"Frontmatter status: {self.status}",
            f"Frontmatter priority: {self.priority}",
            f"Days since last commit: {self.age_days}",
            "",
        ]
        if self.siblings:
            parts.append("## Newer sibling docs (same domain)")
            for s in self.siblings:
                parts.append(
                    f"- {s.path} (last modified {s.age_days}d ago) — {s.title}"
                )
            parts.append("")
        parts.append("## This doc's body")
        parts.append(self.body)
        parts.append("")
        parts.append(
            "Emit a DocFreshnessReport.  `should_change_to` is None if the doc "
            "is genuinely still active.  Cite specific evidence (newer doc path, "
            "age, frontmatter signal) in `reason`."
        )
        return "\n".join(parts)


# ─── handler_audit ──────────────────────────────────────────────────


class HandlerAuditInput(TaskInput):
    """One card's printed oracle text and its registered handler."""

    card_name: str = Field(..., description="Exact printed card name.")
    oracle_text: str = Field(..., description="Printed oracle text.")
    handler_signature: str = Field(
        ..., description="The registered handler: its timing slot and "
        "source (or a faithful summary of the branches it implements).",
    )

    def render(self) -> str:
        return "\n".join([
            f"# Card: {self.card_name}",
            "",
            "## Oracle text",
            self.oracle_text,
            "",
            "## Registered handler",
            "```",
            self.handler_signature,
            "```",
            "",
            "Emit one HandlerGapReport comparing the printed modes with the "
            "modes the handler implements.",
        ])


# ─── failing_test_spec ──────────────────────────────────────────────


_NON_ALNUM = re.compile(r"[^a-z0-9]")


def rule_test_file(rule_name: str) -> str:
    """`tests/test_<rule>.py` for a rule-phrased test name: lower-cased,
    each non-alphanumeric character an underscore (the versioned
    failing_test_spec prompt's naming rule)."""
    return f"tests/test_{_NON_ALNUM.sub('_', rule_name.lower())}.py"


class FailingTestSpecInput(TaskInput):
    """A bug hypothesis to turn into one rule-phrased failing test."""

    hypothesis: BugHypothesis = Field(..., description="The hypothesis.")
    conventions: str = Field(
        "", description="Short note on the project's test conventions.",
    )

    def render(self) -> str:
        parts = [
            to_prompt_section(self.hypothesis, "Bug hypothesis"),
            "",
            "## Test file (the naming rule applied to failing_test_rule)",
            rule_test_file(self.hypothesis.failing_test_rule),
        ]
        if self.conventions:
            parts.extend(["", "## Test conventions", self.conventions])
        parts.extend([
            "",
            "Emit one FailingTestSpec whose assertion goes red without the "
            "fix and green with it.",
        ])
        return "\n".join(parts)


# ─── classify_oracle ────────────────────────────────────────────────


class OracleClassifyInput(TaskInput):
    """One card's printed characteristics — the classifier's whole input,
    so its cache key changes only when they do."""

    name: str = Field(..., description="Card name (data, never a branch).")
    mana_cost: str = Field("", description="Printed mana cost.")
    type_line: str = Field("", description="Supertypes, types — subtypes.")
    oracle_text: str = Field("", description="Printed oracle text.")

    @classmethod
    def from_mtgjson(cls, card: Mapping[str, Any]) -> "OracleClassifyInput":
        """Build from one MTGJSON card entry."""
        types = list(card.get("types", []))
        subtypes = list(card.get("subtypes", []))
        supertypes = list(card.get("supertypes", []))
        type_line_parts = [" ".join(supertypes + types).strip()]
        if subtypes:
            type_line_parts.append("— " + " ".join(subtypes))
        return cls(
            name=card.get("name", "<unknown>"),
            mana_cost=card.get("manaCost", ""),
            type_line=" ".join(p for p in type_line_parts if p),
            oracle_text=card.get("text", ""),
        )

    def render(self) -> str:
        return (
            f"name: {self.name}\n"
            f"mana_cost: {self.mana_cost}\n"
            f"types: {self.type_line}\n"
            f"oracle_text: {self.oracle_text}\n"
        )


# ─── decision_scorer ────────────────────────────────────────────────


class DecisionScorerInput(TaskInput):
    """One `(archetype, context)` pair.  Archetype + context only — never
    a deck name — so two decks of one archetype share an answer (the
    abstraction-contract probe in `tests/test_llm_decision_scorer.py`)."""

    archetype: str = Field(..., description="Lower-case archetype label.")
    context: str = Field(..., description="A `CTX_*` decision context.")

    def render(self) -> str:
        return (
            f"archetype: {self.archetype}\n"
            f"decision_context: {self.context}\n"
        )


# ─── Registry ───────────────────────────────────────────────────────


TASK_INPUT_TYPES: Dict[str, type] = {
    "synth_gameplan":      SynthGameplanInput,
    "diagnose_replay":     DiagnoseReplayInput,
    "audit_doc_freshness": DocFreshnessInput,
    "handler_audit":       HandlerAuditInput,
    "failing_test_spec":   FailingTestSpecInput,
    "classify_oracle":     OracleClassifyInput,
    "decision_scorer":     DecisionScorerInput,
}
"""Task → its input model.  Every `ai.llm_models.LLMTask` has one."""


def task_input(task: str, payload: Any) -> TaskInput:
    """Validate `payload` (a dict, e.g. a golden's `input`, or an
    instance) into the task's input model.  Dicts go through JSON so
    strict mode applies the JSON rules (a list for a list field, a
    nested object for a nested model)."""
    cls = TASK_INPUT_TYPES[task]
    if isinstance(payload, cls):
        return payload
    return cls.model_validate_json(json.dumps(payload))


def render_task_prompt(task: str, payload: Any) -> str:
    """The user prompt the model sees for `payload` on `task`."""
    return task_input(task, payload).render()


__all__ = [
    "TaskInput",
    "DecklistCardRow",
    "SynthGameplanInput",
    "DiagnoseReplayInput",
    "SiblingDoc",
    "DocFreshnessInput",
    "HandlerAuditInput",
    "FailingTestSpecInput",
    "OracleClassifyInput",
    "DecisionScorerInput",
    "TASK_INPUT_TYPES",
    "rule_test_file",
    "task_input",
    "render_task_prompt",
]
