"""Process-wide shared CardDatabase for the test suite.

Loading the 21k-card database costs ~7s. Without sharing, every test module
that constructs its own CardDatabase reloads it, pushing the full suite to
~80 min. This singleton is loaded once per pytest process; the session-scoped
``card_db`` fixture in conftest.py and any direct-construction call sites both
route through it, so the suite loads the database exactly once.

The database is treated as read-only by tests, so sharing one instance across
modules is safe.
"""

from engine.card_database import CardDatabase

_DB = None


def shared_card_database():
    global _DB
    if _DB is None:
        _DB = CardDatabase()
    return _DB
