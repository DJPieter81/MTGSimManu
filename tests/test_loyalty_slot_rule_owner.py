"""The loyalty slot rule has one owner (CR 606).

Design doc 2026-09-29 (A12, section 13). A planeswalker's printed loyalty
lines map to the slots the engine and AI name -- "plus" (the first
loyalty-positive line), "zero", "minus" (the first loyalty-negative line) and
"ult" (the second) -- and a line that finds its slot already taken gets none.
Both the legacy loyalty parser and the clause grammar's structure layer need
that rule, so it lives in one function, `oracle_parser.loyalty_slot_for`,
and nothing else in engine/ or ai/ assigns a slot.

Rules pinned:
* the slot rule itself, on signed cost sequences (only the sign is read);
* no other engine/ai code writes the "ult" slot literal;
* `loyalty_abilities` and `back_face_loyalty_abilities` are unchanged for
  every planeswalker in the pool against a capture taken before the rule
  was factored out (`tests/fixtures/loyalty_abilities_capture.json`).
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CAPTURE_PATH = Path(__file__).parent / "fixtures" / "loyalty_abilities_capture.json"


def _canon_ability(a) -> tuple:
    from tests.test_target_solver_located_parse import canonical_requirement
    clause = getattr(a, "clause", None)
    return (a.slot, a.cost, a.text, a.effect_kind.name,
            canonical_requirement(a.target) if a.target is not None else None,
            a.draws,
            None if clause is None else (clause.name, clause.oracle_text))


def _canon_slots(d) -> tuple:
    return tuple((k, _canon_ability(v)) for k, v in (d or {}).items())


def walker_digests(db) -> dict:
    """name-digest -> digest of (loyalty_abilities, back_face_loyalty_abilities)
    for every template with a printed loyalty line on either face."""
    out = {}
    for t in {id(v): v for v in db.cards.values()}.values():
        front = getattr(t, "loyalty_abilities", None) or {}
        back = getattr(t, "back_face_loyalty_abilities", None) or {}
        if not front and not back:
            continue
        key = hashlib.sha256(t.name.encode()).hexdigest()[:12]
        out[key] = hashlib.sha256(
            repr((_canon_slots(front), _canon_slots(back))).encode()
        ).hexdigest()[:16]
    return out


def test_the_slot_rule_assigns_first_plus_zero_first_minus_then_ult():
    from engine.oracle_parser import loyalty_slot_for
    costs = [1, 0, -3, -8]
    assert [loyalty_slot_for(costs, i) for i in range(4)] == [
        "plus", "zero", "minus", "ult"]
    # A second positive line and a third negative line get no slot.
    costs = [2, 1, -2, -5, -9]
    assert [loyalty_slot_for(costs, i) for i in range(5)] == [
        "plus", "", "minus", "ult", ""]
    # A second zero line finds "zero" taken.
    assert [loyalty_slot_for([0, 0, -1], i) for i in range(3)] == [
        "zero", "", "minus"]
    # Printed order decides, not magnitude.
    assert [loyalty_slot_for([-7, 1, -2], i) for i in range(3)] == [
        "minus", "plus", "ult"]


def _ult_writers(path: Path):
    tree = ast.parse(path.read_text())
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant):
                doc_nodes.add(id(body[0].value))
    owners = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Constant) and node.value == "ult"
                    and id(node) not in doc_nodes):
                owners.append(f"{path.relative_to(REPO)}::{fn.name}")
                break
    module_level = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and n.value == "ult"
                    and id(n) not in doc_nodes]
    return owners, len(module_level)


def test_no_other_engine_or_ai_code_assigns_a_loyalty_slot():
    owners, total = [], 0
    for sub in ("engine", "ai"):
        for path in sorted((REPO / sub).rglob("*.py")):
            o, n = _ult_writers(path)
            owners.extend(o)
            total += n
    assert owners == ["engine/oracle_parser.py::loyalty_slot_for"], owners
    assert total == 1


def test_parse_loyalty_abilities_reads_its_slots_from_the_one_owner():
    import inspect
    from engine import oracle_parser
    src = inspect.getsource(oracle_parser.parse_loyalty_abilities)
    assert "loyalty_slot_for(" in src


@pytest.fixture(scope="module")
def db():
    from tests._card_db_cache import shared_card_database
    return shared_card_database()


def test_the_loyalty_slot_rule_has_one_owner_and_loyalty_abilities_are_unchanged(db):
    pinned = json.loads(CAPTURE_PATH.read_text())["walkers"]
    now = walker_digests(db)
    common = set(pinned) & set(now)
    assert len(common) >= 0.95 * len(pinned), (len(common), len(pinned))
    changed = sorted(k for k in common if pinned[k] != now[k])
    assert not changed, f"{len(changed)} walkers changed"


if __name__ == "__main__" and "--capture" in sys.argv:
    sys.path.insert(0, str(REPO))
    from engine.card_database import CardDatabase
    walkers = walker_digests(CardDatabase())
    CAPTURE_PATH.write_text(json.dumps({
        "comment": ("loyalty_abilities + back_face_loyalty_abilities per "
                    "template with a printed loyalty line: sha256(name)[:12] "
                    "-> sha256(repr(canonical slots))[:16]. Captured before "
                    "the slot rule moved to loyalty_slot_for (design doc "
                    "2026-09-29, A12)."),
        "walkers": walkers,
    }, indent=0, sort_keys=True) + "\n")
    print(f"captured {len(walkers)} walkers")
