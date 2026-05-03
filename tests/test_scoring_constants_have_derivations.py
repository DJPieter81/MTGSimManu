"""Mechanical contract test for ai/scoring_constants.py.

Every module-level numeric constant in this file MUST be justified, either by:

1. A docstring-style triple-quoted string on the line immediately following the
   assignment (the convention used throughout the existing file), OR
2. A comment block in the 5 lines preceding the assignment whose text contains
   one of: "Derived", "Derivation", "rules constant", "rules-constant",
   "Sentinel", "Magic rules", "Per-archetype".

Rationale (from CLAUDE.md ABSTRACTION CONTRACT): "No new numeric threshold
without a test that names the rule it encodes."  This test is the mechanical
gate enforcing that rule for every centralized constant.

Functions are exempt — they're vetted at code-review time, and only the
underlying constants need a derivation.
"""
from __future__ import annotations
import ast
from pathlib import Path

SCORING_CONSTANTS_PATH = Path(__file__).parent.parent / "ai" / "scoring_constants.py"

JUSTIFICATION_KEYWORDS = (
    "derived",
    "derivation",
    "rules constant",
    "rules-constant",
    "sentinel",
    "magic rules",
    "per-archetype",
    "rules-anchor",
    "iter-",          # iteration-tuned values reference their tuning context
    "iteration-",
)


def _is_numeric_value(node: ast.AST) -> bool:
    """True if `node` is a numeric literal, or a collection containing them."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(
            node.value, bool
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _is_numeric_value(node.operand)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(_is_numeric_value(el) for el in node.elts)
    if isinstance(node, ast.Dict):
        return any(_is_numeric_value(v) for v in node.values)
    return False


def _justification_text_for(idx: int, body: list[ast.stmt],
                            source_lines: list[str]) -> str:
    """Collect candidate justification text near the assignment at `body[idx]`:
    its trailing docstring (if any) and the comment lines in the 5 source lines
    immediately preceding it.
    """
    chunks: list[str] = []
    node = body[idx]

    # Trailing docstring expression
    if idx + 1 < len(body):
        nxt = body[idx + 1]
        if (isinstance(nxt, ast.Expr)
                and isinstance(nxt.value, ast.Constant)
                and isinstance(nxt.value.value, str)):
            chunks.append(nxt.value.value)

    # Preceding 5 source lines (comments)
    start_line = max(1, node.lineno - 5)
    for ln in range(start_line, node.lineno):
        text = source_lines[ln - 1]
        stripped = text.strip()
        if stripped.startswith("#"):
            chunks.append(stripped[1:])

    return " ".join(chunks).lower()


def test_every_scoring_constant_has_a_derivation():
    """AST-scan ai/scoring_constants.py: every module-level numeric assignment
    has a docstring or preceding-comment justification containing one of the
    accepted keywords.
    """
    src = SCORING_CONSTANTS_PATH.read_text()
    tree = ast.parse(src)
    source_lines = src.splitlines()

    failures: list[str] = []
    for idx, node in enumerate(tree.body):
        target_name = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None and _is_numeric_value(node.value):
                target_name = node.target.id
        elif isinstance(node, ast.Assign):
            if (len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and _is_numeric_value(node.value)):
                target_name = node.targets[0].id

        if target_name is None:
            continue

        # Skip dunder / private constants — they're internal plumbing.
        if target_name.startswith("_"):
            continue

        text = _justification_text_for(idx, tree.body, source_lines)
        if not any(kw in text for kw in JUSTIFICATION_KEYWORDS):
            failures.append(
                f"L{node.lineno}: {target_name} has no derivation comment. "
                f"Expected one of {JUSTIFICATION_KEYWORDS} in adjacent docstring "
                f"or preceding 5 lines of comments."
            )

    assert not failures, (
        "Constants in ai/scoring_constants.py missing derivation:\n  - "
        + "\n  - ".join(failures)
    )
