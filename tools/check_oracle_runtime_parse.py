"""Ratchet: oracle text must be parsed at load time (oracle_parser.py),
not inspected at decision time in engine/ or ai/ code.

The rule: every card property derivable from oracle text belongs in a typed
field on CardTemplate, populated once by CardDatabase at load time.  Runtime
code reads the field; oracle_parser.py is the only module that may examine
the raw oracle string.

Violations caught:
  - `'keyword' in oracle` / `'keyword' in oracle_lower` string-contains checks
  - `oracle.count(...)` / `oracle.lower().count(...)` substring counts
  - `re.search(..., oracle)` / `re.findall(..., oracle_lower)` inline parses
  - `re.match(..., oracle_text)` wherever oracle_text is the raw template field

Excluded (legitimate callers):
  - engine/oracle_parser.py   — the canonical parse-once module
  - engine/card_database.py   — calls oracle_parser at load time
  - tests/                    — tests may construct templates with oracle text
  - tools/                    — tooling may read oracle for linting
"""
from __future__ import annotations

import re
import sys
import json
import pathlib

ROOT = pathlib.Path(__file__).parent.parent

# Files allowed to inspect oracle text directly
_EXCLUDED = {
    "engine/oracle_parser.py",
    "engine/card_database.py",
}

# Pattern for variables that hold raw oracle text in runtime code.
# We match the local variable names commonly used across the codebase.
_ORACLE_VAR = r'(?:oracle|oracle_l|oracle_lower|oracle_text|fb_oracle|c_oracle)'

# Detection patterns (each counts as one violation per occurrence)
_PATTERNS = [
    # 'substring' in oracle_var
    re.compile(
        r"""['"]\w[^'"]*['"]\ +in\ +""" + _ORACLE_VAR,
        re.VERBOSE,
    ),
    # oracle_var in 'substring'  (reversed membership)
    re.compile(_ORACLE_VAR + r"""\ +in\ +['"]"""),
    # oracle_var.count(
    re.compile(_ORACLE_VAR + r'\.(?:count|find|index)\s*\('),
    # re.search / re.findall / re.match / re.sub with oracle_var as 2nd+ arg
    re.compile(
        r're\.(?:search|findall|match|sub|subn)\s*\([^)]*,\s*' + _ORACLE_VAR
    ),
    # oracle_var.lower() used as a value (not just assigned)
    # catches: 'x' in oracle_lower where oracle_lower = oracle.lower()
    # We catch the upstream assignment sites: oracle.lower() on RHS assigned
    # to a local, then used with 'in' — the 'in' pattern above already catches
    # the use site.  No need for a separate lower() pattern.
]


def _count_violations(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return list of (line_number, matched_text) for violations in path."""
    try:
        src = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return []
    hits = []
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pat in _PATTERNS:
            m = pat.search(line)
            if m:
                hits.append((i, m.group(0).strip()))
                break  # count each line once
    return hits


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    list_mode = "--list" in args

    baseline_path = ROOT / "tools" / "oracle_runtime_parse_baseline.json"
    if not baseline_path.exists():
        print("ERROR: tools/oracle_runtime_parse_baseline.json not found.")
        return 1

    with open(baseline_path) as f:
        baseline: dict = json.load(f)

    # Scan engine/ and ai/ (tests/ and tools/ are excluded)
    target_dirs = [ROOT / "engine", ROOT / "ai"]
    violations: dict[str, list[tuple[int, str]]] = {}

    for d in target_dirs:
        for path in sorted(d.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in _EXCLUDED:
                continue
            hits = _count_violations(path)
            if hits:
                violations[rel] = hits

    total = sum(len(v) for v in violations.values())
    allowed = baseline.get("total", 0)

    if list_mode:
        for rel, hits in sorted(violations.items()):
            for lineno, text in hits:
                print(f"  {rel}:{lineno}  {text}")
        print(f"\nTotal oracle-runtime-parse violations: {total} (baseline: {allowed})")
        return 0

    if total > allowed:
        print(
            f"Oracle-runtime-parse ratchet FAILED: "
            f"{total} violations found, {allowed} allowed (baseline)."
        )
        print(
            "Oracle text must be parsed in engine/oracle_parser.py at load time,\n"
            "not inspected inline in engine/ or ai/ decision code.\n"
            "Add a typed field to CardTemplate and populate it in CardDatabase.\n"
            "To see all violations: python tools/check_oracle_runtime_parse.py --list"
        )
        for rel, hits in sorted(violations.items()):
            for lineno, text in hits[:3]:
                print(f"  {rel}:{lineno}  {text}")
        return 1

    print(
        f"Oracle-runtime-parse ratchet OK — "
        f"total = {total} (baseline allowed = {allowed})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
