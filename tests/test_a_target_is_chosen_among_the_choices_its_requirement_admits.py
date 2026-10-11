"""A spell's target is chosen among the choices its requirement admits
(CR 601.2c, 115.1): castability, the legal-target enumeration and the cast
read one predicate, and a modal spell's chosen mode reads only the targets
chosen for it (CR 700.2a).

* CR 601.2c: a target is chosen among the legal choices for its
  requirement -- its zone, types, owner and printed bounds ("creature card
  with mana value 3 or less"). A spell with no legal choice for a required
  target cannot be cast.
* CR 700.2a: a mode's targets are chosen for that mode; a chosen mode that
  targets nothing has none.

The defects: castability (`target_solver.has_legal_target`) read no printed
mana-value bound, while the enumeration of legal targets did. With only a
mana value 8 creature card in its caster's graveyard, a "mana value 3 or
less" reanimation spell was castable; the AI aimed it at that card, and it
resolved to nothing (Grixis Reanimator's Unearth at Archon of Cruelty). The
cast accepted any target the AI chose: a census of 96 games found 67 casts
aimed at objects their requirement does not admit. A chosen mode that
targets nothing was handed the spell's whole target list, and the carrier
refused it (Lorehold Charm's edict mode: `unhandled/mode`, 23 rows).

Card names are fixture carriers: 40 pool cards bound a returned card by
mana value, and every targeted spell reads the predicate.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


def _add(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    if zone != "stack":
        getattr(game.players[controller], zone).append(card)
    return card


def _reanimation_board(card_db, *graveyard):
    """Player 0 holds a "mana value 3 or less" reanimation spell with a
    Swamp to cast it, in a main phase of its own turn."""
    from engine.game_state import Phase
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    _add(game, card_db, "Swamp", 0, "battlefield")
    spell = _add(game, card_db, "Unearth", 0, "hand")
    dead = [_add(game, card_db, name, 0, "graveyard") for name in graveyard]
    return game, spell, dead


def test_castability_reads_the_requirements_printed_bound(card_db):
    from engine.target_solver import enumerate_legal_targets, has_legal_target
    game, spell, (archon,) = _reanimation_board(card_db, "Archon of Cruelty")
    req = spell.template.effects.spell(0).targets[0]
    assert not enumerate_legal_targets(game, 0, req, exclude=spell,
                                       source=spell)
    assert not has_legal_target(game, 0, req, exclude=spell, source=spell), \
        "castability and the enumeration read one predicate"
    assert not game.can_cast(0, spell)
    game2, spell2, _ = _reanimation_board(card_db, "Archon of Cruelty",
                                          "Abhorrent Oculus")
    assert game2.can_cast(0, spell2)


def test_a_spell_is_not_cast_at_a_target_its_requirement_does_not_admit(card_db):
    game, spell, (archon, oculus) = _reanimation_board(
        card_db, "Archon of Cruelty", "Abhorrent Oculus")
    assert not game.cast_spell(0, spell, [archon.instance_id])
    assert spell in game.players[0].hand, "the cast is refused whole"
    assert not game.players[0].battlefield[0].tapped, "nothing was paid"
    assert game.cast_spell(0, spell, [oculus.instance_id])


def test_the_ai_aims_a_bounded_reanimation_at_a_card_it_may_return(card_db):
    from ai.ev_player import EVPlayer
    game, spell, (archon, oculus) = _reanimation_board(
        card_db, "Archon of Cruelty", "Abhorrent Oculus")
    ai = EVPlayer(player_idx=0, deck_name="Grixis Reanimator",
                  rng=random.Random(0))
    assert ai._choose_targets(game, spell) == [oculus.instance_id]
    game2, spell2, _ = _reanimation_board(card_db, "Archon of Cruelty")
    assert ai._choose_targets(game2, spell2) == [], \
        "no card the spell may return: no target"


def test_a_chosen_mode_that_targets_nothing_reads_no_target(card_db):
    from engine import modal_spell
    game = GameState(rng=random.Random(0))
    game.active_player = 1
    stray = _add(game, card_db, "Hollow One", 1, "graveyard")
    relic = _add(game, card_db, "Mind Stone", 0, "battlefield")
    charm = _add(game, card_db, "Lorehold Charm", 1, "stack")
    assert modal_spell.mode_targets(game, charm, 1, [stray.instance_id], 0,
                                    {}) == []
    game._execute_spell_effects(StackItem(
        item_type=StackItemType.SPELL, source=charm, controller=1,
        targets=[stray.instance_id], modes_chosen=[0], mode_players={}))
    assert relic.zone == "graveyard", \
        "each opponent sacrifices a nontoken artifact"


def test_the_ai_aims_removal_only_at_a_permanent_its_requirement_admits(card_db):
    """An exile spell for "artifact, creature, or enchantment" passes over
    a planeswalker, however threatening, for a creature it may exile; a
    graveyard-card exile has no battlefield target at all."""
    from ai.ev_player import EVPlayer
    from engine.game_state import Phase
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(3):
        _add(game, card_db, "Plains", 0, "battlefield")
    _add(game, card_db, "Wrenn and Six", 1, "battlefield")
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    march = _add(game, card_db, "March of Otherworldly Light", 0, "hand")
    cling = _add(game, card_db, "Cling to Dust", 0, "hand")
    ai = EVPlayer(player_idx=0, deck_name="Azorius Control",
                  rng=random.Random(0))
    assert ai._choose_targets(game, march) == [bears.instance_id]
    assert all(game.get_card_by_id(t).zone == "graveyard"
               for t in ai._choose_targets(game, cling))


def test_a_permanent_is_admitted_by_the_types_it_has_now(card_db):
    """On the battlefield a permanent's types are its current ones (CR
    613.1d, 712.8): a transformed saga that is now a creature is a legal
    "target creature"; its front face's types are not what is checked."""
    from engine.cast_manager import CastManager
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    fable = _add(game, card_db,
                 "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki", 1,
                 "battlefield")
    spell = _add(game, card_db, "Dismember", 0, "hand")
    assert not CastManager.targets_admitted(game, 0, spell,
                                            [fable.instance_id])
    fable.is_transformed = True
    assert CastManager.targets_admitted(game, 0, spell, [fable.instance_id])


