"""Storm prefers direct-damage closer over token closer at lethal range.

Rule (mechanic, not card name):
    When two STORM-keyword payoffs are accessible at lethal range
    (storm_count + 1 >= opp_life), the AI must rank the payoff that
    DEALS direct damage same-turn (oracle text "deals N damage")
    strictly above the payoff that produces summoning-sick CREATURE
    TOKENS (oracle text "create … tokens").  Tokens require a combat
    step to deal damage and creatures cast this turn cannot attack
    the same turn (CR 302.1: summoning sickness).

Class size: every storm-keyword finisher pair, present and future.
The classification is purely oracle-driven (`classify_card`'s
`deals_direct_damage` flag), so any future Modern printing of a
storm payoff is covered.

Subsystem: `ai/combo_calc.py::card_combo_modifier`'s STORM finisher
branch is the single owner. The chain projection in
`ai/combo_chain.py` already separates the two via
`payoff_deals_damage` / `payoff_has_storm`, but the modifier returned
the same value (`combo_value`) for both, leaving the direct-damage
preference unexpressed.

Anti-pattern (pre-fix): when `storm+1 >= opp_life` the modifier
returns `combo_value` for both Grapeshot AND Empty the Warrens.
With opp at 1 life and storm=13, the AI's tiebreaker fell to
gameplan-priority (Grapeshot 18.0 vs Empty 17.0) plus other
non-deterministic noise, occasionally selecting Empty the Warrens
and creating 26 summoning-sick goblins instead of firing Grapeshot
for the immediate 1-damage kill. Goblins cannot attack the same
turn → opponent survives → Storm dies next turn.

Repro: seed-50000-class anti-pattern. Storm chained 13 spells, opp
on 1 life, hand has Grapeshot AND Empty the Warrens. AI must rank
Grapeshot strictly above Empty the Warrens.

This is generic by construction: it benefits any combo deck with
multiple STORM-keyword payoffs of differing damage profile (current
Galvanic Relay-style decks; future Modern storm reprintings).
"""
from __future__ import annotations

import random

import pytest

from ai.combo_calc import assess_combo, card_combo_modifier
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from ai.gameplan import GoalType
from engine.cards import CardInstance, Keyword as Kw
from engine.game_state import GameState, Phase


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


def _build_storm_lethal_state(card_db, opp_life=1, storm=13,
                               mountains=8):
    """Storm at lethal-range: storm count high, opp life low, BOTH
    Grapeshot AND Empty the Warrens in hand (the choice under test),
    plus a small ritual to establish a non-empty hand context."""
    game = GameState(rng=random.Random(0))
    for _ in range(mountains):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    grapeshot = _add(game, card_db, "Grapeshot", controller=0,
                     zone="hand")
    empty = _add(game, card_db, "Empty the Warrens", controller=0,
                 zone="hand")
    _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")

    game.players[0].life = 5
    game.players[1].life = opp_life
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Eldrazi Tron"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 7
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = storm
    game._global_storm_count = storm
    return game, grapeshot, empty


def _force_payoff_goal(player: EVPlayer):
    ge = player.goal_engine
    assert ge is not None, "Storm must have a goal engine"
    while ge.current_goal.goal_type != GoalType.EXECUTE_PAYOFF:
        if ge.current_goal_idx >= len(ge.gameplan.goals) - 1:
            break
        ge.advance_goal(None, "test setup: force EXECUTE_PAYOFF")
    assert ge.current_goal.goal_type == GoalType.EXECUTE_PAYOFF


