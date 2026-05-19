"""Damage primitive caller-callee signature compatibility.

PR #433 introduced the new `engine.damage.deal_damage(source, target,
amount, *, is_combat=False)` primitive (W0-D), but several caller
sites in `engine/card_effects.py` and `engine/spell_resolution.py`
still pass the pre-W0-D signature (`deal_damage(game, target, amount,
source_controller=controller)`), which crashes with:

    TypeError: deal_damage() got an unexpected keyword argument
    'source_controller'

This crash takes down every Lightning Bolt / Lava Dart / Unholy Heat /
Galvanic Discharge resolution and silently destabilises any deck with
direct burn. It was found via `python tools/refresh_wr_baseline.py`
crashing on the first burn-deck matchup.

The rule under test
-------------------
Every caller of `engine.damage.deal_damage` must use a signature
that the function accepts. The test reproduces a Lightning Bolt
resolution via the effect registry — the most direct way to exercise
the call site without spinning up a full game.

Class size
----------
The bug surface is the union of {creature damage, player damage,
planeswalker damage} for every burn-style spell in the engine, plus
combat damage routing in `engine/combat_manager.py`. Class size ≫ 10.

Test scope
----------
Resolves Lightning Bolt at a player target and asserts no exception
is raised AND the player's life decremented by 3. A passing test
proves the signature mismatch is gone for the canonical burn path;
spell_resolution.py and other card_effects.py sites use the same
pattern, so a one-site fix that respects the deal_damage signature
fixes them all.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState


def test_lightning_bolt_resolves_without_signature_error_and_decrements_life(
        card_db):
    """Resolving Lightning Bolt at the opposing player must complete
    without TypeError and reduce that player's life by 3."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming

    game = GameState(rng=random.Random(0))
    opp_starting_life = game.players[1].life

    # Stage the Lightning Bolt card on the stack (controller=0).
    tmpl = card_db.get_card("Lightning Bolt")
    assert tmpl is not None, "Lightning Bolt missing from DB"
    bolt = CardInstance(
        template=tmpl,
        owner=0,
        controller=0,
        instance_id=game.next_instance_id(),
        zone="stack",
    )
    bolt._game_state = game

    # No specific target → engine routes face damage at opponent (P1).
    EFFECT_REGISTRY.execute(
        "Lightning Bolt",
        EffectTiming.SPELL_RESOLVE,
        game,
        bolt,
        0,           # controller
        targets=None,
        item=None,
    )

    # Pre-fix, this assertion is never reached: the resolution raises
    # `TypeError: deal_damage() got an unexpected keyword argument
    # 'source_controller'` from engine/card_effects.py:442.
    delta = opp_starting_life - game.players[1].life
    assert delta == 3, (
        f"Lightning Bolt should reduce opp life by 3 (face damage), "
        f"got delta={delta} (opp life now {game.players[1].life})."
    )
