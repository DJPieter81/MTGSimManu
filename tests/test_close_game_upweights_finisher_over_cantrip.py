"""M4 part 3 / A2 — `goal=close_game` re-weights play scoring.

Per audit `docs/history/audits/2026-05-16_5panel_bo3_audit.md` §M4 and
the 2026-07-05 calibration-probe finding A2: the GoalEngine flips its
label (`grind_value → close_game`) but nothing downstream of the label
changes — `compute_play_ev` returns the same EV for every goal, so the
"convert advantage into a win" gear-shift is inert.  The M4 spec's
third part is a per-(archetype, goal) signal-weight table in
`ai/strategy_profile.py` so that `goal=close_game` literally re-weights
finisher-role plays up and cantrip/value plays down.

Mechanism pinned without naming a card or a deck:

  - `ai.strategy_profile.goal_weights` is a declarative
    `{archetype: {goal_type_value: {tag: float_weight}}}` table.
    Missing keys fall through to the identity weight — the lookup is
    a total function (mirrors `phase_weights`).
  - The finisher signal comes from GAMEPLAN ROLES, not oracle tags:
    `ev_player` injects the synthetic tag `ROLE_FINISHER_TAG` for any
    card declared in the gameplan's `finishers` / `payoffs` role
    buckets.  Card-specific knowledge stays in `decks/gameplans/*.json`.
  - `ai.ev_evaluator.compute_play_ev` composes the goal weight into
    the same sign-aware preference application used by the life-phase
    gear-shift (`apply_preference_weight`).

These tests pin the *contract* (direction + totality), not the weight
magnitudes.  Raising 1.5 → 1.8 must keep them green.
"""
from __future__ import annotations

import math
import random

import pytest

from ai.clock import LifePhase, life_phase
from ai.gameplan import DeckGameplan, Goal, GoalEngine, GoalType
from ai.ev_evaluator import EVSnapshot, snapshot_from_game
from ai.ev_player import EVPlayer
from ai.strategy_profile import (
    ROLE_FINISHER_TAG,
    apply_preference_weight,
    goal_weight_multiplier,
    goal_weights,
)
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


# ─────────────────────────────────────────────────────────────
# Rule 1 — the audit-named contract: with goal=close_game, a
# finisher-role play outscores a cantrip that outscores it under
# grind_value.
# ─────────────────────────────────────────────────────────────


def test_close_game_upweights_finisher_over_cantrip():
    """close_game must be a real gear, not a label.

    Direction contract for control (the archetype the A2 probe
    exercised — s60104, control at 2 life durdling):

      - finisher-role weight at close_game is a bonus (> 1.0)
      - cantrip weight at close_game is a penalty (< 1.0)
      - a finisher whose base EV trails a cantrip under grind_value
        (identity weights) must overtake it under close_game.
    """
    mult_fin = goal_weight_multiplier(
        "control", GoalType.CLOSE_GAME.value, {ROLE_FINISHER_TAG})
    mult_cantrip = goal_weight_multiplier(
        "control", GoalType.CLOSE_GAME.value, {"cantrip"})

    assert mult_fin > 1.0, (
        f"close_game finisher-role weight must be a bonus (>1.0), got "
        f"{mult_fin}. See ai/strategy_profile.py:goal_weights."
    )
    assert mult_cantrip < 1.0, (
        f"close_game cantrip weight must be a penalty (<1.0), got "
        f"{mult_cantrip}."
    )

    # Ordering flip.  Base EVs chosen so the cantrip is slightly ahead
    # under identity weights (the grind_value ranking that made the
    # goal-flip inert): the close_game gear must invert the order.
    base_finisher = 4.0
    base_cantrip = 5.0

    grind = GoalType.GRIND_VALUE.value
    grind_fin = apply_preference_weight(
        base_finisher,
        goal_weight_multiplier("control", grind, {ROLE_FINISHER_TAG}))
    grind_cantrip = apply_preference_weight(
        base_cantrip,
        goal_weight_multiplier("control", grind, {"cantrip"}))
    assert grind_cantrip > grind_fin, (
        "fixture invariant: under grind_value (identity weights) the "
        "cantrip must be ahead — otherwise the flip below proves nothing."
    )

    close = GoalType.CLOSE_GAME.value
    close_fin = apply_preference_weight(base_finisher, mult_fin)
    close_cantrip = apply_preference_weight(base_cantrip, mult_cantrip)
    assert close_fin > close_cantrip, (
        f"goal=close_game must re-weight the finisher-role play "
        f"({close_fin:.2f}) above the cantrip ({close_cantrip:.2f}). "
        f"A goal that changes no ranking is inert — finding A2/M4."
    )


