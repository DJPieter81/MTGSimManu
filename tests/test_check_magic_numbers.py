"""Tests for the magic-number ratchet (tools/check_magic_numbers.py).

Most tests build crafted Python source files in a temporary directory and
invoke the scanner directly via its functions, so the tests do not depend
on the real `ai/` tree or the real baseline file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the repo root is on sys.path so we can import the script.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import check_magic_numbers as cmn  # noqa: E402


# ---------- helpers ----------

def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a fake repo with an `ai/` subdir and the given files."""
    ai = tmp_path / "ai"
    ai.mkdir()
    for name, src in files.items():
        (ai / name).write_text(src)
    return tmp_path


def _scan(tmp_path: Path) -> dict[str, int]:
    return cmn.scan_all(tmp_path)


# ---------- tests ----------

def test_baseline_seeded_to_current():
    """The baseline file matches scan_all() output for ai/ at HEAD."""
    baseline = cmn.load_baseline()
    current = cmn.scan_all()
    # Every file in current must appear in baseline with at least its count.
    for f, n in current.items():
        assert f in baseline, f"{f} missing from baseline"
        assert baseline[f] >= n, f"{f}: baseline {baseline[f]} < current {n}"


def test_ratchet_passes_at_baseline():
    """cmd_check() returns 0 when current counts match baseline."""
    rc = cmn.cmd_check()
    assert rc == 0


def test_ratchet_fails_on_increase(tmp_path, monkeypatch):
    """Adding a bare literal to a tracked file makes the ratchet fail."""
    src_ok = (
        "def f(x):\n"
        "    return x\n"
    )
    src_bad = (
        "def f(x):\n"
        "    return x * 8.0\n"
    )
    repo = _make_repo(tmp_path, {"thing.py": src_ok})
    baseline_file = tmp_path / "tools" / "magic_numbers_baseline.json"
    baseline_file.parent.mkdir()

    monkeypatch.setattr(cmn, "REPO_ROOT", repo)
    monkeypatch.setattr(cmn, "BASELINE_FILE", baseline_file)

    # Seed baseline at zero.
    cmn.write_baseline(cmn.scan_all(repo))
    assert cmn.cmd_check() == 0

    # Introduce a magic number.
    (repo / "ai" / "thing.py").write_text(src_bad)
    assert cmn.cmd_check() == 1


def test_top_level_constants_not_counted(tmp_path):
    src = (
        "THRESHOLD = 0.7\n"
        "FACTOR = 8.0\n"
        "BIG = 999\n"
        "def f():\n"
        "    return None\n"
    )
    repo = _make_repo(tmp_path, {"x.py": src})
    counts = _scan(repo)
    assert counts["ai/x.py"] == 0


def test_exempt_values_not_counted(tmp_path):
    src = (
        "def f(xs):\n"
        "    if len(xs) == 0:\n"
        "        return -1\n"
        "    return xs[1] + xs[2] - 100\n"
    )
    repo = _make_repo(tmp_path, {"y.py": src})
    counts = _scan(repo)
    assert counts["ai/y.py"] == 0


def test_magic_allow_comment_exempts_line(tmp_path):
    src = (
        "def f(x):\n"
        "    NO_CLOCK = 99.0  # magic-allow: sentinel for no creatures\n"
        "    return x + 99.0  # magic-allow: rules-derived sentinel\n"
    )
    repo = _make_repo(tmp_path, {"z.py": src})
    counts = _scan(repo)
    assert counts["ai/z.py"] == 0


def test_excluded_files_not_scanned(tmp_path, monkeypatch):
    # Put one excluded and one tracked file; only the tracked one shows up.
    excluded_src = "def f(): return 0.7\n"  # would be 1 if scanned
    tracked_src = "def g(): return 0.7\n"

    repo = _make_repo(
        tmp_path,
        {"scoring_constants.py": excluded_src, "tracked.py": tracked_src},
    )
    counts = _scan(repo)
    assert "ai/scoring_constants.py" not in counts
    assert counts["ai/tracked.py"] == 1


def test_update_refuses_to_increase(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, {"a.py": "def f(): return None\n"})
    baseline_file = tmp_path / "tools" / "magic_numbers_baseline.json"
    baseline_file.parent.mkdir()
    monkeypatch.setattr(cmn, "REPO_ROOT", repo)
    monkeypatch.setattr(cmn, "BASELINE_FILE", baseline_file)

    cmn.write_baseline({"ai/a.py": 0})

    # Add a literal — update should refuse.
    (repo / "ai" / "a.py").write_text("def f(): return 8.0\n")
    rc = cmn.cmd_update()
    assert rc == 1


def test_update_allows_decrease(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, {"a.py": "def f(): return None\n"})
    baseline_file = tmp_path / "tools" / "magic_numbers_baseline.json"
    baseline_file.parent.mkdir()
    monkeypatch.setattr(cmn, "REPO_ROOT", repo)
    monkeypatch.setattr(cmn, "BASELINE_FILE", baseline_file)

    # Seed with a higher count than current (simulating cleanup).
    cmn.write_baseline({"ai/a.py": 5})
    rc = cmn.cmd_update()
    assert rc == 0
    written = cmn.load_baseline()
    assert written["ai/a.py"] == 0


def test_string_literals_not_counted(tmp_path):
    src = (
        "def f():\n"
        "    return '3.14'\n"
        "def g():\n"
        '    return "8.0"\n'
    )
    repo = _make_repo(tmp_path, {"s.py": src})
    counts = _scan(repo)
    assert counts["ai/s.py"] == 0


def test_type_annotation_with_literal(tmp_path):
    """Literal[5] in an annotation still has the `5` ast.Constant node.

    Pragmatic position: the rule only fires on bare literals in
    expressions. We don't try to special-case Literal[]; if a value
    appears in code, it's counted (unless top-level / exempt / allow-tagged).
    Test pins the current behavior so a future change is intentional.
    """
    src = (
        "from typing import Literal\n"
        "def f(x: Literal[5] = 5) -> int:\n"
        "    return x\n"
    )
    repo = _make_repo(tmp_path, {"t.py": src})
    counts = _scan(repo)
    # Both the annotation and the default count as bare literals here.
    assert counts["ai/t.py"] >= 1


def test_count_file_handles_syntax_error_gracefully(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text("def f(:\n   pass\n")  # invalid syntax
    n, locs = cmn.count_file(bad)
    assert n == 0
    assert locs == []


def test_class_body_counts_as_inside(tmp_path):
    """Literals inside class methods ARE counted (depth tracking)."""
    src = (
        "class C:\n"
        "    K = 0.7\n"  # class body assignment — depth=1, counted
        "    def m(self):\n"
        "        return 0.7\n"
    )
    repo = _make_repo(tmp_path, {"c.py": src})
    counts = _scan(repo)
    assert counts["ai/c.py"] == 2


def test_comments_not_counted(tmp_path):
    src = (
        "def f():\n"
        "    # this is 0.7 in a comment\n"
        "    return None\n"
    )
    repo = _make_repo(tmp_path, {"c2.py": src})
    counts = _scan(repo)
    assert counts["ai/c2.py"] == 0


def test_booleans_not_counted(tmp_path):
    """In Python AST, True/False are Constant(value=True/False) — must not count."""
    src = (
        "def f(x):\n"
        "    if x:\n"
        "        return True\n"
        "    return False\n"
    )
    repo = _make_repo(tmp_path, {"b.py": src})
    counts = _scan(repo)
    assert counts["ai/b.py"] == 0
