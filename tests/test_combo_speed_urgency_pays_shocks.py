"""Combo-speed-urgency strategy tag pays shock land life T1-T3.

A combo deck that needs early untapped mana to deploy its engine
(Ruby Storm with Ruby Medallion T2, Living End cycling enabler,
Goryo's Vengeance reanimator setup) is hurt by the default
shock-payment policy: `evaluate_board` at full life with no clock
scores `pay 2 life → enter untapped` and `skip → enter tapped` as
near-equal, so the AI defers the life payment, the shock enters
tapped, and the combo's T-1-faster line is foreclosed.

Storm vs Boros N=20 measurement: chain reaches lethal T6-T7 while
Boros wins T5-T6. The 1-turn deficit is exactly the deficit
introduced by an unpayed shock land on T1.

# Rule the test names

If the player's gameplan declares `strategy_tags` containing
`combo_speed_urgency` AND the turn is ≤ 3, the optional-cost
decision for `pay life → ETB untapped` (shock lands) returns
True (pay) regardless of `evaluate_board`'s tie-break.

# Generic by tag

Same shape as the existing `multicolor_urgency` mechanism (in
`engine.game_runner.decide_optional_cost`). The new tag is for
combo decks whose engine cards (Ruby Medallion, Amulet of Vigor,
Past in Flames) need to land 1 turn earlier than the default
mana-tempo evaluator would pay for. No hardcoded card or deck
names; any deck opts in by adding the tag.

Class size: every Modern combo deck that has shocks in its mana
base AND wants T1-T3 untapped mana for engine deployment. Storm,
Goryo's Vengeance, Living End, Amulet Titan, Pinnacle Affinity
(treasure-token combo). Class ≥ 5.
"""
from __future__ import annotations

import pytest

from ai.gameplan import create_goal_engine


class TestComboSpeedUrgencyTagPaysShocks:
    """Gameplan-driven override for shock land payment in combo
    decks during T1-T3 — matches the multicolor_urgency pattern
    but keyed on a different urgency class."""

    def test_ruby_storm_gameplan_declares_combo_speed_urgency_tag(self):
        """Ruby Storm's gameplan must declare `combo_speed_urgency`
        in strategy_tags. This is the gameplan-side declaration
        that wires the engine override.

        Without the tag, Storm fetches Sacred Foundry and lets it
        enter tapped, costing 1 turn of mana — and the Storm chain
        is consistently 1 spell short of lethal vs Boros (N=20:
        15% WR; Storm wins T6-T7, Boros wins T5-T6)."""
        engine = create_goal_engine("Ruby Storm")
        assert engine is not None, (
            "Ruby Storm gameplan failed to load.")
        gameplan = engine.gameplan
        tags = gameplan.strategy_tags
        assert 'combo_speed_urgency' in tags, (
            f"Ruby Storm strategy_tags = {tags}. The "
            f"`combo_speed_urgency` tag is the load-bearing signal "
            f"that the runner-side optional-cost decision uses to "
            f"force-pay shock lands T1-T3 — without it, "
            f"`evaluate_board` ties at T1 and the AI skips the "
            f"life payment, leaving Storm without untapped T2 mana "
            f"to deploy Ruby Medallion on curve."
        )

    def test_boros_energy_does_not_declare_combo_speed_urgency(self):
        """Anchor: aggro decks aren't combos. Boros Energy doesn't
        have a deferred-engine deployment, so it doesn't need the
        T1-T3 force-pay override. (Its existing shock decisions
        already favour pay since it's a tempo deck on a clock.)"""
        engine = create_goal_engine("Boros Energy")
        if engine is None:
            pytest.skip("Boros Energy gameplan not loaded")
        gameplan = engine.gameplan
        tags = gameplan.strategy_tags
        assert 'combo_speed_urgency' not in tags, (
            f"Boros Energy declares combo_speed_urgency tag — but "
            f"it's an aggro deck. The tag is for combo decks that "
            f"need early-engine deployment. Boros is on its own clock "
            f"and its shock-pay decision already lands correctly "
            f"under `evaluate_board`'s normal scoring.")
