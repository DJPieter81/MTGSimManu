"""An equip cost is the total of every mana symbol it prints.

`parse_equip_cost` matched the first generic symbol and stopped, so
"Equip {1}{R}" typed as 1: the red pip was free. Every equipment with a
coloured equip cost (24 in the pool print generic + colour, 27 print any
colour pip) equipped one mana cheap.

Rule: the equip quantity is the sum of all printed symbols. (The colour
requirement itself is still not typed — the field is a quantity; that is
a separate gap, recorded, not hidden by this test.)
"""
from __future__ import annotations

from engine.oracle_parser import parse_equip_cost


def test_equip_cost_is_the_sum_of_every_printed_symbol():
    assert parse_equip_cost("Equipped creature gets +1/+1.\nEquip {1}{R}") == 2
    assert parse_equip_cost("Equip {2}") == 2
    assert parse_equip_cost("Equip {B}{B}") == 2
    assert parse_equip_cost("Equip {3}{W}{W}") == 5
    assert parse_equip_cost("Equip {0}") == 0
    assert parse_equip_cost("Flying") is None


def test_a_reminder_text_equip_does_not_shadow_the_printed_cost():
    assert parse_equip_cost(
        "Equip {1}{R} ({1}{R}: Attach to target creature you control. "
        "Equip only as a sorcery.)") == 2
