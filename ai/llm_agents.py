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
from typing import Any, Optional, Union, get_args

from pydantic import BaseModel

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


def _build_raw_agent(
    task: LLMTask,
    *,
    model: Optional[str] = None,
    prompt_version: Optional[int] = None,
):
    """Construct an un-cached `pydantic_ai.Agent` for `task`.

    Extracted from `build_agent` so the cache wrapper can call it
    directly without recursing through cache logic.  See
    `build_agent` for the full contract — this function is the body
    of the original `build_agent` from PR #260, unchanged.
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


class _CachedResult:
    """Mimics pydantic-ai's `AgentRunResult` shape for cache hits.

    Only the `.output` field is populated — `usage`, `all_messages`,
    `new_messages`, etc. are not meaningful for a cache hit (no model
    call happened).  Callers that depend on those fields should bypass
    the cache via `build_agent(task, use_cache=False)`.

    This class is intentionally minimal: a richer mock would lie about
    a model call that didn't happen, which is exactly the failure mode
    the cache exists to prevent."""

    __slots__ = ("output",)

    def __init__(self, output: Any) -> None:
        self.output = output


class CachedAgent:
    """Wraps a `pydantic_ai.Agent` with a SQLite cache lookup.

    `run_sync(input)` first computes a cache key from
    `(task, model, prompt_version, input)` and asks
    `ai.llm_cache.get_cached(...)` for a hit.  On hit, the underlying
    agent is never called — the cached pydantic instance is returned
    inside a `_CachedResult` so callers can keep using `.output`.

    On miss, the call falls through to the wrapped agent's
    `run_sync(...)`.  If the result's `.output` is a `BaseModel` (the
    contract for every task in `_OUTPUT_TYPES`), the response is
    persisted before being returned.

    `override(...)` is forwarded to the wrapped agent so test
    fixtures (`agent.override(model=TestModel(...))`) keep working
    unchanged.

    The cache key includes the model identifier as resolved at
    `build_agent` time — when a test overrides the model with
    `TestModel`, the cache key still reflects the originally-resolved
    model.  This matches production semantics: a cache hit means
    "we've answered this exact prompt with this exact configured
    model before"; the override is a test-only mechanism to inject a
    deterministic mock for the same logical configuration."""

    def __init__(
        self,
        agent: Any,
        *,
        task: LLMTask,
        model: str,
        prompt_version: int,
    ) -> None:
        self._agent = agent
        self._task = task
        self._model = model
        self._prompt_version = prompt_version

    @property
    def output_type(self) -> type:
        """Mirror the wrapped agent's output_type for callers that
        introspect it (and for our own cache-lookup decoding)."""
        return self._agent.output_type

    def run_sync(
        self,
        user_prompt: Union[str, BaseModel, dict],
        **kwargs: Any,
    ) -> Any:
        """Cache-aware `run_sync`: lookup → fall through → store."""
        # Local import: keeps `ai/llm_agents` loadable even if the
        # cache module is broken in some downstream environment.
        from ai import llm_cache

        key = llm_cache.cache_key(
            self._task,
            self._model,
            self._prompt_version,
            user_prompt,
        )
        output_cls = self._agent.output_type
        cached = llm_cache.get_cached(key, output_cls)
        if cached is not None:
            return _CachedResult(cached)

        result = self._agent.run_sync(user_prompt, **kwargs)

        if isinstance(result.output, BaseModel):
            # Re-derive the input_hash without `task/model/version` so
            # the column reflects the input alone (useful for cache
            # forensics, e.g. "which inputs produced different outputs
            # under different prompt versions").
            if isinstance(user_prompt, BaseModel):
                input_payload: Any = user_prompt.model_dump()
            elif isinstance(user_prompt, dict):
                input_payload = user_prompt
            else:
                input_payload = {"_raw_string_input": user_prompt}
            input_hash = llm_cache._input_hash({"input": input_payload})

            llm_cache.store(
                key,
                task=self._task,
                model=self._model,
                prompt_version=self._prompt_version,
                input_hash=input_hash,
                output=result.output,
            )
        return result

    def override(self, **kwargs: Any) -> Any:
        """Forward `override(model=...)` to the wrapped agent so the
        test-time TestModel injection pattern continues to work."""
        return self._agent.override(**kwargs)


def build_agent(
    task: LLMTask,
    *,
    model: Optional[str] = None,
    prompt_version: Optional[int] = None,
    use_cache: bool = True,
):
    """Build an LLM agent configured for `task`, optionally cached.

    The returned agent has:
      * its model resolved via `select_model(task, override=model)`.
      * `output_type` set to the schema for the task — pydantic-ai
        forces structured-output validation against this type.
      * its system prompt assembled from the versioned `<task>_v<N>.md`
        file plus optional few-shot examples from
        `<task>_v<N>_fewshot.json`.
      * `defer_model_check=True` so it can be constructed in CI
        without an API key (tests call `agent.override(model=...)`).
      * (default) a cache wrapper that hits `ai.llm_cache` before
        every `run_sync` call — Phase I-1 of the cost-aware strategy.

    Args:
        task: One of the `LLMTask` literal values.
        model: Optional explicit model override (e.g. for tests).
        prompt_version: Optional explicit version (e.g. to pin an
            experiment to v1 while v2 is being trialled).  Defaults
            to the highest version present on disk.
        use_cache: If True (default) the agent is wrapped in a
            `CachedAgent` that consults `ai.llm_cache` before each
            run.  Set False when callers explicitly need a fresh
            model call on every invocation (e.g. statistical
            evaluation).

    Returns:
        A `CachedAgent` (when `use_cache=True`) or a raw
        `pydantic_ai.Agent` (when `use_cache=False`).
    """
    raw = _build_raw_agent(task, model=model, prompt_version=prompt_version)
    if not use_cache:
        return raw
    resolved_model = select_model(task, override=model)
    resolved_version = (
        prompt_version if prompt_version is not None else latest_version(task)
    )
    return CachedAgent(
        raw,
        task=task,
        model=resolved_model,
        prompt_version=resolved_version,
    )


def supported_tasks() -> tuple[str, ...]:
    """Return the supported task literals.  Useful for CLIs and
    tests that want to enumerate every agent."""
    # `LLMTask` is a Literal[...] — `get_args` returns its members.
    return get_args(LLMTask)


__all__ = [
    "build_agent",
    "supported_tasks",
    "CachedAgent",
]
