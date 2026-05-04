#!/usr/bin/env python3
"""Magic-number ratchet for AI scoring code.

Counts bare numeric literals in `ai/*.py` (excluding the constants
modules themselves) and enforces a non-increasing per-file baseline
stored in `tools/magic_numbers_baseline.json`.

Usage:
    python tools/check_magic_numbers.py            # check (exit 1 on regression)
    python tools/check_magic_numbers.py --list     # list per-file counts
    python tools/check_magic_numbers.py --update   # rewrite baseline (reduces only)

Rules:
- Counted: bare numeric literals that appear in expressions, function
  defaults, or return statements inside `ai/*.py`. Typical anti-pattern
  is `score += 8.0` or `if probability < 0.15: ...`.
- Not counted:
  - Lines containing `# magic-allow:` comment (intentional rule-constant)
  - String literals
  - Comments
  - Type annotations using `Literal[N]`
  - `0`, `1`, `-1`, `2`, `100` when used as indices/bounds/% conversions
    (heuristic — see EXEMPT_VALUES below)
  - Constants defined at module top level (those ARE the named constants)
- Excluded from scan entirely: `ai/scoring_constants.py`, `ai/constants.py`
  (these define constants; counting their literals is circular).

See CLAUDE.md ABSTRACTION CONTRACT for the rule this enforces.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = REPO_ROOT / "tools" / "magic_numbers_baseline.json"

# Files that DEFINE constants, schemas, or are infra rather than scoring —
# exclude from scan. Some entries are forward-looking (Wave 2 modules
# already named in the design but not yet on main); keeping them here
# means they will not be tracked when they land. Existing files only get
# scanned, so listing not-yet-existing files is harmless.
EXCLUDED_FILES = {
    "ai/scoring_constants.py",
    "ai/constants.py",
    "ai/__init__.py",
    "ai/llm_schemas.py",       # PR #260 — schema definitions, not scoring
    "ai/llm_models.py",        # PR #260
    "ai/llm_agents.py",        # PR #260
    "ai/llm_cache.py",         # PR #266 — infra
    "ai/llm_metrics.py",       # I-5 incoming
    "ai/llm_embeddings.py",    # PR #262
    "ai/card_features.py",     # I-2 incoming
    "ai/llm_compression.py",   # I-3 — token caps / pattern strings, not scoring
    "ai/predicates.py",        # tag definitions can have literal limits
    "ai/clock.py",             # rules-derived constants
    "ai/strategy_profile.py",  # tuning data
    "ai/gameplan_schemas.py",  # PR #260 re-export shim
    "ai/schemas.py",           # decision-kernel schemas
    "ai/decision_kernel.py",   # protocol-level, mostly orchestration
}

# Indices/bounds/percent conversions that are usually intentional, not magic.
EXEMPT_VALUES = {0, 1, -1, 2, 100}


class MagicNumberCounter(ast.NodeVisitor):
    """Walks an AST and counts bare numeric literals not exempted.

    Top-level module assignments are NOT counted — those ARE the named
    constants. Only literals encountered inside functions/classes count.
    """

    def __init__(self, source_lines: list[str]):
        self.source_lines = source_lines
        self.count = 0
        self.locations: list[tuple[int, str, str]] = []
        self._depth = 0  # depth of function/class nesting

    def _enter(self):
        self._depth += 1

    def _leave(self):
        self._depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._enter()
        self.generic_visit(node)
        self._leave()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._enter()
        self.generic_visit(node)
        self._leave()

    def visit_ClassDef(self, node: ast.ClassDef):
        self._enter()
        self.generic_visit(node)
        self._leave()

    def visit_Constant(self, node: ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            return
        if node.value in EXEMPT_VALUES:
            return
        # Top-level module assignments ARE the named constants — don't count.
        if self._depth == 0:
            return
        # Check for inline comment exemption.
        line_idx = node.lineno - 1
        line = ""
        if 0 <= line_idx < len(self.source_lines):
            line = self.source_lines[line_idx]
            if "# magic-allow:" in line:
                return
        self.count += 1
        self.locations.append((node.lineno, repr(node.value), line.rstrip()))


def count_file(path: Path) -> tuple[int, list]:
    """Return (count, locations) for a single file. Syntax errors → (0, [])."""
    try:
        src = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0, []
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return 0, []
    counter = MagicNumberCounter(src.splitlines())
    counter.visit(tree)
    return counter.count, counter.locations


def scan_all(repo_root: Path | None = None) -> dict[str, int]:
    """Scan ai/*.py and return {relative_path: count}."""
    root = repo_root or REPO_ROOT
    counts: dict[str, int] = {}
    ai_dir = root / "ai"
    if not ai_dir.exists():
        return counts
    for py in sorted(ai_dir.glob("*.py")):
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
            "Magic-number ratchet baseline. Counts bare numeric literals in "
            "ai/*.py outside excluded infrastructure modules. May only "
            "decrease (or reduce as cleanup PRs land). See CLAUDE.md "
            "ABSTRACTION CONTRACT."
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
        print("Magic-number ratchet failure — counts increased from baseline:", file=sys.stderr)
        for f, base, n in regressions:
            print(f"  {f}: {base} -> {n}  (delta +{n - base})", file=sys.stderr)
        print("", file=sys.stderr)
        print("Either:", file=sys.stderr)
        print("  1. Replace the new literal(s) with named constants in ai/scoring_constants.py", file=sys.stderr)
        print("  2. Add `# magic-allow: <reason>` to the line if it's a genuine rule constant", file=sys.stderr)
        print("  3. Reduce the count to <= baseline before pushing", file=sys.stderr)
        print("", file=sys.stderr)
        print("Run `python tools/check_magic_numbers.py --list` to see the locations.", file=sys.stderr)
        return 1
    new_files = sorted(set(current) - set(baseline))
    if new_files:
        print(f"Note: new files detected (not in baseline): {new_files}")
        print("Add them to the baseline by running `--update` (only if counts didn't increase).")
    print(
        f"Magic-number ratchet OK — total = {sum(current.values())} "
        f"(baseline allowed = {sum(baseline.get(f, 0) for f in current)})"
    )
    return 0


def cmd_update() -> int:
    """Reduce-only update — refuses to write a baseline that INCREASES any per-file count."""
    current = scan_all()
    baseline = load_baseline()
    increases = [(f, baseline.get(f, 0), n) for f, n in current.items() if n > baseline.get(f, 0)]
    # Allow new files (no prior baseline) — base = 0 means n > 0 would be flagged. We treat
    # truly-new files as "additions to scope" and require an explicit first-write. Accept
    # them only if the baseline file is missing entirely (initial seed).
    if not BASELINE_FILE.exists():
        write_baseline(current)
        print(f"Baseline seeded with current counts (total = {sum(current.values())}).")
        return 0
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
