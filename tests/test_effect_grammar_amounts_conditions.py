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
    assert m.value == Duration(DurationKind.THIS_TURN)
    assert m.unmodelled is None
    # The span covers exactly the duration phrase, and stripping it leaves
    # the effect text whole.
    phrase = clause[m.span[0]:m.span[1]]
    assert phrase in ("until end of turn", "this turn")
    rest = m.rest_text(clause)
    assert "flying" in rest or "block" in rest or "haste" in rest
    assert "turn" not in rest


def test_until_your_next_turn_is_the_modelled_next_turn_duration():
    m = parse_duration("target creature can't attack or block until your next turn")
    assert m.value == Duration(DurationKind.UNTIL_YOUR_NEXT_TURN)


def test_for_as_long_as_the_source_remains_is_until_leaves():
    clause = ("exile target nonland permanent an opponent controls until ~ "
              "leaves the battlefield")
    m = parse_duration(clause)
    assert m.value == Duration(DurationKind.UNTIL_LEAVES)
    assert m.rest_text(clause) == "exile target nonland permanent an opponent controls"


def test_a_duration_before_a_scaled_amount_does_not_hide_the_scaler():
    clause = "target creature gets +1/+1 until end of turn for each artifact you control"
    m = parse_duration(clause)
    assert m.value == Duration(DurationKind.THIS_TURN)
    assert "for each artifact you control" in m.rest_text(clause)
    assert "+1/+1" in m.rest_text(clause)


@pytest.mark.parametrize("clause", [
    "it doesn't untap during its controller's next untap step",
    "target creature gets +2/+0 until end of combat",
    "gain control of target creature for as long as you control ~",
    "that player can't cast spells until the beginning of your next upkeep",
])
def test_a_duration_the_effect_model_cannot_expire_is_unmodelled_not_a_new_duration_kind(clause):
    m = parse_duration(clause)
    assert m is not None, "a printed duration is never silently dropped"
    assert m.value is None
    assert m.unmodelled == Unmodelled(Stage.DURATION, detail=m.unmodelled.detail)
    assert m.unmodelled.detail
    # F6: the model's vocabulary grows only with a clock that expires it
    # ("until the end of your next turn": the impulse unit's turn-stamped
    # clock events).
    assert {k.name for k in DurationKind} == {
        "THIS_TURN", "UNTIL_YOUR_NEXT_TURN", "UNTIL_END_OF_YOUR_NEXT_TURN",
        "WHILE_SOURCE_ON_BATTLEFIELD", "UNTIL_LEAVES", "PERMANENT"}


def test_until_the_end_of_your_next_turn_is_a_modelled_duration():
    m = parse_duration("you may play that card until the end of your next turn")
    assert m.value == Duration(DurationKind.UNTIL_END_OF_YOUR_NEXT_TURN)


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
    assert d is not None and d.value is timing and d.unmodelled is None
    inner = d.rest_text(clause)
    assert "at the beginning" not in inner and inner


def test_delay_timings_come_from_the_single_phrase_table():
    """G14: oracle_parser._DELAY_TIMING_PHRASES stays the one source."""
    from engine.oracle_parser import _DELAY_TIMING_PHRASES
    for phrase, name in _DELAY_TIMING_PHRASES.items():
        d = parse_delay("at the beginning of %s, draw a card" % phrase)
        assert d.value is DelayedTriggerTiming[name]


def test_an_until_the_beginning_duration_is_not_a_delay():
    assert parse_delay("that player can't cast spells until the beginning of your next upkeep") is None


def test_a_delay_the_phrase_table_lacks_is_unmodelled_delay():
    d = parse_delay("exile that token at end of combat")
    assert d is not None and d.value is None
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
    assert m is not None and m.value == Duration(DurationKind.THIS_TURN)


def test_a_delay_prefixed_paragraph_on_a_spell_opens_a_delayed_sub_ability():
    """A5 (CR 603.7): on an instant or sorcery the paragraph is a
    CREATE_TRIGGER(DELAYED) spec inside the SPELL host."""
    paragraph = ("at the beginning of your next upkeep, pay {2}{g}{g}. if you "
                 "don't, you lose the game")
    out = delay_paragraph(paragraph, is_spell=True)
    assert out.value is DelayedTriggerTiming.YOUR_NEXT_UPKEEP
    assert out.rest_text(paragraph).startswith("pay {2}{g}{g}")


