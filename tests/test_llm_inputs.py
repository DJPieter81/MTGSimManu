"""Every LLM task has a typed input whose renderer is the user prompt.

`ai.llm_inputs` owns the user-prompt half of each agent: a strict
pydantic input per task and a pure `render()`.  The tools build these
inputs (doing their own I/O) and the eval harness validates each
golden's `input` into the same model, so the evals exercise the exact
prompt the tools send.

Rules pinned here:
1. Every task has a typed input with its own renderer.
2. Every golden input validates against its task's input and renders.
3. The renderers keep the formats the tools sent before the typed
   inputs existed (verified byte-identical across every registered
   decklist, 236 sampled MTGJSON cards and 8 docs when the refactor
   landed) — so string-keyed response-cache rows stay valid.
4. A typed input keys the response cache exactly like the dict of its
   fields, and reaches the model as its rendering.
5. The failing-test naming rule is applied, not asked of the model.
"""
from __future__ import annotations

import json
from typing import get_args

import pytest

from ai.llm_inputs import (
    TASK_INPUT_TYPES,
    DecisionScorerInput,
    DecklistCardRow,
    DocFreshnessInput,
    FailingTestSpecInput,
    OracleClassifyInput,
    SiblingDoc,
    SynthGameplanInput,
    TaskInput,
    render_task_prompt,
    rule_test_file,
    task_input,
)
from ai.llm_models import LLMTask


def test_every_task_has_a_typed_input_with_its_own_renderer():
    assert set(TASK_INPUT_TYPES) == set(get_args(LLMTask))
    for task, cls in TASK_INPUT_TYPES.items():
        assert issubclass(cls, TaskInput), task
        assert cls.render is not TaskInput.render, task


@pytest.mark.parametrize("task", sorted(TASK_INPUT_TYPES))
def test_every_golden_input_validates_against_its_task_input_and_renders(task):
    from tests.eval.llm_eval import load_golden_pairs
    pairs = load_golden_pairs(task)
    assert pairs, f"no goldens for {task}"
    for pair in pairs:
        inp = task_input(task, pair["input"])
        assert isinstance(inp, TASK_INPUT_TYPES[task])
        text = inp.render()
        assert isinstance(text, str) and text.strip(), pair["_pair_id"]
        assert render_task_prompt(task, pair["input"]) == text


def test_a_task_input_rejects_fields_its_schema_does_not_declare():
    with pytest.raises(Exception):
        task_input("decision_scorer",
                   {"archetype": "ramp", "context": "x", "deck": "anything"})


# ─── 3. Formats the tools already sent ──────────────────────────────


def test_the_decklist_renderer_keeps_the_compressed_feature_format():
    inp = SynthGameplanInput(deck_name="Deck", cards=[
        DecklistCardRow(name="A Spell", quantity=4, cmc=1,
                        flags=["removal", "instant_speed"], types=["Instant"],
                        oracle_excerpt="Deal damage."),
        DecklistCardRow(name="A Land", quantity=2, cmc=0,
                        types=["Basic", "Land"]),
        DecklistCardRow(name="A Vanilla", quantity=1, cmc=2),
        DecklistCardRow(name="Unknown", quantity=3, db_miss=True),
    ])
    assert inp.render() == (
        "Deck name: Deck\n"
        "\n"
        "Mainboard (10 cards):\n"
        "- 4× A Spell {1}|removal,instant_speed | Instant\n"
        '    "Deal damage."\n'
        "- 2× A Land {0} | Basic/Land\n"
        "- 1× A Vanilla {2} | ?\n"
        "- 3× Unknown  (NO FEATURES — DB miss)\n"
        "\n"
        "Emit a SynthesizedGameplan JSON object describing this deck's "
        "goals, role assignments, and mulligan keys."
    )


def test_the_oracle_classifier_input_renders_the_printed_card_fields():
    card = {"name": "A Card", "manaCost": "{1}{B}", "supertypes": ["Legendary"],
            "types": ["Creature"], "subtypes": ["Human", "Wizard"],
            "text": "Flying"}
    assert OracleClassifyInput.from_mtgjson(card).render() == (
        "name: A Card\n"
        "mana_cost: {1}{B}\n"
        "types: Legendary Creature — Human Wizard\n"
        "oracle_text: Flying\n"
    )
    assert OracleClassifyInput.from_mtgjson({}).render() == (
        "name: <unknown>\nmana_cost: \ntypes: \noracle_text: \n")


def test_the_classifier_tool_renders_through_the_typed_input():
    from tools.build_oracle_classifier_cache import build_classify_input
    card = {"name": "A Card", "manaCost": "{U}", "types": ["Instant"],
            "text": "Counter target spell."}
    assert build_classify_input(card) == OracleClassifyInput.from_mtgjson(card).render()


def test_the_doc_freshness_input_renders_its_sections_in_order():
    inp = DocFreshnessInput(
        doc_path="docs/a.md", status="active", priority="primary", age_days=9,
        siblings=[SiblingDoc(path="docs/b.md", age_days=2, title="B")],
        body="Body.")
    assert inp.render() == (
        "# Doc to assess: docs/a.md\n"
        "Frontmatter status: active\n"
        "Frontmatter priority: primary\n"
        "Days since last commit: 9\n"
        "\n"
        "## Newer sibling docs (same domain)\n"
        "- docs/b.md (last modified 2d ago) — B\n"
        "\n"
        "## This doc's body\n"
        "Body.\n"
        "\n"
        "Emit a DocFreshnessReport.  `should_change_to` is None if the doc "
        "is genuinely still active.  Cite specific evidence (newer doc path, "
        "age, frontmatter signal) in `reason`."
    )


# ─── 4. Cache key and model-facing text ─────────────────────────────


def test_a_typed_input_keys_the_response_cache_like_the_dict_of_its_fields():
    """The decision scorer looks rows up by the archetype/context dict and
    calls the model with the typed input; both must land on one key."""
    from ai import llm_cache
    from ai.llm_decision_scorer import _cache_input
    as_dict = _cache_input("ramp", "tron_mana_advantage")
    typed = DecisionScorerInput(**as_dict)
    assert (llm_cache.cache_key("decision_scorer", "m", 3, typed)
            == llm_cache.cache_key("decision_scorer", "m", 3, as_dict))


def test_a_typed_input_reaches_the_model_as_its_rendering():
    from ai.llm_agents import render_user_prompt
    typed = DecisionScorerInput(archetype="ramp", context="tron_mana_advantage")
    assert render_user_prompt(typed) == typed.render() == (
        "archetype: ramp\ndecision_context: tron_mana_advantage\n")
    # An untyped structure still crosses as sorted JSON (cache-stable).
    assert render_user_prompt({"b": 1, "a": 2}) == json.dumps(
        {"a": 2, "b": 1}, sort_keys=True)


# ─── 5. The failing-test naming rule ────────────────────────────────


def test_the_failing_test_file_name_is_the_rule_lower_cased_with_underscores():
    assert (rule_test_file("a cast that draws more than the library holds "
                           "loses the game")
            == "tests/test_a_cast_that_draws_more_than_the_library_holds_"
               "loses_the_game.py")
    assert rule_test_file("Caster's X-cost") == "tests/test_caster_s_x_cost.py"


def test_the_failing_test_spec_prompt_states_the_test_file():
    inp = task_input("failing_test_spec", {"hypothesis": {
        "observed_symptom": "s", "suspected_subsystem": "ai.ev_player",
        "failing_test_rule": "a rule", "confidence": 0.5}})
    assert isinstance(inp, FailingTestSpecInput)
    assert "tests/test_a_rule.py" in inp.render()
