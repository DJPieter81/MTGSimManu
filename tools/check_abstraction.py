#!/usr/bin/env python3
"""Abstraction ratchet — block commits that add hardcoded card-name conditionals.

Pattern detected (in engine/ and ai/ only):
    <expr>.name == "<literal>"            # direct name check
    <expr>.name == '<literal>'
    name in (<literals>) / name in {<literals>}   # set/tuple membership

Lines tagged with `# abstraction-allow: <reason>` are exempted.

Behavior:
    count > baseline → exit 1, print new offenders
    count < baseline → exit 1, prompt to lower baseline (forces explicit ratchet)
    count == baseline → exit 0

Why a ratchet, not absolute zero: there is existing legitimate technical debt
(see baseline). The contract is to never *grow* it. Reductions must be
explicit so debt-paydown is visible in git history.

Usage:
    python tools/check_abstraction.py            # check current tree
    python tools/check_abstraction.py --list     # print all current hits
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = [ROOT / "engine", ROOT / "ai"]
BASELINE_FILE = ROOT / "tools" / "abstraction_baseline.json"
ALLOW_MARKER = "# abstraction-allow"

# Patterns that catch hardcoded card-name conditionals. Kept conservative on
# purpose — false positives are fine because they enter the baseline once and
# never grow. False negatives (missed antipatterns) are the real failure mode.
PATTERNS = [
    # <expr>.name == "literal"  /  <expr>.name == 'literal'
    re.compile(r'\.name\s*==\s*"[^"]+"'),
    re.compile(r"\.name\s*==\s*'[^']+'"),
    # name in (...) / name in {...} — opens a literal tuple/set on this line
    # (matches both same-line-closes and multi-line-opens; the next char being
    # a quote, or end-of-line followed by quoted lines, is what we care about)
    re.compile(r"\bname\s+in\s+[\(\{]\s*['\"]"),
    re.compile(r"\bname\s+in\s+[\(\{]\s*$"),
]


def find_hits() -> list[tuple[Path, int, str]]:
    hits: list[tuple[Path, int, str]] = []
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for py in d.rglob("*.py"):
            try:
                lines = py.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for i, line in enumerate(lines, 1):
                if ALLOW_MARKER in line:
                    continue
                if any(p.search(line) for p in PATTERNS):
                    hits.append((py.relative_to(ROOT), i, line.rstrip()))
    return hits


def load_baseline() -> int:
    if not BASELINE_FILE.exists():
        return 0
    return int(json.loads(BASELINE_FILE.read_text())["hardcoded_name_count"])


def main(argv: list[str]) -> int:
    hits = find_hits()
    count = len(hits)

    if "--list" in argv:
        for path, lineno, line in hits:
            print(f"{path}:{lineno}: {line}")
        print(f"\nTotal: {count}")
        return 0

    baseline = load_baseline()

    if count > baseline:
        print(
            f"ABSTRACTION CONTRACT VIOLATION: hardcoded card-name conditionals "
            f"increased from {baseline} → {count}.",
            file=sys.stderr,
        )
        print("\nCurrent hits:", file=sys.stderr)
        for path, lineno, line in hits:
            print(f"  {path}:{lineno}: {line}", file=sys.stderr)
        print(
            "\nFix options:\n"
            "  1. Replace the name check with an oracle-text or template-field check.\n"
            "  2. Move card-specific knowledge into decks/gameplans/*.json.\n"
            "  3. If genuinely unavoidable (e.g. enum check), tag the line:\n"
            f"       {ALLOW_MARKER}: <reason>",
            file=sys.stderr,
        )
        return 1

    if count < baseline:
        print(
            f"Abstraction count dropped from {baseline} → {count}. "
            f"Lower the baseline explicitly:",
            file=sys.stderr,
        )
        print(
            f'  echo \'{{"hardcoded_name_count": {count}}}\' > '
            f"{BASELINE_FILE.relative_to(ROOT)}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
