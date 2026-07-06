"""CardDatabase auto-merge must be bounded and reload the canonical DB.

The loader's "DB too small" recovery path runs merge_db.py and reloads.
Two invariants, both violated on 2026-07-05 (CI run 28756950990, every
PR red): the reload must target the CANONICAL project-root
ModernAtomic.json that merge_db.py writes — reloading the same
too-small candidate path (e.g. the committed 48-card walkthrough
fixture) loops forever — and the merge attempt must happen AT MOST
ONCE per database instance, so a still-small result degrades to a
loud warning instead of unbounded recursion ending in RecursionError
inside the JSON decoder (~2h of repeated full merges per CI job).

No real 150MB merges run here: subprocess.run is monkeypatched and the
canonical path is redirected into tmp_path.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.card_database import CardDatabase  # noqa: E402


def _write_db(path: Path, n_cards: int) -> None:
    data = {
        f"Synthetic Card {i}": [{"types": ["Creature"]}]
        for i in range(n_cards)
    }
    path.write_text(json.dumps({"meta": {}, "data": data}))


@pytest.fixture
def small_db(tmp_path):
    p = tmp_path / "small_db.json"
    _write_db(p, 3)
    return p


def _patch_canonical(monkeypatch, tmp_path):
    canonical = tmp_path / "ModernAtomic.json"
    monkeypatch.setattr(
        CardDatabase,
        "_canonical_db_path",
        lambda self: str(canonical),
        raising=False,
    )
    return canonical


def test_automerge_attempted_at_most_once_when_merge_produces_nothing(
        monkeypatch, tmp_path, small_db):
    """A merge that fails to grow the DB must not trigger another merge:
    one attempt, then a plain (small) load — no recursion, no error."""
    calls = []
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **k: calls.append(a) or None)
    _patch_canonical(monkeypatch, tmp_path)  # path never created

    db = CardDatabase(json_path=str(small_db))  # must not RecursionError

    assert len(calls) == 1, (
        f"merge_db.py invoked {len(calls)} times for one load — the "
        "auto-merge retry is unbounded"
    )
    assert len(db.cards) < 1000  # degraded but alive


def test_automerge_reload_targets_canonical_db_not_original_candidate(
        monkeypatch, tmp_path, small_db):
    """After a successful merge, the reload must pick up the merged
    project-root ModernAtomic.json, not re-read the small candidate
    that triggered the merge."""
    canonical = _patch_canonical(monkeypatch, tmp_path)

    def fake_merge(*args, **kwargs):
        _write_db(canonical, 1200)

    monkeypatch.setattr("subprocess.run", fake_merge)

    db = CardDatabase(json_path=str(small_db))

    assert len(db.cards) >= 1200, (
        f"reload saw {len(db.cards)} cards — it re-read the too-small "
        "candidate path instead of the canonical merged DB"
    )


def test_automerge_still_small_result_does_not_recurse(
        monkeypatch, tmp_path, small_db):
    """If the merged canonical DB is itself too small (corrupt parts),
    the loader must stop after the single retry rather than recursing."""
    canonical = _patch_canonical(monkeypatch, tmp_path)
    calls = []

    def fake_merge(*args, **kwargs):
        calls.append(1)
        _write_db(canonical, 5)

    monkeypatch.setattr("subprocess.run", fake_merge)

    db = CardDatabase(json_path=str(small_db))

    assert len(calls) == 1
    assert len(db.cards) < 1000
