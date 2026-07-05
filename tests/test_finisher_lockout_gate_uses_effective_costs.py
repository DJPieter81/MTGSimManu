"""Finisher mana-sequencing gate must compare EFFECTIVE costs, not
printed CMC — 5-panel audit Unresolved #4 (partial-chain decision math).

Re-verified on current main (2026-07-05): trace `Azorius Control` vs
`Ruby Storm` -s 60100, Storm main phase with 2 Ruby Medallions on
board, 3 floating mana, hand [Grapeshot, Glimpse the Impossible,
Valakut Awakening, Ral x2]:

    -4.9  cast_spell: Grapeshot   <-- CHOSEN (fires for 4 dmg into 17)
    -8.3  cast_spell: Glimpse the Impossible
    -8.5  cast_spell: Valakut Awakening

After Grapeshot leaves hand, Glimpse jumps to +0.2 (delta = 8.5 =
opp_life/2 exactly).  Root cause: the S-2 EXECUTE_PAYOFF gate
(`ai/ev_player.py::_score_spell`) penalises every non-finisher by
`opp_life / 2` when `snap.my_mana - candidate.cmc <
cheapest_finisher.cmc`, using PRINTED cmc on both sides:

  * With 2 Medallions, Grapeshot's effective cost is 0 (and the
    candidate Glimpse costs 1, not 3) — the finisher was never in
    danger of being locked out, yet the gate suppressed all chain
    fuel below the payoff, so the payoff fired FIRST at storm=2 for
    4 damage into 17 life while castable fuel remained in hand.
    Casting the payoff before remaining castable fuel is strictly
    dominated (storm only increases).
  * A ritual candidate REBUILDS mana (Desperate Ritual: pay 2, add
    3) — raw `mana - cmc` says it locks the finisher out when it
    actually nets +1 mana.

Rule under test: the gate's mana arithmetic must use
`ai.effective_cmc.effective_cmc` (the W0-F cost primitive) for both
the candidate and the finisher, and must credit the candidate's
oracle-derived `ritual_mana` production — the same sources
`combo_chain.classify_card` reads.  No card names, no new numerics.

Class size: every combo deck that pairs a payoff with cost reducers
or rituals — Storm (Ruby Medallion, Ral), any Electromancer/Baral
shell, future medallion decks.  Lift-check named in the PR.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from engine.cards import CardInstance, Keyword as Kw
from engine.game_state import GameState, Phase


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
    """Advance the goal engine to EXECUTE_PAYOFF (last Storm goal)."""
    from ai.gameplan import GoalType
    ge = player.goal_engine
    assert ge is not None, "Storm must have a goal engine"
    while ge.current_goal.goal_type != GoalType.EXECUTE_PAYOFF:
        if ge.current_goal_idx >= len(ge.gameplan.goals) - 1:
            break
        ge.advance_goal(None, "test setup: force EXECUTE_PAYOFF")
    assert ge.current_goal.goal_type == GoalType.EXECUTE_PAYOFF


def _setup_game(card_db, untapped_mountains: int, hand_names, board_names=(),
                storm: int = 2):
    """Storm pilot scaffold mirroring test_storm_finisher_priority_in_payoff."""
    game = GameState(rng=random.Random(0))
    for _ in range(untapped_mountains):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    board = [_add(game, card_db, n, controller=0, zone="battlefield")
             for n in board_names]
    hand = [_add(game, card_db, n, controller=0, zone="hand")
            for n in hand_names]

    game.players[0].life = 20
    game.players[1].life = 20
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Dimir Midrange"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 5
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = storm
    game._global_storm_count = storm
    return game, hand, board


class TestFinisherLockoutGateUsesEffectiveCosts:
    """The S-2 gate's lockout arithmetic must be cost-reducer- and
    ritual-aware, so chain fuel is sequenced BEFORE the payoff when
    the payoff cannot actually be locked out."""

    def test_gate_respects_cost_reducers_on_finisher(self, card_db):
        """2 Ruby Medallions on board: Grapeshot's effective cost is 0
        and Glimpse's is 1.  Casting Glimpse leaves 2 mana >= 0 — the
        finisher CANNOT be locked out, so the gate must not penalise
        the fuel spell.

        Verification (same technique as
        test_storm_finisher_priority_in_payoff): score Glimpse in
        DEPLOY_ENGINE (gate can never fire) vs EXECUTE_PAYOFF.  A
        wrong firing shows up as ~opp_life/2 = 10.0; tolerance 1.0
        cleanly rejects it."""
        game, (grapeshot, glimpse), _ = _setup_game(
            card_db, untapped_mountains=3,
            hand_names=["Grapeshot", "Glimpse the Impossible"],
            board_names=["Ruby Medallion", "Ruby Medallion"])
        # Sanity: the effective-cost primitive sees the reducers.
        from ai.effective_cmc import effective_cmc
        assert effective_cmc(grapeshot, None, game=game, player_idx=0) == 0
        assert Kw.STORM in grapeshot.template.keywords

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me, opp = game.players[0], game.players[1]
        assert snap.my_mana == 3

        from ai.gameplan import GoalType
        assert (player.goal_engine.current_goal.goal_type
                == GoalType.DEPLOY_ENGINE)
        ev_glimpse_deploy = player._score_spell(glimpse, snap, game, me, opp)

        _force_payoff_goal(player)
        ev_glimpse_payoff = player._score_spell(glimpse, snap, game, me, opp)

        delta = ev_glimpse_deploy - ev_glimpse_payoff
        assert abs(delta) < 1.0, (
            f"With the finisher's effective cost at 0 (2 cost reducers), "
            f"the finisher-lockout penalty must NOT fire on chain fuel. "
            f"Got delta={delta:.4f} (deploy={ev_glimpse_deploy:.4f}, "
            f"payoff={ev_glimpse_payoff:.4f}); a wrong firing is "
            f"~10.0 (opp_life/2)."
        )

    def test_gate_credits_ritual_mana_production(self, card_db):
        """No reducers.  3 mana, Grapeshot (cmc 2) + Desperate Ritual
        (cmc 2, adds 3).  Raw arithmetic says 3-2=1 < 2 → lockout;
        real arithmetic says 3-2+3=4 >= 2 — the ritual REBUILDS mana
        and cannot lock the finisher out.  The gate must not fire."""
        game, (grapeshot, ritual), _ = _setup_game(
            card_db, untapped_mountains=3,
            hand_names=["Grapeshot", "Desperate Ritual"])
        rdata = getattr(ritual.template, 'ritual_mana', None)
        assert rdata is not None and rdata[1] > (ritual.template.cmc or 0), (
            "scaffold: ritual must net positive mana")

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me, opp = game.players[0], game.players[1]
        assert snap.my_mana == 3

        from ai.gameplan import GoalType
        assert (player.goal_engine.current_goal.goal_type
                == GoalType.DEPLOY_ENGINE)
        ev_ritual_deploy = player._score_spell(ritual, snap, game, me, opp)

        _force_payoff_goal(player)
        ev_ritual_payoff = player._score_spell(ritual, snap, game, me, opp)

        delta = ev_ritual_deploy - ev_ritual_payoff
        assert abs(delta) < 1.0, (
            f"A mana-positive ritual cannot lock the finisher out; the "
            f"finisher-lockout penalty must NOT fire. Got "
            f"delta={delta:.4f} (deploy={ev_ritual_deploy:.4f}, "
            f"payoff={ev_ritual_payoff:.4f}); a wrong firing is "
            f"~10.0 (opp_life/2)."
        )

    def test_fuel_sequenced_before_payoff_under_cost_reducers(self, card_db):
        """Decision-level repro of the s60100 trace misfire: in
        EXECUTE_PAYOFF with 2 Medallions, 3 mana, storm=2, hand
        [Grapeshot, Glimpse], the chain fuel must OUT-SCORE the
        payoff — payoff-before-castable-fuel is strictly dominated
        (storm only increases).  On broken main Grapeshot wins the
        comparison and fires sub-lethal (4 damage into 17+ life)."""
        game, (grapeshot, glimpse), _ = _setup_game(
            card_db, untapped_mountains=3,
            hand_names=["Grapeshot", "Glimpse the Impossible"],
            board_names=["Ruby Medallion", "Ruby Medallion"])
        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        _force_payoff_goal(player)
        snap = snapshot_from_game(game, 0)
        me, opp = game.players[0], game.players[1]

        # Sanity: Glimpse is chain-extending fuel, so the payoff's
        # hold discount (combo_calc storm branch) applies while it
        # remains in hand.
        from ai.predicates import is_chain_fuel
        assert is_chain_fuel(glimpse)

        ev_grapeshot = player._score_spell(grapeshot, snap, game, me, opp)
        ev_glimpse = player._score_spell(glimpse, snap, game, me, opp)

        assert ev_glimpse > ev_grapeshot, (
            f"Sub-lethal payoff must not out-score castable chain fuel "
            f"(payoff-last is weakly dominant). Got "
            f"ev(Glimpse)={ev_glimpse:.2f} <= ev(Grapeshot)="
            f"{ev_grapeshot:.2f} — the payoff fires first at storm=2 "
            f"for ~3 damage into 20 life."
        )

    def test_gate_still_fires_when_lockout_is_real(self, card_db):
        """Regression anchor: no reducers, no ritual production.  2
        mana, Grapeshot (cmc 2) + March of Reckless Joy (cmc 1 —
        its printed cost; it produces no ritual mana).  Casting March
        leaves 1 < 2: the lockout is REAL and the gate must still
        penalise the cantrip so the finisher out-scores it (the
        original S-2 behaviour, pinned by
        test_storm_finisher_priority_in_payoff and re-pinned here
        against the effective-cost rewrite)."""
        game, (grapeshot, march), _ = _setup_game(
            card_db, untapped_mountains=2,
            hand_names=["Grapeshot", "March of Reckless Joy"])
        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        _force_payoff_goal(player)
        snap = snapshot_from_game(game, 0)
        me, opp = game.players[0], game.players[1]
        assert snap.my_mana == 2

        ev_grapeshot = player._score_spell(grapeshot, snap, game, me, opp)
        ev_march = player._score_spell(march, snap, game, me, opp)

        assert ev_grapeshot > ev_march, (
            f"When the lockout is real (post-cast mana < finisher "
            f"effective cost, no mana rebuilt), the gate must still "
            f"fire. Got ev(Grapeshot)={ev_grapeshot:.2f}, "
            f"ev(March)={ev_march:.2f}."
        )
