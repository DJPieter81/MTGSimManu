"""The effect model's grammar-facing vocabulary is additive and gated.

Design doc 2026-09-29 (F5/A22, F6, open question on ModKind): the clause
grammar may type modifications the layer system does not yet apply
(granted abilities, ability loss, P/T switching, control change, "must"
requirements). Those kinds exist as payload vocabulary only; the set of kinds
something actually applies is declared member by member (APPLIED_MODKINDS),
so a dispatcher can refuse the rest -- and a kind added later is refused until
it is listed (fail closed). Durations are never widened here: every
DurationKind has an expiry rule (CLOCKED_DURATIONS). A FILTER selector entry
is supported only when `Selector.covers_object` actually evaluates that
(key, value): support is value-typed and proven behaviourally, not declared by
key -- a keyword exclusion is supported for exactly the `cards.Keyword`
values covers_object compares against, never a printed or unknown keyword.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

from engine.cards import Keyword
from engine.effect_model import (ANY_KEYWORD, APPLIED_MODKINDS,
                                 CLOCKED_DURATIONS, SUPPORTED_FILTER_VALUES,
                                 DurationKind, ModFamily, ModKind,
                                 Modification, Selector, SelectorKind,
                                 is_supported_filter_entry)

_PAYLOAD_ONLY = {"GRANT_ABILITY", "REMOVE_ALL_ABILITIES", "SWITCH_PT",
                 "SET_CONTROLLER", "REQUIRE"}
# Applied by the layer system (CR 613: types, colours, keywords, P/T) or by
# the rule gate that owns the action (rules_query).
_APPLIED = {"SET_TYPES", "ADD_TYPES", "SET_COLORS", "ADD_KEYWORDS",
            "REMOVE_KEYWORDS", "SET_BASE_PT", "MODIFY_PT", "PROHIBIT",
            "PERMIT", "LIMIT", "COST_DELTA", "PREVENT_DAMAGE", "OBSERVE"}


def test_payload_only_modification_kinds_exist_but_are_not_applied():
    names = {k.name for k in ModKind}
    assert _PAYLOAD_ONLY <= names
    applied = {k.name for k in APPLIED_MODKINDS}
    assert applied.isdisjoint(_PAYLOAD_ONLY)
    assert applied == names - _PAYLOAD_ONLY


def test_every_modification_kind_is_declared_applied_or_payload_only():
    """Pinned like DurationKind: a new ModKind fails here until someone
    decides whether an owner applies it."""
    assert {k.name for k in ModKind} == _APPLIED | _PAYLOAD_ONLY
    assert {k.name for k in APPLIED_MODKINDS} == _APPLIED


def test_a_new_modification_kind_is_unapplied_until_it_is_listed(monkeypatch):
    """APPLIED_MODKINDS fails closed: re-execute the module with one extra
    ModKind member and the new kind must not be applied by default."""
    import engine.effect_model as em
    src = Path(em.__file__).read_text()
    anchor = '    OBSERVE = "observe"'
    assert src.count(anchor) == 1
    mutated = src.replace(anchor, anchor + '\n    PROBE_KIND = "probe_kind"')
    probe = types.ModuleType("engine._effect_model_probe")
    probe.__package__ = "engine"
    monkeypatch.setitem(sys.modules, probe.__name__, probe)
    exec(compile(mutated, em.__file__, "exec"), probe.__dict__)
    assert probe.ModKind.PROBE_KIND not in probe.APPLIED_MODKINDS
    assert {k.name for k in probe.APPLIED_MODKINDS} == _APPLIED


def test_a_requirement_is_a_rule_modification_and_the_rest_are_characteristic():
    assert Modification(ModKind.REQUIRE).family is ModFamily.RULE
    for n in _PAYLOAD_ONLY - {"REQUIRE"}:
        assert Modification(ModKind[n]).family is ModFamily.CHARACTERISTIC


def test_every_duration_kind_is_clocked_and_no_kind_is_added():
    assert CLOCKED_DURATIONS == frozenset(DurationKind)
    assert {k.name for k in DurationKind} == {
        "THIS_TURN", "UNTIL_YOUR_NEXT_TURN", "UNTIL_END_OF_YOUR_NEXT_TURN",
        "WHILE_SOURCE_ON_BATTLEFIELD", "UNTIL_LEAVES", "PERMANENT"}


def test_until_the_end_of_your_next_turn_ends_at_that_turns_cleanup():
    """CR 611.2: created in turn T by player 0, it lasts through player 0's
    first turn after T and ends at that turn's cleanup -- not at T's own
    cleanup, not at another player's, not as the next turn begins."""
    from engine.effect_model import until_end_of_your_next_turn
    from engine.turn_clock import Clock, ClockEvent
    own = until_end_of_your_next_turn(0, turn=4)        # created on P0's turn 4
    assert not own.expired_by(ClockEvent(Clock.CLEANUP, 0, turn=4))
    assert not own.expired_by(ClockEvent(Clock.CLEANUP, 1, turn=5))
    assert not own.expired_by(ClockEvent(Clock.TURN_BEGINS, 0, turn=6))
    assert own.expired_by(ClockEvent(Clock.CLEANUP, 0, turn=6))
    theirs = until_end_of_your_next_turn(0, turn=5)     # created on P1's turn 5
    assert theirs.expired_by(ClockEvent(Clock.CLEANUP, 0, turn=6))
    assert not until_end_of_your_next_turn(0, turn=4).expired_by(
        ClockEvent(Clock.CLEANUP, 0))                   # an unstamped event


