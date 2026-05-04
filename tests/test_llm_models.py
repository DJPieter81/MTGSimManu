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


def test_default_returns_built_in_when_no_env_or_override():
    """No env vars, no override → DEFAULT_MODELS table wins."""
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
