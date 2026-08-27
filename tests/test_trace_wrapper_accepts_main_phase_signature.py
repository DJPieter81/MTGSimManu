"""The --trace wrapper must accept the engine's decide_main_phase call.

Regression pin: `run_meta.run_trace_game` monkeypatches
`EVPlayer.decide_main_phase` with a tracing wrapper. The engine calls
that method with keyword arguments (`excluded_cards`,
`excluded_activations` — engine/game_runner.py `_execute_main_phase`),
so any parameter added to `decide_main_phase` must be mirrored by the
wrapper or every `--trace` run dies with a TypeError (observed:
`traced_main() got an unexpected keyword argument
'excluded_activations'`, docs/diagnostics/
2026-08-27_reanimator_pair_root_cause.md).

No full game needed: the runner is stubbed with one that invokes the
(patched) method exactly the way the engine does.
"""
from __future__ import annotations

import random


def test_trace_wrapper_accepts_engine_main_phase_call_signature(monkeypatch):
    import run_meta
    from ai.ev_player import EVPlayer
    from engine.game_state import GameState, Phase

    calls = []

    class _Result:
        winner_deck = "Ruby Storm"
        turns = 1
        win_condition = "stub"
        winner = 0
        winner_life = 20
        loser_life = 0

    class _StubRunner:
        rng = random.Random(0)

        def run_game(self, *args, **kwargs):
            game = GameState(rng=random.Random(0))
            game.players[0].deck_name = "Ruby Storm"
            game.players[1].deck_name = "Dimir Midrange"
            game.active_player = 0
            game.current_phase = Phase.MAIN1
            ai = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
            # The exact call shape the engine uses
            # (engine/game_runner.py::_execute_main_phase).
            ai.decide_main_phase(
                game, excluded_cards=set(), excluded_activations=set())
            calls.append(True)
            return _Result()

    monkeypatch.setattr(run_meta, "_get_runner", lambda: _StubRunner())

    out = run_meta.run_trace_game("Ruby Storm", "Dimir Midrange", seed=1)

    assert calls, "stub runner was never invoked"
    assert "Ruby Storm" in out
