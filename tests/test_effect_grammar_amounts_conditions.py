"""Amounts, quantities, conditions, durations and delays of the typed effect
model (design doc 2026-09-29, section 6; E0).

This file starts with the duration and delay leaf
(`engine/effect_grammar/sub/duration.py`): a closed table over normalised
clause text that runs at load, never at resolution.

- F6: no new `DurationKind` in E0. A printed duration `effect_model` cannot
  expire is `UNMODELLED(DURATION)`, never `duration=None`.
- CR 611.2a: a continuous effect from a resolving spell or ability with no
  printed duration lasts indefinitely; CR 611.3a: a static ability's lasts
  while its source is on the battlefield.
- CR 603.7 / A30 / G14: a delay opens a delayed sub-ability with timing from
  `oracle_parser._DELAY_TIMING_PHRASES`; it is never a duration.
- A5 / M7: a delay-prefixed paragraph is a delayed sub-ability on a spell and
  `UNMODELLED(STRUCTURE)` on a permanent.

The amount, quantity and condition cases join this file with their leaves.
"""
from __future__ import annotations

import pytest

from engine.delayed_triggers import DelayedTriggerTiming
from engine.effect_model import Duration, DurationKind
from engine.effect_spec import HostKind, Stage, Unmodelled, Verb
from engine.effect_grammar.sub.duration import (
    default_duration, delay_paragraph, parse_delay, parse_duration)


@pytest.mark.parametrize("clause", [
    "until end of turn, target creature gains flying",
    "target creature gains flying until end of turn",
    "target creature can't block this turn",
    "this turn, creatures you control have haste",
])
def test_until_end_of_turn_leading_or_trailing_is_this_turn(clause):
    m = parse_duration(clause)
    assert m is not None
    assert m.duration == Duration(DurationKind.THIS_TURN)
    assert m.unmodelled is None
    # The span covers exactly the duration phrase, and stripping it leaves
    # the effect text whole.
    phrase = clause[m.span[0]:m.span[1]]
    assert phrase in ("until end of turn", "this turn")
    assert "flying" in m.rest or "block" in m.rest or "haste" in m.rest
    assert "turn" not in m.rest


def test_until_your_next_turn_is_the_modelled_next_turn_duration():
    m = parse_duration("target creature can't attack or block until your next turn")
    assert m.duration == Duration(DurationKind.UNTIL_YOUR_NEXT_TURN)


def test_for_as_long_as_the_source_remains_is_until_leaves():
    m = parse_duration(
        "exile target nonland permanent an opponent controls until ~ leaves the battlefield")
    assert m.duration == Duration(DurationKind.UNTIL_LEAVES)
    assert m.rest == "exile target nonland permanent an opponent controls"


def test_a_duration_before_a_scaled_amount_does_not_hide_the_scaler():
    m = parse_duration(
        "target creature gets +1/+1 until end of turn for each artifact you control")
    assert m.duration == Duration(DurationKind.THIS_TURN)
    assert "for each artifact you control" in m.rest
    assert "+1/+1" in m.rest


@pytest.mark.parametrize("clause", [
    "you may play that card until the end of your next turn",
    "it doesn't untap during its controller's next untap step",
    "target creature gets +2/+0 until end of combat",
    "gain control of target creature for as long as you control ~",
    "that player can't cast spells until the beginning of your next upkeep",
])
def test_a_duration_the_effect_model_cannot_expire_is_unmodelled_not_a_new_duration_kind(clause):
    m = parse_duration(clause)
    assert m is not None, "a printed duration is never silently dropped"
    assert m.duration is None
    assert m.unmodelled == Unmodelled(Stage.DURATION, detail=m.unmodelled.detail)
    assert m.unmodelled.detail
    # F6: the model's vocabulary is unchanged.
    assert {k.name for k in DurationKind} == {
        "THIS_TURN", "UNTIL_YOUR_NEXT_TURN", "WHILE_SOURCE_ON_BATTLEFIELD",
        "UNTIL_LEAVES", "PERMANENT"}


@pytest.mark.parametrize("clause, timing", [
    ("return it to the battlefield at the beginning of the next end step",
     DelayedTriggerTiming.NEXT_END_STEP),
    ("at the beginning of your next upkeep, pay {2}{g}{g}",
     DelayedTriggerTiming.YOUR_NEXT_UPKEEP),
    ("sacrifice it at the beginning of your next end step",
     DelayedTriggerTiming.YOUR_NEXT_END_STEP),
])
def test_a_delayed_timing_opens_a_delayed_sub_ability_never_a_duration(clause, timing):
    """CR 603.7: 'at the beginning of the next ...' creates a delayed
    triggered ability; it says when, not how long."""
    assert parse_duration(clause) is None
    d = parse_delay(clause)
    assert d is not None and d.timing is timing and d.unmodelled is None
    assert "at the beginning" not in d.inner and d.inner


