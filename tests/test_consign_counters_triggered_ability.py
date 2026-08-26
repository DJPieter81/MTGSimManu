"""Consign to Memory counters a triggered ability (any color), not only
colorless spells.

Oracle: "Counter target triggered ability or colorless spell." The handler
only popped colorless SPELLS and explicitly skipped triggered abilities, so
vs the colored decks that dominate the meta it countered nothing and could
never answer an ETB / cascade / storm / evoke trigger — roughly half its
function.

Rule under test: a triggered ability on the stack is counterable regardless
of its source's color; a colored SPELL remains uncounterable (colorless-only
for spells).
"""
from __future__ import annotations

import random

from engine.game_state import GameState, Phase, StackItem, StackItemType
from engine.card_database import CardDatabase
from engine.cards import CardInstance


def _mk(game, db, name, owner, zone):
    t = db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=owner, controller=owner,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    getattr(game.players[owner],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game(card_db):
    g = GameState(rng=random.Random(0))
    g.active_player = 1
    g.current_phase = Phase.MAIN1
    return g


def test_consign_counters_colored_triggered_ability(card_db):
    g = _game(card_db)
    # A COLORED permanent whose triggered ability is on the stack.
    src = _mk(g, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")
    assert src.template.color_identity, "fixture must be colored"
    g.stack.items.append(StackItem(
        item_type=StackItemType.TRIGGERED_ABILITY,
        source=src, controller=1, description="Ragavan attack trigger"))
    n_before = len(g.stack.items)

    from engine.card_effects import consign_to_memory_resolve
    consign = _mk(g, card_db, "Consign to Memory", 0, "hand")
    consign_to_memory_resolve(g, consign, 0, targets=[src.instance_id])

    assert len(g.stack.items) == n_before - 1, (
        "a triggered ability must be counterable regardless of source color")


def test_consign_does_not_counter_colored_spell(card_db):
    g = _game(card_db)
    spell = _mk(g, card_db, "Lightning Bolt", 1, "hand")  # red spell
    assert spell.template.color_identity
    g.stack.items.append(StackItem(
        item_type=StackItemType.SPELL,
        source=spell, controller=1, description="Bolt"))
    n_before = len(g.stack.items)

    from engine.card_effects import consign_to_memory_resolve
    consign = _mk(g, card_db, "Consign to Memory", 0, "hand")
    consign_to_memory_resolve(g, consign, 0, targets=[spell.instance_id])

    assert len(g.stack.items) == n_before, (
        "a colored spell must NOT be countered (colorless-only for spells)")
