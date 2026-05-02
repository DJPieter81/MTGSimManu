"""Engine-disruption premium for opponent permanents in combo
matchups.

Composition contract:

    effective_threat(card) = permanent_threat(card, opp, game)
                           + engine_disruption_value(card, opp, game)

`permanent_threat` (ai/permanent_threat.py) is the marginal drop
in opp's `position_value` when `card` is removed. It captures
resolved-board impact: a 4/4 attacker contributes ~half the clock,
a Hammer-equipped 1/1 contributes the equipment damage swing, etc.

What it does NOT capture is plan-disruption ABOVE board impact: a
Ruby Medallion is currently doing nothing on the battlefield
(`position_value` sees one artifact + one tapped mana source), but
removing it delays the opponent's storm chain by ~1 mana / ~1 turn.
That delta — the difference between "this card affects the current
position" and "this card affects opp's plan to win" — is what this
helper adds.

Gating (both required, no fallthrough):
    1. Opponent's gameplan archetype == 'combo'.
    2. `card.name` appears in any of opp's gameplan goals'
       `card_roles["engines"]` or `card_roles["payoffs"]` lists.

When either gate fails, the premium is identically 0.0 — the
target picker falls back to pure `permanent_threat` semantics.

The premium magnitude is `role_delay_turns × combo_urgency`:

    combo_urgency = max(0.0, COMBO_WINDOW - combo_clock(opp_snap))

`combo_clock` is computed from opp's perspective (their hand,
mana, GY, storm count). The closer they are to combo, the more
valuable a turn of disruption is. At `combo_clock >= COMBO_WINDOW`
(combo is far enough away that we have other lines), the premium
collapses to 0.

Constants justified inline; the rule each constant encodes is
pinned by tests/test_combo_engine_disruption_premium.py.

This module never inspects oracle text, never branches on card
names, never names a deck in code. The only inputs are opp's
declared gameplan (data) and opp's current `combo_clock`
(derived from existing engine state).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState, PlayerState


# Modern combo decks fire by turn 4-5 in expectation (Storm T4-5,
# Living End T3-4 with cascade, Goryo's T3-4, Amulet Titan T3 on
# the nut draw). Disruption is worth a full turn-of-delay-times-
# role-weight when opp is one turn away (combo_clock == 1.0); it
# scales linearly to zero at this window. The 5.0 constant is the
# fall-off horizon, not a magic threshold — it expresses "if opp
# is more than five turns from combo, removing an engine piece
# does not buy us anything we can't recover via normal play."
COMBO_WINDOW = 5.0

# Per-role expected combo-turns delayed by removing one copy of a
# card in that role. Both values are derived from the standard
# 4-of redundancy assumption in a 60-card combo deck:
#
#   engines = 1.0
#     A combo deck typically runs N=4 cost-reducers / planeswalker
#     engines. Removing one strips 1/N = 25% of the cumulative
#     cost-reduction effect over the ~8-resource Storm assembly
#     model in ai/clock.py. That is ~2 mana saved over the chain →
#     ~1 turn of additional ramp required. (Ramp combos like
#     Amulet Titan are similar: removing one Amulet of Vigor turns
#     a T3 into a T4, give or take.)
#
#   payoffs = 2.0
#     A combo deck typically runs N=4 payoffs (Grapeshot, Empty
#     the Warrens, Living End, Atraxa for Goryo's). Removing the
#     resolved or in-flight payoff forces a re-tutor / redraw
#     cycle. With N=4 in a 60-card library, the expected number of
#     draws to find the next copy is ~15 cards / 4 = ~4 cards = ~2
#     full turns. Re-tutoring (Wish, etc.) costs the tutor's mana
#     plus the payoff's mana, also ~2 turns of resource recovery.
_ROLE_DELAY_TURNS = {
    "engines": 1.0,
    "payoffs": 2.0,
}


def _opp_gameplan(opp):
    """Return opp's loaded DeckGameplan, or None if unavailable."""
    deck_name = getattr(opp, "deck_name", "") or ""
    if not deck_name:
        return None
    try:
        from ai.gameplan import get_gameplan
        return get_gameplan(deck_name)
    except Exception:
        return None


def _role_delay_for(card_name: str, plan) -> float:
    """Highest role-delay value for `card_name` across all of
    `plan`'s goals. Returns 0.0 if the card is not in any of the
    disruption-relevant role lists. We take the MAX across goals
    to handle multi-goal gameplans that place the same card in
    different roles per phase (e.g. Amulet Titan goal[0] DEPLOY
    has Spelunking in 'engines' while a later goal could repeat
    it in 'enablers')."""
    best = 0.0
    for goal in getattr(plan, "goals", []):
        roles = getattr(goal, "card_roles", None) or {}
        for role_name, role_cards in roles.items():
            if card_name in role_cards:
                d = _ROLE_DELAY_TURNS.get(role_name, 0.0)
                if d > best:
                    best = d
    return best


def engine_disruption_value(card: "CardInstance",
                            opp: "PlayerState",
                            game: "GameState") -> float:
    """Premium added to `permanent_threat(card)` when removing
    `card` disrupts opp's combo plan.

    Returns 0.0 unless ALL of the following hold:
      (a) opp has a registered gameplan;
      (b) `gameplan.archetype == "combo"`;
      (c) `card.name` is in any goal's `card_roles["engines"]` or
          `card_roles["payoffs"]`;
      (d) opp's `combo_clock` is below `COMBO_WINDOW`.

    See module docstring for the composition contract and constant
    derivations. This function is the single source of truth for
    "how much extra do we value removing opp's engine piece" — all
    callers compose it additively with permanent_threat.
    """
    plan = _opp_gameplan(opp)
    if plan is None:
        return 0.0
    if getattr(plan, "archetype", "") != "combo":
        return 0.0

    name = (getattr(card, "name", None)
            or getattr(getattr(card, "template", None), "name", None))
    if not name:
        return 0.0

    role_delay = _role_delay_for(name, plan)
    if role_delay == 0.0:
        return 0.0

    # Build snap from opp's perspective so combo_clock reflects
    # opp's hand / mana / GY / storm count, not ours. This is the
    # only way combo_clock's `my_*` semantics make sense for the
    # opponent's combo readiness.
    from ai.ev_evaluator import snapshot_from_game
    from ai.clock import combo_clock
    opp_snap = snapshot_from_game(game, opp.player_idx)
    urgency = max(0.0, COMBO_WINDOW - combo_clock(opp_snap))
    if urgency == 0.0:
        return 0.0

    return role_delay * urgency
