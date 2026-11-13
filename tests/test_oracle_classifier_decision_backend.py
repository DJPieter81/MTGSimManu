"""A decision model classifies oracle tags as one yes/no question per tag.

A decision model (TypeSafe's Jev, model string ``typesafe:jev-latest``)
answers typed questions — booleans and closed choices — and refuses any
output with a free-text field. The tag classifier's list-of-strings
schema is therefore not askable. For a decision model the same task asks
one boolean per closed-set tag; the answers map back to the same tag
list the committed cache stores, so every consumer is unchanged.

Rules pinned:
* the decision schema has exactly one boolean field per `Tag` and no
  free-text field;
* every question's wording is the tag's row in the versioned prompt's
  tag table (one source for the tag definitions);
* `build_agent` picks the decision schema for a decision model and the
  list schema otherwise;
* true answers map back to tag names in `Tag` order;
* the builder's `classify_one` reads either output shape.
All offline: no API call is made.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel


def test_the_decision_schema_asks_one_boolean_per_tag_and_nothing_else():
    from ai.llm_schemas import oracle_tag_decision_model
    from ai.oracle_classifier import Tag

    model = oracle_tag_decision_model()
    fields = model.model_fields
    assert set(fields) == {t.name.lower() for t in Tag}
    assert all(f.annotation is bool for f in fields.values())


def test_every_tag_question_is_its_row_in_the_prompt_tag_table():
    from ai.llm_prompts import latest_version, load_prompt
    from ai.llm_schemas import oracle_tag_decision_model, oracle_tag_table
    from ai.oracle_classifier import Tag

    table = oracle_tag_table()
    assert set(table) == {t.name for t in Tag}
    prompt = load_prompt("classify_oracle", latest_version("classify_oracle"))
    model = oracle_tag_decision_model()
    for tag in Tag:
        desc = model.model_fields[tag.name.lower()].description
        assert desc == table[tag.name]
        assert desc and desc in prompt


def test_build_agent_asks_a_decision_model_the_decision_schema():
    pytest.importorskip("pydantic_ai")
    from ai.llm_agents import build_agent
    from ai.llm_schemas import OracleTagClassification, oracle_tag_decision_model

    jev = build_agent("classify_oracle", model="typesafe:jev-latest",
                      use_cache=False, instrument=False)
    assert jev.output_type is oracle_tag_decision_model()
    text = build_agent("classify_oracle", model="anthropic:claude-haiku-4-5",
                       use_cache=False, instrument=False)
    assert text.output_type is OracleTagClassification


def test_true_answers_map_back_to_tag_names_in_tag_order():
    from ai.llm_schemas import decision_to_tags, oracle_tag_decision_model
    from ai.oracle_classifier import Tag

    model = oracle_tag_decision_model()
    answers = {name: False for name in model.model_fields}
    answers["target_any_damage"] = True
    answers["impulse_draw"] = True
    tags = decision_to_tags(model(**answers))
    order = [t.name for t in Tag]
    assert tags == sorted(["IMPULSE_DRAW", "TARGET_ANY_DAMAGE"], key=order.index)


def test_classify_one_reads_either_output_shape():
    from ai.llm_schemas import OracleTagClassification, oracle_tag_decision_model
    from tools.build_oracle_classifier_cache import classify_one

    card = {"name": "Fixture", "text": "Exile the top two cards of your library.",
            "types": ["Sorcery"]}

    class _Result:
        def __init__(self, output):
            self.output = output

    class _Agent:
        def __init__(self, output):
            self._o = output

        def run_sync(self, prompt):
            return _Result(self._o)

    model = oracle_tag_decision_model()
    answers = {name: False for name in model.model_fields}
    answers["impulse_draw"] = True
    tags, _sha = classify_one(_Agent(model(**answers)), card)
    assert tags == ["IMPULSE_DRAW"]
    listed = OracleTagClassification(card_name="Fixture", tags=["IMPULSE_DRAW"])
    tags, _sha = classify_one(_Agent(listed), card)
    assert tags == ["IMPULSE_DRAW"]
