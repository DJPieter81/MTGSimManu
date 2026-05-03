"""Per-task default model selection + env-var overrides for LLM tools.

Phase H of the abstraction-cleanup pass.  Every LLM-driven tool in
`ai/llm_agents.py` resolves its model identifier through `select_model`
so operators have one consistent override surface across:

  * `synth_gameplan` — gameplan synthesis (PR #258)
  * `diagnose_replay` — Bo3 replay → ranked bug hypotheses (G-3)
  * `audit_doc_freshness` — docs/ frontmatter staleness check (G-2)
  * `handler_audit` — oracle-vs-handler mode-completeness audit (G-4)
  * `failing_test_spec` — reserved for G-5 (deferred)

Resolution order (`select_model(task, override=...)`):

  1. Explicit `override` argument — used by tests and CLI flags.
  2. `MTG_LLM_MODEL_<TASK_UPPER>` env var — per-task override.
  3. `MTG_LLM_MODEL` env var — global override.
  4. `DEFAULT_MODELS[task]` — built-in default.

The `DEFAULT_MODELS` table picks Sonnet for the open-ended generative
tasks (gameplan synth, replay diagnosis) and Haiku for the more
mechanical extraction tasks (doc freshness, handler audit, failing-
test spec).

Backward compatibility: PR #258 used `MTG_SYNTH_MODEL` as the
single env var.  This module accepts that name for the synth_gameplan
task and emits a one-shot DeprecationWarning when it's read, routing
the operator to the new `MTG_LLM_MODEL_SYNTH_GAMEPLAN` slot.  CLI
behaviour is unchanged."""
from __future__ import annotations

import os
import warnings
from typing import Literal, Optional


LLMTask = Literal[
    "synth_gameplan",
    "diagnose_replay",
    "audit_doc_freshness",
    "handler_audit",
    "failing_test_spec",
]


DEFAULT_MODELS: dict[LLMTask, str] = {
    # Sonnet — open-ended synthesis from oracle text.
    "synth_gameplan":      "anthropic:claude-sonnet-4-6",
    # Sonnet — multi-step reasoning over replay logs.
    "diagnose_replay":     "anthropic:claude-sonnet-4-6",
    # Haiku — schema-shaped extraction from a markdown doc.
    "audit_doc_freshness": "anthropic:claude-haiku-4-5",
    # Haiku — schema-shaped extraction from oracle text + handler.
    "handler_audit":       "anthropic:claude-haiku-4-5",
    # Haiku — pseudocode emission against a fixed schema.
    "failing_test_spec":   "anthropic:claude-haiku-4-5",
}
"""Model defaults per task.  Operators can override via env vars
(see module docstring) or by passing `override=` to `select_model`."""


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
        5. `DEFAULT_MODELS[task]`.
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

    return DEFAULT_MODELS[task]


__all__ = [
    "LLMTask",
    "DEFAULT_MODELS",
    "GLOBAL_ENV",
    "TASK_ENV_FMT",
    "LEGACY_SYNTH_ENV",
    "select_model",
]
