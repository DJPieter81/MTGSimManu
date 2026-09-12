"""A drawn game inside a Bo3 match is a game nobody won — the card-detail
extractor counts its turns and casts and credits nobody with a kill.

`extract_card_data.extract_from_match` indexed `kill_cards` and
`kill_turns` (dicts keyed by the two deck names) with `game.winner_deck`,
which the engine sets to the string "draw" for a CR 104.4 turn-cap draw.
That is truthy, so the `if game.winner_deck:` guard let it through and the
whole MATCH raised KeyError('draw'). The per-pair loop swallowed the error
and moved on, so every Bo3 match containing one drawn game vanished from
the pair's card detail: casts, damage, sweeps, game-1 wins and the match
result itself. Control pairs reach the cap most (observed 2026-09-12: nine
matches dropped in the first 38 pairs, all Jeskai Blink vs a control deck).

Rule: a drawn game contributes turns and casts; it yields no kill card,
no kill turn, no game-1 win and no sweep. No sims — the runner is stubbed.
"""
from __future__ import annotations

from types import SimpleNamespace

import extract_card_data as ecd


class _Runner:
    def __init__(self, games, winner):
        self._games = games
        self._winner = winner

    def run_match(self, d1, d1_data, d2, d2_data, verbose=True):
        return SimpleNamespace(games=self._games, winner_deck=self._winner)


def _game(d1, d2, winner, turns, log):
    return SimpleNamespace(deck1_name=d1, deck2_name=d2, winner_deck=winner,
                           turns=turns, game_log=log)


def test_a_drawn_game_contributes_casts_and_turns_but_no_kill(monkeypatch):
    d1, d2 = sorted(ecd.MODERN_DECKS)[:2]
    games = [
        _game(d1, d2, "draw", 30, ["T1 P1: Cast Alpha", "T2 P2: Cast Beta"]),
        _game(d1, d2, d1, 6, ["T1 P1: Cast Alpha", "T3 P1: Cast Alpha"]),
        _game(d1, d2, d1, 7, ["T2 P1: Cast Gamma"]),
    ]
    monkeypatch.setattr(ecd, "parse_damage_from_log",
                        lambda log, a, b: {a: {}, b: {}})
    data = ecd.extract_from_match(_Runner(games, d1), d1, d2, seed=1)

    assert data["games"] == 3 and data["turns"] == 43
    assert data["casts"][d1]["Alpha"] == 3 and data["casts"][d2]["Beta"] == 1
    assert data["kill_turns"] == {d1: [6, 7], d2: []}
    assert sum(data["kill_cards"][d2].values()) == 0
    assert data["g1_wins"] == [0, 0], "a drawn game 1 is nobody's game-1 win"
    assert data["sweeps"] == [0, 0], "a draw then two wins is not a sweep"
    assert data["went_to_3"] == 1
    assert data["comebacks"] == [0, 0], "a drawn game 1 is nobody's loss"
    assert (data["d1_won"], data["d2_won"]) == (1, 0)


def test_two_drawn_games_are_not_a_sweep_for_anyone(monkeypatch):
    d1, d2 = sorted(ecd.MODERN_DECKS)[:2]
    games = [_game(d1, d2, "draw", 30, []), _game(d1, d2, "draw", 30, [])]
    monkeypatch.setattr(ecd, "parse_damage_from_log",
                        lambda log, a, b: {a: {}, b: {}})
    data = ecd.extract_from_match(_Runner(games, "draw"), d1, d2, seed=1)
    assert data["sweeps"] == [0, 0]
    assert (data["d1_won"], data["d2_won"]) == (0, 0)
