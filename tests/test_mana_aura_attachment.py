"""A mana-granting Aura attaches to a land and increases what it taps for.

Root cause (Eldrazi ramp mana audit, 2026-08-25): the engine had NO Aura
attachment support of any kind — `grep` for aura/attachment state returned
nothing outside Equipment. A mana Aura therefore resolved onto the battlefield
as an inert enchantment with `mana_units == []`, granting nothing. Measured on
a live board: three Forests plus a mana Aura still produced exactly 3 mana, and
a ramp deck cast a *third* copy at ~10 mana because none of them did anything.

Aura is a large mechanic in its own right (786 Modern cards); this change adds
the attachment primitive plus its first consumer, mana granting (12 cards).

Rules modelled:
  * CR 303.4a — an Aura spell requires a legal object to enchant, chosen by its
    "Enchant <quality>" ability.
  * CR 303.4c/704.5m — an Aura attached to an illegal or absent object is put
    into its owner's graveyard.

Rule under test: a mana Aura attaches to a legal land its controller controls,
and that land's mana units grow by the granted amount. Mechanic-driven (oracle
"Enchant …" + "whenever enchanted … is tapped for mana"), no card names
asserted.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.oracle_parser import (
    parse_aura_enchant_restriction,
    parse_aura_mana_units,
)

_DB = CardDatabase()


# ── Parser-level (Pattern C: pure oracle unit tests) ───────────────────

def test_enchant_restriction_parsed_for_a_land_aura():
    assert parse_aura_enchant_restriction("Enchant land\nSome text.") == "land"


def test_enchant_restriction_parsed_for_a_subtype_restricted_aura():
    assert parse_aura_enchant_restriction(
        "Enchant Forest\nAs this Aura enters, choose a color.") == "forest"


def test_enchant_restriction_absent_when_not_an_aura():
    assert parse_aura_enchant_restriction("{T}: Add {G}.") is None


def test_mana_aura_grant_parsed_as_units():
    units = parse_aura_mana_units(
        "Whenever enchanted land is tapped for mana, its controller adds an "
        "additional one mana of any color.")
    assert units and len(units) == 1, f"expected one granted unit, got {units}"
    assert set(units[0]) == {"W", "U", "B", "R", "G"}


def test_mana_aura_fixed_symbol_grant():
    units = parse_aura_mana_units(
        "Whenever enchanted Forest is tapped for mana, its controller adds an "
        "additional {G}.")
    assert units == [["G"]], f"expected one green unit, got {units}"


def test_non_mana_aura_grants_no_units():
    assert parse_aura_mana_units(
        "Enchanted creature gets +2/+2.") is None


# ── Attachment + mana behaviour ────────────────────────────────────────

def _game_with_lands(n_forests):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 4
    p = game.players[0]
    p.deck_name = "Eldrazi Ramp"
    game.players[1].deck_name = "Dimir Midrange"
    for _ in range(n_forests):
        t = _DB.get_card("Forest")
        c = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(),
                         zone="battlefield")
        c._game_state = game
        c.enter_battlefield()
        p.battlefield.append(c)
    return game, p


def _play_aura(game, name):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    aura = CardInstance(template=t, owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="stack")
    aura._game_state = game
    game.resolve_permanent_to_battlefield(0, aura) if hasattr(
        game, "resolve_permanent_to_battlefield") else None
    return aura


def test_mana_aura_increases_its_hosts_mana_capacity():
    from engine.mana_payment import ManaPayment
    from engine.permanent_effects import PermanentEffects

    game, p = _game_with_lands(3)
    before = p.untapped_mana_capacity()
    assert before == 3, f"fixture premise: three Forests give 3 mana, got {before}"

    t = _DB.get_card("Utopia Sprawl")
    aura = CardInstance(template=t, owner=0, controller=0,
                        instance_id=game.next_instance_id(),
                        zone="battlefield")
    aura._game_state = game
    aura.enter_battlefield()
    p.battlefield.append(aura)
    attached = PermanentEffects.attach_aura(game, aura, 0)
    assert attached is not None, (
        "a mana Aura must find a legal host land to enchant (CR 303.4a)")

    after = p.untapped_mana_capacity()
    assert after > before, (
        f"the enchanted land must tap for more mana with the Aura attached "
        f"({before} -> {after})")
    # And the host land itself reports the extra unit.
    units = ManaPayment.land_mana_units(game, 0, attached)
    assert len(units) >= 2, (
        f"the host land should expose its own unit plus the granted one; "
        f"got {units}")


def test_mana_aura_with_no_legal_host_does_not_attach():
    """CR 303.4a — no legal object to enchant means no attachment."""
    from engine.permanent_effects import PermanentEffects

    game, p = _game_with_lands(0)  # no lands at all
    t = _DB.get_card("Utopia Sprawl")
    aura = CardInstance(template=t, owner=0, controller=0,
                        instance_id=game.next_instance_id(),
                        zone="battlefield")
    aura._game_state = game
    aura.enter_battlefield()
    p.battlefield.append(aura)
    assert PermanentEffects.attach_aura(game, aura, 0) is None, (
        "with no legal host the Aura must not attach to anything")
