"""Only a printed cast-prevention static stops casting from a graveyard.

CR 601.3 / the Grafdigger's Cage clause: "Players can't cast spells from
graveyards or libraries." That is a STATIC ABILITY carried by four
Modern-legal permanents. It is a different mechanic from graveyard
REMOVAL — an ability that exiles cards out of a graveyard (Tormod's
Crypt, Relic of Progenitus, Scavenging Ooze, Soul-Guide Lantern, …)
removes the fuel but never bans the cast, and it does nothing at all
while it merely sits on the battlefield.

The engine conflated the two: `CastManager.can_cast`'s graveyard/library
gate read `CardTemplate.has_graveyard_hate`, a deliberately BROAD
predicate ("exile … graveyard" anywhere in the oracle) whose job is
sideboard advice, not rules enforcement. 446 Modern permanents satisfy
it; only 4 print the static. Every one of the other 442 acted as a
symmetric, permanent Grafdigger's Cage the moment it hit the
battlefield — switching off flashback and escape for BOTH players,
including its own controller.

Rules pinned here:
  * the gate reads a narrow, parse-once cast-prevention predicate;
  * a graveyard-REMOVAL permanent on the battlefield leaves flashback
    and escape casting legal for both players;
  * a printed cast-prevention static still stops both;
  * the narrow predicate is a strict subset of the broad hate
    predicate, so nothing that used to be gated and should be gated
    stopped being gated.

Card names in test bodies are fixture carriers only — the rule is about
the two mechanics, not about any one permanent.
"""
from __future__ import annotations

import random

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase
from engine.oracle_parser import (parse_has_graveyard_hate,
                                  parse_prevents_graveyard_casting)

_DB = CardDatabase()


def _add(game, name, controller=0, zone="battlefield"):
    tmpl = _DB.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    # Mirror `GameState.setup_game`: innate flashback is an INSTANCE flag
    # set when the library is built from templates, not a template field.
    if 'flashback' in (tmpl.tags or set()):
        card.has_flashback = True
    getattr(game.players[controller], zone).append(card)
    return card


def _game(lands=6, second_land=None):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    for _ in range(lands):
        _add(game, "Swamp")
    for _ in range(lands if second_land else 0):
        _add(game, second_land)
    return game


# ── the predicate itself ──────────────────────────────────────────────

def test_cast_prevention_predicate_matches_only_the_printed_static():
    """"Players can't cast spells from graveyards" is the whole rule.
    An exile-the-graveyard ability is a different mechanic."""
    assert parse_prevents_graveyard_casting(
        "Creature cards in graveyards and libraries can't enter the "
        "battlefield.\nPlayers can't cast spells from graveyards or "
        "libraries.") is True
    assert parse_prevents_graveyard_casting(
        "{T}, Sacrifice this artifact: Exile target player's "
        "graveyard.") is False
    assert parse_prevents_graveyard_casting(
        "{T}: Exile target card from a graveyard.") is False
    assert parse_prevents_graveyard_casting("") is False
    assert parse_prevents_graveyard_casting(None) is False


def test_cast_prevention_is_a_strict_subset_of_graveyard_hate():
    """The narrow predicate never widens the gate: everything it
    catches the broad predicate caught too, and it catches far less."""
    narrow, broad = set(), set()
    for name, tmpl in _DB.cards.items():
        oracle = tmpl.oracle_text or ""
        if parse_prevents_graveyard_casting(oracle):
            narrow.add(name)
        if parse_has_graveyard_hate(oracle):
            broad.add(name)
    assert narrow <= broad, sorted(narrow - broad)
    # The class is the printed Cage family — small by construction.
    assert 0 < len(narrow) < len(broad) / 10


# ── the gate ──────────────────────────────────────────────────────────

def test_graveyard_removal_permanent_does_not_prevent_flashback_casting():
    """An exile-the-graveyard permanent removes fuel when ACTIVATED; it
    never bans the cast while it sits there."""
    game = _game()
    fb = _add(game, "Lingering Souls", 0, "graveyard")
    assert fb.template.flashback_cost is not None
    assert CastManager.can_cast(game, 0, fb) is True

    _add(game, "Tormod's Crypt", 1)      # opponent's removal permanent
    assert CastManager.can_cast(game, 0, fb) is True, (
        "a graveyard-removal permanent is not a cast-prevention static")


def test_graveyard_removal_permanent_does_not_prevent_own_controller_casting():
    """The old gate was symmetric: an Affinity player's own hate
    artifact switched off their own graveyard as well."""
    game = _game()
    fb = _add(game, "Lingering Souls", 0, "graveyard")
    _add(game, "Nihil Spellbomb", 0)     # our OWN removal permanent
    assert CastManager.can_cast(game, 0, fb) is True


def test_printed_cast_prevention_static_still_stops_graveyard_casting():
    game = _game()
    fb = _add(game, "Lingering Souls", 0, "graveyard")
    assert CastManager.can_cast(game, 0, fb) is True
    _add(game, "Grafdigger's Cage", 1)
    assert CastManager.can_cast(game, 0, fb) is False


def test_printed_cast_prevention_static_stops_escape_casting():
    """Escape is the other graveyard-cast route the gate must cover."""
    game = _game(lands=4, second_land="Mountain")
    esc = _add(game, "Kroxa, Titan of Death's Hunger", 0, "graveyard")
    assert esc.template.escape_cost is not None
    for _ in range(esc.template.escape_exile_count + 1):
        _add(game, "Swamp", 0, "graveyard")
    assert CastManager.can_cast(game, 0, esc) is True
    _add(game, "Grafdigger's Cage", 0)
    assert CastManager.can_cast(game, 0, esc) is False


def test_template_carries_the_cast_prevention_flag_as_a_typed_field():
    """Parse-once: the gate reads a typed field, never oracle text."""
    cage = _DB.get_card("Grafdigger's Cage")
    crypt = _DB.get_card("Tormod's Crypt")
    assert cage.prevents_graveyard_casting is True
    assert crypt.prevents_graveyard_casting is False
    # The broad sideboard-advice predicate is unchanged for both.
    assert cage.has_graveyard_hate is True
    assert crypt.has_graveyard_hate is True
