"""The amount sub-grammar (design doc 2026-09-29, section 2 ``Amount``,
section 6 "Amount"; A12, A16, A19, A35; E0 step 11).

An amount is how many: the count of a counted noun ("3 damage", "two
cards", "that many cards plus one"), a trailing scaler ("for each <Q>",
"equal to <Q>", "divided as you choose among"), the definition of X
("where x is <Q>"), or a leading "for each <Q>," frame (A16). It is a
closed table over L0 output that runs at load, never at resolution, under
the one leaf contract (`engine.effect_grammar.sub`). These tests pin the
rules:

* every printed count form has one typed encoding (words, digits, "1,000",
  X, "that many", multipliers, halves with their rounding, "plus N", "any
  number of", "any amount of", "up to N", divided damage);
* X is bound from the cost (``x_bound``: a {X} mana cost or a loyalty X)
  or from "where x is"; an unbound X is UNMODELLED(AMOUNT), never zero;
* "for each <Q>" and "equal to <Q>" read the quantity leaf; a quantity it
  cannot count makes the amount UNMODELLED(QUANTITY), never zero;
* a leading "for each <Q>," is a FOR_EACH amount unless the body refers to
  the element, which is iteration (A16);
* mana is a symbol multiset (the payload leaf's), never an amount.

Synthetic phrases only; no card names.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from engine.effect_spec import (Amount, AmountKind, Quantity, QuantityKind,
                                Ref, RefKind, Stage)
from engine.effect_grammar.sub import amount as A

REPO = Path(__file__).resolve().parent.parent
LEAF_PATH = REPO / "engine" / "effect_grammar" / "sub" / "amount.py"

_X = Amount(AmountKind.X, n=1)
_THAT = Amount(AmountKind.THAT_MUCH)


def _lit(n):
    return Amount(AmountKind.LITERAL, n=n)


def _count(text, **kw):
    return A.parse_amount(text, (0, len(text)), lemma="deal", **kw)


def _scaler(text, **kw):
    return A.parse_scaler(text, (0, len(text)), lemma="deal", **kw)


def _strip_raw(a):
    """An Amount with every Quantity's raw (and its filter's raw) blanked,
    so a test compares the rule, not the echoed text."""
    import dataclasses
    if a is None:
        return None
    q = a.quantity
    if q is not None:
        f = q.filter
        if f is not None:
            f = dataclasses.replace(f, raw="")
        q = dataclasses.replace(q, raw="", filter=f)
    return dataclasses.replace(a, quantity=q, inner=_strip_raw(a.inner))


# ── Count forms ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, value, consumed, rest", [
    ("3 damage to any target", _lit(3), "3", "damage to any target"),
    ("two cards", _lit(2), "two", "cards"),
    ("a card", _lit(1), "a", "card"),
    ("an elf card", _lit(1), "an", "elf card"),
    ("1,000 life", _lit(1000), "1,000", "life"),
    ("twenty cards", _lit(20), "twenty", "cards"),
    ("that many cards", _THAT, "that many", "cards"),
    ("that much damage to any target", _THAT, "that much", "damage to any target"),
    ("twice that many cards", Amount(AmountKind.MULTIPLY, n=2, inner=_THAT),
     "twice that many", "cards"),
    ("double that damage to that creature",
     Amount(AmountKind.MULTIPLY, n=2, inner=_THAT), "double that",
     "damage to that creature"),
    ("triple that damage", Amount(AmountKind.MULTIPLY, n=3, inner=_THAT),
     "triple that", "damage"),
    ("up to two land cards", Amount(AmountKind.UP_TO, n=2), "up to two",
     "land cards"),
    ("up to that many land cards", Amount(AmountKind.UP_TO, inner=_THAT),
     "up to that many", "land cards"),
    ("any number of lands", Amount(AmountKind.ANY_NUMBER), "any number of",
     "lands"),
    ("any amount of {e}", Amount(AmountKind.ANY_NUMBER), "any amount of",
     "{e}"),
    ("all the cards in your hand", Amount(AmountKind.ALL), "all",
     "the cards in your hand"),
])
def test_amount_forms_parse_to_typed_amounts(text, value, consumed, rest):
    """Words, digits (with a thousands comma), that many / that much,
    multipliers, up to, any number of and any amount of (A19): each printed
    form is one typed Amount, the count is consumed and the counted noun is
    handed on as host spans."""
    r = _count(text)
    assert r.value == value, r
    assert r.unmodelled is None
    assert text[slice(*r.span)] == consumed
    assert r.rest_text(text) == rest


def test_an_additional_count_is_a_literal_marked_additional():
    r = _count("an additional card")
    assert r.value == _lit(1) and A.ADDITIONAL in r.flags
    assert r.rest_text("an additional card") == "card"
    r = _count("two additional cards")
    assert r.value == _lit(2) and A.ADDITIONAL in r.flags


def test_x_is_bound_from_the_cost_or_from_where_x_is_and_otherwise_unmodelled():
    """Section 6: X is bound from the loyalty cost paid, from {X} in a mana
    cost, or from 'where x is'; otherwise the clause is UNMODELLED(AMOUNT)
    -- never zero."""
    r = _count("x damage to any target", x_bound=True)
    assert r.value == _X and r.rest_text("x damage to any target") == "damage to any target"
    defined = A.parse_where_x("where x is the number of artifacts you control",
                              (0, 46)).value
    assert defined.kind is AmountKind.X_DEFINED
    r = _count("x damage to any target", x_defined=defined)
    assert r.value == defined
    r = _count("x damage to any target")
    assert r.value is None
    assert r.unmodelled.stage is Stage.AMOUNT
    assert r.unmodelled.detail == "amount.x_unbound"
    assert r.unmodelled.lemma == "deal"


def test_a_where_x_definition_wraps_its_defining_expression():
    host = "creatures you control get +x/+x, where x is the number of creatures you control"
    a = host.index(", where")
    r = A.parse_where_x(host, (a, len(host)), lemma="get")
    assert r.value.kind is AmountKind.X_DEFINED
    assert r.value.inner.kind is AmountKind.EQUAL_TO
    assert r.value.inner.quantity.kind is QuantityKind.COUNT
    assert host[slice(*r.span)] == "where x is the number of creatures you control"
    # An operator is part of the definition.
    text = "where x is twice the number of cards in your hand"
    r = A.parse_where_x(text, (0, len(text)))
    assert r.value.inner.kind is AmountKind.MULTIPLY and r.value.inner.n == 2
    assert r.value.inner.inner.quantity.kind is QuantityKind.CARDS_IN
    text = "where x is 1 plus the number of lands you control"
    r = A.parse_where_x(text, (0, len(text)))
    assert (r.value.inner.kind, r.value.inner.n) == (AmountKind.PLUS, 1)


@pytest.mark.parametrize("text, value, rest", [
    ("twice x damage to any target", Amount(AmountKind.MULTIPLY, n=2, inner=_X),
     "damage to any target"),
    ("three times x cards", Amount(AmountKind.MULTIPLY, n=3, inner=_X), "cards"),
    ("half x cards, rounded down",
     Amount(AmountKind.HALF, inner=_X, rounding="down"), "cards"),
    ("half x cards, rounded up",
     Amount(AmountKind.HALF, inner=_X, rounding="up"), "cards"),
    ("half that damage, rounded down",
     Amount(AmountKind.HALF, inner=_THAT, rounding="down"), "damage"),
    ("that many cards plus one", Amount(AmountKind.PLUS, n=1, inner=_THAT),
     "cards"),
    ("that much damage plus 2 instead",
     Amount(AmountKind.PLUS, n=2, inner=_THAT), "damage instead"),
    ("that many -1/-1 counters minus one",
     Amount(AmountKind.PLUS, n=-1, inner=_THAT), "-1/-1 counters"),
])
def test_an_operator_on_a_count_wraps_the_count_it_scales(text, value, rest):
    """Twice / three times / half (with its printed rounding) / plus N /
    minus N wrap the count they scale; a counted noun the phrase brackets
    ('half x cards, rounded down') stays in the rest."""
    r = _count(text, x_bound=True)
    assert r.value == value, r
    assert r.rest_text(text) == rest
    # The rest never overlaps a consumed operator word.
    for a, b in r.rest_spans:
        assert "rounded" not in text[a:b] and "plus" not in text[a:b]


def test_half_a_life_total_is_half_of_the_players_life_total():
    r = _count("half their life, rounded up")
    assert r.value.kind is AmountKind.HALF and r.value.rounding == "up"
    assert r.value.inner.kind is AmountKind.EQUAL_TO
    q = r.value.inner.quantity
    assert q.kind is QuantityKind.LIFE_TOTAL
    assert ("player", "their") in r.pending
    assert r.rest_spans == ()
    r = _count("half your life, rounded up")
    assert r.value.inner.quantity.player == "you" and r.pending == ()


def test_half_a_counted_set_reads_the_set_through_the_quantity_leaf():
    text = "half the cards in their hand, rounded down"
    r = _count(text)
    assert r.value.kind is AmountKind.HALF and r.value.rounding == "down"
    assert r.value.inner.quantity.kind is QuantityKind.CARDS_IN
    assert r.rest_spans == ()
    assert text[slice(*r.span)] == text


def test_a_rounding_belongs_to_the_half_of_its_own_clause():
    """A later clause's ', rounded up' never rounds an earlier half."""
    text = "half the cards in their hand, then loses half their life, rounded up"
    r = _count(text)
    assert r.value.kind is AmountKind.HALF and r.value.rounding is None
    assert text[slice(*r.span)] == "half the cards in their hand"
    assert r.rest_text(text) == "then loses half their life, rounded up"


