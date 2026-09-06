"""Batch 6 typed-field oracle-parser unit tests.

Pure-parser tests for four new CardTemplate fields introduced in batch 6:
  has_draw_effect         — draw / impulse-draw detection
  can_exile_permanent     — 'exile target <permanent-type>'
  has_symmetric_reanimation — Living End-class mass reanimation
  phyrexian_pip_count     — count of {X/P} Phyrexian mana pips.  Now parsed
                            from the MANA COST rather than the oracle text
                            (CR 107.4f): the reminder clause names the symbol
                            once however many pips the cost carries, so an
                            oracle-derived count is wrong for every multi-pip
                            cost.  See tests/test_phyrexian_mana_cr107_4f.py.

Each function is tested in isolation (no CardDatabase load).
Pattern C: card names appear only in module docstrings and fixture comments.
"""
import pytest
from engine.card_database import parse_mana_cost_mtgjson
from engine.oracle_parser import (
    parse_has_draw_effect,
    parse_can_exile_permanent,
    parse_has_symmetric_reanimation,
)


def parse_phyrexian_pip_count(mana_cost: str) -> int:
    """Total Phyrexian pips in a printed MANA COST (CR 107.4f)."""
    return sum(parse_mana_cost_mtgjson(mana_cost).phyrexian.values())


class TestHasDrawEffect:
    """parse_has_draw_effect covers standard draw and impulse-draw patterns."""

    def test_draw_a_card_detected(self):
        assert parse_has_draw_effect("Draw a card.") is True

    def test_draw_two_cards_detected(self):
        assert parse_has_draw_effect("Draw two cards.") is True

    def test_draw_three_cards_detected(self):
        assert parse_has_draw_effect("Draw three cards.") is True

    def test_draw_cards_generic_detected(self):
        # "draw cards" without numeral — Ancestral Vision pattern
        assert parse_has_draw_effect("Target player draws cards equal to the number...") is True

    def test_look_at_top_detected(self):
        # Scry / Surveil / Brainstorm family
        assert parse_has_draw_effect("Look at the top four cards of your library.") is True

    def test_impulse_exile_top_play_detected(self):
        # Reckless Impulse / Wrenn's Resolve pattern
        assert parse_has_draw_effect(
            "Exile the top two cards of your library. "
            "Until the end of your next turn, you may play those cards."
        ) is True

    def test_exile_top_cast_variant_detected(self):
        assert parse_has_draw_effect(
            "Exile the top three cards. You may cast them this turn."
        ) is True

    def test_destroy_not_draw(self):
        assert parse_has_draw_effect("Destroy target creature.") is False

    def test_counter_not_draw(self):
        assert parse_has_draw_effect("Counter target spell.") is False

    def test_exile_top_without_play_or_cast_not_draw(self):
        # Milling — exile top N without play permission is not card draw
        assert parse_has_draw_effect(
            "Exile the top five cards of target player's library."
        ) is False

    def test_empty_oracle_returns_false(self):
        assert parse_has_draw_effect("") is False

    def test_none_like_empty_returns_false(self):
        assert parse_has_draw_effect(None) is False  # type: ignore[arg-type]


class TestCanExilePermanent:
    """parse_can_exile_permanent matches exile-target-<permanent> removal."""

    def test_exile_target_creature(self):
        assert parse_can_exile_permanent("Exile target creature.") is True

    def test_exile_target_artifact(self):
        assert parse_can_exile_permanent("Exile target artifact.") is True

    def test_exile_target_enchantment(self):
        assert parse_can_exile_permanent("Exile target enchantment.") is True

    def test_exile_target_nonland_permanent(self):
        assert parse_can_exile_permanent("Exile target nonland permanent.") is True

    def test_exile_target_permanent(self):
        assert parse_can_exile_permanent("Exile target permanent.") is True

    def test_exile_target_planeswalker(self):
        assert parse_can_exile_permanent("Exile target planeswalker.") is True

    def test_exile_graveyard_card_not_permanent(self):
        # Surgical Extraction / Tormod's Crypt pattern — graveyard exile only
        assert parse_can_exile_permanent(
            "Exile target card from a graveyard."
        ) is False

    def test_destroy_not_exile(self):
        assert parse_can_exile_permanent("Destroy target creature.") is False

    def test_exile_without_target_not_matched(self):
        # Leyline of the Void — continuous effect, not targeted
        assert parse_can_exile_permanent(
            "If a card would be put into an opponent's graveyard, exile it instead."
        ) is False

    def test_empty_oracle_returns_false(self):
        assert parse_can_exile_permanent("") is False


class TestHasSymmetricReanimation:
    """parse_has_symmetric_reanimation detects Living End-class mass reanimation."""

    def test_each_player_returns_creatures_detected(self):
        assert parse_has_symmetric_reanimation(
            "Each player returns all creature cards from their graveyard to the battlefield."
        ) is True

    def test_all_graveyards_variant_detected(self):
        assert parse_has_symmetric_reanimation(
            "Return all creature cards from all graveyards to the battlefield under their owners' control."
        ) is True

    def test_one_sided_reanimation_not_matched(self):
        # Goryo's Vengeance / Unburial Rites — single-target, one-sided
        assert parse_has_symmetric_reanimation(
            "Return target legendary creature card from your graveyard to the battlefield."
        ) is False

    def test_draw_a_card_not_matched(self):
        assert parse_has_symmetric_reanimation("Draw a card.") is False

    def test_empty_oracle_returns_false(self):
        assert parse_has_symmetric_reanimation("") is False

    def test_graveyard_mention_without_return_not_matched(self):
        # Surveil or mill effects mention graveyard without return
        assert parse_has_symmetric_reanimation(
            "Each player puts the top five cards of their library into their graveyard."
        ) is False


class TestPhyrexianPipCount:
    """Phyrexian pips are counted in the printed MANA COST (CR 107.4f), never
    in oracle text — the reminder clause names the symbol once however many
    pips the cost has."""

    def test_single_pip_counted(self):
        assert parse_phyrexian_pip_count("{U/P}") == 1

    def test_two_pips_counted(self):
        assert parse_phyrexian_pip_count("{1}{W/P}{W/P}") == 2

    def test_three_pips_counted(self):
        assert parse_phyrexian_pip_count("{4}{G/P}{G/P}{G/P}") == 3

    def test_no_pips_zero(self):
        assert parse_phyrexian_pip_count("{2}{R}") == 0

    def test_empty_cost_zero(self):
        assert parse_phyrexian_pip_count("") == 0

    def test_regular_mana_not_counted(self):
        # {U}, {W}, {G} etc. should not match
        assert parse_phyrexian_pip_count("{U}{U}") == 0

    def test_pip_is_recorded_under_its_own_colour(self):
        """Waiving a pip excuses that colour and no other, so the colour is
        kept, not collapsed into a total."""
        cost = parse_mana_cost_mtgjson("{W}{U}{B/P}{R}{G}")
        assert cost.phyrexian == {"B": 1}
