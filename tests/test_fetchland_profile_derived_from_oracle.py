"""Fetchland execution data is derived from the printed card, not a name table.

The mechanic: a land whose ACTIVATED ability sacrifices ITSELF to put a land
card from its controller's library onto the battlefield, with the search
constrained by BASIC LAND TYPES.  ~43 Modern lands print it (the Onslaught and
Zendikar cycles, the Panorama and Landscape cycles, Evolving Wilds,
Terramorphic Expanse, Fabled Passage, Prismatic Vista, Escape Tunnel, …).

Everything the engine needs to run one is printed on the card:

  * WHICH colours it can fetch — "a Mountain or Plains card", "a basic
    Forest, Plains, or Island card", "a basic land card".
  * WHETHER it costs life — "Pay 1 life" is part of the activation cost.
  * HOW the fetched land enters — "put it onto the battlefield" vs
    "… onto the battlefield tapped", plus Fabled Passage's "Then if you
    control four or more lands, untap that land."

So none of it may live in `.py` source.  These tests pin the rule, not the
cards: the profile comes out of `oracle_parser.parse_fetchland_profile` and is
read off `CardTemplate.fetchland`; the old 38-entry `FETCH_LAND_COLORS`
card-name dict and the `no_life_fetches` name set are gone.
"""
from __future__ import annotations

import random

import pytest

from engine import card_database as card_database_module
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.land_manager import LandManager
from engine.oracle_parser import parse_fetchland_profile


# ── Printed shapes, used as parser inputs ────────────────────────────
# Each is the exact oracle line the shape is printed with, so the test
# states the RULE ("this wording means that") rather than naming cards.

ONSLAUGHT_FETCH = (
    "{T}, Pay 1 life, Sacrifice this land: Search your library for a "
    "Mountain or Plains card, put it onto the battlefield, then shuffle."
)
PANORAMA_FETCH = (
    "{T}: Add {C}.\n{1}, {T}, Sacrifice this land: Search your library for a "
    "basic Forest, Plains, or Island card, put it onto the battlefield "
    "tapped, then shuffle."
)
ANY_BASIC_FREE_FETCH = (
    "{T}, Sacrifice this land: Search your library for a basic land card, "
    "put it onto the battlefield tapped, then shuffle."
)
ANY_BASIC_LIFE_FETCH = (
    "{T}, Pay 1 life, Sacrifice this land: Search your library for a basic "
    "land card, put it onto the battlefield, then shuffle."
)
DELAYED_UNTAP_FETCH = (
    "{T}, Sacrifice this land: Search your library for a basic land card, "
    "put it onto the battlefield tapped, then shuffle. Then if you control "
    "four or more lands, untap that land."
)
LAND_DESTRUCTION = (
    "{T}: Add {C}.\n{T}, Sacrifice this land: Destroy target land. Its "
    "controller may search their library for a basic land card, put it onto "
    "the battlefield, then shuffle."
)
LAND_DESTRUCTION_WITH_OWN_SEARCH = (
    "{T}: Add {C}.\n{2}, {T}, Sacrifice this land: Destroy target nonbasic "
    "land an opponent controls. That land's controller may search their "
    "library for a basic land card, put it onto the battlefield, then "
    "shuffle. You may search your library for a basic land card, put it onto "
    "the battlefield, then shuffle."
)
UNTYPED_LAND_SEARCH = (
    "{T}: Add {C}.\n{3}, {T}, Sacrifice this land: Search your library for a "
    "land card, put it onto the battlefield tapped, then shuffle."
)
SEARCH_TO_HAND = (
    "{T}: Add {C}.\n{4}, {T}, Sacrifice this land: Search your library for a "
    "Dragon card, reveal it, put it into your hand, then shuffle."
)
TRIGGERED_SELF_SACRIFICE = (
    "When this land enters, sacrifice it. When you do, search your library "
    "for a basic Forest, Plains, or Island card, put it onto the battlefield "
    "tapped, then shuffle and you gain 1 life."
)


