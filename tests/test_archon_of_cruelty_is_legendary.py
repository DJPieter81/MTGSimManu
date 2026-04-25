"""GV2-7: Archon of Cruelty must be flagged Legendary in the card database.

Real Magic: Archon of Cruelty (Modern Horizons 2) is a
"Legendary Creature — Archon". Some ModernAtomic dumps carry a stale
entry for this card whose `supertypes` array is empty and whose `type`
string lacks the "Legendary" prefix. If the engine trusts that data
verbatim, Goryo's Vengeance — which strictly requires a legendary
target (CR 608.2b + oracle text) — can never reanimate Archon, which
makes it a dead card in the Goryo's decklist.

`engine/card_database.py::_build_template` applies a two-stage
correction:
  1. Re-derive supertypes from the type-line string when the `supertypes`
     array is empty (defensive for the common "one field is fresh" case).
  2. Apply direct name-keyed corrections for cards whose MTGJSON entry
     is corrupt in BOTH locations (Archon of Cruelty today).

This test locks both cards in: Archon flagged Legendary, Griselbrand
unchanged (regression guard), and non-legendary cards untouched.
"""
from __future__ import annotations

import pytest

from engine.card_database import CardDatabase
from engine.cards import Supertype


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def test_archon_of_cruelty_is_legendary(card_db):
    """GV2-7: Goryo's Vengeance needs legendary targets. Archon's MTGJSON
    entry in the shipped ModernAtomic dump is missing the Legendary
    supertype in both `supertypes` and `type`. The DB correction layer
    must restore it so Goryo's can reanimate Archon."""
    archon = card_db.get_card("Archon of Cruelty")
    assert archon is not None, "Archon of Cruelty missing from DB"
    assert Supertype.LEGENDARY in archon.supertypes, (
        "Archon of Cruelty must be flagged Legendary so Goryo's Vengeance "
        "can target it. supertypes=%r" % (archon.supertypes,)
    )


def test_griselbrand_still_legendary(card_db):
    """Regression: the supertype correction layer must not disturb cards
    whose MTGJSON data is already correct."""
    gris = card_db.get_card("Griselbrand")
    assert gris is not None
    assert Supertype.LEGENDARY in gris.supertypes


def test_basic_land_still_basic(card_db):
    """Regression: the type-line supertype fallback must also recognise
    non-legendary supertypes correctly (Basic in this case)."""
    mountain = card_db.get_card("Mountain")
    assert mountain is not None
    assert Supertype.BASIC in mountain.supertypes


def test_nonlegendary_creature_not_flagged_legendary(card_db):
    """Regression: the fallback must not over-match. Memnite is a plain
    "Artifact Creature — Construct" with no supertypes — must stay that
    way."""
    mem = card_db.get_card("Memnite")
    assert mem is not None
    assert Supertype.LEGENDARY not in mem.supertypes
