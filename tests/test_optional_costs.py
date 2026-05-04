"""Tests for engine.optional_costs — oracle-driven optional cost discovery.

These tests verify the OptionalCost discovery + decide_optional_cost
flow without coupling to specific cards or archetypes.  The point of
the abstraction is that adding a new shock land, painland, or future
optional-cost mechanic requires NO callback changes — only oracle-
parser extensions.
"""
from __future__ import annotations
from types import SimpleNamespace

import pytest

from ai.ev_evaluator import EVSnapshot
from ai.schemas import OptionalCost
from engine.optional_costs import (
    parse_optional_costs, _snap_pay_life, _snap_add_untapped_mana,
)


def _fake_card(name: str, untap_life_cost: int = 0,
               produces_mana: tuple[str, ...] = ()) -> SimpleNamespace:
    """Build a minimal fake CardInstance with just the fields the
    parser inspects.  Avoids constructing a full CardTemplate /
    CardInstance, which would require a card database lookup."""
    template = SimpleNamespace(
        name=name,
        untap_life_cost=untap_life_cost,
        produces_mana=list(produces_mana),
    )
    return SimpleNamespace(template=template)


def test_no_optional_cost_for_basic_land():
    card = _fake_card("Mountain", untap_life_cost=0,
                       produces_mana=("R",))
    assert parse_optional_costs(card, trigger="etb") == []


def test_shock_land_produces_one_optional_cost():
    card = _fake_card("Blood Crypt", untap_life_cost=2,
                       produces_mana=("B", "R"))
    costs = parse_optional_costs(card, trigger="etb")
    assert len(costs) == 1
    opt = costs[0]
    assert opt.cost.kind == "life"
    assert opt.cost.amount == 2
    assert opt.effect.kind == "etb_untapped"
    assert opt.effect.colors == ("B", "R")


def test_shock_apply_to_snap_subtracts_life_and_adds_mana():
    card = _fake_card("Blood Crypt", untap_life_cost=2,
                       produces_mana=("B", "R"))
    opt = parse_optional_costs(card, trigger="etb")[0]
    snap = EVSnapshot(my_life=20, my_mana=2, my_total_lands=2,
                      my_mana_by_color={"B": 1})
    opt.apply_to_snap(snap)
    assert snap.my_life == 18
    assert snap.my_mana == 3
    assert snap.my_total_lands == 3
    assert snap.my_mana_by_color["B"] == 2
    assert snap.my_mana_by_color["R"] == 1


def test_no_costs_for_unsupported_trigger():
    card = _fake_card("Blood Crypt", untap_life_cost=2,
                       produces_mana=("B", "R"))
    assert parse_optional_costs(card, trigger="cast") == []
    assert parse_optional_costs(card, trigger="tap") == []


def test_apply_to_snap_does_not_share_color_dict_with_caller():
    """The OptionalCost projection must not mutate the caller's
    mana_by_color dict — `replace()` shallow-copies, so the dict
    reference is shared until we swap in a fresh one."""
    card = _fake_card("Steam Vents", untap_life_cost=2,
                       produces_mana=("U", "R"))
    opt = parse_optional_costs(card, trigger="etb")[0]
    original = {"U": 2}
    snap = EVSnapshot(my_life=20, my_mana_by_color=original).fast_replace()
    # apply mutates a copy of snap's dict in place; the caller's
    # original dict reference must remain unchanged
    opt.apply_to_snap(snap)
    assert original == {"U": 2}, "caller's mana_by_color was mutated"


def test_snap_pay_life_subtracts():
    snap = EVSnapshot(my_life=20)
    out = _snap_pay_life(snap, 3)
    assert out.my_life == 17
    assert out is snap  # in-place by design


def test_snap_add_untapped_mana_handles_empty_color_dict():
    snap = EVSnapshot(my_mana=0, my_total_lands=0)
    snap.my_mana_by_color = {}
    out = _snap_add_untapped_mana(snap, ("G", "W"))
    assert out.my_mana == 1
    assert out.my_total_lands == 1
    assert out.my_mana_by_color == {"G": 1, "W": 1}


def test_decide_optional_cost_delegates_to_kernel():
    """End-to-end: AICallbacks.decide_optional_cost builds a [pay,
    skip] Choice list and lets the kernel pick.  When paying makes
    the position better (extra mana enables a spell), it picks pay;
    when paying makes it worse (life loss with no benefit), skip."""
    from engine.game_runner import AICallbacks

    cb = AICallbacks()

    # Construct a minimal "game" stub — only what _archetype, the
    # multi-shock stagger gate, and the kernel need.
    player = SimpleNamespace(deck_name="midrange-test", battlefield=[])
    game = SimpleNamespace(
        players=[player],
        callbacks=cb,
    )

    # Build a synthetic OptionalCost: pay 2 life, gain a big EV bump
    def big_apply(s):
        s.my_life -= 2
        s.my_power += 10  # huge clock-diff swing — should win
        return s

    from ai.schemas import CostDescriptor, EffectDescriptor
    big_opt = OptionalCost(
        name="huge_value",
        cost=CostDescriptor(kind="life", amount=2),
        effect=EffectDescriptor(kind="etb_untapped"),
        apply_to_game=lambda g, p: None,
        apply_to_snap=big_apply,
    )
    # Bypass snapshot_from_game by stubbing it
    import ai.ev_evaluator as ev
    orig = ev.snapshot_from_game
    try:
        ev.snapshot_from_game = lambda g, p: EVSnapshot(
            my_life=20, opp_life=20, my_power=2, opp_power=2,
            my_creature_count=1, opp_creature_count=1,
            my_hand_size=4, opp_hand_size=4,
            my_mana=3, opp_mana=3, my_total_lands=3, opp_total_lands=3,
            turn_number=4,
        )
        assert cb.decide_optional_cost(game, 0, big_opt) is True
    finally:
        ev.snapshot_from_game = orig

    # Tiny benefit, large life cost → kernel picks skip
    def tiny_apply(s):
        s.my_life -= 5  # heavy cost, no benefit
        return s

    tiny_opt = OptionalCost(
        name="bad_trade",
        cost=CostDescriptor(kind="life", amount=5),
        effect=EffectDescriptor(kind="etb_untapped"),
        apply_to_game=lambda g, p: None,
        apply_to_snap=tiny_apply,
    )
    try:
        ev.snapshot_from_game = lambda g, p: EVSnapshot(
            my_life=20, opp_life=20, my_power=2, opp_power=2,
            my_creature_count=1, opp_creature_count=1,
            my_hand_size=4, opp_hand_size=4,
            my_mana=3, opp_mana=3, my_total_lands=3, opp_total_lands=3,
            turn_number=4,
        )
        assert cb.decide_optional_cost(game, 0, tiny_opt) is False
    finally:
        ev.snapshot_from_game = orig
