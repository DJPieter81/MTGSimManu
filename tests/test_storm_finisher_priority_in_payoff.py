"""Iter4 S-2 — In EXECUTE_PAYOFF, prioritize the finisher cast over
non-finisher cantrips when mana is constrained.

Observed in Storm vs Affinity matchup trace: T3 of game 1, Storm has
Grapeshot (CMC 2) in hand AND is in EXECUTE_PAYOFF mode. Before
casting Grapeshot, the AI elects to cast March of Reckless Joy (CMC
1) for incidental damage / impulse draw. After the cantrip resolves
the available mana drops below Grapeshot's cost — Grapeshot never
fires, the storm count opportunity is wasted, and Storm loses tempo.

Root cause: card-priority weights in `decks/gameplans/ruby_storm.json`
give cantrips like March of Reckless Joy a priority similar to the
finisher (Grapeshot), so the AI doesn't prefer the finisher when both
are legal.

Fix (in `ai/ev_player.py::_score_spell`): when the current goal is
EXECUTE_PAYOFF, and the player has a finisher (STORM keyword) in
hand, AND casting the candidate would leave insufficient mana to cast
the cheapest finisher this turn, downgrade the candidate's score by a
penalty derived from `opp_life / 2.0` (the same scale as the
reanimation-readiness boost — value of NOT casting the finisher this
turn). Detection is keyword/oracle-driven; no hardcoded card names.

Regression anchor: when goal != EXECUTE_PAYOFF, no penalty is
applied. When mana is plentiful (post-cast still covers the
finisher), no penalty is applied.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance, Keyword as Kw
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone, summoning_sick=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = summoning_sick
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


def _force_payoff_goal(player: EVPlayer):
    """Advance the goal engine to EXECUTE_PAYOFF (last goal in Storm
    gameplan)."""
    from ai.gameplan import GoalType
    ge = player.goal_engine
    assert ge is not None, "Storm must have a goal engine"
    while ge.current_goal.goal_type != GoalType.EXECUTE_PAYOFF:
        if ge.current_goal_idx >= len(ge.gameplan.goals) - 1:
            break
        ge.advance_goal(None, "test setup: force EXECUTE_PAYOFF")
    assert ge.current_goal.goal_type == GoalType.EXECUTE_PAYOFF


def _setup_storm_payoff_game(card_db, untapped_mountains: int,
                             storm: int = 2):
    """Common scaffold: Storm pilot with Grapeshot (finisher, CMC 2)
    + March of Reckless Joy (cantrip, CMC 1) in hand.

    `untapped_mountains` controls available mana. `storm` sets the
    spells-cast count this turn — the Affinity-matchup scenario has
    Storm already chained rituals before reaching the Grapeshot vs
    cantrip choice."""
    game = GameState(rng=random.Random(0))
    for _ in range(untapped_mountains):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    grapeshot = _add(game, card_db, "Grapeshot", controller=0,
                     zone="hand")
    march = _add(game, card_db, "March of Reckless Joy", controller=0,
                 zone="hand")

    game.players[0].life = 20
    game.players[1].life = 20
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Affinity"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 3
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = storm
    game._global_storm_count = storm
    return game, grapeshot, march


class TestStormFinisherPriorityInPayoff:
    """When mana is constrained and finisher is in hand, cantrips
    that would lock out the finisher must be penalized."""

    def test_grapeshot_outscores_march_when_mana_constrained(
            self, card_db):
        """Affinity-matchup T3 reproduction: Storm has chained 2
        spells already (storm=2), 2 untapped Mountains floating.
        Hand: Grapeshot (CMC 2) + March of Reckless Joy (CMC 1).

        Casting March (cmc 1) leaves 1 mana — strictly below
        Grapeshot's cmc 2, so the finisher is locked out for the
        turn. The S-2 gate must penalize March by `opp_life / 2.0`,
        and Grapeshot must out-score March (the finisher cast wins).
        """
        game, grapeshot, march = _setup_storm_payoff_game(
            card_db, untapped_mountains=2)
        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        _force_payoff_goal(player)
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        # Sanity: snapshot mana matches scaffold.
        assert snap.my_mana == 2, (
            f"Test scaffold: expected 2 mana, got {snap.my_mana}")
        # Sanity: Grapeshot is a STORM-keyword finisher.
        assert Kw.STORM in grapeshot.template.keywords
        # Sanity: March has no STORM keyword.
        assert Kw.STORM not in march.template.keywords

        ev_grapeshot = player._score_spell(grapeshot, snap, game, me, opp)
        ev_march = player._score_spell(march, snap, game, me, opp)

        assert ev_grapeshot > ev_march, (
            f"In EXECUTE_PAYOFF with finisher in hand and mana "
            f"constrained, finisher must out-score the cantrip that "
            f"would lock it out. Got ev(Grapeshot)={ev_grapeshot:.2f}, "
            f"ev(March)={ev_march:.2f}."
        )

    def test_no_penalty_when_mana_plentiful(self, card_db):
        """7 mana, Grapeshot (CMC 2) + March (CMC 1). Casting March
        (cmc 1) leaves 6 mana — Grapeshot (cmc 2) still fits. The
        S-2 gate must NOT fire.

        Verification: compare March's score in EXECUTE_PAYOFF vs
        DEPLOY_ENGINE with everything else identical. The gate fires
        only in EXECUTE_PAYOFF. Since post-cast mana (6) >= finisher
        cmc (2), no penalty is applied — the two scores must agree
        modulo any non-S-2 goal-dependent scoring."""
        game, grapeshot, march = _setup_storm_payoff_game(
            card_db, untapped_mountains=7)
        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]
        assert snap.my_mana == 7

        # DEPLOY_ENGINE baseline (S-2 gate cannot fire here at all).
        from ai.gameplan import GoalType
        assert (player.goal_engine.current_goal.goal_type
                == GoalType.DEPLOY_ENGINE)
        ev_march_deploy = player._score_spell(
            march, snap, game, me, opp)

        # Switch to EXECUTE_PAYOFF, re-score. With plentiful mana,
        # the S-2 gate must NOT impose its opp_life/2 = 10.0 penalty.
        _force_payoff_goal(player)
        ev_march_payoff = player._score_spell(
            march, snap, game, me, opp)

        delta = ev_march_deploy - ev_march_payoff
        # If the gate fired wrongly, ev_march_payoff would be ~10.0
        # lower than ev_march_deploy. Tolerance 1.0 << 10.0 cleanly
        # rejects that case while admitting unrelated goal-keyed
        # scoring drift.
        assert abs(delta) < 1.0, (
            f"With plentiful mana, the S-2 finisher-lockout penalty "
            f"must NOT fire in EXECUTE_PAYOFF. Got delta={delta:.4f} "
            f"(deploy={ev_march_deploy:.4f}, "
            f"payoff={ev_march_payoff:.4f}). Penalty would be "
            f"~10.0 (opp_life/2)."
        )

    def test_no_penalty_outside_execute_payoff(self, card_db):
        """Goal == DEPLOY_ENGINE (first Storm goal). Hand has
        Grapeshot + March, mana is constrained (2 mana, March cmc 1
        → 1 mana left, below Grapeshot cmc 2). The S-2 gate must NOT
        fire because the goal is not EXECUTE_PAYOFF.

        Verification: compare March's score with vs without Grapeshot
        in hand while staying in DEPLOY_ENGINE. The S-2 gate's
        finisher-presence check cannot trigger here, so any delta is
        from existing scoring (small) — never the ~10.0 penalty."""
        game, grapeshot, march = _setup_storm_payoff_game(
            card_db, untapped_mountains=2)
        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        from ai.gameplan import GoalType
        assert player.goal_engine is not None
        assert (player.goal_engine.current_goal.goal_type
                == GoalType.DEPLOY_ENGINE)

        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        ev_march_with_finisher = player._score_spell(
            march, snap, game, me, opp)

        me.hand = [c for c in me.hand
                   if c.instance_id != grapeshot.instance_id]
        snap2 = snapshot_from_game(game, 0)
        ev_march_no_finisher = player._score_spell(
            march, snap2, game, me, opp)

        delta = ev_march_no_finisher - ev_march_with_finisher
        # If the gate fired wrongly, with-finisher would be ~10
        # lower (no_finisher - with_finisher = +10). Tolerance 1.0
        # cleanly rejects.
        assert abs(delta) < 1.0, (
            f"Outside EXECUTE_PAYOFF, finisher presence must NOT "
            f"impose the S-2 finisher-lockout penalty. Got "
            f"delta={delta:.4f} (with={ev_march_with_finisher:.4f}, "
            f"without={ev_march_no_finisher:.4f}). Penalty would be "
            f"~10.0 (opp_life/2)."
        )