import pytest


@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _admission_findings(audit):
    return [f for f in audit.drain()
            if f["rule"] == "601.2c/cast_target_admitted"]


def test_the_audit_records_a_cast_at_a_target_no_requirement_admits(
        card_db, audit, monkeypatch):
    from engine.cast_manager import CastManager
    monkeypatch.setattr(CastManager, "targets_admitted",       # the defect
                        staticmethod(lambda *a, **k: True))
    # Since unit RT the cast also checks that each required target is among
    # the chosen ones; the inadmissible target answers none.
    monkeypatch.setattr(CastManager, "announced_targets",
                        staticmethod(lambda g, p, c, t: list(t or [])))
    game, spell, (archon, _) = _reanimation_board(
        card_db, "Archon of Cruelty", "Abhorrent Oculus")
    assert game.cast_spell(0, spell, [archon.instance_id])
    assert _admission_findings(audit)


def test_the_audit_is_silent_when_the_target_is_admitted(card_db, audit):
    game, spell, (_, oculus) = _reanimation_board(
        card_db, "Archon of Cruelty", "Abhorrent Oculus")
    assert game.cast_spell(0, spell, [oculus.instance_id])
    assert not _admission_findings(audit)


def test_the_audit_reads_a_transformed_permanents_current_face(card_db, audit):
    """A spell cast at a transformed permanent that is now a creature is
    admitted (CR 712.8): the audit reads the face it shows, not its printed
    front face."""
    from engine.game_state import Phase
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(2):
        _add(game, card_db, "Swamp", 0, "battlefield")
    fable = _add(game, card_db,
                 "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki", 1,
                 "battlefield")
    fable.is_transformed = True
    spell = _add(game, card_db, "Dismember", 0, "hand")
    assert game.cast_spell(0, spell, [fable.instance_id])
    assert not _admission_findings(audit)
