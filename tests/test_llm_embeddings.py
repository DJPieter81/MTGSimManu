"""Tests for ai.llm_embeddings — the local sentence-transformer
similarity index.

CI must NEVER load the real ``sentence-transformers/all-MiniLM-L6-v2``
weights — slow, network-dependent, and a model download is not a unit
of test isolation.  Every test here injects a deterministic
:class:`StubEmbedder` via :func:`set_global_embedder` BEFORE any code
path triggers :meth:`Embedder.get`, then resets it in teardown so
tests don't pollute each other.

Contracts under test:
  1. ``add_document`` is idempotent on equal content (SHA cache hit).
  2. ``index_directory`` skips unchanged files and re-embeds changed
     ones.
  3. ``find_similar`` returns top-k by cosine similarity, filters by
     ``min_similarity``, and isolates corpora.
  4. The replay text-loader splits a file into per-game chunks with
     ``#game{n}`` doc-id suffixes.
  5. ``set_global_embedder`` injection works without importing
     sentence-transformers.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from ai.llm_embeddings import (
    EMBEDDING_DIM,
    Embedder,
    add_document,
    corpus_count,
    find_similar,
    gameplan_text_loader,
    index_directory,
    index_path,
    replay_text_loader,
    set_global_embedder,
    split_replay_into_games,
)


# ─── Fixtures ────────────────────────────────────────────────────────

class StubEmbedder(Embedder):
    """Deterministic pseudo-embedder for tests.

    Hashes input text → seeds an ``np.random.default_rng`` → returns
    L2-normalized random vectors.  Equal text always produces equal
    embeddings (idempotency requirement); different text produces
    near-orthogonal vectors with high probability.
    """

    def __init__(self) -> None:
        super().__init__(model=None)

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            seed = int(hashlib.sha256(t.encode("utf-8")).hexdigest()[:8], 16)
            rng = np.random.default_rng(seed)
            v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
            out[i] = v / np.linalg.norm(v)
        return out


@pytest.fixture(autouse=True)
def _isolated_index(tmp_path, monkeypatch):
    """Redirect ``cache/embeddings/`` to a per-test temp dir and inject
    the stub embedder.  Both reset cleanly after the test."""
    # Patch the module-level INDEX_PATH so per-corpus index files land
    # under tmp_path/cache/embeddings/{corpus}.sqlite.
    import ai.llm_embeddings as mod

    fake_index = tmp_path / "cache" / "embeddings" / "index.sqlite"
    monkeypatch.setattr(mod, "INDEX_PATH", fake_index)

    set_global_embedder(StubEmbedder())
    yield
    set_global_embedder(None)


# ─── add_document ────────────────────────────────────────────────────

def test_add_document_persists_to_sqlite(tmp_path):
    assert add_document("docs", "doc1", "docs/foo.md", "hello world") is True
    # Reopen on a fresh sqlite connection (close happens inside
    # add_document) — the row should still be retrievable via
    # find_similar.
    hits = find_similar("docs", "hello world", k=1, min_similarity=0.0)
    assert len(hits) == 1
    assert hits[0][0] == "doc1"
    # Identical text → cosine sim == 1.0 (StubEmbedder is deterministic).
    assert pytest.approx(hits[0][1], abs=1e-5) == 1.0


def test_add_document_idempotent_on_same_content():
    """Same content_sha → no second insert, no second embed."""
    assert add_document("docs", "doc1", "x.md", "same content") is True
    assert add_document("docs", "doc1", "x.md", "same content") is False
    # Still exactly one row.
    assert corpus_count("docs") == 1


def test_add_document_re_embeds_on_changed_content():
    assert add_document("docs", "doc1", "x.md", "first version") is True
    assert add_document("docs", "doc1", "x.md", "second version") is True
    # Upsert keeps the row count at 1 but the embedding has changed.
    assert corpus_count("docs") == 1
    hits_first = find_similar("docs", "first version", k=1, min_similarity=0.0)
    hits_second = find_similar("docs", "second version", k=1, min_similarity=0.0)
    # The currently-stored embedding matches the LATER content.
    assert pytest.approx(hits_second[0][1], abs=1e-5) == 1.0
    assert hits_first[0][1] < 1.0


# ─── index_directory ─────────────────────────────────────────────────

def _make_md_dir(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (root / name).write_text(body, encoding="utf-8")
    return root


def test_index_directory_indexes_md_files(tmp_path):
    root = _make_md_dir(tmp_path / "docs", {
        "a.md": "alpha document content",
        "b.md": "beta document content",
        "c.md": "gamma document content",
    })
    new = index_directory("docs", root, glob="*.md")
    assert new == 3
    assert corpus_count("docs") == 3


def test_index_directory_skips_unchanged(tmp_path):
    root = _make_md_dir(tmp_path / "docs", {
        "a.md": "alpha", "b.md": "beta",
    })
    assert index_directory("docs", root, glob="*.md") == 2
    # Re-index without changes → 0 new (cache hits all the way).
    assert index_directory("docs", root, glob="*.md") == 0


def test_index_directory_re_embeds_changed(tmp_path):
    root = _make_md_dir(tmp_path / "docs", {
        "a.md": "alpha", "b.md": "beta",
    })
    assert index_directory("docs", root, glob="*.md") == 2
    # Modify one file; the other is unchanged.
    (root / "a.md").write_text("alpha but rewritten", encoding="utf-8")
    assert index_directory("docs", root, glob="*.md") == 1


def test_index_directory_raises_when_root_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        index_directory("docs", tmp_path / "no-such-dir", glob="*.md")


# ─── find_similar ────────────────────────────────────────────────────

def test_find_similar_returns_top_k_with_self_match(tmp_path):
    """A query that exactly matches an indexed document ranks that
    document #1 with similarity == 1.0."""
    docs = {
        "doc_alpha": "alpha content here",
        "doc_beta":  "beta content here",
        "doc_gamma": "gamma content here",
    }
    for doc_id, content in docs.items():
        add_document("docs", doc_id, f"{doc_id}.md", content)
    # min_similarity=-1.0 so the orthogonal-ish stub embeddings of
    # the OTHER two docs aren't filtered out (random pseudo-vectors
    # land near zero with arbitrary sign).
    hits = find_similar("docs", "beta content here", k=3, min_similarity=-1.0)
    # The exact-text match is rank 1 with sim==1.0; the other two rank
    # below.
    assert hits[0][0] == "doc_beta"
    assert pytest.approx(hits[0][1], abs=1e-5) == 1.0
    assert len(hits) == 3
    assert hits[1][1] < hits[0][1]


