"""Bootstrap and refresh the local sentence-transformer indexes.

Used by future LLM tools (G-2 doc-freshness, G-3 replay-diagnose) to
short-circuit "find similar prior case" calls — the cheap embedding
layer ranks candidates first, only top-k go to the foundation-model
API.  Running this tool is a one-time-per-session bootstrap; the
SQLite indexes live in ``cache/embeddings/`` and persist between runs.

Usage:

    # Build / refresh every corpus the project has
    python -m tools.build_embedding_index --all

    # Per-corpus rebuild
    python -m tools.build_embedding_index --corpus docs --root docs --glob '**/*.md'
    python -m tools.build_embedding_index --corpus replays --root replays --glob '*.txt'
    python -m tools.build_embedding_index --corpus gameplans --root decks/gameplans --glob '*.json'

    # Smoke-test query
    python -m tools.build_embedding_index --query "Affinity overperformance" --corpus docs --k 5

The ``--all`` shorthand is the canonical entrypoint after a session
checkout — it builds all three corpora with the right per-corpus
``glob`` and chunking strategy.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai.llm_embeddings import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_TOP_K,
    corpus_count,
    find_similar,
    index_directory,
)


# Per-corpus defaults: (root path relative to repo, glob).  Loader
# (chunking strategy) is resolved inside ``index_directory`` from the
# corpus name, so callers don't need to plumb it through.
DEFAULT_CORPORA: dict[str, tuple[str, str]] = {
    "docs": ("docs", "**/*.md"),
    "replays": ("replays", "*.txt"),
    "gameplans": ("decks/gameplans", "*.json"),
}


def build_one(corpus: str, root: Path, glob: str) -> int:
    """Build a single corpus.  Returns the number of NEW embeddings
    (cache hits don't count)."""
    new = index_directory(corpus, root, glob=glob)
    total = corpus_count(corpus)
    print(
        f"[{corpus}] indexed root={root} glob={glob}: "
        f"{new} new, {total} total"
    )
    return new


def build_all(repo_root: Path) -> None:
    for corpus, (rel, glob) in DEFAULT_CORPORA.items():
        root = repo_root / rel
        if not root.exists():
            print(f"[{corpus}] skipped: root {root} missing", file=sys.stderr)
            continue
        build_one(corpus, root, glob)


def query_one(corpus: str, query: str, k: int, min_sim: float) -> int:
    """Print top-``k`` similar documents.  Returns 0 on success, 1 if
    the corpus is empty or no hits clear the threshold."""
    hits = find_similar(corpus, query, k=k, min_similarity=min_sim)
    if not hits:
        print(
            f"[{corpus}] no hits >= {min_sim:.2f} for {query!r} "
            f"(corpus_count={corpus_count(corpus)})",
            file=sys.stderr,
        )
        return 1
    print(f"[{corpus}] top-{len(hits)} for {query!r}:")
    for doc_id, sim in hits:
        print(f"  {sim:6.3f}  {doc_id}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.build_embedding_index",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Build every default corpus (docs / replays / gameplans).",
    )
    parser.add_argument(
        "--corpus",
        help="Corpus name (e.g. docs, replays, gameplans).  Required "
        "when not using --all.",
    )
    parser.add_argument(
        "--root",
        help="Root directory to walk.  Defaults to the per-corpus "
        "default when --corpus is in DEFAULT_CORPORA.",
    )
    parser.add_argument(
        "--glob",
        help="Glob pattern (e.g. '**/*.md').  Defaults to per-corpus "
        "default when --corpus is in DEFAULT_CORPORA.",
    )
    parser.add_argument(
        "--query",
        help="Run a similarity query against --corpus instead of "
        "indexing.  Prints top-k hits.",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Top-k for --query (default {DEFAULT_TOP_K}).",
    )
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=DEFAULT_MIN_SIMILARITY,
        help=f"Cosine-similarity floor for --query "
        f"(default {DEFAULT_MIN_SIMILARITY}).",
    )

    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent

    if args.all:
        build_all(repo_root)
        return 0

    if args.query is not None:
        if not args.corpus:
            parser.error("--query requires --corpus")
        return query_one(args.corpus, args.query, args.k, args.min_similarity)

    if not args.corpus:
        parser.error("either --all, --corpus, or --query is required")

    if args.corpus in DEFAULT_CORPORA:
        rel, glob = DEFAULT_CORPORA[args.corpus]
        root = Path(args.root) if args.root else (repo_root / rel)
        glob = args.glob or glob
    else:
        if not args.root or not args.glob:
            parser.error(
                f"unknown corpus {args.corpus!r}: pass --root and --glob "
                "explicitly, or pick one of: "
                + ", ".join(sorted(DEFAULT_CORPORA))
            )
        root = Path(args.root)
        glob = args.glob

    build_one(args.corpus, root, glob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
