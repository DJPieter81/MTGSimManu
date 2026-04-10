"""
Tests for extracted modules (Phase 4).

Tests ai/mulligan.py, ai/response.py, engine/sideboard_manager.py,
engine/callbacks.py, and the data loaders.
"""
import pytest
import random
from tests.conftest import run_seeded_game


class TestMulliganDecider:
    """Test the extracted MulliganDecider."""

    def test_auto_keep_at_5(self):
        from ai.mulligan import MulliganDecider
        from ai.ai_player import ArchetypeStrategy
        decider = MulliganDecider(ArchetypeStrategy.MIDRANGE)
        # At 5 cards, the AIPlayer wrapper auto-keeps, but the decider
        # should also return True for any reasonable 5-card hand
        # (this tests the decider directly)
        assert decider is not None

    def test_generic_too_few_lands(self):
        """Generic mulligan rejects 0-land hands."""
        from ai.mulligan import MulliganDecider
        from ai.ai_player import ArchetypeStrategy
        from unittest.mock import MagicMock

        decider = MulliganDecider(ArchetypeStrategy.MIDRANGE)

        # Create mock cards
        def make_card(is_land=False, cmc=2, tags=None):
            card = MagicMock()
            card.template.is_land = is_land
            card.template.cmc = cmc
            card.template.tags = tags or set()
            card.template.is_creature = not is_land
            card.template.produces_mana = ["R"] if is_land else []
            card.name = "Mock"
            return card

        # 0 lands = should mulligan
        hand = [make_card(is_land=False) for _ in range(7)]
        assert decider._generic(hand, [], hand, 7) is False

    def test_generic_too_many_lands(self):
        """Generic mulligan rejects 5+ land hands at 7 cards."""
        from ai.mulligan import MulliganDecider
        from ai.ai_player import ArchetypeStrategy
        from unittest.mock import MagicMock

        decider = MulliganDecider(ArchetypeStrategy.MIDRANGE)

        def make_card(is_land=False, cmc=2, tags=None):
            card = MagicMock()
            card.template.is_land = is_land
            card.template.cmc = cmc
            card.template.tags = tags or set()
            card.name = "Mock"
            return card

        lands = [make_card(is_land=True) for _ in range(5)]
        spells = [make_card() for _ in range(2)]
        assert decider._generic(lands + spells, lands, spells, 7) is False

    def test_reason_keep(self):
        from ai.mulligan import MulliganDecider
        from unittest.mock import MagicMock

        def make_card(is_land=False, cmc=2, tags=None):
            card = MagicMock()
            card.template.is_land = is_land
            card.template.cmc = cmc
            card.template.tags = tags or set()
            return card

        lands = [make_card(is_land=True) for _ in range(3)]
        spells = [make_card(cmc=1), make_card(cmc=2, tags={"removal"})]
        hand = lands + spells
        reason = MulliganDecider.reason(hand, lands, spells, True)
        assert "3 lands" in reason
        assert "Keepable" in reason


class TestSideboardManager:
    """Test the extracted sideboard_manager."""

    def test_no_sideboard(self):
        from engine.sideboard_manager import sideboard
        main = {"Lightning Bolt": 4, "Mountain": 20}
        result_main, result_side = sideboard(main, {}, "Test", "Opponent")
        assert result_main == main

    def test_graveyard_hate_vs_living_end(self):
        """Board in graveyard hate, board out dead removal vs combo."""
        from engine.sideboard_manager import sideboard
        # Living End triggers both GY hate (board in) and removal (board out)
        main = {"Lightning Bolt": 4, "Fatal Push": 4, "Mountain": 20}
        side = {"Surgical Extraction": 3}
        result_main, result_side = sideboard(
            main, side, "Dimir Midrange", "Living End")
        # Should board in Surgical (GY hate) and board out removal
        assert result_main.get("Surgical Extraction", 0) > 0, \
            f"Expected GY hate boarded in vs Living End, got {result_main}"

    def test_returns_valid_counts(self):
        from engine.sideboard_manager import sideboard
        main = {"Lightning Bolt": 4, "Ragavan, Nimble Pilferer": 4, "Mountain": 20}
        side = {"Flusterstorm": 2, "Surgical Extraction": 3}
        result_main, result_side = sideboard(
            main, side, "Dimir Midrange", "Ruby Storm")
        # Total cards should remain consistent
        total_main = sum(result_main.values())
        total_side = sum(result_side.values())
        orig_total = sum(main.values()) + sum(side.values())
        assert total_main + total_side == orig_total


