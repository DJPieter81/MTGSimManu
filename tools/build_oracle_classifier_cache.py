"""Offline builder for the oracle-tag classifier cache (W0-A).

Reads `ModernAtomic.json` (or the bundled parts), classifies each
card's oracle text via the `classify_oracle` LLM agent, and writes
the result to `decks/gameplans/_oracle_classifier.json`.

This script is the ONLY component allowed to call the LLM for tag
classification.  Engine and AI consumers read the committed JSON via
`ai/oracle_classifier.py` — zero runtime LLM calls.

Cost profile (Haiku 4.5, May 2026 rates):
  * Per card: ~$0.005 (200-300 input tokens, 50-100 output tokens).
  * Smoke (10 cards): ~$0.05.
  * Full (~21k cards): ~$10-20.  Hard-capped at $20 via
    `ai.llm_budgets`; can be raised via `--budget-cap-usd`.

Cache semantics:
  * Per-call responses go through `ai.llm_cache` (SHA-256 keyed by
    task + model + prompt_version + input).  Re-running with the
    same prompt is FREE.
  * Per-card entries in the JSON are content-addressed by the
    card's oracle text (sha256).  A stale entry (oracle text drift)
    is logged, never silently served.

CLI:

    # 10-card smoke (committed to repo as the bootstrap cache)
    python tools/build_oracle_classifier_cache.py --smoke

    # Specific card set (for incremental rebuilds)
    python tools/build_oracle_classifier_cache.py \\
        --cards "Reckless Impulse,Counterspell"

    # Full 21k-card Modern pool (deferred to follow-up; ~$10-20)
    python tools/build_oracle_classifier_cache.py --all

Determinism: the prompt is versioned (`classify_oracle_v1.md`) and
the cache keys include `prompt_version`, so bumping the prompt
invalidates old responses correctly.  Outputs are deterministic
modulo the LLM's nondeterminism — the SHA-256 cache layer ensures
re-runs are exact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

# Make the repo root importable when run as
# `python tools/build_oracle_classifier_cache.py`.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.llm_agents import build_agent
from ai.llm_budgets import select_budget_usd
from ai.oracle_classifier import SCHEMA_VERSION, Tag


# ─── Constants (no magic numbers) ────────────────────────────────────

# Path to the committed JSON cache the loader reads at runtime.  This
# is what the engine and AI consume — never an LLM call.
CACHE_JSON_PATH = ROOT / "decks" / "gameplans" / "_oracle_classifier.json"

# Smoke set: 10 cards covering the audit's critical tag families.  The
# list is intentionally hand-picked so the smoke build proves
# coverage of the trickiest classifications — re-runners should leave
# this fixed so the bootstrap cache reproduces.
SMOKE_CARDS: tuple[str, ...] = (
    "Reckless Impulse",       # IMPULSE_DRAW
    "Glimpse the Impossible", # IMPULSE_DRAW + cantrip
    "Thoughtseize",           # FORCED_DISCARD + SELF_DAMAGE_ON_CAST
    "Orcish Bowmasters",      # ON_DRAW_DAMAGE + ETB_ORACLE_TRIGGER + TARGET_ANY_DAMAGE
    "Counterspell",           # no tags (negative example)
    "Galvanic Discharge",     # TARGET_ANY_DAMAGE
    "Meticulous Archive",     # ETB_SURVEIL_N
    "Teferi, Time Raveler",   # SORCERY_SPEED_LOCKOUT + planeswalker tags
    "Past in Flames",         # FLASHBACK
    "Murktide Regent",        # DELVE
)

# Per-card progress-log cadence: print a progress line every N cards
# during a long run.  10 is small enough to give visible feedback on a
# smoke run and large enough to not spam during the 21k full run.
PROGRESS_INTERVAL_CARDS = 10


# ─── Card-database access ────────────────────────────────────────────


def _load_modern_atomic() -> Dict[str, dict]:
    """Return the merged ModernAtomic card dict (name -> first-face dict).

    Prefers the assembled `ModernAtomic.json` (full Modern pool); falls
    back to the part files; falls back to `ModernAtomic_mini.json` as a
    last resort (only useful for the smoke build).
    """
    full = ROOT / "ModernAtomic.json"
    if full.exists():
        with full.open() as f:
            return _flatten(json.load(f))

    parts = sorted(ROOT.glob("ModernAtomic_part*.json"))
    if parts:
        merged: Dict[str, list] = {}
        for p in parts:
            with p.open() as f:
                payload = json.load(f)
            merged.update(payload.get("data", payload))
        return {name: faces[0] for name, faces in merged.items() if faces}

    mini = ROOT / "ModernAtomic_mini.json"
    if mini.exists():
        with mini.open() as f:
            return _flatten(json.load(f))

    raise FileNotFoundError(
        "No ModernAtomic*.json found at repo root.  Reassemble or "
        "fetch the data file before running the classifier builder."
    )


def _flatten(payload: dict) -> Dict[str, dict]:
    """Pick the first card face from each entry — every card in the
    cache is keyed by its printed name, and the first face is the
    primary side for double-faced cards in MTGJSON."""
    data = payload.get("data", payload)
    return {name: faces[0] for name, faces in data.items() if faces}


def _oracle_sha(text: str) -> str:
    """SHA-256 hex of the oracle text.  Used for stale-detection in
    the JSON cache — engine startup logs a warning if the live oracle
    text drifts from the cached entry's hash.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ─── Prompt construction ─────────────────────────────────────────────


