"""ETB "reveal top N, put one card of each type into hand" (Atraxa shape).

Mechanic (CR 121 selective draw): "When this creature enters, reveal the top
N cards of your library. For each card type, you may put a card of that type
from among the revealed cards into your hand. Put the rest on the bottom of
your library in a random order."

Before: `resolve_etb_from_oracle` had no branch for this shape, so the ETB was
a silent no-op — the card entered as a vanilla body with zero card advantage.
This is the entire payoff of the reanimator archetype (Atraxa is the shared
4-of target of Goryo's Vengeance and Instant Reanimator).

Rule under test: the ETB moves at least one card of each distinct card type
present among the revealed top-N into hand, and leaves the library the correct
size (N revealed − taken go to the bottom). Class: selective reveal-to-hand
by card type. Oracle-driven, no card names in the resolver.
"""
from __future__ import annotations

import random

from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.cards import CardInstance


def _mk(game, db, name, owner, zone):
    t = db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=owner, controller=owner,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    return c


def test_atraxa_etb_reveals_and_takes_one_of_each_type(card_db):
    game = GameState(rng=random.Random(1))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    p = game.players[0]

    # Stack a known top-10: a creature, a land, an instant, a sorcery,
    # an artifact + filler creatures. At least 4 distinct card types.
    top = ["Devourer of Destiny",   # creature
           "Forest",                # land
           "Lightning Bolt",        # instant
           "Malevolent Rumble",     # sorcery
           "Talisman of Impulse",   # artifact
           "Forest", "Forest", "Forest", "Forest", "Forest"]
    for name in top:
        p.library.append(_mk(game, card_db, name, 0, "library"))
    lib_before = len(p.library)
    hand_before = len(p.hand)

    atraxa = _mk(game, card_db, "Atraxa, Grand Unifier", 0, "battlefield")

    from engine.oracle_resolver import resolve_etb_from_oracle
    handled = resolve_etb_from_oracle(game, atraxa, 0)

    assert handled is True, "Atraxa ETB must be handled by the resolver"
    drawn = len(p.hand) - hand_before
    assert drawn >= 4, (
        f"Atraxa should put one card of each distinct type (>=4: creature, "
        f"land, instant, sorcery, artifact) into hand; got {drawn}")
    # Taken cards left the library; the rest went to the bottom. No card
    # is lost or duplicated.
    assert len(p.library) == lib_before - drawn, (
        f"library should shrink by exactly the number taken ({drawn}); "
        f"got {lib_before} -> {len(p.library)}")
    hand_names = {c.name for c in p.hand}
    assert "Lightning Bolt" in hand_names or "Malevolent Rumble" in hand_names, (
        "a noncreature spell should be among the cards taken")


def test_atraxa_etb_empty_library_is_safe(card_db):
    """No cards to reveal → ETB is a handled no-op (does not crash)."""
    game = GameState(rng=random.Random(2))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    p = game.players[0]
    atraxa = _mk(game, card_db, "Atraxa, Grand Unifier", 0, "battlefield")
    from engine.oracle_resolver import resolve_etb_from_oracle
    handled = resolve_etb_from_oracle(game, atraxa, 0)
    assert handled is True
    assert len(p.hand) == 0