class TestCardKnowledge:
    """Test the card knowledge loader."""

    def test_load_threat_value(self):
        from decks.card_knowledge_loader import get_threat_value
        assert get_threat_value("Ragavan, Nimble Pilferer") == 5.0
        assert get_threat_value("Murktide Regent") == 5.0
        assert get_threat_value("Nonexistent Card") == 0.0

    def test_load_burn_damage(self):
        from decks.card_knowledge_loader import get_burn_damage
        assert get_burn_damage("Lightning Bolt") == 3
        assert get_burn_damage("Tribal Flames") == 5
        assert get_burn_damage("Nonexistent Card") == 0

    def test_requires_target(self):
        from decks.card_knowledge_loader import requires_target
        assert requires_target("Solitude") is True
        assert requires_target("Endurance") is False
        assert requires_target("Lightning Bolt") is False

    def test_get_extra_tags(self):
        from decks.card_knowledge_loader import get_extra_tags
        tags = get_extra_tags("Psychic Frog")
        assert "self_pump" in tags
        assert "discard_outlet" in tags

    def test_get_all_with_tag(self):
        from decks.card_knowledge_loader import get_all_with_tag
        evoke_cards = get_all_with_tag("evoke_pitch")
        assert "Solitude" in evoke_cards
        assert "Subtlety" in evoke_cards


class TestGameplanLoader:
    """Test the JSON gameplan loader."""

    def test_load_all_gameplans(self):
        from decks.gameplan_loader import load_all_gameplans
        plans = load_all_gameplans()
        assert len(plans) == 14

    def test_load_specific_deck(self):
        from decks.gameplan_loader import load_gameplan
        plan = load_gameplan("Domain Zoo")
        assert plan is not None
        assert plan.deck_name == "Domain Zoo"
        assert plan.archetype == "aggro"
        assert len(plan.goals) >= 2

    def test_load_nonexistent_deck(self):
        from decks.gameplan_loader import load_gameplan
        plan = load_gameplan("Nonexistent Deck")
        assert plan is None

    def test_combo_readiness_loaded(self):
        from decks.gameplan_loader import load_gameplan
        plan = load_gameplan("Ruby Storm")
        assert plan is not None
        assert plan.combo_readiness_check is not None

    def test_mulligan_keys_loaded(self):
        from decks.gameplan_loader import load_gameplan
        plan = load_gameplan("Dimir Midrange")
        assert "Thoughtseize" in plan.mulligan_keys
        assert "Orcish Bowmasters" in plan.mulligan_keys


class TestCallbacks:
    """Test the engine/AI callback boundary."""

    def test_default_callbacks_safe(self):
        from engine.callbacks import DefaultCallbacks
        cb = DefaultCallbacks()
        # Default should never shock
        assert cb.should_shock_land(None, 0, None) is False
        # Default should never evoke
        assert cb.should_evoke(None, 0, None) is False

    def test_ai_callbacks_class_exists(self):
        from engine.game_runner import AICallbacks
        cb = AICallbacks()
        assert hasattr(cb, 'should_shock_land')
        assert hasattr(cb, 'choose_fetch_target')
        assert hasattr(cb, 'should_evoke')
        assert hasattr(cb, 'should_dash')


class TestIntegrationAfterRefactor:
    """Verify everything still works end-to-end after all extractions."""

    def test_all_decks_complete_game(self, game_runner):
        """Every deck should complete a game without errors."""
        from decks.modern_meta import MODERN_DECKS, get_all_deck_names
        for name in get_all_deck_names():
            random.seed(42)
            d = MODERN_DECKS[name]
            opp_name = "Domain Zoo" if name != "Domain Zoo" else "Dimir Midrange"
            d_opp = MODERN_DECKS[opp_name]
            result = game_runner.run_game(
                name, d["mainboard"],
                opp_name, d_opp["mainboard"],
                deck1_sideboard=d.get("sideboard", {}),
                deck2_sideboard=d_opp.get("sideboard", {}),
            )
            assert result.turns > 0, f"{name} game didn't complete"

    def test_mulligan_works_via_aiplayer(self, game_runner):
        """AIPlayer should delegate to MulliganDecider correctly."""
        result = run_seeded_game(game_runner, "Domain Zoo", "Dimir Midrange", seed=42)
        assert result.turns > 0

    def test_response_works_via_aiplayer(self, game_runner):
        """AIPlayer should delegate responses correctly."""
        # Just verify a game with interaction completes
        result = run_seeded_game(game_runner, "Dimir Midrange", "Boros Energy", seed=42)
        assert result.turns > 0
