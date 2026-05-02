#!/usr/bin/env python3
"""Doc hygiene — enforce root .md allowlist and ban _V[0-9]+.md versioning.

Why this exists: the project accumulated 13 stale plan files at root from the
patch-and-iterate antipattern (ORACLE_REFACTOR_PLAN -> _V2; ITERATION_5/6/7;
single-shot OVERNIGHT_*.md). Cleanup happened once; this check prevents
recurrence. See CLAUDE.md ABSTRACTION CONTRACT.

Rules enforced:
  1. Root-level *.md must be in ALLOWLIST below.
  2. No file anywhere named *_V[0-9]+.md (supersession via frontmatter only).

Usage:
    python tools/check_doc_hygiene.py
    python tools/check_doc_hygiene.py --list   # show all root .md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ROOT_MD_ALLOWLIST = {
    "README.md",
    "CLAUDE.md",
    "PROJECT_STATUS.md",
    "MODERN_PROPOSAL.md",
    "CROSS_PROJECT_SYNC.md",
}

VERSIONED_FILENAME = re.compile(r"_V[0-9]+\.md$")


def find_root_md() -> list[Path]:
    return sorted(p for p in ROOT.iterdir() if p.is_file() and p.suffix == ".md")


def find_versioned_md() -> list[Path]:
    """Walk only git-tracked files. This matches what CI sees on a fresh
    `actions/checkout` and excludes scratch state (worktrees under
    .claude/, untracked design drafts, vendored docs in node_modules).

    Skipped: anything under `docs/history/` — that subtree is the
    frozen archive and may contain legacy `_V[0-9]+.md` filenames that
    the rule grandfathered in. New files anywhere else, even outside
    `docs/`, must use frontmatter supersession.
    """
    import subprocess
    hits: list[Path] = []
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.md"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback to filesystem walk when not in a git checkout
        # (rare — abstraction-allow: bootstrap path for dev shells).
        return _find_versioned_md_fs()
    for line in out.splitlines():
        rel = Path(line.strip())
        if not rel.parts:
            continue
        # docs/history/ is the frozen archive — legacy _V2.md grandfather.
        if rel.parts[:2] == ("docs", "history"):
            continue
        if VERSIONED_FILENAME.search(rel.name):
            hits.append(rel)
    return hits


def _find_versioned_md_fs() -> list[Path]:
    """Filesystem-walk fallback for non-git environments. Same skip
    rule as the git-tracked path."""
    hits: list[Path] = []
    for p in ROOT.rglob("*.md"):
        rel = p.relative_to(ROOT)
        if rel.parts and rel.parts[0] in {".git", "node_modules", ".claude"}:
            continue
        if rel.parts[:2] == ("docs", "history"):
            continue
        if VERSIONED_FILENAME.search(p.name):
            hits.append(rel)
    return hits


def main(argv: list[str]) -> int:
    if "--list" in argv:
        for p in find_root_md():
            mark = " " if p.name in ROOT_MD_ALLOWLIST else "!"
            print(f"  {mark} {p.name}")
        return 0

    failed = False

    root_md = [p for p in find_root_md()]
    disallowed = [p for p in root_md if p.name not in ROOT_MD_ALLOWLIST]
    if disallowed:
        failed = True
        print(
            "DOC HYGIENE VIOLATION: root-level .md not in allowlist:",
            file=sys.stderr,
        )
        for p in disallowed:
            print(f"  {p.name}", file=sys.stderr)
        print(
            "\nAllowed at root: " + ", ".join(sorted(ROOT_MD_ALLOWLIST)),
            file=sys.stderr,
        )
        print(
            "Move design/plan docs to docs/design/ or docs/proposals/ with frontmatter.\n"
            "Move finished single-shot tasks to docs/history/plans/.",
            file=sys.stderr,
        )

    versioned = find_versioned_md()
    if versioned:
        failed = True
        print(
            "\nDOC HYGIENE VIOLATION: _V[0-9]+.md filenames are forbidden "
            "(use frontmatter `superseded_by` instead):",
            file=sys.stderr,
        )
        for p in versioned:
            print(f"  {p}", file=sys.stderr)
        # Diagnostic: also print to STDOUT so CI annotation captures the
        # offending paths (some runners only surface stdout in summaries).
        print(
            "DOC HYGIENE: violating _V[0-9]+.md paths (stdout copy):"
        )
        for p in versioned:
            print(f"  {p}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
