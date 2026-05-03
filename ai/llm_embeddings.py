"""Local sentence-transformer embeddings + persistent vector index.

Use case: cheap similarity search over docs/, replays/, gameplans/ to
short-circuit LLM "find similar prior case" calls.  The big LLM is only
invoked on the top-k retrieved items, never on the full corpus.

Model: sentence-transformers/all-MiniLM-L6-v2 (22M params, CPU, free).
Output dimension: 384.  Outputs are L2-normalized so cosine similarity
is just the dot product.

Index storage: SQLite-backed persistent vector store at
``cache/embeddings/{corpus}.sqlite`` (gitignored).  Schema::

    embeddings(
        id          TEXT PRIMARY KEY,
        source_path TEXT NOT NULL,
        content_sha TEXT NOT NULL,
        embedding   BLOB NOT NULL,
        indexed_at  TEXT NOT NULL
    )

Vectors are stored as little-endian float32 bytes via ``ndarray.tobytes``.
Linear scan over ~200 documents on CPU is sub-millisecond, so faiss/annoy
are unnecessary at our scale.

Replay chunking: each ``.txt`` replay log is too long to embed whole
(5-10k words per file).  ``index_directory`` with ``glob='*.txt'`` and
the ``replay`` corpus splits each file on ``GAME N`` headers and emits
one row per game with ``doc_id = f"{filename}#game{n}"``.

Gameplan flattening: gameplan JSONs embed best as a small text blob
joining ``deck_name`` + archetype + role-bucket headers + key card
mentions.  Raw card lists are noise for similarity search.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np


# ─── Constants ───────────────────────────────────────────────────────

EMBEDDING_DIM: int = 384
"""Output dimension of sentence-transformers/all-MiniLM-L6-v2."""

MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"
"""Default local embedding model.  Free, CPU-friendly, 22M params."""

INDEX_PATH: Path = Path("cache/embeddings/index.sqlite")
"""Default index location (gitignored).  Per-corpus paths derive from
:func:`index_path`."""

DEFAULT_TOP_K: int = 5
"""Fallback k for :func:`find_similar` when caller doesn't specify."""

DEFAULT_MIN_SIMILARITY: float = 0.5
"""Default cosine-similarity cutoff for :func:`find_similar`.  Anything
below this is too weak to bother an LLM with."""

REPLAY_GAME_HEADER: str = "GAME "
"""Marker used by ``run_meta.py --bo3`` to delimit games inside one
replay log.  Used by :func:`split_replay_into_games`."""


# ─── Embedder ─────────────────────────────────────────────────────────

class Embedder:
    """Lazy-loaded singleton wrapping a SentenceTransformer model.

    Tests substitute via dependency injection — see
    :func:`set_global_embedder`.  The wrapped ``_model`` only needs an
    ``encode(list[str]) -> np.ndarray`` method, so unit tests can pass
    a stub model and avoid downloading the real weights.
    """

    _instance: Optional["Embedder"] = None

    def __init__(self, model) -> None:
        self._model = model

    @classmethod
    def get(cls) -> "Embedder":
        """Return the process-global embedder, instantiating the real
        SentenceTransformer model on first call.

        Tests should call :func:`set_global_embedder` BEFORE any code
        path triggers ``get()`` to avoid the model download.
        """
        if cls._instance is None:
            # Local import — keeps the module loadable even when
            # sentence-transformers isn't installed (e.g. when the
            # caller plans to inject a stub via set_global_embedder).
            from sentence_transformers import SentenceTransformer
            cls._instance = cls(SentenceTransformer(MODEL_NAME))
        return cls._instance

    def embed(self, texts: list[str]) -> np.ndarray:
        """Embed a list of strings into an ``(N, EMBEDDING_DIM)``
        float32 array with L2-normalized rows.

        SentenceTransformer's default ``encode()`` already returns
        normalized vectors when ``normalize_embeddings=True``; we set
        it explicitly so cosine similarity reduces to a dot product.
        Fallback path renormalizes defensively in case the wrapped
        model doesn't honor the flag (test stubs).
        """
        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        arr = self._model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        arr = np.asarray(arr, dtype=np.float32)
        # Defensive renormalize — TestModel-style stubs may ignore
        # normalize_embeddings=True.  Idempotent for already-normalized
        # vectors.
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms


def set_global_embedder(embedder: Optional[Embedder]) -> None:
    """Test-only: inject a stub embedder so tests don't load the real
    model.  Pass ``None`` to reset to a fresh instantiation on next
    :meth:`Embedder.get` call."""
    Embedder._instance = embedder


# ─── Index path / schema ─────────────────────────────────────────────

def index_path(corpus: str) -> Path:
    """Return the SQLite path for ``corpus`` under
    ``cache/embeddings/``.  Path's parent is created on demand by
    :func:`_open`."""
    base = INDEX_PATH.parent
    return base / f"{corpus}.sqlite"


