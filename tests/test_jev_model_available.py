"""TypeSafe AI's Jev is reachable as a pydantic-ai model.

Jev is a decision model: it answers typed questions (choices, scores,
booleans) with calibrated confidence instead of writing text. pydantic-ai
2.51 ships the provider, so any agent in this repo can target it with the
model string ``typesafe:jev-latest`` (``MTG_LLM_MODEL_<TASK>``). These
tests run offline and never call the API.
"""
from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, Field

pydantic_ai = pytest.importorskip("pydantic_ai")
# The provider module raises ImportError (not ModuleNotFoundError) when the
# TypeSafe SDK is absent, so skip on the SDK itself first — CI installs its
# own package list, which may not include it.
pytest.importorskip("typesafe_sdk")
typesafe = pytest.importorskip("pydantic_ai.models.typesafe")


def test_the_jev_model_string_resolves_to_the_typesafe_model(monkeypatch):
    from pydantic_ai.models import infer_model
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-construction-only")
    model = infer_model("typesafe:jev-latest")
    assert isinstance(model, typesafe.TypeSafeModel)
    assert model.model_name == "jev-latest"


def test_a_decision_shaped_output_builds_a_jev_agent(monkeypatch):
    """An output made only of choices and booleans is Jev's native shape."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-construction-only")

    class Verdict(BaseModel):
        kind: Literal["impulse_draw", "cantrip", "neither"] = Field(
            description="Which mechanic this card text is.")
        repeatable: bool = Field(description="Can it be used every turn?")

    agent = pydantic_ai.Agent("typesafe:jev-latest", output_type=Verdict)
    assert isinstance(agent.model, typesafe.TypeSafeModel)
