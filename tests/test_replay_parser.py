"""Tests for the replay snapshot parser (Phase 0 — calibrated P_win).

These are written Option-C style: failing-first against the parser in
`tools/parse_replay_snapshots.py`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Module under test
from tools import parse_replay_snapshots as parser


REPLAYS_DIR = Path(__file__).resolve().parent.parent / "replays"


def _candidate_file() -> Path:
    """Pick a real, predictable replay file for the single-file test."""
    target = REPLAYS_DIR / "affinity_vs_boros_energy_s60001.txt"
    if target.exists():
        return target
    # Fallback: any non-empty .txt
    for f in sorted(REPLAYS_DIR.glob("*.txt")):
        if f.stat().st_size > 0:
            return f
    pytest.skip("No replay files available")


def test_parses_one_replay_file():
    """Parsing a single replay yields >=3 turn snapshots."""
    f = _candidate_file()
    snaps = list(parser.parse_replay_file(f))
    assert len(snaps) >= 3, f"Expected >=3 snapshots from {f.name}, got {len(snaps)}"
    # Each snapshot must have the required keys
    required = {
        "file", "game_idx", "turn",
        "p1_arch", "p2_arch",
        "life_p1", "life_p2",
        "hand_p1", "hand_p2",
        "lands_p1", "lands_p2",
        "lib_p1", "lib_p2",
        "gy_p1", "gy_p2",
        "p1_won",
    }
    for s in snaps:
        missing = required - set(s.keys())
        assert not missing, f"Snapshot missing keys: {missing}"


def test_total_snapshots_in_range():
    """Across all replays/*.txt the parser yields between 1900 and 2200 snapshots."""
    total = 0
    for f in sorted(REPLAYS_DIR.glob("*.txt")):
        total += sum(1 for _ in parser.parse_replay_file(f))
    assert 1900 <= total <= 2200, (
        f"Expected snapshots in [1900, 2200], got {total}. "
        f"Format may have drifted."
    )


def test_label_extraction():
    """Every snapshot has p1_won as a bool (True or False)."""
    seen = 0
    for f in sorted(REPLAYS_DIR.glob("*.txt"))[:10]:
        for snap in parser.parse_replay_file(f):
            assert isinstance(snap["p1_won"], bool), (
                f"p1_won must be bool, got {type(snap['p1_won']).__name__} in {f.name}"
            )
            seen += 1
    assert seen > 0, "No snapshots parsed at all from first 10 files"


def test_format_version_pinned():
    """REPLAY_FORMAT_V is the schema version constant."""
    assert hasattr(parser, "REPLAY_FORMAT_V")
    assert parser.REPLAY_FORMAT_V == 1
