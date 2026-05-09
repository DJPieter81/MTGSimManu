"""LlamaCppBackend — local llama.cpp / GGUF model integration.

Phase 4C Week 2 deliverable. Implements the ``_Backend`` protocol
declared in ``ai/llm/policy.py``. Wraps ``llama_cpp.Llama`` with
deterministic-decode defaults and lazy model loading so:

  - The module imports cleanly even when ``llama_cpp`` is absent
    or the model file is missing — callers see
    ``BackendUnavailable`` (per the policy contract).
  - Test suites can skip llama-dependent tests via the
    ``MTG_LLM_MODEL_PATH`` env var.
  - Production deployments instantiate once at session start and
    reuse the loaded model; the SHA-256 cache layer in
    ``LLMPolicy`` then absorbs all repeated calls.

Recommended model (per docs/research/2026-05_phase_4c_slm_scoping.md):
  Qwen2.5-7B-Instruct-Q4_K_M.gguf (~4.5 GB, 25-35 tok/s CPU)

Smaller alternatives for smoke testing:
  Qwen2.5-0.5B-Instruct-Q4_K_M.gguf (~400 MB, 100+ tok/s)
  Qwen2.5-1.5B-Instruct-Q4_K_M.gguf (~900 MB, 60-80 tok/s)
  Phi-3.5-mini-instruct-Q4_K_M.gguf (~2.3 GB, 40-60 tok/s)

Configuration via env var:
  MTG_LLM_MODEL_PATH=/abs/path/to/model.gguf
  MTG_LLM_CONTEXT_TOKENS=4096   (default)
  MTG_LLM_THREADS=auto          (auto = os.cpu_count())

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ai.llm.policy import BackendUnavailable


def _import_llama_cpp():
    """Try to import llama_cpp lazily. Raises BackendUnavailable
    with a helpful message rather than letting ImportError bubble
    up unhelpfully from inside the backend."""
    try:
        import llama_cpp  # type: ignore
    except ImportError as e:
        raise BackendUnavailable(
            "llama_cpp is not installed. Install with: "
            "pip install llama-cpp-python"
        ) from e
    return llama_cpp


@dataclass
class LlamaCppBackend:
    """A local llama.cpp backend with deterministic decode.

    Attributes:
      model_path: absolute path to a GGUF model file. If None,
        looked up from ``MTG_LLM_MODEL_PATH`` env var.
      context_tokens: max context window. 4096 is enough for the
        oracle-parsing and SB-advisor schemas; raise for richer
        prompts.
      threads: CPU thread count for inference. None = auto.
      seed: llama.cpp inference seed. Pinned for reproducibility
        (matrix-sim must be deterministic).
      name: backend identity for cache-key namespacing. Format:
        ``llama-{model-basename}-q{quant}``. Auto-derived if not
        set explicitly.
    """

    model_path: Optional[str] = None
    context_tokens: int = 4096
    threads: Optional[int] = None
    seed: int = 0
    name: str = ""
    _llm: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.model_path is None:
            self.model_path = os.environ.get("MTG_LLM_MODEL_PATH")
        if self.threads is None:
            env_threads = os.environ.get("MTG_LLM_THREADS")
            if env_threads and env_threads != "auto":
                self.threads = int(env_threads)
        if not self.name and self.model_path:
            base = Path(self.model_path).stem
            self.name = f"llama-{base}"

    def _ensure_loaded(self) -> None:
        if self._llm is not None:
            return
        if not self.model_path:
            raise BackendUnavailable(
                "LlamaCppBackend.model_path is not set. Provide a "
                "GGUF file path or set MTG_LLM_MODEL_PATH env var."
            )
        if not Path(self.model_path).exists():
            raise BackendUnavailable(
                f"Model file not found: {self.model_path}. Download "
                "a GGUF model (e.g. Qwen2.5-7B-Instruct-Q4_K_M.gguf) "
                "from HuggingFace and point MTG_LLM_MODEL_PATH at it."
            )
        llama_cpp = _import_llama_cpp()
        self._llm = llama_cpp.Llama(
            model_path=self.model_path,
            n_ctx=self.context_tokens,
            n_threads=self.threads,
            seed=self.seed,
            verbose=False,
        )

    def generate(self, prompt: str, max_tokens: int = 256) -> str:
        """Run greedy decode (temperature=0) and return raw text.

        Determinism: greedy decode + fixed seed = identical output
        across invocations. The ``LLMPolicy`` cache layer absorbs
        all repeated calls, so this method is invoked once per
        unique prompt per session.
        """
        self._ensure_loaded()
        result = self._llm(
            prompt,
            max_tokens=max_tokens,
            temperature=0.0,
            top_k=1,
            top_p=1.0,
            seed=self.seed,
            echo=False,
        )
        # llama_cpp returns OpenAI-compatible dict.
        choices = result.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("text", "")


def make_backend_from_env() -> Optional[LlamaCppBackend]:
    """Convenience: build a backend from environment variables.

    Returns None if ``MTG_LLM_MODEL_PATH`` is unset (allows callers
    to silently skip when no model is configured). Tests use this
    pattern to skip llama-dependent assertions.
    """
    if not os.environ.get("MTG_LLM_MODEL_PATH"):
        return None
    return LlamaCppBackend()
