"""merge_db.py default mode must REBUILD from parts alone, not merge-into.

Merging into a pre-existing ModernAtomic.json lets stale keys survive
forever: a card removed from the parts (e.g. the 30 fabricated texts
pulled on 2026-07-05) stays in the merged DB on every machine that ever
loaded it, because `cards.update(chunk)` never deletes. The default run
must therefore start from an empty skeleton so the output is a pure
function of the part files. The old merge-into behaviour remains
available behind an explicit `--incremental` flag.

Drives the real script in a sandbox, mirroring
tests/test_merge_db_numeric_part_order.py.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MERGE_DB = REPO_ROOT / "merge_db.py"


def _setup_stale_db(tmp_path: Path) -> None:
    shutil.copy(MERGE_DB, tmp_path / "merge_db.py")
    # Pre-existing merged DB carrying a key NO part provides anymore.
    with open(tmp_path / "ModernAtomic.json", "w") as f:
        json.dump({"meta": {}, "data": {"Stale Card": {"power": "1"}}}, f)
    # Parts lack the stale key.
    with open(tmp_path / "ModernAtomic_part1.json", "w") as f:
        json.dump({"meta": {}, "data": {"Fresh Card": {"power": "2"}}}, f)


def _run_merge(tmp_path: Path, *args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(tmp_path / "merge_db.py"), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    with open(tmp_path / "ModernAtomic.json") as f:
        raw = json.load(f)
    return raw.get("data", raw)


def test_default_merge_rebuilds_from_parts_alone(tmp_path):
    _setup_stale_db(tmp_path)
    cards = _run_merge(tmp_path)
    assert "Fresh Card" in cards
    assert "Stale Card" not in cards, (
        "default merge kept a key absent from all parts — merge-into "
        "semantics let stale card data survive part removals"
    )


def test_incremental_flag_keeps_existing_keys(tmp_path):
    _setup_stale_db(tmp_path)
    cards = _run_merge(tmp_path, "--incremental")
    assert "Fresh Card" in cards
    assert "Stale Card" in cards, (
        "--incremental must preserve the old merge-into behaviour"
    )
