"""Amass grows the Army you already control with +1/+1 counters instead
of minting a fresh token each time.

Rule (CR 701.44a): "amass Orcs N" puts N +1/+1 counters on an Army you
control — creating a new 0/0 Orc Army token only if you control none.
The engine spawned a brand-new 1/1 token on every amass, so a repeated
amass source left three separate 1/1 bodies instead of one Army growing
1/1 → 2/2 → 3/3 (audit: Dimir vs Azorius, s55630 — three Orcish
Bowmasters ETBs made three 1/1 Orc Armies).

Card names are fixture carriers; the mechanic is amass's grow-the-Army
rule, shared by the amass keyword cycle.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState


def _bowmasters_etb(game, card_db):
    """Resolve an Orcish Bowmasters ETB (its 'amass Orcs 1' rider) for
    player 0."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    t = card_db.get_card("Orcish Bowmasters")
    assert t is not None
    src = CardInstance(template=t, owner=0, controller=0,
                       instance_id=game.next_instance_id(), zone="battlefield")
    src._game_state = game
    game.players[0].battlefield.append(src)
    EFFECT_REGISTRY.execute("Orcish Bowmasters", EffectTiming.ETB,
                            game, src, 0, targets=None, item=None)


def _armies(game, controller=0):
    return [c for c in game.players[controller].creatures
            if "Army" in (c.template.subtypes or [])]


def test_second_amass_grows_the_army_not_a_new_token(card_db):
    game = GameState(rng=random.Random(0))

    _bowmasters_etb(game, card_db)
    armies = _armies(game)
    assert len(armies) == 1, f"first amass makes one Army (got {len(armies)})"
    assert armies[0].power == 1

    _bowmasters_etb(game, card_db)
    armies = _armies(game)
    assert len(armies) == 1, (
        f"second amass must GROW the existing Army, not mint a second token "
        f"(got {len(armies)} Armies)")
    assert armies[0].power == 2, (
        f"the Army grows to 2/2 via a +1/+1 counter (power {armies[0].power})")


def test_three_amass_yields_one_three_three_army(card_db):
    game = GameState(rng=random.Random(0))
    for _ in range(3):
        _bowmasters_etb(game, card_db)
    armies = _armies(game)
    assert len(armies) == 1 and armies[0].power == 3 and armies[0].toughness == 3, (
        f"three amass → one 3/3 Army (got {len(armies)} armies, "
        f"{armies[0].power if armies else '-'}/"
        f"{armies[0].toughness if armies else '-'})")
