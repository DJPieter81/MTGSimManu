"""merge_db.py must merge ModernAtomic parts in NUMERIC suffix order.

Later parts carry updated versions of cards from earlier parts, so the
merge order is load-bearing: part10 must be applied AFTER part2..part9.
A lexicographic sort ("part10" < "part2") would let stale early-part
entries overwrite the updated ones — the same stale-DB failure class
that produced the 2026-07-05 CI/local WR-anchor divergence (part9
missing from a hand-rolled 1..8 merge).

The test drives the real script in a sandbox with synthetic parts and
asserts double-digit parts win over single-digit ones for the same card.
Both part shapes (wrapper and bare dict) are exercised, mirroring
tests/conftest.py's sidecar assembly.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MERGE_DB = REPO_ROOT / "merge_db.py"


def _run_merge(tmp_path: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(tmp_path / "merge_db.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    with open(tmp_path / "ModernAtomic.json") as f:
        raw = json.load(f)
    return raw.get("data", raw)


def test_double_digit_part_overrides_single_digit_part(tmp_path):
    shutil.copy(MERGE_DB, tmp_path / "merge_db.py")
    # part2: wrapper shape, stale version of the card
    with open(tmp_path / "ModernAtomic_part2.json", "w") as f:
        json.dump({"meta": {}, "data": {"Test Card": {"power": "1"}}}, f)
    # part10: bare shape, updated version of the same card
    with open(tmp_path / "ModernAtomic_part10.json", "w") as f:
        json.dump({"Test Card": {"power": "9"}}, f)

    cards = _run_merge(tmp_path)
    assert cards["Test Card"]["power"] == "9", (
        "part10 merged before part2 — merge order is lexicographic, "
        "stale card data overwrote the updated part"
    )


def test_both_part_shapes_contribute_cards(tmp_path):
    shutil.copy(MERGE_DB, tmp_path / "merge_db.py")
    with open(tmp_path / "ModernAtomic_part1.json", "w") as f:
        json.dump({"meta": {}, "data": {"Wrapper Card": {}}}, f)
    with open(tmp_path / "ModernAtomic_part2.json", "w") as f:
        json.dump({"Bare Card": {}}, f)

    cards = _run_merge(tmp_path)
    assert "Wrapper Card" in cards and "Bare Card" in cards