class TestColorsComeFromThePrintedLandTypes:
    """Which colours a fetch reaches is stated on the card."""

    def test_named_basic_types_become_those_colors_in_canonical_order(self):
        # "a Mountain or Plains card" — printed R-then-W, canonicalised WR.
        assert parse_fetchland_profile(ONSLAUGHT_FETCH).colors == ("W", "R")

    def test_three_named_types_become_exactly_those_three_colors(self):
        assert parse_fetchland_profile(PANORAMA_FETCH).colors == (
            "W", "U", "G")

    def test_unqualified_basic_land_means_every_basic_type(self):
        assert parse_fetchland_profile(ANY_BASIC_FREE_FETCH).colors == (
            "W", "U", "B", "R", "G")

    def test_untyped_land_search_is_refused_not_approximated(self):
        """"Search your library for a land card" can find ANY land — a
        creature-land, a Tron piece, a bounce land.  A colour set cannot
        express that, so the parse refuses rather than pretending the card
        is a five-colour fetch."""
        assert parse_fetchland_profile(UNTYPED_LAND_SEARCH) is None


class TestLifePaymentComesFromThePrintedActivationCost:
    """A life-paying fetch and a free fetch are told apart by their text."""

    def test_pay_1_life_in_the_cost_is_a_life_cost(self):
        assert parse_fetchland_profile(ONSLAUGHT_FETCH).life_cost == 1

    def test_same_search_without_the_payment_costs_no_life(self):
        assert parse_fetchland_profile(ANY_BASIC_FREE_FETCH).life_cost == 0

    def test_life_payment_is_independent_of_what_is_searched_for(self):
        """The two "basic land card" fetches differ ONLY in the printed
        payment — the colour set is identical.  A name set cannot see that
        difference; the cost line can."""
        free = parse_fetchland_profile(ANY_BASIC_FREE_FETCH)
        paid = parse_fetchland_profile(ANY_BASIC_LIFE_FETCH)
        assert free.colors == paid.colors
        assert (free.life_cost, paid.life_cost) == (0, 1)


class TestFetchedLandEntryStateComesFromThePrintedEffect:
    """"put it onto the battlefield [tapped]" is a property of the FETCH."""

    def test_tapped_rider_makes_the_fetched_land_enter_tapped(self):
        assert parse_fetchland_profile(
            ANY_BASIC_FREE_FETCH).target_enters_tapped is True

    def test_no_tapped_rider_means_the_fetched_land_enters_untapped(self):
        assert parse_fetchland_profile(
            ONSLAUGHT_FETCH).target_enters_tapped is False

    def test_conditional_untap_rider_records_its_own_land_threshold(self):
        profile = parse_fetchland_profile(DELAYED_UNTAP_FETCH)
        assert profile.target_enters_tapped is True
        assert profile.untap_target_min_lands == 4

    def test_absent_untap_rider_records_no_threshold(self):
        assert parse_fetchland_profile(
            ANY_BASIC_FREE_FETCH).untap_target_min_lands == 0


class TestShapesOutsideTheMechanicAreRefused:
    """The sacrifice-and-search FLAG is broader than the mechanic; the
    parse is what separates them."""

    def test_land_destruction_is_not_a_fetchland(self):
        """The sacrifice is a cost for REMOVAL and the search is the
        opponent's.  Treating this as a fetchland makes the engine play it
        and instantly crack it for a free basic it never paid for."""
        assert parse_fetchland_profile(LAND_DESTRUCTION) is None

    def test_land_destruction_is_not_a_fetchland_even_with_an_own_search(
            self):
        """The controller's own search rides on a TARGETED destroy that
        costs mana — the ability is land destruction, not a fetch."""
        assert parse_fetchland_profile(LAND_DESTRUCTION_WITH_OWN_SEARCH) is None

    def test_search_that_ends_in_hand_is_not_a_fetchland(self):
        assert parse_fetchland_profile(SEARCH_TO_HAND) is None

    def test_triggered_self_sacrifice_is_outside_the_activated_class(self):
        """Same search, but a TRIGGER with a life-gain rider rather than an
        activated ability whose cost this profile models."""
        assert parse_fetchland_profile(TRIGGERED_SELF_SACRIFICE) is None

    def test_empty_or_missing_oracle_is_not_a_fetchland(self):
        assert parse_fetchland_profile("") is None
        assert parse_fetchland_profile(None) is None