@pytest.mark.parametrize("text, code", [
    ("", "empty"),
    ("blorp cards", "no_count"),
    ("your second card each turn", "ordinal"),
    ("{2}{r}", "mana_symbols"),
    ("2ˣ cards", "exponent"),
    ("exactly 1 life", "exactly"),
    ("half of their library", "operand"),
    ("that much damage plus x", "operator"),
    # A second counted amount, not an operator on the first.
    ("2 life plus 2 life for each creature you control", "sum"),
])
def test_a_count_the_closed_table_lacks_is_unmodelled_amount(text, code):
    r = _count(text)
    assert r.value is None
    assert r.unmodelled.stage is Stage.AMOUNT
    assert r.unmodelled.detail.split(":")[0] == "amount." + code
    assert r.span == (0, len(text.strip()))


@pytest.mark.parametrize("text, kw", [
    ("6 or more damage", {}),
    ("three or fewer cards", {}),
    ("one or more cards", {}),
    ("x or more cards", {"x_bound": True}),
    ("2 or less", {}),
    ("7 or greater", {}),
    ("up to two or more cards", {}),
])
def test_a_threshold_count_is_no_amount(text, kw):
    """"N or more" / "N or fewer" is a threshold a condition compares
    against, not how many: the count is refused (one comparator family with
    "exactly N"), never typed as N."""
    r = _count(text, **kw)
    assert r.value is None, r
    assert r.unmodelled.stage is Stage.AMOUNT
    assert r.unmodelled.detail == "amount.comparison"
    assert r.span == (0, len(text))


