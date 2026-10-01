"""The condition sub-grammar (design doc 2026-09-29, section 2
``Condition``, section 6 "Condition"; F9, A2, A23, A27, A31; E0 step 11).

A condition is a closed predicate table over L0 output that runs at load,
never at resolution, under the one leaf contract (`engine.effect_grammar.sub`).
These tests pin the rules:

* a board count ("you control three or more artifacts") is a STATE
  ``count`` over the filter leaf's CardFilter, with the printed comparison
  as ``op`` / ``n``; a comparison against another player's count is a
  quantity comparand (``Amount(EQUAL_TO, quantity=...)``);
* "this turn" inside a condition is history: the condition consumes it
  and it is never left as a duration (section 6);
* a pronoun inside a condition is the linker's (rule 0, A23): it is left in
  ``pending``, never bound here;
* a past-tense object condition reads last-known information (CR 608.2h);
* ``unless <player> pays <cost>`` is UNLESS with the payer from the
  participant leaf and the cost through the payload leaf's PAY payload,
  i.e. the activation-cost parser (A31: a frozen CostSnapshot); any other
  ``unless <cond>`` is NOT(cond);
* performed-gating, "countered this way", replacement "would", "if able"
  and "for as long as" are never conditions (section 6): the leaf returns
  None and the caller's structure owns them;
* an ability word is the condition's label, informational only (CR 207.2c);
* an unknown phrase is UNMODELLED(CONDITION) over the whole slot, never a
  broader condition.

Synthetic phrases only; no card names.
"""
from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

import pytest

from engine.effect_model import Selector, SelectorKind
from engine.effect_spec import (Amount, AmountKind, CardFilter, Condition,
                                ConditionKind, Quantity, QuantityKind, Ref,
                                RefKind, Stage, freeze_cost)
from engine.effect_grammar.sub import condition as C

REPO = Path(__file__).resolve().parent.parent
SUB = REPO / "engine" / "effect_grammar" / "sub"

_ONE = Amount(AmountKind.LITERAL, n=1)


def _n(k):
    return Amount(AmountKind.LITERAL, n=k)


def _raw_free(obj):
    """`obj` with every ``raw`` field blanked, recursively: the printed raw
    text is pinned separately, so values compare on their typed fields."""
    if isinstance(obj, tuple):
        return tuple(_raw_free(x) for x in obj)
    if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
        return obj
    changes = {}
    for f in dataclasses.fields(obj):
        v = getattr(obj, f.name)
        if f.name == "raw":
            changes["raw"] = ""
        elif dataclasses.is_dataclass(v) or isinstance(v, tuple):
            changes[f.name] = _raw_free(v)
    return dataclasses.replace(obj, **changes)


def _cond(text, **kw):
    r = C.parse_condition(text, (0, len(text)), lemma="x", **kw)
    assert r is not None, text
    return r


def _value(text, **kw):
    r = _cond(text, **kw)
    assert r.unmodelled is None and r.value is not None, (text, r)
    return r


# ── Board state: counts over a typed filter ────────────────────────────

def test_a_board_count_condition_is_a_state_count_over_a_typed_filter():
    r = _value("if you control three or more artifacts")
    assert _raw_free(r.value) == Condition(
        ConditionKind.STATE, pred="count", op=">=", n=_n(3),
        filter=CardFilter(types=frozenset({"artifact"}), controller="you"))
    assert r.value.raw == "if you control three or more artifacts"
    assert r.span == (0, len("if you control three or more artifacts"))
    assert r.rest_spans == ()


@pytest.mark.parametrize("text,op,n,extra", [
    ("if you control two or fewer other lands", "<=", 2, {"other": True}),
    ("if you control no artifacts", "==", 0, {}),
    ("if you control at least five other lands", ">=", 5, {"other": True}),
    ("if you control exactly one land", "==", 1, {}),
    ("if you control a land", ">=", 1, {}),
    ("if you control another land", ">=", 1, {"other": True}),
])
def test_a_threshold_comparison_reads_every_printed_direction(text, op, n, extra):
    """'N or fewer', 'no', 'at least N', 'exactly N' and a determiner are
    comparisons of the count, never its first number."""
    r = _value(text)
    assert r.value.kind is ConditionKind.STATE and r.value.pred == "count"
    assert (r.value.op, r.value.n) == (op, _n(n))
    f = r.value.filter
    assert f.types == frozenset({"land"}) or f.types == frozenset({"artifact"})
    assert f.controller == "you"
    for k, v in extra.items():
        assert getattr(f, k) == v, (text, f)


