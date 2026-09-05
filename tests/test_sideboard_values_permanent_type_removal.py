"""The sideboard solver values removal by the opponent's density of the
permanent TYPES it can hit — read from the typed removal fields
(`targeted_removal_data` for spells, `etb_targeted_removal_data` for
enters-the-battlefield bodies), not from an artifact-only tag.

Before this, only artifacts were counted and only spell tags were read, so
a naturalize creature was valued as a bare body and boarded OUT against a
deck running eight enchantments (Azorius Blink vs Domain Zoo: "+2 Spell
Pierce, −2 Witch Enchanter"), and the games that follow the swap never saw
its enters-trigger at all. Card names are fixture carriers only.
"""
from __future__ import annotations

from ai.sideboard_solver import plan_sideboard, sb_value
from decks.modern_meta import MODERN_DECKS


def _templates(card_db, names):
    out = []
    for n in names:
        t = card_db.get_card(n)
        assert t is not None, f"missing {n}"
        out.append(t)
    return out


def test_etb_enchantment_removal_body_is_worth_more_against_enchantments(card_db):
    body = card_db.get_card("Witch Enchanter // Witch-Blessed Meadow")
    enchantment_deck = _templates(card_db, ["Leyline Binding"] * 4
                                  + ["Leyline of the Guildpact"] * 4
                                  + ["Grizzly Bears"] * 8)
    creature_deck = _templates(card_db, ["Grizzly Bears"] * 16)
    assert sb_value(body, enchantment_deck) > sb_value(body, creature_deck)


def test_numeric_ceiling_excludes_permanents_it_cannot_reach(card_db):
    """A "with mana value 3 or less" artifact/enchantment removal spell
    counts only the opponent's permanents it can legally hit."""
    spell = card_db.get_card("Natural State")           # artifact/enchantment, MV ≤ 3
    reachable = _templates(card_db, ["Rest in Peace"] * 8 + ["Grizzly Bears"] * 8)   # MV 2
    unreachable = _templates(card_db, ["Leyline Binding"] * 8 + ["Grizzly Bears"] * 8)  # MV 6
    assert sb_value(spell, reachable) > sb_value(spell, unreachable)
