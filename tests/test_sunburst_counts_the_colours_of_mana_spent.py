"""Sunburst counts the colours of mana spent to cast the permanent (CR
702.44a, 105.1, 107.3).

"Sunburst" means: if the object enters the battlefield from the stack as a
creature, it enters with a +1/+1 counter for each colour of mana spent to
cast it; otherwise it enters with that many charge counters (CR 702.44a).
Colourless mana is no colour (CR 105.1); a second mana of a colour already
spent adds nothing. X is mana like any other (CR 107.3): it buys counters
only through the colours that pay it.

The keyword is typed at load (`Keyword.SUNBURST`, from the card's keyword
line); the entry rule reads the colours the cast recorded
(`StackItem.colors_spent`) in `spell_resolution._enter_with_sunburst`; an X
of a spell that counts its colours is paid with new colours first. The
auditor `702.44a/sunburst` restates the rule from the stack item.

The defect (docs/diagnostics/2026-10-14_dimir_midrange_root_cause.md,
subsystem 3): a name-keyed entry handler counted the colours the
controller's lands could make, not the mana spent, so X=3 paid with two
colours and X=1 paid with one both read as two; the X-cost parser also
read the reminder text as "enters with X charge counters".

Card names are fixture carriers: 16 pool cards have sunburst.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, Keyword
from engine.game_state import GameState, Phase


def _put(game, card_db, name, controller, zone="battlefield"):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller], zone).append(c)
    return c


def _cast(card_db, name, lands, tapped=()):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for land in lands:
        _put(game, card_db, land, 0)
    for land in tapped:
        _put(game, card_db, land, 0).tapped = True
    for _ in range(5):
        _put(game, card_db, "Island", 0, "library")
        _put(game, card_db, "Island", 1, "library")
    card = _put(game, card_db, name, 0, "hand")
    assert game.cast_spell(0, card, [])
    while not game.stack.is_empty:
        game.resolve_stack()
    assert card.zone == "battlefield"
    return game, card


@pytest.mark.parametrize("name", ["Engineered Explosives", "Suntouched Myr",
                                  "Pentad Prism", "Etched Oracle"])
def test_sunburst_is_typed_from_the_keyword_line(card_db, name):
    assert Keyword.SUNBURST in card_db.get_card(name).keywords


def test_sunburst_charge_counters_are_not_x_charge_counters(card_db):
    t = card_db.get_card("Engineered Explosives")
    assert t.x_cost_data and t.x_cost_data["effect"] != "charge_counters"
    assert card_db.get_card("Chalice of the Void").x_cost_data["effect"] \
        == "charge_counters"


@pytest.mark.parametrize("lands, counters", [
    (["Island", "Swamp"], 2),                 # X=2: blue and black
    (["Island", "Swamp", "Island"], 2),       # X=3: a second blue adds none
    (["Island"], 1),
    (["Island", "Swamp", "Mountain"], 3),
])
def test_a_noncreature_enters_with_a_charge_counter_per_colour_spent(
        card_db, lands, counters):
    game, ee = _cast(card_db, "Engineered Explosives", lands)
    assert ee.other_counters.get("charge", 0) == counters


def test_a_colour_its_controller_could_make_but_did_not_spend_counts_nothing(
        card_db):
    """A tapped Mountain makes no mana for this cast: red was not spent."""
    game, ee = _cast(card_db, "Engineered Explosives", ["Island", "Swamp"],
                     tapped=["Mountain"])
    assert ee.other_counters.get("charge", 0) == 2


def test_a_source_that_can_make_a_colour_pays_it_rather_than_colourless(
        card_db):
    """Shivan Reef makes {C}, {U} or {R}: paying X for a spell that counts
    its colours, it pays a colour (CR 105.1: colourless is no colour)."""
    game, ee = _cast(card_db, "Engineered Explosives", ["Shivan Reef"])
    assert ee.other_counters.get("charge", 0) == 1
    assert game.players[0].life == 19, "its coloured mana deals 1 damage"


def test_colourless_mana_is_no_colour(card_db):
    game, ee = _cast(card_db, "Engineered Explosives",
                     ["Wastes", "Wastes"])
    assert ee.other_counters.get("charge", 0) == 0


def test_a_creature_enters_with_a_plus_one_counter_per_colour_spent(card_db):
    """Suntouched Myr ({3}, 0/0 sunburst) paid with three colours."""
    game, myr = _cast(card_db, "Suntouched Myr",
                      ["Island", "Swamp", "Mountain"])
    assert myr.plus_counters == 3 and not myr.other_counters.get("charge")


# ── The auditor (CR 702.44a) ──────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "702.44a/sunburst"]


def test_the_audit_is_silent_when_sunburst_counts_the_colours(card_db, audit):
    _cast(card_db, "Engineered Explosives", ["Island", "Swamp"])
    assert not _findings(audit)


def test_the_audit_records_a_permanent_entering_without_its_counters(
        card_db, audit, monkeypatch):
    from engine import spell_resolution
    monkeypatch.setattr(spell_resolution, "_enter_with_sunburst",
                        lambda game, card, item: None)
    _cast(card_db, "Engineered Explosives", ["Island", "Swamp"])
    assert _findings(audit)
