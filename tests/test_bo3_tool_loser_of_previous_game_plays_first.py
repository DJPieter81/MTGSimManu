"""The `--bo3` diagnostic tool must seat the loser of the previous game on
the play (CR 103.2), exactly as the matrix's own Bo3 path does.

`run_meta.run_bo3` relabelled the seats — the loser became "P1" in the log
header — but never passed `forced_first_player` to `run_game`, so
`GameState.setup_game` rolled the opening die again with the per-game seed.
Observed (2026-09-08, seven replays): the previous game's WINNER kept the
play in every game 2 and 3 ("Izzet Prowess wins → chooses to play first"
three times in a match Prowess had won game 1 of). The matrix path
(`GameRunner.run_match`) forces the loser first, so every `--bo3` diagnostic
was read under conditions the matrix never produces.

Rule: game 1 is a die roll; every later game's first player is the loser of
the previous decided game; after a drawn game the die is rolled again.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import run_meta


def _result(winner_idx, deck_names, turns=5):
    winner = None if winner_idx is None else winner_idx
    return SimpleNamespace(
        winner=winner,
        winner_deck=("draw" if winner is None else deck_names[winner]),
        loser_deck=("draw" if winner is None else deck_names[1 - winner]),
        turns=turns, win_condition=("draw" if winner is None else "damage"),
        winner_life=10, loser_life=0, game_log=[], game_number=1,
    )


class _Runner:
    """Records the seating and forced first player of every game."""
    def __init__(self, winners):
        self.winners = list(winners)   # winner index per game, in P1/P2 seats
        self.calls = []
        self.rng = None

    def run_game(self, d1_name, d1_main, d2_name, d2_main, *,
                 deck1_sideboard=None, deck2_sideboard=None, verbose=False,
                 replay_log=None, game_number=1, forced_first_player=None):
        self.calls.append({"p1": d1_name, "p2": d2_name,
                           "forced_first_player": forced_first_player})
        return _result(self.winners.pop(0), (d1_name, d2_name))


@pytest.fixture()
def two_fake_decks(monkeypatch):
    decks = {"Deck A": {"mainboard": {"Island": 60}, "sideboard": {}},
             "Deck B": {"mainboard": {"Mountain": 60}, "sideboard": {}}}
    monkeypatch.setattr(run_meta, "MODERN_DECKS", decks)
    import engine.sideboard_manager as sbm
    monkeypatch.setattr(sbm, "sideboard", lambda main, sb, me, opp: (main, sb))
    return decks


def test_loser_of_the_previous_game_is_seated_first_and_forced_on_the_play(
        monkeypatch, two_fake_decks):
    # G1: seat-2 (Deck B) wins; G2: seat-2 wins again; match over 0-2? No —
    # make it go three: G1 B wins, G2 A wins (A is P1 as G1's loser), G3 ...
    runner = _Runner(winners=[1, 0, 0])
    monkeypatch.setattr(run_meta, "_get_runner", lambda: runner)

    run_meta.run_bo3("Deck A", "Deck B", seed=50000)

    g1, g2, g3 = runner.calls
    assert g1["forced_first_player"] is None, "game 1 is a die roll"
    # G1: Deck B (P2) won → Deck A lost → Deck A is P1 in G2 and is forced first.
    assert (g2["p1"], g2["forced_first_player"]) == ("Deck A", 0)
    # G2: P1 (Deck A) won → Deck B lost → Deck B is P1 in G3 and is forced first.
    assert (g3["p1"], g3["forced_first_player"]) == ("Deck B", 0)


def test_after_a_drawn_game_the_die_is_rolled_again(monkeypatch, two_fake_decks):
    # G1 draw; G2 P1 (Deck A, seated first after the draw) wins; G3 seats
    # Deck B first as G2's loser and Deck A (now P2) wins to close 2-0.
    runner = _Runner(winners=[None, 0, 1])
    monkeypatch.setattr(run_meta, "_get_runner", lambda: runner)

    run_meta.run_bo3("Deck A", "Deck B", seed=50000)

    assert len(runner.calls) == 3
    assert runner.calls[1]["forced_first_player"] is None, (
        "a draw has no loser — CR 103.2 gives nobody the choice, so the "
        "opening die decides again")
    assert (runner.calls[2]["p1"], runner.calls[2]["forced_first_player"]) == ("Deck B", 0)
