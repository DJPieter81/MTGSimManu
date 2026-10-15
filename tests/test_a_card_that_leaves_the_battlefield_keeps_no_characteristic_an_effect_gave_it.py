"""A card that leaves the battlefield keeps no characteristic a continuous
effect gave the permanent (CR 400.7, 611.3a, 613.1).

A continuous effect from a static ability applies to the objects it names
while they are permanents ("each nonland permanent you control is all
colors", "creatures you control get +1/+1", "... have flying"). A card
that leaves the battlefield is a new object with no memory of its previous
existence (CR 400.7): in a hand, on the stack, in a graveyard or in exile
its colour, power, toughness, keywords and land types are its printed ones
until an effect that names it there says otherwise.

The continuous-effect layer writes these characteristics on battlefield
objects only (`ContinuousEffectsManager.recalculate`); the zone funnel
(`ZoneManager._cleanup_leaving_battlefield`) clears every one of them as a
permanent leaves. The auditor `400.7/effect_characteristic_off_battlefield`
restates the rule from the raw fields of every object off the battlefield
at each recalculation.

The defect (td-post, Domain Zoo vs 4/5c Control s51000 game 2 T3): a
Ragavan dashed under two Leyline of the Guildpact went back to its owner's
hand still all colours; recast, it was blue on the stack, and Mystical
Dispute was discounted for a blue target. The funnel cleared the types an
effect added (unit AT) but no other characteristic the layer writes.

Card names are fixture carriers: the class is every characteristic the
continuous-effect layer writes, on every card that leaves the battlefield.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, Color, Keyword
from engine.continuous_effects import ALL_COLORS
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 1
    game.current_phase = Phase.MAIN1
    return game


def _bounce(game, card):
    assert game.zone_mgr.move_card(game, card, "battlefield", "hand",
                                   cause="test")
    game.continuous_effects.recalculate(game)


# ── The zone funnel: every characteristic the layer writes ────────────

def test_a_permanent_that_leaves_drops_every_characteristic_an_effect_wrote(
        card_db):
    """Synthetic: the fields the continuous-effect layer writes, set as a
    recalculation would, are all gone once the card leaves the battlefield."""
    game = _game()
    bears = _add(game, card_db, "Grizzly Bears", 1)
    bears.cem_power_mod = 1
    bears.cem_toughness_mod = 1
    bears.cem_keywords.add(Keyword.FLYING)
    bears.cem_colors_set = ALL_COLORS
    bears.cem_land_type_set = "mountain"
    bears.cem_land_types_added = {"island"}
    bears.cem_types_added = {"artifact"}
    bears.cem_subtypes_added = {"Bird"}
    assert game.zone_mgr.move_card(game, bears, "battlefield", "graveyard",
                                   cause="test")
    assert (bears.cem_power_mod, bears.cem_toughness_mod) == (0, 0)
    assert not bears.cem_keywords
    assert bears.cem_colors_set is None and bears.cem_land_type_set is None
    assert not bears.cem_land_types_added
    assert not bears.cem_types_added and not bears.cem_subtypes_added
    assert (bears.power, bears.toughness) == (2, 2)
    assert Keyword.FLYING not in bears.keywords
    assert bears.colors == {Color.GREEN}


def test_a_card_returned_to_hand_from_under_an_all_colours_static_is_its_printed_colour(
        card_db):
    """The real path: the static writes all colours on the permanent; in
    its owner's hand the card is green again."""
    game = _game()
    _add(game, card_db, "Leyline of the Guildpact", 1)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    game.continuous_effects.recalculate(game)
    assert bears.colors == set(ALL_COLORS), "a nonland permanent you control"
    _bounce(game, bears)
    assert bears.zone == "hand" and bears.colors == {Color.GREEN}


def test_a_card_returned_to_hand_from_under_an_anthem_has_its_printed_power_and_keywords(
        card_db):
    """A static that gives your creatures +1/+1 and flying (the layer's own
    factory, as a resolving lord registers it)."""
    from engine.continuous_effects import create_lord_effect
    game = _game()
    anthem = _add(game, card_db, "Glorious Anthem", 1)
    for effect in create_lord_effect(
            source_id=anthem.instance_id, source_name=anthem.name,
            affected_fn=lambda g, c: c.controller == 1 and c.template.is_creature,
            power_bonus=1, toughness_bonus=1,
            keyword_grants={Keyword.FLYING}, description="test anthem"):
        game.continuous_effects.register(effect)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    game.continuous_effects.recalculate(game)
    assert (bears.power, bears.toughness) == (3, 3)
    assert Keyword.FLYING in bears.keywords
    _bounce(game, bears)
    assert (bears.power, bears.toughness) == (2, 2)
    assert Keyword.FLYING not in bears.keywords


def test_a_recast_spell_is_its_printed_colour_for_a_target_discount(card_db):
    """End to end, the defect's shape: a green creature that was all
    colours as a permanent is cast again; "costs {2} less if it targets a
    blue spell" does not apply to it (Mystical Dispute stays {2}{U})."""
    game = _game()
    for p in (0, 1):
        for _ in range(5):
            _add(game, card_db, "Island", p, "library")
    _add(game, card_db, "Leyline of the Guildpact", 1)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    game.continuous_effects.recalculate(game)
    _bounce(game, bears)
    for _ in range(2):
        _add(game, card_db, "Forest", 1)
    assert game.cast_spell(1, bears, [])
    assert bears.zone == "stack"
    _add(game, card_db, "Island", 0)
    dispute = _add(game, card_db, "Mystical Dispute", 0, "hand")
    assert not game.can_cast(0, dispute), "{2}{U} against a green spell"


# ── The auditor (CR 400.7) ────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain()
            if f["rule"] == "400.7/effect_characteristic_off_battlefield"]


def _leyline_bounce(card_db):
    game = _game()
    _add(game, card_db, "Leyline of the Guildpact", 1)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    game.continuous_effects.recalculate(game)
    _bounce(game, bears)
    return bears


def test_the_audit_is_silent_when_a_card_leaves_with_nothing_an_effect_gave_it(
        card_db, audit):
    _leyline_bounce(card_db)
    assert not _findings(audit)


def test_the_audit_records_a_card_off_the_battlefield_that_keeps_a_colour(
        card_db, audit, monkeypatch):
    """Broken the way the defect was: the funnel clears the added types
    and nothing else the layer wrote."""
    from engine.zone_manager import ZoneManager
    real = ZoneManager._cleanup_leaving_battlefield

    def _types_only(self, card):
        kept = card.cem_colors_set
        real(self, card)
        card.cem_colors_set = kept
    monkeypatch.setattr(ZoneManager, "_cleanup_leaving_battlefield",
                        _types_only)
    bears = _leyline_bounce(card_db)
    assert bears.colors == set(ALL_COLORS)
    assert _findings(audit)
