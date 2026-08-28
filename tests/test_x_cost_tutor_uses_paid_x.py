"""An X-cost tutor searches up to the mana value actually paid.

Root cause (Amulet Titan mechanic audit, 2026-08-25): the X-cost creature-tutor
resolver read `card._x_value` — an attribute written NOWHERE in the codebase
(one read, zero writes). The paid X lives on the stack item (`item.x_value`),
which is what every other X-consuming resolver in `engine/card_effects.py`
correctly reads. The tutor therefore always resolved with X = 0 and could only
ever find a mana-value-0 creature, no matter how much mana was spent — in
practice a 1/1 land-creature that dies to any ping, from a card the deck plays
as its primary toolbox tutor.

A second defect in the same handler: candidates were ranked by raw
`power + toughness`. Several premium tutor targets have a base P/T of 0/0 and
derive their size from a characteristic-defining ability, so P/T ranking picks
the wrong card even once X is correct. Mana value is the size proxy that
matches what the player paid for.

Rule under test: the tutor searches up to the X actually paid, and prefers the
highest mana value within that budget. Mechanic-driven (X-cost resolution), no
card names asserted.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase

_DB = CardDatabase()


def _add(game, name, controller, zone):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _resolve_tutor_with_x(x_paid, library_names):
    # The per-card handler this file used to call was retired: the shape is
    # now resolved generically from `CardTemplate.x_creature_tutor_data`
    # (oracle_resolver._resolve_x_creature_tutor). Same card, same paid X,
    # same assertions — only the entry point moved.
    from engine.oracle_resolver import resolve_spell_from_oracle

    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.players[0].deck_name = "Amulet Titan"
    game.players[1].deck_name = "Boros Energy"
    for nm in library_names:
        _add(game, nm, 0, "library")
    spell = _add(game, "Green Sun's Zenith", 0, "graveyard")
    resolve_spell_from_oracle(game, spell, 0, None, x_value=x_paid)
    return [c.name for c in game.players[0].battlefield]


def test_tutor_finds_a_target_costing_up_to_the_x_actually_paid():
    # X=6 must be able to reach a 6-mana creature, not just a 0-mana one.
    on_bf = _resolve_tutor_with_x(
        6, ["Primeval Titan", "Arboreal Grazer", "Dryad Arbor"])
    assert "Primeval Titan" in on_bf, (
        f"with X=6 paid, the tutor must be able to find a mana-value-6 "
        f"creature; it put {on_bf} onto the battlefield")


def test_tutor_respects_the_x_budget():
    # Regression: X=1 must NOT reach a 6-drop.
    on_bf = _resolve_tutor_with_x(1, ["Primeval Titan", "Dryad Arbor"])
    assert "Primeval Titan" not in on_bf, (
        f"X=1 must not reach a mana-value-6 creature; got {on_bf}")


def test_tutor_prefers_highest_mana_value_within_budget():
    # Arboreal Grazer (MV 1) vs Primeval Titan (MV 6) at X=6 → take the Titan.
    on_bf = _resolve_tutor_with_x(6, ["Arboreal Grazer", "Primeval Titan"])
    assert "Primeval Titan" in on_bf and "Arboreal Grazer" not in on_bf, (
        f"within the X budget the tutor should take the highest mana value; "
        f"got {on_bf}")


def test_no_spell_resolver_reads_an_undefined_private_x_attribute():
    """Ratchet-style guard: the bug class was a read with no writer."""
    import pathlib
    import re

    src = pathlib.Path("engine/card_effects.py").read_text()
    offenders = re.findall(r"getattr\(\s*card\s*,\s*['\"]_x_\w+['\"]", src)
    assert not offenders, (
        f"resolvers must read the paid X from the stack item, not a private "
        f"card attribute nothing writes: {offenders}")