def _open(corpus: str) -> sqlite3.Connection:
    """Open (and lazily-create) the SQLite index for ``corpus``."""
    path = index_path(corpus)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embeddings (
            id          TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            content_sha TEXT NOT NULL,
            embedding   BLOB NOT NULL,
            indexed_at  TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _existing_sha(conn: sqlite3.Connection, doc_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT content_sha FROM embeddings WHERE id = ?", (doc_id,)
    ).fetchone()
    return row[0] if row else None


# ─── Public API: add / index / query ─────────────────────────────────

def add_document(
    corpus: str,
    doc_id: str,
    source_path: str,
    content: str,
) -> bool:
    """Embed ``content`` and upsert into the ``corpus`` index.

    Idempotent: when the SHA-256 of ``content`` matches the row
    already stored under ``doc_id``, no work is done and the model
    is not invoked.

    Returns:
        ``True`` if a new embedding was computed and stored;
        ``False`` if the row was up-to-date (cache hit).
    """
    sha = _sha256(content)
    conn = _open(corpus)
    try:
        if _existing_sha(conn, doc_id) == sha:
            return False
        emb = Embedder.get().embed([content])[0]
        # tobytes() of a contiguous float32 ndarray is the canonical
        # cheap-serialization path; numpy.frombuffer(blob, float32)
        # round-trips exactly.
        conn.execute(
            """
            INSERT INTO embeddings (id, source_path, content_sha, embedding, indexed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source_path = excluded.source_path,
                content_sha = excluded.content_sha,
                embedding   = excluded.embedding,
                indexed_at  = excluded.indexed_at
            """,
            (
                doc_id,
                source_path,
                sha,
                emb.astype(np.float32).tobytes(),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


# Type alias: a text loader takes a Path and yields (sub_id_suffix,
# content) pairs.  ``sub_id_suffix=""`` means "use the file as one
# document" with ``doc_id = relative_path``.  Replay chunking yields
# multiple ``("#game1", text), ("#game2", text), ...`` pairs per file.
TextLoader = Callable[[Path], Iterable[tuple[str, str]]]


def _default_text_loader(path: Path) -> Iterable[tuple[str, str]]:
    """One document per file — read the file as UTF-8 (lossy)."""
    yield "", path.read_text(encoding="utf-8", errors="replace")


def split_replay_into_games(text: str) -> list[str]:
    """Split a replay log into per-game segments.

    Replays from ``run_meta.py --bo3`` separate games with a banner of
    ``=`` characters surrounding a ``GAME N: …`` header line.  We split
    on the header line itself (matching ``REPLAY_GAME_HEADER``) so the
    resulting segments include the header for context.

    Returns the list of segments in order.  Empty input yields an
    empty list.  A file with no header markers is returned as a single
    segment (the whole file).
    """
    if not text.strip():
        return []
    lines = text.splitlines(keepends=True)
    segments: list[list[str]] = []
    current: list[str] = []
    seen_first_header = False
    for line in lines:
        if line.lstrip().startswith(REPLAY_GAME_HEADER) and ":" in line:
            # Heuristic: header lines look like ``  GAME 1: A vs B …``.
            # When we see one, flush the current segment (if any) and
            # start a new one rooted at this header.  The pre-header
            # preamble (``Loaded 21759 cards`` banner, separator
            # rules) is dropped — it isn't game content.
            if seen_first_header and current:
                segments.append(current)
            current = [line]
            seen_first_header = True
        elif seen_first_header:
            current.append(line)
        # Lines before the first GAME header are preamble; skip them.
    if seen_first_header and current:
        segments.append(current)
    if not segments:
        # No GAME header at all → treat the whole non-empty file as a
        # single segment for callers that want to embed flat logs too.
        return [text]
    return ["".join(seg) for seg in segments]


def replay_text_loader(path: Path) -> Iterable[tuple[str, str]]:
    """``text_loader`` for the replay corpus: split each file into
    per-game chunks and yield ``("#game{n}", text)`` for each."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    games = split_replay_into_games(raw)
    if not games:
        return
    if len(games) == 1:
        # No game header found — treat the whole file as one chunk
        # (still keyed off "#game1" so downstream callers don't have
        # to special-case the no-header layout).
        yield "#game1", games[0]
        return
    for i, segment in enumerate(games, start=1):
        yield f"#game{i}", segment


def gameplan_text_loader(path: Path) -> Iterable[tuple[str, str]]:
    """``text_loader`` for the gameplans corpus.

    Embeds a flattened text representation of the JSON: deck name,
    archetype, role-bucket headers, mulligan keys, and ``always_early``
    cards.  The raw 60-card decklist is intentionally NOT embedded —
    card lists are noise for similarity search across decks.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    parts: list[str] = []
    parts.append(f"Deck: {raw.get('deck_name', path.stem)}")
    parts.append(f"Archetype: {raw.get('archetype', 'unknown')}")
    if raw.get("archetype_subtype"):
        parts.append(f"Subtype: {raw['archetype_subtype']}")
    for goal in raw.get("goals", []) or []:
        parts.append(f"Goal: {goal.get('goal_type', '')} — {goal.get('description', '')}")
        for bucket, cards in (goal.get("card_roles") or {}).items():
            if cards:
                parts.append(f"{bucket}: {', '.join(cards)}")
    for key in ("always_early", "reactive_only", "critical_pieces", "mulligan_keys"):
        vals = raw.get(key) or []
        if vals:
            parts.append(f"{key}: {', '.join(vals)}")
    yield "", "\n".join(parts)


# Built-in loaders by corpus name.  ``index_directory`` consults this
# table when no explicit ``text_loader`` is passed.
_LOADER_REGISTRY: dict[str, TextLoader] = {
    "replays": replay_text_loader,
    "gameplans": gameplan_text_loader,
}


def index_directory(
    corpus: str,
    root: Path,
    *,
    glob: str = "*.md",
    text_loader: Optional[TextLoader] = None,
) -> int:
    """Walk ``root`` for files matching ``glob``, embed each file's
    content (split into chunks via ``text_loader`` when applicable),
    and upsert into the ``corpus`` index.

    Returns:
        Number of NEW embeddings written (cache hits are not counted).

    Raises:
        FileNotFoundError: when ``root`` does not exist.
    """
    if not root.exists():
        raise FileNotFoundError(f"index_directory: root not found: {root}")
    loader = text_loader or _LOADER_REGISTRY.get(corpus, _default_text_loader)

    new_count = 0
    for path in sorted(root.rglob(glob)):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        for sub_id, content in loader(path):
            if not content or not content.strip():
                continue
            doc_id = f"{rel.as_posix()}{sub_id}"
            if add_document(corpus, doc_id, str(rel.as_posix()), content):
                new_count += 1
    return new_count


def _load_corpus_matrix(
    corpus: str,
) -> tuple[list[str], list[str], np.ndarray]:
    """Load every (id, source_path, embedding) triple from the corpus.

    Returns:
        ``(ids, source_paths, matrix)`` where ``matrix`` is
        ``(N, EMBEDDING_DIM)`` float32.  ``N == 0`` → empty matrix.
    """
    conn = _open(corpus)
    try:
        rows = conn.execute(
            "SELECT id, source_path, embedding FROM embeddings"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return [], [], np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    ids = [r[0] for r in rows]
    paths = [r[1] for r in rows]
    matrix = np.vstack([
        np.frombuffer(r[2], dtype=np.float32) for r in rows
    ]).reshape(len(rows), EMBEDDING_DIM)
    return ids, paths, matrix


def find_similar(
    corpus: str,
    query: str,
    *,
    k: int = DEFAULT_TOP_K,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> list[tuple[str, float]]:
    """Top-``k`` closest documents from the ``corpus`` index.

    Args:
        corpus: Name of the corpus (e.g. ``"docs"``, ``"replays"``).
        query: Free-text query — embedded with the same model as the
            corpus, so semantic phrasing works ("Affinity overperformance").
        k: Maximum number of hits to return.  Returned list may be
            shorter when ``min_similarity`` filters or the corpus is
            small.
        min_similarity: Cosine-similarity floor.  ``0.5`` is a
            reasonable starting point for MiniLM; the LLM-call layer
            should bump this when latency budget tightens.

    Returns:
        ``[(doc_id, cosine_similarity), …]`` sorted by similarity
        descending.  Empty list on empty corpus or all-below-threshold.
    """
    ids, _paths, matrix = _load_corpus_matrix(corpus)
    if not ids:
        return []
    query_vec = Embedder.get().embed([query])[0]
    # Cosine similarity == dot product when both sides are
    # L2-normalized (Embedder.embed enforces this).
    sims = (matrix @ query_vec).astype(np.float32)
    # Use argpartition for top-k when the corpus is large enough to
    # matter, but argsort is fine and simpler at our 200-document scale.
    order = np.argsort(-sims)
    out: list[tuple[str, float]] = []
    for idx in order[:k]:
        sim = float(sims[idx])
        if sim < min_similarity:
            break  # remaining are even smaller (sorted desc)
        out.append((ids[idx], sim))
    return out


# ─── CLI helpers (used by tools/build_embedding_index.py) ────────────

def corpus_count(corpus: str) -> int:
    """Number of rows in the corpus index — for status / smoke checks."""
    conn = _open(corpus)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM embeddings"
        ).fetchone()[0]
    finally:
        conn.close()


__all__ = [
    "EMBEDDING_DIM",
    "MODEL_NAME",
    "INDEX_PATH",
    "DEFAULT_TOP_K",
    "DEFAULT_MIN_SIMILARITY",
    "Embedder",
    "set_global_embedder",
    "index_path",
    "add_document",
    "index_directory",
    "find_similar",
    "split_replay_into_games",
    "replay_text_loader",
    "gameplan_text_loader",
    "corpus_count",
]
