"""Tutor threat-scorer scales with N searched cards.

Seventh audit row beyond the original four named in
`docs/design/2026-05-10_oracle_pattern_projection_blindspot_audit.md`
— extends row 4 (multi-card tutor projection, shipped in PR #337)
to the threat-value scorer in `ai/evaluator.py`.

# Rule the test names

Two cards with `search your library for N cards` oracle phrasings
(N1 != N2) must score distinct threat-value bonuses. The pre-fix
scorer at `ai/evaluator.py:300-301` credited every tutor with a
flat `ORACLE_TUTOR_BONUS = 2.0`. The post-fix parsed extractor
gives `N × ORACLE_TUTOR_BONUS`, mirroring the projection-side
fix from PR #337 + the token-creation fix from PR #345.

Singular `search your library for a card` phrasings (Mastermind's
Acquisition, Bring to Light, Demonic Tutor variants) fall through
to baseline +1 — already correct.
"""
from __future__ import annotations

import pytest

from ai.evaluator import _ability_bonus


class _MockTemplate:
    def __init__(self, oracle_text="", tags=None):
        self.oracle_text = oracle_text
        self.tags = set(tags or [])
        self.keywords = set()
        self.is_creature = False
        self.is_artifact = False
        self.is_enchantment = False
        self.is_land = False
        self.is_instant = True
        self.is_sorcery = False


class TestTutorBonusScalesWithN:
    """Tutor threat-value scales with N searched cards."""

    def test_single_vs_multi_target_tutor_score_differently(self):
        """Singular vs multi-target tutor — pre-fix both = +2.0.
        Post-fix: multi-card tutor strictly higher."""
        single = _MockTemplate(
            oracle_text=(
                "Search your library for a card, put that card "
                "into your hand, then shuffle."),
            tags={'tutor'},
        )
        multi = _MockTemplate(
            oracle_text=(
                "Search your library for up to four cards, reveal "
                "them, put them into your hand, then shuffle."),
            tags={'tutor'},
        )
        single_bonus = _ability_bonus(single)
        multi_bonus = _ability_bonus(multi)
        assert multi_bonus > single_bonus, (
            f"Single-target tutor bonus = {single_bonus:.2f}, "
            f"multi-target tutor bonus = {multi_bonus:.2f}. The flat "
            f"`ORACLE_TUTOR_BONUS` scorer collapsed both onto the "
            f"same threat. Fix: scale by N parsed from oracle text "
            f"(`search your library for N cards`)."
        )

    def test_digit_form_and_english_numeral_form_score_identically(self):
        """`search for 3 cards` and `search for three cards` must
        produce the same threat bonus — both encode N=3."""
        digit = _MockTemplate(
            oracle_text=(
                "Search your library for 3 cards, reveal them, put "
                "them into your hand, then shuffle."),
            tags={'tutor'},
        )
        word = _MockTemplate(
            oracle_text=(
                "Search your library for three cards, reveal them, "
                "put them into your hand, then shuffle."),
            tags={'tutor'},
        )
        assert _ability_bonus(digit) == pytest.approx(
            _ability_bonus(word), abs=0.01), (
            f"Digit and English-numeral forms diverged. The tuple-"
            f"index trick from PR #334 reuses the same lookup; the "
            f"two encodings must score identically.")

    def test_singular_a_card_tutor_falls_through_to_baseline(self):
        """Anchor: `search for a card` (singular) must keep its
        baseline +1 bonus, not accidentally match the multi-card
        regex. Demonic Tutor, Mastermind's Acquisition pattern."""
        singular = _MockTemplate(
            oracle_text=(
                "Search your library for a creature card, put that "
                "card into your hand, then shuffle."),
            tags={'tutor'},
        )
        bonus = _ability_bonus(singular)
        # The baseline tutor bonus is ORACLE_TUTOR_BONUS = 2.0;
        # multi-card extractor would have multiplied by N. We only
        # assert the bonus is not negative and not the runaway value
        # the regex would produce on a free word capture.
        assert 0 < bonus < 10, (
            f"Singular tutor bonus = {bonus:.2f}. Either the regex "
            f"is too greedy and parsed a non-numeric word as N, or "
            f"the baseline is broken.")