def build_classify_input(card: dict) -> str:
    """Format one card's MTGJSON entry as the per-card prompt body.

    The system prompt (loaded from `classify_oracle_v1.md`) carries
    the closed-set tag table and few-shot examples.  This per-card
    payload is intentionally minimal — name, mana cost, type line,
    oracle text — so the prompt stays within the 4000-token cap and
    the cache key changes only when the inputs that matter change.
    """
    name = card.get("name", "<unknown>")
    mana = card.get("manaCost", "")
    types = card.get("types", [])
    subtypes = card.get("subtypes", [])
    supertypes = card.get("supertypes", [])
    type_line_parts = [" ".join(supertypes + types).strip()]
    if subtypes:
        type_line_parts.append("— " + " ".join(subtypes))
    type_line = " ".join(p for p in type_line_parts if p)
    oracle = card.get("text", "")
    return (
        f"name: {name}\n"
        f"mana_cost: {mana}\n"
        f"types: {type_line}\n"
        f"oracle_text: {oracle}\n"
    )


# ─── Classification ──────────────────────────────────────────────────


def _coerce_tags(raw_tags: Iterable[str], card_name: str) -> List[str]:
    """Drop tag names that don't match the `Tag` enum.

    The LLM should never return invalid names (the prompt pins the
    closed set), but defensive validation here keeps a one-card slip
    from corrupting the JSON.  Unknown names are logged to stderr —
    the human running the builder sees them, no need for a warning
    spam in the engine.
    """
    valid = {t.name for t in Tag}
    out: List[str] = []
    dropped: List[str] = []
    for s in raw_tags:
        if s in valid:
            out.append(s)
        else:
            dropped.append(s)
    if dropped:
        print(
            f"  [warn] dropped unknown tags for {card_name!r}: {dropped}",
            file=sys.stderr,
        )
    return out


def classify_one(
    agent,
    card: dict,
) -> tuple[List[str], str]:
    """Classify one card.  Returns `(tag_names, oracle_text_sha256)`.

    `oracle_text_sha256` is computed inline so the call-site doesn't
    have to re-derive it for the cache entry.
    """
    user_prompt = build_classify_input(card)
    result = agent.run_sync(user_prompt)
    classification = result.output
    tags = _coerce_tags(classification.tags, card.get("name", "<unknown>"))
    sha = _oracle_sha(card.get("text", ""))
    return tags, sha


