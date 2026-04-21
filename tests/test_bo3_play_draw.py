"""
Bo3 play/draw rule — loser of game N chooses who plays game N+1.

In Modern tournament Bo3, after game 1 the loser chooses whether to play
or draw first in the next game. Near-universally the loser chooses play
(on-play has ~54% WR). We model that rule here: default = loser plays.

See docs/diagnostics/2026-04-19_bo3_play_draw_rule.md for the observed
symptom (seed 63000 Bo3, Boros won G1 but Boros was also on the play in G2).
"""
import random
from dataclasses import replace
from typing import List

import pytest

from engine.game_runner import GameRunner, GameResult


class _CapturingRunner(GameRunner):
    """GameRunner that records the forced_first_player passed to run_game,
    then returns a scripted GameResult without actually playing anything.

    This isolates the Bo3 orchestration logic from card-level sim cost."""

    def __init__(self, card_db, scripted_winners: List[int]):
        super().__init__(card_db, rng=random.Random(0))
        self._scripted_winners = list(scripted_winners)
        self.captured_forced: List = []  # one entry per run_game call

    def run_game(self, deck1_name, deck1_list, deck2_name, deck2_list,
                 verbose=False, deck1_sideboard=None, deck2_sideboard=None,
                 forced_first_player=None):
        self.captured_forced.append(forced_first_player)
        winner = self._scripted_winners[len(self.captured_forced) - 1]
        return GameResult(
            winner=winner,
            winner_deck=deck1_name if winner == 0 else deck2_name,
            loser_deck=deck2_name if winner == 0 else deck1_name,
            turns=5, winner_life=10, loser_life=0,
            win_condition="damage",
            deck1_name=deck1_name, deck2_name=deck2_name,
        )


@pytest.fixture
def minimal_decks():
    """Return deck dicts with empty mainboards; _CapturingRunner never
    actually builds them so contents don't matter."""
    return (
        {"mainboard": {}, "sideboard": {}},
        {"mainboard": {}, "sideboard": {}},
    )


def test_g1_uses_random_die_not_forced(card_db, minimal_decks):
    """G1 has no prior loser, so forced_first_player must be None
    (the engine then rolls via rng.randint). Regression guard."""
    d1, d2 = minimal_decks
    runner = _CapturingRunner(card_db, scripted_winners=[0, 0])  # P1 sweeps 2-0
    runner.run_match("Deck1", d1, "Deck2", d2)

    # G1: no prior game, no forced player.
    assert runner.captured_forced[0] is None, (
        f"G1 must use random die, got forced={runner.captured_forced[0]}"
    )


def test_g2_loser_of_g1_is_on_play(card_db, minimal_decks):
    """G1 winner = P1 (deck1, idx 0). Loser = P2 (idx 1). G2 must force P2 onto the play."""
    d1, d2 = minimal_decks
    runner = _CapturingRunner(card_db, scripted_winners=[0, 0])  # P1 sweeps; G3 never runs
    runner.run_match("Deck1", d1, "Deck2", d2)

    assert len(runner.captured_forced) == 2  # sweep stops after G2
    assert runner.captured_forced[1] == 1, (
        f"G2 must force loser of G1 (idx 1) onto play, got forced={runner.captured_forced[1]}"
    )


def test_g3_loser_of_g2_is_on_play(card_db, minimal_decks):
    """P1 wins G1, P2 wins G2. G3: loser of G2 = P1 (idx 0) must be on play."""
    d1, d2 = minimal_decks
    # Script: G1=P1 wins, G2=P2 wins, G3=P1 wins. Match goes to G3.
    runner = _CapturingRunner(card_db, scripted_winners=[0, 1, 0])
    runner.run_match("Deck1", d1, "Deck2", d2)

    assert len(runner.captured_forced) == 3
    # G2: loser of G1 = idx 1
    assert runner.captured_forced[1] == 1
    # G3: loser of G2 = idx 0
    assert runner.captured_forced[2] == 0, (
        f"G3 must force loser of G2 (idx 0) onto play, got forced={runner.captured_forced[2]}"
    )


def test_setup_game_honours_forced_first_player(card_db):
    """Direct engine-layer contract: GameState.setup_game accepts
    forced_first_player and respects it in preference to the RNG roll."""
    from engine.game_state import GameState
    from engine.card_database import CardDatabase

    # Build trivial 10-card decks (any legal CardTemplates will do)
    db = card_db
    # Pick any basic — the Mountain is present in modern sims
    template = db.get_card("Mountain")
    deck1 = [template] * 10
    deck2 = [template] * 10

    for forced in (0, 1):
        game = GameState(rng=random.Random(1))  # fixed seed
        game.setup_game(deck1, deck2, forced_first_player=forced)
        assert game.active_player == forced
        assert game.priority_player == forced


def test_setup_game_defaults_to_rng_when_unforced(card_db):
    """Regression: when forced_first_player is not supplied, the RNG
    is still consulted. Over many seeds, both players must be active
    at least once — proves we didn't accidentally hardwire a default."""
    from engine.game_state import GameState
    db = card_db
    template = db.get_card("Mountain")
    deck1 = [template] * 10
    deck2 = [template] * 10

    seen = set()
    for seed in range(50):
        game = GameState(rng=random.Random(seed))
        game.setup_game(deck1, deck2)  # no forced
        seen.add(game.active_player)
    assert seen == {0, 1}, f"RNG default must hit both players, got {seen}"
