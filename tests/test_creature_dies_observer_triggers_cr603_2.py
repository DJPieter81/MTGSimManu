"""A permanent that watches OTHER creatures die ("whenever a/another
creature [you control] dies, <effect>") triggers when one does (CR 603.2).

The death funnel (`PermanentEffects._creature_dies`) resolved only the
DYING creature's own dies clause; nothing on either battlefield was ever
asked whether it watches creature deaths. Blade of the Bloodchief never
grew its bearer, so the Broodscale loop's first leg never existed
(2026-09-08 replays); the same held for every drain enchantment, every
"another creature you control dies" body.

Rule: the observer shape is typed once (`creature_dies_observer`: scope,
another, effect) and the funnel fans the death out to every observer on
either battlefield whose scope matches, after the dying creature has left
(CR 603.10 leaves-the-battlefield look-back is handled by dispatching
after the zone move). Effects executed: +1/+1 counter on the attached or
own creature (with a subtype bonus), drain, gain, draw; every other rider
is refused at parse time. Class: 36 non-creature observers plus the
creature ones. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


def test_the_observer_shape_is_typed_with_scope_and_effect():
    from engine.oracle_parser import parse_creature_dies_observer
    blade = parse_creature_dies_observer(
        "Whenever a creature dies, put a +1/+1 counter on equipped creature. "
        "If equipped creature is a Vampire, put two +1/+1 counters on it "
        "instead.\nEquip {1}")
    assert blade["scope"] == "any" and blade["another"] is False
    assert blade["kind"] == "counter_attached" and blade["amount"] == 1
    assert blade["subtype_bonus"] == ("vampire", 2)
    drain = parse_creature_dies_observer(
        "Whenever a creature you control dies, each opponent loses 1 life "
        "and you gain 1 life.")
    assert drain["scope"] == "you" and drain["kind"] == "drain"
    assert drain["amount"] == 1 and drain["gain"] == 1
    assert parse_creature_dies_observer(
        "Whenever a creature you control dies, each opponent sacrifices a "
        "creature of their choice.") is None, "an unmodelled rider is refused"
    assert parse_creature_dies_observer("When this creature dies, draw a card.") is None, (
        "the dying creature's OWN dies clause is not an observer")


def test_an_equipment_watching_deaths_grows_its_bearer_on_any_creatures_death(card_db):
    game = _game()
    bearer = _add(game, card_db, "Basking Broodscale", 0)
    blade = _add(game, card_db, "Blade of the Bloodchief", 0)
    assert game.attach_equipment(0, blade, bearer)
    victim = _add(game, card_db, "Memnite", 1)                # the OPPONENT's creature
    before = bearer.plus_counters
    game._creature_dies(victim)
    assert bearer.plus_counters == before + 1, (
        "a creature died — the equipped creature must get its +1/+1 counter")
    # The loop's second leg: the bearer's own counters-placed trigger fires.
    assert any(c.template.is_creature and c is not bearer
               for c in game.players[0].battlefield), (
        "the counters-placed trigger must see the counter and make its token")


def test_a_you_control_observer_ignores_the_opponents_deaths_and_drains_on_its_own(card_db):
    game = _game()
    _add(game, card_db, "Bastion of Remembrance", 0)
    mine = _add(game, card_db, "Memnite", 0)
    theirs = _add(game, card_db, "Memnite", 1)
    my_life, opp_life = game.players[0].life, game.players[1].life
    game._creature_dies(theirs)
    assert (game.players[0].life, game.players[1].life) == (my_life, opp_life), (
        "'a creature you control dies' does not trigger on the opponent's creature")
    game._creature_dies(mine)
    assert game.players[1].life == opp_life - 1 and game.players[0].life == my_life + 1
