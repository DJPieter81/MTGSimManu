"""Schema validation tests for ai.schemas Pydantic models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai.schemas import (
    Choice, CostDescriptor, EffectDescriptor, OptionalCost,
)


def test_cost_descriptor_round_trip():
    c = CostDescriptor(kind="life", amount=2)
    dumped = c.model_dump()
    assert dumped == {"kind": "life", "amount": 2,
                       "color": None, "target_filter": None}
    restored = CostDescriptor.model_validate(dumped)
    assert restored == c


def test_cost_descriptor_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        CostDescriptor(kind="not_a_real_kind", amount=2)


def test_effect_descriptor_with_colors():
    e = EffectDescriptor(kind="produce_mana", magnitude=1,
                          colors=("U", "R"))
    assert e.colors == ("U", "R")


def test_effect_descriptor_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        EffectDescriptor(kind="bogus")


def test_choice_carries_apply_and_source():
    def apply(s):
        return s

    c = Choice(name="hold", apply=apply, source="cast")
    assert c.name == "hold"
    assert c.source == "cast"
    assert c.apply is apply


def test_choice_rejects_unknown_source():
    def apply(s):
        return s

    with pytest.raises(ValidationError):
        Choice(name="bad", apply=apply, source="not_a_source")


def test_optional_cost_combines_cost_and_effect():
    cost = CostDescriptor(kind="life", amount=2)
    effect = EffectDescriptor(kind="etb_untapped")

    def to_game(g, p):
        pass

    def to_snap(s):
        return s

    opt = OptionalCost(
        name="Blood Crypt: pay 2 life, ETB untapped",
        cost=cost, effect=effect,
        apply_to_game=to_game, apply_to_snap=to_snap,
    )
    assert opt.cost.amount == 2
    assert opt.effect.kind == "etb_untapped"
    assert opt.apply_to_game is to_game


def test_models_are_frozen():
    c = CostDescriptor(kind="life", amount=2)
    with pytest.raises(ValidationError):
        c.amount = 5
