"""Wraps tools/check_single_owner.py so `pytest tests/ -q` catches a new
second path re-implementing a rule's check (target picks outside the
target solver, damage/life writes outside engine/damage.py, counter writes
outside the counter primitive). The logic lives in the tool; this is the
thin bridge, like tests/test_zone_mutation_ratchet.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "check_single_owner.py"
BASELINE = ROOT / "tools" / "single_owner_baseline.json"


def test_baseline_file_exists_with_the_three_categories():
    assert BASELINE.exists()
    data = json.loads(BASELINE.read_text())
    assert {"target_pick", "damage_write", "counter_write"} <= set(data)


def test_single_owner_ratchet_passes():
    proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                          cwd=str(ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_new_direct_target_pick_would_fail(tmp_path, monkeypatch):
    """A handler that picks from the opponent's creatures and acts without
    the solver is counted; the ratchet's own detector sees it."""
    from tools import check_single_owner as cso
    src = (
        "def bad_handler(game, card, controller, targets=None, item=None):\n"
        "    opp = game.players[1 - controller]\n"
        "    target = max(opp.creatures, key=lambda c: c.power or 0)\n"
        "    game._exile_permanent(target)\n"
        "\n"
        "def good_handler(game, card, controller, targets=None, item=None):\n"
        "    opp = game.players[1 - controller]\n"
        "    pool = legal_targets(game, controller, card, opp.creatures)\n"
        "    if pool:\n"
        "        game._exile_permanent(max(pool, key=lambda c: c.power or 0))\n"
    )
    p = tmp_path / "engine" / "card_effects.py"
    p.parent.mkdir()
    p.write_text(src)
    monkeypatch.setattr(cso, "REPO_ROOT", tmp_path)
    hits = cso._target_pick_hits(p)
    assert [h[2] for h in hits] == ["bad_handler"]
