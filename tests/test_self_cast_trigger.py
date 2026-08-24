"""On-cast self-triggers: "When you cast this spell, <effect>".

The spell's OWN cast trigger had no dispatch anywhere — `cast_manager` only
called `resolve_spell_cast_trigger` (watcher triggers on OTHER permanents).
So a spell's "When you cast this spell, ..." clause was a silent no-op. This
blanked the primary ramp engine of Eldrazi decks (Sowing Mycospawn's land
search) and the Eldrazi interaction suite (Devourer of Destiny / Ugin exile a
colored permanent on cast).

Rules under test (CR 601.2i cast triggers):
  - "When you cast this spell, search your library for a land card, put it
    onto the battlefield" ramps a land from library to battlefield.
  - "When you cast this spell, exile [up to one] target permanent that's one
    or more colors" exiles an opponent's colored permanent.
Oracle-shape-gated; no card names in the resolver.
"""
from __future__ import annotations

import random

from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardType


def _mk(game, db, name, owner, zone):
    t = db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=owner, controller=owner,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
    getattr(game.players[owner],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game():
    g = GameState(rng=random.Random(0))
    g.active_player = 0
    g.current_phase = Phase.MAIN1
    g.turn_number = 4
    return g


def test_on_cast_land_search_ramps_a_land(card_db):
    g = _game()
    p = g.players[0]
    for name in ["Eldrazi Temple", "Forest", "Forest"]:
        _mk(g, card_db, name, 0, "library")
    lands_before = sum(1 for c in p.battlefield
                       if CardType.LAND in c.template.card_types)
    sowing = _mk(g, card_db, "Sowing Mycospawn", 0, "hand")

    from engine.oracle_resolver import resolve_self_cast_trigger
    fired = resolve_self_cast_trigger(g, 0, sowing)
    assert fired is True
    lands_after = sum(1 for c in p.battlefield
                      if CardType.LAND in c.template.card_types)
    assert lands_after == lands_before + 1, (
        "Sowing Mycospawn's on-cast trigger must put a land onto the "
        "battlefield")


def test_on_cast_exile_targets_opponent_colored_permanent(card_db):
    g = _game()
    opp = g.players[1]
    # Opponent has a colored creature and a colorless one; only the colored is
    # a legal target.
    colored = _mk(g, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")
    _mk(g, card_db, "Ornithopter", 1, "battlefield")  # colorless, not a target
    devourer = _mk(g, card_db, "Devourer of Destiny", 0, "hand")

    from engine.oracle_resolver import resolve_self_cast_trigger
    fired = resolve_self_cast_trigger(g, 0, devourer)
    assert fired is True
    assert colored not in opp.battlefield, (
        "Devourer's on-cast trigger must exile an opponent's colored "
        "permanent")


def test_no_self_cast_trigger_is_noop(card_db):
    """A card with no 'when you cast this spell' clause returns False."""
    g = _game()
    bolt = _mk(g, card_db, "Lightning Bolt", 0, "hand")
    from engine.oracle_resolver import resolve_self_cast_trigger
    assert resolve_self_cast_trigger(g, 0, bolt) is False
