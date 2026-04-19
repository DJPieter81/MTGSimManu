"""Bug E.1 — Cycling a creature scores the +N "future reanimate
target" bonus unconditionally, even when the deck has no way to
reanimate anything.

Design: docs/design/ev_correctness_overhaul.md §2.E

`ai/ev_player.py:_score_cycling` at line 1301 currently adds
`(4.0 + power * 0.5)` for every cyclable creature — a flat bonus
rationalised as "creature in graveyard = future reanimation
target".  In a deck without Living End / Persist / Unburial Rites /
Goryo's / etc., the creature just sits in the graveyard as dead
equity; the bonus is a free +5 that biases the AI toward cycling
regardless of board state.

Observed: replays/boros_rarakkyo_vs_affinity_s63000_trace.txt —
Affinity T2 scored cycle at +8.0 vs cast Engineered Explosives at
−20.0, cycled Sojourner for 2 mana for no payoff.

The fix gates the bonus on a reanimation path existing in the
deck's gameplan or visible cards.  Regression: for Living End
(a reanimator deck), the bonus still fires and cycle EV remains
materially above the pure-card-draw baseline.
"""
from __future__ import annotations

import random

import pytest

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
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
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
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _cycle_ev(game, deck_name, card):
    """Helper: compute _score_cycling for the given card and deck."""
    game.players[0].deck_name = deck_name
    player = EVPlayer(player_idx=0, deck_name=deck_name,
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    opp = game.players[1]
    return player._score_cycling(card, snap, game, me, opp)


class TestCycleNonReanimateDeckScoresLow:
    """Cycle EV for a creature in a non-reanimator deck should not
    inherit the `creature-in-GY = future-reanimate` bonus."""

    def test_street_wraith_cycle_ev_low_in_non_reanimator_deck(
            self, card_db):
        """Boros Energy has no reanimation.  Street Wraith's cycle EV
        should be ≈ the pure card-draw value + free-cost bonus, NOT
        include the +4 + power·0.5 "creature cycled" bonus.  Current
        EV ≈ 10; fix should drop it below 5 (draw + free-cost only)."""
        game = GameState(rng=random.Random(0))
        for _ in range(3):
            _add_to_battlefield(game, card_db, "Mountain", controller=0)
        wraith = _add_to_hand(game, card_db, "Street Wraith",
                               controller=0)
        game.players[1].deck_name = "Dimir Midrange"

        ev_boros = _cycle_ev(game, "Boros Energy", wraith)

        # Current code path: 4.0 + power*0.5 + free_cost (2) + draw (≈1)
        #   = ≈ 7.5 with power=1.  Actual observed: 10.0 (game-state
        #   specifics bump it).  Fix target: below 5.0 — remove the
        #   unconditional creature-in-GY bonus.
        MAX_CYCLE_EV_WITHOUT_REANIMATION = 5.0
        assert ev_boros < MAX_CYCLE_EV_WITHOUT_REANIMATION, (
            f"Street Wraith cycle EV in a Boros deck (no reanimation) "
            f"= {ev_boros:.3f}, above the cap {MAX_CYCLE_EV_WITHOUT_REANIMATION}.  "
            f"The '+ (4.0 + power·0.5)' creature-in-GY bonus at "
            f"ai/ev_player.py:1301 should only fire when the deck "
            f"actually has a reanimation path (Living End, Persist, "
            f"Unburial Rites, Goryo's Vengeance, etc.).  A dead "
            f"creature in Boros Energy's graveyard is not equity."
        )

    def test_street_wraith_cycle_ev_high_in_living_end(
            self, card_db):
        """Regression: Living End IS a reanimator deck — creatures in
        its GY are the win condition.  Cycle EV should remain high
        (well above the non-reanimator cap) so the deck still cycles
        aggressively to fill the GY before cascading."""
        game = GameState(rng=random.Random(0))
        for _ in range(3):
            _add_to_battlefield(game, card_db, "Mountain", controller=0)
        wraith = _add_to_hand(game, card_db, "Street Wraith",
                               controller=0)
        game.players[1].deck_name = "Dimir Midrange"

        ev_le = _cycle_ev(game, "Living End", wraith)

        MIN_CYCLE_EV_WITH_REANIMATION = 5.0
        assert ev_le > MIN_CYCLE_EV_WITH_REANIMATION, (
            f"Regression: Street Wraith cycle EV in Living End "
            f"= {ev_le:.3f}, at or below the non-reanimator cap "
            f"({MIN_CYCLE_EV_WITH_REANIMATION}).  Living End IS a "
            f"reanimator — creatures in GY are the combo payoff.  The "
            f"fix must keep the creature-in-GY bonus active for decks "
            f"with reanimation paths."
        )