# ─────────────────────────────────────────────────────────────
# Rule 2 — the lookup is a total function: unknown archetype /
# goal / tag / None goal all return identity.
# ─────────────────────────────────────────────────────────────


def test_goal_weight_lookup_is_total_function():
    all_goals = [g.value for g in GoalType] + [None, "not_a_goal"]
    for archetype in ("not_a_real_archetype", "control", "aggro"):
        for goal in all_goals:
            mult = goal_weight_multiplier(archetype, goal,
                                          {"completely_made_up_tag"})
            assert mult == 1.0, (
                f"unknown-tag lookup must be identity for "
                f"({archetype}, {goal}), got {mult}"
            )
    # None goal (no goal engine) is identity for every tag set.
    for tags in (set(), {ROLE_FINISHER_TAG}, {"cantrip"}):
        assert goal_weight_multiplier("control", None, tags) == 1.0


# ─────────────────────────────────────────────────────────────
# Rule 3 — Storm-side robustness (CLAUDE.md generalization rule 5):
# chain-fuel cantrips are NOT down-weighted for combo/storm
# archetypes at close_game.  For Storm, cantrips ARE the finisher
# access path; penalising them at close_game would re-create the
# "rituals penalised mid-chain" P1.
# ─────────────────────────────────────────────────────────────


def test_storm_chain_fuel_unaffected_by_close_game():
    for archetype in ("storm", "combo"):
        for tag in ("cantrip", "ritual", "card_advantage", "storm_payoff"):
            mult = goal_weight_multiplier(
                archetype, GoalType.CLOSE_GAME.value, {tag})
            assert mult == 1.0, (
                f"{archetype} chain fuel ({tag}) must not be re-weighted "
                f"at close_game — Storm's cantrips are finisher access, "
                f"got {mult}"
            )


# ─────────────────────────────────────────────────────────────
# Rule 4 — table shape: every leaf weight positive finite; every
# archetype key exists in PROFILES; every goal key is a real
# GoalType value.
# ─────────────────────────────────────────────────────────────


def test_goal_weights_table_shape_and_consistency():
    from ai.strategy_profile import PROFILES

    valid_goal_values = {g.value for g in GoalType}
    assert isinstance(goal_weights, dict)
    for archetype, by_goal in goal_weights.items():
        assert archetype in PROFILES, (
            f"goal_weights archetype {archetype!r} not in PROFILES"
        )
        assert isinstance(by_goal, dict)
        for goal_value, by_tag in by_goal.items():
            assert goal_value in valid_goal_values, (
                f"goal_weights[{archetype}] key {goal_value!r} is not a "
                f"GoalType value"
            )
            for tag, weight in by_tag.items():
                assert isinstance(tag, str)
                assert math.isfinite(weight) and weight > 0.0, (
                    f"goal_weights[{archetype}][{goal_value}][{tag}] = "
                    f"{weight} must be positive finite"
                )


# ─────────────────────────────────────────────────────────────
# Rule 5 — integration through the real play-scoring path:
# the SAME card in the SAME game state scores strictly higher
# when the goal engine sits on CLOSE_GAME and the card carries
# the gameplan-declared finisher role, and a cantrip-tagged spell
# scores strictly lower.  Role knowledge comes from a synthetic
# gameplan constructed in-test (data, not code).
# ─────────────────────────────────────────────────────────────


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    else:
        game.players[controller].library.append(card)
    return card


