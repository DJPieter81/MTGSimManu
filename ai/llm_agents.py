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
from typing import Any, Optional, get_args

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


class MeteredAgent:
    """Wraps a ``pydantic_ai.Agent`` so every ``run_sync`` call is
    logged via ``ai.llm_metrics`` (Phase I-5).

    Why a wrapper instead of a subclass: pydantic-ai's ``Agent`` is a
    concrete class with internal state we don't want to subclass —
    composition keeps us decoupled from upstream changes.  When the
    SQLite cache (Phase I-1) lands, a ``CachedAgent`` wrapper can
    stack on top of (or below) this one without either layer knowing
    about the other.

    The wrapper preserves the agent's public surface: ``run_sync``
    and ``override`` are the only methods exercised by the project's
    callers today.  ``__getattr__`` forwards any other attribute
    access to the wrapped agent so future pydantic-ai features keep
    working.
    """

    def __init__(
        self,
        agent: Any,
        *,
        task: str,
        model: str,
        prompt_version: int,
    ) -> None:
        self._agent = agent
        self._task = task
        self._model = model
        self._prompt_version = prompt_version

    def run_sync(self, user_prompt: Any, **kwargs: Any) -> Any:
        """Run the wrapped agent and append a metrics record.

        Token counts are read from ``result.usage()`` (a ``RunUsage``
        object exposing ``input_tokens`` and ``output_tokens``).  If
        the SDK ever returns ``None`` for either field, the timer
        records ``0`` rather than crashing.
        """
        # Local import: keeps `ai.llm_agents` importable in
        # environments where `ai.llm_metrics` was not installed
        # (currently always present, but the same pattern is used
        # for the pydantic-ai import itself).
        from ai.llm_metrics import CallTimer, _input_hash_for_metrics

        input_hash = _input_hash_for_metrics(user_prompt)
        with CallTimer(
            task=self._task,
            model=self._model,
            prompt_version=self._prompt_version,
            # I-1 will toggle this when a cache layer wraps this class.
            cache_hit=False,
            input_hash=input_hash,
        ) as timer:
            result = self._agent.run_sync(user_prompt, **kwargs)
            try:
                usage = result.usage()
                tokens_in = getattr(usage, "input_tokens", 0) or 0
                tokens_out = getattr(usage, "output_tokens", 0) or 0
            except Exception:
                tokens_in = 0
                tokens_out = 0
            timer.set_tokens(tokens_in, tokens_out)
            timer.set_output_type(type(result.output).__name__)
            return result

    def override(self, **kwargs: Any) -> Any:
        """Pass-through to ``Agent.override`` — used by tests to swap
        in ``TestModel`` for offline runs."""
        return self._agent.override(**kwargs)

    def __getattr__(self, name: str) -> Any:
        # ``__getattr__`` is only called when normal lookup fails,
        # so it cleanly forwards anything we haven't shadowed above.
        return getattr(self._agent, name)


def _build_raw_agent(
    task: LLMTask,
    *,
    model: Optional[str] = None,
    prompt_version: Optional[int] = None,
):
    """Construct the underlying ``pydantic_ai.Agent`` without any
    instrumentation.  Split out so ``MeteredAgent`` can wrap a
    fresh instance and tests can request the raw object via
    ``build_agent(..., instrument=False)``.
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


def build_agent(
    task: LLMTask,
    *,
    model: Optional[str] = None,
    prompt_version: Optional[int] = None,
    instrument: bool = True,
):
    """Build a ``pydantic_ai.Agent`` configured for ``task``.

    The returned agent has:
      * its model resolved via ``select_model(task, override=model)``.
      * ``output_type`` set to the schema for the task — pydantic-ai
        forces structured-output validation against this type.
      * its system prompt assembled from the versioned
        ``<task>_v<N>.md`` file plus optional few-shot examples from
        ``<task>_v<N>_fewshot.json``.
      * ``defer_model_check=True`` so it can be constructed in CI
        without an API key (tests call ``agent.override(model=...)``).

    By default the agent is wrapped in :class:`MeteredAgent` so every
    ``run_sync`` appends one record to ``cache/llm/calls.jsonl``.
    Pass ``instrument=False`` to receive the raw ``Agent`` (used by
    schema-roundtrip smoke tests that don't need telemetry).

    Args:
        task: One of the ``LLMTask`` literal values.
        model: Optional explicit model override (e.g. for tests).
        prompt_version: Optional explicit version (e.g. to pin an
            experiment to v1 while v2 is being trialled).  Defaults
            to the highest version present on disk.
        instrument: If True (default), wrap the agent so calls are
            metered.  If False, return the raw pydantic-ai agent.

    Returns:
        A built but not-yet-run agent.  When ``instrument=True``,
        this is a :class:`MeteredAgent` that quacks like the raw
        agent for the methods used by callers today.
    """
    raw = _build_raw_agent(task, model=model, prompt_version=prompt_version)
    if not instrument:
        return raw
    chosen_model = select_model(task, override=model)
    version = prompt_version if prompt_version is not None else latest_version(task)
    return MeteredAgent(
        raw,
        task=task,
        model=chosen_model,
        prompt_version=version,
    )


def supported_tasks() -> tuple[str, ...]:
    """Return the supported task literals.  Useful for CLIs and
    tests that want to enumerate every agent."""
    # `LLMTask` is a Literal[...] — `get_args` returns its members.
    return get_args(LLMTask)


__all__ = [
    "build_agent",
    "supported_tasks",
    "MeteredAgent",
]
