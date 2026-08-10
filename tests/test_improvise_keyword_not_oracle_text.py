"""oracle-ratchet: improvise must be detected via Keyword.IMPROVISE, not oracle text.

Rule: every card property derivable from oracle text is parsed ONCE at load
time (engine/card_database.py via KEYWORD_MAP + oracle word-boundary scan) and
stored as a typed field on CardTemplate.  Decision-time code reads the field;
it never re-inspects the raw oracle string.

This test pins the mechanic: improvise lets a player tap artifacts to pay {1}
of generic mana each.  Detection of improvise membership belongs to
Keyword.IMPROVISE in template.keywords, populated by KEYWORD_MAP / oracle
word-boundary scan at DB load.  No runtime code may check
'improvise' in oracle_text.

Class size: every improvise card in Modern (Reverse Engineer, Metallic Rebuke,
Aethersphere Harvester, Skysovereign, Consul Flagship, …).  The mechanic is
the keyword, not the card name.

Subsystem: engine/card_database.py (population via KEYWORD_MAP),
engine/cast_manager.py (consumer — must read Keyword.IMPROVISE in keywords),
engine/mana_payment.py (consumer — must read Keyword.IMPROVISE in keywords),
ai/effective_cmc.py (consumer — must read Keyword.IMPROVISE in keywords).
"""
from __future__ import annotations

import pytest

from engine.cards import Keyword


# ──────────────────────────────────────────────────────────────────────────────
# Keyword enum — entry must exist
# ──────────────────────────────────────────────────────────────────────────────

def test_keyword_improvise_enum_entry_exists():
    """Keyword.IMPROVISE must be a member of the Keyword enum.

    Mechanism: IMPROVISE = 'improvise' must be declared in engine/cards.py so
    KEYWORD_MAP can map the MTGJSON keyword string → enum member at load time.
    """
    assert hasattr(Keyword, "IMPROVISE"), (
        "Keyword enum must have an IMPROVISE member. "
        "Add IMPROVISE = 'improvise' to the Keyword enum in engine/cards.py."
    )
    assert Keyword.IMPROVISE.value == "improvise", (
        "Keyword.IMPROVISE must have value 'improvise'."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Field population — load-time
# ──────────────────────────────────────────────────────────────────────────────

def test_improvise_card_has_keyword_improvise_in_keywords(card_db):
    """An improvise card must have Keyword.IMPROVISE in template.keywords.

    Mechanism: KEYWORD_MAP maps 'Improvise' → Keyword.IMPROVISE at DB load;
    the oracle word-boundary scan ensures cards listed with only oracle text
    (not the MTGJSON keywords field) are also detected.
    """
    # Reverse Engineer is a canonical Modern improvise card.
    tmpl = card_db.get_card("Reverse Engineer")
    if tmpl is None:
        pytest.skip("Reverse Engineer not in DB")
    assert Keyword.IMPROVISE in (tmpl.keywords or set()), (
        "Reverse Engineer must carry Keyword.IMPROVISE in template.keywords. "
        "Add 'Improvise': Keyword.IMPROVISE to KEYWORD_MAP in card_database.py."
    )


def test_metallic_rebuke_has_keyword_improvise(card_db):
    """Metallic Rebuke must have Keyword.IMPROVISE in template.keywords.

    Mechanism: same KEYWORD_MAP / oracle scan path.  Second improvise card
    test for robustness.
    """
    tmpl = card_db.get_card("Metallic Rebuke")
    if tmpl is None:
        pytest.skip("Metallic Rebuke not in DB")
    assert Keyword.IMPROVISE in (tmpl.keywords or set()), (
        "Metallic Rebuke must carry Keyword.IMPROVISE in template.keywords."
    )


def test_non_improvise_card_does_not_have_keyword_improvise(card_db):
    """A non-improvise card must NOT have Keyword.IMPROVISE in keywords.

    Mechanism: the KEYWORD_MAP word-boundary pattern must not false-positive.
    """
    tmpl = card_db.get_card("Lightning Bolt")
    if tmpl is None:
        pytest.skip("Lightning Bolt not in DB")
    assert Keyword.IMPROVISE not in (tmpl.keywords or set()), (
        "Lightning Bolt must NOT carry Keyword.IMPROVISE in template.keywords."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Affinity cards must NOT be mis-tagged as improvise
# ──────────────────────────────────────────────────────────────────────────────

def test_affinity_card_does_not_get_improvise_tag(card_db):
    """An affinity-for-artifacts card must NOT get Keyword.IMPROVISE.

    Mechanism: affinity and improvise are distinct keywords; the word-boundary
    pattern r'(?:^|\\n)improvise(?:\\s|$|,|\\n)' must not match oracle text
    that only mentions 'affinity'.
    """
    tmpl = card_db.get_card("Thoughtcast")
    if tmpl is None:
        pytest.skip("Thoughtcast not in DB")
    assert Keyword.IMPROVISE not in (tmpl.keywords or set()), (
        "Thoughtcast (affinity, not improvise) must NOT have Keyword.IMPROVISE. "
        "The word-boundary pattern must distinguish 'affinity' from 'improvise'."
    )
