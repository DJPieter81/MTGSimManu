#!/usr/bin/env python3
"""Ratchet: temporal and rule-effect state has one read path and one clock.

Counts, across engine/ and ai/, and pins (may only SHRINK):

  * raw_reads   — reads of rule-effect state outside its owners: gates must
                  ask engine/rules_query.py instead of reading the player
                  effect fields, the lockout set, the draw-limit scan or the
                  cost-reducer scan directly.
  * clock_bypass — reset / expiry calls outside engine/turn_clock.py: every
                  turn-boundary reset is a clock subscriber.

Owners (excluded): the modules that hold or define the state
(player_state.py, rules_query.py, turn_clock.py, effect_model.py,
continuous_effects.py) and definitions (`def …`). Writers that register an
effect and the rules auditor's independent restatements carry
`# temporal-allow: <reason>` until their family moves onto the effect model.

    python tools/check_temporal_state.py            # check
    python tools/check_temporal_state.py --list     # show every counted line
    python tools/check_temporal_state.py --update   # lower the baseline
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tools" / "temporal_state_baseline.json"

_OWNERS = {
    "engine/player_state.py", "engine/rules_query.py", "engine/turn_clock.py",
    "engine/effect_model.py", "engine/continuous_effects.py",
}
_RAW_READ = re.compile(
    r"cannot_attack_this_turn|cannot_be_attacked_this_turn|"
    r"combat_damage_prevented_this_turn|silenced_this_turn|"
    r"spell_types_prohibited_this_turn|flash_permission_types|temp_cost_rules|"
    r"_sorcery_speed_lockout_set\(|_draw_limit_for\(|\bcount_cost_reducers\(")
_CLOCK_BYPASS = re.compile(
    r"\.reset_turn_tracking\(\)|\.reset_cross_turn_event_counters\(\)|"
    r"\.cleanup_end_of_turn\(\)|\.cleanup_until_next_turn\(|"
    r"\.cleanup_end_of_combat\(\)|fire_delayed_triggers\(DelayedTriggerStep\.(UPKEEP|END_STEP)\)")


def _scan():
    found = {"raw_reads": [], "clock_bypass": []}
    for base in ("engine", "ai"):
        for path in sorted((ROOT / base).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in _OWNERS:
                continue
            for n, line in enumerate(path.read_text().splitlines(), 1):
                code = line.split("#", 1)[0]
                if "temporal-allow:" in line or code.lstrip().startswith("def "):
                    continue
                if _RAW_READ.search(code):
                    found["raw_reads"].append(f"{rel}:{n}: {line.strip()}")
                if _CLOCK_BYPASS.search(code):
                    found["clock_bypass"].append(f"{rel}:{n}: {line.strip()}")
    return found


def main(argv) -> int:
    found = _scan()
    counts = {k: len(v) for k, v in found.items()}
    if "--list" in argv:
        for k, rows in found.items():
            print(f"== {k} ({len(rows)})")
            for r in rows:
                print("  " + r)
    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    if "--update" in argv:
        grew = {k: v for k, v in counts.items() if v > base.get(k, v)}
        if grew:
            print(f"Refusing to raise the baseline: {grew}")
            return 1
        BASELINE.write_text(json.dumps({"_comment": (
            "Temporal-state ratchet (tools/check_temporal_state.py). May only "
            "fall: gates read rule-effect state through engine/rules_query.py; "
            "resets run as engine/turn_clock.py subscribers."), **counts},
            indent=2) + "\n")
        print(f"Baseline updated: {counts}")
        return 0
    bad = {k: (v, base.get(k)) for k, v in counts.items()
           if base.get(k) is not None and v > base[k]}
    if bad:
        print("Temporal-state ratchet FAILED — counts grew: "
              + ", ".join(f"{k} {b} -> {v}" for k, (v, b) in bad.items()))
        print("Read rule-effect state through engine/rules_query.py; run resets "
              "as engine/turn_clock.py subscribers. See --list.")
        return 1
    stale = {k: (v, base.get(k)) for k, v in counts.items()
             if base.get(k) is not None and v < base[k]}
    if stale:
        print("FAIL: baseline is stale — "
              + ", ".join(f"{k} {b} -> {v}" for k, (v, b) in stale.items())
              + ". Lower it in this commit: --update.")
        return 1
    print(f"Temporal-state ratchet OK — {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
