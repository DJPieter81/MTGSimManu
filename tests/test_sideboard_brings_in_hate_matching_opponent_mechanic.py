"""Sideboarding must board in hate that answers the opponent's mechanic.

`sb_value` scores SB cards by oracle-clause families (removal,
counterspells, protection, graveyard hate, artifact hate). There was
NO clause family for spell-chain denial, so cast-rate hate — "can't
cast more than one spell each turn", "costs {1} more for each other
spell", "counter target triggered/activated ability" — scored ~0
against a storm-class opponent and stayed in the board all match
(the storm-overshoot diagnostic's second defender lever).

Mechanic-level: value scales with the opponent's measured chain
density (rituals / cost reducers / storm-keyword spells), the same
shape as graveyard hate scaling with graveyard reliance. No card
names in the scorer; this test asserts by oracle predicate.
"""
from __future__ import annotations

import re

from ai.sideboard_solver import plan_sideboard
from decks.modern_meta import MODERN_DECKS

_HATE_PATTERNS = (
    r"can'?t cast more than one spell",
    r"costs? \{1\} more to cast for each other spell",
    r"counter target (?:triggered|activated)",
)


def _is_chain_hate(card_db, name: str) -> bool:
    t = card_db.get_card(name)
    oracle = (t.oracle_text or "").lower() if t else ""
    return any(re.search(p, oracle) for p in _HATE_PATTERNS)


def _plan(card_db, me: str, opp: str):
    my = MODERN_DECKS[me]
    theirs = MODERN_DECKS[opp]
    new_main, new_sb, log = plan_sideboard(
        my_main=dict(my["mainboard"]),
        my_sb=dict(my.get("sideboard", {})),
        opp_deck_name=opp,
        card_db=card_db,
        opp_mainboard=dict(theirs["mainboard"]),
        my_deck_name=me,
    )
    return new_main, new_sb


def test_cast_rate_hate_boarded_in_versus_spell_chain_deck(card_db):
    """A control deck whose board holds cast-rate denial must bring at
    least one such card in against a chain-combo opponent."""
    sb_hate = [n for n in MODERN_DECKS["Azorius Control"]["sideboard"]
               if _is_chain_hate(card_db, n)]
    assert sb_hate, "test premise: the SB carries cast-rate hate"

    new_main, _ = _plan(card_db, "Azorius Control", "Ruby Storm")
    boarded_in = [n for n in new_main if _is_chain_hate(card_db, n)]
    assert boarded_in, (
        "no cast-rate denial was boarded in vs a spell-chain deck — "
        "the spell-chain-hate clause family is missing from sb_value"
    )


def test_cast_rate_hate_stays_out_versus_creature_deck(card_db):
    """Negative pin: the same hate must NOT be boarded in against a
    creature deck with zero chain density."""
    new_main, _ = _plan(card_db, "Azorius Control", "Boros Energy")
    boarded_in = [n for n in new_main
                  if _is_chain_hate(card_db, n)
                  and n not in MODERN_DECKS["Azorius Control"]["mainboard"]]
    assert not boarded_in, (
        f"cast-rate hate {boarded_in} boarded in vs a zero-chain-density "
        "creature deck — the clause must scale with measured chain density"
    )
