"""oracle-ratchet batch 5: typed fields for on-hit triggers, destroy capability,
tutor detection, noncreature-spell-cast triggers, and artifact synergy.

Rule: every card property derivable from oracle text is parsed ONCE in
oracle_parser.py at load time and stored as a typed field on CardTemplate.
Decision-time code (engine/, ai/) reads the field; it never re-inspects
the raw oracle string.

This file pins five mechanic clusters:

  has_combat_damage_player_trigger  — on-hit trigger pattern (Ragavan class)
  can_destroy_{artifact,enchantment,nonland_permanent} — removal capability flags
  is_tutor                          — library-search / Wish-type fetch
  has_noncreature_spell_cast_trigger — prowess / magecraft pattern
  has_artifact_synergy              — Metalcraft / affinity / for-each-artifact scaling

Class size: hundreds of cards across each mechanic cluster in Modern.
"""
from __future__ import annotations

import pytest
from engine.oracle_parser import (
    parse_has_combat_damage_player_trigger,
    parse_can_destroy_artifact,
    parse_can_destroy_enchantment,
    parse_can_destroy_nonland_permanent,
    parse_is_tutor,
    parse_has_noncreature_spell_cast_trigger,
    parse_has_artifact_synergy,
)
from engine.cards import CardTemplate, CardType
from engine.mana import ManaCost


def _tmpl(oracle: str) -> CardTemplate:
    """Minimal CardTemplate — triggers __post_init__ fallback derivation."""
    return CardTemplate(
        name="__test__",
        oracle_text=oracle,
        card_types=[CardType.INSTANT],
        mana_cost=ManaCost(generic=0),
        supertypes=[],
        subtypes=[],
        keywords=set(),
        abilities=[],
    )


# ─────────────────────────────────────────────────────────────────────────────
# has_combat_damage_player_trigger
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatDamagePlayerTrigger:
    def test_on_hit_trigger_detected(self):
        oracle = "Whenever this creature deals combat damage to a player, you may cast the top card of their library."
        assert parse_has_combat_damage_player_trigger(oracle) is True

    def test_no_trigger_on_noncombat_damage(self):
        oracle = "This creature deals 2 damage to any target."
        assert parse_has_combat_damage_player_trigger(oracle) is False

    def test_empty_oracle_returns_false(self):
        assert parse_has_combat_damage_player_trigger("") is False

    def test_case_insensitive(self):
        oracle = "WHENEVER THIS CREATURE DEALS COMBAT DAMAGE TO A PLAYER, DRAW A CARD."
        assert parse_has_combat_damage_player_trigger(oracle) is True

    def test_card_template_field_populated_from_oracle(self):
        t = _tmpl("Whenever this creature deals combat damage to a player, create a Treasure token.")
        assert t.has_combat_damage_player_trigger is True

    def test_card_template_field_false_without_trigger(self):
        t = _tmpl("Trample")
        assert t.has_combat_damage_player_trigger is False


# ─────────────────────────────────────────────────────────────────────────────
# can_destroy_artifact / enchantment / nonland_permanent
# ─────────────────────────────────────────────────────────────────────────────

class TestDestroyCapabilityFlags:
    def test_destroy_target_artifact_detected(self):
        oracle = "Destroy target artifact."
        assert parse_can_destroy_artifact(oracle) is True

    def test_destroy_all_artifacts_detected(self):
        oracle = "Destroy all artifacts and enchantments."
        assert parse_can_destroy_artifact(oracle) is True

    def test_destroy_target_enchantment_detected(self):
        oracle = "Destroy target enchantment."
        assert parse_can_destroy_enchantment(oracle) is True

    def test_destroy_target_nonland_permanent_detected(self):
        oracle = "Destroy target nonland permanent with mana value 3 or less."
        assert parse_can_destroy_nonland_permanent(oracle) is True

    def test_artifact_destroy_does_not_match_enchantment_flag(self):
        oracle = "Destroy target artifact."
        assert parse_can_destroy_enchantment(oracle) is False

    def test_enchantment_destroy_does_not_match_artifact_flag(self):
        oracle = "Destroy target enchantment."
        assert parse_can_destroy_artifact(oracle) is False

    def test_card_template_artifact_removal_populated(self):
        t = _tmpl("When this creature enters, destroy target artifact.")
        assert t.can_destroy_artifact is True
        assert t.can_destroy_enchantment is False

    def test_card_template_nonland_permanent_populated(self):
        t = _tmpl("Destroy target nonland permanent.")
        assert t.can_destroy_nonland_permanent is True

    def test_empty_oracle_returns_false(self):
        assert parse_can_destroy_artifact("") is False
        assert parse_can_destroy_enchantment("") is False
        assert parse_can_destroy_nonland_permanent("") is False


