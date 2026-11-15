"""Every registered list can pay for what it plays (blind-spot class B5).

`tools/check_colour_feasibility.py`: a nonland card whose coloured pips no
mana source of its own list produces is either fixed or declared, with the
reason it is played uncast. The rule is checked on synthetic lists too, so a
change to the colour closure cannot quietly pass every card.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location(
        "check_colour_feasibility",
        ROOT / "tools" / "check_colour_feasibility.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_registered_list_pays_for_its_cards_or_declares_why_not():
    assert _tool().main([]) == 0


def test_a_spell_off_the_lists_colours_is_infeasible_and_an_on_colour_one_is_not(card_db):
    t = _tool()
    deck = {"X": {"mainboard": {"Mountain": 20, "Lightning Bolt": 4,
                                "Counterspell": 4}, "sideboard": {}}}
    found = t.findings(card_db, deck)
    assert found == {"X": {"Counterspell": ["U"]}}


def test_a_fetch_land_supplies_the_colours_of_the_lands_it_can_find(card_db):
    t = _tool()
    deck = {"X": {"mainboard": {"Flooded Strand": 4, "Steam Vents": 4,
                                "Counterspell": 4}, "sideboard": {}}}
    assert t.findings(card_db, deck) == {}


def test_an_enter_only_colour_is_not_a_tap_colour(card_db):
    t = _tool()
    deck = {"X": {"mainboard": {"Crumbling Vestige": 20, "Counterspell": 4},
                  "sideboard": {}}}
    assert t.findings(card_db, deck) == {"X": {"Counterspell": ["U"]}}


def test_a_mana_creature_paid_by_the_lands_adds_its_colours(card_db):
    t = _tool()
    deck = {"X": {"mainboard": {"Forest": 20, "Birds of Paradise": 4,
                                "Counterspell": 4}, "sideboard": {}}}
    assert t.findings(card_db, deck) == {}
    unpaid = {"X": {"mainboard": {"Island": 20, "Birds of Paradise": 4},
                    "sideboard": {}}}
    assert t.findings(card_db, unpaid) == {"X": {"Birds of Paradise": ["G"]}}


def test_a_payable_cycling_cost_is_a_way_to_play_the_card(card_db):
    t = _tool()
    deck = {"X": {"mainboard": {"Mountain": 20, "Street Wraith": 4},
                  "sideboard": {}}}
    assert t.findings(card_db, deck) == {}
