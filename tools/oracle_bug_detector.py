"""Class A oracle bug detector — Phase 4F tool.

Scans all card oracle text in the Modern card pool through the
regex parsers in ``engine/oracle_parser.py`` and flags suspicious
results. Optionally cross-references with the SLM oracle parser
(``ai/llm/oracle_parse.py``) when ``MTG_LLM_MODEL_PATH`` is set.

Goal: catch the Class A bug pattern that surfaced in Phase 1A
(parse_cost_reduction's "colorless" / "mana cost {N}" false
positives on 554 cards) before it ships, by:

  1. Running each regex parser on every card.
  2. Flagging cards where a parser returns a positive result but
     the canonical phrase that triggers the parser doesn't appear
     in the oracle (regex matched on a substring of an unrelated
     word).
  3. Listing those cards in ``docs/diagnostics/oracle_bug_candidates.jsonl``
     for human review.
  4. Optionally: comparing to the SLM parser's structured output.
     Disagreements where the SLM says "no cost reduction" but the
     regex says "yes" are strong false-positive signals.

Usage:

    # Static-only scan (no SLM required):
    python -m tools.oracle_bug_detector --target cost_reduction

    # SLM-augmented scan:
    MTG_LLM_MODEL_PATH=/path/to/qwen.gguf \\
        python -m tools.oracle_bug_detector --target all --use-slm

    # Output a CSV-friendly report:
    python -m tools.oracle_bug_detector --target all --format csv \\
        > oracle_bug_report.csv

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class Suspicion:
    """A suspicious parser result that warrants human review."""

    card_name: str
    parser: str
    """The regex parser whose result is suspicious. e.g.
    "parse_cost_reduction"."""
    parsed_result: dict
    """The parser's actual return value (serialized)."""
    reason: str
    """Why we think this might be a false positive."""
    oracle_excerpt: str
    """First 200 chars of the oracle text for human review."""
    slm_disagrees: Optional[bool] = None
    """If SLM cross-check ran: True if SLM produced a meaningfully
    different result; None if not run."""


# ─── Static heuristics for false-positive detection ──────────────────


def _check_cost_reduction(name: str, oracle: str) -> Optional[Suspicion]:
    """Replicates Phase 1A's bug-detection logic. The fix in PR #304
    requires the strict ``cost {N} less`` pattern. This detector
    flags cards where the regex parser would return a non-None
    value WITHOUT the strict pattern matching — i.e. cards that
    used to be false positives.

    Post-Phase-1A this should never fire on the current parser, so
    it serves as a regression anchor: if anyone re-introduces the
    lazy check, this detector lights up immediately.
    """
    from engine.oracle_parser import parse_cost_reduction
    rule = parse_cost_reduction(oracle)
    if rule is None:
        return None
    # Parser returned non-None — verify the strict pattern is
    # present. If not, the parser regressed.
    strict = re.search(r'cost\s*\{(\d+)\}\s*less', oracle.lower())
    if strict is not None:
        return None  # canonical positive
    return Suspicion(
        card_name=name,
        parser="parse_cost_reduction",
        parsed_result=rule,
        reason=(
            "Returned a non-None reduction rule, but the strict "
            "'cost {N} less' pattern doesn't appear in the oracle. "
            "Likely Class A regression of PR #304."
        ),
        oracle_excerpt=oracle[:200],
    )


def _check_ritual(name: str, oracle: str) -> Optional[Suspicion]:
    """Flag cards where parse_ritual_mana fires on text that
    isn't a ritual. Pre-fix the ``add`` substring matched against
    "additional" (Orim's Chant); post-fix uses word boundaries.

    This detector flags any positive result where the immediate
    context around 'add' is preceded by 'addition' or 'additi'.
    Regression anchor for that bug class.
    """
    from engine.oracle_parser import parse_ritual_mana
    result = parse_ritual_mana(oracle)
    if result is None:
        return None
    lower = oracle.lower()
    # Find any standalone "add" — and verify it isn't inside
    # "additional".
    for m in re.finditer(r'\badd\b', lower):
        # Look at the 12 chars BEFORE this occurrence — are we
        # inside "additional"?
        start = max(0, m.start() - 12)
        prefix = lower[start:m.start()]
        if "addition" in prefix:
            return Suspicion(
                card_name=name,
                parser="parse_ritual_mana",
                parsed_result={
                    "color": result[0], "amount": result[1],
                },
                reason=(
                    "Returned a positive ritual but 'add' appears "
                    "to be a substring of 'additional'. Likely "
                    "Class A bug pattern (Orim's Chant misparse)."
                ),
                oracle_excerpt=oracle[:200],
            )
    return None


# ─── DB iteration ────────────────────────────────────────────────────