class TestTypedFieldReplacesTheCardNameTable:
    """Consumers read `CardTemplate.fetchland`; the table is gone."""

    def test_card_database_exposes_no_fetch_land_name_table(self):
        assert not hasattr(card_database_module, "FETCH_LAND_COLORS")

    def test_land_manager_carries_no_no_life_fetch_name_set(self):
        import inspect

        from engine import land_manager

        src = inspect.getsource(land_manager)
        assert "no_life_fetches" not in src

    def test_every_pool_fetchland_derives_the_colors_the_table_declared(
            self, card_db):
        """Entry-by-entry equivalence against the retired 38-entry
        `FETCH_LAND_COLORS`, frozen here as the migration's evidence.

        37 of 38 derive identically.  The 38th, Demolition Field, is the
        table's own bug: its text is "{2}, {T}, Sacrifice this land: Destroy
        target nonbasic land an opponent controls…", and the old substring
        match ('sacrifice this land' + 'search your library' + 'basic land
        card') read the destroy ability's rider as a fetch.  The card text
        wins, so the derived data drops it — see
        TestShapesOutsideTheMechanicAreRefused.
        """
        retired_table = {
            "Arid Mesa": ["W", "R"],
            "Bant Panorama": ["W", "U", "G"],
            "Blighted Woodland": ["W", "U", "B", "R", "G"],
            "Bloodstained Mire": ["B", "R"],
            "Bountiful Landscape": ["U", "R", "G"],
            "Contaminated Landscape": ["W", "U", "B"],
            "Deceptive Landscape": ["W", "B", "G"],
            "Demolition Field": ["W", "U", "B", "R", "G"],
            "Elven Passage": ["W", "U", "B", "R", "G"],
            "Escape Tunnel": ["W", "U", "B", "R", "G"],
            "Esper Panorama": ["W", "U", "B"],
            "Evolving Wilds": ["W", "U", "B", "R", "G"],
            "Fabled Passage": ["W", "U", "B", "R", "G"],
            "Flooded Strand": ["W", "U"],
            "Foreboding Landscape": ["U", "B", "G"],
            "Grixis Panorama": ["U", "B", "R"],
            "Hobbit Hole": ["W", "U", "B", "R", "G"],
            "Jund Panorama": ["B", "R", "G"],
            "Marsh Flats": ["W", "B"],
            "Misty Rainforest": ["U", "G"],
            "Naya Panorama": ["W", "R", "G"],
            "Perilous Landscape": ["W", "U", "R"],
            "Polluted Delta": ["U", "B"],
            "Prismatic Vista": ["W", "U", "B", "R", "G"],
            "Promising Vein": ["W", "U", "B", "R", "G"],
            "Scalding Tarn": ["U", "R"],
            "Seething Landscape": ["U", "B", "R"],
            "Shattered Landscape": ["W", "B", "R"],
            "Sheltering Landscape": ["W", "R", "G"],
            "Shire Terrace": ["W", "U", "B", "R", "G"],
            "Terramorphic Expanse": ["W", "U", "B", "R", "G"],
            "Tranquil Landscape": ["W", "U", "G"],
            "Twisted Landscape": ["B", "R", "G"],
            "Verdant Catacombs": ["B", "G"],
            "Vibrant Cityscape": ["W", "U", "B", "R", "G"],
            "Warped Landscape": ["W", "U", "B", "R", "G"],
            "Windswept Heath": ["W", "G"],
            "Wooded Foothills": ["R", "G"],
        }
        # The one entry the card text overrules — land destruction.
        refuted_by_card_text = {"Demolition Field"}

        mismatches = []
        for name, colors in retired_table.items():
            template = card_db.get_card(name)
            if template is None:
                continue  # card not in this DB build
            profile = template.fetchland
            if name in refuted_by_card_text:
                if profile is not None:
                    mismatches.append(f"{name}: expected refusal, got {profile}")
                continue
            if profile is None:
                mismatches.append(f"{name}: derived nothing, table said {colors}")
            elif list(profile.colors) != colors:
                mismatches.append(
                    f"{name}: derived {list(profile.colors)}, table said {colors}")
        assert not mismatches, "\n".join(mismatches)

    def test_no_pool_land_gains_fetch_status_it_did_not_have(self, card_db):
        """The migration must not silently widen the class either: every
        land the derivation accepts was already in the retired table."""
        table_names = {
            "Arid Mesa", "Bant Panorama", "Blighted Woodland",
            "Bloodstained Mire", "Bountiful Landscape",
            "Contaminated Landscape", "Deceptive Landscape",
            "Demolition Field", "Elven Passage", "Escape Tunnel",
            "Esper Panorama", "Evolving Wilds", "Fabled Passage",
            "Flooded Strand", "Foreboding Landscape", "Grixis Panorama",
            "Hobbit Hole", "Jund Panorama", "Marsh Flats",
            "Misty Rainforest", "Naya Panorama", "Perilous Landscape",
            "Polluted Delta", "Prismatic Vista", "Promising Vein",
            "Scalding Tarn", "Seething Landscape", "Shattered Landscape",
            "Sheltering Landscape", "Shire Terrace", "Terramorphic Expanse",
            "Tranquil Landscape", "Twisted Landscape", "Verdant Catacombs",
            "Vibrant Cityscape", "Warped Landscape", "Windswept Heath",
            "Wooded Foothills",
        }
        derived = {n for n, t in card_db.cards.items()
                   if t.fetchland is not None}
        assert not (derived - table_names)