def _obj(controller, keywords=()):
    return SimpleNamespace(controller=controller, keywords=list(keywords),
                           instance_id=1, battlefield_entry_seq=0)


def _covers(entry, card, player=0):
    return Selector(SelectorKind.FILTER, player=player,
                    filter=(entry,)).covers_object(card)


def test_supported_filter_values_are_exactly_the_entries_covers_object_evaluates():
    assert SUPPORTED_FILTER_VALUES == frozenset({
        ("controller", "opponents"), ("without_keyword", ANY_KEYWORD)})
    # ('controller', 'opponents'): excludes the selector player's own object.
    assert not _covers(("controller", "opponents"), _obj(0))
    assert _covers(("controller", "opponents"), _obj(1))
    # ('without_keyword', <kw>): excludes objects with that keyword, any kw.
    for kw in (Keyword.FLYING, Keyword.HASTE):
        assert not _covers(("without_keyword", kw.value), _obj(1, [kw]))
        assert _covers(("without_keyword", kw.value), _obj(1))
        assert is_supported_filter_entry("without_keyword", kw.value)
    assert is_supported_filter_entry("controller", "opponents")


def test_an_entry_covers_object_ignores_is_unsupported():
    # controller 'you' is NOT evaluated: it covers an opponent's object too.
    assert _covers(("controller", "you"), _obj(1))
    assert not is_supported_filter_entry("controller", "you")
    # Type keys are not evaluated either.
    assert _covers(("types", ("creature",)), _obj(1))
    assert not is_supported_filter_entry("types", ("creature",))
    assert not is_supported_filter_entry("controller", "any")
    assert not is_supported_filter_entry("without_keyword", "")


def test_a_keyword_exclusion_is_supported_for_exactly_the_keyword_values_covers_object_matches():
    """Every cards.Keyword value excludes its holder and is supported."""
    for kw in Keyword:
        assert is_supported_filter_entry("without_keyword", kw.value), kw
        assert not _covers(("without_keyword", kw.value), _obj(1, [kw])), kw
        assert _covers(("without_keyword", kw.value), _obj(1)), kw


def test_a_printed_or_unknown_keyword_exclusion_is_unsupported_because_it_excludes_nothing():
    """A printed multi-word keyword ('first strike' against the enum value
    'first_strike'), a differently cased value, a misspelling, a keyword the
    enum lacks, and the ANY_KEYWORD placeholder itself are never matched by
    covers_object -- a creature holding the keyword stays covered -- so a
    spec needing them must not pass the gate (it would silently widen)."""
    holders = {"first strike": Keyword.FIRST_STRIKE,
               "double strike": Keyword.DOUBLE_STRIKE,
               "First_Strike": Keyword.FIRST_STRIKE,
               "Flying": Keyword.FLYING, "flyingg": Keyword.FLYING,
               "horsemanship": Keyword.FLYING, ANY_KEYWORD: Keyword.FLYING}
    for value, kw in holders.items():
        assert _covers(("without_keyword", value), _obj(1, [kw])), value
        assert not is_supported_filter_entry("without_keyword", value), value
    for junk in (None, 3, ("flying",)):
        assert not is_supported_filter_entry("without_keyword", junk), junk


def test_a_reference_valued_controller_is_unsupported():
    """covers_object compares the controller entry to 'opponents' only: a
    Ref controller ("creatures that player controls") is ignored, so it
    covers every object and must be refused."""
    from engine.effect_spec import CardFilter, Ref, RefKind
    ref = Ref(RefKind.TARGET, index=0)
    assert _covers(("controller", ref), _obj(0))
    assert _covers(("controller", ref), _obj(1))
    assert not is_supported_filter_entry("controller", ref)
    entries = CardFilter(controller=ref).as_tuple()
    assert entries == (("controller", ref),)
    assert not any(is_supported_filter_entry(k, v) for k, v in entries)
    assert not is_supported_filter_entry("controller", ["opponents"])


def test_a_card_filter_keyword_exclusion_passes_the_gate_only_as_a_keyword_value():
    """The CardFilter bridge carries the keyword string through unchanged,
    so the gate is what refuses a printed form."""
    from engine.effect_spec import CardFilter
    printed = CardFilter(without_keywords=frozenset({"first strike"})).as_tuple()
    assert printed == (("without_keyword", "first strike"),)
    assert not any(is_supported_filter_entry(k, v) for k, v in printed)
    typed = CardFilter(
        without_keywords=frozenset({Keyword.FIRST_STRIKE.value})).as_tuple()
    assert all(is_supported_filter_entry(k, v) for k, v in typed)
    sel = Selector(SelectorKind.FILTER, player=0, filter=typed)
    assert not sel.covers_object(_obj(1, [Keyword.FIRST_STRIKE]))
    assert sel.covers_object(_obj(1, [Keyword.FLYING]))
