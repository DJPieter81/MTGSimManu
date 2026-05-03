"""LLM call telemetry — append-only JSONL log of every Agent.run_sync.

Phase I-5 of the cost-aware LLM strategy.  Each call to the
pydantic-ai layer appends one record to ``cache/llm/calls.jsonl``
(gitignored).  Records are durable, so cost reports cover the full
historical run; ``tools/llm_cost_report.py`` aggregates them.

Schema::

    {
        "ts": "2026-05-03T18:30:00.123456+00:00",   # ISO-8601 UTC
        "task": "synth_gameplan",
        "model": "anthropic:claude-sonnet-4-6",
        "prompt_version": 1,
        "tokens_in": 3142,
        "tokens_out": 1402,
        "cost_usd": 0.030,
        "cache_hit": false,
        "input_hash": "sha256:...",
        "output_type": "SynthesizedGameplan",
        "duration_ms": 1248,
        "success": true,
        "error": null
    }

For cache hits, ``tokens_in``/``tokens_out``/``cost_usd`` are 0;
``duration_ms`` is still recorded (cache lookup latency).

Why JSONL: append-only, crash-safe, line-oriented for easy
streaming aggregation.  No DB dependency, no service to run, the
file can be inspected with ``tail -f`` or ``jq``.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


METRICS_DIR = Path("cache/llm")
"""Directory holding LLM caches and the call log.  Gitignored."""

METRICS_FILE = METRICS_DIR / "calls.jsonl"
"""Append-only JSONL file — one call record per line."""


# Per-million-tokens pricing (anthropic public, USD).
# Update when pricing changes.  The cost calculator falls back to
# 0.0 if the model name isn't in this table — log warns but doesn't
# crash, so a new model id never breaks the metrics pipeline.
#
# anthropic public pricing — update when changes.
MODEL_PRICING_USD_PER_MTOKEN: dict[str, dict[str, float]] = {
    "anthropic:claude-haiku-4-5":            {"in": 0.25,  "out": 1.25},
    "anthropic:claude-haiku-4-5-20251001":   {"in": 0.25,  "out": 1.25},
    "anthropic:claude-sonnet-4-6":           {"in": 3.0,   "out": 15.0},
    "anthropic:claude-opus-4-7":             {"in": 15.0,  "out": 75.0},
    "anthropic:claude-opus-4-7-1m":          {"in": 15.0,  "out": 75.0},
    # Free local / test models — kept at 0.0 so TestModel-driven
    # smoke runs don't pollute the cost totals.
    "test":                                  {"in": 0.0,   "out": 0.0},
}


def estimate_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    """Cost estimate from the pricing table.

    Returns ``0.0`` if ``model`` is not in
    ``MODEL_PRICING_USD_PER_MTOKEN`` — a missing entry is treated as
    free rather than as an error so unknown model identifiers don't
    crash the metrics pipeline.  When pricing changes, update the
    table and re-run the cost report (records keep their original
    ``cost_usd`` field; aggregation is from records, not re-computed).
    """
    pricing = MODEL_PRICING_USD_PER_MTOKEN.get(model)
    if pricing is None:
        return 0.0
    return (tokens_in * pricing["in"] + tokens_out * pricing["out"]) / 1_000_000.0


def _input_hash_for_metrics(user_prompt: Any) -> str:
    """Stable content hash for the user prompt — used to correlate
    metrics rows with cache entries (Phase I-1).

    Accepts strings, dicts, or any JSON-serialisable shape.  Falls
    back to ``repr()`` for non-JSON inputs so the function is
    crash-free even on exotic prompt objects.
    """
    if isinstance(user_prompt, str):
        material = user_prompt
    else:
        try:
            material = json.dumps(user_prompt, sort_keys=True, default=str)
        except Exception:
            material = repr(user_prompt)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def log_call(
    *,
    task: str,
    model: str,
    prompt_version: int,
    tokens_in: int,
    tokens_out: int,
    cache_hit: bool,
    input_hash: str,
    output_type: str,
    duration_ms: int,
    success: bool,
    error: Optional[str] = None,
) -> None:
    """Append one call record to ``METRICS_FILE``.

    Creates ``METRICS_DIR`` on first use.  Cache hits record
    ``cost_usd=0.0`` regardless of token counts (which are typically 0
    for cache hits anyway).
    """
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "task": task,
        "model": model,
        "prompt_version": prompt_version,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": (
            0.0
            if cache_hit
            else estimate_cost_usd(model, tokens_in, tokens_out)
        ),
        "cache_hit": cache_hit,
        "input_hash": input_hash,
        "output_type": output_type,
        "duration_ms": duration_ms,
        "success": success,
        "error": error,
    }
    with METRICS_FILE.open("a") as f:
        f.write(json.dumps(record) + "\n")


class CallTimer:
    """Context manager for timing + logging a single ``Agent.run_sync``.

    Usage::

        with CallTimer(task="synth_gameplan", model="anthropic:...",
                       prompt_version=1, cache_hit=False,
                       input_hash="sha256:...") as timer:
            result = agent.run_sync(prompt)
            usage = result.usage()
            timer.set_tokens(usage.input_tokens, usage.output_tokens)
            timer.set_output_type(type(result.output).__name__)

    On context exit, ``log_call(...)`` is invoked with the gathered
    fields.  If an exception was raised inside the block, the record
    is still written with ``success=False`` and ``error=str(exc)``.
    The exception is then re-raised — ``CallTimer`` does not swallow
    failures.
    """

    def __init__(
        self,
        *,
        task: str,
        model: str,
        prompt_version: int,
        cache_hit: bool,
        input_hash: str,
    ) -> None:
        self._task = task
        self._model = model
        self._prompt_version = prompt_version
        self._cache_hit = cache_hit
        self._input_hash = input_hash

        self._tokens_in = 0
        self._tokens_out = 0
        self._output_type = ""
        self._t_start: Optional[float] = None
        self._duration_ms: int = 0

    # Setters used by the wrapper after run_sync returns.
    def set_tokens(self, tokens_in: int, tokens_out: int) -> None:
        self._tokens_in = int(tokens_in or 0)
        self._tokens_out = int(tokens_out or 0)

    def set_output_type(self, output_type: str) -> None:
        self._output_type = output_type

    def __enter__(self) -> "CallTimer":
        self._t_start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Always record duration first so timeouts / crashes still log.
        if self._t_start is not None:
            self._duration_ms = int((time.perf_counter() - self._t_start) * 1000)

        success = exc_type is None
        error_msg: Optional[str] = None
        if not success:
            error_msg = f"{exc_type.__name__}: {exc}" if exc is not None else str(exc_type)

        log_call(
            task=self._task,
            model=self._model,
            prompt_version=self._prompt_version,
            tokens_in=self._tokens_in,
            tokens_out=self._tokens_out,
            cache_hit=self._cache_hit,
            input_hash=self._input_hash,
            output_type=self._output_type,
            duration_ms=self._duration_ms,
            success=success,
            error=error_msg,
        )
        # Returning False (or None) re-raises any exception.
        return False


def _parse_ts(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp from a log record.

    Records always use ``datetime.now(timezone.utc).isoformat()``, so
    the format is stable.  This helper exists so call-sites don't
    have to import ``datetime`` just to filter records.
    """
    return datetime.fromisoformat(ts)


def iter_calls(
    *,
    since: Optional[datetime] = None,
    task: Optional[str] = None,
    path: Optional[Path] = None,
) -> Iterator[dict]:
    """Iterate over logged call records from ``METRICS_FILE``.

    Filters:
      * ``since`` — only records with ``ts >= since`` (inclusive).
      * ``task`` — only records whose ``task`` field matches.

    Streaming — does NOT load the full file into memory.  Lines that
    fail to parse are skipped silently (a partially-flushed line at
    the tail of the file should not crash a cost report).

    The optional ``path`` argument is for tests; production code
    leaves it ``None`` to use ``METRICS_FILE``.
    """
    target = path if path is not None else METRICS_FILE
    if not target.exists():
        return
    with target.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if task is not None and record.get("task") != task:
                continue
            if since is not None:
                try:
                    record_ts = _parse_ts(record["ts"])
                except (KeyError, ValueError):
                    continue
                if record_ts < since:
                    continue
            yield record


__all__ = [
    "METRICS_DIR",
    "METRICS_FILE",
    "MODEL_PRICING_USD_PER_MTOKEN",
    "estimate_cost_usd",
    "log_call",
    "CallTimer",
    "iter_calls",
    "_input_hash_for_metrics",
]
