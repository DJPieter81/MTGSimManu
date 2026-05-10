"""Self-pump / scale-over-time threat scales with parsed N from oracle.

Eighth audit row beyond the original four — extends the
projection-blindspot pattern to `ai/evaluator.py`'s self-pump
branch at line 363, where the same boolean detection of "put a
+1/+1 counter" / "gets +" recurred with `ORACLE_SCALE_OVER_TIME_BONUS`
flat.

# Mechanic

Two oracle phrasings dominate self-pump effects:
- (a) `put N +1/+1 counters on …` — N copies of the buff
- (b) `gets +N/+M` — power/toughness pump magnitude

Boolean detection treated `+1/+1` the same as `+5/+5`. Walking
Ballista (X +1/+1 counters), Slickshot Show-Off (+2/+0 prowess),
larger-N pump cards all collapsed onto a single bonus.

# Failing-test rule

Two cards with `gets +1/+0` and `gets +5/+5` oracle phrasings
must score distinct threat-value bonuses. Pre-fix both = 1 ×
ORACLE_SCALE_OVER_TIME_BONUS. Post-fix scales with max(N, M).
"""
from __future__ import annotations

import pytest

from ai.evaluator import _ability_bonus


class _MockTemplate:
    def __init__(self, oracle_text="", tags=None):
        self.oracle_text = oracle_text
        self.tags = set(tags or [])
        self.keywords = set()
        self.is_creature = True
        self.is_artifact = False
        self.is_enchantment = False
        self.is_land = False
        self.is_instant = False
        self.is_sorcery = False


class TestScaleOverTimeBonusScalesWithN:
    """Self-pump / +1/+1 counter / +N/+M power pump threat-value
    must scale with N parsed from oracle text."""

    def test_small_vs_large_pump_score_differently(self):
        """Cards getting `+1/+0` vs `+5/+5` must score distinct
        bonuses. Pre-fix both = +X flat. Post-fix multi-pump
        strictly higher."""
        small = _MockTemplate(
            oracle_text="When this creature attacks, it gets +1/+0 until end of turn.",
            tags={'pump'},
        )
        large = _MockTemplate(
            oracle_text="When this creature attacks, it gets +5/+5 until end of turn.",
            tags={'pump'},
        )
        small_bonus = _ability_bonus(small)
        large_bonus = _ability_bonus(large)
        assert large_bonus > small_bonus, (
            f"Small pump bonus = {small_bonus:.2f}, large pump = "
            f"{large_bonus:.2f}. The flat `ORACLE_SCALE_OVER_TIME_BONUS` "
            f"scorer treated +1/+0 the same as +5/+5. Fix: scale by "
            f"max(N, M) parsed from `gets +N/+M` regex."
        )

    def test_single_vs_multi_counter_pump_score_differently(self):
        """Cards putting `a +1/+1 counter` vs `three +1/+1 counters`
        must score distinct bonuses."""
        single = _MockTemplate(
            oracle_text="When this enters, put a +1/+1 counter on target creature.",
            tags={'pump'},
        )
        multi = _MockTemplate(
            oracle_text="When this enters, put three +1/+1 counters on target creature.",
            tags={'pump'},
        )
        assert _ability_bonus(multi) > _ability_bonus(single), (
            "Multi-counter pump must score strictly higher than "
            "single-counter pump.")

    def test_no_pump_keyword_no_pump_bonus_inflation(self):
        """Anchor: a card with no pump phrasing is unaffected by
        the new regex (no pump_n found; baseline scoring)."""
        plain = _MockTemplate(
            oracle_text="When this enters, draw a card.",
            tags={'card_advantage'},
        )
        bonus = _ability_bonus(plain)
        # Should be a positive but bounded value (card_advantage etc).
        # The pump regex must not have falsely matched on this oracle.
        assert 0 < bonus < 20, (
            f"Plain card_advantage bonus = {bonus:.2f}. Either the "
            f"pump regex incorrectly matched, or scoring is broken.")