@pytest.mark.parametrize("text", [
    "one, two, or three cards", "one or two cards", "1 or 2 damage",
    "two, three, or four target creatures",
])
def test_a_choice_list_of_counts_is_no_single_amount(text):
    """A printed list of counts to choose from is a choice, not its first
    count: refused, never typed as the first number."""
    r = _count(text)
    assert r.value is None, r
    assert r.unmodelled.detail == "amount.choice_list"


@pytest.mark.parametrize("text, n", [
    ("twenty-five cards", 25), ("twenty-one cards", 21),
])
def test_a_hyphenated_number_word_is_one_count(text, n):
    """A compound number word is one count, never its first half."""
    r = _count(text)
    assert r.value == _lit(n), r
    assert r.rest_text(text) == "cards"


@pytest.mark.parametrize("text", [
    "twenty-blorp cards", "thirty-two cards", "two-headed giants",
])
def test_a_number_word_glued_to_a_hyphen_is_no_count(text):
    """A number word followed by a hyphen the table cannot read is never
    truncated to the number word."""
    r = _count(text)
    assert r.value is None, r
    assert r.unmodelled.detail.split(":")[0] == "amount.no_count"


def test_mana_is_a_symbol_multiset_not_an_amount():
    """CR 106: '{2}{r}' is the payload leaf's symbol multiset; the amount
    leaf never reads it as a number."""
    for text in ("{2}{r}", "{x}", "{c}{c} to your mana pool"):
        r = _count(text)
        assert r.value is None
        assert r.unmodelled.detail == "amount.mana_symbols", text


