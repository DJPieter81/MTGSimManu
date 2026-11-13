"""A storm finisher fires when the finisher casts the mana pays sum to
lethal; it is held only while a castable play keeps the mana it needs (AI,
from CR 702.40a, 106.4, 500.4).

- A storm spell is copied once for each spell cast before it this turn (CR
  702.40a): a damage closer cast with S spells before it deals S + 1, and a
  second closer cast after it S + 2. A tutor that brings a closer adds one
  spell before it. So a turn's finishers sum their damage: at 20 life two
  closers need 9 spells before them, one closer 19.
- Mana empties between steps (CR 106.4, 500.4): a play that spends the
  mana a held finisher needs strands it for the turn. A finisher is held
  only while some play keeps that mana. A finisher the mana does not pay
  now is stranded by no play: every play is fuel for it as before.
- A tutor whose closer the mana cannot follow deals nothing this turn: it
  is not cashed but held for a turn whose mana pays both (a card played
  from outside the game is played this turn or never, CR 400.11a).

The owner is `ai/finisher_reach.py` (the finisher casts, the damage a
sequence of them reaches within the mana, and whether a play keeps a
finisher's mana), read by the storm and tutor branches of
`ai/combo_calc.card_combo_modifier`.

The defect (docs/diagnostics/2026-10-15_ruby_storm_goldfish_speed.md):
lethal was one finisher (storm + 1, storm + 2) and the hold counted fuel by
its kind. In the goldfish 57% of the turns that cast ten spells cast no
closer, and a lethal sum of castable finisher casts went untaken in 61 of
160 games -- draw seed 70048 T7 the clearest: at storm 8 with 3 mana and two
Ruby Medallions, Wish, the sideboard Grapeshot and a Grapeshot in hand deal
21 into 20; the AI cast a third Ruby Medallion with its last mana and
passed.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


# ── The reach (pure arithmetic of CR 702.40a) ─────────────────────────

def _casts(*specs):
    from ai.finisher_reach import FinisherCast
    return [FinisherCast(object(), cost, before, damage=1)
            for cost, before in specs]


def test_each_closer_deals_the_spells_cast_before_it_plus_one():
    from ai.finisher_reach import reach
    two = _casts((1, 0), (1, 0))
    dmg, _ = reach(two, storm=8, mana=2)
    assert dmg == 9 + 10, "8 before the first, 9 before the second"


def test_each_instance_deals_the_closers_own_damage():
    from ai.finisher_reach import FinisherCast, reach
    two_each = FinisherCast(object(), 1, 0, damage=2)
    assert reach([two_each], storm=8, mana=1)[0] == 9 * 2


def test_a_closer_is_a_storm_spell_dealing_a_printed_amount_to_a_player(
        card_db):
    """Read off the typed host: one DAMAGE instruction with a literal
    amount, aimed at a slot that admits a player. A variable amount or a
    spell that deals no damage is not counted."""
    from ai.finisher_reach import per_copy_damage
    game = _storm_turn(card_db, mountains=1)
    shot = _add(game, card_db, "Grapeshot", 0, "hand")
    memories = _add(game, card_db, "Ignite Memories", 0, "hand")
    tokens = _add(game, card_db, "Empty the Warrens", 0, "hand")
    bolt = _add(game, card_db, "Lightning Bolt", 0, "hand")
    assert per_copy_damage(game, 0, shot) == 1
    assert per_copy_damage(game, 0, memories) is None, "a revealed card's MV"
    assert per_copy_damage(game, 0, tokens) is None
    assert per_copy_damage(game, 0, bolt) is None, "no storm: no copies"


def test_a_tutor_cast_first_adds_its_spell_before_its_closer():
    from ai.finisher_reach import reach
    shot, wish = _casts((1, 0), (2, 1))
    dmg, first = reach([shot, wish], storm=8, mana=3)
    assert dmg == 10 + 11, "the tutor's spell, then its closer, then the other"
    assert first is wish.card


def test_the_reach_is_bounded_by_the_mana():
    from ai.finisher_reach import reach
    dmg, _ = reach(_casts((1, 0), (2, 1)), storm=8, mana=1)
    assert dmg == 9
    assert reach(_casts((2, 0)), storm=8, mana=1) == (0, None)


# ── A play keeps a finisher's mana ────────────────────────────────────

def _storm_turn(card_db, mountains, medallions=0):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for p in (0, 1):
        for _ in range(5):
            _add(game, card_db, "Island", p, "library")
    for _ in range(medallions):
        _add(game, card_db, "Ruby Medallion", 0)
    for _ in range(mountains):
        _add(game, card_db, "Mountain", 0)
    game.continuous_effects.recalculate(game)
    return game


def test_a_ritual_keeps_the_finishers_mana_and_a_costly_spell_does_not(
        card_db):
    from ai.finisher_reach import keeps_finisher
    game = _storm_turn(card_db, mountains=4)
    ritual = _add(game, card_db, "Pyretic Ritual", 0, "hand")   # {1}{R}: RRR
    flames = _add(game, card_db, "Past in Flames", 0, "hand")   # {3}{R}
    assert keeps_finisher(game, 0, ritual, finisher_cost=2, mana=2)
    assert not keeps_finisher(game, 0, flames, finisher_cost=2, mana=4)
    assert keeps_finisher(game, 0, flames, finisher_cost=2, mana=6)


def test_only_a_tutor_adds_the_closer_it_brings_to_a_finishers_cost(card_db):
    from ai.finisher_reach import finisher_cost
    game = _storm_turn(card_db, mountains=4)
    _add(game, card_db, "Grapeshot", 0, "sideboard")
    tokens = _add(game, card_db, "Empty the Warrens", 0, "hand")  # {3}{R}
    wish = _add(game, card_db, "Wish", 0, "hand")                 # {2}{R}
    assert finisher_cost(game, 0, tokens) == 4, "a payoff, not a tutor"
    assert finisher_cost(game, 0, wish) == 3 + 2


def test_a_card_sharing_the_finishers_one_play_is_no_fuel(card_db):
    """Wish resolved: one play from the sideboard. Past in Flames there is
    no fuel for the Grapeshot there -- playing it spends the play."""
    from ai.finisher_reach import keeps_finisher
    game = _storm_turn(card_db, mountains=9)
    shot = _add(game, card_db, "Grapeshot", 0, "sideboard")
    flames = _add(game, card_db, "Past in Flames", 0, "sideboard")
    ritual = _add(game, card_db, "Pyretic Ritual", 0, "hand")
    wish = _add(game, card_db, "Wish", 0, "hand")
    assert game.cast_spell(0, wish, [])
    while not game.stack.is_empty:
        game.resolve_stack()
    assert not keeps_finisher(game, 0, flames, finisher_cost=2, mana=6,
                              finisher=shot)
    assert keeps_finisher(game, 0, ritual, finisher_cost=2, mana=6,
                          finisher=shot), "a hand card spends no play"


def test_the_reach_never_casts_two_cards_sharing_one_play():
    from ai.finisher_reach import reach
    a, b = _casts((1, 0), (1, 0))
    apart = lambda x, y: {x, y} == {a.card, b.card}
    assert reach([a, b], storm=8, mana=2, apart=apart)[0] == 9, (
        "one closer of the two: 8 before it")


def test_a_play_is_fuel_while_the_mana_does_not_pay_the_finisher(card_db):
    from ai.finisher_reach import keeps_finisher
    game = _storm_turn(card_db, mountains=3)
    impulse = _add(game, card_db, "Reckless Impulse", 0, "hand")  # {1}{R}
    assert keeps_finisher(game, 0, impulse, finisher_cost=5, mana=3), (
        "the finisher's whole cast costs 5 of the 3 there are: no play "
        "strands it")


# ── The decision, end to end (the goldfish's draw 70048 T7) ───────────

def _seventy_forty_eight(card_db):
    """Storm 8, three Mountains untapped, two Ruby Medallions; Wish, a
    Grapeshot and a third Ruby Medallion in hand; a Grapeshot in the
    sideboard; the opponent at 20 with nothing."""
    game = _storm_turn(card_db, mountains=3, medallions=2)
    me = game.players[0]
    me.spells_cast_this_turn = 8
    game._global_storm_count = 8
    for name in ("Wish", "Grapeshot", "Ruby Medallion"):
        _add(game, card_db, name, 0, "hand")
    _add(game, card_db, "Grapeshot", 0, "sideboard")
    _add(game, card_db, "Empty the Warrens", 0, "sideboard")
    return game


def test_the_reach_of_the_board_is_lethal(card_db):
    from ai.finisher_reach import reach_now
    game = _seventy_forty_eight(card_db)
    dmg, first = reach_now(game, 0, mana=3)
    assert dmg >= game.players[1].life
    assert first.name == "Wish", "the tutor's spell goes first"


def _main_phase(game):
    from ai.ev_player import EVPlayer
    from engine.game_runner import AICallbacks, GameRunner
    game.callbacks = AICallbacks()
    ai0 = EVPlayer(player_idx=0, deck_name="Ruby Storm", rng=random.Random(0))
    ai1 = EVPlayer(player_idx=1, deck_name="Dimir Midrange",
                   rng=random.Random(0))
    game.callbacks.bind_players([ai0, ai1])
    runner = GameRunner.__new__(GameRunner)
    runner._execute_main_phase(game, ai0, ai1)


def test_the_turn_ends_on_its_finishers_not_on_a_stranding_play(card_db):
    game = _seventy_forty_eight(card_db)
    _main_phase(game)
    assert game.players[1].life <= 0, (
        "Wish, the sideboard Grapeshot and the Grapeshot in hand deal 21 "
        "into 20; " + " / ".join(l for l in game.log[-12:]))


# ── A tutor whose closer the mana cannot follow ───────────────────────

def _wish_turn(card_db, storm, hand, library):
    """Three Mountains untapped; Wish (2R) brings a Grapeshot (1R): the
    whole cast costs 5 of the 3 there are."""
    game = _storm_turn(card_db, mountains=3)
    for name in library:
        _add(game, card_db, name, 0, "library")
    me = game.players[0]
    me.spells_cast_this_turn = storm
    game._global_storm_count = storm
    for name in hand:
        _add(game, card_db, name, 0, "hand")
    _add(game, card_db, "Grapeshot", 0, "sideboard")
    return game


@pytest.mark.parametrize("library", ["Island", "Pyretic Ritual"])
def test_a_tutor_whose_closer_the_mana_cannot_follow_is_held(card_db,
                                                             library):
    """Mid-chain (storm 4) with Wish the only card: cast now, Wish's play
    from outside the game ends with the turn unplayed."""
    game = _wish_turn(card_db, storm=4, hand=["Wish"],
                      library=[library] * 5)
    _main_phase(game)
    assert [c.name for c in game.players[0].hand] == ["Wish"], " / ".join(
        game.log[-8:])


def test_a_dig_is_fuel_for_a_tutor_the_mana_does_not_pay(card_db):
    """The digest's Azorius Control vs Ruby Storm s58500 g3 T5: storm 0,
    three lands, Wish and Reckless Impulse in hand."""
    game = _wish_turn(card_db, storm=0, hand=["Wish", "Reckless Impulse"],
                      library=["Island"] * 5)
    _main_phase(game)
    hand = [c.name for c in game.players[0].hand]
    assert "Wish" in hand and "Reckless Impulse" not in hand, " / ".join(
        game.log[-8:])
