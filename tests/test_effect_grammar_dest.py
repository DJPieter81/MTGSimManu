"""Destination sub-grammar (design doc 2026-09-29, section 4 MOVE row and
the put/return disambiguation; section 3 L2 instead-of trailer; A9, A13,
A15, A18).

The leaf reads NORMALISED clause text (lowercased, self-references already
`~`). A destination prepositional phrase is typed into
`effect_spec.Destination` only when every token of the slot is consumed; an
unconsumed tail is UNMODELLED, never a guessed destination. Synthetic
phrases only -- no card names.
"""
from __future__ import annotations

import pytest

from engine.effect_spec import Amount, AmountKind, Destination, RefKind


def _dest():
    from engine.effect_grammar.sub import dest
    return dest


def _whole(text, **kw):
    return _dest().parse_destination(text, (0, len(text)), **kw)


# ── A15: the instead-of trailer is a destination override ──────────────

def test_instead_of_putting_it_into_a_zone_is_a_destination_override_of_the_named_action():
    """CR 701.5a: 'exile it instead of putting it into its owner's
    graveyard' changes WHERE the countered card goes; it is an override on
    the named action, never a sibling that replaces the action."""
    text = "exile it instead of putting it into its owner's graveyard"
    r = _dest().parse_instead_of(text, (0, len(text)))
    assert r.unmodelled is None
    assert r.value == Destination("exile", instead_of="graveyard")
    assert "dest_override" in r.flags
    assert r.span == (0, len(text))


@pytest.mark.parametrize("text,expected", [
    ("put it on top of its owner's library instead of into that player's graveyard",
     Destination("library", position="top", instead_of="graveyard")),
    ("put that card onto the battlefield instead of putting it into your hand",
     Destination("battlefield", instead_of="hand")),
    ("exile them instead of putting them anywhere else",
     Destination("exile", instead_of="anywhere")),
    ("exile that spell instead of putting it into your graveyard as it resolves",
     Destination("exile", instead_of="graveyard")),
    ("exile it with three time counters on it instead of putting it into its owner's graveyard",
     Destination("exile", instead_of="graveyard",
                 entry_counters=(("time", Amount(AmountKind.LITERAL, n=3)),))),
])
def test_every_instead_of_form_names_the_replaced_zone_and_the_new_destination(text, expected):
    r = _dest().parse_instead_of(text, (0, len(text)))
    assert r.value == expected, r
    assert "dest_override" in r.flags


def test_the_this_way_rider_before_an_instead_of_form_is_consumed_into_the_override():
    """'If <ref> is <verb>ed this way,' only scopes the override to the
    named action being performed (A15); it adds no condition of its own."""
    text = ("if that spell is countered this way, exile it instead of "
            "putting it into its owner's graveyard")
    r = _dest().parse_instead_of(text, (0, len(text)))
    assert r.value == Destination("exile", instead_of="graveyard")


def test_an_instead_of_phrase_without_a_move_action_is_unmodelled():
    text = "draw a card instead of putting it into your graveyard"
    r = _dest().parse_instead_of(text, (0, len(text)))
    assert r.value is None and r.unmodelled is not None


# ── A18: shuffling an object into a library ────────────────────────────

def test_shuffling_an_object_into_a_library_is_a_zone_move_not_a_library_shuffle():
    """'shuffle <object> into <library>' moves the object (MOVE with a
    shuffle position); 'shuffle your library' names no destination."""
    r = _whole("into its owner's library", lemma="shuffle")
    assert r.value == Destination("library", position="shuffle")
    r2 = _whole("into their library", lemma="shuffle")
    assert r2.value == Destination("library", position="shuffle")
    assert _dest().locate_destination("your library", (0, 12)) is None


def test_a_put_into_a_library_without_a_shuffle_or_position_names_no_position():
    assert _whole("into your library").value == Destination("library")


# ── A13: gapping needs a standalone destination PP ─────────────────────

@pytest.mark.parametrize("np_pp,object_text,expected", [
    ("the rest on the bottom of your library in any order", "the rest",
     Destination("library", position="bottom", order="any")),
    ("the rest on the bottom of your library in a random order", "the rest",
     Destination("library", position="bottom", order="random")),
    ("the other into your graveyard", "the other", Destination("graveyard")),
    ("the rest into your hand", "the rest", Destination("hand")),
])
def test_a_gapped_count_or_rest_noun_phrase_is_split_from_its_destination_phrase(
        np_pp, object_text, expected):
    """A gapped 'and <count|REST NP> <destination PP>' carries no verb; the
    standalone destination parser finds the PP after the NP."""
    d = _dest()
    span = d.locate_destination(np_pp, (0, len(np_pp)))
    assert span is not None
    assert np_pp[:span[0]].strip() == object_text
    assert d.parse_destination(np_pp, span).value == expected


# ── Battlefield entry modifiers (CR 110.2, 506.3, 701.28, 708) ─────────