def test_delay_timings_come_from_the_single_phrase_table():
    """G14: oracle_parser._DELAY_TIMING_PHRASES stays the one source."""
    from engine.oracle_parser import _DELAY_TIMING_PHRASES
    for phrase, name in _DELAY_TIMING_PHRASES.items():
        d = parse_delay("at the beginning of %s, draw a card" % phrase)
        assert d.timing is DelayedTriggerTiming[name]


def test_an_until_the_beginning_duration_is_not_a_delay():
    assert parse_delay("that player can't cast spells until the beginning of your next upkeep") is None


def test_a_delay_the_phrase_table_lacks_is_unmodelled_delay():
    d = parse_delay("exile that token at end of combat")
    assert d is not None and d.timing is None
    assert d.unmodelled.stage is Stage.DELAY


@pytest.mark.parametrize("host_kind", [
    HostKind.SPELL, HostKind.MODE, HostKind.ACTIVATED, HostKind.TRIGGERED,
    HostKind.LOYALTY, HostKind.CHAPTER])
def test_a_resolved_grant_with_no_printed_duration_is_permanent(host_kind):
    """CR 611.2a."""
    assert default_duration(host_kind, Verb.CONTINUOUS) == Duration(DurationKind.PERMANENT)


def test_a_static_grant_lasts_while_its_source_is_on_the_battlefield():
    """CR 611.3a."""
    assert default_duration(HostKind.STATIC, Verb.CONTINUOUS) == Duration(
        DurationKind.WHILE_SOURCE_ON_BATTLEFIELD)


def test_only_continuous_specs_take_a_default_duration():
    """Invariant 6: duration is set only on CONTINUOUS, UNTIL_LEAVES EXILE or
    PLAYER_COUNTERS, and only CONTINUOUS has an unprinted default."""
    for verb in Verb:
        if verb is not Verb.CONTINUOUS:
            assert default_duration(HostKind.SPELL, verb) is None


@pytest.mark.parametrize("clause", [
    "if you gained life this turn, draw a card",
    "draw a card for each creature that died this turn",
    "~ deals damage equal to the number of spells you've cast this turn to any target",
    "destroy target creature that was dealt damage this turn",
    "draw a card if an opponent lost life this turn",
    "destroy target creature that dealt damage to you this turn",
    "target player dealt damage by ~ this turn loses 1 life",
    "copy it for each spell cast before it this turn",
])
def test_this_turn_inside_a_condition_or_quantity_is_history_not_a_duration(clause):
    assert parse_duration(clause) is None


@pytest.mark.parametrize("clause", [
    "two target creatures can't be blocked this turn",
    "you may cast that card this turn",
    "if a source you control would deal damage this turn, it deals double that damage instead",
    "prevent all damage that would be dealt to target creature this turn",
])
def test_this_turn_on_a_prospective_or_passive_predicate_is_a_duration(clause):
    m = parse_duration(clause)
    assert m is not None and m.duration == Duration(DurationKind.THIS_TURN)


def test_a_delay_prefixed_paragraph_on_a_spell_opens_a_delayed_sub_ability():
    """A5 (CR 603.7): on an instant or sorcery the paragraph is a
    CREATE_TRIGGER(DELAYED) spec inside the SPELL host."""
    out = delay_paragraph(
        "at the beginning of your next upkeep, pay {2}{g}{g}. if you don't, you lose the game",
        is_spell=True)
    assert out.timing is DelayedTriggerTiming.YOUR_NEXT_UPKEEP
    assert out.inner.startswith("pay {2}{g}{g}")


def test_a_delay_prefixed_paragraph_on_a_permanent_is_unmodelled_structure():
    """M7: a printed triggered ability never says 'the next'."""
    out = delay_paragraph("at the beginning of the next end step, sacrifice ~",
                          is_spell=False)
    assert out == Unmodelled(Stage.STRUCTURE, detail=out.detail)


def test_an_ordinary_triggered_paragraph_is_not_delay_prefixed():
    assert delay_paragraph("at the beginning of your upkeep, draw a card",
                           is_spell=False) is None
