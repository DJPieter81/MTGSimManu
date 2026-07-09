"""Central interaction-class registry — one membership answer for all.

Structural finding #2 (docs/proposals/2026-07-09_structural_findings.md):
every scorer kept a private allowlist of "cards that count as
interaction", and each new mechanic class (most recently the
cast-lock class) had to be added consumer-by-consumer, each miss a
shipped bug. The registry gives one oracle/tag-derived predicate that
every consumer imports; adding a class is one edit.

Contract pinned here: the held-interaction class covers counterspells,
instant-speed removal, and turn-scoped cast-locks; sorcery-speed cards
and non-interaction instants are excluded; and the mana-holdback site
actually consumes the registry (so the next class added there is
automatically priced).
"""
from __future__ import annotations

import inspect


def _tmpl(card_db, name):
    t = card_db.get_card(name)
    assert t is not None, f"missing card: {name}"
    return t


def test_held_interaction_covers_counter_removal_and_cast_lock(card_db):
    from ai.card_classes import is_held_interaction
    assert is_held_interaction(_tmpl(card_db, "Counterspell"))
    assert is_held_interaction(_tmpl(card_db, "Fatal Push"))
    assert is_held_interaction(_tmpl(card_db, "Silence"))


def test_non_interaction_and_sorcery_speed_excluded(card_db):
    from ai.card_classes import is_held_interaction
    # A sorcery-speed discard spell is interaction but can't be held
    # for the opponent's turn.
    assert not is_held_interaction(_tmpl(card_db, "Thoughtseize"))
    # An instant with no interaction class (pure draw) doesn't count.
    assert not is_held_interaction(_tmpl(card_db, "Opt"))
    # A creature never counts.
    assert not is_held_interaction(_tmpl(card_db, "Memnite"))


def test_holdback_site_consumes_the_registry():
    """The mana-holdback pricer must import its membership from the
    registry rather than a private tag allowlist — pinned by source
    inspection so a future inline list re-introduction fails here."""
    from ai.ev_player import EVPlayer
    src = inspect.getsource(EVPlayer._holdback_penalty)
    assert "is_held_interaction" in src, (
        "_holdback_penalty no longer consumes ai.card_classes."
        "is_held_interaction — held-interaction membership has been "
        "re-inlined and will drift from the registry"
    )
