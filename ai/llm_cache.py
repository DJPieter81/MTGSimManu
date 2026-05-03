"""SQLite-backed LLM response cache.

Phase I-1 of the cost-aware LLM strategy.  This module is tier 2 of
the resolution hierarchy:

    1. Rule-based heuristic    free, deterministic
    2. Persistent cache hit    free, identical input already seen   <-- HERE
    3. Embedding retrieval     ~$0.02/M tokens
    4. Haiku 4.5 + few-shot    default model
    5. Sonnet 4.6              only on schema-validation failure
    6. Opus 4.7                only on human-flagged hard cases

Re-running an LLM tool on identical input must be a free cache hit,
never a paid API call.  Cache entries are keyed by
`(task, model, prompt_version, input_hash)` so:

  * bumping the prompt version invalidates old responses.
  * switching the model invalidates old responses.
  * changing the input invalidates old responses.

Cache location: ``cache/llm/responses.sqlite`` (gitignored).

Schema::

    responses(
        cache_key      TEXT PRIMARY KEY,
        task           TEXT NOT NULL,
        model          TEXT NOT NULL,
        prompt_version INTEGER NOT NULL,
        input_hash     TEXT NOT NULL,
        output_json    TEXT NOT NULL,
        output_type    TEXT NOT NULL,
        created_at     TEXT NOT NULL,
        hit_count      INTEGER DEFAULT 0,
        last_hit_at    TEXT
    )

The ``output_type`` is the qualified name (e.g.
``ai.llm_schemas.SynthesizedGameplan``) so callers can reconstruct the
right pydantic class on retrieval.  Round-trip is via
``BaseModel.model_dump_json`` / ``BaseModel.model_validate_json``.

This module never mutates ``CACHE_DB`` outside of the documented
operations: insert (idempotent), increment-on-hit, and delete (test
isolation).  No schema migrations live here yet — when the schema
needs to change, the path forward is a new column with a default and
an additive migration in this module.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, TypeVar

from pydantic import BaseModel


# ─── Cache location ──────────────────────────────────────────────────
#
# Module-level constants so tests can monkeypatch them onto a tmp
# directory.  Production callers never need to override these.

CACHE_DIR = Path("cache/llm")
"""Directory holding the SQLite cache file.  Gitignored."""

CACHE_DB = CACHE_DIR / "responses.sqlite"
"""SQLite file backing the response cache.  Created on first use."""


T = TypeVar("T", bound=BaseModel)


# ─── Hashing ─────────────────────────────────────────────────────────


def _input_hash(payload: dict) -> str:
    """Deterministic SHA-256 of a JSON-serializable input dict.

    ``sort_keys=True`` makes the hash invariant under dict insertion
    order — a critical property for cache keys, since two semantically
    identical inputs that happened to be built in different orders
    must collide on the same key.

    ``default=str`` is a defensive fallback for stray non-JSON-native
    values (e.g. ``datetime``) that may show up inside payloads; in
    the steady state, callers should pass plain JSON-native dicts.
    """
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def cache_key(
    task: str,
    model: str,
    prompt_version: int,
    input_obj: BaseModel | dict | str,
) -> str:
    """Build the cache key for a ``(task, model, prompt_version, input)``
    tuple.

    The ``input_obj`` may be:
      * a pydantic ``BaseModel`` — serialized via ``model_dump()``;
      * a plain ``dict`` — used directly;
      * a ``str`` — wrapped as ``{"_raw_string_input": <s>}`` so the
        hash differs from a dict that happens to contain the string.

    The wrapping for strings is intentional: the
    ``Agent.run_sync(user_prompt)`` API frequently takes a single
    formatted-string prompt, and we want those calls to cache without
    callers having to wrap the prompt themselves.
    """
    if isinstance(input_obj, BaseModel):
        payload: Any = input_obj.model_dump()
    elif isinstance(input_obj, dict):
        payload = input_obj
    else:
        payload = {"_raw_string_input": input_obj}
    return _input_hash(
        {
            "task": task,
            "model": model,
            "prompt_version": prompt_version,
            "input": payload,
        }
    )


# ─── Connection management ──────────────────────────────────────────


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """Context-managed SQLite connection.

    Creates the cache directory + ``responses`` table on first use
    (idempotent — ``CREATE TABLE IF NOT EXISTS``).  Commits on clean
    exit; closes unconditionally.  Callers should not hold the
    connection longer than the work that needs it — every public
    function in this module opens its own ``_connection`` so test-time
    monkeypatching of ``CACHE_DB`` to a tmp file is reliable.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS responses (
                cache_key TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_version INTEGER NOT NULL,
                input_hash TEXT NOT NULL,
                output_json TEXT NOT NULL,
                output_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                hit_count INTEGER DEFAULT 0,
                last_hit_at TEXT
            )
            """
        )
        yield conn
        conn.commit()
    finally:
        conn.close()


def _utcnow_iso() -> str:
    """ISO-8601 UTC timestamp string used for ``created_at`` /
    ``last_hit_at`` columns.  Centralized so tests can monkeypatch one
    function rather than chasing two call-sites."""
    return datetime.now(timezone.utc).isoformat()


# ─── Public API: get / store ────────────────────────────────────────


def get_cached(cache_key: str, output_cls: type[T]) -> Optional[T]:
    """Look up a cached response by ``cache_key``.

    Returns the parsed ``output_cls`` instance on hit (and increments
    ``hit_count`` + updates ``last_hit_at``), or ``None`` on miss.

    Validation uses ``model_validate_json`` so the round-trip is
    type-checked: a stale cache row whose schema has since changed
    raises ``pydantic.ValidationError`` rather than silently returning
    bad data.
    """
    with _connection() as conn:
        row = conn.execute(
            "SELECT output_json FROM responses WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE responses "
            "SET hit_count = hit_count + 1, last_hit_at = ? "
            "WHERE cache_key = ?",
            (_utcnow_iso(), cache_key),
        )
        return output_cls.model_validate_json(row[0])


def store(
    cache_key: str,
    *,
    task: str,
    model: str,
    prompt_version: int,
    input_hash: str,
    output: BaseModel,
) -> None:
    """Persist a model output, idempotently.

    Uses ``INSERT … ON CONFLICT(cache_key) DO NOTHING`` so calling
    ``store`` twice with the same key + content is a no-op on the
    second call.  This matches the cache-as-memoization contract:
    repeated stores of the same answer must not multiply rows or
    reset hit counters.
    """
    output_type = f"{type(output).__module__}.{type(output).__name__}"
    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO responses
                (cache_key, task, model, prompt_version, input_hash,
                 output_json, output_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO NOTHING
            """,
            (
                cache_key,
                task,
                model,
                prompt_version,
                input_hash,
                output.model_dump_json(),
                output_type,
                _utcnow_iso(),
            ),
        )


