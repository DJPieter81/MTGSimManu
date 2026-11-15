"""Per-task default model selection + env-var overrides for LLM tools.

Phase H of the abstraction-cleanup pass.  Every LLM-driven tool in
`ai/llm_agents.py` resolves its model identifier through `select_model`
so operators have one consistent override surface across:

  * `synth_gameplan` — gameplan synthesis (PR #258)
  * `diagnose_replay` — Bo3 replay → ranked bug hypotheses (G-3)
  * `audit_doc_freshness` — docs/ frontmatter staleness check (G-2)
  * `handler_audit` — oracle-vs-handler mode-completeness audit (G-4)
  * `failing_test_spec` — hypothesis → rule-phrased failing test (G-5)
  * `classify_oracle` — oracle text → closed-set mechanic tags (W0-A)
  * `decision_scorer` — (archetype, context) → scaling weight (Phase 1)

Resolution order (`select_model(task, override=...)`):

  1. Explicit `override` argument — used by tests and CLI flags.
  2. `MTG_LLM_MODEL_<TASK_UPPER>` env var — per-task override.
  3. `MTG_LLM_MODEL` env var — global override.
  4. (synth_gameplan only) the legacy `MTG_SYNTH_MODEL` env var.
  5. `DEFAULT_MODELS[task]` — built-in default, unless its provider's
     client SDK is not importable here, in which case
     `FALLBACK_MODELS[task]` (see "Provider fallback" below).

Tier rationale (2026-11 refresh — the table below carries the
identifiers; this text names tiers only):

  * **Opus tier — the difficult decisions.**  `diagnose_replay`
    (causal reasoning over a long Bo3 log: which subsystem diverged
    from correct play, and why), `synth_gameplan` (a multi-constraint
    strategy for a whole 60-card list), `failing_test_spec` (turning a
    hypothesis into a *rule-phrased* red test — the abstraction
    contract's hardest step, since naming the mechanic instead of the
    card is exactly where weaker answers fail) and `handler_audit`
    (reading a Python handler against printed oracle modes; a missed
    fabricated mode is a silent rules bug).  Each runs a handful of
    times per session, offline, so per-call quality dominates cost.
    The current Opus tier lists below the previous Opus generation,
    so the upgrade is cheaper per token, not dearer.
  * **Sonnet tier — `classify_oracle`.**  Closed-set tagging, but a
    wrong tag distorts AI scoring across every sim until the next
    audit (the 2026-07-05 impulse-draw incident class, and the
    2026-10-02 decision-model A/B that a single false tag sank).  The
    output is committed and trusted for weeks, so it sits one tier
    above pure extraction.
  * **Haiku tier — `audit_doc_freshness`.**  Schema-shaped extraction
    from frontmatter plus a short body; low stakes (the tool only
    proposes a frontmatter edit) and run over every active doc.
  * **Decision model — `decision_scorer`.**  One typed answer per
    `(archetype, context)` pair: the native shape of a decision model
    (TypeSafe's Jev), which answers typed questions with calibrated
    confidence instead of writing text, at a fraction of a language
    model's price.  Its decision shape is
    `ai.llm_schemas.decision_weight_decision_model` (a pick from the
    weight grid); `ai.llm_agents.output_type_for` asks it there.
    `classify_oracle` also has a decision shape but stays on the
    Sonnet tier: the 2026-10-02 same-seed A/B of decision-model tags
    was rejected (`docs/design/rules-foundation-sweep-tracker.md`).
    Route it to the decision model per run with
    `MTG_LLM_MODEL_CLASSIFY_ORACLE` when re-measuring.

This layer passes no sampling parameters and no thinking config
(`ai.llm_agents._build_raw_agent` passes only model, output_type and
system_prompt), which is what the current Anthropic tiers require:
they reject non-default sampling params and run adaptive thinking
when it is omitted.

Provider fallback: a default whose provider client SDK cannot be
imported (`PROVIDER_SDK_MODULES`) resolves to `FALLBACK_MODELS[task]`
instead, so a checkout without the decision-model SDK still has a
working, priced model for every task.  Explicit overrides and env
vars are never rewritten — an operator who names a model gets it.

Determinism: the sim never depends on this table.  With
`MTG_LLM_DECISION_SCORER_OFFLINE=1` (every sim, CI included) the
decision scorer makes no call and reads the committed
`ai/llm_decision_weights.json`, whose rows are keyed by archetype and
context only — not by model — so a default change here cannot orphan
them (pinned by `tests/test_llm_models.py`).  Rows in the gitignored
SQLite response cache are keyed by model string
(`ai.llm_cache.cache_key`); a default change starts those afresh,
which is the intended behaviour for a new model.

Backward compatibility: PR #258 used `MTG_SYNTH_MODEL` as the
single env var.  This module accepts that name for the synth_gameplan
task and emits a one-shot DeprecationWarning when it's read, routing
the operator to the new `MTG_LLM_MODEL_SYNTH_GAMEPLAN` slot.  CLI
behaviour is unchanged."""
from __future__ import annotations

import importlib.util
import os
import warnings
from functools import lru_cache
from typing import Literal, Optional