@pytest.mark.parametrize("text,expected", [
    ("onto the battlefield", Destination("battlefield")),
    ("to the battlefield", Destination("battlefield")),
    ("onto the battlefield tapped", Destination("battlefield", tapped=True)),
    ("onto the battlefield tapped and attacking",
     Destination("battlefield", tapped=True, attacking=True)),
    ("to the battlefield transformed under its owner's control",
     Destination("battlefield", transformed=True, controller="owner")),
    ("to the battlefield tapped and transformed under its owner's control",
     Destination("battlefield", tapped=True, transformed=True, controller="owner")),
    ("onto the battlefield under your control",
     Destination("battlefield", controller="you")),
    ("to the battlefield under their owners' control",
     Destination("battlefield", controller="owner")),
    ("onto the battlefield face down", Destination("battlefield", face_down=True)),
    ("to the battlefield with a finality counter on it",
     Destination("battlefield", entry_counters=(
         ("finality", Amount(AmountKind.LITERAL, n=1)),))),
    ("to the battlefield under its owner's control with a +1/+1 counter on it",
     Destination("battlefield", controller="owner", entry_counters=(
         ("+1/+1", Amount(AmountKind.LITERAL, n=1)),))),
    ("onto the battlefield with two additional +1/+1 counters on it",
     Destination("battlefield", entry_counters=(
         ("+1/+1", Amount(AmountKind.LITERAL, n=2)),))),
    ("onto the battlefield with x +1/+1 counters on it",
     Destination("battlefield", entry_counters=(
         ("+1/+1", Amount(AmountKind.X, n=1)),))),
])
def test_battlefield_entry_modifiers_are_typed_destination_fields(text, expected):
    r = _whole(text)
    assert r.unmodelled is None, r
    assert r.value == expected


def test_attached_to_self_is_a_self_reference_and_attached_to_a_pronoun_is_left_to_linking():
    r = _whole("onto the battlefield attached to ~")
    assert r.value is not None and r.value.attached_to.kind is RefKind.SELF
    r2 = _whole("onto the battlefield attached to it")
    assert r2.value is None and r2.unmodelled is not None


# ── Hand, graveyard, library, exile ────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("into your hand", Destination("hand")),
    ("to its owner's hand", Destination("hand")),
    ("to their owners' hands", Destination("hand")),
    ("to hand", Destination("hand")),
    ("into your graveyard", Destination("graveyard")),
    ("into their owners' graveyards", Destination("graveyard")),
    ("on top of your library", Destination("library", position="top")),
    ("on top of your library in any order",
     Destination("library", position="top", order="any")),
    ("on the bottom of its owner's library", Destination("library", position="bottom")),
    ("into its owner's library third from the top",
     Destination("library", position="nth", nth=3)),
    ("into your library second from the top",
     Destination("library", position="nth", nth=2)),
    ("into exile", Destination("exile")),
])
def test_zone_destinations_are_closed_table_rows(text, expected):
    assert _whole(text).value == expected


def test_a_self_owner_possessive_is_accepted_in_a_destination():
    """A9: on legendary and planeswalker faces 'his/her owner's' is
    normalised to '~'s owner's'; the skeleton reads it as the owner."""
    assert _whole("to ~'s owner's hand").value == Destination("hand")
    assert _whole("onto the battlefield under ~'s owner's control").value == \
        Destination("battlefield", controller="owner")


# ── Full consumption: an unconsumed tail is never a guess ──────────────

@pytest.mark.parametrize("text", [
    "onto the battlefield face down as a 2/2 creature",   # characteristic-setting
    "on the top or bottom of your library",               # a choice of position
    "into their library second from the top or on the bottom",
    "onto the battlefield under an opponent's control",   # controller not in vocabulary
    "into your hand blorp",
])
def test_a_destination_phrase_with_unconsumed_tokens_is_unmodelled(text):
    r = _whole(text)
    assert r.value is None
    assert r.unmodelled is not None and r.unmodelled.lemma


def test_a_span_selects_the_slot_and_the_result_span_is_absolute():
    text = "return target creature to its owner's hand"
    start = text.index("to its")
    r = _dest().parse_destination(text, (start, len(text)))
    assert r.value == Destination("hand")
    assert r.span == (start, len(text))
    assert _dest().locate_destination(text, (0, len(text))) == (start, len(text))


def test_the_destination_locator_skips_a_zone_phrase_inside_the_object():
    """'up to', 'equal to' and a passive 'put into' inside the object are
    not destinations; the first PP that parses to the slot end is."""
    text = "return up to two target creature cards put into your graveyard this turn to your hand"
    span = _dest().locate_destination(text, (0, len(text)))
    assert text[span[0]:span[1]] == "to your hand"


# ── return: the source zone comes from the object span ─────────────────

@pytest.mark.parametrize("object_text,zone", [
    ("target creature card from your graveyard", "graveyard"),
    ("up to two target creature cards from your graveyard", "graveyard"),
    ("a creature card in your graveyard", "graveyard"),
    ("target creature", "battlefield"),
    ("target nonland permanent", "battlefield"),
    ("each creature", "battlefield"),
    ("target spell", "stack"),
    ("a card from your hand", "hand"),
    ("the exiled card", "exile"),
    ("all cards exiled with ~", "exile"),
    ("it", None),
    ("~", None),
    ("that card", None),
    ("a card from your hand or graveyard", None),   # a union has no single zone
])
def test_the_source_zone_of_a_move_comes_from_its_object_span(object_text, zone):
    """'return' names no source zone; 'return ~ to its owner's hand' and
    'return ~ from your graveyard to your hand' differ only in the object
    span, so the zone is read there, never inferred from verb plus
    destination."""
    assert _dest().source_zone(object_text) == zone


def test_the_destination_parse_is_memoised_and_deterministic():
    d = _dest()
    a = _whole("onto the battlefield tapped under your control")
    b = _whole("onto the battlefield tapped under your control")
    assert a == b
    assert a.value is b.value     # one frozen spec, shared (load-time memo)
