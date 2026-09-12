"""A permanent with protection from a colour cannot be targeted by a
spell of that colour (CR 702.16b) — at cast time, at the AI's choice of
target, and at resolution.

`engine/target_solver.py` filtered hexproof but never read the typed
`protection_from_colors` field; only combat (`combat_manager._can_block`)
did. A red burn spell was cast at, aimed at, and resolved against a
pro-red creature (Prowess vs WST s50000: Bolt killed Sanctifier en-Vec).

Rule: one predicate (`target_solver.can_be_targeted`) answers "may this
source target this permanent" for hexproof and protection alike, and the
cast-time legality check, the AI's candidate enumeration, and the
resolution-time re-check all read it. Class: 75 pool cards with
protection from a colour. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


def test_the_predicate_reads_protection_and_hexproof_alike(card_db):
    from engine.target_solver import can_be_targeted
    game = _game()
    pro_red = _add(game, card_db, "Sanctifier en-Vec", 1, "battlefield")   # pro black & red
    bolt = _add(game, card_db, "Lightning Bolt", 0, "hand")                # red
    path = _add(game, card_db, "Path to Exile", 0, "hand")                 # white
    assert not can_be_targeted(pro_red, bolt, 0), "a red spell cannot target a pro-red creature"
    assert can_be_targeted(pro_red, path, 0), "a white spell can"


def test_a_spell_with_only_protected_targets_is_not_castable(card_db):
    game = _game()
    _add(game, card_db, "Mountain", 0, "battlefield")
    pro_red = _add(game, card_db, "Sanctifier en-Vec", 1, "battlefield")
    # A creature-only red removal: no legal target means no cast (CR 601.2c).
    kill = _add(game, card_db, "Flame Slash", 0, "hand")   # "target creature"
    assert not CastManager.can_cast(game, 0, kill), (
        "the only creature has protection from red — no legal target")
    _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    assert CastManager.can_cast(game, 0, kill)


def test_the_ai_never_aims_a_spell_at_a_permanent_protected_from_it(card_db):
    from ai.ev_player import EVPlayer
    game = _game()
    _add(game, card_db, "Mountain", 0, "battlefield")
    pro_red = _add(game, card_db, "Sanctifier en-Vec", 1, "battlefield")   # 2/2, the bigger threat
    bear = _add(game, card_db, "Memnite", 1, "battlefield")                # 1/1
    bolt = _add(game, card_db, "Lightning Bolt", 0, "hand")
    ai = EVPlayer(player_idx=0, deck_name="Izzet Prowess", rng=random.Random(0))
    chosen = ai._choose_targets(game, bolt)
    assert pro_red.instance_id not in chosen, "aimed a red spell at a pro-red creature"
    assert chosen in ([bear.instance_id], [-1])


def test_a_target_that_gains_protection_before_resolution_is_illegal(card_db):
    from engine.spell_resolution import ResolutionManager
    game = _game()
    _add(game, card_db, "Mountain", 0, "battlefield")
    bear = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    bolt = _add(game, card_db, "Lightning Bolt", 0, "hand")
    assert CastManager.cast_spell(game, 0, bolt, [bear.instance_id])
    item = game.stack.top
    assert not ResolutionManager._spell_fizzles(game, item)
    # The target acquires protection from red while the spell is on the stack.
    bear.template.protection_from_colors = bolt.template.colors
    try:
        assert ResolutionManager._spell_fizzles(game, item), (
            "a target protected from the spell's colour is illegal at resolution (CR 608.2b)")
    finally:
        bear.template.protection_from_colors = frozenset()
