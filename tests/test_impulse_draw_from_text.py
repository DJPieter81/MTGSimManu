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


# ── The permission to play (CR 305.1, 601.2) ───────────────────────────

def test_you_may_play_those_cards_is_a_permission_over_the_exiled_cards(card_db):
    """"You may play those cards this turn": a PERMIT to the controller
    (the actor), over the cards the exile before it produced (the linker's
    RESULT), for the printed duration. The "may" is the grant: the spec is
    not an optional effect."""
    from engine.effect_model import DurationKind, ModKind, SelectorKind
    from engine.effect_spec import RefKind, Verb
    specs = _specs(card_db, "Glimpse the Impossible")
    exile = next(s for s in specs if s.verb is Verb.EXILE)
    permit = next(s for s in specs if s.verb is Verb.CONTINUOUS)
    assert permit.payload.kind is ModKind.PERMIT
    assert permit.payload.action == "play"
    assert permit.actor.kind is SelectorKind.PLAYER
    assert permit.ref.kind is RefKind.RESULT and permit.ref.index == exile.seq
    assert permit.duration.kind is DurationKind.THIS_TURN
    assert not permit.optional


def test_a_free_cast_and_a_flash_permission_are_no_play_permission(card_db):
    """"You may cast it without paying its mana cost" stays a free cast,
    optional; "you may cast spells as though they had flash" never reads as
    a permission to play named objects."""
    from engine.effect_model import ModKind
    from engine.effect_spec import Verb
    free = [s for s in _specs(card_db, "Hidetsugu and Kairi")
            if s.verb is Verb.CAST_FREE]
    assert free and free[0].optional
    t = card_db.get_card("Vedalken Orrery")
    specs = [s for h in t.effects.walk(include_sub=False) for s in h.specs]
    assert not any(s.verb is Verb.CONTINUOUS
                   and s.payload.kind is ModKind.PERMIT
                   and s.payload.action in ("play", "cast") for s in specs)
