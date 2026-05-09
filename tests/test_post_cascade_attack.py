"""Post-cascade / post-payoff aggression generalises across goal layer.

Rule (mechanic-phrased): when the GoalEngine has advanced to
``PUSH_DAMAGE`` (the gameplan-declared post-payoff phase: "win with
the army on the table"), ``decide_attackers`` MUST loosen its
combat-EV threshold by the same magnitude it does when the legacy
``aggression_boost_turns`` flag is set.

In code terms: the active-aggression branch in ``decide_attackers``
must fire when EITHER signal is present:

  post_payoff_active = (
      getattr(me, 'aggression_boost_turns', 0) > 0   # LE resolve flag
      or self._is_push_damage_goal()                 # goal-layer signal
  )

Why this rule, not the carrier-card or the matchup:
- ``PUSH_DAMAGE`` is a deck-declared goal in
  ``decks/gameplans/*.json``; reaching it is the gameplan's
  contract that "the win condition is now on the battlefield".
- The legacy ``aggression_boost_turns`` flag is set ONLY by
  ``_resolve_living_end`` in the engine.  Goryo's Vengeance,
  Through the Breach, future cascade-into-army printings, and
  every other reanimator with a final ``PUSH_DAMAGE`` goal go
  through ``game.reanimate`` (which doesn't set the flag).  This
  test lifts the gate to the goal layer so the same threshold
  reduction applies generically.

Class size: every Modern combo / cascade / reanimator deck whose
gameplan ends in a ``PUSH_DAMAGE`` goal — Living End, Goryo's
Vengeance (currently registered), and any future printing of the
shape.  Today both decks declare this goal; today only LE benefits
from the threshold reduction.

Subsystem: ``ai/ev_player.py::decide_attackers`` and the new
``EVPlayer._is_push_damage_goal`` helper that this test pins.

Knowledge location: this test consults ``ai.gameplan.GoalType``
and the gameplan-loader output; no card name appears in the
assertion logic.

Failing-first record: before the fix, ``_is_push_damage_goal`` did
not exist on EVPlayer (AttributeError), and the
``decide_attackers`` aggression branch read only the LE-specific
flag.  This test pins both: existence of the helper, and that the
helper feeds the aggression-boost path of ``decide_attackers``.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from ai.gameplan import GoalType
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone, sick=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = sick
    pile = ('library' if zone == 'library' else zone)
    getattr(game.players[controller], pile).append(card)
    return card


def _post_payoff_state(card_db, deck_name: str,
                        attacker_names, blocker_names=(),
                        my_life: int = 12, opp_life: int = 18):
    """Construct a minimal post-payoff combat state for a combo deck."""
    game = GameState(rng=random.Random(0))
    for land_name in ("Swamp", "Swamp", "Mountain", "Forest"):
        _add(game, card_db, land_name, controller=0, zone="battlefield")
    for c in attacker_names:
        _add(game, card_db, c, controller=0, zone="battlefield",
             sick=False)
    for c in blocker_names:
        _add(game, card_db, c, controller=1, zone="battlefield",
             sick=False)
    game.players[0].life = my_life
    game.players[1].life = opp_life
    game.players[0].deck_name = deck_name
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.active_player_idx = 0
    return game


class TestPushDamageGoalLiftsAttackThreshold:
    """The post-payoff aggression threshold-reduction must fire on
    the goal-layer signal, not only on the LE-specific resolve flag."""

    def test_is_push_damage_goal_helper_exists_and_reads_goal_engine(
            self, card_db):
        """Pin the existence of ``EVPlayer._is_push_damage_goal``.

        The helper centralises the goal-layer query so the
        aggression branch in ``decide_attackers`` doesn't import
        ``GoalType`` inline.  Without the helper there is no
        principled, factored point to add the goal-layer signal —
        the legacy code only reads ``aggression_boost_turns``.
        """
        player = EVPlayer(player_idx=0, deck_name="Goryo's Vengeance",
                          rng=random.Random(0))
        # The method must exist (failing-first: AttributeError before fix).
        assert hasattr(player, "_is_push_damage_goal"), (
            "EVPlayer must expose `_is_push_damage_goal` so the "
            "aggression branch in `decide_attackers` can detect the "
            "deck-declared post-payoff state without importing "
            "GoalType inline.  Missing helper means no principled "
            "place to wire the goal-layer signal."
        )

    def test_helper_returns_true_when_goal_is_push_damage(
            self, card_db):
        """The helper must return True after the GoalEngine advances
        to PUSH_DAMAGE — exactly the state the engine produces post-
        Living-End via ``_pending_goal_advance`` and the equivalent
        state every cascade-reanimator deck reaches at the end of
        its gameplan."""
        player = EVPlayer(player_idx=0, deck_name="Goryo's Vengeance",
                          rng=random.Random(0))
        assert player.goal_engine is not None
        # Initially the goal engine is on the first goal (DISRUPT or
        # FILL_RESOURCE for combo decks); advance to PUSH_DAMAGE.
        while (player.goal_engine.current_goal.goal_type
               != GoalType.PUSH_DAMAGE):
            player.goal_engine.advance_goal()
        assert player._is_push_damage_goal() is True, (
            "Helper must return True when the goal engine has "
            "advanced to PUSH_DAMAGE.  This is the goal-layer "
            "signal that ``decide_attackers`` consults to loosen "
            "the attack threshold for post-payoff aggression."
        )

    def test_helper_returns_false_when_goal_is_not_push_damage(
            self, card_db):
        """Symmetric: the helper must return False when the goal
        engine is on any earlier goal (DISRUPT, FILL_RESOURCE,
        EXECUTE_PAYOFF) — those are pre-payoff phases where the
        loose threshold should NOT apply."""
        player = EVPlayer(player_idx=0, deck_name="Goryo's Vengeance",
                          rng=random.Random(0))
        assert player.goal_engine is not None
        # First goal is not PUSH_DAMAGE for any cascade-reanimator
        # gameplan (LE / Goryo's both start on FILL_RESOURCE or
        # DISRUPT).
        assert (player.goal_engine.current_goal.goal_type
                != GoalType.PUSH_DAMAGE), (
            "Test setup: the gameplan's first goal must not be "
            "PUSH_DAMAGE — every combo gameplan progresses through "
            "earlier goals first."
        )
        assert player._is_push_damage_goal() is False, (
            "Helper must return False when the goal engine has "
            "not yet advanced to PUSH_DAMAGE.  Loosening the "
            "attack threshold pre-payoff would mis-trigger the "
            "aggression branch on earlier turns."
        )

    def test_decide_attackers_loosens_threshold_under_push_damage_goal(
            self, card_db):
        """Integration: the goal-layer signal must propagate into
        ``decide_attackers`` so a borderline-EV combat that would
        be vetoed under the default threshold (-0.5 for COMBO) is
        green-lit when the goal layer is on PUSH_DAMAGE.

        Setup mirrors the real Living-End-just-resolved board: the
        AI has its reanimated army (creatures with summoning
        sickness lifted) and the opponent's board has been wiped
        to a single creature.  The state is strictly clock-positive
        for the AI; passing combat would forfeit the cascade.
        """
        game = _post_payoff_state(
            card_db,
            deck_name="Goryo's Vengeance",
            attacker_names=["Subtlety"],
            # Single 1/1 chump.  Subtlety (3/3) trivially survives
            # any block — strictly clock-positive swing.
            blocker_names=["Memnite"],
            my_life=10, opp_life=14,
        )
        player = EVPlayer(player_idx=0,
                          deck_name="Goryo's Vengeance",
                          rng=random.Random(0))
        assert player.goal_engine is not None
        while (player.goal_engine.current_goal.goal_type
               != GoalType.PUSH_DAMAGE):
            player.goal_engine.advance_goal(game, reason="test_setup")
        # Pre-condition: aggression_boost_turns is NOT set — this
        # test exercises the goal-layer signal independently of the
        # legacy LE-specific flag.
        assert getattr(game.players[0],
                       'aggression_boost_turns', 0) == 0
        # Pre-condition: the helper agrees we're in push-damage state.
        assert player._is_push_damage_goal() is True

        attackers = player.decide_attackers(game)
        # The strictly-safe Subtlety swing must fire in PUSH_DAMAGE
        # state.  Failing here means the goal-layer signal is not
        # wired into ``decide_attackers`` — the AI is still relying
        # on the LE-only ``aggression_boost_turns`` flag.
        assert len(attackers) >= 1, (
            f"AI passed combat with PUSH_DAMAGE goal active and a "
            f"strictly-safe attacker (Subtlety 3/3 vs Memnite 1/1).  "
            f"The post-payoff aggression branch in decide_attackers "
            f"is reading only the LE-specific aggression_boost_turns "
            f"flag — it must also read the goal-layer signal so all "
            f"cascade-reanimator decks (Living End, Goryo's, future "
            f"PUSH_DAMAGE-final shells) benefit equally.  Got "
            f"{len(attackers)} attackers."
        )
