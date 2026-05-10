"""Phase 4A — `--mcts` CLI flag smoke tests.

The opt-in flag sets ``MTGSIM_USE_MCTS=1`` in the environment so
downstream AI hooks can pick it up. Phase 5 wires the actual
forward-simulation comparison; this Week-4 deliverable lands the
flag plumbing.

Reference: docs/research/2026-05_phase_4a_ismcts_scoping.md
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(args, env=None):
    """Run run_meta.py as a subprocess; return CompletedProcess."""
    cmd = [sys.executable, str(REPO_ROOT / "run_meta.py")] + list(args)
    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)
    return subprocess.run(
        cmd, capture_output=True, text=True,
        env=proc_env, timeout=30,
    )


# ─── Flag is parsed without crashing ─────────────────────────────────


def test_mcts_flag_appears_in_help():
    """--mcts shows up in --help output."""
    proc = _run_cli(["--help"])
    assert proc.returncode == 0
    # Combined stdout/stderr — argparse may write to either.
    output = proc.stdout + proc.stderr
    assert "--mcts" in output
    assert "ISMCTS" in output


def test_mcts_flag_with_list_does_not_crash():
    """Pass --mcts alongside an action that exits early (--list).
    Exercises the flag-handling code path without running a sim."""
    proc = _run_cli(["--mcts", "--list"])
    assert proc.returncode == 0
    # The --mcts opt-in note should appear on stderr.
    assert "--mcts opt-in active" in proc.stderr


def test_no_mcts_flag_does_not_set_env(monkeypatch):
    """Without --mcts, MTGSIM_USE_MCTS must not be set by run_meta
    (we test by checking after a --list run)."""
    # Subprocess inherits the parent env minus our explicit unset.
    env = {"MTGSIM_USE_MCTS": ""}
    proc = _run_cli(["--list"], env=env)
    assert proc.returncode == 0
    # Stderr shouldn't carry the opt-in line.
    assert "--mcts opt-in active" not in proc.stderr
