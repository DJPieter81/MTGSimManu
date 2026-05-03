"""Magic-number ratchet — pytest integration.

Wraps tools/check_magic_numbers.py so the existing `pytest tests/ -q`
workflow catches contract violations before they reach CI. See
CLAUDE.md ABSTRACTION CONTRACT.

The actual logic lives in tools/check_magic_numbers.py; this is just a
thin adapter so the contract runs as part of the standard test suite.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "check_magic_numbers.py"
BASELINE = ROOT / "tools" / "magic_numbers_baseline.json"


def test_magic_numbers_baseline_file_exists():
    assert BASELINE.exists(), (
        f"Missing {BASELINE.relative_to(ROOT)} — magic-number ratchet not "
        f"installed. See CLAUDE.md ABSTRACTION CONTRACT."
    )
    data = json.loads(BASELINE.read_text())
    # Every non-comment entry should be a non-negative int.
    for k, v in data.items():
        if k.startswith("_"):
            continue
        assert isinstance(v, int) and v >= 0, f"bad baseline entry {k}={v!r}"


def test_magic_numbers_check_script_exists():
    assert SCRIPT.exists(), f"Missing {SCRIPT.relative_to(ROOT)}"


def test_magic_numbers_ratchet():
    """Bare numeric literals in ai/*.py do not exceed baseline."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise AssertionError(
            f"Magic-number ratchet failed:\n{msg}\n\n"
            f"See CLAUDE.md ABSTRACTION CONTRACT for fix options."
        )
