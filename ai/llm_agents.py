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
TestModel-based smoke runs.

Wrapper composition (Phase I-1 cache + Phase I-5 metrics):
``build_agent`` produces a stack ``MeteredAgent(CachedAgent(raw))``
by default.  The order is deliberate — metrics wraps cache so that:

  * cache hits are still observed by the metrics layer and logged
    with ``cache_hit=True, tokens_in=0, tokens_out=0, cost_usd=0`` —
    the cost report can therefore show how much the cache saved.
  * cache misses fall through to the underlying API call and the
    metrics layer times the real network round-trip, including any
    retries pydantic-ai performs internally.

Either flag can be disabled independently via ``use_cache=False`` /
``instrument=False`` — useful for statistical eval runs that need
fresh model calls and for schema-roundtrip smoke tests that don't
care about telemetry.
"""
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
    """Construct the underlying ``pydantic_ai.Agent`` without any
    instrumentation.

    Split out so the cache + metrics wrappers in ``build_agent`` can
    each wrap a fresh instance, and so tests can request the raw
    object via ``build_agent(..., use_cache=False, instrument=False)``.
    The body of this function is the original ``build_agent`` from
    PR #260 — it is deliberately kept free of cache/metrics concerns.
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
    the cache exists to prevent.

    The metrics layer (:class:`MeteredAgent`) checks ``isinstance(...,
    _CachedResult)`` after the inner ``run_sync`` to decide whether
    to log the call as a cache hit (0 tokens, 0 cost) or a paid API
    miss.  That coupling is intentional — a single sentinel type is
    cleaner than a flag flag attribute on ``CachedAgent`` that
    callers would have to remember to read at the right moment.
    """

    __slots__ = ("output",)

    def __init__(self, output: Any) -> None:
        self.output = output

    def usage(self) -> Any:
        """Return a zero-token usage stub for callers that introspect
        usage on a cache hit.  Mirrors the shape of pydantic-ai's
        ``RunUsage`` (``input_tokens`` + ``output_tokens``).  No call
        was made, so both are 0.
        """
        class _ZeroUsage:
            input_tokens = 0
            output_tokens = 0

        return _ZeroUsage()


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


class MeteredAgent:
    """Wraps a ``pydantic_ai.Agent`` (or a :class:`CachedAgent`) so
    every ``run_sync`` call is logged via ``ai.llm_metrics`` (Phase
    I-5).

    Why a wrapper instead of a subclass: pydantic-ai's ``Agent`` is a
    concrete class with internal state we don't want to subclass —
    composition keeps us decoupled from upstream changes.  The same
    wrapper composes cleanly on top of :class:`CachedAgent`: when the
    inner ``run_sync`` returns a :class:`_CachedResult`, the metrics
    layer logs the call as ``cache_hit=True, tokens_in=0,
    tokens_out=0, cost_usd=0``.

    The wrapper preserves the agent's public surface: ``run_sync``
    and ``override`` are the only methods exercised by the project's
    callers today.  ``__getattr__`` forwards any other attribute
    access to the wrapped agent so future pydantic-ai features keep
    working without modifying this class.
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

        Cache-aware behaviour (when stacked on :class:`CachedAgent`):
        the inner call returns a :class:`_CachedResult` on a cache
        hit.  We detect that *after* the call completes and update
        the timer's ``cache_hit`` flag + zero out tokens before the
        record is written.  This keeps the cost report honest — a
        cache hit shows up as ``cost_usd=0`` even if tokens were
        recorded by the model usage object on a miss.
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
            # Default False; flipped to True below if the inner call
            # returned a `_CachedResult` sentinel.
            cache_hit=False,
            input_hash=input_hash,
        ) as timer:
            result = self._agent.run_sync(user_prompt, **kwargs)
            if isinstance(result, _CachedResult):
                # Cache hit — no API call happened.  Record 0 tokens
                # and flip the cache_hit flag so the cost calculator
                # reports $0 for this row.
                timer._cache_hit = True
                timer.set_tokens(0, 0)
            else:
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
        """Pass-through to the wrapped agent's ``override`` — used by
        tests to swap in ``TestModel`` for offline runs.  When the
        wrapped agent is a :class:`CachedAgent`, this forwards twice
        (MeteredAgent → CachedAgent → raw Agent), which is correct:
        the test-time model swap must reach the raw pydantic-ai
        agent at the bottom of the stack.
        """
        return self._agent.override(**kwargs)

    def __getattr__(self, name: str) -> Any:
        # ``__getattr__`` is only called when normal lookup fails,
        # so it cleanly forwards anything we haven't shadowed above
        # (e.g. ``output_type``, which both the raw Agent and
        # :class:`CachedAgent` expose).
        return getattr(self._agent, name)


