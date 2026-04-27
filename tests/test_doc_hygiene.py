"""Doc hygiene — pytest integration.

Wraps tools/check_doc_hygiene.py so the standard `pytest tests/ -q` workflow
catches root-allowlist and `_V[0-9]+.md` violations before push. See
CLAUDE.md ABSTRACTION CONTRACT.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "check_doc_hygiene.py"


def test_doc_hygiene_script_exists():
    assert SCRIPT.exists(), f"Missing {SCRIPT.relative_to(ROOT)}"


def test_root_md_and_versioning_rules():
    """Root .md is restricted to allowlist; no _V[0-9]+.md anywhere.

    To add a new design/plan doc: put it under docs/design/ or docs/proposals/
    with frontmatter, NOT at repo root. To supersede an existing doc: set
    `superseded_by:` in its frontmatter — never spawn a `_V2` sibling.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise AssertionError(
            f"Doc hygiene violation:\n{msg}\n\n"
            f"See CLAUDE.md ABSTRACTION CONTRACT for the rule."
        )
