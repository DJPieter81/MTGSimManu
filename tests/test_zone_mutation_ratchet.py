"""Zone-mutation ratchet — pytest integration.

Wraps tools/check_zone_mutation.py so the existing `pytest tests/ -q`
workflow catches new zone-transfer-funnel bypasses before they reach
CI. See docs/design/rules-foundation-sweep-tracker.md (item 0a) and
CLAUDE.md ABSTRACTION CONTRACT for the ratchet pattern this mirrors.

The actual logic lives in tools/check_zone_mutation.py; this is just a
thin adapter so the contract runs as part of the standard test suite.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "check_zone_mutation.py"
BASELINE = ROOT / "tools" / "zone_mutation_baseline.json"


def test_zone_mutation_baseline_file_exists():
    assert BASELINE.exists(), (
        f"Missing {BASELINE.relative_to(ROOT)} — zone-mutation ratchet not "
        f"installed. See docs/design/rules-foundation-sweep-tracker.md."
    )
    data = json.loads(BASELINE.read_text())
    for k, v in data.items():
        if k.startswith("_"):
            continue
        assert isinstance(v, int) and v >= 0, f"bad baseline entry {k}={v!r}"


def test_zone_mutation_check_script_exists():
    assert SCRIPT.exists(), f"Missing {SCRIPT.relative_to(ROOT)}"


def test_zone_mutation_ratchet():
    """Raw `<card>.zone = ...` assignments outside the zone-transfer
    funnel (engine/zone_manager.py, engine/zone_transfer.py) do not
    exceed the per-file baseline — new zone-change code must route
    through the funnel so CR 603 triggers and CR 614 replacement
    effects actually fire."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise AssertionError(
            f"Zone-mutation ratchet failed:\n{msg}\n\n"
            f"See docs/design/rules-foundation-sweep-tracker.md for context."
        )
