"""The bo3_trace reasoning-inlined wrapper must accept the engine's
decide_main_phase call and not read a deleted profile attribute.

Regression pin (mirrors tests/test_trace_wrapper_accepts_main_phase_signature.py
for run_meta): `tools.bo3_trace._make_traced_main` wraps
`EVPlayer.decide_main_phase`, which the engine calls with the
`excluded_activations` keyword (engine/game_runner.py::_execute_main_phase).
The wrapper's signature must mirror it (or every traced game dies with a
TypeError), and its PASS branch must not read `profile.pass_threshold` (a
deleted attribute → AttributeError). Two panels hit both bugs on 2026-09-15.
"""
from __future__ import annotations

import random


def test_bo3_trace_wrapper_accepts_engine_call_and_survives_pass(monkeypatch):
    from tools.bo3_trace import _make_traced_main
    from ai.ev_player import EVPlayer
    from engine.game_state import GameState, Phase

    seen = {}

    def _stub_orig(self, game, excluded_cards=None, excluded_activations=None):
        seen["excluded_activations"] = excluded_activations
        return None  # PASS branch — exercises the pass_threshold read

    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Dimir Midrange"
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    ai = EVPlayer(player_idx=0, deck_name="Ruby Storm", rng=random.Random(0))

    wrapped = _make_traced_main(_stub_orig)
    # The exact call shape the engine uses.
    result = wrapped(ai, game, excluded_cards=set(), excluded_activations=set())

    assert result is None
    assert seen["excluded_activations"] == set(), "kwarg forwarded to orig"
    assert any("PASS" in line for line in game.log), "PASS line logged"
