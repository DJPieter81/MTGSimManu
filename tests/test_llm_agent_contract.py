"""Every LLM agent is schema-first, prompt-versioned and eval-covered.

The agent layer's contract, per task (`ai.llm_models.LLMTask`):

  * a typed pydantic output schema (`ai.llm_agents._OUTPUT_TYPES`);
  * a versioned system prompt (`ai/llm_prompts/<task>_v<N>.md`);
  * a typed input whose renderer is the user prompt (`ai.llm_inputs`);
  * golden eval pairs, a pass threshold and a real-model floor
    (`tests/eval/`), whose `expected` blocks validate against the
    output schema.

A task with a decision-model shape (`_DECISION_OUTPUT_TYPES`) must be
askable of a decision model — only yes/no and pick-one fields within
the provider's option cap — and its answers must map back onto the
canonical schema (`canonical_output`).  The last test runs the decision
scorer end to end on the decision model's real request path against a
stub client: no key, no network.
"""
from __future__ import annotations

import json
import typing
from types import SimpleNamespace

import pytest

from ai.llm_agents import (
    _DECISION_OUTPUT_TYPES,
    _OUTPUT_TYPES,
    canonical_output,
    supported_tasks,
)
from ai.llm_inputs import TASK_INPUT_TYPES, DecisionScorerInput
from ai.llm_prompts import available_versions, latest_version, load_prompt
from ai.llm_schemas import (
    DECISION_WEIGHT_DECIMALS,
    DECISION_WEIGHT_MAX,
    BugHypothesis,
    DecisionScoringWeights,
    OracleTagClassification,
    decision_weight_decision_model,
    decision_weight_options,
)


TASKS = sorted(supported_tasks())

# A golden set this small is a smoke corpus, not a benchmark; three is
# the size of the smallest seed set the harness shipped with.
MIN_GOLDEN_PAIRS = 3


@pytest.mark.parametrize("task", TASKS)
def test_every_task_has_an_output_schema_a_versioned_prompt_and_a_renderer(task):
    assert task in _OUTPUT_TYPES
    assert available_versions(task), f"no ai/llm_prompts/{task}_v<N>.md"
    assert load_prompt(task, latest_version(task)).strip()
    assert task in TASK_INPUT_TYPES


@pytest.mark.parametrize("task", TASKS)
def test_every_task_has_golden_evals_a_threshold_and_a_real_model_floor(task):
    from tests.eval.llm_eval import DEFAULT_THRESHOLD, load_golden_pairs
    from tests.eval.test_real_model_eval import _PASS_RATE_FLOOR
    assert len(load_golden_pairs(task)) >= MIN_GOLDEN_PAIRS, task
    assert task in DEFAULT_THRESHOLD
    assert task in _PASS_RATE_FLOOR


@pytest.mark.parametrize("task", TASKS)
def test_every_golden_expected_validates_against_the_output_schema(task):
    """A golden must describe an output the agent could legally return;
    goldens may omit optional fields (the schema fills the defaults)."""
    from tests.eval.llm_eval import load_golden_pairs
    schema = _OUTPUT_TYPES[task]
    item = (typing.get_args(schema)[0]
            if typing.get_origin(schema) is list else schema)
    for pair in load_golden_pairs(task):
        item.model_validate_json(json.dumps(pair["expected"]))


# ─── Decision-model shapes ──────────────────────────────────────────


def _askable(annotation) -> bool:
    if annotation is bool:
        return True
    return typing.get_origin(annotation) is typing.Literal


@pytest.mark.parametrize("task", sorted(_DECISION_OUTPUT_TYPES))
def test_a_decision_shape_asks_only_yes_no_and_pick_one_questions(task):
    shape = _DECISION_OUTPUT_TYPES[task]()
    for name, field in shape.model_fields.items():
        assert _askable(field.annotation), (task, name, field.annotation)
        assert field.description, (task, name, "a question needs wording")


@pytest.mark.parametrize("task", sorted(_DECISION_OUTPUT_TYPES))
def test_a_decision_shapes_pick_one_fields_fit_the_providers_option_cap(task):
    profile = pytest.importorskip("pydantic_ai.profiles.typesafe")
    cap = profile._JEV_MAX_CHOICE_OPTIONS
    shape = _DECISION_OUTPUT_TYPES[task]()
    for name, field in shape.model_fields.items():
        if typing.get_origin(field.annotation) is typing.Literal:
            assert len(typing.get_args(field.annotation)) <= cap, (task, name)


