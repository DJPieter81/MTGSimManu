"""The effect model's grammar-facing vocabulary is additive and gated.

Design doc 2026-09-29 (F5/A22, F6, open question on ModKind): the clause
grammar may type modifications the layer system does not yet apply
(granted abilities, ability loss, P/T switching, control change, "must"
requirements). Those kinds exist as payload vocabulary only; the set of kinds
something actually applies is declared (APPLIED_MODKINDS), so a dispatcher
can refuse the rest. Durations are never widened here: every DurationKind has
an expiry rule (CLOCKED_DURATIONS). A FILTER selector entry is supported only
when `Selector.covers_object` actually evaluates that (key, value): support is
value-typed and proven behaviourally, not declared by key.
"""
from __future__ import annotations

from types import SimpleNamespace

from engine.cards import Keyword
from engine.effect_model import (ANY_KEYWORD, APPLIED_MODKINDS,
                                 CLOCKED_DURATIONS, SUPPORTED_FILTER_VALUES,
                                 DurationKind, ModFamily, ModKind,
                                 Modification, Selector, SelectorKind,
                                 is_supported_filter_entry)

_PAYLOAD_ONLY = {"GRANT_ABILITY", "REMOVE_ALL_ABILITIES", "SWITCH_PT",
                 "SET_CONTROLLER", "REQUIRE"}


def test_payload_only_modification_kinds_exist_but_are_not_applied():
    names = {k.name for k in ModKind}
    assert _PAYLOAD_ONLY <= names
    applied = {k.name for k in APPLIED_MODKINDS}
    assert applied.isdisjoint(_PAYLOAD_ONLY)
    assert applied == names - _PAYLOAD_ONLY


def test_a_requirement_is_a_rule_modification_and_the_rest_are_characteristic():
    assert Modification(ModKind.REQUIRE).family is ModFamily.RULE
    for n in _PAYLOAD_ONLY - {"REQUIRE"}:
        assert Modification(ModKind[n]).family is ModFamily.CHARACTERISTIC


def test_every_duration_kind_is_clocked_and_no_kind_is_added():
    assert CLOCKED_DURATIONS == frozenset(DurationKind)
    assert {k.name for k in DurationKind} == {
        "THIS_TURN", "UNTIL_YOUR_NEXT_TURN", "WHILE_SOURCE_ON_BATTLEFIELD",
        "UNTIL_LEAVES", "PERMANENT"}


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
