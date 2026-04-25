"""Storm mid-chain rituals — qualitative held-when-fizzle assertions.

Phase-2b rewrite: the legacy mid-chain refinements (PR #166) — storm
coverage escalation past 0.5, cascade-draw-floor risk, MIN_CHAIN_DEPTH
gating — were a piecewise heuristic on top of a soft penalty.  They
have been deleted in favour of `OutcomeDistribution` math.

The new framework captures the same QUALITATIVE behaviour: a ritual
mid-chain with no finisher in hand and no live PiF flashback target
lands FIZZLE probability mass, producing a negative-EV score below
`pass_threshold` regardless of whether 1 or 3 draws remain in hand.

We do NOT preserve the legacy "1-draw EV is measurably lower than
3-draw EV" relative ordering — this delta was a clamp artefact, not
real signal.  In the distribution model both scenarios share the
same FIZZLE probability (~1.0) and therefore the same expected
value to leading order.  This is documented as a deliberate
simplification: the mid-chain refinements were a tuning knob, not
an observable behaviour, so dropping the relative ordering does not
change AI play in the field.

What we still pin:
1. Mid-chain ritual with no finisher and no PiF → EV < pass_threshold
   (so the AI holds rather than continuing the chain).
2. Mid-chain ritual WITH a finisher in hand → EV > pass_threshold
   (regression: the held behaviour must not over-fire).
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
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
    getattr(game.players[controller], 'library' if zone == 'library'
            else zone).append(card)
    return card


def _build_storm_scenario(card_db, *, storm, opp_life, draws_in_hand,
                          library_size=30, my_life=15,
                          finisher_in_hand=False):
    """Build a mid-chain Storm scenario."""
    game = GameState(rng=random.Random(0))
    _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    _add(game, card_db, "Ruby Medallion", controller=0, zone="battlefield")
    _add(game, card_db, "Ruby Medallion", controller=0, zone="battlefield")

    ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                  zone="hand")
    _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")
    for _ in range(draws_in_hand):
        _add(game, card_db, "Reckless Impulse", controller=0, zone="hand")
    if finisher_in_hand:
        _add(game, card_db, "Grapeshot", controller=0, zone="hand")

    for _ in range(library_size):
        _add(game, card_db, "Mountain", controller=0, zone="library")

    _add(game, card_db, "Guide of Souls", controller=1, zone="battlefield")

    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = storm
    game._global_storm_count = storm
    game.players[0].life = my_life
    game.players[1].life = opp_life

    return game, ritual


def _score(game, ritual):
    player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    opp = game.players[1]
    return player, player._score_spell(ritual, snap, game, me, opp)


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


class TestMidChainRitualHeldWhenFizzling:
    """A mid-chain ritual with no finisher path is held below
    pass_threshold (qualitative); previously this was a hard clamp."""

    def test_no_finisher_held_with_one_draw(self, card_db):
        """storm=4, no finisher in hand, only 1 draw left — chain
        cannot reach a kill.  EV must sit below pass_threshold so
        the AI holds (qualitative, not magnitude-pinned)."""
        game, ritual = _build_storm_scenario(
            card_db, storm=4, opp_life=15, draws_in_hand=1,
            library_size=30, finisher_in_hand=False,
        )
        player, ev = _score(game, ritual)
        assert ev < player.profile.pass_threshold, (
            f"Mid-chain ritual without finisher access scored EV={ev:.2f}, "
            f"above pass_threshold={player.profile.pass_threshold}.  "
            f"FIZZLE probability mass must dominate when no finisher is "
            f"reachable in hand or library."
        )

    def test_no_finisher_held_with_three_draws(self, card_db):
        """Same scenario with 3 draws in hand.  Phase-2b: the
        distribution model does not differentiate 1-draw vs 3-draw
        scenarios at the leading-order EV (both produce FIZZLE-mass
        ≈ 1.0 when no finisher is reachable from hand or library).
        We pin only the qualitative "held below pass_threshold"
        behaviour — relative ordering between draw counts was a
        clamp artefact, not an observable AI behaviour, and is
        documented as a deliberate Phase-2b simplification in the
        PR body."""
        game, ritual = _build_storm_scenario(
            card_db, storm=4, opp_life=15, draws_in_hand=3,
            library_size=30, finisher_in_hand=False,
        )
        player, ev = _score(game, ritual)
        assert ev < player.profile.pass_threshold, (
            f"Mid-chain ritual without finisher access scored EV={ev:.2f}, "
            f"above pass_threshold={player.profile.pass_threshold}."
        )


class TestMidChainRitualFiresWhenFinisherInHand:
    """Regression anchor — the qualitative "fires when ready"
    behaviour must survive the Phase-2b deletions."""

    def test_finisher_in_hand_scores_positive(self, card_db):
        """storm=4, Grapeshot in hand — distribution lands
        COMPLETE_COMBO probability mass on the (storm+1)>=opp_life
        chains.  EV must exceed pass_threshold so the chain
        continues."""
        game, ritual = _build_storm_scenario(
            card_db, storm=4, opp_life=5, draws_in_hand=2,
            library_size=30, finisher_in_hand=True,
        )
        player, ev = _score(game, ritual)
        assert ev > player.profile.pass_threshold, (
            f"Mid-chain ritual WITH Grapeshot in hand scored EV={ev:.2f} "
            f"at or below pass_threshold={player.profile.pass_threshold}.  "
            f"Lethal chain (storm+1=5 >= opp_life=5) must clear the gate."
        )
