"""State-based actions are checked as a repeating fixpoint within a
single call (CR 704.3) — not just once per external call.

Rule under test
----------------
`GameState.check_state_based_actions` (the LIVE path — the dead
`SBAManager.check_and_perform_loop` has zero production callers) ran
its rule sequence exactly ONCE per call. CR 704.3: "Whenever a player
would get priority, the game checks for any of the listed conditions
... If any of these state-based actions are performed, the check is
made again ... This process repeats until no state-based actions are
performed."

Concretely, this matters now that ContinuousEffectsManager (0b) is
live: a legend-rule sacrifice (checked LAST in the rule sequence) can
retract a continuous toughness bonus a DIFFERENT creature depended on
to survive — but the toughness/lethal-damage checks (rules g/h) ran
EARLIER in the same pass, before the retraction happened, so a
single-pass implementation misses the cascade until the NEXT external
`check_state_based_actions()` call (13 call sites throughout the
engine, but not guaranteed to fire before other logic in the same
game step reads the stale state).

Class size: every state-based-action cascade — legend rule → stale
continuous effect retraction → toughness death is the fixture used
here, but the same fixpoint gap applies to any rule ordering where an
earlier-checked rule's outcome depends on a later rule's effect.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Supertype, ManaCost
from engine.continuous_effects import ContinuousEffect, Layer, PTSublayer
from engine.game_state import GameState


def _legend_template(name):
    return CardTemplate(
        name=name, card_types=[CardType.CREATURE], mana_cost=ManaCost(generic=1),
        supertypes=[Supertype.LEGENDARY], subtypes=[], power=1, toughness=1,
        loyalty=None, keywords=set(), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False, oracle_text="", tags=set(),
    )


def _fragile_template():
    # Printed toughness -1: dead on its own, alive only while a +0/+2
    # continuous bonus is active.
    return CardTemplate(
        name="Test Fragile Creature", card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=1, toughness=-1, loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )


def _put_on_battlefield(game, template, controller):
    card = CardInstance(
        template=template, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    game.players[controller].battlefield.append(card)
    return card


def test_legend_rule_cascade_resolves_within_one_call(card_db):
    """Sacrificing the older of two same-named legendary permanents
    retracts its continuous toughness grant, which must be caught in
    the SAME check_state_based_actions() call — the fragile creature
    must be dead by the time the call returns, not require a second
    external call."""
    game = GameState(rng=random.Random(0))

    lord_old = _put_on_battlefield(game, _legend_template("Test Legend"), controller=0)
    lord_new = _put_on_battlefield(game, _legend_template("Test Legend"), controller=0)
    assert lord_new.instance_id > lord_old.instance_id, (
        "test setup requires lord_new to be the newer instance — legend "
        "rule keeps the highest instance_id"
    )
    fragile = _put_on_battlefield(game, _fragile_template(), controller=0)

    # Only lord_old grants the toughness bonus that's keeping fragile
    # alive — lord_new is a "bait" duplicate with no effect of its own,
    # so sacrificing lord_old (the one legend rule removes) must cause
    # the bonus to vanish.
    game.continuous_effects.register(ContinuousEffect(
        source_id=lord_old.instance_id, source_name="Test Legend",
        layer=Layer.POWER_TOUGHNESS, pt_sublayer=PTSublayer.STATIC_MOD,
        affected=lambda g, c: c is fragile,
        apply=lambda g, c: setattr(c, 'cem_toughness_mod', c.cem_toughness_mod + 2),
        description="Test Legend: fragile gets +0/+2",
    ))
    game.continuous_effects.recalculate(game)
    assert fragile.toughness == 1, "setup: fragile should start alive (+0/+2 from lord_old)"

    game.check_state_based_actions()

    assert lord_old not in game.players[0].battlefield, (
        "legend rule should have sacrificed the older duplicate"
    )
    assert fragile not in game.players[0].battlefield, (
        "fragile should have died in the SAME check_state_based_actions() "
        "call once lord_old's toughness grant retracted — this requires "
        "the CR 704.3 fixpoint (re-checking after a state-based action "
        "is performed), not a single pass"
    )


def test_loop_is_bounded_by_sba_max_iterations(monkeypatch):
    """The fixpoint loop must be bounded by the configurable
    SBA_MAX_ITERATIONS constant, not loop forever if some pathological
    state keeps reporting 'performed'."""
    from engine import game_state as game_state_module

    game = GameState(rng=random.Random(0))
    call_count = {"n": 0}

    def _always_performed(self):
        call_count["n"] += 1
        return True

    monkeypatch.setattr(game_state_module, "SBA_MAX_ITERATIONS", 3, raising=False)
    monkeypatch.setattr(GameState, "_check_sba_once", _always_performed)

    game.check_state_based_actions()

    assert call_count["n"] == 3, (
        f"expected exactly 3 passes (the monkeypatched SBA_MAX_ITERATIONS "
        f"bound), got {call_count['n']} — the fixpoint loop is not "
        f"honoring the configurable safety valve"
    )
