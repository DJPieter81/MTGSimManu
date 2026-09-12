"""A process that already holds a loaded CardDatabase never builds another.

Profiling a "17 s first game" (Ruby Storm vs Domain Zoo @ 50000, 2026-09-06)
showed no game time at all: `EVPlayer.__init__ -> gameplan.create_goal_engine
-> _lookup_deck_and_db -> sideboard_manager._get_card_db -> CardDatabase()`
loaded the full 22.5k-card database a SECOND time although `GameRunner`
already held one. Every parallel worker paid it twice (2 x 16 s of start-up
and double the memory — the pressure that kills the pool on a 2-core runner).

Rule: `CardDatabase.shared()` is the one process-wide instance. A runner
registers the database it was built with; every lazy consumer resolves
through the shared accessor; constructing a game on an existing runner
builds zero additional databases.
"""
from __future__ import annotations

from tests._card_db_cache import shared_card_database


def test_a_game_on_an_existing_runner_does_not_build_a_second_database(
        monkeypatch):
    from engine import card_database as cdb
    from engine.game_runner import GameRunner
    from run_meta import _run_game
    import engine.sideboard_manager as sbm

    db = shared_card_database()
    runner = GameRunner(db)

    builds = []
    real_init = cdb.CardDatabase.__init__

    def _counting_init(self, *a, **kw):
        builds.append(1)
        return real_init(self, *a, **kw)

    monkeypatch.setattr(cdb.CardDatabase, "__init__", _counting_init)

    _run_game(runner, "Amulet Titan", "Jeskai Blink", 50000)

    assert builds == [], (
        f"{len(builds)} extra CardDatabase build(s) during one game on a "
        "runner that already holds one — a lazy consumer is loading its "
        "own copy instead of resolving CardDatabase.shared()")
    # One PARSED POOL per process. The suite's conftest clones the cached
    # database into fresh shells (`self.__dict__.update`), so object
    # identity can differ between shells; the pool they carry cannot.
    assert sbm._get_card_db().cards is db.cards
    assert cdb.CardDatabase.shared().cards is db.cards


def test_shared_accessor_registers_the_first_loaded_instance():
    from engine.card_database import CardDatabase

    db = shared_card_database()
    assert CardDatabase.shared().cards is db.cards
