"""A tutor that reaches a payoff closes the chain as that payoff.

The chain planner (`ai.combo_chain.find_all_chains`) projects what a turn's
spells can do: their mana, their storm count, and the payoff the chain ends
on. A tutor whose pool holds a storm payoff (`combo_calc._tutor_pool`, the
same reach `_tutor_has_payoff_access` reads) is a route to that payoff: cast
the tutor, then the payoff it finds. Both are paid for and both are spells
cast, so the payoff's storm count includes the tutor (CR 702.40a).

The planner classified no tutor, so a chain ending "tutor, then payoff"
never existed for it. A Wish → Grapeshot kill was invisible to every gate
that asks whether the turn's chain can kill (Ruby Storm vs Dimir Midrange
s50000, game 1, Storm's turn 4; the Storm diagnostic's fourth root cause).
Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, zone):
    c = CardInstance(template=card_db.get_card(name), owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
    getattr(game.players[0], zone).append(c)
    return c


def _chains(card_db, hand, mana, sideboard=("Grapeshot",), library=(),
            base_storm=0):
    from ai.combo_chain import find_all_chains
    g = GameState(rng=random.Random(0))
    g.active_player = 0
    g.current_phase = Phase.MAIN1
    cards = [_add(g, card_db, n, "hand") for n in hand]
    for n in sideboard:
        _add(g, card_db, n, "sideboard")
    for n in library:
        _add(g, card_db, n, "library")
    return find_all_chains(cards, mana, {"Grapeshot"}, base_storm,
                           game=g, player_idx=0)


def _best(chains):
    return max(chains, key=lambda c: c.storm_damage, default=None)


def test_a_tutor_and_the_payoff_it_finds_end_the_chain(card_db):
    """Desperate Ritual (2 -> 3), Wish (3), Grapeshot (2): four mana."""
    best = _best(_chains(card_db, ["Desperate Ritual", "Wish"], mana=4))
    assert best is not None and best.payoff_name == "Grapeshot"
    assert best.sequence[-2:] == ["Wish", "Grapeshot"]
    assert best.storm_damage == 3


def test_the_payoff_counts_every_spell_before_it_including_the_tutor(
        card_db):
    best = _best(_chains(card_db, ["Desperate Ritual", "Wish"], mana=4,
                         base_storm=4))
    assert best.storm_damage == 7


def test_a_tutor_the_mana_cannot_follow_with_its_payoff_closes_nothing(
        card_db):
    """Three mana: the ritual and Wish, but not Grapeshot after."""
    chains = _chains(card_db, ["Desperate Ritual", "Wish"], mana=3)
    assert all(c.payoff_name is None for c in chains)


def test_a_tutor_whose_pool_holds_no_payoff_is_no_payoff(card_db):
    chains = _chains(card_db, ["Desperate Ritual", "Wish"], mana=6,
                     sideboard=("Fatal Push",))
    assert all(c.payoff_name is None for c in chains)
