"""L4 of the clause grammar: the pattern cascade (design doc 2026-09-29,
section 3 "L4, pattern cascade", section 4, section 5; E0 step 11, the
patterns.py part).

`match_clause` reads a clause's lemma from the verb lexicon, cuts the
verb's slots, fills them through the sub-grammar leaves (the one leaf
contract) and returns a typed EffectSpec or an UNMODELLED one naming the
deepest failure. Every character of the clause must be consumed by a slot
(the coverage invariant); a leftover word is a refusal, never ignored.
"""
from __future__ import annotations

import pytest

from engine.effect_grammar import patterns as PT
from engine.effect_model import DurationKind, ModKind, SelectorKind
from engine.effect_spec import (AmountKind, Chooser, ConditionKind, HostKind,
                                Ref, RefKind, Stage, Verb)


def _m(text, **kw):
    return PT.match_clause(text, **kw)


@pytest.mark.parametrize("text, verb", [
    ("destroy target creature", Verb.DESTROY),
    ("exile target nonland permanent", Verb.EXILE),
    ("draw two cards", Verb.DRAW),
    ("you gain 3 life", Verb.GAIN_LIFE),
    ("each opponent loses 2 life", Verb.LOSE_LIFE),
    ("~ deals 3 damage to any target", Verb.DAMAGE),
    ("target creature gets +2/+2 until end of turn", Verb.CONTINUOUS),
    ("create two 1/1 white soldier creature tokens", Verb.CREATE_TOKEN),
    ("put a +1/+1 counter on each creature you control", Verb.PUT_COUNTERS),
    ("add {r}{r}{r}", Verb.ADD_MANA),
    ("scry 2", Verb.SCRY),
    ("investigate", Verb.KEYWORD_ACTION),
    ("counter target spell", Verb.COUNTER),
    ("return target creature card from your graveyard to your hand",
     Verb.MOVE),
    ("sacrifice a creature", Verb.SACRIFICE),
    ("search your library for a basic land card", Verb.SEARCH),
])
def test_a_clause_is_typed_by_its_lexicon_lemma_and_every_slot_is_consumed(text, verb):
    m = _m(text)
    assert m.spec.verb is verb, m.spec
    assert m.row
    assert PT.unconsumed(m, text) == ""


def test_an_actor_only_verb_types_its_subject_as_the_actor_never_a_principal():
    m = _m("each opponent loses 2 life")
    s = m.spec
    assert s.actor.kind is SelectorKind.OPPONENTS
    assert s.target is s.subject is s.ref is None
    assert s.amount.kind is AmountKind.LITERAL and s.amount.n == 2


def test_a_targeted_player_actor_is_a_requirement_with_the_actor_role():
    m = _m("target player draws two cards")
    assert [role for role, _, _ in m.targets] == ["actor"]
    assert m.spec.actor is None and m.spec.amount.n == 2


def test_a_damage_source_is_the_other_participant_and_the_recipient_the_principal():
    m = _m("~ deals 3 damage to any target")
    assert m.spec.other == Ref(RefKind.SELF)
    assert [role for role, _, _ in m.targets] == ["principal"]
    assert m.spec.amount.n == 3


def test_a_damage_amount_printed_as_a_scaler_is_the_scalers_amount():
    m = _m("~ deals damage equal to its power to target creature")
    assert m.spec.verb is Verb.DAMAGE
    assert m.spec.amount.kind is AmountKind.EQUAL_TO
    assert ("ref", "its") in m.pending


def test_a_continuous_predicate_takes_its_subject_as_principal_and_its_duration():
    m = _m("creatures you control get +1/+1 until end of turn")
    s = m.spec
    assert s.verb is Verb.CONTINUOUS
    assert s.payload.kind is ModKind.MODIFY_PT
    assert s.subject is not None and s.filter.controller == "you"
    assert s.duration is not None


def test_a_continuous_predicate_with_no_printed_duration_takes_the_hosts_default():
    """CR 611.3a: a static ability's effect lasts while its source is on
    the battlefield; CR 611.2a: a resolving spell's effect with no printed
    duration lasts indefinitely."""
    m = _m("creatures you control get +1/+1", host_kind=HostKind.STATIC)
    assert m.spec.duration.kind is DurationKind.WHILE_SOURCE_ON_BATTLEFIELD
    m = _m("target creature gets +1/+1", host_kind=HostKind.SPELL)
    assert m.spec.duration.kind is DurationKind.PERMANENT


def test_an_untargeted_choice_is_a_filter_with_its_count():
    m = _m("sacrifice a creature")
    assert m.spec.filter.types == frozenset({"creature"})
    assert m.spec.amount.n == 1
    assert m.spec.target is m.spec.subject is None


def test_a_printed_chooser_is_typed_on_the_choice():
    m = _m("discard a card at random")
    assert m.spec.verb is Verb.DISCARD
    assert m.spec.chooser is Chooser.RANDOM


def test_you_may_makes_the_clause_optional_and_is_consumed():
    m = _m("you may draw a card")
    assert m.spec.optional is True and m.spec.verb is Verb.DRAW
    assert PT.unconsumed(m, "you may draw a card") == ""


def test_a_leftover_word_makes_the_clause_unmodelled_never_ignored():
    m = _m("draw a card from the sideboard of your choice")
    assert m.spec.verb is Verb.UNMODELLED
    assert m.spec.payload.lemma == "draw"
    assert m.spec.payload.stage is not None


