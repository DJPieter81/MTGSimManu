"""Oracle-runtime-parse ratchet — pytest integration.

Wraps tools/check_oracle_runtime_parse.py so the existing `pytest tests/ -q`
workflow catches new oracle-text substring checks in engine/ or ai/ code before
they reach CI.

The rule: every card property derivable from oracle text must be parsed once in
engine/oracle_parser.py at load time and stored as a typed field on CardTemplate.
Runtime code in engine/ and ai/ reads the field; oracle_parser.py is the only
module that may examine the raw oracle string.

The actual detection logic lives in tools/check_oracle_runtime_parse.py; this
is just a thin adapter so the contract runs as part of the standard test suite,
mirroring the pattern used by test_zone_mutation_ratchet.py and
test_magic_numbers_ratchet.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "check_oracle_runtime_parse.py"
BASELINE = ROOT / "tools" / "oracle_runtime_parse_baseline.json"


def test_oracle_runtime_parse_baseline_file_exists():
    assert BASELINE.exists(), (
        f"Missing {BASELINE.relative_to(ROOT)} — oracle-runtime-parse ratchet "
        f"not installed. Run `python tools/check_oracle_runtime_parse.py --list` "
        f"then write the baseline JSON with the current total."
    )
    data = json.loads(BASELINE.read_text())
    assert "total" in data, "baseline must have a 'total' key"
    assert isinstance(data["total"], int) and data["total"] >= 0


def test_oracle_runtime_parse_check_script_exists():
    assert SCRIPT.exists(), f"Missing {SCRIPT.relative_to(ROOT)}"


def test_oracle_runtime_parse_ratchet():
    """Oracle-text substring checks in engine/ or ai/ outside oracle_parser.py
    and card_database.py do not exceed the baseline in
    tools/oracle_runtime_parse_baseline.json.

    To migrate a violation: move the oracle-text inspection into
    engine/oracle_parser.py, add a typed field on CardTemplate, populate it
    in CardDatabase at load time, and replace the runtime check with the field
    lookup.  Then lower the baseline total in the same commit.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise AssertionError(
            f"Oracle-runtime-parse ratchet failed:\n{msg}\n\n"
            f"To see all violations: python tools/check_oracle_runtime_parse.py --list\n"
            f"To lower the baseline, migrate the check to a typed CardTemplate "
            f"field populated in oracle_parser.py at load time."
        )