def build_agent(
    task: LLMTask,
    *,
    model: Optional[str] = None,
    prompt_version: Optional[int] = None,
    use_cache: bool = True,
    instrument: bool = True,
):
    """Build an LLM agent configured for `task`, optionally cached
    and instrumented.

    The returned agent has:
      * its model resolved via `select_model(task, override=model)`.
      * `output_type` set to the schema for the task — pydantic-ai
        forces structured-output validation against this type.
      * its system prompt assembled from the versioned `<task>_v<N>.md`
        file plus optional few-shot examples from
        `<task>_v<N>_fewshot.json`.
      * `defer_model_check=True` so it can be constructed in CI
        without an API key (tests call `agent.override(model=...)`).
      * (default) a :class:`CachedAgent` wrapper that hits
        ``ai.llm_cache`` before every ``run_sync`` call — Phase I-1
        of the cost-aware strategy.
      * (default) a :class:`MeteredAgent` wrapper outside the cache
        wrapper that appends one record to ``cache/llm/calls.jsonl``
        per ``run_sync`` — Phase I-5 of the cost-aware strategy.

    The wrapper order is ``MeteredAgent(CachedAgent(raw))``: cache
    hits are seen by the metrics layer and logged as zero-cost rows
    (``cache_hit=True, tokens_in=0, tokens_out=0``) so the cost
    report can quantify cache savings without double-counting.

    Args:
        task: One of the `LLMTask` literal values.
        model: Optional explicit model override (e.g. for tests).
        prompt_version: Optional explicit version (e.g. to pin an
            experiment to v1 while v2 is being trialled).  Defaults
            to the highest version present on disk.
        use_cache: If True (default) the agent is wrapped in a
            ``CachedAgent`` that consults ``ai.llm_cache`` before
            each run.  Set False when callers explicitly need a
            fresh model call on every invocation (e.g. statistical
            evaluation).
        instrument: If True (default), wrap the (possibly cached)
            agent so calls are metered.  Set False to receive the
            raw / cache-only agent (used by schema-roundtrip smoke
            tests that don't need telemetry, and by the cache test
            suite to avoid polluting ``cache/llm/calls.jsonl``).

    Returns:
        A built but not-yet-run agent.  Concrete return type depends
        on the flags::

            instrument=True,  use_cache=True   → MeteredAgent(CachedAgent(raw))
            instrument=True,  use_cache=False  → MeteredAgent(raw)
            instrument=False, use_cache=True   → CachedAgent(raw)
            instrument=False, use_cache=False  → raw pydantic-ai Agent

        All four shapes implement ``run_sync(...)`` and
        ``override(...)`` identically from the caller's perspective.
    """
    raw = _build_raw_agent(task, model=model, prompt_version=prompt_version)
    resolved_model = select_model(task, override=model)
    resolved_version = (
        prompt_version if prompt_version is not None else latest_version(task)
    )

    agent: Any = raw
    if use_cache:
        agent = CachedAgent(
            agent,
            task=task,
            model=resolved_model,
            prompt_version=resolved_version,
        )
    if instrument:
        agent = MeteredAgent(
            agent,
            task=task,
            model=resolved_model,
            prompt_version=resolved_version,
        )
    return agent


def supported_tasks() -> tuple[str, ...]:
    """Return the supported task literals.  Useful for CLIs and
    tests that want to enumerate every agent."""
    # `LLMTask` is a Literal[...] — `get_args` returns its members.
    return get_args(LLMTask)


__all__ = [
    "build_agent",
    "supported_tasks",
    "CachedAgent",
    "MeteredAgent",
]
