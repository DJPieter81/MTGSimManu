"""Multicolor-urgency strategy tag pays shock land life T1-T3.

A 3+ color deck that runs cards with deep color requirements
(4c Omnath, 4/5c Control) needs untapped multicolor mana on T1-T3
or its multi-color spells get stranded uncast. The current
`evaluate_board` at full life with no clock scores
`pay 2 life → enter untapped` and `skip → enter tapped` as
near-equal (or skip slightly higher), so the AI defers the life
payment indefinitely.

# Rule the test names

If the player's gameplan declares `strategy_tags` containing
`multicolor_urgency` AND the turn is ≤ 3, the optional-cost
decision for `pay life → ETB untapped` (shock lands) returns
True (pay) regardless of `evaluate_board`'s tie-break.

# Generic by tag

The override is gameplan-driven, not deck-name-driven. Any deck
whose mana base has 3+ colors with significant double-color
requirements opts in by adding the tag. No hardcoded card or
deck names.
"""
from __future__ import annotations

import pytest

from ai.gameplan import create_goal_engine


class TestMulticolorUrgencyTagPaysShocks:
    """Gameplan-driven override for shock land payment in multicolor
    decks during T1-T3."""

    def test_4c_omnath_gameplan_declares_multicolor_urgency_tag(self):
        """4c Omnath's gameplan must declare `multicolor_urgency`
        in strategy_tags. This is the gameplan-side declaration
        that wires the engine override."""
        engine = create_goal_engine("4c Omnath")
        assert engine is not None, (
            "4c Omnath gameplan failed to load.")
        gameplan = engine.gameplan
        tags = gameplan.strategy_tags
        assert 'multicolor_urgency' in tags, (
            f"4c Omnath strategy_tags = {tags}. The "
            f"`multicolor_urgency` tag is the load-bearing signal "
            f"that the runner-side optional-cost decision uses to "
            f"force-pay shock lands T1-T3 — without it, "
            f"`evaluate_board` ties at T1 and the AI skips the "
            f"life payment, leaving Omnath without untapped color "
            f"access for its 4-color cards.")

    def test_other_decks_without_tag_unaffected(self):
        """Anchor: a deck without the tag (Boros Energy = 2 colors,
        no multicolor urgency) does NOT have the tag declared."""
        engine = create_goal_engine("Boros Energy")
        if engine is None:
            pytest.skip("Boros Energy gameplan not loaded")
        gameplan = engine.gameplan
        tags = gameplan.strategy_tags
        assert 'multicolor_urgency' not in tags, (
            f"Boros Energy declares multicolor_urgency tag — but "
            f"it's a 2-color deck and doesn't need T1-T3 shock-pay "
            f"override. Tag should be reserved for 3+ color decks.")
