"""An Equipment with a flat "Equipped creature gets +N/+M" grant must
raise the equipped creature's power and toughness by that amount.

Rule (CR 301.5c / 613): a flat P/T grant from an attached Equipment is a
continuous effect on the equipped creature. Only per-artifact SCALING
equipment (Cranial Plating, "+1/+0 for each artifact you control") was
applied; the flat-grant class — the whole Sword cycle, Bonesplitter,
Vulshok Morningstar, Cori-Steel Cutter, ... — had no application branch,
so the equipped creature stayed at base P/T (audit: Izzet Prowess vs
Goryo's Vengeance, s55623 — a creature holding Cori-Steel Cutter (+1/+1)
attacked at base P/T, and an isolated repro left Grizzly Bears at 2/2
under Bonesplitter/Vulshok/Sword).

Card names are fixture carriers; the mechanic is the flat equipment P/T
grant, parsed once at load into equip_power_grant/equip_toughness_grant.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState


def _bf(game, card_db, name, controller):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    game.players[controller].battlefield.append(c)
    return c


def _equip(creature, equipment):
    """Attach: mark the creature with the equipment's equipped_<id> tag,
    the same instance tag the engine's attach path sets."""
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")


def test_flat_plus_two_zero_equipment_raises_power_only(card_db):
    game = GameState(rng=random.Random(0))
    bear = _bf(game, card_db, "Grizzly Bears", 0)          # 2/2
    axe = _bf(game, card_db, "Bonesplitter", 0)            # +2/+0
    base_p, base_t = bear.power, bear.toughness
    _equip(bear, axe)
    assert bear.power == base_p + 2, (
        f"Bonesplitter grants +2/+0; power {bear.power}, expected {base_p + 2}")
    assert bear.toughness == base_t, "Bonesplitter grants no toughness"


def test_flat_plus_two_two_equipment_raises_both(card_db):
    game = GameState(rng=random.Random(0))
    bear = _bf(game, card_db, "Grizzly Bears", 0)          # 2/2
    star = _bf(game, card_db, "Vulshok Morningstar", 0)    # +2/+2
    base_p, base_t = bear.power, bear.toughness
    _equip(bear, star)
    assert bear.power == base_p + 2 and bear.toughness == base_t + 2


def test_two_equipment_stack(card_db):
    game = GameState(rng=random.Random(0))
    bear = _bf(game, card_db, "Grizzly Bears", 0)
    base_p, base_t = bear.power, bear.toughness
    _equip(bear, _bf(game, card_db, "Bonesplitter", 0))        # +2/+0
    _equip(bear, _bf(game, card_db, "Vulshok Morningstar", 0)) # +2/+2
    assert bear.power == base_p + 4 and bear.toughness == base_t + 2


def test_per_artifact_scaling_equipment_not_double_counted(card_db):
    """Cranial Plating (+1/+0 for each artifact) is the SCALING form —
    it must not also pick up a flat grant (equip_power_grant == 0)."""
    plating = card_db.get_card("Cranial Plating")
    if plating is None:
        import pytest
        pytest.skip("no per-artifact scaling equipment in this DB")
    assert plating.equip_power_grant == 0 and plating.equip_toughness_grant == 0
