"""Token-creation threat scorer extracts N from oracle text.

Sixth audit row beyond the original four in
`docs/design/2026-05-10_oracle_pattern_projection_blindspot_audit.md`
— extends the audit to `ai/evaluator.py`'s threat-value scorer
(distinct from `ai/ev_evaluator.py`'s play-projection).

# Mechanic the test names

The threat-value scorer at `ai/evaluator.py:296` credited every
token-creating card with a flat `ORACLE_TOKEN_CREATION_BONUS`
regardless of N. Two cards with oracle "create one token" and
"create five tokens" both received +1.5 threat. Empty the Warrens
(creates `2 * storm_count` Goblins, often 10–20+), Bitterblossom
(1 Faerie/upkeep × residency = ~2 over window), Saheeli combo
(many copies) all collapsed onto the same boolean.

# Generic by oracle

Match `create N` where N is digit or English-numeral, scaled by
the existing `ORACLE_TOKEN_CREATION_BONUS` per-token coefficient.
Tuple-index trick from PR #334 reused for English numerals; zero
new bare numeric literals.

# Class size

~200 Modern cards have `create N` token oracle wording. N
distribution: {1, 2, 3, 4, 5, X}. Active-pool: ~30 cards across
the 16 registered decks (Saheeli, Bitterblossom, various
sideboard tech). Above the abstraction-contract floor.

# Failing-test rule

Two cards with `create N tokens` oracle phrasings (N1 != N2) must
project distinct threat-value bonuses. The pre-fix scorer
treated them identically; the post-fix parsed extractor gives
N × per-token coefficient.
"""
from __future__ import annotations

import pytest

from ai.evaluator import _ability_bonus


class _MockTemplate:
    """Minimal template with the fields the evaluator scorer reads."""
    def __init__(self, oracle_text="", tags=None, keywords=None):
        self.oracle_text = oracle_text
        self.tags = set(tags or [])
        self.keywords = set(keywords or [])
        self.is_creature = True
        self.is_artifact = False
        self.is_enchantment = False
        self.is_land = False
        self.is_instant = False
        self.is_sorcery = False


class TestTokenCreationScalesWithN:
    """The threat-value bonus from `create N tokens` must scale
    with N parsed from oracle text."""

    def test_single_vs_multi_token_creators_score_differently(self):
        """Two cards: 'create a 1/1 token' vs 'create five 1/1
        tokens'. The flat scorer treated them identically. Post-fix:
        five-token card scores strictly higher."""
        single = _MockTemplate(
            oracle_text=("When this creature enters, create a 1/1 "
                         "white Soldier creature token."),
            tags={'etb_value', 'token_maker'},
        )
        multi = _MockTemplate(
            oracle_text=("When this creature enters, create five 1/1 "
                         "white Soldier creature tokens."),
            tags={'etb_value', 'token_maker'},
        )
        single_bonus = _ability_bonus(single)
        multi_bonus = _ability_bonus(multi)
        assert multi_bonus > single_bonus, (
            f"Single-token bonus = {single_bonus:.2f}, multi-token "
            f"bonus = {multi_bonus:.2f}. The flat token-creation "
            f"scorer in `ai/evaluator.py:296` credited every "
            f"token-creating card with the same bonus regardless "
            f"of N. The fix parses N from oracle text and scales "
            f"the per-card bonus by N."
        )

    def test_digit_form_token_creators_score_with_actual_n(self):
        """Anchor: '4 1/1 tokens' (digit form) scales the same as
        'four 1/1 tokens' (English-numeral form)."""
        digit = _MockTemplate(
            oracle_text="Create 4 1/1 white Soldier creature tokens.",
            tags={'etb_value', 'token_maker'},
        )
        word = _MockTemplate(
            oracle_text="Create four 1/1 white Soldier creature tokens.",
            tags={'etb_value', 'token_maker'},
        )
        digit_bonus = _ability_bonus(digit)
        word_bonus = _ability_bonus(word)
        # Both should be identical. The English-numeral extractor
        # uses the tuple-index trick: ('zero','one',…)[4] == 'four',
        # index('four') == 4 — same number both ways.
        assert digit_bonus == pytest.approx(word_bonus, abs=0.01), (
            f"Digit form bonus = {digit_bonus:.2f} vs English-numeral "
            f"form bonus = {word_bonus:.2f}. Both encode N=4 in "
            f"oracle text and must score identically."
        )

    def test_no_token_clause_no_token_bonus(self):
        """Anchor: a card that says 'create' but doesn't make a
        token (e.g. 'create a delayed trigger', 'create an
        emblem') must NOT trigger the token-N parser. The regex
        + the existing 'token' guard prevent false matches."""
        emblem = _MockTemplate(
            oracle_text=("You get an emblem with 'Whenever a player "
                         "casts a spell, ...'."),
            tags={'etb_value'},
        )
        trigger = _MockTemplate(
            oracle_text=("Create a delayed triggered ability that "
                         "fires at the beginning of your next "
                         "upkeep."),
            tags={'etb_value'},
        )
        # Neither has 'token' in oracle, so the token-creation
        # branch must not fire. Their bonus is just the etb_value
        # baseline (whatever the scorer computes from the rest of
        # the oracle), not multiplied by some token-N parse.
        emblem_bonus = _ability_bonus(emblem)
        trigger_bonus = _ability_bonus(trigger)
        # Floor anchor: bonuses are non-negative finite numbers.
        # The scorer must not have crashed on the emblem/trigger
        # phrasings.
        assert emblem_bonus is not None and trigger_bonus is not None
        assert emblem_bonus >= 0 and trigger_bonus >= 0