def test_the_weight_grid_spans_the_documented_range_at_the_prompts_resolution():
    """The decision shape of the scorer is a pick on a grid: the
    documented [0, 20] weight range at the scorer prompt's 1dp rounding.
    Every committed weight must lie on it, so each committed row is an
    answer the decision model could give."""
    from ai import llm_decision_scorer as scorer
    options = decision_weight_options()
    assert options[0] == f"{0:.{DECISION_WEIGHT_DECIMALS}f}"
    assert float(options[-1]) == DECISION_WEIGHT_MAX
    steps = {round(float(b) - float(a), DECISION_WEIGHT_DECIMALS)
             for a, b in zip(options, options[1:])}
    assert steps == {10 ** -DECISION_WEIGHT_DECIMALS}
    rows = json.loads(scorer._WEIGHTS_FILE.read_text())["rows"]
    on_grid = {float(o) for o in options}
    assert all(float(r["weight"]) in on_grid for r in rows)


def test_a_decision_answer_maps_onto_the_canonical_schema():
    pick = decision_weight_decision_model()(weight="2.5")
    weights = canonical_output("decision_scorer", pick)
    assert isinstance(weights, DecisionScoringWeights) and weights.weight == 2.5

    from ai.llm_schemas import oracle_tag_decision_model
    shape = oracle_tag_decision_model()
    answers = shape(**{name: name == "forced_discard"
                       for name in shape.model_fields})
    tags = canonical_output("classify_oracle", answers, {"name": "A Card"})
    assert tags == OracleTagClassification(card_name="A Card",
                                           tags=["FORCED_DISCARD"])


def test_a_canonical_answer_passes_through_unchanged():
    row = DecisionScoringWeights(weight=1.0)
    assert canonical_output("decision_scorer", row) is row
    hyp = [BugHypothesis(observed_symptom="s", suspected_subsystem="other",
                         failing_test_rule="r", confidence=0.5)]
    assert canonical_output("diagnose_replay", hyp) is hyp


# ─── The decision scorer on the decision model, end to end, offline ──


def _strings(value):
    """Every string inside a nested JSON-like value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def test_the_decision_scorer_runs_on_the_decision_models_request_path():
    """Build the scorer agent on the decision model, swap its client for
    a stub, and run: the agent must ask exactly one pick-one question
    over the whole weight grid about the rendered input, and the pick
    must come back as the canonical weight row."""
    typesafe_sdk = pytest.importorskip("typesafe_sdk")
    typesafe = pytest.importorskip("pydantic_ai.models.typesafe")
    from pydantic_ai.providers import Provider

    from ai.llm_agents import build_agent
    from ai.llm_models import DEFAULT_MODELS

    asked = {}

    class _Client:
        async def system_one(self, state, questions, **kwargs):
            asked["state"], asked["questions"] = state, questions
            (name, _), = questions.items()
            answer = typesafe_sdk.ChoiceAnswer(
                choice="4.0", confidence=0.9, probabilities={"4.0": 0.9})
            return SimpleNamespace(
                answers={name: answer}, model="stub",
                usage=SimpleNamespace(input_tokens=1, output_tokens=0),
                request_id="stub")

    class _Provider(Provider):
        name = "typesafe"
        base_url = "https://stub.invalid"
        client = _Client()

        def __init__(self):
            pass

    model_name = DEFAULT_MODELS["decision_scorer"].split(":", 1)[1]
    model = typesafe.TypeSafeModel(model_name, provider=_Provider())
    agent = build_agent("decision_scorer", model=DEFAULT_MODELS["decision_scorer"],
                        use_cache=False, instrument=False)
    prompt = DecisionScorerInput(archetype="ramp",
                                 context="tron_mana_advantage").render()
    with agent.override(model=model):
        result = agent.run_sync(prompt)

    (question,) = asked["questions"].values()
    assert isinstance(question, typesafe_sdk.Choice)
    assert list(question.criteria) == list(decision_weight_options())
    assert any(prompt in s for s in _strings(asked["state"])), (
        "the rendered input is the state the decision model judges")
    weights = canonical_output("decision_scorer", result.output)
    assert weights == DecisionScoringWeights(weight=4.0)
