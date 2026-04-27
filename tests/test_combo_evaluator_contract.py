"""Combo evaluator contract tests (rewrite session 1/5).

Pins the rule-phrased behaviour of `ai.combo_calc.card_combo_modifier`
so the upcoming replacement (`ai.combo_evaluator.card_combo_evaluation`)
can be a drop-in. Each test names a *mechanic*, not a card. See
`docs/proposals/combo_simulator_unification.md`.

Rules pinned (one test each, eight tests total):

  1. test_chain_fuel_credited_when_chain_reachable
     STORM-keyword finisher with chain-extending fuel still in hand
     returns a NEGATIVE modifier (hold to grow chain). Lethal short-
     circuit returns combo_value (fire immediately).

  2. test_storm_hard_hold_when_chain_unreachable
     Ritual at storm=0 with no finisher path AND not under lethal
     pressure returns the STORM_HARD_HOLD sentinel — CR 500.4: mana
     empties at phase end.

  3. test_tutor_scores_as_closer_when_sb_or_library_has_payoff
     A tutor whose target deck holds a STORM-keyword payoff scores
     symmetrically to a STORM finisher (hold while non-tutor fuel
     remains, fire when chain is exhausted).

  4. test_cost_reducer_arithmetic_matches_storm_count
     Cost-reducer engine returns NON-NEGATIVE modifier (storm chain
     improvement, derived from `find_all_chains` damage delta — never
     a magic constant).

  5. test_ritual_patience_at_storm_zero
     Ritual at storm=0 with reducer in hand and castable returns a
     NEGATIVE modifier (deploy reducer first for amplification).

  6. test_ritual_patience_at_storm_geq_one
     Ritual at storm>=1 with no finisher and no draws returns the
     STORM_HARD_HOLD sentinel; with draws remaining, returns a
     soft-negative penalty (not the sentinel).

  7. test_flip_transform_stack_batching
     Cheap instant/sorcery with a flip-transform creature on board
     returns a POSITIVE modifier proportional to 0.5^(storm+1).

  8. test_search_tax_awareness
     Tutor on a board where opp has a "whenever a player searches"
     permanent returns a NEGATIVE modifier.

The assertions pin SIGN, SENTINEL, and DIRECTIONAL behaviour — not
exact float values. A future replacement that preserves these rules
passes; one that flips a sign or skips a branch fails.
"""
from __future__ import annotations

import random

import pytest

from ai.combo_calc import (
    ComboAssessment,
    STORM_HARD_HOLD,
    card_combo_modifier,
)
from ai.ev_evaluator import EVSnapshot, snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone):
    """Standard helper used across the storm test suite."""
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
    getattr(game.players[controller], 'library' if zone == 'library'
            else zone).append(card)
    return card


def _base_game(card_db, opp_life=20, my_mana=2, storm=0,
               my_lands=2, opp_power=0, my_life=20):
    """Minimal game + snapshot pair that covers card_combo_modifier's reads.

    `am_dead_next` and `opp_clock_discrete` are @properties derived
    from `opp_power` / `my_life` — set those to control them.  Defaults
    keep the AI out of lethal pressure (opp_power=0 → opp_clock=99,
    am_dead_next=False).
    """
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Dimir Midrange"
    game.players[0].life = my_life
    game.players[1].life = opp_life
    game.players[0].spells_cast_this_turn = storm
    game._global_storm_count = storm
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 3
    snap = EVSnapshot(
        my_life=my_life, opp_life=opp_life, my_mana=my_mana,
        my_total_lands=my_lands, storm_count=storm,
        opp_power=opp_power,
    )
    return game, snap


def _assessment(combo_value=80.0, resource_zone="storm", is_ready=True,
                payoff_names=None, r_res=5.0, payoff_value=0.5,
                resource_target=4):
    return ComboAssessment(
        resource_zone=resource_zone,
        is_ready=is_ready,
        payoff_value=payoff_value,
        combo_value=combo_value,
        payoff_names=payoff_names or {"Grapeshot"},
        r_res=r_res,
        resource_target=resource_target,
    )


# ═════════════════════════════════════════════════════════════════
# 1. Chain-fuel credit (STORM finisher branch)
# ═════════════════════════════════════════════════════════════════

