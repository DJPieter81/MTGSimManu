"""Every activation-cost pattern actually matches the cost text it names.

A cost the parser fails to recognise lands in `unpayable` as `unrecognised`,
which makes the ability visible-but-refused. That is the correct behaviour for a
genuinely unsupported cost — but it is indistinguishable from a BROKEN pattern,
so a corrupted regex degrades silently into "this cost is unsupported" and
nothing fails.

That is exactly what happened: six of the eleven patterns were written through a
non-raw Python string literal, which converted the intended `\\b` word-boundary
into `\\x08` (an actual backspace character). The file still *displays* as
`r'sacrifice this'` because a backspace is invisible in terminal output, so
reading the source did not reveal it. 2,024 abilities — including ~900
sacrifice costs — were classified `unrecognised` as a result.

Two rules under test:
  1. No pattern contains a control character. This catches the escape-damage
     class directly, whatever regex it corrupts.
  2. Each pattern matches a representative cost phrase it is named for. This
     catches a pattern that is well-formed but wrong.
"""
from __future__ import annotations

import re

import pytest

from engine.oracle_parser import _UNPAYABLE_COST_PATTERNS, parse_activation_cost

# (cost phrase, the unpayable name it must be classified as).
# NOTE: sacrifice-self and pay-life graduated to STRUCTURED payable fields in
# tranche 2; single-victim typed sacrifice and plain discard-N graduated in
# tranche 3 — all asserted separately below, not via this table. What remains
# on the sacrifice/discard patterns is the residue the structured parse
# deliberately rejects.
REPRESENTATIVE_COSTS = [
    ("Sacrifice two creatures", "sacrifice"),
    ("Sacrifice an artifact or land", "sacrifice"),
    ("Discard a card at random", "discard"),
    ("Discard a creature card", "discard"),
    ("Exile this card from your graveyard", "exile"),
    ("Return a land you control to its owner's hand", "return"),
    ("Reveal a card from your hand", "reveal"),
]


@pytest.mark.parametrize("phrase", [
    "Sacrifice this creature", "Sacrifice this artifact",
    "Sacrifice this land",
])
def test_sacrifice_self_is_a_structured_payable_field(phrase):
    """Tranche 2: sacrifice-self is charged, not refused."""
    cost = parse_activation_cost(phrase)
    assert cost is not None and cost.sacrifice_self is True
    assert "unrecognised" not in cost.unpayable, (
        f"{phrase!r} must never fall through to 'unrecognised'")


def test_pay_life_is_a_structured_payable_field():
    cost = parse_activation_cost("Pay 3 life")
    assert cost is not None and cost.life == 3
    assert cost.unpayable == ()


@pytest.mark.parametrize("phrase,type_word,another", [
    ("Sacrifice a creature", "creature", False),
    ("Sacrifice another creature", "creature", True),
    ("Sacrifice an artifact", "artifact", False),
    ("Sacrifice a land", "land", False),
    ("Sacrifice a permanent", "permanent", False),
])
def test_single_victim_sacrifice_is_a_structured_payable_field(
        phrase, type_word, another):
    """Tranche 3: a single-victim typed sacrifice is charged, not refused."""
    cost = parse_activation_cost(phrase)
    assert cost is not None and cost.sacrifice_type == type_word
    assert cost.sacrifice_another is another
    assert cost.unpayable == (), (
        f"{phrase!r} must be fully payable, got {cost.unpayable}")


def test_plain_discard_is_a_structured_payable_field():
    """Tranche 3: an untyped, non-random discard-N is charged, not refused."""
    cost = parse_activation_cost("Discard a card")
    assert cost is not None and cost.discard_cards == 1
    assert cost.unpayable == ()
    cost = parse_activation_cost("Discard two cards")
    assert cost is not None and cost.discard_cards == 2
    assert cost.unpayable == ()


def test_no_pattern_contains_a_control_character():
    """Escape damage: `\\b` written through a non-raw string becomes `\\x08`."""
    offenders = [
        (name, repr(pat)) for name, pat in _UNPAYABLE_COST_PATTERNS
        if any(ord(ch) < 32 for ch in pat)
    ]
    assert not offenders, (
        f"activation-cost patterns must not contain control characters — a "
        f"literal backspace is invisible when the file is read, so the regex "
        f"looks correct and silently never matches: {offenders}")


def test_every_pattern_compiles():
    for name, pat in _UNPAYABLE_COST_PATTERNS:
        try:
            re.compile(pat)
        except re.error as exc:  # pragma: no cover - failure path
            pytest.fail(f"pattern {name!r} does not compile: {exc}")


@pytest.mark.parametrize("phrase,expected", REPRESENTATIVE_COSTS)
def test_representative_cost_is_classified_not_unrecognised(phrase, expected):
    cost = parse_activation_cost(phrase)
    assert cost is not None, f"{phrase!r} should parse to a cost"
    assert "unrecognised" not in cost.unpayable, (
        f"{phrase!r} fell through to 'unrecognised'; a cost the parser is "
        f"supposed to name must not be reported as unsupported")
    assert expected in cost.unpayable, (
        f"{phrase!r} should be classified {expected!r}, got {cost.unpayable}")


def test_mana_and_tap_still_parse_as_payable():
    """Regression: the payable path must be unaffected."""
    cost = parse_activation_cost("{1}{R}, {T}")
    assert cost.unpayable == (), f"expected fully payable, got {cost.unpayable}"
    assert cost.tap_self is True
    assert cost.mana.cmc == 2