def _land(game, card_db, name, controller, zone):
    template = card_db.get_card(name)
    assert template is not None, f"missing card: {name}"
    card = CardInstance(
        template=template, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    getattr(game.players[controller], zone).append(card)
    return card


class TestCrackingSpendsExactlyThePrintedCost:
    """`crack_fetchland` reads the profile, so the life payment and the
    fetched land's entry state follow the printed card."""

    def test_life_paying_fetch_costs_its_printed_life(self, card_db):
        game = GameState(rng=random.Random(0))
        fetch = _land(game, card_db, "Arid Mesa", 0, "battlefield")
        _land(game, card_db, "Mountain", 0, "library")
        before = game.players[0].life

        LandManager.crack_fetchland(game, 0, fetch)

        assert game.players[0].life == before - 1

    def test_fetch_without_a_printed_payment_costs_no_life(self, card_db):
        game = GameState(rng=random.Random(0))
        fetch = _land(game, card_db, "Evolving Wilds", 0, "battlefield")
        _land(game, card_db, "Mountain", 0, "library")
        before = game.players[0].life

        LandManager.crack_fetchland(game, 0, fetch)

        assert game.players[0].life == before

    def test_fetch_printing_tapped_puts_the_land_in_tapped(self, card_db):
        game = GameState(rng=random.Random(0))
        fetch = _land(game, card_db, "Evolving Wilds", 0, "battlefield")
        _land(game, card_db, "Mountain", 0, "library")

        LandManager.crack_fetchland(game, 0, fetch)

        fetched = [c for c in game.players[0].battlefield
                   if c.name == "Mountain"]
        assert len(fetched) == 1
        assert fetched[0].tapped is True

    def test_fetch_not_printing_tapped_puts_the_land_in_untapped(
            self, card_db):
        game = GameState(rng=random.Random(0))
        fetch = _land(game, card_db, "Arid Mesa", 0, "battlefield")
        _land(game, card_db, "Mountain", 0, "library")

        LandManager.crack_fetchland(game, 0, fetch)

        fetched = [c for c in game.players[0].battlefield
                   if c.name == "Mountain"]
        assert len(fetched) == 1
        assert fetched[0].tapped is False