def test_a_qualified_count_keeps_every_qualifier_of_the_filter():
    r = _value("if you control a creature with power 4 or greater")
    assert r.value.op == ">=" and r.value.n == _ONE
    assert r.value.filter.stat_bounds == (("power", ">=", _n(4)),)
    assert r.value.filter.types == frozenset({"creature"})


def test_a_count_of_cards_a_player_holds_is_a_count_in_that_zone():
    r = _value("if you have no cards in hand")
    assert _raw_free(r.value) == Condition(
        ConditionKind.STATE, pred="count", op="==", n=_n(0),
        filter=CardFilter(zone="hand", owner="you"))
    r = _value("as long as there are three or more land cards in your graveyard")
    assert _raw_free(r.value) == Condition(
        ConditionKind.STATE, pred="count", op=">=", n=_n(3),
        filter=CardFilter(zone="graveyard", types=frozenset({"land"}),
                          owner="you"))


def test_card_types_among_graveyard_cards_is_its_own_state_measure():
    r = _value("as long as there are four or more card types among cards in "
               "your graveyard", label="delirium")
    assert _raw_free(r.value) == Condition(
        ConditionKind.STATE, pred="card_types", op=">=", n=_n(4),
        filter=CardFilter(zone="graveyard", owner="you"), label="delirium")


def test_a_comparison_against_another_players_count_is_a_quantity_comparand():
    r = _value("if an opponent controls more lands than you")
    v = r.value
    assert (v.kind, v.pred, v.op) == (ConditionKind.STATE, "count", ">")
    assert v.filter.types == frozenset({"land"}) and v.filter.controller == "opponents"
    assert v.n.kind is AmountKind.EQUAL_TO
    q = v.n.quantity
    assert q.kind is QuantityKind.COUNT
    assert q.filter.types == frozenset({"land"}) and q.filter.controller == "you"


def test_a_life_comparison_against_another_player_is_a_quantity_comparand():
    r = _value("if an opponent has more life than you")
    v = r.value
    assert (v.kind, v.pred, v.op) == (ConditionKind.STATE, "life_total", ">")
    assert v.payer == Selector(SelectorKind.OPPONENTS)
    assert v.n.kind is AmountKind.EQUAL_TO
    assert v.n.quantity == Quantity(QuantityKind.LIFE_TOTAL, player="you",
                                    raw=v.n.quantity.raw)


@pytest.mark.parametrize("text,pred,payer,op,n", [
    ("if you have the city's blessing", "citys_blessing",
     Selector(SelectorKind.PLAYER), ">=", None),
    ("if an opponent has three or more poison counters", "poison",
     Selector(SelectorKind.OPPONENTS), ">=", _n(3)),
    ("if you have 5 or less life", "life_total",
     Selector(SelectorKind.PLAYER), "<=", _n(5)),
    ("if your life total is 5 or less", "life_total",
     Selector(SelectorKind.PLAYER), "<=", _n(5)),
    ("if you have at least 10 life", "life_total",
     Selector(SelectorKind.PLAYER), ">=", _n(10)),
])
def test_a_player_state_condition_names_the_player_whose_state_it_reads(
        text, pred, payer, op, n):
    r = _value(text)
    v = r.value
    assert (v.kind, v.pred, v.payer, v.op, v.n) == (
        ConditionKind.STATE, pred, payer, op, n), (text, v)


# ── unless ─────────────────────────────────────────────────────────────

