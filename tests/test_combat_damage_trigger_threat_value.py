"""A connect-triggered value engine is a bigger threat than its raw body.

Root cause (post-sweep control-execution audit, 2026-08-25): `creature_threat_
value` credits *attack* triggers ("Whenever this creature attacks, …") as
virtual power, but had no term at all for *combat-damage* triggers ("Whenever
this creature deals combat damage to a player, …"). Those are a distinct oracle
shape — `parse_has_attack_trigger` correctly returns False for them — so every
"connects → draw a card / make a Treasure / steal a card" creature was valued
as a vanilla body. 331 Modern creatures carry this trigger.

Consequence: removal priority and block decisions systematically under-rate the
cheap evasive value engines that power aggro/tempo decks. A control deck holding
spot removal scored such a creature below its big-threat floor and declined to
kill it, then lost to the card advantage it generated every turn.

Both shapes are per-combat recurring value, so a combat-damage trigger reuses
the SAME virtual-power amplifier constant as an attack trigger — no new
tunable. Combat-damage triggers are strictly narrower (they require connecting),
so equal magnitude is conservative rather than inflationary.

Rule under test: a creature with a combat-damage-to-player trigger scores
strictly above an identically-statted creature without one. Mechanic-driven
(oracle trigger shape), no card names in the assertions.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.oracle_parser import parse_has_combat_damage_trigger
from ai.ev_evaluator import snapshot_from_game, creature_threat_value

_DB = CardDatabase()


def _add(game, name, controller, zone="battlefield"):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 4
    game.players[0].deck_name = "4/5c Control"
    game.players[1].deck_name = "Domain Zoo"
    game.players[0].life = 16
    game.players[1].life = 20
    return game


# ── Parser-level (Pattern C: pure oracle unit tests) ────────────────────

def test_parser_detects_self_referential_combat_damage_trigger():
    assert parse_has_combat_damage_trigger(
        "Whenever this creature deals combat damage to a player, draw a card.")


def test_parser_detects_self_named_combat_damage_trigger():
    assert parse_has_combat_damage_trigger(
        "Whenever Ragavan deals combat damage to a player, create a Treasure "
        "token.", "Ragavan, Nimble Pilferer")


def test_parser_rejects_other_creatures_combat_damage_trigger():
    # "Whenever a creature you control deals combat damage" is a trigger on a
    # DIFFERENT card firing off someone else's combat — not this body's own
    # recurring value. Same discriminator parse_has_attack_trigger uses.
    assert not parse_has_combat_damage_trigger(
        "Whenever a creature you control deals combat damage to a player, "
        "you gain 1 life.")


def test_parser_rejects_plain_attack_trigger():
    # An attack trigger is already credited by parse_has_attack_trigger; this
    # parser must not double-claim it.
    assert not parse_has_combat_damage_trigger(
        "Whenever this creature attacks, create a 1/1 Goblin token.")


# ── Valuation-level ────────────────────────────────────────────────────

def test_connect_triggered_value_engine_outscores_vanilla_body():
    game = _game()
    # A 2/1 whose combat damage generates recurring card advantage.
    engine_creature = _add(game, "Ragavan, Nimble Pilferer", 1)
    assert engine_creature.template.has_combat_damage_trigger, (
        "fixture premise: this creature's trigger is a combat-damage trigger")
    assert not engine_creature.template.has_attack_trigger, (
        "fixture premise: it is NOT an attack trigger — that is the whole "
        "point; the existing credit does not cover this shape")
    snap = snapshot_from_game(game, 0)
    engine_value = creature_threat_value(engine_creature, snap)
    game.players[1].battlefield.remove(engine_creature)

    # A vanilla body of comparable size, no recurring trigger.
    vanilla = _add(game, "Doorkeeper Thrull", 1)
    assert not vanilla.template.has_combat_damage_trigger
    snap2 = snapshot_from_game(game, 0)
    vanilla_value = creature_threat_value(vanilla, snap2)

    assert engine_value > vanilla_value, (
        f"a creature that converts combat damage into recurring card advantage "
        f"must outscore a vanilla body ({engine_value:.2f} vs "
        f"{vanilla_value:.2f})")


def test_combat_damage_trigger_adds_threat_over_same_creature_without_it():
    """The credit is attributable to the trigger itself, not to other terms."""
    game = _game()
    c = _add(game, "Ragavan, Nimble Pilferer", 1)
    snap = snapshot_from_game(game, 0)
    with_trigger = creature_threat_value(c, snap)
    # Suppress only the trigger flag; every other characteristic is identical.
    object.__setattr__(c.template, "has_combat_damage_trigger", False)
    try:
        without_trigger = creature_threat_value(c, snap)
    finally:
        object.__setattr__(c.template, "has_combat_damage_trigger", True)
    assert with_trigger > without_trigger, (
        f"removing the combat-damage trigger must lower the threat value "
        f"({with_trigger:.2f} vs {without_trigger:.2f}) — otherwise the credit "
        f"is not wired to the trigger")