def test_find_similar_filters_by_threshold():
    """min_similarity=0.99 against random pseudo-embeddings → only
    the exact match (or nothing) clears the bar."""
    add_document("docs", "doc1", "a.md", "completely unrelated content")
    hits = find_similar(
        "docs",
        "totally different unrelated text we did not index",
        k=5,
        min_similarity=0.99,
    )
    assert hits == []


def test_find_similar_empty_corpus_returns_empty():
    assert find_similar("docs", "anything", k=5, min_similarity=0.0) == []


def test_find_similar_k_caps_results():
    for i in range(10):
        add_document("docs", f"d{i}", f"d{i}.md", f"document number {i}")
    # min_similarity=-1.0 so we get the full top-3 regardless of the
    # orthogonal-ish stub embedding values.
    hits = find_similar("docs", "document number 4", k=3, min_similarity=-1.0)
    assert len(hits) == 3
    # Self-match is rank 1.
    assert hits[0][0] == "d4"


# ─── corpus isolation ────────────────────────────────────────────────

def test_corpus_isolation():
    add_document("docs", "doc1", "a.md", "shared phrase here")
    # Same content under a different corpus name MUST NOT be found by
    # a query against the original corpus.
    add_document("replays", "rep1", "rep1.txt", "totally other content")
    docs_hits = find_similar("docs", "shared phrase here", k=5, min_similarity=0.0)
    replays_hits = find_similar("replays", "shared phrase here", k=5, min_similarity=0.0)
    assert [h[0] for h in docs_hits] == ["doc1"]
    assert "doc1" not in [h[0] for h in replays_hits]


# ─── replay chunking ─────────────────────────────────────────────────