def test_unless_pays_names_the_payer_and_reuses_the_activation_cost_parser():
    """A31: the cost is the payload leaf's PAY payload -- the activation
    cost parser's frozen snapshot -- and the payer is the participant
    leaf's; "its" is left for the linker (rule 0, A23)."""
    from engine.oracle_parser import parse_activation_cost
    host = "counter target spell unless its controller pays {2}."
    slot = (host.index("unless"), len(host))
    r = C.parse_condition(host, slot, lemma="counter")
    v = r.value
    assert v.kind is ConditionKind.UNLESS and v.pred == "pays"
    assert v.ref == Ref(RefKind.CONTROLLER_OF)
    assert v.cost == freeze_cost(parse_activation_cost("{2}"))
    assert r.pending == (("ref", "its"),)
    assert host[slice(*r.span)] == "unless its controller pays {2}"
    assert r.rest_spans == ()
    r = _value("unless you pay {1}")
    assert r.value.payer == Selector(SelectorKind.PLAYER) and r.value.ref is None


def test_an_unless_payment_hands_a_trailing_instead_on_as_rest():
    host = "unless its controller pays {4} instead"
    r = _value(host)
    assert r.rest_text(host) == "instead"
    assert host[slice(*r.span)] == "unless its controller pays {4}"


def test_an_unless_cost_scaled_by_a_quantity_is_unmodelled_not_the_bare_cost():
    r = _cond("unless its controller pays {1} for each card in your hand")
    assert r.value is None
    assert r.unmodelled.stage is Stage.CONDITION
    assert r.unmodelled.detail == "condition.unless_scaled"


def test_unless_a_board_state_is_the_negation_of_that_state():
    r = _value("unless you control a plains")
    v = r.value
    assert v.kind is ConditionKind.NOT and len(v.children) == 1
    (child,) = v.children
    assert (child.kind, child.pred, child.op, child.n) == (
        ConditionKind.STATE, "count", ">=", _ONE)
    assert child.filter.subtypes == frozenset({"plains"})
    assert child.filter.controller == "you"


# ── History: "this turn" belongs to the condition ──────────────────────

@pytest.mark.parametrize("text,pred,op,n", [
    ("if you gained life this turn", "life_gained", ">=", 1),
    ("if you've gained 3 or more life this turn", "life_gained", ">=", 3),
    ("if an opponent lost life this turn", "life_lost", ">=", 1),
    ("if a creature died this turn", "died", ">=", 1),
    ("if you've cast two or more spells this turn", "cast", ">=", 2),
    ("if you attacked this turn", "attacked", ">=", 1),
    ("if a permanent left the battlefield under your control this turn",
     "permanent_left", ">=", 1),
])
def test_this_turn_inside_a_condition_is_history_and_the_condition_consumes_it(
        text, pred, op, n):
    from engine.effect_grammar.sub.duration import parse_duration
    r = _value(text)
    v = r.value
    assert (v.kind, v.pred, v.op, v.n) == (ConditionKind.HISTORY, pred, op, _n(n))
    assert v.pred in C.HISTORY_PREDS
    assert r.span == (0, len(text)) and r.rest_spans == ()
    assert parse_duration(text) is None


def test_a_revolt_history_condition_keeps_who_controlled_the_object():
    r = _value("if a permanent left the battlefield under your control this turn")
    assert r.value.filter.controller == "you"
    assert r.value.filter.zone == ""


def test_a_negated_history_condition_is_not_of_that_history():
    r = _value("if you haven't cast a spell this turn")
    v = r.value
    assert v.kind is ConditionKind.NOT
    assert v.children[0].kind is ConditionKind.HISTORY
    assert v.children[0].pred == "cast"


# ── Objects ────────────────────────────────────────────────────────────

def test_a_pronoun_inside_a_condition_is_left_for_the_linker():
    """Rule 0 (A23): the linker binds 'it' to the spec's own target."""
    r = _value("if it has mana value 2 or less")
    v = r.value
    assert (v.kind, v.pred, v.op, v.n, v.ref) == (
        ConditionKind.OBJECT, "mana_value", "<=", _n(2), None)
    assert r.pending == (("ref", "it"),)


