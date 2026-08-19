"""Batch 17 typed-field migration tests.

# Mechanic: converge keyword (rule, not a card)

has_converge replaces 'converge'/'colors of mana spent' in oracle in
cast_manager.py — converge spells benefit from using more colors of mana
when cast, and the engine sorts lands to maximize color diversity.

# Mechanic: delirium condition (rule, not a card)

has_delirium replaces 'delirium' in oracle in cast_manager.py — delirium
abilities activate when the controller has four or more card types in their
graveyard, used to conditionally grant keywords like flying.

# Mechanic: all basic land types (rule, not a card)

has_all_basic_land_types replaces 'lands you control are every basic land
type' in oracle in cards.py _get_domain_count — Leyline of the Guildpact
pattern that short-circuits domain to 5 when a qualifying permanent is
on the battlefield.

# Mechanic: destroy or exile removal (rule, not a card)

has_destroy_or_exile replaces 'destroy'/'exile' in oracle in evaluator.py
_spell_damage — removal spells return a sentinel value rather than a
damage amount, since they kill by rule rather than by damage.

Card names appear only as fixture carriers in comments.
"""
from __future__ import annotations
import pytest
from engine.oracle_parser import (
    parse_has_converge,
    parse_has_delirium,
    parse_has_all_basic_land_types,
    parse_has_destroy_or_exile,
)


class TestHasConvergeCastManager:
    """Pins the replacement of 'converge'/'colors of mana spent' in oracle
    in cast_manager.py — converge spells sort lands to maximize color count."""

    def test_converge_keyword_text(self):
        # Skyrider Elf / Painful Truths / Bring to Light: "Converge — ..."
        assert parse_has_converge(
            "Converge — Skyrider Elf enters the battlefield with a +1/+1 counter "
            "on it for each color of mana spent to cast it."
        ) is True

    def test_colors_of_mana_spent_reminder(self):
        # Reminder text phrasing for converge (some printings omit keyword)
        assert parse_has_converge(
            "This spell's effect increases based on the colors of mana spent to cast it."
        ) is True

    def test_plain_spell_no_converge(self):
        assert parse_has_converge("Counter target spell.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_converge("") is False
        assert parse_has_converge(None) is False


class TestHasDeliriumCastManager:
    """Pins the replacement of 'delirium' in oracle in cast_manager.py —
    delirium conditions grant keywords when 4+ card types in graveyard."""

    def test_delirium_keyword_text(self):
        # Traverse the Ulvenwald / Gnarlwood Dryad / Autumnal Gloom
        assert parse_has_delirium(
            "Delirium — If there are four or more card types among cards in your "
            "graveyard, this creature has flying."
        ) is True

    def test_delirium_inline_condition(self):
        assert parse_has_delirium(
            "Delirium: +1/+1 for each card type in your graveyard."
        ) is True

    def test_plain_flying_no_delirium(self):
        assert parse_has_delirium("Flying. Lifelink.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_delirium("") is False
        assert parse_has_delirium(None) is False


class TestHasAllBasicLandTypesDomainCount:
    """Pins the replacement of 'lands you control are every basic land type'
    in oracle in cards.py _get_domain_count — Leyline of the Guildpact pattern
    short-circuits domain to 5."""

    def test_lands_you_control_are_every_basic_land_type(self):
        # Leyline of the Guildpact pattern
        assert parse_has_all_basic_land_types(
            "Lands you control are every basic land type in addition to their other types."
        ) is True

    def test_partial_land_type_grant_is_false(self):
        # Breeding Pool / shock land — adds basic land types but not all of them
        assert parse_has_all_basic_land_types(
            "({T}: Add {G} or {U}.) As this land enters the battlefield, you may "
            "pay 2 life. If you don't, it enters the battlefield tapped."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_all_basic_land_types("") is False
        assert parse_has_all_basic_land_types(None) is False


class TestHasDestroyOrExileSpellDamage:
    """Pins the replacement of 'destroy'/'exile' in oracle in evaluator.py
    _spell_damage — removal spells return a sentinel instead of damage amount."""

    def test_destroy_target_creature(self):
        # Fatal Push / Assassin's Trophy pattern
        assert parse_has_destroy_or_exile("Destroy target creature.") is True

    def test_exile_target_permanent(self):
        # Leyline Binding / Swords to Plowshares pattern
        assert parse_has_destroy_or_exile(
            "Exile target nonland nontoken permanent."
        ) is True

    def test_pure_burn_no_remove(self):
        # Lightning Bolt — damage only, no destroy/exile
        assert parse_has_destroy_or_exile(
            "Deals 3 damage to any target."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_destroy_or_exile("") is False
        assert parse_has_destroy_or_exile(None) is False
