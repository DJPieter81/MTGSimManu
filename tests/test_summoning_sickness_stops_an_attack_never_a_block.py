"""Summoning sickness stops a creature attacking until its controller's next
turn begins; it never stops a block (CR 302.6, 509.1a).

A creature its controller has not controlled continuously since their most
recent turn began can't attack and can't use {T} abilities (CR 302.6). Haste
lifts that (CR 702.10b). Nothing in the rule touches blocking: a creature
cast on its controller's turn blocks on the next player's turn.

The defect: the attack planner's board (`extract_virtual_board`) marked a
creature unavailable when it was tapped or summoning sick, for both sides.
The planning player's creatures are its attackers, so a haste creature that
just entered was left out of every attack; the opponent's creatures are its
blockers, so a creature the opponent cast on their last turn was no blocker
at all, and the AI attacked into it. The defender's next-turn reach
(`_two_turn_lethal`) also left out the attacker's creatures that entered
this turn, though they attack on their controller's next turn.

Class: every creature, in every game where one entered since its
controller's turn began. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, *, sick=False, tapped=False):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(),
                        zone="battlefield")
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = sick
    card.tapped = tapped
    game.players[controller].battlefield.append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    return game


def _board(game, player_idx=0):
    from ai.turn_planner import extract_virtual_board
    return extract_virtual_board(game, player_idx)


# ── The planner's board ────────────────────────────────────────────────

def test_a_creature_that_entered_since_its_controllers_turn_is_a_blocker(
        card_db):
    game = _game()
    _add(game, card_db, "Grizzly Bears", 0)
    angel = _add(game, card_db, "Serra Angel", 1, sick=True)
    assert angel.can_block
    (blocker,) = _board(game).opp_creatures
    assert blocker.is_tapped is False


def test_a_haste_creature_that_entered_this_turn_is_an_attacker(card_db):
    game = _game()
    swift = _add(game, card_db, "Monastery Swiftspear", 0, sick=True)
    assert swift.can_attack
    (attacker,) = _board(game).my_creatures
    assert attacker.is_tapped is False


def test_a_creature_without_haste_that_entered_this_turn_is_no_attacker(
        card_db):
    game = _game()
    _add(game, card_db, "Grizzly Bears", 0, sick=True)
    (attacker,) = _board(game).my_creatures
    assert attacker.is_tapped is True


def test_a_tapped_creature_is_neither_attacker_nor_blocker(card_db):
    game = _game()
    _add(game, card_db, "Grizzly Bears", 0, tapped=True)
    _add(game, card_db, "Serra Angel", 1, tapped=True)
    board = _board(game)
    assert [c.is_tapped for c in board.my_creatures] == [True]
    assert [c.is_tapped for c in board.opp_creatures] == [True]


# ── The decisions ──────────────────────────────────────────────────────

def _attack_with(card_db, *, blocker_sick):
    from ai.ev_player import EVPlayer
    game = _game()
    _add(game, card_db, "Grizzly Bears", 0)
    _add(game, card_db, "Serra Angel", 1, sick=blocker_sick)
    attack = EVPlayer(0, "Boros Energy").decide_attackers(game)
    return sorted(c.name for c in attack)


def test_a_blockers_summoning_sickness_does_not_change_the_attack(card_db):
    """The attack into an untapped blocker is the one made had the blocker
    been there since its controller's turn began."""
    assert _attack_with(card_db, blocker_sick=True) == \
        _attack_with(card_db, blocker_sick=False)


def test_the_next_turn_reach_counts_a_creature_that_entered_this_turn(
        card_db):
    """While blocking, the attacker's creatures that entered this turn
    attack on its next turn: they are in its next-turn reach."""
    from ai.ev_player import EVPlayer
    game = _game()
    game.active_player = game.priority_player = 1
    me, opp = game.players
    me.life = 5
    bears = _add(game, card_db, "Grizzly Bears", 1)
    _add(game, card_db, "Serra Angel", 1, sick=True)
    player = EVPlayer(0, "Boros Energy")
    # 2 now and the Angel's 4 next turn reach 5 life.
    assert player._two_turn_lethal(game, me, opp, [bears])
