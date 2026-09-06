"""A non-modal "deals N damage to each creature" sweeper damages every
creature, not the opponent's face.

The generic per-ability damage branch handled targeted damage,
"each opponent", and a face fallback, but had no case for a mass
"deals N to each creature" clause — so Pyroclasm / Anger of the Gods /
Sweltering Suns / Kozilek's Return (all non-modal sweepers that fall
through to this branch) dealt their N to the OPPONENT'S FACE and left
every creature untouched (audit: Broodscale vs Eldrazi Ramp, s58003).

Rule: a symmetric "N damage to each creature" clause deals N to each
creature on both battlefields; it deals nothing to any player. Card
names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.spell_resolution import ResolutionManager
from engine.stack import StackItem, StackItemType


def _bf(game, card_db, name, controller):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    if t.is_creature:
        game.players[controller].creatures.append(c)
    return c


def test_pyroclasm_sweeps_creatures_and_spares_faces(card_db):
    game = GameState(rng=random.Random(0))
    small = _bf(game, card_db, "Memnite", 1)        # 1/1 — dies to 2
    tough = _bf(game, card_db, "Griselbrand", 1)    # 7/7 — survives 2
    mine = _bf(game, card_db, "Ragavan, Nimble Pilferer", 0)  # 2/1 — symmetric

    opp_life_before = game.players[1].life

    spell = CardInstance(template=card_db.get_card("Pyroclasm"), owner=0,
                         controller=0, instance_id=game.next_instance_id(),
                         zone="stack")
    spell._game_state = game
    item = StackItem(item_type=StackItemType.SPELL, source=spell,
                     controller=0, targets=[])
    ResolutionManager._execute_spell_effects(game, item)
    game.check_state_based_actions()

    assert game.players[1].life == opp_life_before, (
        f"a 'deals 2 damage to each creature' sweep must not damage the "
        f"opponent's face (life {game.players[1].life}, was {opp_life_before})"
    )
    opp_creatures = {c.name for c in game.players[1].creatures}
    assert "Memnite" not in opp_creatures, "the 1/1 must die to 2 damage"
    assert "Griselbrand" in opp_creatures, "the 7/7 must survive 2 damage"
    # Symmetric: the caster's own small creature is hit too.
    assert mine not in game.players[0].creatures, (
        "a symmetric each-creature sweep hits the caster's own creatures"
    )
