"""Abstraction contract — pytest integration.

Wraps tools/check_abstraction.py so the existing `pytest tests/ -q` workflow
catches contract violations before they reach CI. See CLAUDE.md ABSTRACTION
CONTRACT.

The actual logic lives in tools/check_abstraction.py; this is just a thin
adapter so the contract runs as part of the standard test suite.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "check_abstraction.py"
BASELINE = ROOT / "tools" / "abstraction_baseline.json"


def test_abstraction_baseline_file_exists():
    assert BASELINE.exists(), (
        f"Missing {BASELINE.relative_to(ROOT)} — abstraction contract not "
        f"installed. See CLAUDE.md."
    )
    data = json.loads(BASELINE.read_text())
    assert "hardcoded_name_count" in data
    assert isinstance(data["hardcoded_name_count"], int)
    assert data["hardcoded_name_count"] >= 0


def test_abstraction_check_script_exists():
    assert SCRIPT.exists(), f"Missing {SCRIPT.relative_to(ROOT)}"


def test_no_new_hardcoded_card_names():
    """Ratchet: hardcoded card-name conditionals in engine/+ai/ must not exceed baseline.

    To reduce the count: remove name-checks (replace with oracle-text or
    template-field checks, or move into decks/gameplans/*.json), then lower
    the number in tools/abstraction_baseline.json.

    To allow a true exception (e.g. enum check): tag the line
        # abstraction-allow: <reason>
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if result.returncode != 0:
        # Surface the script's diagnostic in the test failure
        msg = (result.stderr or result.stdout or "").strip()
        raise AssertionError(
            f"Abstraction contract violation:\n{msg}\n\n"
            f"See CLAUDE.md ABSTRACTION CONTRACT for fix options."
        )
