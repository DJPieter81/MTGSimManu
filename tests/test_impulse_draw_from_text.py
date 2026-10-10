"""Impulse draw is read from the card's text (CR 401.5, 406, 601.2, 305.1).

"Exile the top N cards of your library. Until the end of your next turn,
you may play those cards." -- the exiled cards go to exile, not to hand,
and may be played from there until the permission expires. The effect
grammar types each part; the engine performs them through its owners. A
classifier tag neither adds nor removes the effect.

This file grows with the unit's steps; the first is the grammar's typed
library position.
"""
from __future__ import annotations

import pytest


def _specs(card_db, name):
    from engine.effect_spec import HostKind, iter_specs
    t = card_db.get_card(name)
    return [s for h in t.effects.walk(include_sub=False)
            if h.kind in (HostKind.SPELL, HostKind.TRIGGERED,
                          HostKind.ACTIVATED)
            for s in iter_specs(h.specs)]


# ── The library position (CR 401.5) ────────────────────────────────────

@pytest.mark.parametrize("name,n", [("Reckless Impulse", 2),
                                    ("Wrenn's Resolve", 2),
                                    ("Glimpse the Impossible", 3)])
def test_the_top_n_cards_of_your_library_is_a_typed_exile_object(card_db, name, n):
    """The object of "exile the top N cards of your library" is the
    controller's own library, from the top, N cards."""
    from engine.effect_spec import AmountKind, Verb
    exile = next(s for s in _specs(card_db, name) if s.verb is Verb.EXILE)
    f = exile.filter
    assert (f.zone, f.owner, f.position) == ("library", "you", "top")
    assert exile.amount.kind is AmountKind.LITERAL and exile.amount.n == n


def test_the_top_x_cards_is_counted_by_x(card_db):
    from engine.effect_spec import AmountKind, Verb
    exile = next(s for s in _specs(card_db, "Culmination of Studies")
                 if s.verb is Verb.EXILE)
    assert exile.filter.position == "top"
    assert exile.amount.kind is AmountKind.X


def test_a_library_position_another_verb_reads_stays_refused(card_db):
    """Staged by verb: "look at the top N cards of your library" keeps the
    participant leaf's refusal until its executor lands."""
    from engine.effect_spec import Verb
    looks = [s for s in _specs(card_db, "Consult the Star Charts")
             if s.verb is Verb.UNMODELLED
             and s.payload.detail == "participant.library_position"]
    assert looks


def test_another_players_library_position_stays_refused():
    from engine.effect_grammar.sub import participant
    for text in ("exile the top card of target player's library",
                 "exile the top two cards of each player's library",
                 "exile the bottom card of your library"):
        r = participant.parse_participant(text, (len("exile "), len(text)),
                                          lemma="exile")
        assert r.value is None, text