# ─── Public API: maintenance + observability ────────────────────────


def clear_cache(*, task: Optional[str] = None) -> int:
    """Delete cache entries.

    If ``task`` is given, only that task's entries are removed; the
    other tasks' caches are untouched.  Returns the count deleted.

    Primary use: per-test isolation (drop just the entries the test
    populated, not the whole cache).  In production, a stale cache is
    cleared with ``clear_cache()`` (no arg) before re-running with a
    bumped ``prompt_version``.
    """
    with _connection() as conn:
        if task is not None:
            cur = conn.execute(
                "DELETE FROM responses WHERE task = ?", (task,)
            )
        else:
            cur = conn.execute("DELETE FROM responses")
        return cur.rowcount


def cache_stats(*, task: Optional[str] = None) -> dict:
    """Aggregate stats over the cache for observability.

    Returns a dict with keys::

        entries:    total rows (filtered by task if given)
        total_hits: sum of hit_count over those rows
        by_model:   {model: row count}
        by_task:    {task: row count}    (always present, useful even
                    when filtered — tells the caller which tasks
                    co-exist in the cache)

    The shape is stable so dashboards can rely on it.  Adding a key
    is a non-breaking change; renaming or removing one is breaking
    and requires a migration note.
    """
    with _connection() as conn:
        where = "WHERE task = ?" if task is not None else ""
        params: tuple = (task,) if task is not None else ()

        entries_row = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(hit_count), 0) "
            f"FROM responses {where}",
            params,
        ).fetchone()
        entries = entries_row[0]
        total_hits = int(entries_row[1] or 0)

        by_model_rows = conn.execute(
            f"SELECT model, COUNT(*) FROM responses {where} GROUP BY model",
            params,
        ).fetchall()
        by_model = {m: c for (m, c) in by_model_rows}

        # by_task is always over the unfiltered table — see docstring.
        by_task_rows = conn.execute(
            "SELECT task, COUNT(*) FROM responses GROUP BY task"
        ).fetchall()
        by_task = {t: c for (t, c) in by_task_rows}

        return {
            "entries": entries,
            "total_hits": total_hits,
            "by_model": by_model,
            "by_task": by_task,
        }


__all__ = [
    "CACHE_DIR",
    "CACHE_DB",
    "cache_key",
    "get_cached",
    "store",
    "clear_cache",
    "cache_stats",
]
