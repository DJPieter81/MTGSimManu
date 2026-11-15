"""Resolution-order contract for `ai.llm_models.select_model`.

The matrix this test locks in (highest-priority first):

    explicit override  >  MTG_LLM_MODEL_<TASK>  >  MTG_LLM_MODEL  >
    [synth_gameplan only] MTG_SYNTH_MODEL (legacy, deprecated)  >
    DEFAULT_MODELS[task]

If any of these layers swaps order, an operator who set
`MTG_LLM_MODEL=anthropic:sonnet` for a CI run could see one task
silently fall back to Haiku because of a legacy env var still on the
shell.  That has happened on this project before
(`MTG_SYNTH_MODEL` was the only knob in PR #258), so the legacy
deprecation alias matters."""
from __future__ import annotations

import warnings

import pytest

from ai.llm_models import (
    DEFAULT_MODELS,
    GLOBAL_ENV,
    LEGACY_SYNTH_ENV,
    TASK_ENV_FMT,
    select_model,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Clear every env var this module reads so each test starts clean."""
    monkeypatch.delenv(GLOBAL_ENV, raising=False)
    monkeypatch.delenv(LEGACY_SYNTH_ENV, raising=False)
    for task in DEFAULT_MODELS:
        monkeypatch.delenv(TASK_ENV_FMT.format(task_upper=task.upper()), raising=False)
    # Reset the deprecation-warning latch so each test sees a fresh warning.
    from ai import llm_models
    for k in list(llm_models._LEGACY_WARNED):
        llm_models._LEGACY_WARNED[k] = False
    yield


def test_default_returns_built_in_when_no_env_or_override(monkeypatch):
    """No env vars, no override → DEFAULT_MODELS table wins (with every
    provider SDK present; the SDK-absent fallback is pinned below)."""
    from ai import llm_models
    monkeypatch.setattr(llm_models, "_module_importable", lambda module: True)
    for task, expected in DEFAULT_MODELS.items():
        assert select_model(task) == expected


def test_explicit_override_beats_env_vars(monkeypatch):
    """`override=` argument has highest priority."""
    monkeypatch.setenv(GLOBAL_ENV, "anthropic:from-global")
    monkeypatch.setenv(TASK_ENV_FMT.format(task_upper="SYNTH_GAMEPLAN"), "anthropic:from-task")
    assert select_model("synth_gameplan", override="anthropic:from-arg") == "anthropic:from-arg"


def test_task_specific_env_beats_global(monkeypatch):
    """`MTG_LLM_MODEL_<TASK>` beats the global `MTG_LLM_MODEL`."""
    monkeypatch.setenv(GLOBAL_ENV, "anthropic:from-global")
    monkeypatch.setenv(TASK_ENV_FMT.format(task_upper="HANDLER_AUDIT"), "anthropic:from-task")
    assert select_model("handler_audit") == "anthropic:from-task"
    # Other tasks still see the global var.
    assert select_model("synth_gameplan") == "anthropic:from-global"


def test_global_env_applies_to_every_task(monkeypatch):
    monkeypatch.setenv(GLOBAL_ENV, "anthropic:everywhere")
    for task in DEFAULT_MODELS:
        assert select_model(task) == "anthropic:everywhere"


def test_legacy_synth_env_alias_routes_to_synth_gameplan(monkeypatch):
    """`MTG_SYNTH_MODEL` continues to control the synth_gameplan task
    (PR #258 backward compatibility) — but it does NOT affect any
    other task."""
    monkeypatch.setenv(LEGACY_SYNTH_ENV, "anthropic:legacy-synth")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        assert select_model("synth_gameplan") == "anthropic:legacy-synth"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
        "legacy MTG_SYNTH_MODEL must emit a DeprecationWarning on first read"
    )
    # Other tasks still see the default.
    assert select_model("handler_audit") == DEFAULT_MODELS["handler_audit"]


def test_legacy_synth_env_loses_to_task_specific_env(monkeypatch):
    """If both `MTG_LLM_MODEL_SYNTH_GAMEPLAN` and `MTG_SYNTH_MODEL` are
    set, the new task-specific var wins — the legacy one is the
    last-resort fallback before defaults."""
    monkeypatch.setenv(LEGACY_SYNTH_ENV, "anthropic:legacy-synth")
    monkeypatch.setenv(TASK_ENV_FMT.format(task_upper="SYNTH_GAMEPLAN"), "anthropic:new-task")
    assert select_model("synth_gameplan") == "anthropic:new-task"


def test_legacy_synth_env_loses_to_global_env(monkeypatch):
    """If both `MTG_LLM_MODEL` and `MTG_SYNTH_MODEL` are set, the new
    global var wins."""
    monkeypatch.setenv(LEGACY_SYNTH_ENV, "anthropic:legacy-synth")
    monkeypatch.setenv(GLOBAL_ENV, "anthropic:global")
    assert select_model("synth_gameplan") == "anthropic:global"


def test_deprecation_warning_emitted_only_once_per_process(monkeypatch):
    """The warning is one-shot to avoid spamming long-running scripts.
    Subsequent calls still resolve the legacy value but stay silent."""
    monkeypatch.setenv(LEGACY_SYNTH_ENV, "anthropic:legacy-synth")
    with warnings.catch_warnings(record=True) as first:
        warnings.simplefilter("always", DeprecationWarning)
        select_model("synth_gameplan")
    assert any(issubclass(w.category, DeprecationWarning) for w in first)

    with warnings.catch_warnings(record=True) as second:
        warnings.simplefilter("always", DeprecationWarning)
        select_model("synth_gameplan")
    assert not any(issubclass(w.category, DeprecationWarning) for w in second), (
        "deprecation warning should be one-shot"
    )


# ─── Tier assignment (2026-11 registry refresh) ─────────────────────────

# The tier each task's default must sit on (see the ai.llm_models
# docstring for the rationale).  A family is matched on the model id,
# so the pin survives a same-tier version bump but not a tier change.
_DIFFICULT_REASONING_TASKS = (
    "synth_gameplan", "diagnose_replay", "failing_test_spec", "handler_audit",
)


def _family(model: str) -> str:
    return next((f for f in ("opus", "sonnet", "haiku") if f in model), "")


def test_difficult_reasoning_tasks_default_to_the_opus_tier():
    """Replay diagnosis, gameplan synthesis, rule-phrased failing-test
    specs and handler audits are the project's difficult decisions;
    they run a handful of times per session, offline, so per-call
    quality dominates cost.  All four share one Opus-tier default."""
    models = {DEFAULT_MODELS[t] for t in _DIFFICULT_REASONING_TASKS}
    assert len(models) == 1, models
    assert _family(models.pop()) == "opus"


def test_committed_tag_classification_sits_above_pure_extraction():
    """A wrong oracle tag distorts AI scoring in every sim until the
    next audit, so classification rides the Sonnet tier; doc-freshness
    extraction (which only proposes a frontmatter edit) rides Haiku."""
    assert _family(DEFAULT_MODELS["classify_oracle"]) == "sonnet"
    assert _family(DEFAULT_MODELS["audit_doc_freshness"]) == "haiku"


def test_per_decision_scoring_defaults_to_a_decision_model():
    """One typed answer per (archetype, context) is a decision model's
    native shape; the scorer defaults to one and has a language-model
    fallback for checkouts without its SDK."""
    from ai.llm_agents import is_decision_model
    from ai.llm_models import FALLBACK_MODELS
    assert is_decision_model(DEFAULT_MODELS["decision_scorer"])
    assert not is_decision_model(FALLBACK_MODELS["decision_scorer"])


def test_no_default_rides_a_superseded_generation():
    """No default or fallback may name a model a newer same-tier model
    in the pricing table supersedes.  The previous generations stay in
    the pricing table only so historical call records still price."""
    from ai.llm_models import FALLBACK_MODELS
    superseded = {
        "anthropic:claude-sonnet-5", "anthropic:claude-sonnet-4-6",
        "anthropic:claude-haiku-4-5", "anthropic:claude-haiku-4-5-20251001",
        "anthropic:claude-opus-4-7", "anthropic:claude-opus-4-7-1m",
        "anthropic:claude-opus-4-8",
    }
    for task, model in {**DEFAULT_MODELS, **FALLBACK_MODELS}.items():
        assert model not in superseded, (task, model)


def test_every_default_model_has_a_pricing_entry():
    """Registry/pricing coherence: every model in DEFAULT_MODELS (and
    every fallback) must price in MODEL_PRICING_USD_PER_MTOKEN — an
    unknown model estimates as free and disables the budget gate for
    that task."""
    from ai.llm_metrics import MODEL_PRICING_USD_PER_MTOKEN
    from ai.llm_models import FALLBACK_MODELS
    for task, model in {**DEFAULT_MODELS, **FALLBACK_MODELS}.items():
        assert model in MODEL_PRICING_USD_PER_MTOKEN, (task, model)


# ─── Provider fallback ──────────────────────────────────────────────────

def test_a_default_whose_provider_sdk_is_absent_resolves_to_the_fallback(
    monkeypatch,
):
    """A checkout without the decision-model SDK still gets a working,
    priced model for the decision scorer: its language-model fallback."""
    from ai import llm_models
    monkeypatch.setattr(llm_models, "_module_importable", lambda module: False)
    assert (select_model("decision_scorer")
            == llm_models.FALLBACK_MODELS["decision_scorer"])


def test_a_default_whose_provider_sdk_is_present_is_kept(monkeypatch):
    from ai import llm_models
    monkeypatch.setattr(llm_models, "_module_importable", lambda module: True)
    assert select_model("decision_scorer") == DEFAULT_MODELS["decision_scorer"]


def test_an_operator_named_model_is_never_rewritten_by_the_fallback(
    monkeypatch,
):
    """Env vars and overrides name exactly the model the operator wants,
    SDK present or not — the fallback only replaces a built-in default."""
    from ai import llm_models
    monkeypatch.setattr(llm_models, "_module_importable", lambda module: False)
    jev = DEFAULT_MODELS["decision_scorer"]
    assert select_model("decision_scorer", override=jev) == jev
    monkeypatch.setenv(
        TASK_ENV_FMT.format(task_upper="DECISION_SCORER"), jev)
    assert select_model("decision_scorer") == jev


def test_providers_without_a_listed_sdk_are_available():
    from ai.llm_models import provider_available
    assert provider_available("anthropic:any-model")
    assert provider_available("test")


# ─── Determinism: committed decision weights are model-independent ──────

def test_committed_decision_weights_load_whatever_model_the_scorer_resolves(
    tmp_path, monkeypatch,
):
    """Offline sims read `ai/llm_decision_weights.json`, keyed by
    archetype + context only.  Changing the scorer's default model (or
    its SDK being absent) must not orphan those rows: the offline
    weight is the committed row under every model the scorer can
    resolve to.  The pair below is one whose committed row differs from
    the in-code default, so a miss would be visible."""
    import json
    from pathlib import Path

    from ai import llm_cache, llm_decision_scorer as scorer, llm_models

    monkeypatch.setenv("MTG_LLM_DECISION_SCORER_OFFLINE", "1")
    cache_dir = tmp_path / "cache_llm"
    monkeypatch.setattr(llm_cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(llm_cache, "CACHE_DB", cache_dir / "responses.sqlite")
    rows = json.loads(Path(scorer._WEIGHTS_FILE).read_text())["rows"]
    row = next(r for r in rows
               if (r["archetype"], r["context"]) not in scorer.DEFAULT_WEIGHTS
               and r["weight"] != scorer.DEFAULT_WEIGHTS.get(
                   ("*", r["context"]), scorer.NEUTRAL_WEIGHT))
    pair = (row["archetype"], row["context"])

    scorer._reset_weights_file_cache()
    try:
        seen = set()
        for importable in (True, False):
            monkeypatch.setattr(llm_models, "_module_importable",
                                lambda module, ok=importable: ok)
            seen.add(select_model("decision_scorer"))
            assert scorer.weight(*pair) == row["weight"]
        assert len(seen) == 2, "both the default and the fallback were tried"
    finally:
        scorer._reset_weights_file_cache()
