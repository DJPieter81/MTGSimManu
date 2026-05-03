"""Agent factory for every LLM-driven tool in the project.

Phase H of the abstraction-cleanup pass.  This module is the single
entry point for constructing a configured `pydantic_ai.Agent` for any
of the project's LLM tasks.  It composes:

  * `ai.llm_models.select_model` — model identifier resolution.
  * `ai.llm_prompts.load_prompt` / `load_fewshot` — versioned prompt
    files outside Python source.
  * `ai.llm_schemas.*` — strict pydantic v2 output type per task.

The result is one function — `build_agent(task)` — that every
caller (CLI, tests, eval harness) uses to get a configured agent.

Why a factory: each new LLM tool would otherwise re-implement model
selection, prompt-file lookup, output-type wiring, and TestModel
override plumbing.  Centralizing avoids N copies of that
boilerplate and keeps the choice of foundation model swappable from
one place.

`defer_model_check=True` lets the agent be built without an API key
in the environment.  Tests immediately call
`agent.override(model=TestModel(...))` so CI never makes a real API
call.  The eval harness uses the same override mechanism for
TestModel-based smoke runs."""
from __future__ import annotations

import json
from typing import Optional, get_args

from ai.llm_models import LLMTask, select_model
from ai.llm_prompts import latest_version, load_fewshot, load_prompt
from ai.llm_schemas import (
    BugHypothesis,
    DocFreshnessReport,
    FailingTestSpec,
    HandlerGapReport,
    SynthesizedGameplan,
)


# Each task's pydantic-ai output type.  Lists vs. single-objects are
# intentional — `diagnose_replay` returns a ranked list of hypotheses,
# the others return one record per call.
_OUTPUT_TYPES: dict[str, type] = {
    "synth_gameplan":      SynthesizedGameplan,
    "diagnose_replay":     list[BugHypothesis],
    "audit_doc_freshness": DocFreshnessReport,
    "handler_audit":       HandlerGapReport,
    "failing_test_spec":   FailingTestSpec,
}


def _format_fewshot(examples: list[dict]) -> str:
    """Render a list of few-shot example dicts into a Markdown
    sub-section appended to the system prompt.  Each example is a
    JSON-encoded code block so the model parses it as a structured
    record rather than free prose.

    Examples can carry an optional `stem` (the deck or doc this
    example refers to) which is rendered as a sub-heading."""
    blocks: list[str] = []
    for i, ex in enumerate(examples, 1):
        if isinstance(ex, dict) and "stem" in ex:
            heading = f"### Example {i}: {ex['stem']}"
            body = json.dumps(ex.get("gameplan", ex), indent=2, sort_keys=True)
        else:
            heading = f"### Example {i}"
            body = json.dumps(ex, indent=2, sort_keys=True)
        blocks.append(f"{heading}\n\n```json\n{body}\n```")
    return "\n\n".join(blocks)


def build_agent(
    task: LLMTask,
    *,
    model: Optional[str] = None,
    prompt_version: Optional[int] = None,
):
    """Build a `pydantic_ai.Agent` configured for `task`.

    The returned agent has:
      * its model resolved via `select_model(task, override=model)`.
      * `output_type` set to the schema for the task — pydantic-ai
        forces structured-output validation against this type.
      * its system prompt assembled from the versioned `<task>_v<N>.md`
        file plus optional few-shot examples from
        `<task>_v<N>_fewshot.json`.
      * `defer_model_check=True` so it can be constructed in CI
        without an API key (tests call `agent.override(model=...)`).

    Args:
        task: One of the `LLMTask` literal values.
        model: Optional explicit model override (e.g. for tests).
        prompt_version: Optional explicit version (e.g. to pin an
            experiment to v1 while v2 is being trialled).  Defaults
            to the highest version present on disk.

    Returns:
        A built but not-yet-run `pydantic_ai.Agent` instance.
    """
    # Local import keeps this module loadable in environments where
    # pydantic-ai is missing (e.g. schema-only smoke tests).  In
    # production it's a hard dependency (see requirements.txt).
    from pydantic_ai import Agent  # type: ignore

    chosen_model = select_model(task, override=model)
    version = prompt_version if prompt_version is not None else latest_version(task)
    system_prompt = load_prompt(task, version)

    fewshot = load_fewshot(task, version)
    if fewshot:
        system_prompt = (
            system_prompt
            + "\n\n## Examples\n\n"
            + _format_fewshot(fewshot)
        )

    return Agent(
        chosen_model,
        output_type=_OUTPUT_TYPES[task],
        system_prompt=system_prompt,
        defer_model_check=True,
    )


def supported_tasks() -> tuple[str, ...]:
    """Return the supported task literals.  Useful for CLIs and
    tests that want to enumerate every agent."""
    # `LLMTask` is a Literal[...] — `get_args` returns its members.
    return get_args(LLMTask)


__all__ = [
    "build_agent",
    "supported_tasks",
]
