"""Failing-test-first contract for the oracle-tag classifier (W0-A).

Per CLAUDE.md `§Hard prohibitions`: "No fix without a failing test in
the same diff.  Test goes red first, then the fix lands and turns it
green.  Both in the same commit."

This file pins the *mechanic* contract — every assertion names a rule
("a card whose oracle text exiles cards and lets you play them later
is an IMPULSE_DRAW card"), never a card.  Card names appear only as
fixture data, never in the test identifier or the assertion message.

The classifier itself is a pure-data loader (`ai/oracle_classifier.py`)
that reads tags from a committed JSON file produced offline by
`tools/build_oracle_classifier_cache.py`.  The engine and AI compose
the resulting `Tag` enum with the existing predicate primitives —
they NEVER call the LLM at runtime.
"""
from __future__ import annotations

import pytest


# All imports happen inside test bodies so collection still works
# during the RED phase (module-not-yet-existing).  Once `oracle_classifier`
# lands the import succeeds and every assertion is exercised.


def test_load_oracle_tags_returns_dict():
    """The classifier loader returns a dict keyed by card name with a
    frozenset of `Tag` values.  Empty cache (no smoke build yet) is
    legal — the dict simply has no entries.  Loading must never raise
    on a missing optional field."""
    from ai.oracle_classifier import Tag, load_oracle_tags

    tags_by_name = load_oracle_tags()
    assert isinstance(tags_by_name, dict)
    # If the cache has any entries at all, every value must be a
    # frozenset of `Tag`.  This pins the round-trip contract: bad data
    # in the JSON fails fast, not at first use.
    for name, tags in tags_by_name.items():
        assert isinstance(name, str)
        assert isinstance(tags, frozenset)
        for t in tags:
            assert isinstance(t, Tag)


def test_impulse_draw_card_has_impulse_draw_tag():
    """A card whose oracle text exiles cards from the top of the library
    and grants the controller permission to play them within a bounded
    window is an IMPULSE_DRAW card.  This is the structural mechanic
    behind 'temporary card-advantage that converts mana into action';
    the classifier MUST identify it from oracle text alone."""
    from ai.oracle_classifier import Tag, has_tag

    assert has_tag("Reckless Impulse", Tag.IMPULSE_DRAW), (
        "An exile-top-and-may-play-them card must be tagged IMPULSE_DRAW"
    )


def test_drawn_card_has_no_impulse_draw_tag():
    """A pure card-draw or pure counterspell does NOT have IMPULSE_DRAW.
    This guards against the most common false-positive: the
    classifier overreaching from 'card advantage' to 'impulse draw'
    when the cards are simply drawn into hand."""
    from ai.oracle_classifier import Tag, has_tag

    assert not has_tag("Counterspell", Tag.IMPULSE_DRAW), (
        "A spell that puts cards into hand (or counters) must NOT be "
        "tagged IMPULSE_DRAW — that tag is reserved for "
        "exile-and-may-play mechanics."
    )


def test_force_discard_card_has_tag():
    """A spell that forces an opponent to reveal their hand and
    discard a non-land card (or that strips a card from hand) is
    FORCED_DISCARD.  The mechanic is 'remove a known card from
    opponent's hand'; the classifier identifies this from oracle text
    independent of the cost structure."""
    from ai.oracle_classifier import Tag, has_tag

    assert has_tag("Thoughtseize", Tag.FORCED_DISCARD), (
        "An opponent-reveals-and-discard spell must be tagged FORCED_DISCARD"
    )


def test_on_draw_damage_card_has_tag():
    """A permanent or triggered ability that deals damage whenever an
    opponent draws a card is ON_DRAW_DAMAGE.  This drives the
    'cantrips become liabilities' projection for downstream EV math;
    it is structurally distinct from on-cast damage (Eidolon-style)
    and must be tagged separately."""
    from ai.oracle_classifier import Tag, has_tag

    assert has_tag("Orcish Bowmasters", Tag.ON_DRAW_DAMAGE), (
        "A 'whenever an opponent draws' damage trigger must be tagged "
        "ON_DRAW_DAMAGE"
    )


def test_tags_for_unknown_card_returns_empty_frozenset():
    """The classifier does NOT raise on unknown card names — it
    returns an empty frozenset so the caller's predicate logic can
    treat 'unknown' as 'no tags' without an isinstance check."""
    from ai.oracle_classifier import tags_for

    result = tags_for("This Card Definitely Does Not Exist 12345")
    assert isinstance(result, frozenset)
    assert len(result) == 0


def test_tag_enum_members_are_stable():
    """The Tag enum is the public contract for every downstream
    consumer (engine, AI, gameplan JSON).  Adding a new member is
    additive and safe; renaming or removing is a breaking change
    that requires the cache to be rebuilt.  Pin the audit-critical
    members so a rename in this PR doesn't silently break callers
    on a different branch."""
    from ai.oracle_classifier import Tag

    required = {
        "IMPULSE_DRAW",
        "FORCED_DISCARD",
        "ON_DRAW_DAMAGE",
        "ON_CAST_DAMAGE",
        "CHANNEL_ABILITY",
        "DELVE",
        "EVOKE",
        "KICKER",
        "FLASHBACK",
        "SORCERY_SPEED_LOCKOUT",
        "ETB_SURVEIL_N",
        "ETB_SCRY_N",
        "ETB_ORACLE_TRIGGER",
        "STORM_PAYOFF",
        "CHAIN_FUEL",
        "TARGET_CREATURE_OR_PW",
        "TARGET_ANY_DAMAGE",
        "PLANESWALKER_LOYALTY_PLUS1_USEFUL",
        "PLANESWALKER_LOYALTY_X_USEFUL",
        "SELF_DAMAGE_ON_CAST",
    }
    actual = {m.name for m in Tag}
    missing = required - actual
    assert not missing, f"Tag enum is missing required members: {missing}"
