"""A second removal-evoke in the same turn must be penalised unless the
target is a sentinel-class threat.

Diagnostic: docs/diagnostics/2026-05-01_azcon_followup.md (step 1).

Replay (seed 50001 AzCon vs Affinity, T3) shows AzCon evoking BOTH
Solitudes on the same turn — one on the Construct Token, one on
Sojourner's Companion. Both targets pass the existing small-target
gate (power > 1, cmc > 2), so the pre-fix `_eval_evoke` returned
``+1.0`` for each independently. Burning 4 cards (2 Solitudes + 2
pitched Orim's Chants) for two medium exiles wins T3 but loses T4
when Cranial Plating arrives and there are no answers left.

Rule (no card names): the Nth removal-evoke this turn carries an
additive diminishing-return penalty equal to ``N ×
EVOKE_BUDGET_PENALTY_PER_PRIOR``. The penalty is waived when the
candidate target's ``creature_threat_value`` is at or above the
sentinel-class threshold (``EVOKE_BUDGET_SENTINEL_THREAT``, twice
the premium tier already documented as ``REMOVAL_DEFERRAL_TARGET_GAP``).

Class-size: every Modern evoke-pitch elemental — Solitude, Subtlety,
Endurance, Grief, Fury, and any future printing — flows through the
same ``ActionType.EVOKE`` evaluator with the ``removal`` tag. The
fix is mechanism-driven, not card-name-driven.
"""
from __future__ import annotations

import random

import pytest

from ai.board_eval import Action, ActionType, evaluate_action
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


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
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


def _build_azcon_facing_medium_threat(card_db, prior_evokes: int):
    """T3 mid-priority: AzCon holds Solitude. Affinity board is a
    single medium-threat creature (Sojourner's Companion, threat ≈ 4.45)
    — passes the small-target gate but is BELOW the sentinel-class
    threshold. This is the exact replay condition where the budget
    guard must apply.

    `prior_evokes` populates the new turn-scoped counter that the
    fix consumes."""
    game = GameState(rng=random.Random(0))
    _add(game, card_db, "Sojourner's Companion", controller=0,
         zone="battlefield")
    sol = _add(game, card_db, "Solitude", controller=1, zone="hand")
    _add(game, card_db, "Orim's Chant", controller=1, zone="hand")
    for _ in range(3):
        _add(game, card_db, "Plains", controller=1, zone="battlefield")
    game.players[0].deck_name = "Affinity"
    game.players[1].deck_name = "Azorius Control"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 1
    game.turn_number = 3
    game.players[0].life = 20
    game.players[1].life = 18
    game.players[1].removal_evokes_resolved_this_turn = prior_evokes
    return game, sol


def _build_azcon_facing_sentinel_threat(card_db, prior_evokes: int):
    """Plating-equipped carrier on a wide artifact board is sentinel-
    class. With 7 artifacts in play (Plating + carrier + 5 supports),
    the carrier's `creature_threat_value` is ≈ 8.15 — just over
    `EVOKE_BUDGET_SENTINEL_THREAT` (8.0). The budget guard must waive
    the penalty even when one removal-evoke has already fired this
    turn."""
    game = GameState(rng=random.Random(0))
    plating = _add(game, card_db, "Cranial Plating",
                   controller=0, zone="battlefield")
    carrier = _add(game, card_db, "Memnite", controller=0,
                   zone="battlefield")
    for _ in range(5):
        _add(game, card_db, "Ornithopter", controller=0,
             zone="battlefield")
    carrier.instance_tags.add(f"equipped_{plating.instance_id}")

    sol = _add(game, card_db, "Solitude", controller=1, zone="hand")
    _add(game, card_db, "Orim's Chant", controller=1, zone="hand")
    # Two Plains only — fewer than Solitude's CMC (3), so the
    # "save for hardcast next turn" branch does not pre-empt the
    # budget guard. This matches the AzCon T3 replay where Solitude
    # cannot be hard-cast in time anyway.
    for _ in range(2):
        _add(game, card_db, "Plains", controller=1, zone="battlefield")
    game.players[0].deck_name = "Affinity"
    game.players[1].deck_name = "Azorius Control"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 1
    game.turn_number = 3
    game.players[0].life = 20
    game.players[1].life = 14
    game.players[1].removal_evokes_resolved_this_turn = prior_evokes
    return game, sol


class TestEvokeBudgetGuard:

    def test_first_evoke_on_medium_target_unchanged(self, card_db):
        """Regression: counter=0, medium target → score unchanged
        from the pre-fix +1.0 baseline. The budget guard must not
        perturb the first removal-evoke of the turn."""
        game, sol = _build_azcon_facing_medium_threat(card_db,
                                                       prior_evokes=0)
        score = evaluate_action(
            game, player_idx=1,
            action=Action(ActionType.EVOKE, {'card': sol}),
        )
        assert score > 0, (
            f"First removal-evoke of the turn on a medium target "
            f"scored {score:.2f}; expected > 0 (the +1.0 default-"
            f"evoke value). The budget guard must only fire when the "
            f"counter > 0."
        )

    def test_second_evoke_on_medium_target_returns_negative(self, card_db):
        """Counter=1, medium target (Sojourner's Companion, ≈ 4.45
        threat) → second evoke must score negative.

        This is the AzCon vs Affinity T3 failure mode: pre-fix the
        score for each evoke is +1.0 independently, so AzCon happily
        fires both, burning 4 cards for 2 medium exiles."""
        game, sol = _build_azcon_facing_medium_threat(card_db,
                                                       prior_evokes=1)
        score = evaluate_action(
            game, player_idx=1,
            action=Action(ActionType.EVOKE, {'card': sol}),
        )
        assert score < 0, (
            f"After one removal-evoke this turn, the second evoke on "
            f"a medium target (Sojourner's Companion, ~4.45 threat) "
            f"scored {score:.2f}; expected < 0. The budget guard in "
            f"`_eval_evoke` is not reading "
            f"`removal_evokes_resolved_this_turn`. AzCon vs Affinity "
            f"replay (seed 50001 T3) shows this exact failure mode: "
            f"4-card spend for 2 medium exiles."
        )

    def test_second_evoke_on_sentinel_target_still_fires(self, card_db):
        """Counter=1, sentinel target (Plating-equipped carrier, ≈ 13
        threat) → score must remain positive. The waiver clause keeps
        the AI honest against board states where not answering loses
        the game."""
        game, sol = _build_azcon_facing_sentinel_threat(card_db,
                                                         prior_evokes=1)
        score = evaluate_action(
            game, player_idx=1,
            action=Action(ActionType.EVOKE, {'card': sol}),
        )
        assert score > 0, (
            f"Second evoke on a sentinel-class target (Plating-equipped "
            f"carrier, threat ≥ EVOKE_BUDGET_SENTINEL_THREAT) scored "
            f"{score:.2f}; expected > 0. The budget guard is over-"
            f"tightening — sentinel threats must always fire even when "
            f"a removal-evoke already resolved this turn."
        )
