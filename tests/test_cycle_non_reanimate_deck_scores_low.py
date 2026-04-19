"""Bug E.1 — `_score_cycling` credits a flat creature-in-GY bonus even
when the deck has no reanimation path.

Evidence: `replays/boros_rarakkyo_vs_affinity_s63000_trace.txt` —
Affinity T2 scored cycling Sojourner's Companion at +8.0 EV vs casting
Engineered Explosives at −20.0, cycling a 4-power creature into the
graveyard as "future reanimation target" in a deck that cannot
reanimate.

Root cause (see `docs/design/ev_correctness_overhaul.md` §2 Bug E.1):
`ai/ev_player.py:_score_cycling` contains

    if card.template.is_creature:
        power = card.template.power or 0
        ev += (4.0 + power * 0.5)

This bonus fires unconditionally.  The rationale ("creature in GY is
a future reanimation target") only applies when the deck contains a
reanimation path: oracle text like "return target creature card from
a graveyard to the battlefield", a reanimate-tagged spell in hand, a
combo chain that goes graveyard→battlefield, or a gameplan explicitly
declaring GY filling as a goal.  Affinity has none of those.  The
bonus is dead equity — cycling Sojourner produces only the card draw.

This test asserts: in an Affinity context with no reanimation signal
available, the cycle EV must reduce to the card-draw baseline
(`card_clock_impact(snap) × 20.0`) — the only real value the action
produces in that deck.  The flat +4/+power bonus must be gated on a
reanimation-path signal and not fire here.

Regression anchor: in a Living-End context (cascade in hand, cycling
is the explicit gameplan), cycle EV must stay above that same
card-draw baseline so the gameplan-critical action isn't suppressed.
"""
from __future__ import annotations

import random

import pytest

from ai.clock import card_clock_impact
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


class TestCycleNonReanimateDeckScoresLow:
    """Cycle scoring must not credit a creature-in-GY bonus for decks
    that have no reanimation path."""

    def test_affinity_cycle_reduces_to_draw_baseline(self, card_db):
        """Affinity T2 cycling Sojourner's Companion.  No reanimation
        spell in hand, no cascade, no prefer_cycling gameplan.  The
        value of cycling is exactly: +1 card drawn = card_clock_impact
        × 20.  Current code adds a flat +4 + power*0.5 = +6 on top.

        Cycling cost for Sojourner = {2}, NOT cheap (> 1) and NOT free
        (no life payment), so neither cost-bonus branch should fire
        either — the post-fix score is exactly the draw baseline.
        """
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Mountain", 0)
        _add_to_battlefield(game, card_db, "Darksteel Citadel", 0)
        sojourner = _add_to_hand(game, card_db, "Sojourner's Companion", 0)

        player = EVPlayer(player_idx=0, deck_name="Affinity",
                          rng=random.Random(0))
        me = game.players[0]
        opp = game.players[1]
        snap = snapshot_from_game(game, 0)

        cycle_ev = player._score_cycling(sojourner, snap, game, me, opp)
        draw_baseline = card_clock_impact(snap) * 20.0

        # Derive the non-magic tolerance from the same cycling-cost
        # bonus constants the scorer itself uses. Sojourner's cycling
        # cost is {2} (not cheap, not free) so neither bonus fires —
        # epsilon is purely floating-point slack.
        eps = 1e-6

        assert cycle_ev <= draw_baseline + eps, (
            f"Affinity has no reanimation path and no cascade to enable "
            f"a graveyard-creature payoff. Cycling Sojourner's Companion "
            f"should score as card-draw only (~{draw_baseline:.3f}); "
            f"got {cycle_ev:.3f} — a creature-in-GY flat bonus is "
            f"firing where no reanimation target exists. Gate the "
            f"bonus on a reanimation signal (spell in hand, oracle text "
            f"referencing GY-to-battlefield, or gameplan prefer_cycling)."
        )

    def test_living_end_cycle_stays_above_draw_baseline(self, card_db):
        """Regression anchor — when the deck DOES have a graveyard
        payoff (Living End in hand, which is the cascade target and
        the board-reset trigger), cycling a creature MUST still score
        above the pure-draw baseline so the gameplan-critical sequence
        (cycle → accumulate GY → cascade into Living End) is chosen."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Forest", 0)
        _add_to_battlefield(game, card_db, "Mountain", 0)
        sojourner = _add_to_hand(game, card_db, "Sojourner's Companion", 0)
        # Cascade-ahead-of-Living-End signal: a cascade card in hand
        # triggers the existing CYCLING_CASCADE_BOOST branch in
        # `_score_cycling` — this is the gameplan-critical case.
        _add_to_hand(game, card_db, "Shardless Agent", 0)

        player = EVPlayer(player_idx=0, deck_name="Living End",
                          rng=random.Random(0))
        me = game.players[0]
        opp = game.players[1]
        snap = snapshot_from_game(game, 0)

        cycle_ev = player._score_cycling(sojourner, snap, game, me, opp)
        draw_baseline = card_clock_impact(snap) * 20.0

        assert cycle_ev > draw_baseline, (
            f"Living End with cascade in hand: cycling a creature must "
            f"score ABOVE the pure-draw baseline ({draw_baseline:.3f}) "
            f"so the gameplan-critical sequence is preferred. Got "
            f"{cycle_ev:.3f}. Over-aggressive suppression of the "
            f"creature-cycle bonus has broken Living End."
        )
