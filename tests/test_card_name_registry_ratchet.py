"""Contract test: the card-name-keyed registry surface may only shrink.

CLAUDE.md bans new `card.name == "X"` gates and pins that at 0 via
tools/check_abstraction.py — but it also records, explicitly, that
`EFFECT_REGISTRY.register("Card Name", ...)` entries are **invisible to that
ratchet's regex**, and that "that blindness is not permission".  This adapter
runs tools/check_card_name_registry.py as part of the standard suite so the
blind surface is enforced like every other contract rule, mirroring
test_oracle_runtime_parse_ratchet.py / test_zone_mutation_ratchet.py.

Direction of travel: the count goes DOWN when a per-card handler is replaced
by a mechanic class (the X-creature-tutor consolidation deleted
green_suns_zenith_resolve, taking it 97 -> 96).  It must never go up.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "check_card_name_registry.py"
BASELINE = ROOT / "tools" / "card_name_registry_baseline.json"


def test_card_name_registry_baseline_file_exists():
    assert BASELINE.exists(), (
        f"Missing {BASELINE.relative_to(ROOT)} — card-name-registry ratchet "
        f"not installed. Run `python tools/check_card_name_registry.py --list` "
        f"then write the baseline JSON with the current total."
    )
    data = json.loads(BASELINE.read_text())
    assert "total" in data, "baseline must have a 'total' key"
    assert isinstance(data["total"], int) and data["total"] >= 0


def test_card_name_registry_check_script_exists():
    assert SCRIPT.exists(), f"Missing {SCRIPT.relative_to(ROOT)}"


def test_card_name_registry_ratchet():
    """Per-card registry entries must exactly equal the recorded baseline.

    - total > baseline: a new per-card handler was added → fail.  Build the
      mechanic class instead (parse the shape once into a typed CardTemplate
      field and dispatch off it), or refuse the variant outright.
    - total < baseline: an improvement was made but not recorded → fail;
      lower the baseline in the same commit.
    - total == baseline: pass.
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
            f"Card-name-registry ratchet failed:\n{msg}\n\n"
            f"To see every entry: python tools/check_card_name_registry.py --list"
        )


def test_card_name_registry_growth_is_rejected():
    """A baseline BELOW the actual count must fail — i.e. adding a per-card
    handler without shrinking something else is a regression.

    Simulated by pointing the checker at a deliberately low baseline, so the
    guard itself is proven rather than assumed.
    """
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"total": 0}, f)
        temp_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--baseline", temp_path],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.returncode != 0, (
            "ratchet passed with a baseline of 0 — growth would go undetected"
        )
        assert "grew" in (result.stdout + result.stderr)
    finally:
        os.unlink(temp_path)


def test_card_name_registry_stale_high_baseline_fails():
    """A baseline ABOVE the actual count must also fail, so a consolidation
    that deletes handlers cannot leave an inflated ceiling for the next
    change to silently re-fill.
    """
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
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
            "ratchet passed with an inflated baseline — improvements could go "
            "unrecorded and the ceiling would be re-fillable"
        )
        assert "stale" in (result.stdout + result.stderr)
    finally:
        os.unlink(temp_path)
