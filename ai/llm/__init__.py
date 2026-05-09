"""SLM-in-VM scaffolding — Phase 4C.

Domain-specific small-language-model layer for MTG decisions where
the regex / categorization parsers saturate (oracle-misread bugs,
sideboard plans against fringe matchups, mulligan corner cases).

Public API:

    from ai.llm.policy import LLMPolicy, BackendUnavailable

The policy wraps a local model with a SHA-256 prompt cache. Cache
hits are deterministic and zero-latency, so opt-in SLM use does not
break matrix-sim reproducibility.

The Phase 4C scope is **not** per-decision hot-loop scoring (too
slow even at 30 tok/s). It is:

  - oracle parsing (replaces engine.oracle_parser regex for cards
    flagged as "needs review")
  - sideboard plan generation (Phase 2A's matcher backed by a
    learned model)
  - mulligan advice (corner-case fallback)

Backend integration is pluggable. The Week-1 deliverable lands the
cache infrastructure with a deterministic in-memory stub backend so
the structured-output contract can be tested without loading
llama.cpp / ggml. Week 2 adds the real backend behind a
``BackendUnavailable`` graceful-fallback boundary.

Reference:
- docs/research/2026-05_phase_4c_slm_scoping.md
- docs/research/2026-05_mtg_ai_landscape.md §5
"""

from ai.llm.policy import (
    BackendUnavailable,
    LLMPolicy,
    LLMResponse,
    StubBackend,
)
from ai.llm.llama_cpp_backend import (
    LlamaCppBackend,
    make_backend_from_env,
)
from ai.llm.oracle_parse import (
    ORACLE_EFFECT_SCHEMA_ID,
    OracleEffect,
    parse_oracle,
)

__all__ = [
    "BackendUnavailable",
    "LLMPolicy",
    "LLMResponse",
    "StubBackend",
    "LlamaCppBackend",
    "make_backend_from_env",
    "ORACLE_EFFECT_SCHEMA_ID",
    "OracleEffect",
    "parse_oracle",
]
