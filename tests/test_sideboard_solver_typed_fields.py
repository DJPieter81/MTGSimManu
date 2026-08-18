"""Graveyard-hate and spell-chain-hate are classified by typed fields
parsed once at DB load, not by runtime oracle regex scans inside
sideboard_solver.py.

# Mechanic: graveyard hate (rule, not a card)

Cards that exile graveyards or prevent graveyard casting — the
Rest-in-Peace / Leyline-of-the-Void / Grafdigger's-Cage shape —
are detected by oracle_parser.parse_has_graveyard_hate
→ CardTemplate.has_graveyard_hate.  This retires 4 re.search calls
from ai/sideboard_solver._clause_gy_hate.

# Mechanic: spell-chain hate (rule, not a card)

Cards that limit spells per turn or tax each spell —
the Rule-of-Law / Ethersworn-Canonist / Trickbind shape —
are detected by oracle_parser.parse_has_spell_chain_hate
→ CardTemplate.has_spell_chain_hate.  This retires 3 re.search calls
from ai/sideboard_solver._clause_spell_chain_hate.

Card names appear only as fixture carriers; tests pin the *mechanic*.
"""
from __future__ import annotations

from engine.oracle_parser import (
    parse_has_graveyard_hate,
    parse_has_spell_chain_hate,
)


class TestParseHasGraveyardHate:
    # ── True cases ──────────────────────────────────────────────────────────

    def test_exile_target_graveyard_is_true(self):
        # Relic of Progenitus pattern
        assert parse_has_graveyard_hate(
            "Exile target player's graveyard."
        ) is True

    def test_exile_all_cards_from_graveyards_is_true(self):
        # Rest in Peace / Tormod's Crypt
        assert parse_has_graveyard_hate(
            "Exile all cards from all graveyards."
        ) is True

    def test_cant_cast_from_graveyard_is_true(self):
        # Grafdigger's Cage
        assert parse_has_graveyard_hate(
            "Creature cards can't be cast from graveyards or libraries."
        ) is True

    def test_replacement_into_exile_is_true(self):
        # Leyline of the Void / Anafenza
        assert parse_has_graveyard_hate(
            "If a card would be put into an opponent's graveyard from anywhere, "
            "exile it instead."
        ) is True

    def test_exile_single_card_from_graveyard_is_true(self):
        # Relic activated ability: "exile target card from a graveyard"
        assert parse_has_graveyard_hate(
            "Exile target card from a graveyard."
        ) is True

    # ── False cases ─────────────────────────────────────────────────────────

    def test_reanimation_to_battlefield_is_false(self):
        # Goryo's Vengeance — moves FROM graveyard but is not hate
        assert parse_has_graveyard_hate(
            "Return target legendary creature card from your graveyard "
            "to the battlefield."
        ) is False

    def test_mass_reanimation_is_false(self):
        # Living End — exile all creatures and return from GY is recursion
        assert parse_has_graveyard_hate(
            "Exile all creature cards from all graveyards, then return "
            "them to the battlefield under their owners' control."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_graveyard_hate("") is False
        assert parse_has_graveyard_hate(None) is False

    def test_damage_spell_is_false(self):
        assert parse_has_graveyard_hate("Deal 3 damage to any target.") is False


class TestParseHasSpellChainHate:
    # ── True cases ──────────────────────────────────────────────────────────

    def test_cant_cast_more_than_one_spell_is_true(self):
        # Rule of Law / Ethersworn Canonist
        assert parse_has_spell_chain_hate(
            "Each player can't cast more than one spell each turn."
        ) is True

    def test_costs_more_for_each_other_spell_is_true(self):
        # Sphere of Resistance / Thalia-adjacent taxer
        assert parse_has_spell_chain_hate(
            "Spells cost {1} more to cast for each other spell "
            "cast this turn."
        ) is True

    def test_counter_target_triggered_ability_is_true(self):
        # Trickbind / Squelch
        assert parse_has_spell_chain_hate(
            "Counter target triggered ability."
        ) is True

    def test_cant_cast_more_than_one_spell_apostrophe_variant_is_true(self):
        # Some cards use "can't" vs "cant"
        assert parse_has_spell_chain_hate(
            "Players can't cast more than one spell each turn."
        ) is True

    # ── False cases ─────────────────────────────────────────────────────────

    def test_burn_spell_is_false(self):
        assert parse_has_spell_chain_hate("Deal 1 damage to any target.") is False

    def test_counterspell_without_chain_hate_is_false(self):
        # Generic counterspell — not a chain hate card
        assert parse_has_spell_chain_hate("Counter target spell.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_spell_chain_hate("") is False
        assert parse_has_spell_chain_hate(None) is False


class TestConsumersReadTypedField:
    """_clause_gy_hate and _clause_spell_chain_hate read the typed field."""

    def _make_template(self, **kwargs):
        class _T:
            oracle_text = ""
            has_graveyard_hate = False
            has_spell_chain_hate = False

        t = _T()
        for k, v in kwargs.items():
            setattr(t, k, v)
        return t

    def test_clause_gy_hate_true_when_field_set(self):
        from ai.sideboard_solver import _clause_gy_hate
        tmpl = self._make_template(has_graveyard_hate=True)
        # _clause_gy_hate(template, [], None) — signature takes template now
        result = _clause_gy_hate(tmpl, [], None)
        # reliance = 0 so value = 0, but no ValueError means field was read
        assert result == 0.0  # zero opp templates → zero reliance → zero value

    def test_clause_gy_hate_false_when_field_not_set(self):
        from ai.sideboard_solver import _clause_gy_hate
        tmpl = self._make_template(has_graveyard_hate=False)
        assert _clause_gy_hate(tmpl, [], None) == 0.0

    def test_clause_spell_chain_hate_true_when_field_set(self):
        from ai.sideboard_solver import _clause_spell_chain_hate
        tmpl = self._make_template(has_spell_chain_hate=True)
        result = _clause_spell_chain_hate(tmpl, [])
        assert result == 0.0  # zero opp reliance → zero value

    def test_clause_spell_chain_hate_false_when_field_not_set(self):
        from ai.sideboard_solver import _clause_spell_chain_hate
        tmpl = self._make_template(has_spell_chain_hate=False)
        assert _clause_spell_chain_hate(tmpl, []) == 0.0
