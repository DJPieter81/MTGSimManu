"""Anchor: orphaned scoring constants stay deleted.

W2-3 deletion pass catalogued constants in `ai/scoring_constants.py`
that survived past their last production caller. Each entry below was
identified by AST walk + grep across `ai/`, `engine/`, `decks/`,
`tools/` (excluding the defining file and ratchet baselines).

The rule: a module-level constant has no business outliving its only
caller — either the upstream shipped PR replaced it with an oracle-
parsed value or the gate it encoded was removed. Re-introducing one
is a regression.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

from ai import scoring_constants


# Each name's note must point at the shipped PR that orphaned it.
DELETED_CONSTANTS = (
    # PR #343 replaced flat 3-mana ritual projection with parsed
    # `template.ritual_mana[1]`. Audit:
    # docs/design/2026-05-10_oracle_pattern_projection_blindspot_audit.md.
    "RITUAL_MANA_PRODUCED",
)


@pytest.mark.parametrize("name", DELETED_CONSTANTS)
def test_deleted_constant_is_not_importable(name: str):
    importlib.reload(scoring_constants)
    assert not hasattr(scoring_constants, name), (
        f"`ai.scoring_constants.{name}` was deleted by W2-3 because "
        f"it had no production caller. Re-adding it without naming "
        f"the new caller is a regression."
    )


def test_no_urgency_factor_constant_resurfaced():
    """`urgency_factor` names a derived property on EVSnapshot — never
    a tunable scalar. Zero code-level mentions must appear in
    `ai/scoring_constants.py`. Comments/docstrings referencing the
    snapshot field are OK (they don't introduce coupling).
    """
    src = inspect.getsource(scoring_constants)
    # Strip docstrings + comments; same heuristic as
    # tests/test_response_constants_linkage.py.
    code_lines, in_triple, triple_marker = [], False, None
    for ln in src.splitlines():
        stripped = ln.strip()
        if in_triple:
            code_lines.append("")
            if triple_marker in ln:
                in_triple, triple_marker = False, None
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            triple_marker = stripped[:3]
            if triple_marker not in stripped[3:]:
                in_triple = True
            code_lines.append("")
            continue
        if stripped.startswith("#"):
            code_lines.append("")
            continue
        code_lines.append(ln)
    assert "urgency_factor" not in "\n".join(code_lines), (
        "ai/scoring_constants.py contains a code-level `urgency_factor` "
        "reference. That identifier is a derived EVSnapshot property "
        "composed from clock primitives — read it from the snapshot, "
        "do not pin it as a constant."
    )