def test_a_clause_with_no_lexicon_verb_is_unmodelled_no_lemma():
    m = _m("the greatest power among creatures you control")
    assert m.spec.verb is Verb.UNMODELLED
    assert m.spec.payload.stage is Stage.NO_LEMMA


def test_a_recognised_unsupported_action_is_typed_as_such():
    m = _m("regenerate target creature")
    assert m.spec.verb is Verb.UNMODELLED
    assert m.spec.payload.stage is Stage.RECOGNIZED_UNSUPPORTED


def test_an_unbound_x_is_unmodelled_amount_and_a_bound_x_is_typed():
    m = _m("~ deals x damage to any target")
    assert m.spec.verb is Verb.UNMODELLED
    assert m.spec.payload.stage is Stage.AMOUNT
    m = _m("~ deals x damage to any target", has_x=True)
    assert m.spec.verb is Verb.DAMAGE
    assert m.spec.amount.kind is AmountKind.X


def test_an_inherited_prefix_is_read_but_never_consumed_twice():
    """L3 hands a gapped or elided clause its antecedent's text as a
    prefix: the prefix supplies the verb or subject, and its spans are not
    the clause's."""
    m = _m("the rest on the bottom of your library", prefix="put")
    assert m.spec.verb is Verb.MOVE
    assert m.spec.dest.position == "bottom"
    assert all(a >= 0 for _, (a, b) in m.consumed)
    assert PT.unconsumed(m, "the rest on the bottom of your library") == ""


def test_match_clause_is_memoised_with_a_bounded_cache():
    PT.clear_caches()
    a = _m("draw a card")
    b = _m("draw a card")
    assert a is b
    info = PT.match_clause.cache_info()
    assert info.maxsize is not None and info.hits >= 1


def test_a_frame_condition_reaches_every_clause_spec_of_its_sentence():
    from engine.effect_grammar import normalize as N
    from engine.effect_grammar import structure as S
    f = N.Facts(type_class=frozenset({"instant"}), is_spell=True)
    (h,) = S.parse_face_structure(
        "If you control an artifact, draw a card and you gain 2 life.", f).hosts
    fms = PT.match_host(h)
    specs = [cm.spec for fm in fms for cm in fm.clauses]
    assert [s.verb for s in specs] == [Verb.DRAW, Verb.GAIN_LIFE]
    assert all(s.condition is not None
               and s.condition.kind is ConditionKind.STATE for s in specs)


@pytest.mark.parametrize("text", [
    "each opponent sacrifices a creature",
    "target player discards a card",
    "each player discards two cards",
    "that player sacrifices a permanent",
])
def test_an_unprinted_sacrifice_or_discard_choice_belongs_to_the_acting_player(text):
    """CR 701.21a / 701.8a: the player who sacrifices or discards chooses
    which; an unprinted "of their choice" changes nothing."""
    unprinted = _m(text).spec
    printed = _m(text + " of their choice").spec
    assert unprinted.verb is printed.verb is not Verb.UNMODELLED
    assert unprinted.chooser is printed.chooser is Chooser.PARTICIPANT


def test_your_own_sacrifice_or_discard_is_chosen_by_the_controller():
    assert _m("sacrifice a creature").spec.chooser is Chooser.CONTROLLER
    assert _m("you discard a card").spec.chooser is Chooser.CONTROLLER
    assert _m("discard a card at random").spec.chooser is Chooser.RANDOM


@pytest.mark.parametrize("text", [
    "exile target player's graveyard",
    "exile target opponent's graveyard",
])
def test_a_target_players_zone_as_the_object_is_one_target_requirement(text):
    """Section 5 acceptance (a): every counted target word is one
    requirement. "target player's graveyard" is the whole zone of the
    targeted player, whose requirement is the player."""
    m = _m(text)
    assert m.spec.verb is Verb.EXILE, m.spec
    assert m.spec.amount.kind is AmountKind.WHOLE_ZONE
    assert m.spec.filter.zone == "graveyard"
    ((role, req, _span),) = m.targets
    assert role == "principal" and req.types == frozenset({"player"})
    assert PT.unconsumed(m, text) == ""


def test_control_of_a_player_is_never_a_typed_object_control_change():
    """CR 722: controlling another player is not gaining control of an
    object; the printed "during that player's next turn" is a duration the
    duration leaf does not read. Refused, never a permanent SET_CONTROLLER
    over a player."""
    m = _m("you gain control of target opponent during that player's next "
           "turn")
    assert m.spec.verb is Verb.UNMODELLED
    m = _m("you gain control of target creature until end of turn")
    assert m.spec.verb is Verb.CONTINUOUS
    assert m.spec.payload.kind is ModKind.SET_CONTROLLER
    assert m.spec.duration.kind is DurationKind.THIS_TURN


def test_a_selection_from_among_named_cards_takes_no_default_zone():
    """A verb's default zone (a discard or reveal reads the hand) applies
    only when no source is printed; "from among them" is the source."""
    m = _m("you may reveal a colorless card from among them")
    assert m.spec.verb is Verb.REVEAL
    assert m.spec.filter.zone != "hand"
    assert _m("reveal a creature card").spec.filter.zone == "hand"
