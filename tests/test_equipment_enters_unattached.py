"""Equipment enters the battlefield unattached (CR 301.5c).

Rule under test
---------------
CR 301.5c: an Equipment can only become attached to a creature by an
effect that says so — most commonly its own equip ability (CR 702.6).
Therefore EVERY Equipment permanent that enters the battlefield does so
*unattached*, and the game state must say so.

The simulator records that fact as the ``equipment_unattached``
instance tag.  Two consumers read it and nothing else:

  * ``ai/ev_player.py::_consider_equip`` — enumerates equip plays only
    for battlefield equipment carrying the tag.  No tag, no equip play
    is ever offered to the planner.
  * ``ai/permanent_threat.py::_equipment_unattached`` — values a
    stranded equipment differently from an attached one.

Before this test, the tag was written by exactly ONE place: a
card-name-keyed ``EFFECT_REGISTRY`` ETB handler.  That made the rule
true for one printing and silently false for every other Equipment in
the format: the AI could cast an Equipment, see it resolve, and then
never be able to equip it for the rest of the game — the ability was
legal in the engine and simply never enumerated.

Class size: every Equipment card in Modern (hundreds of printings), not
one card.  The condition is a typed field parsed once at DB load
(``CardTemplate.equip_cost``) plus the Equipment subtype — no card name
is compared anywhere in the rule or in this test's assertions.

Related mechanic already implemented elsewhere: when an equipped
creature leaves the battlefield, ``engine/permanent_effects.py`` puts
the tag BACK on the equipment.  The "falls off" half of the mechanic
was generic; only the "enters" half was card-name gated.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType
from engine.game_state import GameState


def _equipment_templates(card_db, limit=8):
    """Return Equipment templates straight from the DB.

    Selected by MECHANIC — an artifact whose subtypes include Equipment
    and whose oracle parsed an equip cost — never by name, so the test
    covers whatever Equipment the card pool happens to contain.
    """
    found = []
    for name in card_db.cards:
        tmpl = card_db.get_card(name)
        if tmpl is None:
            continue
        if CardType.ARTIFACT not in tmpl.card_types:
            continue
        if "Equipment" not in (tmpl.subtypes or []):
            continue
        if getattr(tmpl, "equip_cost", None) is None:
            continue
        found.append(tmpl)
        if len(found) >= limit:
            break
    return found


def _new_game(card_db):
    game = GameState(rng=random.Random(1234))
    game.card_db = card_db
    return game


def _resolve_onto_battlefield(game, tmpl, controller=0):
    """Put a permanent onto the battlefield through the ETB pipeline."""
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    game.players[controller].battlefield.append(card)
    card.enter_battlefield()
    game.trigger_etb(card, controller)
    return card


def test_equipment_enters_battlefield_marked_unattached(card_db):
    """Every Equipment entering play carries the unattached marker."""
    game = _new_game(card_db)
    templates = _equipment_templates(card_db)
    assert templates, "card pool contains no Equipment — test is vacuous"

    for tmpl in templates:
        card = _resolve_onto_battlefield(game, tmpl)
        assert "equipment_unattached" in card.instance_tags, (
            f"{tmpl.name} entered the battlefield without the unattached "
            f"marker — the AI can never enumerate an equip play for it"
        )
        assert "equipment_attached" not in card.instance_tags


def test_unattached_marker_does_not_depend_on_a_card_specific_handler(card_db):
    """The marker is set for Equipment with no ETB registry entry.

    This is the discriminator between "the mechanic is implemented" and
    "one card happens to have a handler that writes the tag".
    """
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming

    game = _new_game(card_db)
    unhandled = [
        t for t in _equipment_templates(card_db, limit=40)
        if not EFFECT_REGISTRY.has_handler(t.name, EffectTiming.ETB)
    ]
    assert unhandled, (
        "every Equipment in the pool has a card-specific ETB handler — "
        "the generic path cannot be exercised"
    )
    for tmpl in unhandled:
        card = _resolve_onto_battlefield(game, tmpl)
        assert "equipment_unattached" in card.instance_tags, tmpl.name


def test_attaching_then_re_entering_does_not_resurrect_a_stale_marker(card_db):
    """An equipment already attached must not be re-marked unattached.

    Some Equipment attach themselves on entry (they create a token and
    attach to it).  Those run their attach effect BEFORE the generic ETB
    fan-out, so the fan-out must not clobber the attachment.
    """
    game = _new_game(card_db)
    templates = _equipment_templates(card_db, limit=1)
    tmpl = templates[0]
    card = _resolve_onto_battlefield(game, tmpl)

    # Simulate the "already attached on entry" shape.
    card.instance_tags.discard("equipment_unattached")
    card.instance_tags.add("equipment_attached")
    game.trigger_etb(card, 0)

    assert "equipment_unattached" not in card.instance_tags
    assert "equipment_attached" in card.instance_tags


def test_ai_enumerates_an_equip_play_for_a_freshly_resolved_equipment(card_db):
    """End-to-end: the marker makes the equip play reachable by the AI.

    Engine legality is not enough — the planner must actually see the
    play.  This asserts the consumer side of the tag, which is where the
    original failure was observable (equipment resolved, never equipped).
    """
    from ai.ev_player import EVPlayer

    game = _new_game(card_db)
    controller = 0

    # A vanilla creature to carry it, already able to be equipped.
    creature_tmpl = None
    for name in card_db.cards:
        t = card_db.get_card(name)
        if t is None or CardType.CREATURE not in t.card_types:
            continue
        if (t.power or 0) >= 1 and (t.mana_cost.cmc or 0) <= 2:
            creature_tmpl = t
            break
    assert creature_tmpl is not None

    creature = CardInstance(
        template=creature_tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    creature._game_state = game
    game.players[controller].battlefield.append(creature)
    creature.enter_battlefield()
    creature.summoning_sick = False

    # Untapped lands so the equip cost is affordable.
    land_tmpl = card_db.get_card("Mountain")
    for _ in range(4):
        land = CardInstance(
            template=land_tmpl, owner=controller, controller=controller,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        land._game_state = game
        game.players[controller].battlefield.append(land)
        land.enter_battlefield()

    tmpl = _equipment_templates(card_db, limit=1)[0]
    equipment = _resolve_onto_battlefield(game, tmpl, controller)

    ai = EVPlayer(controller, deck_name="Izzet Prowess")
    play = ai._consider_equip(game, game.players[controller])
    assert play is not None, (
        f"AI enumerated no equip play for {equipment.name} despite an "
        f"untapped board and a legal carrier"
    )
    assert play.action == "equip"
