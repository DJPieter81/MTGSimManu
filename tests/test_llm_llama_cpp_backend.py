"""Phase 4C Week 2 — LlamaCppBackend integration smoke tests.

Two tiers of tests:

1. **Always run**: import + instantiation + graceful-failure
   contract. These run on any environment, including CI without
   a model file.

2. **Conditional**: actual inference. Skipped unless
   ``MTG_LLM_MODEL_PATH`` is set and points to a valid GGUF.
   Local developers with a downloaded model can run them via:

       MTG_LLM_MODEL_PATH=/path/to/qwen2.5-0.5b-instruct-q4_k_m.gguf \
           python -m pytest tests/test_llm_llama_cpp_backend.py

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai.llm.llama_cpp_backend import (
    LlamaCppBackend,
    make_backend_from_env,
)
from ai.llm.policy import BackendUnavailable, LLMPolicy


# ─── Tier 1: always-on contract tests ────────────────────────────────


class TestContract:
    def test_module_imports_cleanly(self):
        """Importing the backend must NOT require llama_cpp or a
        model file. Lazy loading is critical for CI."""
        # If we got here, the import succeeded — that's the test.
        assert LlamaCppBackend is not None

    def test_no_model_path_raises_unavailable_on_use(self):
        """A backend with no model_path must raise
        BackendUnavailable when generate() is called, NOT at
        construction (the policy decides whether to invoke; the
        backend stays cheap to construct)."""
        backend = LlamaCppBackend(model_path=None)
        with pytest.raises(BackendUnavailable):
            backend.generate("hello")

    def test_missing_model_file_raises_unavailable(self):
        """A backend pointed at a nonexistent path must raise
        BackendUnavailable with a helpful message."""
        backend = LlamaCppBackend(model_path="/nonexistent/model.gguf")
        with pytest.raises(BackendUnavailable) as exc_info:
            backend.generate("hello")
        assert "Model file not found" in str(exc_info.value)

    def test_make_backend_from_env_returns_none_when_unset(
        self, monkeypatch
    ):
        """``make_backend_from_env`` must return None when the env
        var isn't set, so callers cleanly skip without a try/except."""
        monkeypatch.delenv("MTG_LLM_MODEL_PATH", raising=False)
        assert make_backend_from_env() is None

    def test_make_backend_from_env_builds_when_set(
        self, monkeypatch, tmp_path
    ):
        """When the env var is set, the convenience helper builds a
        backend pointed at it. (We don't actually invoke
        generate() — that would require a real model.)"""
        fake = tmp_path / "fake-model.gguf"
        fake.write_bytes(b"")  # exists, empty file
        monkeypatch.setenv("MTG_LLM_MODEL_PATH", str(fake))
        backend = make_backend_from_env()
        assert backend is not None
        assert backend.model_path == str(fake)

    def test_backend_name_auto_derived_from_path(self, tmp_path):
        """Backend name = llama-{stem}, used in cache-key
        namespacing. Different model files → different cache
        namespaces, so model upgrades don't pollute the old cache."""
        fake = tmp_path / "qwen2.5-0.5b-instruct-q4_k_m.gguf"
        fake.write_bytes(b"")
        backend = LlamaCppBackend(model_path=str(fake))
        assert backend.name == "llama-qwen2.5-0.5b-instruct-q4_k_m"


# ─── Tier 2: conditional integration tests ───────────────────────────


@pytest.mark.skipif(
    not os.environ.get("MTG_LLM_MODEL_PATH"),
    reason=(
        "MTG_LLM_MODEL_PATH not set. Download a GGUF model and set "
        "the env var to enable end-to-end llama_cpp tests."
    ),
)
class TestLiveInference:
    """These tests exercise the real model. Skipped on CI; run
    locally with a downloaded GGUF.

    Acceptance for Week 2 promotion:
      - All tier-2 tests pass on Qwen 2.5 0.5B Instruct Q4_K_M.
      - Same prompt twice produces identical output (greedy
        decode + fixed seed = determinism).
      - The cache layer in LLMPolicy treats the second call as a
        hit (zero-latency replay).
    """

    def test_generates_nonempty_response(self):
        backend = make_backend_from_env()
        assert backend is not None
        text = backend.generate("Reply with just the word OK.", max_tokens=8)
        assert text.strip(), "Backend produced empty output"

    def test_deterministic_under_fixed_seed(self):
        backend = make_backend_from_env()
        assert backend is not None
        # Two calls in the SAME backend instance — sharing the
        # loaded model. Greedy + fixed seed = identical output.
        a = backend.generate("Reply with just the word OK.", max_tokens=8)
        b = backend.generate("Reply with just the word OK.", max_tokens=8)
        assert a == b, (
            f"Greedy decode + fixed seed must be deterministic. "
            f"Got a={a!r}, b={b!r}."
        )

    def test_cache_absorbs_repeated_calls(self, tmp_path):
        """End-to-end: LLMPolicy + LlamaCppBackend. Second call
        must be a cache hit (zero model invocation)."""
        backend = make_backend_from_env()
        assert backend is not None
        policy = LLMPolicy(backend=backend, cache_dir=tmp_path)
        r1 = policy.generate(
            prompt="Reply with just the word OK.",
            schema_id="ok_v1",
            parser=lambda raw: raw.strip(),
            max_tokens=8,
        )
        assert r1.cache_hit is False
        r2 = policy.generate(
            prompt="Reply with just the word OK.",
            schema_id="ok_v1",
            parser=lambda raw: raw.strip(),
            max_tokens=8,
        )
        assert r2.cache_hit is True
        assert r2.parsed == r1.parsed
