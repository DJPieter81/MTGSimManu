"""Stax classification uses typed fields parsed once at DB load, not
runtime oracle substring scans inside ai/stax_ev.py.

# Mechanic: stax permanent families (rule, not a card)

Four locking/taxing mechanics have distinct classification buckets:
- 'chalice'    — counter spells with mana value = charge counters on permanent
- 'blood_moon' — nonbasic lands become forced basic type
- 'canonist'   — one spell (or nonartifact spell) per turn per player
- 'torpor_orb' — ETB triggers don't trigger

Both oracle_parser.parse_stax_class and oracle_parser.parse_stax_forced_basic
are tested here. Card names appear only as fixture carriers in comments.
"""
from __future__ import annotations

from engine.oracle_parser import parse_stax_class, parse_stax_forced_basic


class TestParseStaxClass:
    # ── chalice family ──────────────────────────────────────────────────────

    def test_chalice_charge_counter_mana_value_is_chalice(self):
        # Chalice of the Void
        assert parse_stax_class(
            "Whenever a player casts a spell with mana value equal to the number "
            "of charge counters on this artifact, counter that spell."
        ) == 'chalice'

    def test_chalice_counter_it_variant_is_chalice(self):
        assert parse_stax_class(
            "When a player casts a spell with mana value equal to charge counters "
            "here, counter it."
        ) == 'chalice'

    # ── blood_moon family ───────────────────────────────────────────────────

    def test_nonbasic_lands_are_mountains_is_blood_moon(self):
        # Blood Moon / Magus of the Moon
        assert parse_stax_class("Nonbasic lands are Mountains.") == 'blood_moon'

    def test_nonbasic_lands_are_islands_is_blood_moon(self):
        # Hypothetical Island-Moon variant
        assert parse_stax_class("Nonbasic lands are Islands.") == 'blood_moon'

    # ── canonist family ─────────────────────────────────────────────────────

    def test_cant_cast_more_than_one_each_turn_is_canonist(self):
        # Rule of Law
        assert parse_stax_class(
            "Each player can't cast more than one spell each turn."
        ) == 'canonist'

    def test_cant_cast_additional_nonartifact_is_canonist(self):
        # Ethersworn Canonist
        assert parse_stax_class(
            "Each player who has cast a nonartifact spell this turn "
            "can't cast additional nonartifact spells this turn."
        ) == 'canonist'

    # ── torpor_orb family ───────────────────────────────────────────────────

    def test_entering_dont_cause_abilities_is_torpor_orb(self):
        # Torpor Orb
        assert parse_stax_class(
            "Creatures entering the battlefield don't cause abilities to trigger."
        ) == 'torpor_orb'

    def test_entering_dont_trigger_variant_is_torpor_orb(self):
        assert parse_stax_class(
            "Abilities of creatures entering the battlefield don't trigger."
        ) == 'torpor_orb'

    # ── None cases ──────────────────────────────────────────────────────────

    def test_counterspell_is_none(self):
        assert parse_stax_class("Counter target spell.") is None

    def test_burn_spell_is_none(self):
        assert parse_stax_class("Deal 3 damage to any target.") is None

    def test_empty_oracle_is_none(self):
        assert parse_stax_class("") is None
        assert parse_stax_class(None) is None


class TestParseStaxForcedBasic:
    def test_blood_moon_forces_mountain(self):
        assert parse_stax_forced_basic("Nonbasic lands are Mountains.") == 'mountain'

    def test_island_variant_forces_island(self):
        assert parse_stax_forced_basic("Nonbasic lands are Islands.") == 'island'

    def test_non_blood_moon_card_is_none(self):
        assert parse_stax_forced_basic("Counter target spell.") is None

    def test_empty_oracle_is_none(self):
        assert parse_stax_forced_basic("") is None
        assert parse_stax_forced_basic(None) is None


class TestConsumersReadTypedField:
    """classify_stax and _blood_moon_lock_ev read typed fields."""

    def _make_template(self, **kwargs):
        class _T:
            oracle_text = ""
            stax_class = None
            stax_forced_basic = None

        t = _T()
        for k, v in kwargs.items():
            setattr(t, k, v)
        return t

    def test_classify_stax_reads_typed_field(self):
        from ai.stax_ev import classify_stax
        assert classify_stax(self._make_template(stax_class='chalice')) == 'chalice'
        assert classify_stax(self._make_template(stax_class='blood_moon')) == 'blood_moon'
        assert classify_stax(self._make_template(stax_class='canonist')) == 'canonist'
        assert classify_stax(self._make_template(stax_class=None)) is None