def test_a_delay_prefixed_paragraph_on_a_permanent_is_unmodelled_structure():
    """M7: a printed triggered ability never says 'the next'."""
    paragraph = "at the beginning of the next end step, sacrifice ~"
    out = delay_paragraph(paragraph, is_spell=False)
    assert out.value is None
    assert out.unmodelled == Unmodelled(Stage.STRUCTURE,
                                        detail=out.unmodelled.detail)
    assert out.span == (0, len(paragraph))


def test_an_ordinary_triggered_paragraph_is_not_delay_prefixed():
    assert delay_paragraph("at the beginning of your upkeep, draw a card",
                           is_spell=False) is None


@pytest.mark.parametrize("clause", [
    "any number of target creatures can't block this turn.",
    "a creature dealt damage this way can't be regenerated this turn.",
    "creatures dealt damage this way can't block this turn.",
    "players dealt damage this way can't cast noncreature spells this turn.",
    "target creature can block any number of creatures this turn.",
    "target creature with power less than or equal to ~'s power can't block this turn.",
    "target creature an opponent controls with power less than or equal to "
    "the number of warriors you control can't block this turn.",
    "creatures your opponents control with power less than or equal to that "
    "number can't block this turn.",
])
def test_this_turn_closing_a_prohibition_or_permission_after_a_history_word_is_a_duration(clause):
    """A history frame or event in the subject noun phrase ('dealt damage
    this way', 'equal to', 'any number of') does not make the main
    predicate's 'this turn' history: the phrase closes the prohibition or
    permission after it. Read as history, F6 / CR 611.2a would make the
    one-turn prohibition permanent."""
    m = parse_duration(clause)
    assert m is not None and m.value == Duration(DurationKind.THIS_TURN), clause


@pytest.mark.parametrize("clause", [
    "~ can't attack unless you've cast a creature spell this turn.",
    "target creature gets +1/+1 for each creature that died this turn.",
    "each player who has cast a nonartifact spell this turn can't cast "
    "additional nonartifact spells.",
    "return up to one target creature that crewed it this turn to its "
    "owner's hand.",
    "choose one that hasn't been chosen this turn -",
    "return target creature card in your graveyard that wasn't put there "
    "this combat.",
])
def test_this_turn_closing_a_history_frame_after_the_predicate_stays_history(clause):
    assert parse_duration(clause) is None, clause


@pytest.mark.parametrize("clause,rest", [
    ("if you do, until end of turn, target creature gets +2/+2",
     "if you do, target creature gets +2/+2"),
    ("whenever ~ attacks, until end of turn, ~ gets +1/+0.",
     "whenever ~ attacks, ~ gets +1/+0."),
])
def test_removing_a_comma_set_duration_leaves_one_comma(clause, rest):
    m = parse_duration(clause)
    assert m.rest_text(clause) == rest


def test_removing_a_comma_set_delay_leaves_one_comma():
    clause = ("if you do, at the beginning of the next end step, create a "
              "token that's a copy of that artifact.")
    d = parse_delay(clause)
    assert d.rest_text(clause) == "if you do, create a token that's a copy of that artifact"


@pytest.mark.parametrize("clause,inner", [
    ("at the beginning of your next upkeep step, draw a card", "draw a card"),
    ("~ deals 3 damage to that player at the beginning of your next upkeep "
     "step unless that player pays {u}",
     "~ deals 3 damage to that player unless that player pays {u}"),
])
def test_a_printed_step_word_belongs_to_the_delay_phrase(clause, inner):
    d = parse_delay(clause)
    assert d.value is DelayedTriggerTiming.YOUR_NEXT_UPKEEP
    assert clause[slice(*d.span)].endswith("upkeep step")
    assert d.rest_text(clause) == inner


