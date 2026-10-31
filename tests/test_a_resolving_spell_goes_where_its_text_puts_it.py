"""A resolving spell goes where its own text puts it (CR 608.2n).

"As the final part of an instant or sorcery spell's resolution, the spell
itself is put into its owner's graveyard" -- unless its own instructions
put it somewhere else: "Shuffle Green Sun's Zenith into its owner's
library", "Exile Alrund's Epiphany", "put it into its owner's library
seventh from the top". The effect grammar types each as a MOVE (or EXILE)
of the spell itself; the one owner of where a resolved spell goes
(`ResolutionManager._move_resolved_spell_off_stack`) reads it.

A spell that is countered, or that fizzles, performs none of its
instructions, so it goes to the graveyard. Flashback's exile (CR 702.34a)
and a copy ceasing to exist (CR 707.10a) still come first.

The stack-exit path never read the instruction: Green Sun's Zenith (Amulet
Titan x3) went to the graveyard, where it could not be found again and an
opponent's Territorial Kavu exiled it. The tutor's own self-shuffle rider
fired only for a card already in the graveyard, which a resolving spell
never is.

Card names are fixture carriers: 80 pool instants and sorceries move
themselves as they resolve.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.game_state import GameState, Phase


def _game():
    g = GameState(rng=random.Random(0))
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    g.turn_number = 5
    return g


def _put(game, card_db, name, zone, idx=0):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _lands(game, card_db, names):
    for n in names:
        _put(game, card_db, n, "battlefield")


def test_a_spell_that_shuffles_itself_into_its_library_goes_there(card_db):
    game = _game()
    _lands(game, card_db, ["Forest", "Forest"])
    for n in ("Island", "Arboreal Grazer", "Island"):
        _put(game, card_db, n, "library")
    gsz = _put(game, card_db, "Green Sun's Zenith", "hand")
    assert game.cast_spell(0, gsz)
    game.resolve_stack()
    p = game.players[0]
    assert gsz.zone == "library" and gsz in p.library
    assert gsz not in p.graveyard
    assert any(c.name == "Arboreal Grazer" for c in p.battlefield)


def test_a_damage_spell_that_shuffles_itself_goes_to_the_library(card_db):
    game = _game()
    _lands(game, card_db, ["Mountain"] * 6)
    beacon = _put(game, card_db, "Beacon of Destruction", "hand")
    assert game.cast_spell(0, beacon, targets=[PLAYER_TARGET_OPPONENT])
    game.resolve_stack()
    assert beacon.zone == "library"
    assert game.players[1].life == 15


def test_a_countered_spell_performs_no_instruction_and_goes_to_the_graveyard(
        card_db):
    from engine.spell_resolution import ResolutionManager
    from engine.stack import StackItem, StackItemType
    game = _game()
    gsz = CardInstance(template=card_db.get_card("Green Sun's Zenith"),
                       owner=0, controller=0,
                       instance_id=game.next_instance_id(), zone="stack")
    item = StackItem(item_type=StackItemType.SPELL, source=gsz, controller=0)
    ResolutionManager._move_countered_stack_item(game, item, gsz)
    assert gsz.zone == "graveyard" and gsz in game.players[0].graveyard


def test_flashback_exile_still_wins(card_db):
    from engine.spell_resolution import ResolutionManager
    game = _game()
    gsz = CardInstance(template=card_db.get_card("Green Sun's Zenith"),
                       owner=0, controller=0,
                       instance_id=game.next_instance_id(), zone="stack")
    gsz._cast_with_flashback = True
    ResolutionManager._move_resolved_spell_off_stack(game, gsz, resolved=True)
    assert gsz.zone == "exile"


# ── Auditor (CR 608.2n) ────────────────────────────────────────────────

def test_the_audit_records_a_resolved_spell_that_ignored_its_own_destination(
        card_db, monkeypatch):
    from engine import rules_audit
    from engine.spell_resolution import ResolutionManager
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    monkeypatch.setattr(ResolutionManager, "_own_destination",
                        staticmethod(lambda card: None))   # the defect
    game = _game()
    _lands(game, card_db, ["Forest", "Forest"])
    _put(game, card_db, "Arboreal Grazer", "library")
    gsz = _put(game, card_db, "Green Sun's Zenith", "hand")
    assert game.cast_spell(0, gsz)
    game.resolve_stack()
    assert "608.2n/resolved_spell_destination" in [
        f["rule"] for f in rules_audit.drain()]


def test_the_audit_is_silent_when_the_spell_goes_where_its_text_puts_it(
        card_db, monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    game = _game()
    _lands(game, card_db, ["Forest", "Forest", "Mountain"])
    _put(game, card_db, "Arboreal Grazer", "library")
    gsz = _put(game, card_db, "Green Sun's Zenith", "hand")
    assert game.cast_spell(0, gsz)
    game.resolve_stack()
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    assert game.cast_spell(0, bolt, targets=[PLAYER_TARGET_OPPONENT])
    game.resolve_stack()
    assert "608.2n/resolved_spell_destination" not in [
        f["rule"] for f in rules_audit.drain()]
