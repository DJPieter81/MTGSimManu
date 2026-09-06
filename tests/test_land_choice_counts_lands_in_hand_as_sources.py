"""A land decision counts the lands still in hand as sources the player
will have, so a search fills the colour no held land provides.

# Mechanic the tests name

`analyze_mana_needs` measured colour deficits against the BATTLEFIELD
only.  On turn 1 with an empty board every colour the hand needs is
"missing", so a fetchland's choice among duals is a tie broken by
redundancy weights — and it fetched a W/B dual while the hand held a
W/B land, leaving the deck's only WU outlet uncastable until a blue
source was drawn three turns later (Pinnacle Affinity vs Goryo's
Vengeance s50000, game 1: Faithful Mending stranded turns 2–3, dead on
turn 4).  A non-fetch land in hand is a committed source: its colours
are covered by the coming land drops, and the search's marginal value
is in the colours no held land produces.

A fetchland in hand commits to nothing until it is cracked, so it
covers no colour.

Class: every fetch, land-search and shock-colour decision in every
multicolour deck.  Card names below are fixture carriers only.
"""
from __future__ import annotations

import random

from ai.mana_planner import analyze_mana_needs, choose_fetch_target
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _put(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = False
        game.players[controller].battlefield.append(c)
    elif zone == "library":
        game.players[controller].library.append(c)
    else:
        game.players[controller].hand.append(c)
    return c


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.turn_number = 1
    return game


def _library_of_duals(game, card_db):
    return {
        "shrine": _put(game, card_db, "Godless Shrine", 0, "library"),    # W/B
        "grave": _put(game, card_db, "Watery Grave", 0, "library"),       # U/B
        "fountain": _put(game, card_db, "Hallowed Fountain", 0, "library"),  # W/U
    }


def test_a_held_land_is_a_pending_source_not_a_deficit(card_db):
    game = _game()
    _put(game, card_db, "Concealed Courtyard", 0, "hand")       # W/B, will be played
    _put(game, card_db, "Faithful Mending", 0, "hand")          # WU
    _put(game, card_db, "Goryo's Vengeance", 0, "hand")         # 1B
    needs = analyze_mana_needs(game, 0, {})
    assert needs.missing_colors == {"U"}
    # The battlefield still provides nothing THIS turn.
    assert needs.existing_colors == set()


def test_the_fetch_fills_the_colour_no_held_land_provides(card_db):
    game = _game()
    _put(game, card_db, "Concealed Courtyard", 0, "hand")
    _put(game, card_db, "Faithful Mending", 0, "hand")
    _put(game, card_db, "Goryo's Vengeance", 0, "hand")
    lib = _library_of_duals(game, card_db)
    needs = analyze_mana_needs(game, 0, {})
    pick = choose_fetch_target(game.players[0].library, ["W", "U"], needs,
                               turn=game.turn_number)
    assert pick is not lib["shrine"], "fetched the colours the held land already covers"
    assert "U" in pick.template.produces_mana


def test_a_fetchland_in_hand_covers_nothing_until_it_is_cracked(card_db):
    game = _game()
    _put(game, card_db, "Flooded Strand", 0, "hand")
    _put(game, card_db, "Faithful Mending", 0, "hand")
    _put(game, card_db, "Goryo's Vengeance", 0, "hand")
    needs = analyze_mana_needs(game, 0, {})
    assert needs.missing_colors == {"W", "U", "B"}


def test_without_a_held_land_every_needed_colour_is_a_deficit(card_db):
    """Regression anchor: the battlefield-only reading is unchanged
    when the hand holds no land."""
    game = _game()
    _put(game, card_db, "Faithful Mending", 0, "hand")
    _put(game, card_db, "Goryo's Vengeance", 0, "hand")
    lib = _library_of_duals(game, card_db)
    needs = analyze_mana_needs(game, 0, {})
    assert needs.missing_colors == {"W", "U", "B"}
    pick = choose_fetch_target(game.players[0].library, ["W", "U"], needs,
                               turn=game.turn_number)
    assert pick in lib.values()
    assert len(set(pick.template.produces_mana) & {"W", "U", "B"}) == 2


def test_a_battlefield_source_and_a_held_source_add_up_for_double_pips(card_db):
    """A {B}{B} pip depth is met by one black source in play plus one
    black land in hand — the deficit is measured against sources the
    player will have."""
    game = _game()
    _put(game, card_db, "Swamp", 0, "battlefield")
    _put(game, card_db, "Swamp", 0, "hand")
    _put(game, card_db, "Damnation", 0, "hand")                 # 2BB
    needs = analyze_mana_needs(game, 0, {})
    assert "B" not in needs.missing_colors
