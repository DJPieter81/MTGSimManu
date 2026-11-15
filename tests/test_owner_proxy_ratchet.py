"""The owner-proxy ratchet (blind-spot class B1) holds, and sees a proxy.

A question the engine owns once is not re-answered elsewhere with a cheaper
proxy (`tools/check_owner_proxies.py`;
docs/design/2026-11-15_root_cause_blindspot_analysis.md).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location(
        "check_owner_proxies", ROOT / "tools" / "check_owner_proxies.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_owner_proxy_counts_hold_their_baseline():
    assert _tool().main([]) == 0


def test_each_known_proxy_shape_is_seen_and_an_annotated_one_is_not():
    t = _tool()
    shapes = {
        "target_count": "if snap.opp_creature_count == 0 and 'removal' in tags:",
        "tap_colours": "colours = set(land.template.produces_mana)",
        "mana_total_pay": "if player.available_mana_estimate < cost.cmc:",
        "enter_queue": "game.trigger_etb(card, controller)",
    }
    for cat, line in shapes.items():
        rx = t.CATEGORIES[cat][1]
        assert rx.search(line), cat
    allowed = shapes["target_count"] + f"  # {t.ALLOW} a combat question"
    assert t.ALLOW in allowed
