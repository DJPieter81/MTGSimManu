"""CardDatabase.load must cross-check the meta.merged_from stamp.

When the merged DB carries a provenance stamp (written by merge_db.py)
whose card_count disagrees with the number of entries actually in the
file, the loader warns loudly — the file was truncated, hand-edited, or
assembled outside merge_db.py (the silent-stale-DB failure class of
2026-07-05). It must NEVER crash on a bad/mismatched stamp, and a file
with no stamp at all (pre-stamp merges, fixtures) loads silently.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.card_database import CardDatabase  # noqa: E402

# Above _MIN_REAL_DB_CARDS so the automerge recovery path stays dormant.
N_CARDS = 1200
MARKER = "PROVENANCE MISMATCH"


def _write_db(path: Path, meta) -> None:
    data = {
        f"Synthetic Card {i}": [{"types": ["Creature"]}]
        for i in range(N_CARDS)
    }
    body = {"data": data}
    if meta is not None:
        body["meta"] = meta
    path.write_text(json.dumps(body))


def test_stamp_card_count_mismatch_warns_loudly(tmp_path, capsys):
    p = tmp_path / "db.json"
    _write_db(p, {"merged_from": {
        "part_count": 8, "card_count": N_CARDS + 999,
        "part_names": ["ModernAtomic_part1.json"]}})

    db = CardDatabase(json_path=str(p))  # must not raise

    out = capsys.readouterr().out
    assert MARKER in out, (
        "loader stayed silent on a merged_from.card_count that disagrees "
        "with the file's actual entry count"
    )
    assert len(db.cards) >= N_CARDS  # warned, but still loaded


def test_matching_stamp_is_silent(tmp_path, capsys):
    p = tmp_path / "db.json"
    _write_db(p, {"merged_from": {
        "part_count": 8, "card_count": N_CARDS,
        "part_names": ["ModernAtomic_part1.json"]}})

    CardDatabase(json_path=str(p))

    assert MARKER not in capsys.readouterr().out


def test_absent_stamp_is_silent(tmp_path, capsys):
    p = tmp_path / "db.json"
    _write_db(p, {})  # meta present, no merged_from

    CardDatabase(json_path=str(p))

    assert MARKER not in capsys.readouterr().out


def test_malformed_stamp_never_crashes(tmp_path, capsys):
    p = tmp_path / "db.json"
    _write_db(p, {"merged_from": "not a dict"})

    db = CardDatabase(json_path=str(p))  # must not raise

    assert len(db.cards) >= N_CARDS
