"""Classifier-coverage gate — the repo must never ship a deck-pool
card whose oracle matches a gating tag family without a fresh cache
verdict.

Failing-first anchor: at authoring time, Wrenn's Resolve (4× in a
registered deck, textbook impulse shape) was absent from the cache —
the engine's impulse split silently didn't apply to it and the
audited Bowmasters bug persisted for exactly that card.
"""
from __future__ import annotations

import hashlib

from tools.check_classifier_coverage import (
    CANDIDATE_FAMILIES,
    find_coverage_misses,
)


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


IMPULSE_ORACLE = ("Exile the top two cards of your library. Until the "
                  "end of your next turn, you may play those cards.")


# ─── synthetic behavior ──────────────────────────────────────────────

def test_candidate_without_cache_entry_is_a_miss():
    pool = {"Some Impulse Card": IMPULSE_ORACLE}
    cache = {"cards": {}}
    misses = find_coverage_misses(pool, cache)
    assert misses == [("IMPULSE_DRAW", "Some Impulse Card", "missing")]


def test_classified_negative_entry_satisfies_the_gate():
    """The gate demands a VERDICT, not the tag — an entry with empty
    tags (classified-negative) passes."""
    pool = {"Looks Like Impulse": IMPULSE_ORACLE}
    cache = {"cards": {"Looks Like Impulse": {
        "tags": [], "oracle_text_sha256": _sha(IMPULSE_ORACLE)}}}
    assert find_coverage_misses(pool, cache) == []


def test_stale_sha_fails_hard():
    pool = {"Drifted Card": IMPULSE_ORACLE}
    cache = {"cards": {"Drifted Card": {
        "tags": ["IMPULSE_DRAW"], "oracle_text_sha256": _sha("old text")}}}
    assert find_coverage_misses(pool, cache) == [
        ("IMPULSE_DRAW", "Drifted Card", "stale_sha")]


def test_non_candidate_cards_are_ignored():
    pool = {"Vanilla Bear": "Creature — Bear. 2/2."}
    cache = {"cards": {}}
    assert find_coverage_misses(pool, cache) == []


def test_impulse_candidate_net_catches_the_known_family():
    rx = CANDIDATE_FAMILIES["IMPULSE_DRAW"]
    assert rx.search(IMPULSE_ORACLE)
    assert rx.search("Exile the top card of your library. You may play "
                     "it this turn.")
    assert not rx.search("Draw two cards.")


# ─── repo state ──────────────────────────────────────────────────────

def test_registered_deck_pool_has_full_impulse_coverage():
    """The real gate over the real repo: every impulse-shaped card in
    every registered deck has a fresh classifier verdict."""
    misses = find_coverage_misses()
    assert misses == [], (
        "tag-gated engine behavior silently disabled for: " + repr(misses))
