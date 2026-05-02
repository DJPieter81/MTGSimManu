"""Failing-test-first: parallel worker errors must surface to caller.

`run_meta._worker_matchup` is dispatched by `mp.Pool` from
`run_meta.run_field` and `run_meta.run_meta_matrix(parallel=True)`.
The pre-fix shape `(d1, d2, pct)` plus the bare `except Exception:
pass` inside the per-game loop made a worker that crashed every
game indistinguishable from a worker whose deck cleanly lost every
game — both report `pct=0`. Combined with the worker-side
`sys.stderr = /dev/null` redirect in `_init_worker`, a parallel
`--matrix` or `--field` run could have every game crash and emit
zero diagnostic output.

The rule this test names: a worker that catches per-game
exceptions must surface an error count to the parent process, so
silent crashes are detectable.
"""
from __future__ import annotations

import run_meta


def test_worker_matchup_reports_error_count_when_games_raise(monkeypatch):
    """When every per-game call raises, `_worker_matchup` must return
    an error indicator alongside the win pct so the caller can
    distinguish a crashed worker from a clean 0% sweep.

    Pre-fix:  returns (d1, d2, 0)        — silent
    Post-fix: returns (d1, d2, 0, errors) where errors is truthy.
    """
    n_games = 4

    def _always_raise(*args, **kwargs):
        raise RuntimeError("synthetic worker failure")

    monkeypatch.setattr(run_meta, "_worker_runner", object())
    monkeypatch.setattr(run_meta, "_run_pair", _always_raise)

    args = ("Boros Energy", "Domain Zoo", n_games, 50000, False)
    result = run_meta._worker_matchup(args)

    assert len(result) >= 4, (
        f"_worker_matchup must surface error info "
        f"(got {len(result)}-tuple={result!r}); silent worker "
        "failures hide crashes during parallel sims."
    )
    d1, d2, pct, errors = result[0], result[1], result[2], result[3]
    assert d1 == "Boros Energy"
    assert d2 == "Domain Zoo"
    assert pct == 0, "no wins recorded when every game raised"
    assert errors, (
        "errors field must be truthy when every game raised — "
        "this is the dispatch contract that distinguishes a crashing "
        "worker from a deck that cleanly lost every game."
    )


def test_worker_matchup_reports_no_errors_on_clean_sweep(monkeypatch):
    """Regression: a deck that cleanly loses every game (no
    exceptions raised) must report an empty errors field. This is
    the discriminator that the silent-failure bug erased.
    """
    n_games = 3

    class _FakeResult:
        winner_deck = "Domain Zoo"

    def _always_d2_wins(*args, **kwargs):
        return _FakeResult()

    monkeypatch.setattr(run_meta, "_worker_runner", object())
    monkeypatch.setattr(run_meta, "_run_pair", _always_d2_wins)

    args = ("Boros Energy", "Domain Zoo", n_games, 50000, False)
    result = run_meta._worker_matchup(args)

    assert len(result) >= 4, "tuple must include errors field"
    d1, d2, pct, errors = result[0], result[1], result[2], result[3]
    assert pct == 0, "Boros lost every game cleanly"
    assert not errors, (
        "errors must be falsy on a clean 0-3 — this is the "
        "discriminator vs. a crashing worker."
    )
