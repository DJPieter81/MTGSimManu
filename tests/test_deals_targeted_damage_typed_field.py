"""A direct-damage finisher is classified by a typed field parsed once
at DB load, not by a runtime oracle-substring scan.

# Mechanic the tests name (rule, not a card)

"Deals damage to a target / to each player / to any target" is the
direct-damage finisher shape (Grapeshot). The predicate — the word
"damage" present together with one of target/each/any — was duplicated
as a runtime `'x' in oracle` scan in two scoring modules
(`ai/combo_calc._payoff_deals_direct_damage`, `ai/combo_chain`'s
`classify_card`). It now lives as `oracle_parser.parse_deals_targeted_
damage` → `CardTemplate.deals_targeted_damage`, read as a field. These
tests pin the parser's exact semantics (identical to the old inline
predicate — no behavior change) and that the consumer reads the field.
Card names appear only as fixture carriers.
"""
from __future__ import annotations

from engine.oracle_parser import parse_deals_targeted_damage


class TestParseDealsTargetedDamage:
    def test_damage_to_any_target_is_targeted_damage(self):
        # Grapeshot: "deal 1 damage to any target."
        assert parse_deals_targeted_damage(
            "Deal 1 damage to any target.") is True

    def test_damage_to_each_player_is_targeted_damage(self):
        # Fireball / Pyroclasm shape: "deals damage to each ...".
        assert parse_deals_targeted_damage(
            "~ deals 3 damage to each creature and each player.") is True

    def test_damage_to_target_is_targeted_damage(self):
        assert parse_deals_targeted_damage(
            "Lightning-style: deal 3 damage to target creature or player."
        ) is True

    def test_token_maker_without_damage_is_not_targeted_damage(self):
        # Empty-the-Warrens: creates tokens, deals no damage → False.
        assert parse_deals_targeted_damage(
            "Create two 1/1 red Goblin creature tokens.") is False

    def test_damage_word_without_target_each_or_any_is_false(self):
        # "damage" present but none of target/each/any → the old
        # predicate returned False; the field must match exactly.
        assert parse_deals_targeted_damage(
            "If a source would deal damage this turn, prevent that damage."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_deals_targeted_damage("") is False
        assert parse_deals_targeted_damage(None) is False


class TestConsumerReadsField:
    def test_payoff_direct_damage_reads_typed_field(self):
        from ai.combo_calc import _payoff_deals_direct_damage

        class _T:
            deals_targeted_damage = True

        class _C:
            template = _T()

        assert _payoff_deals_direct_damage(_C()) is True

        class _T2:
            deals_targeted_damage = False

        class _C2:
            template = _T2()

        assert _payoff_deals_direct_damage(_C2()) is False
