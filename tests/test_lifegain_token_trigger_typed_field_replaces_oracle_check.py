"""Pre-computed typed field must detect 'whenever you gain life, create a token' triggers.

Rule: any permanent whose oracle text contains 'whenever you gain life' combined
with 'create' and 'token' must have has_lifegain_token_trigger=True at load time,
replacing runtime oracle-text inspection in permanent_effects.py.

Fixture carrier note: Attended Healer is used here because its oracle text
exercises the 'whenever you gain life, create a 1/1 white Cat creature token'
pattern. The mechanic under test is generic: any card matching this combination
gets the typed field set True.  Cat Collector exercises the same mechanic with a
slightly different phrasing.  Dawn of Hope (lifegain draw + separate token
activate) and Starscape Cleric (lifegain drain, no token) verify the exclusion
side — cards that contain some but not all of the trigger keywords.
"""
from __future__ import annotations

import pytest
from engine.cards import CardTemplate, CardType
from engine.mana import ManaCost


def _template(oracle: str, name: str = "__test__") -> CardTemplate:
    return CardTemplate(
        name=name,
        oracle_text=oracle,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=2, white=1),
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
# parse_has_lifegain_token_trigger
# ---------------------------------------------------------------------------

class TestHasLifegainTokenTriggerDetection:
    def test_lifegain_token_trigger_with_all_three_keywords_detected(self):
        # "Whenever you gain life, create a token" — generic lifegain-token mechanic
        t = _template(
            "Whenever you gain life for the first time each turn, "
            "create a 1/1 white Soldier creature token."
        )
        assert t.has_lifegain_token_trigger is True

    def test_attended_healer_oracle_pattern_detected(self):
        # Attended Healer — the canonical Cat-token lifegain trigger in Modern
        t = _template(
            "Whenever you gain life for the first time each turn, "
            "create a 1/1 white Cat creature token.\n"
            "{2}{W}: Another target Cleric gains lifelink until end of turn."
        )
        assert t.has_lifegain_token_trigger is True

    def test_cat_collector_oracle_pattern_detected(self):
        # Cat Collector — same mechanic, slightly different phrasing
        t = _template(
            "When this creature enters, create a Food token.\n"
            "Whenever you gain life for the first time during each of your turns, "
            "create a 1/1 white Cat creature token."
        )
        assert t.has_lifegain_token_trigger is True

    def test_lifegain_without_create_is_false(self):
        # Starscape Cleric — gains life triggers drain, no token
        t = _template("Whenever you gain life, each opponent loses 1 life.")
        assert t.has_lifegain_token_trigger is False

    def test_lifegain_without_token_keyword_is_false(self):
        # Lifegain triggers counter/draw but no token
        t = _template("Whenever you gain life, put a +1/+1 counter on this creature.")
        assert t.has_lifegain_token_trigger is False

    def test_create_token_without_lifegain_trigger_is_false(self):
        # Activated ability creates tokens but no lifegain trigger
        t = _template("{3}{W}: Create a 1/1 white Soldier creature token with lifelink.")
        assert t.has_lifegain_token_trigger is False

    def test_empty_oracle_is_false(self):
        t = _template("")
        assert t.has_lifegain_token_trigger is False

    def test_no_oracle_clause_at_all_is_false(self):
        t = _template("Flying\nVigilance")
        assert t.has_lifegain_token_trigger is False


# ---------------------------------------------------------------------------
# parse_lifegain_token_type
# ---------------------------------------------------------------------------

class TestLifegainTokenType:
    def test_cat_in_oracle_returns_cat(self):
        # Oracle mentions 'cat' → token type should be 'cat'
        t = _template(
            "Whenever you gain life for the first time each turn, "
            "create a 1/1 white Cat creature token."
        )
        assert t.lifegain_token_type == "cat"

    def test_no_cat_returns_creature(self):
        # Generic creature token (Soldier, Cleric, etc.) → 'creature'
        t = _template(
            "Whenever you gain life for the first time each turn, "
            "create a 1/1 white Soldier creature token."
        )
        assert t.lifegain_token_type == "creature"

    def test_default_for_non_trigger_card_is_creature(self):
        # Cards without the trigger should still have the default 'creature'
        t = _template("Flying")
        assert t.lifegain_token_type == "creature"