def test_a_number_of_defers_the_count_to_the_trailing_scaler():
    """'a number of <noun> equal to <Q>': the printed count is the scaler's,
    so the count slot holds no amount of its own (SCALED) and the scaler
    reads it."""
    text = "a number of cards equal to the number of creatures you control"
    r = _count(text)
    assert r.value is None and r.unmodelled is None
    assert A.SCALED in r.flags
    assert text[slice(*r.span)] == "a number of"
    s = A.parse_scaler(text, (text.index("equal"), len(text)))
    assert s.value.kind is AmountKind.EQUAL_TO


# ── Trailing scalers ───────────────────────────────────────────────────

def test_for_each_counts_the_quantity_per_printed_unit():
    host = "draw two cards for each artifact you control"
    a = host.index("for each")
    r = A.parse_scaler(host, (a, len(host)), lemma="draw", per=_lit(2))
    assert r.value.kind is AmountKind.FOR_EACH and r.value.n == 2
    assert r.value.quantity.kind is QuantityKind.COUNT
    assert host[slice(*r.span)] == "for each artifact you control"
    r = A.parse_scaler(host, (a, len(host)))
    assert r.value.n == 1


def test_a_for_each_on_a_variable_count_is_unmodelled():
    host = "draw x cards for each artifact you control"
    a = host.index("for each")
    r = A.parse_scaler(host, (a, len(host)), per=_X)
    assert r.value is None and r.unmodelled.detail == "amount.for_each_base"


@pytest.mark.parametrize("text, outer, rest", [
    ("equal to its power to any target", AmountKind.EQUAL_TO, "to any target"),
    ("equal to twice the number of creatures you control to target creature",
     AmountKind.MULTIPLY, "to target creature"),
    ("equal to half the number of cards in your hand, rounded up",
     AmountKind.HALF, ""),
    ("equal to 1 plus the number of lands you control", AmountKind.PLUS, ""),
    ("equal to the number of cards in your hand plus 1", AmountKind.PLUS, ""),
    ("equal to that much", AmountKind.THAT_MUCH, ""),
    ("equal to the damage dealt this way", AmountKind.THAT_MUCH, ""),
])
def test_equal_to_reads_its_operand_and_operators(text, outer, rest):
    r = _scaler(text, x_bound=True)
    assert r.value is not None, r
    assert r.value.kind is outer
    assert r.rest_text(text) == rest


_RESULT = Amount(AmountKind.THAT_MUCH, ref=Ref(RefKind.RESULT))


@pytest.mark.parametrize("text, event, noun", [
    ("equal to the damage dealt this way", "dealt", "damage"),
    ("equal to the damage dealt to that creature this way", "dealt", "damage"),
    ("equal to the life lost this way", "lost", "life"),
    ("equal to the life gained this way", "gained", "life"),
    ("equal to the damage prevented this way", "prevented", "damage"),
    ("equal to the amount of {e} paid this way", "paid", "{e}"),
    ("equal to the excess damage dealt this way", "dealt", "excess damage"),
])
def test_a_result_amount_is_that_much_bound_to_the_result_it_names(text, event, noun):
    """A result amount named outright ("the damage dealt this way") is
    THAT_MUCH bound to an earlier spec's result (Ref(RESULT)), and the event
    it names is left in ``pending`` so the linker binds it to the spec that
    produced it; a bare "that much" carries no such binding."""
    r = _scaler(text)
    assert r.value == _RESULT, r
    assert ("result", event) in r.pending and ("result_of", noun) in r.pending
    assert _count("that much life").value == _THAT != _RESULT


def test_half_a_result_amount_is_half_of_the_same_result_encoding():
    """One rule, one encoding: "half the damage dealt this way" halves the
    same result amount "equal to the damage dealt this way" names."""
    text = "half the damage dealt this way, rounded up"
    r = _count(text)
    assert r.value == Amount(AmountKind.HALF, inner=_RESULT, rounding="up"), r
    assert ("result", "dealt") in r.pending
    s = _scaler("equal to half the damage dealt this way, rounded down")
    assert s.value == Amount(AmountKind.HALF, inner=_RESULT, rounding="down"), s


@pytest.mark.parametrize("text", [
    "equal to the damage", "equal to that damage", "equal to the life",
])
def test_a_named_amount_without_its_result_event_is_unmodelled(text):
    """"the damage" with no "this way" is the triggering event's damage, not
    an earlier spec's result; the leaf has no event binding, so it refuses
    rather than collapse it into the result encoding."""
    r = _scaler(text)
    assert r.value is None
    assert r.unmodelled.stage is Stage.AMOUNT
    assert r.unmodelled.detail == "amount.result_event"


