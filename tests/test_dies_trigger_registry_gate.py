"""Oracle-derived dies triggers must not be skipped because a card has
an EFFECT_REGISTRY handler for an UNRELATED timing.

Rule under test
----------------
`PermanentEffects._creature_dies` (engine/permanent_effects.py) gates
the generic oracle-text-derived dies trigger (`resolve_dies_trigger`)
behind ``creature.template.name not in EFFECT_REGISTRY._handlers`` —
membership in the handler dict at all, regardless of *timing*.
`EFFECT_REGISTRY` is keyed by card name with a list of
``(timing, handler)`` pairs; 53 of ~104 registered entries are
``EffectTiming.ETB``, 49 are ``EffectTiming.SPELL_RESOLVE``, and only
1 is ``EffectTiming.DIES``. A card with a registered ETB handler (an
enters-the-battlefield effect) has nothing to do with what happens
when it dies, yet the presence check silently skips its unrelated
oracle-derived dies clause.

Class size: every card with both (a) a registered handler for any
timing and (b) an independent oracle-text dies/leaves-the-battlefield
clause hits this path — the gate's granularity (per-name) doesn't
match its intent (per-timing), so this is a mechanic-level bug, not
a single-card one.

Fix: the gate must check ``EFFECT_REGISTRY.has_handler(name,
EffectTiming.DIES)`` — "does this card have its OWN registered dies
handler" — not "does this card have a registered handler of any
kind." Only a registered DIES handler should suppress the generic
oracle-derived path (to avoid double-firing the same trigger through
two paths); an ETB/SPELL_RESOLVE/ATTACK/END_STEP handler must not.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, Keyword
from engine.game_state import GameState


def _synthetic_dies_draw_creature():
    """A creature whose only ability is oracle-text 'when this dies,
    draw a card' — the simplest pattern resolve_dies_trigger
    recognizes (engine/oracle_resolver.py: 'dies' + 'draw' in the
    same ability paragraph)."""
    from engine.cards import ManaCost, CardType
    return CardTemplate(
        name="Test Fixture: Dies-Draw Creature",
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=["Test"],
        power=1, toughness=1, loyalty=None,
        keywords=set(), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False,
        oracle_text="When this creature dies, draw a card.",
        tags=set(),
    )


def _put_on_battlefield(game, template, controller):
    card = CardInstance(
        template=template, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def test_dies_trigger_fires_when_card_has_only_an_unrelated_timing_handler(card_db):
    """A card with a registered ETB handler (unrelated to dying) must
    still get its independent oracle-derived dies trigger — the
    handler-presence gate must be timing-scoped, not name-scoped."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming

    template = _synthetic_dies_draw_creature()

    # Register an ETB handler under the same name — this is the
    # "unrelated timing" that must NOT suppress the dies trigger.
    @EFFECT_REGISTRY.register(template.name, EffectTiming.ETB,
                              description="test fixture: no-op ETB")
    def _noop_etb(game, card, controller, targets=None, item=None):
        pass

    game = GameState(rng=random.Random(0))
    creature = _put_on_battlefield(game, template, controller=0)
    library_before = len(game.players[0].library)
    hand_before = len(game.players[0].hand)
    for _ in range(3):
        _put_on_battlefield(game, _synthetic_dies_draw_creature(), controller=0)
    game.players[0].library.extend(
        CardInstance(template=template, owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone="library")
        for _ in range(5)
    )

    game._creature_dies(creature)

    assert len(game.players[0].hand) == hand_before + 1, (
        "card has a registered ETB handler (unrelated timing) but its "
        "own independent 'dies, draw a card' oracle clause did not fire — "
        "the registry gate is checking name-presence instead of "
        "DIES-timing-presence"
    )


def test_registered_dies_handler_is_actually_invoked(card_db):
    """A card with a registered EffectTiming.DIES handler must have
    that handler INVOKED when it dies — via EFFECT_REGISTRY.execute,
    the same dispatch already used correctly for ETB
    (card_effects.py:69: `if not registry.execute(..., EffectTiming.ETB,
    ...): <generic fallback>`). Currently EFFECT_REGISTRY.execute is
    never called with EffectTiming.DIES anywhere in the engine, so the
    repo's one real DIES registration (Haywire Mite: 'gain 2 life on
    death') can never fire."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming

    template = _synthetic_dies_draw_creature()
    calls = []

    @EFFECT_REGISTRY.register(template.name, EffectTiming.DIES,
                              description="test fixture: custom dies handler")
    def _custom_dies(game, card, controller, targets=None, item=None):
        calls.append(1)

    game = GameState(rng=random.Random(0))
    creature = _put_on_battlefield(game, template, controller=0)
    hand_before = len(game.players[0].hand)
    game.players[0].library.extend(
        CardInstance(template=template, owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone="library")
        for _ in range(5)
    )

    game._creature_dies(creature)

    assert calls == [1], (
        f"registered DIES handler was invoked {len(calls)} times, expected "
        f"exactly 1 (EFFECT_REGISTRY.execute must be called with "
        f"EffectTiming.DIES from _creature_dies)"
    )
    assert len(game.players[0].hand) == hand_before, (
        "the generic oracle-derived dies trigger ALSO fired even though "
        "this card has its own registered DIES handler — should be "
        "suppressed once the registered handler owns this timing, to "
        "avoid double-firing the same trigger through two mechanisms"
    )
