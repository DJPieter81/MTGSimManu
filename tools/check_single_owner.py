#!/usr/bin/env python3
"""Single-owner ratchet — one code path owns each rule; second paths that
re-implement a rule's check are where the 2026-09 defects lived.

Counts, per category, the places that bypass the rule's owner, and fails
when any category GROWS over `tools/single_owner_baseline.json`:

  target_pick   — a function in engine/card_effects.py or engine/game_runner.py
                  that picks from the opponent's creatures/battlefield and acts
                  on the pick, without `can_be_targeted` / `legal_targets` in its
                  body and without saying "Not targeting" (the opponent's own
                  choice, CR 701.17). Owner: engine/target_solver.
  damage_write  — a direct `damage_marked +=` / `.life -=` / `.life +=` outside
                  engine/damage.py. Owner: engine/damage.deal_damage (lifelink,
                  deathtouch, prevention live there).
  counter_write — a direct `plus_counters =`/`+=`/`-=` outside engine/cards.py.
                  Owner: CardInstance.add_plus_counters (the counter primitive).

    python tools/check_single_owner.py            # check (CI + pre-commit)
    python tools/check_single_owner.py --list     # show every hit
    python tools/check_single_owner.py --update   # rewrite the baseline (reduce only)

Reducing a count lowers the baseline in the same commit; growing one
fails. True exceptions get `# single-owner-allow: <reason>` on the line.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = REPO_ROOT / "tools" / "single_owner_baseline.json"
ALLOW = "single-owner-allow:"

_TARGET_FILES = ("engine/card_effects.py", "engine/game_runner.py")
_DAMAGE_OWNER = "engine/damage.py"
_COUNTER_OWNER = "engine/cards.py"
_SCAN_DIRS = ("engine", "ai")

_DAMAGE_RE = re.compile(r"\.damage_marked\s*\+=|\.life\s*-=|\.life\s*\+=")
_COUNTER_RE = re.compile(r"\.plus_counters\s*(=|\+=|-=)(?!=)")


def _target_pick_hits(path: Path):
    src = path.read_text()
    tree = ast.parse(src)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.get_source_segment(src, node) or ""
        picks = (".creatures" in body or ".battlefield" in body) and (
            "max(" in body or "for c in" in body or "min(" in body)
        acts = any(k in body for k in ("_exile_permanent(", "_permanent_destroyed(",
                                        "deal_damage(", "damage_marked +=",
                                        "_creature_dies(", ".tapped = True"))
        solver = any(m in body for m in ("can_be_targeted", "legal_targets",
                                         "_removal_legal_pool", "enumerate_legal_targets"))
        if picks and acts and not solver and "Not targeting" not in body \
                and ALLOW not in body:
            hits.append((str(path.relative_to(REPO_ROOT)), node.lineno, node.name))
    return hits


def _regex_hits(rx, owner_rel: str):
    hits = []
    for d in _SCAN_DIRS:
        for path in sorted((REPO_ROOT / d).rglob("*.py")):
            rel = str(path.relative_to(REPO_ROOT))
            if rel == owner_rel:
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if rx.search(line) and ALLOW not in line and not line.lstrip().startswith("#"):
                    hits.append((rel, i, line.strip()[:80]))
    return hits


def scan() -> dict:
    return {
        "target_pick": [h for f in _TARGET_FILES for h in _target_pick_hits(REPO_ROOT / f)],
        "damage_write": _regex_hits(_DAMAGE_RE, _DAMAGE_OWNER),
        "counter_write": _regex_hits(_COUNTER_RE, _COUNTER_OWNER),
    }


def load_baseline() -> dict:
    if not BASELINE_FILE.exists():
        return {}
    return {k: v for k, v in json.loads(BASELINE_FILE.read_text()).items()
            if not k.startswith("_")}


def write_baseline(counts: dict) -> None:
    payload = {"_comment": "Single-owner ratchet baseline (tools/check_single_owner.py). "
                           "Each count may only fall; lower it in the same commit that "
                           "routes a path through the rule's owner.", **counts}
    BASELINE_FILE.write_text(json.dumps(payload, indent=2) + "\n")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    hits = scan()
    counts = {k: len(v) for k, v in hits.items()}
    if "--list" in argv:
        for k, v in hits.items():
            print(f"== {k} ({len(v)})")
            for h in v:
                print("  " + ":".join(str(x) for x in h))
        return 0
    if "--update" in argv:
        write_baseline(counts)
        print(f"Wrote {BASELINE_FILE}: {counts}")
        return 0
    baseline = load_baseline()
    if not baseline:
        print("Single-owner ratchet: no baseline — run with --update first.", file=sys.stderr)
        return 1
    bad = {k: (counts[k], baseline.get(k, 0)) for k in counts if counts[k] > baseline.get(k, 0)}
    stale = {k: (counts[k], baseline.get(k, 0)) for k in counts if counts[k] < baseline.get(k, 0)}
    if bad:
        print("Single-owner ratchet FAILED — a second path re-implements a rule's check:",
              file=sys.stderr)
        for k, (now, base) in bad.items():
            print(f"  {k}: {now} > baseline {base}", file=sys.stderr)
        print("Route it through the owner (target_solver / engine.damage / "
              "CardInstance.add_plus_counters) or mark a true exception with "
              f"`# {ALLOW} <reason>`.", file=sys.stderr)
        return 1
    if stale:
        print("Single-owner ratchet: baseline is stale (counts fell) — claim it: "
              "python tools/check_single_owner.py --update", file=sys.stderr)
        for k, (now, base) in stale.items():
            print(f"  {k}: {now} < baseline {base}", file=sys.stderr)
        return 1
    print(f"Single-owner ratchet OK: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
