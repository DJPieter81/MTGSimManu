"""LLM cache pre-warm — Phase 4C Week 4 tool.

One-time pre-population of the SHA-256 prompt cache so matrix-sim
runs hit the cache for every (deck, opp) sideboard plan and every
review-flagged oracle parse, never invoking the model in the
hot loop.

Usage:

    # Pre-warm the SB advisor cache for all 16x16 = 256 matchups.
    MTG_LLM_MODEL_PATH=/path/to/qwen.gguf \\
        python -m tools.llm_cache_warm --target sb_advisor

    # Pre-warm the oracle parser for the labeled corpus.
    MTG_LLM_MODEL_PATH=/path/to/qwen.gguf \\
        python -m tools.llm_cache_warm --target oracle_parse

    # Both targets:
    MTG_LLM_MODEL_PATH=/path/to/qwen.gguf \\
        python -m tools.llm_cache_warm --target all

The tool is idempotent — already-cached entries are skipped. A
fresh model upgrade (different ``backend.name``) creates a new
cache namespace; the old cache still works for old runs.

Reference: docs/research/2026-05_phase_4c_slm_scoping.md §
Inference interface.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

# Make the repo root importable when run as ``python -m tools.llm_cache_warm``.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.llm.policy import BackendUnavailable, LLMPolicy, StubBackend


DEFAULT_CACHE_DIR = ROOT / ".cache" / "llm_responses"


def _build_policy(stub: bool) -> LLMPolicy:
    """Build an LLMPolicy. Real backend if MTG_LLM_MODEL_PATH set
    AND not in stub mode; otherwise a deterministic stub.

    Stub mode makes the warm tool a structural smoke test (verify
    we can iterate the corpus and write cache entries) without
    needing a 5GB GGUF on disk.
    """
    if stub or not os.environ.get("MTG_LLM_MODEL_PATH"):
        backend = StubBackend(
            name="stub-warm",
            responder=lambda p: '{"warmed": true}',
        )
    else:
        from ai.llm.llama_cpp_backend import LlamaCppBackend
        backend = LlamaCppBackend()
    return LLMPolicy(backend=backend, cache_dir=DEFAULT_CACHE_DIR)


# ─── Target: SB advisor (16x16 = 256 matchups) ───────────────────────


def _iter_sb_matchups(deck_names: Optional[Iterable[str]] = None):
    """Yield (my_deck, my_sb_dict, opp_deck) tuples for every
    matchup in MODERN_DECKS x MODERN_DECKS."""
    from decks.modern_meta import MODERN_DECKS
    names = list(deck_names or MODERN_DECKS.keys())
    for my in names:
        my_sb = MODERN_DECKS[my].get("sideboard", {})
        if not my_sb:
            continue
        for opp in names:
            if opp == my:
                continue
            yield my, my_sb, opp


def warm_sb_advisor(policy: LLMPolicy,
                    deck_names: Optional[Iterable[str]] = None) -> dict:
    """Pre-warm the SB advisor cache. Returns {warmed, skipped, errors}."""
    from ai.llm.sideboard_advisor import (
        SIDEBOARD_PLAN_SCHEMA_ID, _build_prompt, _parse_response,
    )
    counts = {"warmed": 0, "skipped": 0, "errors": 0, "total": 0}
    for my, my_sb, opp in _iter_sb_matchups(deck_names):
        counts["total"] += 1
        prompt = _build_prompt(my, my_sb, opp)
        if policy.has_cached(prompt, SIDEBOARD_PLAN_SCHEMA_ID):
            counts["skipped"] += 1
            continue
        try:
            policy.generate(
                prompt=prompt,
                schema_id=SIDEBOARD_PLAN_SCHEMA_ID,
                parser=_parse_response,
                max_tokens=400,
            )
            counts["warmed"] += 1
        except (BackendUnavailable, ValueError) as e:
            counts["errors"] += 1
            print(f"  [WARN] {my} vs {opp}: {e}", file=sys.stderr)
    return counts


# ─── Target: oracle parser (labeled corpus) ──────────────────────────


def _iter_oracle_corpus(corpus_path: Optional[Path] = None):
    """Yield oracle texts from the labeled corpus."""
    p = corpus_path or (
        ROOT / "tests" / "fixtures" / "oracle_corpus_known_outputs.jsonl"
    )
    if not p.exists():
        return
    with p.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            yield row.get("name", "?"), row.get("oracle", "")


def warm_oracle_parser(policy: LLMPolicy,
                        corpus_path: Optional[Path] = None) -> dict:
    """Pre-warm the oracle parser cache for every card in the
    labeled corpus."""
    from ai.llm.oracle_parse import (
        ORACLE_EFFECT_SCHEMA_ID, _build_prompt, _parse_json_response,
    )
    counts = {"warmed": 0, "skipped": 0, "errors": 0, "total": 0}
    for name, oracle in _iter_oracle_corpus(corpus_path):
        counts["total"] += 1
        prompt = _build_prompt(oracle)
        if policy.has_cached(prompt, ORACLE_EFFECT_SCHEMA_ID):
            counts["skipped"] += 1
            continue
        try:
            policy.generate(
                prompt=prompt,
                schema_id=ORACLE_EFFECT_SCHEMA_ID,
                parser=_parse_json_response,
                max_tokens=200,
            )
            counts["warmed"] += 1
        except (BackendUnavailable, ValueError) as e:
            counts["errors"] += 1
            print(f"  [WARN] {name}: {e}", file=sys.stderr)
    return counts


# ─── Target: decision_scorer (16 decks × ~8 contexts) ───────────────


def _iter_decision_scorer_contexts(deck_names: Optional[Iterable[str]] = None):
    """Yield ``(archetype, context)`` tuples to warm.

    Phase 1 refactor: the cache key is keyed by
    ``(archetype, decision_context)`` only — the raw deck name does
    NOT participate in the key (see
    ``ai.llm_decision_scorer._cache_input``).  So we enumerate
    distinct archetypes × the registered contexts and skip duplicates.
    """
    from ai import llm_decision_scorer as scorer
    contexts = (
        scorer.CTX_COMBO_FORCE_PAYOFF_STORM_THRESHOLD,
        scorer.CTX_TRON_MANA_ADVANTAGE,
        scorer.CTX_AMULET_TITAN_MANA_BONUS,
        scorer.CTX_CYCLING_CASCADE_BOOST,
        scorer.CTX_CYCLING_GY_URGENCY,
        scorer.CTX_CYCLING_GAMEPLAN_BOOST,
        scorer.CTX_CYCLING_FREE_COST_BONUS,
        scorer.CTX_CASCADE_FREE_SPELL_VALUE,
    )

    # Build the archetype set from the registered deck list when
    # ``deck_names`` is None (default: every Modern deck the project
    # ships with).  Otherwise resolve only the requested decks'
    # archetypes.
    if deck_names is None:
        from ai.strategy_profile import DECK_ARCHETYPES
        archetypes = sorted({a.value for a in DECK_ARCHETYPES.values()})
    else:
        from ai.strategy_profile import DECK_ARCHETYPES, ArchetypeStrategy
        archs = set()
        for d in deck_names:
            a = DECK_ARCHETYPES.get(d)
            if a is not None:
                archs.add(a.value)
        archetypes = sorted(archs)

    # Always include the "*" wildcard for keyword-tied contexts and
    # the "storm" / "cascade" refinements that appear in
    # `DEFAULT_WEIGHTS` even though they aren't in `DECK_ARCHETYPES`.
    extras = ("*", "storm", "cascade")
    for a in archetypes + [e for e in extras if e not in archetypes]:
        for ctx in contexts:
            yield a, ctx


def warm_decision_scorer(deck_names: Optional[Iterable[str]] = None) -> dict:
    """Pre-warm the ``decision_scorer`` cache.

    Uses the pydantic-ai ``build_agent`` stack (separate from the
    ``ai.llm.policy`` system used by ``sb_advisor`` / ``oracle_parse``)
    — see ``ai/llm_agents.py`` and ``ai/llm_decision_scorer.py``.

    If the budget is exhausted or no API key is available, the helper
    falls back to the offline ``DEFAULT_WEIGHTS`` table; the row is
    still recorded as "skipped" (cache miss + fallback) so operators
    see how much would have warmed under a real run.

    Returns ``{warmed, skipped, errors, total}``.
    """
    from ai import llm_decision_scorer
    counts = {"warmed": 0, "skipped": 0, "errors": 0, "total": 0}
    for arch, ctx in _iter_decision_scorer_contexts(deck_names):
        counts["total"] += 1
        # Cache hit short-circuit — if the row is already cached, this
        # is a no-op and we count it as a skip.
        before = llm_decision_scorer._try_cache_only(arch, ctx)
        if before is not None:
            counts["skipped"] += 1
            continue
        try:
            w = llm_decision_scorer.weight(arch, ctx)
            after = llm_decision_scorer._try_cache_only(arch, ctx)
            if after is not None:
                counts["warmed"] += 1
            else:
                # No cache row produced (LLM unreachable, fell back to
                # the offline default table or NEUTRAL_WEIGHT).
                counts["skipped"] += 1
            # Touch `w` so the linter doesn't flag the unused variable
            # — and so operators see the value in verbose mode.
            _ = w
        except Exception as e:
            counts["errors"] += 1
            print(f"  [WARN] {arch} / {ctx}: {e}", file=sys.stderr)
    return counts


# ─── CLI ────────────────────────────────────────────────────────────


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-warm the LLM prompt cache."
    )
    parser.add_argument(
        "--target",
        choices=("sb_advisor", "oracle_parse", "all"),
        default="all",
        help="Which caller's cache to warm (legacy ai.llm.policy stack).",
    )
    parser.add_argument(
        "--task",
        choices=("decision_scorer",),
        default=None,
        help="Which pydantic-ai task to warm.  Currently only "
             "`decision_scorer` (Phase 1 refactor target).  Mutually "
             "exclusive with --target — picking a task warms only the "
             "pydantic-ai cache stack.",
    )
    parser.add_argument(
        "--decks",
        default=None,
        help="Comma-separated subset of deck names to warm (for the "
             "decision_scorer target only).  Default: every registered "
             "Modern deck.",
    )
    parser.add_argument(
        "--stub",
        action="store_true",
        help="Use the StubBackend (no model required) — useful for "
             "verifying the iteration shape without burning model time.",
    )
    args = parser.parse_args(argv)

    overall = {"warmed": 0, "skipped": 0, "errors": 0, "total": 0}
    started = time.time()

    # Phase 1 refactor path — decision_scorer warming on the
    # pydantic-ai stack.  Independent from the legacy ai.llm.policy
    # warming below.
    if args.task == "decision_scorer":
        deck_names = None
        if args.decks:
            deck_names = [s.strip() for s in args.decks.split(",") if s.strip()]
        print("\n→ Warming decision_scorer "
              f"(archetypes × contexts, decks={deck_names or 'ALL'})…")
        counts = warm_decision_scorer(deck_names=deck_names)
        for k, v in counts.items():
            overall[k] += v
        print(f"  warmed={counts['warmed']} "
              f"skipped={counts['skipped']} "
              f"errors={counts['errors']} "
              f"total={counts['total']}")
        elapsed = time.time() - started
        print(f"\nDone in {elapsed:.1f}s. {overall}")
        return 0 if overall["errors"] == 0 else 1

    policy = _build_policy(stub=args.stub)
    print(f"Cache dir: {DEFAULT_CACHE_DIR}")
    print(f"Backend: {policy.backend.name}")

    if args.target in ("sb_advisor", "all"):
        print("\n→ Warming SB advisor (16x16 matchups)…")
        counts = warm_sb_advisor(policy)
        for k, v in counts.items():
            overall[k] += v
        print(f"  warmed={counts['warmed']} "
              f"skipped={counts['skipped']} "
              f"errors={counts['errors']} "
              f"total={counts['total']}")

    if args.target in ("oracle_parse", "all"):
        print("\n→ Warming oracle parser (labeled corpus)…")
        counts = warm_oracle_parser(policy)
        for k, v in counts.items():
            overall[k] += v
        print(f"  warmed={counts['warmed']} "
              f"skipped={counts['skipped']} "
              f"errors={counts['errors']} "
              f"total={counts['total']}")

    elapsed = time.time() - started
    print(f"\nDone in {elapsed:.1f}s. {overall}")
    return 0 if overall["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