@pytest.mark.parametrize("text,ref,filt", [
    ("as long as ~ is untapped", Ref(RefKind.SELF),
     CardFilter(zone="", state=frozenset({"untapped"}))),
    ("if equipped creature is a vampire", Ref(RefKind.ATTACHED, noun="creature"),
     CardFilter(zone="", subtypes=frozenset({"vampire"}))),
    ("if ~ is attacking", Ref(RefKind.SELF),
     CardFilter(zone="", state=frozenset({"attacking"}))),
    ("if ~ is legendary", Ref(RefKind.SELF),
     CardFilter(zone="", supertypes=frozenset({"legendary"}))),
])
def test_an_object_condition_names_its_reference_and_a_typed_characteristic(
        text, ref, filt):
    r = _value(text)
    v = r.value
    assert (v.kind, v.pred, v.ref) == (ConditionKind.OBJECT, "is", ref)
    assert _raw_free(v.filter) == filt


def test_an_object_colour_condition_is_a_colour_filter_on_the_member():
    r = _value("if it's white")
    assert r.value.filter.colors == frozenset({"W"})
    assert r.pending == (("ref", "it"),)


def test_a_disjunction_of_colours_or_states_is_the_filters_disjunctive_field():
    r = _value("if that creature is black or red")
    assert r.value.filter.colors == frozenset({"B", "R"})
    r = _value("if ~ is attacking or blocking")
    assert r.value.filter.state == frozenset({"attacking", "blocking"})


def test_a_total_power_condition_sums_a_typed_set():
    r = _value("if creatures you control have total power 8 or greater",
               label="formidable")
    v = r.value
    assert (v.kind, v.pred, v.op, v.n) == (
        ConditionKind.STATE, "total_power", ">=", _n(8))
    assert v.filter.types == frozenset({"creature"})
    assert v.filter.controller == "you"


def test_an_object_type_condition_reads_the_card_type_without_a_zone():
    r = _value("if it's a land card")
    v = r.value
    assert (v.kind, v.pred) == (ConditionKind.OBJECT, "is")
    assert v.filter.types == frozenset({"land"}) and v.filter.zone == ""


def test_a_past_tense_object_condition_reads_last_known_information():
    """CR 608.2h: 'if it was a creature card' describes the object as it
    last existed in its previous zone."""
    r = _value("if it was a creature card")
    assert C.LKI in r.flags
    r = _value("if ~ was a creature")
    assert r.value.ref == Ref(RefKind.SELF, lki=True)
    r = _value("if it's a creature card")
    assert C.LKI not in r.flags


def test_a_counter_condition_counts_one_kind_on_the_object():
    r = _value("if ~ has three or more +1/+1 counters on it")
    v = r.value
    assert (v.kind, v.pred, v.op, v.n, v.ref) == (
        ConditionKind.OBJECT, "counters", ">=", _n(3), Ref(RefKind.SELF))
    assert v.filter.counters == (("+1/+1", True),)
    r = _value("if there are two or more ki counters on ~")
    assert (r.value.pred, r.value.op, r.value.n, r.value.ref) == (
        "counters", ">=", _n(2), Ref(RefKind.SELF))


def test_an_object_stat_condition_reads_the_possessed_stat():
    r = _value("if its power is 4 or greater")
    assert (r.value.pred, r.value.op, r.value.n) == ("power", ">=", _n(4))
    assert r.pending == (("ref", "its"),)


# ── Turn, cast facts, resolution ordinal ───────────────────────────────

@pytest.mark.parametrize("text,pred", [
    ("if it's your turn", "your_turn"),
    ("if it's not your turn", "not_your_turn"),
    ("if it isn't your turn", "not_your_turn"),
])
def test_a_turn_condition_is_the_closed_turn_table(text, pred):
    """A2: an alternative cost's 'if it's not your turn' is TURN
    not_your_turn."""
    r = _value(text)
    assert _raw_free(r.value) == Condition(ConditionKind.TURN, pred=pred)