# ─────────────────────────────────────────────────────────────────────────────
# is_tutor
# ─────────────────────────────────────────────────────────────────────────────

class TestIsTutor:
    def test_search_your_library_detected(self):
        oracle = "Search your library for a creature card, reveal it, and put it into your hand."
        assert parse_is_tutor(oracle) is True

    def test_from_outside_the_game_detected(self):
        oracle = "You may choose a card you own from outside the game, reveal it, and put it into your hand."
        assert parse_is_tutor(oracle) is True

    def test_from_your_sideboard_detected(self):
        oracle = "You may put a card from your sideboard into your hand."
        assert parse_is_tutor(oracle) is True

    def test_non_tutor_oracle_returns_false(self):
        oracle = "Draw two cards."
        assert parse_is_tutor(oracle) is False

    def test_empty_oracle_returns_false(self):
        assert parse_is_tutor("") is False

    def test_card_template_tutor_field_populated(self):
        t = _tmpl("Search your library for a land card and put it onto the battlefield tapped.")
        assert t.is_tutor is True

    def test_card_template_non_tutor_field_false(self):
        t = _tmpl("Draw three cards.")
        assert t.is_tutor is False


# ─────────────────────────────────────────────────────────────────────────────
# has_noncreature_spell_cast_trigger
# ─────────────────────────────────────────────────────────────────────────────

class TestNoncreatureSpellCastTrigger:
    def test_whenever_you_cast_noncreature_spell_detected(self):
        oracle = "Whenever you cast a noncreature spell, this creature gets +1/+0 until end of turn."
        assert parse_has_noncreature_spell_cast_trigger(oracle) is True

    def test_whenever_player_casts_noncreature_spell_detected(self):
        oracle = "Whenever a player casts a noncreature spell, put a +1/+1 counter on this creature."
        assert parse_has_noncreature_spell_cast_trigger(oracle) is True

    def test_creature_spell_trigger_not_matched(self):
        oracle = "Whenever you cast a creature spell, create a 1/1 token."
        assert parse_has_noncreature_spell_cast_trigger(oracle) is False

    def test_empty_oracle_returns_false(self):
        assert parse_has_noncreature_spell_cast_trigger("") is False

    def test_card_template_field_populated(self):
        t = _tmpl("Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn.")
        assert t.has_noncreature_spell_cast_trigger is True

    def test_card_template_field_false_for_vanilla(self):
        t = _tmpl("Flying")
        assert t.has_noncreature_spell_cast_trigger is False


# ─────────────────────────────────────────────────────────────────────────────
# has_artifact_synergy
# ─────────────────────────────────────────────────────────────────────────────

class TestArtifactSynergy:
    def test_for_each_artifact_detected(self):
        oracle = "This creature gets +1/+0 for each artifact you control."
        assert parse_has_artifact_synergy(oracle) is True

    def test_metalcraft_detected(self):
        oracle = "Metalcraft — This creature has first strike as long as you control three or more artifacts."
        assert parse_has_artifact_synergy(oracle) is True

    def test_affinity_for_artifacts_detected(self):
        oracle = "Affinity for artifacts (This spell costs {1} less to cast for each artifact you control.)"
        assert parse_has_artifact_synergy(oracle) is True

    def test_non_artifact_synergy_returns_false(self):
        oracle = "This creature gets +1/+0 for each creature you control."
        assert parse_has_artifact_synergy(oracle) is False

    def test_empty_oracle_returns_false(self):
        assert parse_has_artifact_synergy("") is False

    def test_card_template_field_populated(self):
        t = _tmpl("Metalcraft — This creature has lifelink.")
        assert t.has_artifact_synergy is True

    def test_card_template_field_false_without_synergy(self):
        t = _tmpl("Flying, vigilance.")
        assert t.has_artifact_synergy is False