# ─── JSON cache I/O ──────────────────────────────────────────────────


def _read_existing_cache() -> dict:
    """Load the existing JSON cache so per-card updates are merge-able.

    Returns the parsed payload (with `schema_version` + `cards` keys)
    or a fresh empty payload if the file doesn't exist yet.  Schema
    version drift is treated as "start over" — the builder is the
    only writer, so divergent schemas mean the loader and writer
    disagreed, and a rebuild is the right fix.
    """
    if not CACHE_JSON_PATH.exists():
        return {"schema_version": SCHEMA_VERSION, "cards": {}}
    with CACHE_JSON_PATH.open() as f:
        payload = json.load(f)
    if payload.get("schema_version") != SCHEMA_VERSION:
        print(
            f"  [warn] existing cache schema {payload.get('schema_version')!r} "
            f"!= expected {SCHEMA_VERSION!r}; rebuilding from scratch",
            file=sys.stderr,
        )
        return {"schema_version": SCHEMA_VERSION, "cards": {}}
    return payload


def _write_cache(payload: dict) -> None:
    """Write the JSON cache atomically.  The temp-file-rename pattern
    prevents a half-written file on disk if the writer is killed
    mid-flight (e.g. by a budget cap hit on the last card).
    """
    CACHE_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_JSON_PATH.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(CACHE_JSON_PATH)


# ─── Driver ──────────────────────────────────────────────────────────


def select_target_cards(
    *,
    smoke: bool,
    all_cards: bool,
    explicit: Optional[List[str]],
    db: Dict[str, dict],
) -> List[str]:
    """Resolve which card names to classify on this run.

    Exactly one of (`smoke`, `all_cards`, `explicit`) must be true /
    non-None.  Unknown names in `explicit` are warned-and-dropped so
    the run continues with the rest.
    """
    chosen = sum(1 for x in (smoke, all_cards, explicit) if x)
    if chosen != 1:
        raise ValueError(
            "Exactly one of --smoke / --all / --cards must be specified"
        )
    if smoke:
        out = []
        for name in SMOKE_CARDS:
            if name in db:
                out.append(name)
            else:
                print(
                    f"  [warn] smoke card {name!r} not in card DB; skipping",
                    file=sys.stderr,
                )
        return out
    if all_cards:
        return sorted(db.keys())
    # explicit
    out = []
    for name in explicit or []:
        name = name.strip()
        if not name:
            continue
        if name in db:
            out.append(name)
        else:
            print(
                f"  [warn] requested card {name!r} not in card DB; skipping",
                file=sys.stderr,
            )
    return out