def test_cast_facts_read_how_the_spell_was_cast():
    from engine.oracle_parser import parse_activation_cost
    r = _value("if ~ was kicked")
    assert (r.value.kind, r.value.pred, r.value.ref) == (
        ConditionKind.CAST_FACT, "kicked", Ref(RefKind.SELF))
    r = _value("if {g}{g} was spent to cast it")
    assert (r.value.kind, r.value.pred) == (ConditionKind.CAST_FACT, "mana_spent")
    assert r.value.cost == freeze_cost(parse_activation_cost("{g}{g}"))
    assert r.pending == (("ref", "it"),)
    r = _value("if no mana was spent to cast it")
    assert (r.value.pred, r.value.op, r.value.n) == ("mana_spent_total", "==", _n(0))
    r = _value("if ~'s additional cost was paid")
    assert r.value.pred == "additional_cost_paid"
    r = _value("if x is 5 or more")
    assert (r.value.pred, r.value.op, r.value.n) == ("x", ">=", _n(5))
    r = _value("if it's bargained")
    assert r.value.pred == "bargained"


@pytest.mark.parametrize("text,detail", [
    ("if tribute wasn't paid", "condition.keyword_fact:tribute"),
    ("if its madness cost was paid", "condition.keyword_fact:madness"),
    ("if ~ is suspended", "condition.keyword_fact:suspended"),
])
def test_a_cast_mechanic_with_no_fact_in_the_table_is_refused_by_name(text, detail):
    r = _cond(text)
    assert r.value is None and r.unmodelled.detail == detail


def test_a_leading_conditions_refusal_is_its_own_phrase_not_the_sentence():
    """The census groups by the condition the leaf refused, so the detail
    is the comma-bounded condition's, not the whole sentence's."""
    host = "if tribute wasn't paid, it gets +1/+1 and gains haste"
    r = C.parse_condition(host, (0, len(host)), lemma="x")
    assert r.unmodelled.detail == "condition.keyword_fact:tribute"
    assert r.span == (0, len(host))


def test_a_resolution_ordinal_counts_this_abilitys_resolutions_this_turn():
    r = _value("if this is the second time this ability has resolved this turn")
    assert _raw_free(r.value) == Condition(
        ConditionKind.RESOLUTION_ORDINAL, pred="resolved", op="==", n=_n(2))


# ── Connectives ────────────────────────────────────────────────────────

def test_conjoined_and_disjoined_conditions_are_all_of_and_any_of():
    r = _value("if you control a desert or there is a desert card in your graveyard")
    v = r.value
    assert v.kind is ConditionKind.ANY_OF and len(v.children) == 2
    assert [c.filter.zone for c in v.children] == ["battlefield", "graveyard"]
    r = _value("if you control an artifact and an enchantment")
    v = r.value
    assert v.kind is ConditionKind.ALL_OF
    assert [c.filter.types for c in v.children] == [
        frozenset({"artifact"}), frozenset({"enchantment"})]
    assert all(c.filter.controller == "you" for c in v.children)
    r = _value("unless you control a plains or an island")
    v = r.value
    assert v.kind is ConditionKind.NOT
    assert v.children[0].kind is ConditionKind.ANY_OF
    assert [c.filter.subtypes for c in v.children[0].children] == [
        frozenset({"plains"}), frozenset({"island"})]


@pytest.mark.parametrize("text", [
    "if you do", "if you don't", "if they do", "if a player does",
    "if you don't put the card into your hand",
    "if that spell is countered this way",
    "if you search your library this way",
    "if a creature would die this turn",
    "if able",
    "for as long as ~ remains on the battlefield",
])
def test_structure_owned_connectives_are_never_conditions(text):
    """Section 6: performed-gating is structural, 'countered this way' is a
    destination override, replacement 'would' is a replacement, 'if able'
    is a requirement rider and 'for as long as' is the duration leaf's."""
    assert C.parse_condition(text, (0, len(text))) is None


def test_a_leading_condition_hands_the_clause_after_its_comma_on_as_rest():
    host = "if you control an artifact, draw a card."
    r = C.parse_condition(host, (0, len(host)), lemma="draw")
    assert host[slice(*r.span)] == "if you control an artifact"
    assert r.rest_text(host) == "draw a card"


