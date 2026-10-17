"""An unblocked double striker deals its power twice (CR 702.4b, 510.4).

A creature with double strike deals combat damage in both the first-strike
and the regular combat damage step (CR 702.4b, 510.4): unblocked, it deals
the player it attacks its power twice. Every read the AI makes of what
unblocked attackers will deal reads it from one primitive
(`ai.clock.unblocked_combat_damage`, summed by `unblocked_reach`):
- the defender's survival test and its race and two-turn checks;
- the coverage pass that picks forced blocks;
- the turn planner's prediction of the opponent's blocks;
- the attacker's own unblocked-lethal test.

The defect: they summed each attacker's power once. Grixis Reanimator, at
7 life with an untapped 6/6 and 5/5, did not block a 4/4 Scion of Draco
that Practiced Offense had given double strike. The defender judged 4
incoming against its 7 life, and its own 11 untapped power against Zoo's
10 life, so it took its "winning state, don't block" exit and died to 8
(Grixis Reanimator vs Domain Zoo s50000 game 3).

Card names are fixture carriers.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

from ai.clock import unblocked_combat_damage, unblocked_reach
from engine.cards import CardInstance, Keyword
from engine.game_state import GameState


def _battlefield(game, card_db, name, controller, double_strike=False):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(),
                        zone="battlefield")
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    if double_strike:
        card.temp_keywords.add(Keyword.DOUBLE_STRIKE)
    game.players[controller].battlefield.append(card)
    return card


# ── The primitive ─────────────────────────────────────────────────────

def test_an_unblocked_double_striker_deals_its_power_twice(card_db):
    game = GameState(rng=random.Random(0))
    bears = _battlefield(game, card_db, "Grizzly Bears", 0)
    assert unblocked_combat_damage(bears) == 2
    bears.temp_keywords.add(Keyword.DOUBLE_STRIKE)    # a granted keyword
    assert unblocked_combat_damage(bears) == 4
    printed = _battlefield(game, card_db, "Boros Swiftblade", 0)
    assert unblocked_combat_damage(printed) == 2 * printed.power
    simulated = SimpleNamespace(power=3, keywords={"double_strike"})
    assert unblocked_combat_damage(simulated) == 6, "the planner's creatures"
    assert unblocked_combat_damage(SimpleNamespace(
        power=-1, keywords={"double_strike"})) == 0
    assert unblocked_reach([bears, printed]) == 4 + 2 * printed.power


# ── The defender ──────────────────────────────────────────────────────

def _lethal_double_striker(card_db):
    """The defender (player 0) at 3 life with 7 untapped power facing a
    5-life opponent, attacked by a 2/2 with double strike: 4 damage."""
    game = GameState(rng=random.Random(0))
    game.players[0].life = 3
    game.players[1].life = 5
    angel = _battlefield(game, card_db, "Serra Angel", 0)
    giant = _battlefield(game, card_db, "Hill Giant", 0)
    attacker = _battlefield(game, card_db, "Grizzly Bears", 1,
                            double_strike=True)
    return game, attacker, (angel, giant)


def test_the_defender_blocks_a_double_striker_whose_damage_is_lethal(
        card_db):
    from ai.ev_player import EVPlayer
    game, attacker, _ = _lethal_double_striker(card_db)
    defender = EVPlayer(player_idx=0, deck_name="Boros Energy",
                        rng=random.Random(0))
    blocks = defender.decide_blockers(game, [attacker])
    assert attacker.instance_id in blocks


def _lethal_unkillable_double_striker(card_db):
    """The defender (player 0) at 6 life with a 3/3, attacked by a 3/10
    with double strike: 6 damage, and no block kills it -- only the lethal
    damage makes the chump worth its 3/3."""
    game = GameState(rng=random.Random(0))
    game.players[0].life = 6
    chump = _battlefield(game, card_db, "Hill Giant", 0)
    attacker = _battlefield(game, card_db, "Grizzly Bears", 1,
                            double_strike=True)
    attacker.temp_power_mod = 1
    attacker.temp_toughness_mod = 8
    return game, attacker, chump


def test_decision_and_prediction_chump_a_lethal_double_striker(card_db):
    """The defender's decision and the attacker's prediction of it read
    the same incoming damage, and both chump."""
    from ai.ev_player import EVPlayer
    from ai.turn_planner import CombatPlanner, extract_virtual_board
    game, attacker, chump = _lethal_unkillable_double_striker(card_db)
    defender = EVPlayer(player_idx=0, deck_name="Boros Energy",
                        rng=random.Random(0))
    decided = defender.decide_blockers(game, [attacker])
    vboard = extract_virtual_board(game, player_idx=1)
    v_attackers = [c for c in vboard.my_creatures
                   if c.instance_id == attacker.instance_id]
    v_blockers = [c for c in vboard.opp_creatures
                  if c.instance_id == chump.instance_id]
    predicted = CombatPlanner()._predict_blocks(v_attackers, v_blockers,
                                                vboard)
    assert decided == predicted == {attacker.instance_id:
                                    [chump.instance_id]}


def test_the_coverage_pass_counts_a_double_striker_twice():
    """At 4 life, a 2/2 double striker and a 1/1 deal 5: the coverage pass
    blocks the double striker."""
    from ai.block_assignment import coverage_pass
    striker = SimpleNamespace(instance_id=1, power=2, toughness=2,
                              keywords={"double_strike"})
    other = SimpleNamespace(instance_id=2, power=1, toughness=1,
                            keywords=set())
    wall = SimpleNamespace(instance_id=3, power=0, toughness=4,
                           keywords=set())
    blocks, _ = coverage_pass([striker, other], [wall], my_life=4,
                              can_block_fn=lambda a, b: True,
                              cost_fn=lambda b: 0.0)
    assert blocks == {1: [3]}


# ── The attacker ──────────────────────────────────────────────────────

def test_the_attacker_sends_only_what_its_unblocked_lethal_needs(card_db):
    """A 2/2 double striker alone deals 4 to a 4-life opponent with no
    blockers: lethal, so a mana creature, worth more at home than the 1
    damage it adds, stays home. Counting the striker's power once, the
    declarer saw 3, no lethal, and sent both."""
    from ai.ev_player import EVPlayer
    game = GameState(rng=random.Random(0))
    game.players[1].life = 4
    striker = _battlefield(game, card_db, "Grizzly Bears", 0,
                           double_strike=True)
    elves = _battlefield(game, card_db, "Llanowar Elves", 0)
    attacker = EVPlayer(player_idx=0, deck_name="Boros Energy",
                        rng=random.Random(0))
    sent = attacker.decide_attackers(game)
    assert striker in sent and elves not in sent
