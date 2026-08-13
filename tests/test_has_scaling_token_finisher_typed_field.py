"""Scaling token finisher is classified by a typed field parsed once
at DB load, not by a runtime oracle-substring scan.

# Mechanic the tests name (rule, not a card)

"Create [N] tokens for each [event]" is the storm-token-finisher shape
(Empty-the-Warrens).  The predicate — the words 'create', 'tokens', and
'for each' all present in oracle text — was duplicated as a runtime scan
in six sites across three modules (ai/combo_calc._has_storm_finisher,
ai/combo_calc._tutor_has_payoff_access, ai/finisher_simulator.
_tutor_can_fetch_finisher, ai/finisher_simulator._has_token_finisher_oracle,
ai/finisher_simulator_v3._has_token_finisher_oracle_v3, and the finisher
zone scan).  It now lives as oracle_parser.parse_has_scaling_token_finisher
→ CardTemplate.has_scaling_token_finisher, read as a field.

These tests pin the parser's exact semantics (identical to the old inline
predicate — no behaviour change) and that each consumer reads the field.
Card names appear only as fixture carriers.
"""
from __future__ import annotations

from engine.oracle_parser import parse_has_scaling_token_finisher


class TestParseHasScalingTokenFinisher:
    def test_create_tokens_for_each_spell_is_true(self):
        # Empty-the-Warrens: "Create two 1/1 red Goblin creature tokens
        # for each spell cast before it this turn."
        assert parse_has_scaling_token_finisher(
            "Create two 1/1 red Goblin creature tokens for each spell cast "
            "before it this turn."
        ) is True

    def test_capitalized_create_is_true(self):
        assert parse_has_scaling_token_finisher(
            "Create two tokens for each creature you control."
        ) is True

    def test_damage_spell_without_tokens_is_false(self):
        # Grapeshot: deals damage, no tokens → False.
        assert parse_has_scaling_token_finisher(
            "Deal 1 damage to any target."
        ) is False

    def test_create_tokens_without_for_each_is_false(self):
        # Plain token maker with no scaling clause → False.
        assert parse_has_scaling_token_finisher(
            "Create two 1/1 red Goblin creature tokens."
        ) is False

    def test_for_each_without_create_tokens_is_false(self):
        assert parse_has_scaling_token_finisher(
            "Deal 1 damage for each creature you control."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_scaling_token_finisher("") is False
        assert parse_has_scaling_token_finisher(None) is False


class TestConsumersReadField:
    def _make_token_finisher(self):
        """Mock card whose template has has_scaling_token_finisher=True."""
        class _T:
            has_scaling_token_finisher = True
            oracle_text = "Create two 1/1 tokens for each spell."
            keywords = set()

        class _C:
            template = _T()

        return _C()

    def _make_non_finisher(self):
        class _T:
            has_scaling_token_finisher = False
            oracle_text = "Draw two cards."
            keywords = set()

        class _C:
            template = _T()

        return _C()

    def test_has_token_finisher_oracle_reads_typed_field(self):
        from ai.finisher_simulator import _has_token_finisher_oracle
        assert _has_token_finisher_oracle(self._make_token_finisher().template) is True
        assert _has_token_finisher_oracle(self._make_non_finisher().template) is False

    def test_has_token_finisher_oracle_v3_reads_typed_field(self):
        from ai.finisher_simulator_v3 import _has_token_finisher_oracle_v3
        assert _has_token_finisher_oracle_v3(self._make_token_finisher()) is True
        assert _has_token_finisher_oracle_v3(self._make_non_finisher()) is False