def test_every_returned_span_indexes_the_host():
    host = "draw a card if you control an artifact. then scry 1."
    slot = (host.index("if"), host.index("."))
    r = C.parse_condition(host, slot, lemma="draw")
    assert host[slice(*r.span)] == "if you control an artifact"
    assert r.value.raw == "if you control an artifact"


def test_an_ability_word_is_a_label_on_the_condition_not_a_condition():
    """CR 207.2c: the label is informational; the predicate is the
    printed text's."""
    plain = _value("if you control three or more artifacts").value
    labelled = _value("if you control three or more artifacts",
                      label="metalcraft").value
    assert labelled.label == "metalcraft"
    assert dataclasses.replace(labelled, label="") == plain


# ── Refusals ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,detail", [
    ("if ~ is in your opening hand", "condition.opening_hand"),
    ("if you win the flip", "condition.coin_flip"),
    ("if it targets a blue spell", "condition.targets"),
    ("if blorp the zorp", "condition.unparsed:blorp"),
    ("unless you discard a card", "condition.unless_action:discard"),
    ("if no spells were cast last turn", "condition.last_turn"),
])
def test_an_unknown_condition_is_unmodelled_condition_over_the_whole_slot(text, detail):
    r = C.parse_condition(text, (0, len(text)), lemma="draw")
    assert r.value is None
    assert r.unmodelled.stage is Stage.CONDITION
    assert r.unmodelled.lemma == "draw"
    assert r.unmodelled.detail == detail
    assert r.span == (0, len(text))


def test_a_refused_conjunct_refuses_the_whole_condition():
    """Never a broader condition: dropping the unknown half would gate on
    less than the printed rule."""
    r = _cond("if you control an artifact and ~ is in your opening hand")
    assert r.value is None and r.unmodelled.stage is Stage.CONDITION


# ── The leaf contract ──────────────────────────────────────────────────

def test_every_typed_condition_uses_the_closed_predicate_and_operator_tables():
    for text in ("if you control three or more artifacts", "if it's your turn",
                 "unless its controller pays {2}", "if ~ was kicked",
                 "if you gained life this turn", "if it has mana value 2 or less",
                 "unless you control a plains or an island"):
        stack = [_value(text).value]
        while stack:
            c = stack.pop()
            assert c.pred in C.PREDICATES[c.kind], (text, c)
            assert c.op in C.OPS, (text, c)
            stack.extend(c.children)


def test_the_condition_leaf_imports_other_leaves_only_along_its_declared_edges():
    from engine.effect_grammar.sub import LEAF_EDGES
    out = set()
    for node in ast.walk(ast.parse((SUB / "condition.py").read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("engine.effect_grammar.sub."):
                out.add(node.module.rsplit(".", 1)[1])
            elif node.module == "engine.effect_grammar.sub":
                out.update(a.name for a in node.names
                           if (SUB / (a.name + ".py")).exists())
    assert out == set(LEAF_EDGES["condition"])


def test_the_condition_leaf_caches_are_bounded_and_cleared_by_the_package():
    from engine.effect_grammar import clear_caches, sub
    caches = [a for a in vars(C).values()
              if callable(a) and hasattr(a, "cache_info")
              and getattr(a, "__module__", "") == C.__name__]
    assert caches
    assert all(c.cache_info().maxsize == sub.CACHE_SIZE for c in caches)
    _value("if you control an artifact")
    assert any(c.cache_info().currsize for c in caches)
    clear_caches()
    assert all(c.cache_info().currsize == 0 for c in caches)


def test_every_condition_detail_is_leaf_dot_code_from_the_closed_list():
    for text in ("if blorp", "if ~ is in your opening hand",
                 "unless its controller pays {1} for each card in your hand",
                 "unless you sacrifice a land"):
        u = _cond(text).unmodelled
        m = re.match(r"^condition\.(?P<code>[a-z_]+)(?::\S+)?$", u.detail)
        assert m and m.group("code") in C.DETAIL_CODES, u.detail


def test_the_condition_leaf_reads_l0_output_without_re_normalising():
    src = (SUB / "condition.py").read_text()
    assert "’" not in src
    assert ".lower()" not in src