class TestChainFuelCreditedWhenChainReachable:
    """STORM-keyword finisher: hold while chain-extending fuel
    remains, fire when chain is exhausted, fire immediately when
    storm + 1 >= opp_life."""

    def test_holds_when_chain_fuel_remains(self, card_db):
        game, snap = _base_game(card_db, opp_life=20, storm=2)
        finisher = _add(game, card_db, "Grapeshot", controller=0,
                        zone="hand")
        # Two chain-extending fuel cards (rituals).
        _add(game, card_db, "Desperate Ritual", controller=0, zone="hand")
        _add(game, card_db, "Desperate Ritual", controller=0, zone="hand")

        ev = card_combo_modifier(
            finisher, _assessment(), snap,
            game.players[0], game, 0)

        assert ev < 0.0, (
            f"STORM finisher with 2 ritual fuel in hand must HOLD "
            f"(negative modifier); got {ev:.3f}.  Rule: each chain-"
            f"extending fuel card adds 1/opp_life of opportunity cost."
        )

    def test_fires_when_storm_count_is_lethal(self, card_db):
        game, snap = _base_game(card_db, opp_life=3, storm=3)
        finisher = _add(game, card_db, "Grapeshot", controller=0,
                        zone="hand")
        # Even with fuel in hand, storm + 1 (= 4) >= opp_life (= 3)
        # is the lethal short-circuit.
        _add(game, card_db, "Desperate Ritual", controller=0, zone="hand")

        a = _assessment(combo_value=80.0)
        ev = card_combo_modifier(
            finisher, a, snap, game.players[0], game, 0)

        assert ev == pytest.approx(a.combo_value), (
            f"Lethal STORM finisher must short-circuit to combo_value "
            f"({a.combo_value}); got {ev}."
        )

    def test_fires_when_no_chain_fuel_remains(self, card_db):
        game, snap = _base_game(card_db, opp_life=20, storm=4)
        finisher = _add(game, card_db, "Grapeshot", controller=0,
                        zone="hand")
        # No fuel — only a creature, which is not is_chain_fuel.
        _add(game, card_db, "Ral, Storm Conduit", controller=0,
             zone="hand")

        ev = card_combo_modifier(
            finisher, _assessment(), snap, game.players[0], game, 0)

        assert ev > 0.0, (
            f"STORM finisher with no chain-extending fuel must FIRE "
            f"(positive modifier = storm/opp_life × combo_value); "
            f"got {ev:.3f}."
        )


# ═════════════════════════════════════════════════════════════════
# 2. Storm hard hold sentinel (ritual / no path / no pressure)
# ═════════════════════════════════════════════════════════════════

class TestStormHardHoldWhenChainUnreachable:
    """Ritual at storm=0 with no finisher path returns
    STORM_HARD_HOLD — CR 500.4: mana empties at phase end."""

    def test_ritual_with_no_finisher_returns_sentinel(self, card_db):
        # opp_power=0 (default) → am_dead_next=False, no lethal pressure.
        game, snap = _base_game(card_db, opp_life=20, storm=0)
        ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                      zone="hand")
        # No STORM-keyword card, no tutor, no PiF setup — empty hand
        # except the ritual itself.

        ev = card_combo_modifier(
            ritual, _assessment(), snap, game.players[0], game, 0)

        assert ev == STORM_HARD_HOLD, (
            f"Ritual at storm=0 with no reachable chain must return "
            f"STORM_HARD_HOLD ({STORM_HARD_HOLD}); got {ev}.  Rule: "
            f"CR 500.4 — mana from rituals empties at phase end."
        )


# ═════════════════════════════════════════════════════════════════
# 3. Tutor-as-closer
# ═════════════════════════════════════════════════════════════════

class TestTutorScoresAsCloserWhenSBOrLibraryHasPayoff:
    """Tutor whose target deck holds a STORM-keyword payoff scores
    symmetrically to the STORM finisher branch."""

    def test_tutor_holds_while_non_tutor_fuel_remains(self, card_db):
        game, snap = _base_game(card_db, opp_life=20, storm=2)
        wish = _add(game, card_db, "Wish", controller=0, zone="hand")
        # SB has a real payoff so tutor IS a finisher path.
        game.players[0].sideboard.append(
            _add(game, card_db, "Grapeshot", controller=0,
                 zone="sideboard"))
        # Non-tutor chain fuel still in hand.
        _add(game, card_db, "Desperate Ritual", controller=0, zone="hand")

        ev = card_combo_modifier(
            wish, _assessment(payoff_names={"Grapeshot"}), snap,
            game.players[0], game, 0)

        assert ev < 0.0, (
            f"Tutor with non-tutor fuel still in hand must HOLD; "
            f"got {ev:.3f}.  Rule: symmetric to STORM finisher branch — "
            f"each chain-extending non-tutor card delays the tutor."
        )

    def test_tutor_does_not_score_without_payoff_access(self, card_db):
        """If SB ∪ library has NO real payoff, the tutor branch must
        NOT engage — it falls through to other branches and returns
        whatever they decide (commonly 0)."""
        game, snap = _base_game(card_db, opp_life=20, storm=2)
        wish = _add(game, card_db, "Wish", controller=0, zone="hand")
        # SB and library are empty of finishers — tutor has no real
        # target.  No fuel either — no other branch applies.

        ev = card_combo_modifier(
            wish, _assessment(payoff_names=set()), snap,
            game.players[0], game, 0)

        # Without payoff access the tutor branch is skipped; what
        # remains is "no other rule fires" → 0.0.
        assert ev == 0.0, (
            f"Tutor without payoff access must NOT engage the closer "
            f"branch; got {ev}.  Rule: payoff_names empty → tutor "
            f"is not a finisher path."
        )