def test_equal_to_a_quantity_keeps_its_references_for_the_linker():
    r = _scaler("equal to that creature's power")
    assert r.value.quantity.kind is QuantityKind.POWER
    assert ("ref", "that creature") in r.pending


def test_a_quantity_on_a_source_that_left_as_a_cost_reads_last_known_information():
    """A27, CR 608.2h: the amount leaf hands source_left to the quantity
    leaf, so a SELF quantity reads last-known information."""
    r = _scaler("equal to the number of charge counters on ~", source_left=True)
    assert r.value.quantity.ref == Ref(RefKind.SELF, lki=True)
    r = _scaler("equal to the number of charge counters on ~")
    assert r.value.quantity.ref == Ref(RefKind.SELF)


def test_divided_damage_wraps_the_printed_total_and_hands_on_the_targets():
    """CR 601.2d: the division is chosen with the targets; the amount
    records the total and the evenly / rounding rule, the targets are the
    target leaf's."""
    host = "~ deals 4 damage divided as you choose among any number of targets"
    a = host.index("divided")
    r = A.parse_scaler(host, (a, len(host)), per=_lit(4))
    assert r.value == Amount(AmountKind.DIVIDED, inner=_lit(4))
    assert r.rest_text(host) == "among any number of targets"
    text = "divided evenly, rounded down, among any number of target creatures"
    r = A.parse_scaler(text, (0, len(text)), per=_X)
    assert r.value == Amount(AmountKind.DIVIDED, inner=_X, evenly=True,
                             rounding="down")
    r = A.parse_scaler(text, (0, len(text)))
    assert r.unmodelled.detail == "amount.divided_base"
    text = "divided as its controller chooses among any number of creatures"
    r = A.parse_scaler(text, (0, len(text)), per=_lit(3))
    assert r.unmodelled.detail == "amount.divided_chooser"


@pytest.mark.parametrize("text, code", [
    ("equal to the number of creatures you control plus the number of lands you control",
     "amount.sum"),
    ("equal to x", "amount.x_unbound"),
    ("blorp", "amount.scaler"),
])
def test_a_scaler_the_closed_table_lacks_is_unmodelled(text, code):
    r = _scaler(text)
    assert r.value is None and r.unmodelled.stage is Stage.AMOUNT
    assert r.unmodelled.detail.split(":")[0] == code


@pytest.mark.parametrize("text, code", [
    ("equal to the difference", "quantity.anaphoric_number"),
    ("equal to the amount of mana spent to cast ~", "quantity.mana_spent"),
    ("for each creature in your party", "quantity.party"),
    ("equal to its mana cost", "quantity.mana_cost"),
])
def test_an_uncountable_quantity_makes_the_clause_unmodelled_never_zero(text, code):
    """Section 6: an unknown quantity is UNMODELLED(QUANTITY) (the deepest
    failure), never a zero amount; the failure span is the whole slot and
    the lemma is the caller's."""
    r = _scaler(text)
    assert r.value is None
    assert r.unmodelled.stage is Stage.QUANTITY
    assert r.unmodelled.detail.split(":")[0] == code
    assert r.unmodelled.lemma == "deal"
    assert r.span == (0, len(text))


# ── A16: the leading "for each" frame ──────────────────────────────────

def test_a_leading_for_each_without_an_element_anaphor_is_a_for_each_amount_and_with_one_is_iteration():
    """A16: 'for each <Q>, <counted VP>' is FOR_EACH(Q) on the counted verb
    when the body has no anaphor to the element; when it refers to the
    element ('a copy of it', 'of that type') it is UNMODELLED(ITERATION)."""
    host = ("for each nonland card discarded this way, create a 1/1 red "
            "elemental creature token")
    r = A.parse_leading_for_each(host, lemma="create")
    assert r.value.kind is AmountKind.FOR_EACH and r.value.n == 1
    assert r.value.quantity.kind is QuantityKind.RESULT_SIZE
    assert host[slice(*r.span)] == "for each nonland card discarded this way,"
    assert r.rest_text(host) == "create a 1/1 red elemental creature token"
    for host in ("for each card type, you may put a card of that type into your hand",
                 "for each token you control, create a token that's a copy of it",
                 "for each opponent, that player loses 1 life"):
        r = A.parse_leading_for_each(host, lemma="put")
        assert r.value is None, host
        assert r.unmodelled.stage is Stage.ITERATION
        assert r.unmodelled.detail == "amount.element_anaphor"
        assert r.unmodelled.lemma == "put"
        assert r.span == (0, len(host))


