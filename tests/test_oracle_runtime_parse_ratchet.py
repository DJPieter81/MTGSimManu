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
    and card_database.py must equal the baseline in
    tools/oracle_runtime_parse_baseline.json.

    Baseline must equal actual count exactly:
    - total > baseline: regression (new check added) → fail
    - total < baseline: improvement unclaimed (baseline stale) → fail
    - total == baseline: pass

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
            f"To update the baseline, set tools/oracle_runtime_parse_baseline.json "
            f"'total' to the actual violation count in the same commit as your changes."
        )


def _detector():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_crop", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _count_source(src: str) -> int:
    import os
    import tempfile
    from pathlib import Path as _P
    mod = _detector()
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(src)
        p = f.name
    try:
        return len(mod._count_violations(_P(p)))
    finally:
        os.unlink(p)


def test_ast_detector_catches_oracle_under_any_variable_name():
    """The whole point of the AST rewrite: an oracle-text variable named
    anything (not just the old regex's 6 whitelisted prefixes) is caught, and
    both `in` and `not in` forms count. The bare binding is NOT a violation;
    a same-named non-oracle variable is NOT a violation."""
    m = _detector()
    # Non-whitelisted name `o`, bound from .oracle_text, then `in` tested.
    assert _count_source(
        "def f(c):\n"
        "    o = (c.template.oracle_text or '').lower()\n"
        "    return 'flying' in o\n") == 1
    # `not in` form is caught too.
    assert _count_source(
        "def f(c):\n"
        "    zo = c.template.oracle_text.lower()\n"
        "    return 'draw' not in zo\n") == 1
    # A parameter named `oracle` is tainted by convention.
    assert _count_source(
        "def f(oracle):\n"
        "    return oracle.count('enters')\n") == 1
    # A non-oracle variable of a similar name is NOT flagged.
    assert _count_source(
        "def f(c):\n"
        "    name = c.name.lower()\n"
        "    return 'bolt' in name\n") == 0
    # A bare binding with no substring/regex test is NOT a violation.
    assert _count_source(
        "def f(c):\n"
        "    o = c.template.oracle_text\n"
        "    return len(o)\n") == 0
    assert "engine/oracle_resolver.py" in m._EXCLUDED, (
        "the resolution-fallback layer must be excluded")


def test_oracle_runtime_parse_stale_high_baseline_fails():
    """Ratchet must fail when baseline > actual count (improvement made but not claimed).

    This enforces the strict-decrease rule: every oracle-migration PR must
    update the baseline to the new lower count, so the next PR cannot silently
    re-add checks up to the old inflated limit.
    """
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump({"total": 999_999}, f)
        temp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--baseline", temp_path],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.returncode != 0, (
            "Ratchet should fail when baseline total is higher than the actual "
            "violation count — the improvement must be claimed by updating the baseline."
        )
        combined = (result.stdout + result.stderr).lower()
        assert "stale" in combined, (
            f"Error message should contain 'stale' to explain the baseline-too-high "
            f"scenario. Got:\n{result.stdout}{result.stderr}"
        )
    finally:
        os.unlink(temp_path)
