"""Generic oracle-derived ETB resolution must not be skipped because a
card has an EFFECT_REGISTRY handler for an UNRELATED timing.

Rule under test
----------------
`ResolutionManager._handle_permanent_etb` (engine/spell_resolution.py)
computes ``has_specific_handler = template.name in EFFECT_REGISTRY._handlers``
— name-presence in the handler dict, across ALL timings — and uses it
to decide whether to fall back to the generic classifier-tag-driven
`resolve_etb_from_oracle`. A card whose only registration is for
EffectTiming.SPELL_RESOLVE (49 of ~104 registered entries) or another
non-ETB timing has nothing to do with what happens when it enters the
battlefield, yet the presence check silently skips its independent
oracle-derived ETB behavior.

This is the same bug shape as `PermanentEffects._creature_dies`'s
gate (tests/test_dies_trigger_registry_gate.py), now confirmed at a
second, higher-traffic call site — and a third at the X-cost
charge-counter gate a few lines earlier in the same function
(`has_dedicated_etb`). `engine/zone_transfer.py::_fire_etb_triggers`
already does this correctly (`h.timing == EffectTiming.ETB` — see its
`has_specific` local), which is the pattern both fixes here converge
on: `EFFECT_REGISTRY.has_handler(name, EffectTiming.ETB)`.

Class size: every card with (a) a registered handler for any
non-ETB timing and (b) an independent oracle-classifier-tagged ETB
effect hits this path.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState


def _synthetic_template(name, x_cost_data=None):
    return CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=["Test"],
        power=1, toughness=1, loyalty=None,
        keywords=set(), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False,
        oracle_text="Test fixture creature.",
        tags=set(),
        x_cost_data=x_cost_data,
    )


def _put_on_battlefield(game, template, controller):
    card = CardInstance(
        template=template, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    game.players[controller].battlefield.append(card)
    return card


def test_generic_etb_resolver_fires_despite_unrelated_timing_handler(card_db, monkeypatch):
    """A card with a registered SPELL_RESOLVE handler (unrelated to
    entering the battlefield) must still reach the generic
    oracle-derived ETB resolver — the handler-presence gate must be
    ETB-timing-scoped, not name-scoped."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    import engine.oracle_resolver as oracle_resolver_module

    template = _synthetic_template("Test Fixture: SpellResolve-Only Card")

    @EFFECT_REGISTRY.register(template.name, EffectTiming.SPELL_RESOLVE,
                              description="test fixture: unrelated timing")
    def _noop_spell_resolve(game, card, controller, targets=None, item=None):
        pass

    calls = []
    monkeypatch.setattr(
        oracle_resolver_module, "resolve_etb_from_oracle",
        lambda game, card, controller: calls.append(1) or True,
    )

    game = GameState(rng=random.Random(0))
    card = _put_on_battlefield(game, template, controller=0)

    game._handle_permanent_etb(card, controller=0)

    assert calls == [1], (
        "resolve_etb_from_oracle was not called for a card whose only "
        "registered handler is for an unrelated timing (SPELL_RESOLVE) — "
        "the ETB gate is checking name-presence instead of "
        "ETB-timing-presence"
    )


def test_generic_etb_resolver_suppressed_by_dedicated_etb_handler(card_db, monkeypatch):
    """A card WITH a registered ETB handler must NOT also reach the
    generic oracle-derived resolver — that would double-fire ETB
    behavior through two mechanisms."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    import engine.oracle_resolver as oracle_resolver_module

    template = _synthetic_template("Test Fixture: Dedicated-ETB Card")

    @EFFECT_REGISTRY.register(template.name, EffectTiming.ETB,
                              description="test fixture: dedicated ETB")
    def _noop_etb(game, card, controller, targets=None, item=None):
        pass

    calls = []
    monkeypatch.setattr(
        oracle_resolver_module, "resolve_etb_from_oracle",
        lambda game, card, controller: calls.append(1) or True,
    )

    game = GameState(rng=random.Random(0))
    card = _put_on_battlefield(game, template, controller=0)

    game._handle_permanent_etb(card, controller=0)

    assert calls == [], (
        "resolve_etb_from_oracle fired even though this card has its "
        "own registered ETB handler — should be suppressed to avoid "
        "double-firing"
    )


def test_x_cost_charge_counters_set_despite_unrelated_timing_handler(card_db):
    """The X-cost charge-counter placement in `ResolutionManager.
    resolve_stack` (engine/spell_resolution.py, just before it calls
    `_handle_permanent_etb`) gates on `has_dedicated_etb`, which must
    also be ETB-timing-scoped: a card with only a registered
    SPELL_RESOLVE handler and a charge_counters X-cost shape must
    still get its charge counters set when it resolves onto the
    battlefield. Exercises the real resolve_stack path (not
    _handle_permanent_etb directly — the charge-counter block runs
    in resolve_stack BEFORE _handle_permanent_etb is called)."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    from engine.stack import StackItem, StackItemType

    template = _synthetic_template(
        "Test Fixture: X-Cost SpellResolve-Only Card",
        x_cost_data={"effect": "charge_counters"},
    )

    @EFFECT_REGISTRY.register(template.name, EffectTiming.SPELL_RESOLVE,
                              description="test fixture: unrelated timing")
    def _noop_spell_resolve(game, card, controller, targets=None, item=None):
        pass

    game = GameState(rng=random.Random(0))
    card = CardInstance(
        template=template, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="stack",
    )
    card._game_state = game
    item = StackItem(
        item_type=StackItemType.SPELL, source=card, controller=0,
        targets=[], effect=None, description="", x_value=4,
    )
    game.stack.push(item)

    game.resolve_stack()

    assert card.other_counters.get("charge") == 4, (
        f"expected 4 charge counters from X={4}, got "
        f"{card.other_counters.get('charge')} — the has_dedicated_etb "
        f"gate is suppressing charge-counter placement for a card whose "
        f"only registered handler is an unrelated timing (SPELL_RESOLVE)"
    )