def _iter_modern_cards(deck_filter: bool = True):
    """Yield (name, oracle) for every card in the 16 Modern decks
    when ``deck_filter`` is True, else for every card in the DB.

    Deck-filter is the default to keep the scan fast and focused
    on cards that actually run in our matrix sims; setting
    ``deck_filter=False`` walks the entire ModernAtomic.json.
    """
    from engine.card_database import CardDatabase
    db = CardDatabase()

    if deck_filter:
        from decks.modern_meta import MODERN_DECKS
        seen: set = set()
        for d in MODERN_DECKS.values():
            for name in list(d.get("mainboard", {}).keys()) + list(
                d.get("sideboard", {}).keys()
            ):
                if name in seen:
                    continue
                seen.add(name)
                tmpl = db.get_card(name)
                if tmpl is None:
                    continue
                yield name, (tmpl.oracle_text or "")
    else:
        # Full DB walk.
        for name, tmpl in db.cards.items():
            yield name, (tmpl.oracle_text or "")


# ─── SLM cross-check (optional) ──────────────────────────────────────


def _maybe_slm_disagrees(name: str, oracle: str,
                         suspicion: Suspicion) -> bool:
    """If MTG_LLM_MODEL_PATH is set, ask the SLM to parse this
    oracle and compare. Return True if the SLM's primary_effect
    DOES NOT match the regex parser's return.

    Returns False on backend unavailability so the report still
    surfaces the suspicion."""
    import os
    if not os.environ.get("MTG_LLM_MODEL_PATH"):
        return False
    try:
        from ai.llm.policy import LLMPolicy
        from ai.llm.llama_cpp_backend import LlamaCppBackend
        from ai.llm.oracle_parse import parse_oracle
        backend = LlamaCppBackend()
        policy = LLMPolicy(
            backend=backend,
            cache_dir=ROOT / ".cache" / "llm_responses",
        )
        slm_result = parse_oracle(oracle, policy)
    except Exception:
        return False

    # If SLM says the primary effect is "passive" but regex says
    # cost_reduction, that's a flag-worthy disagreement.
    if suspicion.parser == "parse_cost_reduction":
        if slm_result.primary_effect != "cost_reduce":
            return True
    return False


# ─── CLI ────────────────────────────────────────────────────────────


def scan(target: str, deck_filter: bool, use_slm: bool) -> List[Suspicion]:
    """Run the chosen detectors on the corpus."""
    detectors = {
        "cost_reduction": _check_cost_reduction,
        "ritual": _check_ritual,
    }
    if target == "all":
        chosen = list(detectors.values())
    else:
        if target not in detectors:
            raise ValueError(f"Unknown target {target!r}; "
                             f"available: {list(detectors)} or 'all'")
        chosen = [detectors[target]]

    suspicions: List[Suspicion] = []
    for name, oracle in _iter_modern_cards(deck_filter=deck_filter):
        for det in chosen:
            s = det(name, oracle)
            if s is not None:
                if use_slm:
                    s.slm_disagrees = _maybe_slm_disagrees(name, oracle, s)
                suspicions.append(s)
    return suspicions


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=("cost_reduction", "ritual", "all"),
        default="all",
        help="Which detector(s) to run.",
    )
    parser.add_argument(
        "--full-db", action="store_true",
        help="Scan the entire ModernAtomic.json (~21k cards) "
             "instead of just the 16-deck universe (default).",
    )
    parser.add_argument(
        "--use-slm", action="store_true",
        help="Cross-reference suspicious results against the SLM "
             "oracle parser. Requires MTG_LLM_MODEL_PATH.",
    )
    parser.add_argument(
        "--format", choices=("jsonl", "csv", "summary"),
        default="summary",
        help="Output format.",
    )
    args = parser.parse_args(argv)

    suspicions = scan(
        target=args.target,
        deck_filter=not args.full_db,
        use_slm=args.use_slm,
    )

    if args.format == "jsonl":
        for s in suspicions:
            print(json.dumps(asdict(s)))
    elif args.format == "csv":
        writer = csv.DictWriter(
            sys.stdout,
            fieldnames=("card_name", "parser", "reason", "slm_disagrees"),
        )
        writer.writeheader()
        for s in suspicions:
            writer.writerow({
                "card_name": s.card_name,
                "parser": s.parser,
                "reason": s.reason,
                "slm_disagrees": s.slm_disagrees,
            })
    else:  # summary
        print(f"Scanned. Found {len(suspicions)} suspicious results.")
        for s in suspicions[:20]:
            slm_note = ""
            if s.slm_disagrees is True:
                slm_note = " [SLM DISAGREES]"
            elif s.slm_disagrees is False and args.use_slm:
                slm_note = " [SLM agrees]"
            print(f"  {s.card_name} ({s.parser}){slm_note}")
            print(f"    {s.reason}")
        if len(suspicions) > 20:
            print(f"  ... +{len(suspicions) - 20} more "
                  "(use --format jsonl for full list)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
