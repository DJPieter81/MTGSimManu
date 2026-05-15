"""Failing-first tests for tutor over-tagging audit.

The F-1 tutor predicate excluded basic-land searches via an explicit
phrase list, but the list missed:

  - "up to X basic land cards" (Boundless Realms, Harvest Season,
    Prismatic Undercurrents, Verdant Mastery, Point the Way) — the
    explicit list only covered "up to two" and "up to three".
  - "snow land card" (Into the North, Spirit of the Aldergard) —
    snow lands are basic-typed; these are ramp/fixing, not tutor.

The fix consolidates the basic-land exclusion to a single regex
that matches any "search your library for [quantifier] (basic|snow)
land" phrasing.

Negative anchor — Search for Glory MIXES "snow permanent",
"legendary", and "saga" targets. Since it can tutor non-land cards,
it must KEEP the tutor tag.
"""
from engine.card_database import CardDatabase


def _tags(name: str) -> set:
    db = CardDatabase()
    t = db.cards.get(name)
    if t is None:
        return None
    return set(t.tags)


def _has_tutor(name: str) -> bool:
    tags = _tags(name)
    return tags is not None and "tutor" in tags


# ──────────────────────────────────────────────────────────────────
# Basic-land searches must NOT be tutor
# ──────────────────────────────────────────────────────────────────

def test_boundless_realms_basic_ramp_not_tutor():
    """search your library for up to X basic land cards — ramp."""
    db = CardDatabase()
    if "Boundless Realms" in db.cards:
        assert not _has_tutor("Boundless Realms"), (
            "Boundless Realms is basic-land ramp, must not be tutor"
        )


def test_harvest_season_basic_ramp_not_tutor():
    db = CardDatabase()
    if "Harvest Season" in db.cards:
        assert not _has_tutor("Harvest Season")


def test_verdant_mastery_basic_ramp_not_tutor():
    db = CardDatabase()
    if "Verdant Mastery" in db.cards:
        assert not _has_tutor("Verdant Mastery")


def test_into_the_north_snow_land_is_not_tutor():
    """search your library for a snow land card — fixing, not tutor."""
    db = CardDatabase()
    if "Into the North" in db.cards:
        assert not _has_tutor("Into the North")


def test_spirit_of_the_aldergard_snow_land_is_not_tutor():
    db = CardDatabase()
    if "Spirit of the Aldergard" in db.cards:
        assert not _has_tutor("Spirit of the Aldergard")


# ──────────────────────────────────────────────────────────────────
# Mixed-target search — KEEPS tutor tag (can fetch non-land)
# ──────────────────────────────────────────────────────────────────

def test_search_for_glory_mixed_target_is_still_tutor():
    """Search for Glory can fetch snow permanent OR legendary OR
    saga — the non-land targets make it a real tutor."""
    db = CardDatabase()
    if "Search for Glory" in db.cards:
        assert _has_tutor("Search for Glory")


# ──────────────────────────────────────────────────────────────────
# Regression anchors — tutors that must stay tagged
# ──────────────────────────────────────────────────────────────────

def test_gifts_ungiven_still_tutor():
    assert _has_tutor("Gifts Ungiven")


def test_unmarked_grave_still_tutor():
    assert _has_tutor("Unmarked Grave")


def test_stoneforge_mystic_still_tutor():
    assert _has_tutor("Stoneforge Mystic")