@pytest.mark.parametrize("clause", [
    "add {c}{c} at the beginning of your next main phase this turn.",
    "at the beginning of the next combat phase this turn, target creature "
    "you control deals damage equal to its power to up to one target creature.",
])
def test_this_turn_inside_a_delay_phrase_is_not_also_a_duration(clause):
    """One text span, one leaf: the delay owns its 'this turn'."""
    d = parse_delay(clause)
    assert d is not None and d.unmodelled.stage is Stage.DELAY
    assert parse_duration(clause) is None


def test_for_the_rest_of_the_game_is_a_permanent_duration():
    """CR 611.2a: the effect has no end; it is never dropped."""
    m = parse_duration("that player can't gain life for the rest of the game.")
    assert m.value == Duration(DurationKind.PERMANENT)


def test_this_combat_is_a_printed_duration_the_model_cannot_expire():
    m = parse_duration("that creature can't block this combat.")
    assert m.value is None and m.unmodelled.stage is Stage.DURATION
    assert m.unmodelled.detail == "duration.this_combat"


# ── Clause-level amounts and riders (L2-L4, E0 step 10-11) ─────────────

def _frames_and_specs(text, types=("sorcery",), keywords=()):
    from engine.effect_grammar import clauses as CL
    from engine.effect_grammar import normalize as N
    from engine.effect_grammar import patterns as PT
    from engine.effect_grammar import structure as S
    from engine.effect_grammar.keywords import keywords702
    tc = frozenset(types)
    facts = N.Facts(type_class=tc, is_spell=bool({"instant", "sorcery"} & tc),
                    keywords702=keywords702(keywords))
    frames, specs = [], []
    for h in S.parse_face_structure(text, facts).hosts:
        frames.extend(CL.frame_host(h))
        specs.extend(cm.spec for fm in PT.match_host(h) for cm in fm.clauses)
    return frames, specs


@pytest.mark.parametrize("text, stage", [
    ("You gain life equal to the greatest number of creatures any opponent "
     "controls.", Stage.QUANTITY),
    ("Draw a card for each color among permanents you control.",
     Stage.QUANTITY),
])
def test_an_uncountable_quantity_makes_the_clause_unmodelled_never_zero(text, stage):
    _, specs = _frames_and_specs(text)
    (s,) = specs
    assert s.verb is Verb.UNMODELLED
    assert s.payload.stage is stage
    assert s.amount is None


def test_the_flashback_cost_equal_to_mana_cost_sentence_is_the_granted_keywords_cost_rule():
    """A8: the cost-rule sentence is absorbed as the cost parameter of the
    granted flashback keyword, never an effect clause."""
    from engine.effect_spec import KeywordSpec
    frames, specs = _frames_and_specs(
        "Each instant and sorcery card in your graveyard gains flashback "
        "until end of turn. The flashback cost is equal to its mana cost.")
    assert frames[-1].clauses == () or len(frames) == 1
    riders = [v for f in frames for k, v in f.riders if k == "cost_rule"]
    assert riders == [KeywordSpec("flashback", cost_rule="mana_cost")]
    assert len(specs) == 1


def test_a_leading_for_each_is_a_for_each_amount_on_the_counted_verb():
    """A16: "For each <Q>, <counted VP>" with no anaphor to the element is
    FOR_EACH(Q) on the counted verb."""
    from engine.effect_spec import AmountKind
    frames, specs = _frames_and_specs("For each opponent, create a 1/1 "
                                      "black Rat creature token.")
    (s,) = specs
    assert s.verb is Verb.CREATE_TOKEN
    assert s.amount.kind is AmountKind.FOR_EACH and s.amount.n == 1


def test_unless_pays_is_an_unless_condition_with_the_printed_cost():
    from engine.effect_spec import ConditionKind
    frames, specs = _frames_and_specs(
        "Counter target spell unless its controller pays {3}.",
        types=("instant",))
    (s,) = specs
    assert s.verb is Verb.COUNTER
    assert s.condition.kind is ConditionKind.UNLESS
    assert s.condition.cost is not None


def test_where_x_defines_the_amount_of_the_clause():
    from engine.effect_spec import AmountKind
    frames, specs = _frames_and_specs(
        "~ deals X damage to any target, where X is the number of cards in "
        "your hand.", types=("instant",))
    (s,) = specs
    assert s.verb is Verb.DAMAGE
    assert s.amount.kind is AmountKind.X_DEFINED