class TestStormPrefersDirectDamageCloserAtLethalRange:
    """At lethal-range, the direct-damage STORM payoff must rank
    strictly above the non-damage token payoff in the combo modifier
    that drives play-ordering in turn_planner."""

    def test_grapeshot_outranks_empty_in_combo_modifier_at_lethal(
            self, card_db):
        """Both Grapeshot and Empty the Warrens are STORM-keyword
        payoffs with `storm+1 >= opp_life`. Pre-fix both return
        `combo_value` from `card_combo_modifier`; the post-fix
        contract is `mod(Grapeshot) > mod(Empty)` by a margin
        derived from existing primitives (combo_value / opp_life =
        per-point-of-damage swing) — large enough to win the play-
        ordering tiebreaker against gameplan-priority noise."""
        game, grapeshot, empty = _build_storm_lethal_state(
            card_db, opp_life=1, storm=13)
        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        _force_payoff_goal(player)
        snap = snapshot_from_game(game, 0)
        me = game.players[0]

        # Sanity: both are STORM-keyword payoffs.
        assert Kw.STORM in grapeshot.template.keywords
        assert Kw.STORM in empty.template.keywords
        # Sanity: lethal-range condition holds.
        assert me.spells_cast_this_turn + 1 >= max(1, snap.opp_life)

        a = assess_combo(game, 0, player.goal_engine, snap, None)
        mod_g = card_combo_modifier(grapeshot, a, snap, me, game, 0)
        mod_e = card_combo_modifier(empty, a, snap, me, game, 0)

        margin = mod_g - mod_e
        # The premium must derive from a principled primitive
        # (combo_value / opp_life — the per-point-of-damage swing the
        # combo engine already uses). With opp_life=1 and combo_value
        # ~62.5, a single per-point swing is ~62.5; we require a
        # strictly positive margin AND large enough to beat the
        # gameplan-priority delta (Grapeshot 18.0 vs Empty 17.0 = 1.0
        # in the priority axis) plus typical discount noise.
        assert margin > 1.0, (
            f"At lethal range with both STORM-keyword payoffs in "
            f"hand, the direct-damage closer (Grapeshot) must "
            f"rank strictly above the token closer (Empty the "
            f"Warrens) in the combo modifier. Got "
            f"mod(Grapeshot)={mod_g:.4f}, "
            f"mod(Empty)={mod_e:.4f}, margin={margin:.4f}. The rule: "
            f"summoning-sick goblins don't kill same-turn (CR 302.1), "
            f"so a token finisher fails to close at lethal range "
            f"while a damage finisher succeeds. The modifier must "
            f"reflect that asymmetry."
        )

    def test_grapeshot_outranks_empty_in_full_score_at_lethal(
            self, card_db):
        """End-to-end check: the play-ordering signal that
        `turn_planner` consumes is `_score_spell`, not the bare
        modifier. With the priority-axis delta only ~1.0pp in favour
        of Grapeshot, the combo-modifier premium must be large enough
        for `_score_spell(Grapeshot) > _score_spell(Empty)` to hold
        robustly, otherwise the tiebreaker can still flip to Empty."""
        game, grapeshot, empty = _build_storm_lethal_state(
            card_db, opp_life=1, storm=13)
        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        _force_payoff_goal(player)
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        ev_g = player._score_spell(grapeshot, snap, game, me, opp)
        ev_e = player._score_spell(empty, snap, game, me, opp)
        margin = ev_g - ev_e
        # Must beat the non-deterministic tie-noise margin in
        # turn_planner's ordering search. The premium-from-primitive
        # (combo_value / opp_life) at opp_life=1 is at scale of
        # combo_value (~10s), so requiring margin > 5.0 cleanly
        # separates the rule from Bayesian/discount noise (typically
        # < 1.0 in this regime).
        assert margin > 5.0, (
            f"At lethal range, _score_spell(Grapeshot) must exceed "
            f"_score_spell(Empty the Warrens) by enough margin to "
            f"survive turn-planner tiebreakers. Got "
            f"ev(Grapeshot)={ev_g:.4f}, ev(Empty)={ev_e:.4f}, "
            f"margin={margin:.4f}. Without this margin the AI fires "
            f"Empty for 26 summoning-sick goblins (CR 302.1) and "
            f"opp survives at 1 life."
        )

    def test_no_premium_when_token_payoff_alone(self, card_db):
        """Regression anchor: when ONLY Empty the Warrens is in hand
        (no direct-damage closer alternative), Empty must still score
        positive at lethal — the premium is a RANKING tool between
        two payoffs, not a penalty against tokens. Tokens still win
        when they're the only option. Anti-regression: don't let the
        fix accidentally suppress the lone-Empty cast."""
        game = GameState(rng=random.Random(0))
        for _ in range(8):
            _add(game, card_db, "Mountain", controller=0,
                 zone="battlefield")
        empty = _add(game, card_db, "Empty the Warrens",
                     controller=0, zone="hand")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")
        game.players[0].life = 5
        game.players[1].life = 1
        game.players[0].deck_name = "Ruby Storm"
        game.players[1].deck_name = "Eldrazi Tron"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 7
        game.players[0].lands_played_this_turn = 1
        game.players[0].spells_cast_this_turn = 13
        game._global_storm_count = 13

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        _force_payoff_goal(player)
        snap = snapshot_from_game(game, 0)
        me = game.players[0]

        a = assess_combo(game, 0, player.goal_engine, snap, None)
        mod_e = card_combo_modifier(empty, a, snap, me, game, 0)

        # Empty alone must remain positively scored (it's still the
        # only payoff). The premium is a ranking tool — it must not
        # suppress the lone-payoff cast below 0.
        assert mod_e > 0.0, (
            f"With Empty as the only finisher in hand at lethal "
            f"range, its modifier must remain positive. Got "
            f"mod(Empty)={mod_e:.4f}. The direct-damage premium is "
            f"meant as a ranking tool between two STORM payoffs, "
            f"not as a token-payoff penalty."
        )
