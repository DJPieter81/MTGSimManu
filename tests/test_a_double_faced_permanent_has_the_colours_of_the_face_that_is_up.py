"""A double-faced permanent has the colours of the face that is up (CR 712.8).

While a transforming double-faced permanent has its back face up, it has
only its back face's characteristics (CR 712.8). Its colour is among them;
a back face usually prints it as a colour indicator (CR 204), and MTGJSON
records it in that face's own `colors`. With its front face up, or off
the battlefield (CR 712.8a), it has its front face's. A colour-setting
effect still sets it (CR 105.2b, 613.1e).

The engine already reads a transformed permanent's card types, subtypes,
power, toughness and text from its back face. Colour was the one
characteristic it did not switch. The defect:
- a transformed Ajani, Nacatl Avenger read white, not red and white;
- Ral, Leyline Prodigy read red, not blue and red;
- Tamiyo, Seasoned Scholar read blue, not green and blue;
- a land or Eldrazi back face kept its front face's colour.

A second reader of "is this permanent coloured" read the printed template
colour directly, bypassing the face and any colour-setting effect
(`oracle_resolver._permanent_is_colored`).

Card names are fixture carriers. The class is the 93 pool transforming
double-faced cards whose faces differ in colour; four are in registered
decks (Ajani, Ral, Tamiyo, The Legend of Roku).
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, Color
from engine.game_state import GameState, Phase

AJANI = "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger"
AVACYN = "Archangel Avacyn // Avacyn, the Purifier"
ACLAZOTZ = "Aclazotz, Deepest Betrayal // Temple of the Dead"
TAMIYO = "Tamiyo, Inquisitive Student // Tamiyo, Seasoned Scholar"
RAL = "Ral, Monsoon Mage // Ral, Leyline Prodigy"

W, U, B, R, G = (Color.WHITE, Color.BLUE, Color.BLACK, Color.RED,
                 Color.GREEN)


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def _put(game, card_db, name, controller=0, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


# ── The face that is up ───────────────────────────────────────────────

@pytest.mark.parametrize("name, front, back", [
    (AJANI, {W}, {R, W}),
    (TAMIYO, {U}, {G, U}),
    (RAL, {R}, {U, R}),
    (AVACYN, {W}, {R}),
    (ACLAZOTZ, {B}, set()),
], ids=["indicator_adds_a_colour", "indicator_adds_green",
        "indicator_adds_blue", "indicator_replaces_the_colour",
        "colourless_back_face"])
def test_a_permanent_shows_the_colours_of_the_face_that_is_up(
        card_db, name, front, back):
    game = _game()
    perm = _put(game, card_db, name)
    assert perm.colors == front
    perm.is_transformed = True
    assert perm.colors == back


def test_the_back_face_colours_are_typed_at_load(card_db):
    """Read once, from the back face's own MTGJSON entry. A colourless
    back face is a value (no colours), not an absent one."""
    assert card_db.get_card(AJANI).back_face_colors == {R, W}
    assert card_db.get_card(ACLAZOTZ).back_face_colors == set()
    assert card_db.get_card("Lightning Bolt").back_face_colors is None


def test_a_colour_an_effect_sets_wins_over_either_face(card_db):
    game = _game()
    perm = _put(game, card_db, AJANI)
    perm.is_transformed = True
    perm.cem_colors_set = {G}
    assert perm.colors == {G}


def test_off_the_battlefield_it_shows_its_front_face_again(card_db):
    game = _game()
    perm = _put(game, card_db, AVACYN)
    perm.is_transformed = True
    assert perm.colors == {R}
    game.zone_mgr.move_card(game, perm, "battlefield", "graveyard",
                            cause="test")
    assert perm.colors == {W}


def test_a_permanent_transformed_in_place_shows_its_back_faces_colours(
        card_db):
    """Through the engine's transform (`oracle_resolver.
    _transform_permanent`), the path a coin flip or a threshold takes."""
    from engine.oracle_resolver import _transform_permanent
    game = _game()
    perm = _put(game, card_db, RAL)
    _transform_permanent(game, perm, 0)
    assert perm.is_transformed and perm.colors == {U, R}


def test_a_permanent_returned_transformed_shows_its_back_faces_colours(
        card_db):
    """Exiled and returned with its back face up (`return_transformed`,
    through the zone funnel): a new object, back face up."""
    from engine.oracle_resolver import _transform_permanent
    game = _game()
    perm = _put(game, card_db, AVACYN)
    _transform_permanent(game, perm, 0, returns_as_new_object=True)
    assert perm.zone == "battlefield" and perm.colors == {R}


# ── The readers ───────────────────────────────────────────────────────

def test_a_description_admits_a_transformed_permanent_by_its_shown_colour(
        card_db):
    """"a red permanent" (the description owner, `permanent_described`)."""
    from engine.effect_conditions import permanent_described
    from engine.effect_spec import CardFilter
    red = CardFilter(colors=frozenset({"R"}), controller="you")
    game = _game()
    ajani = _put(game, card_db, AJANI)
    assert not permanent_described(ajani, red)
    ajani.is_transformed = True
    assert permanent_described(ajani, red)


def test_a_permanent_is_coloured_by_the_colours_it_shows(card_db):
    """"Target permanent that's one or more colors": a back face that is
    colourless is not coloured, and neither is a permanent an effect made
    colourless."""
    from engine.oracle_resolver import _permanent_is_colored
    game = _game()
    aclazotz = _put(game, card_db, ACLAZOTZ)
    assert _permanent_is_colored(aclazotz)
    aclazotz.is_transformed = True
    assert not _permanent_is_colored(aclazotz)
    ajani = _put(game, card_db, AJANI)
    ajani.cem_colors_set = set()
    assert not _permanent_is_colored(ajani)


# ── Auditor (CR 712.8) ────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def test_the_audit_records_a_back_face_shown_in_its_front_faces_colours(
        card_db, audit, monkeypatch):
    from engine.oracle_resolver import _transform_permanent
    monkeypatch.setattr(CardInstance, "colors",                  # the defect
                        property(lambda self: set(self.template.colors)))
    game = _game()
    _transform_permanent(game, _put(game, card_db, AJANI), 0)
    assert "712.8/face_colours" in [f["rule"] for f in audit.drain()]


def test_the_audit_is_silent_when_the_back_face_shows_its_colours(
        card_db, audit):
    from engine.oracle_resolver import _transform_permanent
    game = _game()
    _transform_permanent(game, _put(game, card_db, AJANI), 0)
    _transform_permanent(game, _put(game, card_db, AVACYN), 0,
                         returns_as_new_object=True)
    assert "712.8/face_colours" not in [f["rule"] for f in audit.drain()]