def _make_control_player_with_two_gear_plan(finisher_name: str):
    """Control-archetype EVPlayer whose gameplan declares two gears:
    GRIND_VALUE then CLOSE_GAME, with `finisher_name` in the
    finishers role bucket of the closing gear.  The role declaration
    is the ONLY card-specific knowledge — it lives in the (synthetic)
    gameplan data, mirroring decks/gameplans/*.json."""
    player = EVPlayer(player_idx=0, deck_name="__goal_gear_test__",
                      rng=random.Random(0))
    assert player.archetype == "midrange"  # unknown deck default
    # Pin the archetype the table declares weights for.
    from ai.strategy_profile import get_profile
    player.archetype = "control"
    player.profile = get_profile("control")

    plan = DeckGameplan(
        deck_name="__goal_gear_test__",
        archetype="control",
        goals=[
            Goal(goal_type=GoalType.GRIND_VALUE, description="grind"),
            Goal(goal_type=GoalType.CLOSE_GAME, description="close",
                 card_roles={"finishers": {finisher_name}}),
        ],
    )
    player.goal_engine = GoalEngine(plan)
    # Rebuild the role cache the constructor derives from the plan.
    player._payoff_names = {finisher_name}
    return player


def _score_in_goal(player, game, card_name, goal_idx):
    player.goal_engine.current_goal_idx = goal_idx
    me = game.players[0]
    opp = game.players[1]
    snap = snapshot_from_game(game, 0)
    card = next(c for c in me.hand if c.template.name == card_name)
    player.bhi.initialize_from_game(game)
    return player._score_spell(card, snap, game, me, opp)


def test_score_spell_goal_gearshift_integration(card_db):
    """Real engine, real cards, synthetic gameplan roles.

    Board is quiet (both boards empty, both at 20) so the life-phase
    is DEVELOP and the phase gear-shift is identity — any EV change
    between goal indices is the goal gear-shift alone.
    """
    finisher_name = "Frogmite"   # role comes from the gameplan, not the card
    cantrip_name = "Stock Up"    # oracle-tagged cantrip

    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "__goal_gear_test__"
    game.players[1].deck_name = "__goal_gear_opponent__"
    game.turn_number = 5
    game.active_player = 0
    game.current_phase = Phase.MAIN1

    for land in ("Island", "Island", "Hallowed Fountain", "Hallowed Fountain"):
        c = _add(game, card_db, land, 0, "battlefield")
        c.tapped = False
    _add(game, card_db, finisher_name, 0, "hand")
    _add(game, card_db, cantrip_name, 0, "hand")
    for n in ("Island", "Island", "Island"):
        _add(game, card_db, n, 0, "library")
    for n in ("Island", "Island", "Island"):
        _add(game, card_db, n, 1, "library")

    player = _make_control_player_with_two_gear_plan(finisher_name)

    snap = snapshot_from_game(game, 0)
    assert life_phase(snap) is LifePhase.DEVELOP, (
        "fixture invariant: quiet board must classify as DEVELOP so the "
        "phase gear-shift is identity and only the goal gear moves EV"
    )

    fin_grind = _score_in_goal(player, game, finisher_name, goal_idx=0)
    fin_close = _score_in_goal(player, game, finisher_name, goal_idx=1)
    assert abs(fin_grind) > 1e-9, (
        "fixture invariant: finisher base EV must be nonzero for the "
        "preference weight to move it"
    )
    assert fin_close > fin_grind, (
        f"finisher-role card must score strictly higher under "
        f"goal=close_game ({fin_close:.3f}) than under grind_value "
        f"({fin_grind:.3f}) — the goal gear-shift is inert (finding A2/M4)"
    )

    cant_grind = _score_in_goal(player, game, cantrip_name, goal_idx=0)
    cant_close = _score_in_goal(player, game, cantrip_name, goal_idx=1)
    assert abs(cant_grind) > 1e-9
    assert cant_close < cant_grind, (
        f"cantrip must score strictly lower under goal=close_game "
        f"({cant_close:.3f}) than under grind_value ({cant_grind:.3f})"
    )
