"""Pool-wide invariants of the clause grammar (design doc 2026-09-29, E0).

E0 changes no resolution behaviour. Besides the seeded digest, that is
proven by pool-wide unchanged-output pins for the refactors E0 makes to
existing modules (design section 0):

* the located target parse: `target_solver.parse()` is re-expressed over
  `parse_located`, the one placement owner (F3, A20), and its output is
  unchanged for every oracle text, line and sentence of the pool;
* the loyalty slot rule: it has one owner, `oracle_parser.loyalty_slot_for`
  (G1, A12), and `loyalty_abilities` / `back_face_loyalty_abilities` are
  unchanged for every planeswalker.

Each pin compares against a capture taken before its refactor (the capture
helpers and their `--capture` entry points live with the module tests:
tests/test_target_solver_located_parse.py and
tests/test_loyalty_slot_rule_owner.py). The grammar's own pool invariants
(effects populated, spans covered, witnesses, schema invariants, load
budget) join this file with `engine/effect_grammar/`.
"""
from __future__ import annotations

import json

import pytest

from tests.test_loyalty_slot_rule_owner import (
    CAPTURE_PATH as LOYALTY_CAPTURE_PATH, REPO, _ult_writers, walker_digests)
from tests.test_target_solver_located_parse import (
    CAPTURE_PATH as TARGET_PARSE_CAPTURE_PATH, _digest, output_digest,
    pool_texts)


# Pool-wide (~17k pool texts containing "target"). Measured 2026-09-30 on
# this container (quiet, 4 cores): ~1.3 s for the body, plus ~16 s when it is
# the first test of the process to load the shared card DB. 120 s bounds a
# hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_target_solver_parse_is_unchanged_by_the_located_refactor_pool_wide(card_db):
    from engine.target_solver import parse
    pinned = json.loads(TARGET_PARSE_CAPTURE_PATH.read_text())["entries"]
    checked, diffs = 0, []
    for t in pool_texts(card_db):
        if "target" not in t.lower():
            continue
        want = pinned.get(_digest(t))
        if want is None:          # a text added by a later DB refresh
            continue
        checked += 1
        if output_digest(parse(t)) != want:
            diffs.append(t)
    # The capture must still describe the pool, or the pin is vacuous.
    assert checked >= 0.95 * len(pinned), (checked, len(pinned))
    assert not diffs, f"{len(diffs)} texts changed, e.g. {diffs[:3]}"


# Pool-wide (~316 templates with a printed loyalty line, plus an AST scan of
# engine/ and ai/). Measured 2026-09-30 on this container (quiet, 4 cores):
# ~2.0 s for the body, plus ~16 s when it is the first test of the process to
# load the shared card DB. 120 s bounds a hang with room for a slower 2-core
# CI runner.
@pytest.mark.timeout(120)
def test_the_loyalty_slot_rule_has_one_owner_and_loyalty_abilities_are_unchanged(card_db):
    import inspect
    from engine import oracle_parser
    # One owner: no other engine/ai function writes the "ult" slot, and the
    # legacy parser reads its slots from the owner.
    owners, total = [], 0
    for sub in ("engine", "ai"):
        for path in sorted((REPO / sub).rglob("*.py")):
            o, n = _ult_writers(path)
            owners.extend(o)
            total += n
    assert owners == ["engine/oracle_parser.py::loyalty_slot_for"], owners
    assert total == 1
    assert "loyalty_slot_for(" in inspect.getsource(
        oracle_parser.parse_loyalty_abilities)
    # Unchanged: every walker's slots against the pre-refactor capture.
    pinned = json.loads(LOYALTY_CAPTURE_PATH.read_text())["walkers"]
    now = walker_digests(card_db)
    common = set(pinned) & set(now)
    assert len(common) >= 0.95 * len(pinned), (len(common), len(pinned))
    changed = sorted(k for k in common if pinned[k] != now[k])
    assert not changed, f"{len(changed)} walkers changed"
