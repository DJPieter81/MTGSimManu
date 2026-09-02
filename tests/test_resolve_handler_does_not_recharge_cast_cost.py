"""A spell-resolution handler must not re-apply the spell's cast cost —
in particular a Phyrexian-mana life payment, which the cast path already
charges.

Rule: {C/P} means "pay 2 life instead of one mana of that colour" as a
CAST cost, deducted once by the casting machinery (cast_manager). A
SPELL_RESOLVE handler encodes only on-resolution effects; if it also
subtracts the Phyrexian life, the controller pays twice — or pays 2 life
even when the pip was paid with real mana (audit: 4/5c Control vs Izzet
Prowess, s55642 — Mutagenic Growth resolve handler ran `life -= 2`,
dropping the caster 20->18 even paid with a Forest).

Card names are fixture carriers; the mechanic is "resolution handlers
never re-charge a cast cost." Phyrexian mana is a real class (28 Modern
{C/P} cards); Mutagenic Growth is the one whose resolve handler had the
duplicate.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState


def test_pump_resolve_handler_does_not_deduct_phyrexian_life(card_db):
    game = GameState(rng=random.Random(0))
    t = card_db.get_card("Mutagenic Growth")
    if t is None:
        import pytest
        pytest.skip("no {G/P} pump fixture in this DB")

    # A creature to receive the +2/+2.
    bear_t = card_db.get_card("Grizzly Bears")
    bear = CardInstance(template=bear_t, owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="battlefield")
    bear._game_state = game
    bear.enter_battlefield()
    game.players[0].battlefield.append(bear)
    game.players[0].creatures  # property
    base_p, base_t = bear.power, bear.toughness

    spell = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="stack")
    spell._game_state = game
    life_before = game.players[0].life

    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    EFFECT_REGISTRY.execute("Mutagenic Growth", EffectTiming.SPELL_RESOLVE,
                            game, spell, 0, targets=None, item=None)

    assert game.players[0].life == life_before, (
        "the resolve handler must NOT deduct the Phyrexian-mana life — that "
        f"is the cast path's cost (life {game.players[0].life}, expected "
        f"{life_before})")
    # The actual effect (+2/+2) still applies.
    assert bear.power == base_p + 2 and bear.toughness == base_t + 2, (
        "the +2/+2 pump must still be applied by the resolve handler")
