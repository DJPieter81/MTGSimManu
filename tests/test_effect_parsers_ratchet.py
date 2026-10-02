"""The effect-parser ratchet (design doc 2026-09-29, section 13).

`tools/check_effect_parsers.py` pins, and lets only fall, the legacy
per-shape effect-parsing debt the clause grammar retires family by family:
(a) card_database assignments of a derived field from a legacy parser, (b)
runtime oracle reads in the resolution handlers, (c) the `_legacy_*`
predicates and masks of effect_views, (d) the tolerated residue codes, (e)
parse_* results landing in an unaccounted field (pinned at 0) and (f) the
(handler, host) pairs on legacy fallback (pool-wide; checked by the
equivalence-tool pool test and the CI step after "Assemble card DB").

No card DB: these tests read source files only, so they run in the
abstraction-contract ratchet step.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "check_effect_parsers.py"
BASELINE = REPO / "tools" / "effect_parsers_baseline.json"
WORKFLOW = REPO / ".github" / "workflows" / "abstraction-contract.yml"


def _r():
    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import check_effect_parsers
    return check_effect_parsers


def test_the_static_effect_parser_counts_hold_their_baseline():
    r = _r()
    base = json.loads(BASELINE.read_text())
    found = r.counts(pool=False)
    assert set(found) == set(r.STATIC_COUNTS)
    assert r.check(found, base) == [], {k: len(v) for k, v in found.items()}
    assert base["e"] == 0 and isinstance(base["f"], int)


def test_the_ratchet_script_passes_without_the_card_pool():
    res = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                         text=True, cwd=REPO)
    assert res.returncode == 0, res.stdout + res.stderr


def test_a_legacy_parser_assignment_to_a_derived_field_counts_and_a_view_assignment_does_not():
    r = _r()
    src = ("def load(template, text):\n"
           "    template.loot_data = parse_loot_effect(text)\n"
           "    template.board_sweep_data = effect_views.board_sweep_data("
           "template.effects)\n"
           "    template.name = text\n")
    hits = r.count_a(src, {"loot_data", "board_sweep_data"})
    assert len(hits) == 1 and "template.loot_data" in hits[0]


def test_a_parse_function_whose_result_lands_in_an_unaccounted_field_is_a_violation():
    r = _r()
    parsers = r.parser_functions("def parse_new_shape(t):\n    return t\n"
                                 "def helper(t):\n    return t\n")
    assert parsers == {"parse_new_shape"}
    src = ("def load(template, text):\n"
           "    template.brand_new_field = parse_new_shape(text)\n"
           "    template.loot_data = parse_new_shape(text)\n")
    hits = r.count_e(src, parsers,
                     covered_fn=lambda f: f == "loot_data")
    assert len(hits) == 1 and "brand_new_field" in hits[0]
    # (e) is pinned at zero: one violation fails whatever the baseline says
    assert r.check({"e": hits}, {"e": 5})


def test_runtime_oracle_reads_parse_calls_and_override_lookups_in_a_handler_are_counted():
    r = _r()
    src = ("import re\n"
           "def handler(ctx, card):\n"
           "    text = card.template.oracle_text.lower()\n"
           "    if 'draw' in text:\n"
           "        pass\n"
           "    if re.search(r'x', ctx.oracle):\n"
           "        pass\n"
           "    desc = card.ability.description\n"
           "    if desc.startswith('destroy'):\n"
           "        pass\n"
           "    shape = parse_combat_prevention(text)\n"
           "    host = host_for_override(card.template, text)\n"
           "    return card.template.loot_data\n")
    hits = r.count_b_source(src, "engine/x.py", {"parse_combat_prevention"})
    kinds = [h.split(" ", 1)[1] for h in hits]
    assert kinds == ["membership test on oracle text",
                     "re.search(..., oracle)", "oracle.startswith(...)",
                     "parse_combat_prevention(...) at resolution",
                     "host_for_override(...)"], kinds


def test_a_count_above_its_baseline_regresses_and_a_count_below_it_is_stale():
    r = _r()
    base = {"a": 2, "b": 2, "c": 1, "d": 0, "e": 0}
    two = ["x", "y"]
    assert r.check({"a": two, "b": two, "c": ["z"], "d": [], "e": []},
                   base) == []
    assert any("regression" in p for p in r.check({"a": two + ["w"]}, base))
    assert any("stale" in p for p in r.check({"b": ["x"]}, base))


def test_the_ratchet_runs_in_ci_after_the_card_db_is_assembled_and_its_tests_in_the_ratchet_step():
    wf = WORKFLOW.read_text()
    step = wf.index("python tools/check_effect_parsers.py --pool")
    assert wf.index("python3 merge_db.py") < step
    ratchet_step = next(line for line in wf.splitlines()
                        if "tests/test_abstraction_contract.py" in line
                        and "--timeout=60" in line)
    assert "tests/test_effect_parsers_ratchet.py" in ratchet_step
