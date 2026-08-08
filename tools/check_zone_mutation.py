#!/usr/bin/env python3
"""Zone-mutation ratchet for the engine layer.

Counts raw `<card>.zone = ...` assignments in `engine/*.py` outside the
designated zone-transfer funnel, and enforces a non-increasing per-file
baseline stored in `tools/zone_mutation_baseline.json`.

Usage:
    python tools/check_zone_mutation.py            # check (exit 1 on regression)
    python tools/check_zone_mutation.py --list     # list per-file counts
    python tools/check_zone_mutation.py --update   # rewrite baseline (reduces only)

Why this exists
----------------
`engine/zone_manager.py` documents itself as "the ONLY sanctioned way to
change a card's zone" — moving a card any other way skips CR 603
zone-change triggers and CR 614 replacement effects. At the time this
ratchet was introduced, ~103 sites across engine/*.py bypassed it via
raw `card.zone = "..."` mutation (see
docs/design/rules-foundation-sweep-tracker.md, item 0a). Migrating all
of them at once is not required — the ratchet's job is to stop the
count from GROWING while the migration proceeds incrementally (Phase 0
migrates the highest-value tranches: death/sacrifice, discard, counter
resolution; the rest is tracked Phase 3 sweep work).

Rules:
- Counted: any `<expr>.zone = <value>` assignment (literal or
  variable), excluding `==` comparisons and commented-out lines.
- Excluded from scan entirely: `engine/zone_manager.py`,
  `engine/zone_transfer.py` — these ARE the funnel; their own zone-list
  mutation is the sanctioned implementation, not a bypass.
- A file's internal, single-owner zone-replacement helper (e.g.
  `ResolutionManager._move_resolved_spell_off_stack`) still counts —
  the ratchet doesn't distinguish "consolidated into one well-named
  place" from "scattered inline"; it only prevents the total from
  growing. Reducing the count by migrating a site to
  `zone_mgr.move_card` is real progress and should lower the baseline
  in the same commit (`--update`).

See CLAUDE.md ABSTRACTION CONTRACT for the ratchet pattern this mirrors
(tools/check_abstraction.py, tools/check_magic_numbers.py).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = REPO_ROOT / "tools" / "zone_mutation_baseline.json"

# These files ARE the zone-transfer funnel — their own zone-list
# mutation is the sanctioned implementation, not a bypass.
EXCLUDED_FILES = {
    "engine/zone_manager.py",
    "engine/zone_transfer.py",
}

# `<expr>.zone = <value>` but not `.zone == ` (comparison) and not
# inside a `#`-prefixed comment.
_ZONE_ASSIGN_RE = re.compile(r'\.zone\s*=\s*[^=]')


def _is_commented(line: str, match_start: int) -> bool:
    """True if `#` appears before the match on this line (a comment)."""
    hash_idx = line.find('#')
    return hash_idx != -1 and hash_idx < match_start


def count_file(path: Path) -> tuple[int, list[tuple[int, str]]]:
    """Return (count, [(lineno, line_text), ...]) for a single file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return 0, []
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(lines, start=1):
        m = _ZONE_ASSIGN_RE.search(line)
        if m and not _is_commented(line, m.start()):
            hits.append((i, line.strip()))
    return len(hits), hits


def scan_all(repo_root: Path | None = None) -> dict[str, int]:
    """Scan engine/*.py and return {relative_path: count}."""
    root = repo_root or REPO_ROOT
    counts: dict[str, int] = {}
    engine_dir = root / "engine"
    if not engine_dir.exists():
        return counts
    for py in sorted(engine_dir.glob("*.py")):
        rel = py.relative_to(root).as_posix()
        if rel in EXCLUDED_FILES:
            continue
        n, _ = count_file(py)
        counts[rel] = n
    return counts


def load_baseline(path: Path | None = None) -> dict[str, int]:
    p = path or BASELINE_FILE
    if not p.exists():
        return {}
    raw = json.loads(p.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, int)}


def write_baseline(counts: dict[str, int], path: Path | None = None) -> None:
    p = path or BASELINE_FILE
    payload = {
        "_comment": (
            "Zone-mutation ratchet baseline. Counts raw `<card>.zone = ...` "
            "assignments in engine/*.py outside the zone-transfer funnel "
            "(zone_manager.py, zone_transfer.py). May only decrease as "
            "sites migrate to the funnel. See CLAUDE.md ABSTRACTION "
            "CONTRACT and docs/design/rules-foundation-sweep-tracker.md."
        ),
    }
    payload.update(dict(sorted(counts.items())))
    p.write_text(json.dumps(payload, indent=2) + "\n")


def cmd_list() -> int:
    counts = scan_all()
    total = sum(counts.values())
    for f, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {n:3d}  {f}")
    print(f"\nTotal: {total} across {len(counts)} files")
    return 0


def cmd_check() -> int:
    current = scan_all()
    baseline = load_baseline()
    regressions: list[tuple[str, int, int]] = []
    for f, n in sorted(current.items()):
        base = baseline.get(f, 0)
        if n > base:
            regressions.append((f, base, n))
    if regressions:
        print("Zone-mutation ratchet failure — counts increased from baseline:", file=sys.stderr)
        for f, base, n in regressions:
            print(f"  {f}: {base} -> {n}  (delta +{n - base})", file=sys.stderr)
        print("", file=sys.stderr)
        print("Either:", file=sys.stderr)
        print("  1. Route the new zone change through engine/zone_manager.py's move_card()", file=sys.stderr)
        print("  2. Consolidate into an existing single-owner helper in this file if move_card", file=sys.stderr)
        print("     doesn't yet support this transition (e.g. stack-zone moves)", file=sys.stderr)
        print("  3. Reduce the count to <= baseline before pushing", file=sys.stderr)
        print("", file=sys.stderr)
        print("Run `python tools/check_zone_mutation.py --list` to see per-file counts.", file=sys.stderr)
        return 1
    new_files = sorted(set(current) - set(baseline))
    if new_files:
        print(f"Note: new files detected (not in baseline): {new_files}")
        print("Add them to the baseline by running `--update` (only if counts didn't increase).")
    print(
        f"Zone-mutation ratchet OK — total = {sum(current.values())} "
        f"(baseline allowed = {sum(baseline.get(f, 0) for f in current)})"
    )
    return 0


def cmd_update() -> int:
    """Reduce-only update — refuses to write a baseline that INCREASES any per-file count."""
    current = scan_all()
    baseline = load_baseline()
    if not BASELINE_FILE.exists():
        write_baseline(current)
        print(f"Baseline seeded with current counts (total = {sum(current.values())}).")
        return 0
    increases = [(f, baseline.get(f, 0), n) for f, n in current.items() if n > baseline.get(f, 0)]
    if increases:
        print("Refusing to update baseline — would INCREASE these counts:", file=sys.stderr)
        for f, base, n in increases:
            print(f"  {f}: {base} -> {n}", file=sys.stderr)
        print("", file=sys.stderr)
        print("Reduce first, then re-run `--update`.", file=sys.stderr)
        return 1
    write_baseline(current)
    print(f"Baseline updated to current counts (total = {sum(current.values())}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true", help="print per-file counts")
    p.add_argument("--update", action="store_true", help="rewrite baseline (reduce-only)")
    args = p.parse_args(argv)
    if args.list:
        return cmd_list()
    if args.update:
        return cmd_update()
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
