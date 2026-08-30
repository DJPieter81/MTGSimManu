"""Census guard: loyalty-effect dispatch reads printed oracle text.

Wraps `tools/check_loyalty_dispatch.py` so the standard pytest run
catches a regression in loyalty-ability classification, in the same
shape as `tests/test_abstraction_contract.py` wraps the abstraction
ratchet.

The rule: a loyalty ability's effect is classified ONCE at DB load from
its printed oracle text into a closed `LoyaltyEffectKind` set.  An
ability that classifies as `UNCLASSIFIED` is refused before the loyalty
is paid — visible-but-inert, never a silent no-op.  The number of such
abilities may only shrink.
"""
from __future__ import annotations

import json
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "tools" / "loyalty_dispatch_baseline.json"


@pytest.fixture(scope="module")
def loyalty_census(card_db):
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    from tools.check_loyalty_dispatch import census
    return census(card_db)


def test_loyalty_effect_dispatch_reads_printed_oracle_text_not_a_phrase_table(
        loyalty_census):
    """The count of loyalty abilities the resolver cannot execute is
    pinned and may only shrink.

    Both directions fail:
      * growth  — a mechanic stopped classifying (or a half-executed
        shape was newly refused without implementing it);
      * a stale baseline — a family was implemented but the ceiling was
        left high, letting it be silently re-filled later.
    """
    with BASELINE.open() as f:
        baseline = json.load(f)
    allowed = int(baseline["unclassified"])
    actual = loyalty_census["unclassified"]

    assert actual <= allowed, (
        f"unclassified loyalty abilities grew: {actual} > baseline "
        f"{allowed}. Every ability in this set pays no loyalty and does "
        f"nothing. Run `python tools/check_loyalty_dispatch.py --list` "
        f"to see them."
    )
    assert actual >= allowed, (
        f"baseline is stale: {actual} unclassified, baseline says "
        f"{allowed}. A loyalty family was implemented — record it by "
        f"setting \"unclassified\" to {actual} in {BASELINE.name} in the "
        f"same commit."
    )


def test_no_loyalty_ability_is_dispatched_by_invented_vocabulary():
    """The dispatch table must not key on phrases that occur on zero
    cards.  Five of the original fifteen branches tested vocabulary no
    Magic card prints ("bounce", "brainstorm", "cast sorceries as
    flash", "exile opponent library", "return land from graveyard"), so
    they could never fire while the abilities they stood for resolved
    as no-ops.
    """
    import ast

    path = REPO_ROOT / "engine" / "planeswalker_manager.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # Docstrings are prose (this module documents the defect it fixed),
    # so exclude them and look only at string literals the CODE uses.
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)

    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    } - docstrings

    invented = {"bounce", "brainstorm", "cast sorceries as flash",
                "exile opponent library", "return land from graveyard",
                "exile all colored", "look at top card"}
    offenders = invented & literals
    assert not offenders, (
        f"{sorted(offenders)} are printed on zero cards in the pool; a "
        f"dispatch branch keyed on them can never fire"
    )


def test_every_executable_kind_has_a_dispatch_branch():
    """Schema coherence: the executable-kind whitelist and the
    resolver's dispatch must not drift.  A kind on the whitelist with
    no branch is the original defect wearing a typed field."""
    from engine.planeswalker_manager import (EXECUTABLE_LOYALTY_KINDS,
                                             PlaneswalkerManager)
    source = (REPO_ROOT / "engine" / "planeswalker_manager.py").read_text(
        encoding="utf-8")
    for kind in EXECUTABLE_LOYALTY_KINDS:
        assert f"LoyaltyEffectKind.{kind.name}" in source, (
            f"{kind} is declared executable but has no dispatch branch"
        )
    assert PlaneswalkerManager is not None
