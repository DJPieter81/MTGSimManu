"""A double-pipped colour requirement isn't satisfied by a single source.

Mana-planner root cause (Domain-Zoo-overperformance Bo3 re-diagnosis, seed
50000): `analyze_mana_needs` computed `missing_colors = needed_colors.keys() -
all_land_colors` — a colour left the "missing" set the moment ONE source of it
existed. A `{W}{W}` sweeper held with only a single white source therefore read
as "white is covered", so the fetch/land scorer (block A in `score_land`) gave
a brand-new colour a strong missing-colour bonus while a *second* white source
— the one that actually makes the double-white spell castable — got only the
weak redundant-colour weight, and lost the fetch. The control deck never
assembled `{W}{W}`, never cast its board wipe, and died to a 2/1.

Rule under test: a colour whose deepest single-spell pip requirement exceeds
the number of sources producing it is a colour deficit (flagged `missing`),
even when one source already exists. Mono-pip colours with ≥1 source are
unchanged. Mechanic-driven (pip depth vs source count), no card names in the
assertions.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from ai.mana_planner import analyze_mana_needs

_DB = CardDatabase()


def _add(game, name, controller, zone, tapped=False):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.tapped = tapped
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _fresh_game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 4
    game.players[0].deck_name = "4/5c Control"
    game.players[1].deck_name = "Domain Zoo"
    return game


def test_double_pip_color_with_single_source_is_flagged_missing():
    game = _fresh_game()
    # One untapped white source only.
    hf = _add(game, "Hallowed Fountain", 0, "battlefield")  # produces W, U
    assert "W" in hf.template.produces_mana
    # A double-white spell in hand: {W}{W}.
    wrath = _add(game, "Wrath of the Skies", 0, "hand")
    assert wrath.template.mana_cost.white == 2, "fixture must be double-white"

    needs = analyze_mana_needs(game, 0)

    # We hold a {W}{W} spell but control only ONE white source, so white is a
    # genuine deficit — a second white source is still needed.
    assert "W" in needs.missing_colors, (
        "a colour required at pip-depth 2 with only one source must be flagged "
        "missing; single-source coverage does not satisfy a double pip")


def test_single_pip_color_with_one_source_is_not_missing():
    # Regression: mono-pip demand met by one source stays satisfied (unchanged
    # behaviour — the fix must not flag colours the deck can already produce
    # enough of).
    game = _fresh_game()
    _add(game, "Hallowed Fountain", 0, "battlefield")  # W, U
    # Path to Exile is a single white pip {W}.
    path = _add(game, "Path to Exile", 0, "hand")
    assert path.template.mana_cost.white == 1, "fixture must be single-white"

    needs = analyze_mana_needs(game, 0)
    assert "W" not in needs.missing_colors, (
        "a single white pip is satisfied by one white source; flagging it "
        "missing would over-fetch")


def test_double_pip_color_with_two_sources_is_satisfied():
    # Source-count semantics: {W}{W} with TWO white sources is met — the deficit
    # is pip-depth vs source-count, not "any double pip is always missing".
    game = _fresh_game()
    _add(game, "Hallowed Fountain", 0, "battlefield")  # W, U
    _add(game, "Temple Garden", 0, "battlefield")      # W, G
    wrath = _add(game, "Wrath of the Skies", 0, "hand")
    assert wrath.template.mana_cost.white == 2

    needs = analyze_mana_needs(game, 0)
    assert "W" not in needs.missing_colors, (
        "two white sources satisfy a {W}{W} requirement; the deficit test is "
        "pip-depth vs source-count, not double-pip-implies-missing")
