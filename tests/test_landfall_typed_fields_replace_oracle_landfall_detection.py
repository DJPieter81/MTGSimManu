"""Pre-computed typed fields must cover landfall and library-search trigger detection.

Rule: any permanent whose oracle text carries a landfall trigger must be
identified at load time via has_landfall=True, and the multi-trigger
life/mana/damage amounts must be pre-computed rather than re-derived by
re.search at runtime.

The library-search opponent trigger ("whenever an opponent searches … their
library") follows the same rule: detection and per-trigger draw flag must be
typed fields, not runtime oracle substring checks.

Test card fixtures are named in docstrings, not in test function names (per
repo convention).  Parse-function tests use synthetic CardTemplate instances
so no DB load is required and they run in < 1 s.
"""
from __future__ import annotations
import pytest
from engine.cards import CardTemplate, CardType
from engine.mana import ManaCost


# ---------------------------------------------------------------------------
# Minimal synthetic CardTemplate factory
# ---------------------------------------------------------------------------

def _template(oracle: str, name: str = "__test__") -> CardTemplate:
    return CardTemplate(
        name=name,
        oracle_text=oracle,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=2),
        supertypes=[],
        subtypes=[],
        keywords=set(),
        abilities=[],
        color_identity=set(),
        produces_mana=[],
        enters_tapped=False,
        tags=set(),
    )


# ---------------------------------------------------------------------------
# Tests for parse_has_landfall
# ---------------------------------------------------------------------------

class TestHasLandfall:
    def test_landfall_keyword_sets_flag(self):
        # Any card with the word "landfall" in its oracle gets has_landfall=True.
        # Covers Omnath, Hedron Crab, Lotus Cobra, etc.
        t = _template(
            "Landfall — Whenever a land enters the battlefield under your "
            "control, put a +1/+1 counter on this creature."
        )
        assert t.has_landfall is True

    def test_land_enters_pattern_sets_flag(self):
        # "land enters" phrasing without the keyword header also triggers landfall.
        t = _template(
            "Whenever a land enters the battlefield under your control, "
            "you may draw a card."
        )
        assert t.has_landfall is True

    def test_whenever_a_land_pattern_sets_flag(self):
        # "whenever a land" phrasing
        t = _template(
            "Whenever a land enters the battlefield under your control, "
            "add {G}."
        )
        assert t.has_landfall is True

    def test_no_landfall_clause_is_false(self):
        # A card with no landfall text must have has_landfall=False.
        t = _template("Whenever this creature attacks, create a 1/1 token.")
        assert t.has_landfall is False

    def test_empty_oracle_is_false(self):
        t = _template("")
        assert t.has_landfall is False


# ---------------------------------------------------------------------------
# Tests for parse_has_library_search_opponent_trigger
# ---------------------------------------------------------------------------

class TestLibrarySearchOpponentTrigger:
    def test_opponent_library_search_trigger_detected(self):
        # Wan Shi Tong, It's Library — "whenever an opponent searches their
        # library" pattern.  Detection must use the typed field, not runtime
        # oracle substring at game time.
        t = _template(
            "Flying\nWhenever an opponent searches their library, "
            "put a +1/+1 counter on Wan Shi Tong, It's Library."
        )
        assert t.has_library_search_opponent_trigger is True

    def test_non_library_search_trigger_is_false(self):
        # "whenever an opponent casts" — does not have the library search trigger
        t = _template(
            "Whenever an opponent casts a spell, you may draw a card."
        )
        assert t.has_library_search_opponent_trigger is False

    def test_empty_oracle_is_false(self):
        t = _template("")
        assert t.has_library_search_opponent_trigger is False


# ---------------------------------------------------------------------------
# Tests for parse_library_search_trigger_draws_card
# ---------------------------------------------------------------------------

class TestLibrarySearchTriggerDrawsCard:
    def test_draw_flag_set_when_trigger_and_draw_present(self):
        # A library-search opponent trigger that also says "draw a card" must
        # set library_search_trigger_draws_card=True.
        t = _template(
            "Whenever an opponent searches their library, "
            "put a +1/+1 counter on this creature and draw a card."
        )
        assert t.library_search_trigger_draws_card is True

    def test_draw_flag_false_when_no_draw_in_trigger(self):
        # Library-search trigger but no "draw a card" text.
        t = _template(
            "Whenever an opponent searches their library, "
            "put a +1/+1 counter on this creature."
        )
        assert t.library_search_trigger_draws_card is False

    def test_draw_flag_false_when_no_library_search_trigger(self):
        # "draw a card" present but no library-search opponent trigger.
        t = _template(
            "When this creature enters, draw a card."
        )
        assert t.library_search_trigger_draws_card is False


# ---------------------------------------------------------------------------
# Tests for parse_landfall_first_life_gain
# ---------------------------------------------------------------------------

class TestLandfallFirstLifeGain:
    def test_first_time_life_gain_parsed(self):
        # Omnath pattern: first landfall gains 4 life
        t = _template(
            "Landfall — Whenever a land enters the battlefield under your control, "
            "choose one —\n"
            "• The first time this ability resolves this turn, you gain 4 life.\n"
            "• The second time, add {R}{G}.\n"
            "• The third time, this deals 4 damage."
        )
        assert t.landfall_first_life_gain == 4

    def test_no_first_time_clause_returns_zero(self):
        t = _template(
            "Landfall — Whenever a land enters the battlefield under your "
            "control, put a +1/+1 counter on this creature."
        )
        assert t.landfall_first_life_gain == 0

    def test_empty_oracle_returns_zero(self):
        t = _template("")
        assert t.landfall_first_life_gain == 0


# ---------------------------------------------------------------------------
# Tests for parse_landfall_third_damage
# ---------------------------------------------------------------------------

class TestLandfallThirdDamage:
    def test_third_time_damage_parsed(self):
        # Omnath pattern: third landfall deals 4 damage
        t = _template(
            "Landfall — Whenever a land enters the battlefield under your control:\n"
            "First time: you gain 4 life.\n"
            "Second time: add {R}{G}.\n"
            "Third time: this deals 4 damage to any target."
        )
        assert t.landfall_third_damage == 4

    def test_no_third_time_clause_returns_zero(self):
        t = _template(
            "Landfall — Whenever a land enters the battlefield under your "
            "control, put a +1/+1 counter on this creature."
        )
        assert t.landfall_third_damage == 0

    def test_empty_oracle_returns_zero(self):
        t = _template("")
        assert t.landfall_third_damage == 0


# ---------------------------------------------------------------------------
# Tests for parse_landfall_second_mana_colors
# ---------------------------------------------------------------------------

class TestLandfallSecondManaColors:
    def test_second_time_rg_mana_parsed(self):
        # Omnath pattern: second landfall adds {R} and {G}
        t = _template(
            "Landfall — Whenever a land enters under your control:\n"
            "The second time this ability resolves this turn, add {R}{G}.\n"
        )
        colors = t.landfall_second_mana_colors
        assert 'R' in colors
        assert 'G' in colors

    def test_no_second_time_clause_returns_empty(self):
        t = _template(
            "Landfall — Whenever a land enters the battlefield under your "
            "control, put a +1/+1 counter on this creature."
        )
        assert t.landfall_second_mana_colors == ()

    def test_empty_oracle_returns_empty(self):
        t = _template("")
        assert t.landfall_second_mana_colors == ()