# ═════════════════════════════════════════════════════════════════
# 4. Cost-reducer arithmetic
# ═════════════════════════════════════════════════════════════════

class TestCostReducerArithmeticMatchesStormCount:
    """Cost-reducer engine returns a non-negative modifier derived
    from `find_all_chains` damage delta with vs. without the reducer."""

    def test_reducer_returns_non_negative(self, card_db):
        game, snap = _base_game(card_db, opp_life=20, my_mana=4,
                                 storm=0)
        reducer = _add(game, card_db, "Ruby Medallion", controller=0,
                       zone="hand")
        # Hand has fuel + finisher so chains exist to compare.
        _add(game, card_db, "Desperate Ritual", controller=0, zone="hand")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")
        _add(game, card_db, "Grapeshot", controller=0, zone="hand")

        ev = card_combo_modifier(
            reducer, _assessment(payoff_names={"Grapeshot"}), snap,
            game.players[0], game, 0)

        assert ev >= 0.0, (
            f"Cost-reducer modifier must be non-negative (chain "
            f"improvement, never punitive); got {ev:.3f}."
        )


# ═════════════════════════════════════════════════════════════════
# 5. Ritual patience at storm = 0 (reducer-first heuristic)
# ═════════════════════════════════════════════════════════════════

class TestRitualPatienceAtStormZero:
    """Ritual at storm=0 with castable reducer in hand returns a
    negative modifier — deploy the reducer first for amplification."""

    def test_holds_when_castable_reducer_in_hand(self, card_db):
        # storm=0, my_mana=2 (enough for Ruby Medallion).
        game, snap = _base_game(card_db, opp_life=20, my_mana=2,
                                 storm=0, my_lands=2)
        ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                      zone="hand")
        # Reducer in hand, castable; finisher present so the
        # has_finisher gate doesn't trigger STORM_HARD_HOLD first.
        _add(game, card_db, "Ruby Medallion", controller=0, zone="hand")
        _add(game, card_db, "Grapeshot", controller=0, zone="hand")
        # Extra fuel so the reducer-first penalty has weight.
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")

        ev = card_combo_modifier(
            ritual, _assessment(payoff_names={"Grapeshot"}), snap,
            game.players[0], game, 0)

        assert ev < 0.0 and ev != STORM_HARD_HOLD, (
            f"Ritual at storm=0 with castable reducer in hand must "
            f"return a soft-negative (deploy reducer first), not "
            f"STORM_HARD_HOLD; got {ev}."
        )


# ═════════════════════════════════════════════════════════════════
# 6. Ritual patience at storm >= 1
# ═════════════════════════════════════════════════════════════════

class TestRitualPatienceAtStormGeqOne:
    """Mid-chain ritual gate: hard-hold when no draws remain, soft
    penalty when draws can still dig for the closer."""

    def test_hard_holds_when_no_finisher_no_draws(self, card_db):
        # opp_power=4, my_life=20 → opp_clock_discrete=5, am_dead_next=False.
        game, snap = _base_game(
            card_db, opp_life=20, storm=2, opp_power=4, my_life=20)
        ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                      zone="hand")
        # No finisher in hand, no draws in hand — only fuel.
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")

        ev = card_combo_modifier(
            ritual, _assessment(), snap, game.players[0], game, 0)

        assert ev == STORM_HARD_HOLD, (
            f"Mid-chain ritual with no finisher AND no draws must "
            f"return STORM_HARD_HOLD; got {ev}."
        )

    def test_soft_penalty_when_draws_remain(self, card_db):
        game, snap = _base_game(
            card_db, opp_life=20, storm=2, opp_power=4, my_life=20)
        ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                      zone="hand")
        # Draw spell in hand → can still dig for the finisher.
        _add(game, card_db, "Reckless Impulse", controller=0, zone="hand")

        ev = card_combo_modifier(
            ritual, _assessment(), snap, game.players[0], game, 0)

        assert ev < 0.0 and ev != STORM_HARD_HOLD, (
            f"Mid-chain ritual with draws remaining must return a "
            f"soft-negative penalty (not the sentinel); got {ev}."
        )


