"""LLMPolicy — local SLM wrapper with deterministic cache.

This module is the integration boundary between MTGSimManu and a
local 7B-class small language model (Qwen 2.5 / Phi-3.5 / Llama
3.2 / Gemma 2). Phase 4C Week 1 deliverable: cache infrastructure
+ structured-output contract + stub backend; Week 2 plugs in
llama.cpp behind a `BackendUnavailable` boundary so the absence
of the real model never breaks the heuristic paths.

Key guarantees:

  1. **Determinism**: cache hits return the exact stored response.
     Same prompt → same answer. Required for matrix-sim
     reproducibility when the SLM is opt-in.
  2. **Schema enforcement**: every call declares a JSON schema or
     pydantic model; non-conforming responses raise
     ``ValidationError`` rather than silently feeding malformed
     data into the engine.
  3. **Graceful degradation**: backend unavailability is a normal
     branch — callers fall back to the heuristic. No SLM call is
     load-bearing for the matrix-sim hot loop.
  4. **Reproducibility cache**: cache files are content-addressable
     by SHA-256 of (model_name, quant, prompt, schema_id). A model
     upgrade gets a fresh namespace; old cached results stay valid
     for old model versions.

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional


class BackendUnavailable(RuntimeError):
    """Raised when the underlying model isn't reachable.

    Callers should catch this and fall back to the heuristic
    path. Never let an SLM unavailability error reach the matrix
    hot loop — the project's offline / commodity-hardware posture
    requires SLM use to be opt-in and gracefully optional.
    """


@dataclass
class LLMResponse:
    """Structured-output response with provenance metadata."""

    parsed: Any
    """The parsed object (matching the schema declared at call
    time). Type varies per caller."""

    raw_text: str
    """The model's raw text output, before schema parsing.
    Useful for debugging schema mismatches."""

    cache_hit: bool
    """True iff the response came from the cache (no model
    invocation). Determinism guarantee: cache hits always replay
    the same parsed object."""

    cache_key: str
    """SHA-256 of (model_name, quant, prompt, schema_id). The
    canonical identity of this response."""


# ─── Backend protocol ─────────────────────────────────────────


class _Backend:
    """Internal protocol every model backend must satisfy.

    A backend takes a prompt + sampling config and returns the raw
    text generation. Schema enforcement and caching live in the
    policy layer, not the backend.
    """

    name: str
    """Stable identifier — used in the cache key. Different
    backends with different parameters MUST have different
    names to avoid cache collisions."""

    def generate(self, prompt: str, max_tokens: int = 256) -> str:
        raise NotImplementedError


@dataclass
class StubBackend:
    """Deterministic in-memory backend for tests and CI.

    Wraps a callable that maps prompt → text. Used to test the
    cache + schema layer without needing the real model. Real
    model integration (llama.cpp) lands in Week 2 behind a
    similar interface.
    """

    name: str = "stub"
    responder: Callable[[str], str] = field(default=lambda p: "")

    def generate(self, prompt: str, max_tokens: int = 256) -> str:
        return self.responder(prompt)


# ─── Policy ─────────────────────────────────────────────────────


@dataclass
class LLMPolicy:
    """Wraps a backend with cache + schema enforcement.

    Construction:

        policy = LLMPolicy(
            backend=StubBackend(name="stub", responder=...),
            cache_dir=Path(".cache/llm_responses"),
        )

    Use:

        resp = policy.generate(
            prompt="Parse this oracle: ...",
            schema_id="oracle_effect_v1",
            parser=lambda raw: json.loads(raw),
        )
        if resp.cache_hit:
            ...  # zero-latency replay
    """

    backend: _Backend
    cache_dir: Optional[Path] = None
    """If set, cache responses to disk under this directory.
    None disables disk persistence (in-memory only)."""

    _memory_cache: Dict[str, LLMResponse] = field(
        default_factory=dict, repr=False
    )
    """Process-local cache. Always populated; disk cache is an
    additional layer below."""

    def __post_init__(self) -> None:
        if self.cache_dir is not None:
            self.cache_dir = Path(self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, prompt: str, schema_id: str) -> str:
        """SHA-256 of (backend.name, schema_id, prompt). Stable
        across runs and machines as long as the backend identity
        stays the same."""
        h = hashlib.sha256()
        h.update(self.backend.name.encode("utf-8"))
        h.update(b"|")
        h.update(schema_id.encode("utf-8"))
        h.update(b"|")
        h.update(prompt.encode("utf-8"))
        return h.hexdigest()

    def _disk_path(self, key: str) -> Optional[Path]:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{key}.json"

    def _load_from_disk(self, key: str) -> Optional[Dict]:
        path = self._disk_path(key)
        if path is None or not path.exists():
            return None
        try:
            with path.open("r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            # Corrupted cache entry — ignore and re-generate.
            return None

    def _save_to_disk(self, key: str, payload: Dict) -> None:
        path = self._disk_path(key)
        if path is None:
            return
        # Atomic write: write to .tmp, then rename.
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp, path)

    def generate(
        self,
        prompt: str,
        schema_id: str,
        parser: Callable[[str], Any],
        max_tokens: int = 256,
    ) -> LLMResponse:
        """Run the backend, parse the result through ``parser``,
        cache the structured output.

        Args:
          prompt: the model input.
          schema_id: a stable name for the output schema (e.g.
            ``"oracle_effect_v1"``). Different schemas under the
            same prompt get separate cache entries.
          parser: ``raw_text -> parsed_object``. Must raise on
            invalid input; the policy catches and re-raises as
            ``ValueError`` so callers see a clean error path.
          max_tokens: budget for the backend's generation.

        Returns: ``LLMResponse`` with ``cache_hit=True`` if the
        result came from cache, else False.
        """
        key = self._cache_key(prompt, schema_id)

        # Memory cache hit?
        if key in self._memory_cache:
            cached = self._memory_cache[key]
            return LLMResponse(
                parsed=cached.parsed, raw_text=cached.raw_text,
                cache_hit=True, cache_key=key,
            )

        # Disk cache hit?
        on_disk = self._load_from_disk(key)
        if on_disk is not None:
            try:
                parsed = parser(on_disk["raw_text"])
            except Exception as e:
                raise ValueError(
                    f"Cached response failed parser: {e}. "
                    f"Cache key: {key}"
                )
            response = LLMResponse(
                parsed=parsed, raw_text=on_disk["raw_text"],
                cache_hit=True, cache_key=key,
            )
            self._memory_cache[key] = response
            return response

        # Cache miss — invoke backend.
        try:
            raw = self.backend.generate(prompt, max_tokens=max_tokens)
        except Exception as e:
            raise BackendUnavailable(
                f"Backend {self.backend.name!r} failed: {e}"
            ) from e

        try:
            parsed = parser(raw)
        except Exception as e:
            raise ValueError(
                f"Backend {self.backend.name!r} produced output "
                f"that failed schema {schema_id!r}: {e}\n"
                f"Raw output: {raw[:500]}"
            )

        response = LLMResponse(
            parsed=parsed, raw_text=raw, cache_hit=False,
            cache_key=key,
        )
        self._memory_cache[key] = response
        self._save_to_disk(key, {"raw_text": raw, "schema_id": schema_id})
        return response

    def has_cached(self, prompt: str, schema_id: str) -> bool:
        """Quick check: is this prompt already in cache (memory or
        disk)? Useful for pre-warm and dry-run paths."""
        key = self._cache_key(prompt, schema_id)
        if key in self._memory_cache:
            return True
        return self._disk_path(key) is not None and self._disk_path(key).exists()
