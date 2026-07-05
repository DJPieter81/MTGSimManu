"""E1 — a single land tap can produce more than one mana.

Probe evidence (seed 60105, Amulet vs Dimir Bo3): a land whose plain
tap ability reads "Add {G}{U}" produced ONE green mana per tap.  The
template schema (`produces_mana: List[str]`) stores only the color
union — the quantity dimension is lost, so every land is worth
exactly one mana to the payment solver, the feasibility solver, and
every total-mana estimate.

Class size: every Modern land whose single {T} ability adds 2+ mana —
the karoo/bounce cycle (10 lands), Locus lands, Eldrazi Temple's base
line, plus any future printing.  The fix adds `mana_units:
List[List[str]]` to the template (one inner list of color options per
unit of mana produced), parsed from the SAME oracle text pass that
already derives `produces_mana`.

Rule-phrased tests only — no card-specific behavior is asserted, the
card names below are just carriers for the oracle shapes.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import OracleTextParser
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.mana import ManaCost


# ─── Parser: oracle shape → mana units ──────────────────────────────


def test_consecutive_add_symbols_parse_as_fixed_units():
    """'{T}: Add {G}{U}.' is TWO units, each a single fixed color —
    not one unit with a color choice."""
    units = OracleTextParser.detect_land_mana_units("{T}: Add {G}{U}.")
    assert units == [["G"], ["U"]]


def test_repeated_colorless_symbols_parse_as_two_units():
    units = OracleTextParser.detect_land_mana_units("{T}: Add {C}{C}.")
    assert units == [["C"], ["C"]]


def test_or_separated_symbols_parse_as_one_unit_with_choice():
    """'{T}: Add {G} or {U}.' is ONE unit of mana with two color
    options (a dual), not two mana."""
    units = OracleTextParser.detect_land_mana_units("{T}: Add {G} or {U}.")
    assert units == [["G", "U"]]


def test_comma_or_list_parses_as_one_unit_with_choices():
    units = OracleTextParser.detect_land_mana_units(
        "{T}: Add {W}, {U}, or {B}.")
    assert units == [["W", "U", "B"]]


def test_additional_cost_ability_lines_do_not_inflate_units():
    """A second ability with an extra cost ('{T}, Sacrifice ...') must
    not raise the land's always-available mana count."""
    units = OracleTextParser.detect_land_mana_units(
        "{T}: Add {C}.\n{T}, Sacrifice this land: Add {B}{B}.")
    assert units == [["C"]]


def test_worded_quantity_parses_as_that_many_any_color_units():
    units = OracleTextParser.detect_land_mana_units(
        "{T}: Add two mana of any one color.")
    assert len(units) == 2
    assert all(set(u) == {"W", "U", "B", "R", "G"} for u in units)


# ─── Payment: one tap yields every unit ─────────────────────────────


def _land_on_battlefield(game: GameState, card_db, name: str,
                         player_idx: int = 0) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=player_idx, controller=player_idx,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.tapped = False
    game.players[player_idx].battlefield.append(card)
    return card


def test_single_tap_pays_both_fixed_pips(card_db):
    """A land whose tap ability adds two fixed symbols pays a cost
    containing BOTH of those pips with a single tap."""
    game = GameState(rng=random.Random(0))
    land = _land_on_battlefield(game, card_db, "Simic Growth Chamber")
    ok = game.tap_lands_for_mana(0, ManaCost(green=1, blue=1))
    assert ok, "one two-unit land must pay {G}{U} alone"
    assert land.tapped
    assert game.players[0].mana_pool.total() == 0


def test_fixed_units_cannot_double_pay_one_color(card_db):
    """The two units of an 'Add {G}{U}' land are FIXED — the land must
    not be able to pay {U}{U}."""
    game = GameState(rng=random.Random(0))
    _land_on_battlefield(game, card_db, "Simic Growth Chamber")
    ok = game.tap_lands_for_mana(0, ManaCost(blue=2))
    assert not ok, "fixed {G}{U} units must not pay a {U}{U} cost"


def test_two_unit_land_pays_generic_two_alone(card_db):
    game = GameState(rng=random.Random(0))
    land = _land_on_battlefield(game, card_db, "Simic Growth Chamber")
    ok = game.tap_lands_for_mana(0, ManaCost(generic=2))
    assert ok, "a two-unit land is worth 2 toward generic costs"
    assert land.tapped
    assert game.players[0].mana_pool.total() == 0


def test_untapped_mana_capacity_counts_units_not_lands(card_db):
    """Total-mana estimates must count units of production, not the
    number of untapped land cards."""
    game = GameState(rng=random.Random(0))
    _land_on_battlefield(game, card_db, "Simic Growth Chamber")
    _land_on_battlefield(game, card_db, "Forest")
    player = game.players[0]
    assert player.untapped_mana_capacity() == 3


def test_affordability_sees_multi_unit_land(card_db):
    """can_cast must consider a lone two-unit land sufficient for a
    two-pip cost the land's fixed units cover."""
    game = GameState(rng=random.Random(0))
    _land_on_battlefield(game, card_db, "Simic Growth Chamber")
    tmpl = card_db.get_card("Growth Spiral")  # {G}{U} instant
    assert tmpl is not None
    spell = CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="hand",
    )
    spell._game_state = game
    game.players[0].hand.append(spell)
    assert game.can_cast(0, spell), (
        "a lone {G}{U}-producing land must make a {G}{U} spell castable"
    )