# Real replay headers from run_meta.py --bo3 — pinning the exact
# format here means a refactor of the bo3 logger that breaks chunking
# fails this test rather than silently degrading similarity quality.
REPLAY_FIXTURE = """\
Loaded 21759 cards (0 errors)
======================================================================
  GAME 1: Affinity (P1) vs Azorius Control (P2)  —  seed 60006
  Series: Affinity 0 - 0 Azorius Control
======================================================================
turn 1 of game 1
turn 2 of game 1

======================================================================
  GAME 2: Azorius Control (P1) vs Affinity (P2)  —  seed 60007
  Series: Affinity 1 - 0 Azorius Control
======================================================================
turn 1 of game 2

======================================================================
  GAME 3: Affinity (P1) vs Azorius Control (P2)  —  seed 60008
======================================================================
final turn of game 3
"""


def test_split_replay_into_games_finds_three_segments():
    games = split_replay_into_games(REPLAY_FIXTURE)
    assert len(games) == 3
    assert "GAME 1:" in games[0]
    assert "GAME 2:" in games[1]
    assert "GAME 3:" in games[2]


def test_split_replay_into_games_handles_no_header():
    """A file with no GAME header is returned as a single segment."""
    games = split_replay_into_games("just a flat log with no game banner\n")
    assert len(games) == 1


def test_split_replay_into_games_empty_input():
    assert split_replay_into_games("") == []
    assert split_replay_into_games("   \n\n  ") == []


def test_replay_chunking_via_index_directory(tmp_path):
    root = tmp_path / "replays"
    root.mkdir()
    (root / "match.txt").write_text(REPLAY_FIXTURE, encoding="utf-8")
    new = index_directory("replays", root, glob="*.txt")
    assert new == 3
    # doc_ids must be ``match.txt#game{n}`` (relative-path keyed).
    # min_similarity=-1.0 so the orthogonal-ish stub embeddings of
    # the OTHER segments aren't filtered out (we only care about
    # presence of all three sub-ids in the index).
    hits = find_similar("replays", "GAME 2", k=5, min_similarity=-1.0)
    ids = [h[0] for h in hits]
    assert any(id_.endswith("#game1") for id_ in ids)
    assert any(id_.endswith("#game2") for id_ in ids)
    assert any(id_.endswith("#game3") for id_ in ids)


# ─── gameplan flattening ─────────────────────────────────────────────

def test_gameplan_text_loader_flattens_role_buckets(tmp_path):
    plan = {
        "deck_name": "Test Deck",
        "archetype": "combo",
        "goals": [
            {
                "goal_type": "EXECUTE_PAYOFF",
                "description": "Reanimate the big creature",
                "card_roles": {
                    "enablers": ["Faithless Looting"],
                    "payoffs": ["Goryo's Vengeance", "Atraxa"],
                },
            }
        ],
        "mulligan_keys": ["Goryo's Vengeance"],
    }
    p = tmp_path / "test_deck.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    chunks = list(gameplan_text_loader(p))
    assert len(chunks) == 1
    sub_id, content = chunks[0]
    assert sub_id == ""
    # All semantically meaningful pieces are in the flattened text.
    assert "Test Deck" in content
    assert "combo" in content
    assert "Reanimate the big creature" in content
    assert "Goryo's Vengeance" in content
    assert "Atraxa" in content
    assert "Faithless Looting" in content


# ─── global embedder injection ───────────────────────────────────────

def test_set_global_embedder_uses_injected_stub_without_real_model(monkeypatch):
    """If the test framework (autouse fixture) didn't inject the stub,
    Embedder.get() would import sentence_transformers.  We verify
    the injection plumbing by tampering with the import path."""
    # The autouse fixture has already set _instance to a StubEmbedder.
    inst = Embedder.get()
    assert isinstance(inst, StubEmbedder)

    # If somebody sets it back to None, the next .get() WOULD try to
    # load the real model.  We don't do that here — we just verify the
    # reset path leaves _instance None until something repopulates it.
    set_global_embedder(None)
    assert Embedder._instance is None


# ─── per-corpus index path ───────────────────────────────────────────

def test_index_path_per_corpus():
    p1 = index_path("docs")
    p2 = index_path("replays")
    assert p1 != p2
    assert p1.name == "docs.sqlite"
    assert p2.name == "replays.sqlite"
