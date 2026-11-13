"""A target's colour qualifier is part of the target's requirement, read at
the object's current colours (CR 115.1, 601.2c, 105.2).

"Counter target colorless spell" admits only a spell with no colour (CR
105.2c); "target multicolored spell" one of two or more colours (105.2b);
"target monocolored creature" one of exactly one (105.2a); "target
permanent that's one or more colors" any object with a colour; "target red
creature" one that is red; "target nonblack creature" one that is not black.
An object's colours are what it shows now: its printed colour, the face that
is up, and any colour-setting effect (CR 105.2b, 613 layer 5).

The defect: the target parser read only "target <type>" adjacent words, so a
colour adjective between them hid the phrase (Doom Blade, Ceremonious
Rejection and Infernal Reckoning parsed no requirement at all), and a
trailing "that's one or more colors" was dropped (Ugin, Eye of the Storms and
Devourer of Destiny read "target permanent", admitting a colourless one).

Class: 76 pool cards print a colour-qualified target. Registered: Devourer
of Destiny (Eldrazi Tron, Eldrazi Ramp, Broodscale Bloodchief) and Ugin, Eye
of the Storms (Eldrazi Tron, Eldrazi Ramp). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.mana import Color
from engine.stack import StackItem, StackItemType
from engine.target_solver import (enumerate_legal_targets, has_legal_target,
                                  parse)


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 1
    return game


def _card(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
    if zone != "stack":
        getattr(game.players[controller], zone).append(card)
    return card


def _cast(game, card_db, name, controller=1):
    spell = _card(game, card_db, name, controller, "stack")
    item = StackItem(item_type=StackItemType.SPELL, source=spell,
                     controller=controller)
    game.stack.push(item)
    return item


def _only(text, zone):
    reqs = [r for r in parse(text) if r.zone == zone]
    assert len(reqs) == 1, reqs
    return reqs[0]


# ── The qualifier is read into the requirement ────────────────────────

@pytest.mark.parametrize("text, zone, bound", [
    ("Counter target colorless spell.", "stack", ("colorless", "")),
    ("Counter target multicolored spell.", "stack", ("multicolored", "")),
    ("Counter target blue spell.", "stack", ("is", "U")),
    ("Destroy target nonblack creature.", "battlefield", ("non", "B")),
    ("Gain control of target monocolored creature.", "battlefield",
     ("monocolored", "")),
    ("Exile target colorless creature. You gain life equal to its power.",
     "battlefield", ("colorless", "")),
    ("This spell deals 3 damage to target red creature.", "battlefield",
     ("is", "R")),
    ("Exile up to one target permanent that's one or more colors.",
     "battlefield", ("colored", "")),
])
def test_a_colour_qualifier_is_read_into_its_requirement(text, zone, bound):
    assert _only(text, zone).color_bound == bound


def test_the_qualifier_keeps_the_count_and_scope_around_it():
    two = _only("Destroy two target nonblack creatures.", "battlefield")
    assert (two.count_min, two.count_max, two.color_bound) == (2, 2,
                                                              ("non", "B"))
    scoped = _only("Destroy target nonblack creature an opponent controls.",
                   "battlefield")
    assert (scoped.owner_scope, scoped.color_bound) == ("opponent",
                                                        ("non", "B"))
    up_to = _only("Exile up to one target permanent that's one or more colors.",
                  "battlefield")
    assert (up_to.count_min, up_to.count_max, up_to.is_optional) == (0, 1,
                                                                     True)


def test_an_unqualified_target_has_no_colour_bound():
    assert _only("Destroy target creature.", "battlefield").color_bound is None
    assert _only("Counter target spell.", "stack").color_bound is None


@pytest.mark.parametrize("text", [
    "Return target black card from your graveyard to your hand.",
    "You may cast target red instant or sorcery card from your graveyard.",
    "This spell deals 5 damage to target red creature or red planeswalker.",
])
def test_a_card_target_and_a_per_type_colour_compound_are_refused(text):
    """Unread rather than half-read: a card target in a graveyard (the
    graveyard branch claims a text whole) and a colour on each type of a
    compound keep no colour bound."""
    assert all(r.color_bound is None for r in parse(text))


# ── It is part of what the requirement admits ─────────────────────────

def test_a_nonblack_requirement_admits_only_a_creature_that_is_not_black(
        card_db):
    game = _game()
    black = _card(game, card_db, "Dark Confidant", 1, "battlefield")
    green = _card(game, card_db, "Tarmogoyf", 1, "battlefield")
    legal = enumerate_legal_targets(
        game, 0, _only("Destroy target nonblack creature.", "battlefield"))
    assert green in legal and black not in legal


def test_colourless_coloured_and_monocoloured_read_the_number_of_colours(
        card_db):
    game = _game()
    colourless = _card(game, card_db, "Memnite", 1, "battlefield")
    mono = _card(game, card_db, "Tarmogoyf", 1, "battlefield")

    def legal(text):
        return set(enumerate_legal_targets(game, 0,
                                           _only(text, "battlefield")))

    assert legal("Exile target colorless creature.") == {colourless}
    assert legal("Gain control of target monocolored creature.") == {mono}
    assert legal("Exile up to one target permanent that's one or more "
                 "colors.") == {mono}


def test_the_qualifier_reads_the_colours_an_object_shows_now(card_db):
    game = _game()
    goyf = _card(game, card_db, "Tarmogoyf", 1, "battlefield")
    req = _only("Destroy target nonblack creature.", "battlefield")
    assert goyf in enumerate_legal_targets(game, 0, req)
    goyf.cem_colors_set = {Color.BLACK}                 # a colour-setting effect
    assert goyf not in enumerate_legal_targets(game, 0, req)


def test_a_spell_targets_colour_is_read_on_the_stack(card_db):
    game = _game()
    _cast(game, card_db, "Lightning Bolt")              # red
    colourless = _only("Counter target colorless spell.", "stack")
    multi = _only("Counter target multicolored spell.", "stack")
    assert not has_legal_target(game, 0, colourless)
    assert not has_legal_target(game, 0, multi)
    _cast(game, card_db, "Lightning Helix")             # red and white
    assert has_legal_target(game, 0, multi)
    _cast(game, card_db, "Thought-Knot Seer")           # colourless
    assert has_legal_target(game, 0, colourless)


# ── Through the real path ─────────────────────────────────────────────

def test_a_colourless_counter_is_not_castable_at_a_coloured_spell(card_db):
    from engine.target_solver import has_legal_target_for_spell
    game = _game()
    rejection = card_db.get_card("Ceremonious Rejection")
    reqs = parse(rejection.oracle_text)
    _cast(game, card_db, "Lightning Bolt")
    assert not has_legal_target_for_spell(game, 0, reqs)
    _cast(game, card_db, "Thought-Knot Seer")
    assert has_legal_target_for_spell(game, 0, reqs)


def test_the_ai_counters_only_a_spell_its_counter_admits(card_db):
    from ai.response import ResponseDecider
    game = _game()
    rejection = _card(game, card_db, "Ceremonious Rejection", 0, "hand")
    bolt = _cast(game, card_db, "Lightning Bolt")
    assert not ResponseDecider._counter_can_target(rejection, bolt)
    seer = _cast(game, card_db, "Thought-Knot Seer")
    assert ResponseDecider._counter_can_target(rejection, seer)


def test_an_eldrazi_colour_exile_admits_only_a_coloured_permanent(card_db):
    game = _game()
    goyf = _card(game, card_db, "Tarmogoyf", 1, "battlefield")
    memnite = _card(game, card_db, "Memnite", 1, "battlefield")
    for name in ("Ugin, Eye of the Storms", "Devourer of Destiny"):
        req = next(r for r in parse(card_db.get_card(name).oracle_text)
                   if r.zone == "battlefield")
        legal = enumerate_legal_targets(game, 0, req)
        assert goyf in legal and memnite not in legal, name


# ── Auditor (CR 115.1) ────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_target_its_colour_qualifier_refuses(
        card_db, audit):
    from engine.cast_manager import _audit_targets_color
    game = _game()
    blade = _card(game, card_db, "Doom Blade", 0, "hand")
    black = _card(game, card_db, "Dark Confidant", 1, "battlefield")
    _audit_targets_color(game, blade, [black.instance_id])
    assert "115.1/target_color_qualifier" in _rules(audit)
    rejection = _card(game, card_db, "Ceremonious Rejection", 0, "hand")
    bolt = _cast(game, card_db, "Lightning Bolt")
    _audit_targets_color(game, rejection, [bolt.source.instance_id])
    assert "115.1/target_color_qualifier" in _rules(audit)


def test_the_audit_is_silent_for_targets_the_qualifier_admits(card_db, audit):
    from engine.cast_manager import _audit_targets_color
    game = _game()
    blade = _card(game, card_db, "Doom Blade", 0, "hand")
    green = _card(game, card_db, "Tarmogoyf", 1, "battlefield")
    _audit_targets_color(game, blade, [green.instance_id])
    rejection = _card(game, card_db, "Ceremonious Rejection", 0, "hand")
    seer = _cast(game, card_db, "Thought-Knot Seer")
    _audit_targets_color(game, rejection, [seer.source.instance_id])
    assert "115.1/target_color_qualifier" not in _rules(audit)