LLMTask = Literal[
    "synth_gameplan",
    "diagnose_replay",
    "audit_doc_freshness",
    "handler_audit",
    "failing_test_spec",
    "classify_oracle",
    "decision_scorer",
]


DEFAULT_MODELS: dict[LLMTask, str] = {
    # Opus tier — multi-constraint strategy synthesis for a whole list.
    "synth_gameplan":      "anthropic:claude-opus-5-5",
    # Opus tier — causal diagnosis over a long replay log.
    "diagnose_replay":     "anthropic:claude-opus-5-5",
    # Haiku tier — schema-shaped extraction from a markdown doc.
    "audit_doc_freshness": "anthropic:claude-haiku-5-5",
    # Opus tier — reading handler source against printed modes.
    "handler_audit":       "anthropic:claude-opus-5-5",
    # Opus tier — naming the mechanic, not the card, in a red test.
    "failing_test_spec":   "anthropic:claude-opus-5-5",
    # Sonnet tier — closed-set tags that are committed and trusted.
    "classify_oracle":     "anthropic:claude-sonnet-5-5",
    # Decision model — one typed weight per (archetype, context).
    "decision_scorer":     "typesafe:jev-latest",
}
"""Model defaults per task.  Operators can override via env vars
(see module docstring) or by passing `override=` to `select_model`."""


FALLBACK_MODELS: dict[LLMTask, str] = {
    # The committed decision weights were produced on the Sonnet tier;
    # the fallback stays on that tier's current model.
    "decision_scorer":     "anthropic:claude-sonnet-5-5",
}
"""Per-task model used when the default's provider SDK is absent."""


PROVIDER_SDK_MODULES: dict[str, str] = {
    "typesafe:": "typesafe_sdk",
}
"""Model-string provider prefix → the client module that provider
needs.  A provider absent from this table is assumed available (its
client is a hard dependency or is resolved lazily by pydantic-ai)."""


@lru_cache(maxsize=None)
def _module_importable(module: str) -> bool:
    """True when `module` can be imported here.  Cached: the decision
    scorer resolves its model on every lookup inside the sim loop."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        # A module blocked in `sys.modules` (set to None) has no spec.
        return False


def provider_available(model: str) -> bool:
    """True unless `model`'s provider needs a client SDK that is not
    installed (see `PROVIDER_SDK_MODULES`)."""
    for prefix, module in PROVIDER_SDK_MODULES.items():
        if model.startswith(prefix):
            return _module_importable(module)
    return True


GLOBAL_ENV = "MTG_LLM_MODEL"
"""Global env var: applies to every task unless a task-specific var is set."""

TASK_ENV_FMT = "MTG_LLM_MODEL_{task_upper}"
"""Per-task env var template: `MTG_LLM_MODEL_<TASK_UPPER>`."""

# PR #258 used `MTG_SYNTH_MODEL` as the single override.  We keep it
# alive for the synth_gameplan task with a deprecation warning so
# existing scripts and CI configs don't silently start using a
# different model on upgrade.
LEGACY_SYNTH_ENV = "MTG_SYNTH_MODEL"

_LEGACY_WARNED = {"synth_gameplan": False}


def _read_legacy_synth() -> Optional[str]:
    """Return the legacy `MTG_SYNTH_MODEL` value if set, after emitting
    a one-shot DeprecationWarning pointing at the new env-var name."""
    legacy = os.environ.get(LEGACY_SYNTH_ENV)
    if not legacy:
        return None
    if not _LEGACY_WARNED["synth_gameplan"]:
        warnings.warn(
            f"{LEGACY_SYNTH_ENV} is deprecated; use "
            f"{TASK_ENV_FMT.format(task_upper='SYNTH_GAMEPLAN')} or "
            f"{GLOBAL_ENV} instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        _LEGACY_WARNED["synth_gameplan"] = True
    return legacy


def select_model(task: LLMTask, *, override: Optional[str] = None) -> str:
    """Resolve the model identifier for `task`.

    Resolution order:
        1. `override` argument.
        2. `MTG_LLM_MODEL_<TASK_UPPER>` env var.
        3. `MTG_LLM_MODEL` env var.
        4. (synth_gameplan only) `MTG_SYNTH_MODEL` legacy env var,
           which emits a one-shot DeprecationWarning the first time
           it's read.
        5. `DEFAULT_MODELS[task]`, or `FALLBACK_MODELS[task]` when the
           default's provider SDK is not installed.
    """
    if override:
        return override

    task_specific = os.environ.get(TASK_ENV_FMT.format(task_upper=task.upper()))
    if task_specific:
        return task_specific

    glob = os.environ.get(GLOBAL_ENV)
    if glob:
        return glob

    if task == "synth_gameplan":
        legacy = _read_legacy_synth()
        if legacy:
            return legacy

    default = DEFAULT_MODELS[task]
    if task in FALLBACK_MODELS and not provider_available(default):
        return FALLBACK_MODELS[task]
    return default


__all__ = [
    "LLMTask",
    "DEFAULT_MODELS",
    "FALLBACK_MODELS",
    "PROVIDER_SDK_MODULES",
    "provider_available",
    "GLOBAL_ENV",
    "TASK_ENV_FMT",
    "LEGACY_SYNTH_ENV",
    "select_model",
]
