"""The shared test helper `run_seeded_game` must make a game a function of
its seed: the engine draws from `runner.rng`, not the global `random`, so
the helper seeds that RNG (as `run_meta._run_game` does). Without it every
test built on the helper ran an unseeded game, and a comparison between two
seeds could fail by chance."""
from tests.conftest import run_seeded_game


def _key(r):
    return (r.turns, r.winner, r.winner_life, r.deck1_damage_dealt,
            tuple(r.mulligan_count), r.win_condition)


def test_the_same_seed_replays_the_same_game(game_runner):
    a = run_seeded_game(game_runner, "Domain Zoo", "Dimir Midrange", seed=100)
    b = run_seeded_game(game_runner, "Domain Zoo", "Dimir Midrange", seed=100)
    assert _key(a) == _key(b)