def build_cache(
    *,
    smoke: bool = False,
    all_cards: bool = False,
    explicit: Optional[List[str]] = None,
    budget_cap_usd: Optional[float] = None,
    model: Optional[str] = None,
) -> dict:
    """Build (or extend) the oracle-classifier JSON cache.

    Returns the resulting payload dict.  Side effect: writes
    `decks/gameplans/_oracle_classifier.json`.

    Args:
        smoke: classify the 10 hand-picked audit-coverage cards.
        all_cards: classify every Modern-legal card (~21k).
        explicit: classify a specific list of card names.
        budget_cap_usd: hard cap on this run's spend; defaults to
            the `classify_oracle` budget in `ai.llm_budgets`.
        model: optional model identifier override.

    Cost gating is layered: `ai.llm_budgets.check_budget` fires on
    every `run_sync`, and an optional `budget_cap_usd` here lowers
    the cap further if the operator wants a smaller per-run ceiling.
    """
    db = _load_modern_atomic()
    targets = select_target_cards(
        smoke=smoke, all_cards=all_cards, explicit=explicit, db=db
    )
    if not targets:
        print("Nothing to classify.", file=sys.stderr)
        return _read_existing_cache()

    if budget_cap_usd is not None:
        # The MeteredAgent reads its cap from env vars; the easiest way
        # to push a per-run cap is to set the per-task env var for the
        # duration of this run.  Restore on exit so other tools aren't
        # affected.
        env_key = "MTG_LLM_BUDGET_USD_CLASSIFY_ORACLE"
        prior = os.environ.get(env_key)
        os.environ[env_key] = str(budget_cap_usd)
    else:
        env_key = None
        prior = None

    try:
        agent = build_agent("classify_oracle", model=model)
    except Exception as exc:
        # build_agent imports pydantic_ai lazily; in environments
        # without the SDK installed we want a clear error rather than
        # a silent corruption of the JSON.  See "Honest constraint" in
        # the W0-A plan: stop, do not ship a fallback.
        print(f"  [fatal] failed to build LLM agent: {exc}", file=sys.stderr)
        raise

    payload = _read_existing_cache()
    cards_map = payload.setdefault("cards", {})
    effective_cap = (
        budget_cap_usd
        if budget_cap_usd is not None
        else select_budget_usd("classify_oracle")
    )
    print(
        f"Classifying {len(targets)} card(s) with budget cap "
        f"${effective_cap:.2f}...",
        flush=True,
    )

    started = time.time()
    errors: list[str] = []
    for i, name in enumerate(targets, start=1):
        if i % PROGRESS_INTERVAL_CARDS == 0 or i == 1:
            elapsed = time.time() - started
            print(
                f"  [{i}/{len(targets)}] {name}  ({elapsed:.1f}s elapsed)",
                flush=True,
            )
        card = db[name]
        try:
            tags, sha = classify_one(agent, card)
        except Exception as exc:
            # Persist whatever was classified up to this point so a
            # partial run isn't wasted.  Common causes: budget cap,
            # rate-limit, auth failure.  See "Honest constraint" —
            # stop, report, do not fall back to hardcoded values.
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            print(
                f"  [error] classification failed for {name!r}: {exc}",
                file=sys.stderr,
            )
            break
        cards_map[name] = {
            "oracle_text_sha256": sha,
            "tags": sorted(tags),
        }

    _write_cache(payload)
    print(
        f"\nWrote {len(cards_map)} card entries to {CACHE_JSON_PATH}",
        flush=True,
    )
    if errors:
        print(f"\n{len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
    if env_key is not None:
        if prior is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = prior

    if errors:
        # Non-zero is the signal that the operator should investigate
        # before committing.  The JSON is still written so partial
        # progress is preserved.
        sys.exit(2)

    return payload


# ─── CLI ─────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--smoke",
        action="store_true",
        help=(
            "Classify 10 representative cards covering the audit's "
            "critical tag families.  Use to prove the pipeline end-to-end."
        ),
    )
    group.add_argument(
        "--all",
        action="store_true",
        dest="all_cards",
        help="Classify every Modern-legal card in ModernAtomic.json.",
    )
    group.add_argument(
        "--cards",
        default=None,
        help="Comma-separated list of card names to classify.",
    )
    parser.add_argument(
        "--budget-cap-usd",
        type=float,
        default=None,
        help=(
            "Hard cap on this run's spend.  Defaults to the "
            "classify_oracle budget in ai.llm_budgets (currently $20)."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the model identifier (default: per ai.llm_models).",
    )
    args = parser.parse_args(argv)

    explicit = (
        [s for s in args.cards.split(",") if s.strip()]
        if args.cards
        else None
    )

    build_cache(
        smoke=args.smoke,
        all_cards=args.all_cards,
        explicit=explicit,
        budget_cap_usd=args.budget_cap_usd,
        model=args.model,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CACHE_JSON_PATH",
    "SMOKE_CARDS",
    "build_cache",
    "build_classify_input",
    "classify_one",
    "select_target_cards",
    "main",
]
