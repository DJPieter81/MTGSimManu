"""A creature-only exile ("exile target creature") must not be treated as
able to hit a noncreature permanent.

Rule: "exile target creature" (Path to Exile, Reality Shift) can only
target a creature; it can never exile a planeswalker, artifact, or
enchantment. The coarse can_exile_permanent flag conflated creature-only
exile with broad "exile target nonland permanent" removal, so the AI's
"this removal can hit a noncreature" gate enumerated every nonland
permanent and picked a planeswalker as the target — Path to Exile
illegally exiled a transformed Ral (audit: Eldrazi Tron vs Ruby Storm,
s55643). The parsed exile_hits_noncreature field answers the narrower
question the gate needs.

Card names are fixture carriers; the mechanic is the exile target-type
restriction.
"""
from __future__ import annotations

from engine.oracle_parser import parse_exile_hits_noncreature


def test_creature_only_exile_does_not_flag_noncreature():
    assert parse_exile_hits_noncreature("Exile target creature.") is False
    assert parse_exile_hits_noncreature(
        "Exile target creature. Its controller may search their library "
        "for a basic land card...") is False


def test_nonland_permanent_exile_flags_noncreature():
    assert parse_exile_hits_noncreature(
        "Exile target nonland permanent.") is True
    assert parse_exile_hits_noncreature(
        "Exile target artifact or creature.") is True
    assert parse_exile_hits_noncreature(
        "Converge — Exile target nonland permanent if its mana value is "
        "less than or equal to the number of colors of mana spent...") is True


def test_non_exile_or_graveyard_exile_does_not_flag():
    assert parse_exile_hits_noncreature("") is False
    assert parse_exile_hits_noncreature(
        "Target player reveals their hand, you choose a nonland card...") is False
    # graveyard exile is not permanent removal
    assert parse_exile_hits_noncreature(
        "Exile target card from a graveyard.") is False


def test_real_card_fields(card_db):
    for name in ("Path to Exile", "Reality Shift"):
        t = card_db.get_card(name)
        if t is None:
            continue
        assert t.exile_hits_noncreature is False, (
            f"{name} is a creature-only exile — must not flag noncreature-hitting")
    for name in ("Leyline Binding", "Prismatic Ending"):
        t = card_db.get_card(name)
        if t is None:
            continue
        assert t.exile_hits_noncreature is True, (
            f"{name} exiles a nonland permanent — must flag noncreature-hitting")
