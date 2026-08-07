"""merge_db.py must stamp the merged DB with its own provenance.

`meta.merged_from = {part_count, card_count, part_names}` records what
the merge actually saw, so a later loader can detect a merged file that
was truncated, hand-edited, or built from a different part set — the
silent-stale-DB failure class of 2026-07-05 (PRs #451/#454).

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


def test_merge_writes_merged_from_stamp(tmp_path):
    shutil.copy(MERGE_DB, tmp_path / "merge_db.py")
    with open(tmp_path / "ModernAtomic_part1.json", "w") as f:
        json.dump({"meta": {}, "data": {"Card A": {}, "Card B": {}}}, f)
    with open(tmp_path / "ModernAtomic_part2.json", "w") as f:
        json.dump({"Card C": {}}, f)

    proc = subprocess.run(
        [sys.executable, str(tmp_path / "merge_db.py")],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout

    with open(tmp_path / "ModernAtomic.json") as f:
        raw = json.load(f)
    stamp = raw.get("meta", {}).get("merged_from")
    assert stamp, "merge_db.py wrote no meta.merged_from provenance stamp"
    assert stamp["part_count"] == 2
    assert stamp["card_count"] == 3
    assert stamp["part_names"] == [
        "ModernAtomic_part1.json", "ModernAtomic_part2.json"]
