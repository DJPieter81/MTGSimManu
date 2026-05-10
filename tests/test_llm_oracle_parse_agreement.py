"""Phase 4C — oracle-parse agreement acceptance gate.

This is the named gate from
``docs/research/2026-05_phase_4c_slm_scoping.md``:

    | Oracle agreement | tests/test_llm_oracle_parse_agreement.py
    | 200-card corpus  | < 10 sec (cache) / ~10 min (cold) |

The test exercises ``ai.llm.oracle_parse.parse_oracle`` against the
labeled corpus at ``tests/fixtures/oracle_corpus_known_outputs.jsonl``
and asserts agreement on the load-bearing ``primary_effect`` field
≥ 95% (the threshold mandated by the scoping doc, §"Acceptance
gates").

Run modes
---------

1. **Skip (CI default)** — ``llama_cpp`` is not installed, or
   ``MTG_LLM_MODEL_PATH`` is unset, or the GGUF file is missing.
   Test ``pytest.skip``s with a clear reason. CI never carries the
   ~5 GB model dependency, so this is the expected CI path.

2. **Cold (local, first run)** — ``llama_cpp`` installed, GGUF
   reachable, no warm cache. Each fixture row invokes the model
   once; results land in ``CACHE_DIR`` for subsequent runs. Wall
   clock: minutes (per scoping doc, ~10 min for 200 cards on CPU).

3. **Warm (local, repeat run)** — every fixture row hits the
   on-disk cache. Wall clock: seconds (per scoping doc, < 10 s).
   This is the steady-state path for matrix-sim reproducibility.

The cache is the canonical project cache directory
(``.cache/llm_responses``) so the same warm entries serve this
gate, the cache-warm tool (``tools/llm_cache_warm.py``), and any
production callers. Do NOT redirect to ``tmp_path`` — that would
make every run a cold run.

Why this is separate from ``test_oracle_corpus_acceptance.py``
--------------------------------------------------------------

That earlier test exercises the corpus end-to-end through a stub
backend (a tier-1 contract / round-trip test). This file is the
real acceptance gate — it does not use a stub, and it does not
trivially pass; it either skips (no backend) or measures real
model agreement.

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "oracle_corpus_known_outputs.jsonl"
CACHE_DIR = REPO_ROOT / ".cache" / "llm_responses"

# Acceptance threshold from the scoping doc. Non-negotiable; lowering
# this requires updating docs/research/2026-05_phase_4c_slm_scoping.md
# and a corresponding decision-gate review.
AGREEMENT_THRESHOLD = 0.95


def _load_corpus() -> List[dict]:
    """Load the labeled fixture as a list of {name, oracle, expected}."""
    if not CORPUS_PATH.exists():
        return []
    rows: List[dict] = []
    with CORPUS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _backend_or_skip():
    """Build a real ``LlamaCppBackend`` or skip the test cleanly.

    Three skip reasons (in firing order):

    1. ``llama_cpp`` Python package not installed. CI path.
    2. ``MTG_LLM_MODEL_PATH`` env var unset. Local-without-model path.
    3. The GGUF file at ``MTG_LLM_MODEL_PATH`` does not exist on disk.

    Each surfaces a distinct ``pytest.skip`` reason so operators
    can diagnose at a glance.
    """
    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        pytest.skip(
            "llama_cpp not installed — install llama-cpp-python to "
            "run the oracle-parse agreement gate locally. CI is "
            "expected to skip this test."
        )

    model_path = os.environ.get("MTG_LLM_MODEL_PATH")
    if not model_path:
        pytest.skip(
            "MTG_LLM_MODEL_PATH not set — point it at a GGUF model "
            "(e.g. Qwen2.5-7B-Instruct-Q4_K_M.gguf) to run the "
            "agreement gate."
        )

    if not Path(model_path).exists():
        pytest.skip(
            f"Model file not found at MTG_LLM_MODEL_PATH={model_path!r}."
        )

    from ai.llm.llama_cpp_backend import LlamaCppBackend
    return LlamaCppBackend()


# ─── Pre-flight: corpus is non-empty and well-formed ─────────────────


def test_corpus_fixture_present_and_nonempty():
    """The fixture must exist with at least 25 entries before the
    gate is meaningful. (The scoping doc targets 200 cards; the
    current fixture is a starter set that will grow.)"""
    rows = _load_corpus()
    assert rows, (
        f"Fixture {CORPUS_PATH} is empty or missing. The agreement "
        "gate cannot run without a labeled corpus."
    )
    assert len(rows) >= 25, (
        f"Fixture has {len(rows)} entries; gate requires ≥ 25 to "
        "be meaningful. Extend tests/fixtures/oracle_corpus_known_outputs.jsonl."
    )


# ─── The acceptance gate ─────────────────────────────────────────────


def test_oracle_parse_agreement_meets_threshold():
    """Run ``parse_oracle`` against every fixture row, measure
    agreement on ``primary_effect``, assert ≥ 95%.

    Determinism: ``LlamaCppBackend.generate`` uses
    ``temperature=0`` + ``top_k=1`` + fixed seed (greedy decode),
    so the same prompt always yields the same output. The
    ``LLMPolicy`` SHA-256 cache absorbs repeated calls; after the
    first cold run, every subsequent invocation is < 10 sec.

    Skips cleanly if the backend is unavailable; CI never sees
    this test pass or fail — only skip.
    """
    backend = _backend_or_skip()

    # Late import: keep the file importable on environments without
    # the policy module's optional dependencies (mirrors the
    # backend's own lazy-import pattern).
    from ai.llm.oracle_parse import parse_oracle
    from ai.llm.policy import LLMPolicy

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    policy = LLMPolicy(backend=backend, cache_dir=CACHE_DIR)

    rows = _load_corpus()
    assert rows, "Corpus empty — pre-flight test should have caught this."

    matches = 0
    mismatches: List[tuple] = []
    for row in rows:
        name = row.get("name", "?")
        oracle = row.get("oracle", "")
        expected_primary = row["expected"]["primary_effect"]
        try:
            parsed = parse_oracle(oracle, policy)
        except Exception as e:  # pragma: no cover — defensive
            mismatches.append((name, "parse_error", repr(e)))
            continue
        if parsed.primary_effect == expected_primary:
            matches += 1
        else:
            mismatches.append((name, parsed.primary_effect, expected_primary))

    rate = matches / len(rows)
    # Print agreement so a passing run still surfaces the number.
    print(
        f"\nOracle-parse agreement: {rate:.2%} "
        f"({matches}/{len(rows)}) — threshold {AGREEMENT_THRESHOLD:.0%}"
    )
    if mismatches:
        print(f"First 5 mismatches: {mismatches[:5]}")

    assert rate >= AGREEMENT_THRESHOLD, (
        f"Oracle-parse agreement {rate:.2%} ({matches}/{len(rows)}) "
        f"below {AGREEMENT_THRESHOLD:.0%} acceptance gate. "
        f"First 10 mismatches: {mismatches[:10]}"
    )