# ═════════════════════════════════════════════════════════════════
# 7. Flip-transform stack batching
# ═════════════════════════════════════════════════════════════════

class TestFlipTransformStackBatching:
    """Cheap instant/sorcery with a flip-coin transform creature on
    board returns a NON-NEGATIVE modifier proportional to
    0.5^(storm+1).  Pinned via direct oracle-text patch so the test
    is independent of which DFC face the DB loads as the searchable
    template (different load paths yield different name/oracle for
    Ral, Monsoon Mage // Ral, Leyline Prodigy)."""

    def test_positive_when_flip_creature_in_play(self, card_db):
        game, snap = _base_game(card_db, opp_life=20, storm=2)
        # Use any creature template, then patch its oracle_text to
        # guarantee the detection pattern matches regardless of how
        # the DFC face loaded.  The branch's logic is what we pin,
        # not the specific Modern card.  Save/restore to avoid
        # leaking the patched oracle into later tests (templates are
        # session-scoped via the conftest fixture).
        ral = _add(game, card_db, "Goblin Electromancer", controller=0,
                   zone="battlefield")
        original_oracle = ral.template.oracle_text
        try:
            ral.template.oracle_text = (
                "Whenever you cast an instant or sorcery spell, flip a coin."
            )
            ral.is_transformed = False
            cantrip = _add(game, card_db, "Reckless Impulse",
                           controller=0, zone="hand")

            ev = card_combo_modifier(
                cantrip, _assessment(), snap, game.players[0], game, 0)
        finally:
            ral.template.oracle_text = original_oracle

        assert ev > 0.0, (
            f"Cantrip with untransformed flip-coin creature on board "
            f"must return a positive flip-batching modifier; got {ev:.3f}.  "
            f"Rule: marginal P(flip this spell) = 0.5^(storm+1)."
        )

    def test_zero_when_no_flip_creature(self, card_db):
        game, snap = _base_game(card_db, opp_life=20, storm=2)
        # Ordinary creature with no flip-coin oracle — branch must
        # not fire; cantrip falls through every other branch to 0.0.
        _add(game, card_db, "Psychic Frog", controller=0,
             zone="battlefield")
        cantrip = _add(game, card_db, "Reckless Impulse", controller=0,
                       zone="hand")

        ev = card_combo_modifier(
            cantrip, _assessment(), snap, game.players[0], game, 0)

        assert ev == 0.0, (
            f"Cantrip with no flip-coin creature on board must fall "
            f"through to 0.0; got {ev}."
        )


# ═════════════════════════════════════════════════════════════════
# 8. Search-tax awareness
# ═════════════════════════════════════════════════════════════════

class TestSearchTaxAwareness:
    """Tutor on a board where opp controls a "whenever a player
    searches" permanent returns a negative modifier."""

    def test_negative_when_opp_has_search_tax(self, card_db):
        # Search-tax is reached only after the non-storm-payoff branch
        # is bypassed.  Tutors get role='payoff' from the fallback
        # role detection (`'tutor' in tags` → 'payoff'), which would
        # short-circuit at line 711 with return 0.0.  Pre-populate
        # `_role_cache` to classify Wish as 'enablers' (gameplan-style
        # role) so the non-storm-payoff branch is skipped and
        # search-tax is reachable.
        game, snap = _base_game(card_db, opp_life=20, storm=0)
        wish = _add(game, card_db, "Wish", controller=0, zone="hand")
        _add(game, card_db, "Aven Mindcensor", controller=1,
             zone="battlefield")

        a = _assessment(payoff_names=set(), payoff_value=0.0)
        a._role_cache = {"Wish": "enablers"}

        ev = card_combo_modifier(
            wish, a, snap, game.players[0], game, 0)

        assert ev < 0.0, (
            f"Tutor with opp search-tax permanent on board must "
            f"return a negative modifier (cards given away); "
            f"got {ev:.3f}."
        )
