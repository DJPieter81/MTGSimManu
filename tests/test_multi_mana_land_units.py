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
from engine.stack import StackItem, StackItemType


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


def test_two_equal_arity_tap_lines_merge_their_color_choices():
    """A land with two SEPARATE plain '{T}: Add …' lines, each
    producing one unit of mana, is tapped ONCE per turn for whichever
    line the player picks — the parsed production must be the union
    of both lines' colors, not just the first one seen.

    Live bug this pins (replay audit, docs artifact "The Colorless
    Counterspell" — Ruby Storm vs Dimir Midrange, seed 57008): the
    tie-break used a strict `len(units) > len(best)`, so when a
    second same-arity line resolved AFTER the first (e.g. a painland's
    free colorless line printed before its paid colored line), the
    colored option was silently discarded and the land's parsed
    production regressed to colorless-only — `can_cast`'s color solver
    then treated a genuinely castable colored spell as uncastable.

    Class size: the full painland cycle (10 lands — Underground River,
    Yavimaya Coast, Adarkar Wastes, Sulfurous Springs, Karplusan
    Forest, Battlefield Forge, Brushland, Caves of Koilos, Shivan
    Reef, Llanowar Wastes) plus any future land printed with the same
    two-line shape. No card names appear in the parser fix; the
    fixture below is oracle text only.
    """
    units = OracleTextParser.detect_land_mana_units(
        "{T}: Add {C}.\n{T}: Add {U} or {B}. This land deals 1 damage to you."
    )
    assert units == [["C", "U", "B"]], (
        f"expected the free-colorless and paid-colored lines to merge "
        f"into one unit offering all three colors, got {units!r}"
    )


def test_first_seen_equal_arity_line_does_not_win_over_a_later_one():
    """Order must not matter for equal-arity lines: swapping which
    line prints first must not change which colors survive."""
    units = OracleTextParser.detect_land_mana_units(
        "{T}: Add {U} or {B}. This land deals 1 damage to you.\n{T}: Add {C}."
    )
    assert set(units[0]) == {"C", "U", "B"}


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


def test_affordability_sees_the_painland_colored_line_not_just_its_free_line(
        card_db):
    """A painland's paid-colored line must be visible to `can_cast`,
    not shadowed by its own free-colorless line printed first in the
    oracle text (the exact shape of the live bug this file's parser
    tests pin: `can_cast` returned False for a genuinely castable
    UU spell because the second land's colored option had been
    silently dropped)."""
    game = GameState(rng=random.Random(0))
    _land_on_battlefield(game, card_db, "Underground River")
    _land_on_battlefield(game, card_db, "Underground River")

    # Counterspell requires a legal target (CR 601.2c) — put an
    # opposing spell on the stack, matching the live bug's shape
    # (a lethal Grapeshot on the stack, held Counterspell unable to
    # respond).
    target_tmpl = card_db.get_card("Lightning Bolt")
    assert target_tmpl is not None
    target_card = CardInstance(
        template=target_tmpl, owner=1, controller=1,
        instance_id=game.next_instance_id(), zone="stack",
    )
    target_card._game_state = game
    game.stack.push(StackItem(item_type=StackItemType.SPELL,
                              source=target_card, controller=1, targets=[]))

    tmpl = card_db.get_card("Counterspell")  # {U}{U} instant
    assert tmpl is not None
    spell = CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="hand",
    )
    spell._game_state = game
    game.players[0].hand.append(spell)
    assert game.can_cast(0, spell), (
        "two Underground Rivers must make a {U}{U} spell castable — "
        "each land's paid-colored line ({U} or {B}) must not be "
        "shadowed by its own free-colorless line ({C})"
    )