def test_a_leading_for_each_reads_a_comma_inside_its_quantity():
    host = "for each artifact, creature, and land you control, draw a card"
    r = A.parse_leading_for_each(host)
    assert r.value is not None, r
    assert r.rest_text(host) == "draw a card"


def test_a_sentence_that_does_not_open_with_for_each_is_no_leading_frame():
    assert A.parse_leading_for_each("draw a card for each artifact you control") is None


# ── The leaf contract ──────────────────────────────────────────────────

def test_every_returned_span_indexes_the_host():
    host = "you gain life. ~ deals 3 damage to any target."
    a = host.index("3")
    r = A.parse_amount(host, (a, len(host) - 1), lemma="deal")
    assert host[slice(*r.span)] == "3"
    assert r.rest_text(host) == "damage to any target"
    assert all(a <= x <= y <= len(host) - 1 for x, y in r.rest_spans)


def test_every_closed_detail_code_is_one_the_leaf_emits():
    """The census buckets are the leaf's refusals; a code no phrase reaches
    would claim an always-empty bucket."""
    emitted = set()
    for text in ("", "blorp cards", "your second card", "{2}", "2ˣ cards",
                 "exactly 1 life", "half of their library",
                 "that much damage plus x", "x damage", "6 or more damage",
                 "one, two, or three cards"):
        emitted.add(_count(text).unmodelled.detail.split(".")[1].split(":")[0])
    for text, per in (("blorp", None),
                      ("equal to the number of creatures you control plus the "
                       "number of lands you control", None),
                      ("for each artifact you control", _X),
                      ("equal to the damage", None),
                      ("equal to the number of artifacts you control", _lit(2)),
                      ("divided as you choose among any number of targets", None),
                      ("divided as its controller chooses among two creatures",
                       _lit(2))):
        emitted.add(_scaler(text, per=per).unmodelled.detail.split(".")[1].split(":")[0])
    emitted.add(A.parse_where_x("where x is", (0, 10)).unmodelled.detail.split(".")[1])
    emitted.add(A.parse_leading_for_each(
        "for each opponent, it deals 1 damage").unmodelled.detail.split(".")[1])
    emitted.add(A.parse_leading_for_each(
        "for each artifact you control draw a card").unmodelled.detail.split(".")[1])
    assert emitted == set(A.DETAIL_CODES)


def test_the_amount_leaf_imports_only_its_declared_edges():
    from engine.effect_grammar.sub import LEAF_EDGES
    out = set()
    for node in ast.walk(ast.parse(LEAF_PATH.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("engine.effect_grammar.sub."):
                out.add(node.module.rsplit(".", 1)[1])
            elif node.module == "engine.effect_grammar.sub":
                out.update(a.name for a in node.names
                           if (LEAF_PATH.parent / (a.name + ".py")).exists())
    assert out == set(LEAF_EDGES["amount"]) == {"quantity"}


def test_the_amount_leaf_holds_no_memo_and_keeps_the_contracts_clear_hook():
    """A pool pass repeats almost no amount slot, so the leaf memoises
    nothing (no memory held until the load driver clears); it still
    exposes the contract's clear_caches."""
    caches = [a for a in vars(A).values()
              if callable(a) and hasattr(a, "cache_info")
              and getattr(a, "__module__", "") == A.__name__]
    assert caches == []
    assert callable(A.clear_caches)
    A.clear_caches()


def test_the_amount_leaf_does_not_re_normalise_l0_output():
    src = LEAF_PATH.read_text()
    assert "’" not in src and ".lower()" not in src
    assert A.LEAF == "amount"


def test_the_amount_values_are_hashable_and_frozen():
    """The memo caches and canonical() need value types; a quantity-bearing
    amount is one."""
    r = _scaler("for each artifact you control")
    hash(r.value)
    assert isinstance(r.value.quantity, Quantity)
    assert _strip_raw(r.value).quantity.raw == ""
