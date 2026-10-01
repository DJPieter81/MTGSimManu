"""The condition sub-grammar over the whole card pool (design doc
2026-09-29, section 6 "Condition"; F9, A2, A23, A31; E0 step 11).

Every condition slot the pool prints in the four condition frames runs
through the leaf:

* a leading "if <COND>, ..." (at the sentence start, after a trigger head,
  "then" or "otherwise") -- the slot runs to the sentence end, so the
  leaf's comma cut and rest hand-off are exercised;
* a trailing " if <COND>";
* "unless <COND>" / "unless <player> pays <cost>";
* "as long as <COND>" (and "for as long as", the duration leaf's).

The slots are cut by a deliberately crude stand-in for L0-L2 (the payload
pool's L0 stand-in, then the frame patterns below). A slot the stand-in
cuts badly is refused, which lowers coverage but never hides an exception
or yields a broader condition.

It asserts that the leaf never raises, returns None (structure: performed
gating, "this way", "would", "if able", "for as long as"), a typed
Condition over the closed predicate and operator tables, or an
UNMODELLED(CONDITION) stage with a closed detail code over the whole slot;
keeps every span inside the slot; is deterministic across a cache clear;
and types at least the measured share of each frame. The coverage table
is printed (``pytest -s``) for the census.
"""
from __future__ import annotations

import dataclasses
import re
from collections import Counter

import pytest

from engine.effect_model import Selector, SelectorKind
from engine.effect_spec import (Amount, AmountKind, CardFilter, Condition,
                                ConditionKind, Quantity, QuantityKind, Ref,
                                RefKind, Stage, canonical, freeze_cost)
from tests.test_effect_grammar_filter_pool import _mask_names
from tests.test_effect_grammar_payload_pool import _SENT_RE, _normalise

_FRAMES = (
    ("leading", re.compile(
        r"(?:^|^(?:when|whenever|at)\b[^,]*, |^then |^otherwise, )"
        r"(if .+)$")),
    ("trailing", re.compile(r"(?<!,) (if (?:(?!\bif\b)[^,;])+)$")),
    ("unless", re.compile(r"\b(unless [^,;]+?)(?=[,;]|$)")),
    ("as_long_as", re.compile(r"\b((?:for )?as long as [^,;]+?)(?=[,;]|$)")),
)

def _closed_detail(detail: str) -> bool:
    """'<leaf>.<code>[:<param>]' whose code is in that leaf's closed
    DETAIL_CODES, for any grammar leaf (a callee's refusal propagates
    unchanged through its caller)."""
    import importlib
    leaf, code = detail.split(":")[0].split(".")
    name = {"destination": "dest"}.get(leaf, leaf)
    mod = importlib.import_module("engine.effect_grammar.sub." + name)
    return mod.LEAF == leaf and code in mod.DETAIL_CODES



def _slots(card_db):
    seen = set()
    for template in {id(v): v for v in card_db.cards.values()}.values():
        if not template.oracle_text:
            continue
        for sentence in _SENT_RE.split(_normalise(template)):
            s = _mask_names(sentence.strip().rstrip("."))
            for frame, rx in _FRAMES:
                for m in rx.finditer(s):
                    key = (frame, s[m.start(1):])
                    if key in seen:
                        continue
                    seen.add(key)
                    yield frame, s, m.span(1)


def _run(slots):
    from engine.effect_grammar.sub import condition as C
    return [(frame, host, span, C.parse_condition(host, span, lemma="x"))
            for frame, host, span in slots]


def _key(r):
    if r is None:
        return "None"
    return canonical((r.value, r.unmodelled, r.span, r.rest_spans, r.pending,
                      r.flags))


def _walk(c: Condition):
    yield c
    for child in c.children:
        yield from _walk(child)


# Typed share of the non-structural slots per frame, measured 2026-10-01 on
# this branch's DB (22.7k cards, 3727 distinct slots): leading 1156/1675
# (69.0%, 875 more structural), trailing 251/389 (64.5%), unless 125/224
# (55.8%), as_long_as 359/518 (69.3%, 40 "for as long as"); 1891/2806
# (67.4%) overall. (Re-measured after the review fixes: 15 leading slots
# a gated clause's "would" / "this way" had dropped as structure are now
# typed or refused, and universal player quantifiers, set comparands and
# past-tense counts are refused instead of typed as a broader state.) The floors sit a few points under the measurement. The parse is deterministic, so a fall
# below a floor is a closed-table regression; a DB refresh moves the share
# by far less. The refusals are the closed table working, not gaps:
# replacement and performed-gating shapes the stand-in cannot tell from a
# condition, opening-hand and coin-flip gates, "targets" / "shares" /
# "chosen" comparisons the model has no predicate for, keyword cast facts
# (foretold, madness, ...), relative comparisons the quantity leaf
# refuses, and stand-in cuts that land inside a clause.
_FLOORS = {"leading": 0.66, "trailing": 0.62, "unless": 0.52,
           "as_long_as": 0.66}


# Pool-wide (~3.7k distinct condition slots). Measured 2026-10-01 on this
# container (quiet, 4 cores): ~2 s for the slot cut and two passes, plus
# ~16 s when it is the first test of the process to load the shared card
# DB. 120 s bounds a hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_the_condition_leaf_types_or_refuses_every_pool_condition_slot_deterministically(card_db):
    from engine.effect_grammar.sub import condition as C
    slots = list(_slots(card_db))
    assert len(slots) > 2000, len(slots)
    C.clear_caches()
    first = _run(slots)
    C.clear_caches()
    second = _run(slots)
    assert [_key(r) for *_, r in first] == [_key(r) for *_, r in second]

    typed, refused, structural = Counter(), Counter(), Counter()
    kinds, codes = Counter(), Counter()
    for frame, host, (a, b), r in first:
        if r is None:
            # Structure is the condition phrase's own: the slot cut at its
            # first comma (the leading condition alone) is structure too,
            # so a "would" / "this way" in the gated clause never drops a
            # parseable condition.
            slot = host[a:b]
            head = slot.split(", ", 1)[0]
            assert C.parse_condition(head, (0, len(head)), lemma="x") is None, (
                host, head)
            structural[frame] += 1
            continue
        assert (r.value is None) != (r.unmodelled is None), (host, r)
        assert a <= r.span[0] <= r.span[1] <= b, (host, r)
        assert all(r.span[1] <= x <= y <= b for x, y in r.rest_spans), (host, r)
        if r.value is not None:
            assert isinstance(r.value, Condition)
            assert r.value.raw == host[slice(*r.span)], (host, r)
            for c in _walk(r.value):
                assert c.pred in C.PREDICATES[c.kind], (host, c)
                assert c.op in C.OPS, (host, c)
            typed[frame] += 1
            kinds[r.value.kind.name] += 1
        else:
            u = r.unmodelled
            assert u.lemma == "x"
            leaf, code = u.detail.split(":")[0].split(".")
            # The condition leaf's own refusal, or a callee's propagated
            # unchanged.
            assert _closed_detail(u.detail), u.detail
            assert (u.stage is Stage.CONDITION) == (leaf == C.LEAF), u
            assert host[slice(*r.span)] == host[a:b].strip(), (host, r)
            refused[frame] += 1
            codes[u.detail.split(":")[0]] += 1

    print("\ncondition slots by frame (typed / typed+refused, structural):")
    for frame, _ in _FRAMES:
        n = typed[frame] + refused[frame]
        print("  %-10s %4d / %4d (%.1f%%), %d structural" % (
            frame, typed[frame], n, 100 * typed[frame] / max(n, 1),
            structural[frame]))
    t, n = sum(typed.values()), sum(typed.values()) + sum(refused.values())
    print("  overall    %4d / %4d (%.1f%%)" % (t, n, 100 * t / n))
    print("typed kinds:", dict(kinds.most_common()))
    print("unmodelled codes:", dict(codes.most_common()))
    for frame, floor in _FLOORS.items():
        n = typed[frame] + refused[frame]
        assert n, frame
        assert typed[frame] / n >= floor, (frame, typed[frame], n)


# ── Registered-deck witnesses ──────────────────────────────────────────
# Condition slots printed by registered-deck cards (decks/modern_meta.py),
# with the exact Condition the leaf must return. The share floors above
# count a slot as typed whenever a value exists; these pin that the value
# is the printed rule. The card name only locates the printed text in the
# DB; the leaf never sees it.

_YOU = Selector(SelectorKind.PLAYER)
_OPPONENTS = Selector(SelectorKind.OPPONENTS)


def _n(k):
    return Amount(AmountKind.LITERAL, n=k)


def _count(op, n, **f):
    return Condition(ConditionKind.STATE, pred="count", op=op, n=n,
                     filter=CardFilter(**f))


def _cost(printed):
    from engine.oracle_parser import parse_activation_cost
    return freeze_cost(parse_activation_cost(printed))


_ITS_CONTROLLER_PAYS = {"kind": ConditionKind.UNLESS, "pred": "pays",
                        "ref": Ref(RefKind.CONTROLLER_OF)}

# (card, printed slot, label, expected Condition (raw ignored) or a dict of
#  pinned fields, pending)
_WITNESSES = (
    ("Galvanic Blast", "if you control three or more artifacts", "metalcraft",
     _count(">=", _n(3), types=frozenset({"artifact"}), controller="you"), ()),
    ("Stubborn Denial", "if you control a creature with power 4 or greater",
     "ferocious",
     _count(">=", _n(1), types=frozenset({"creature"}), controller="you",
            stat_bounds=(("power", ">=", _n(4)),)), ()),
    ("Valakut, the Molten Pinnacle", "if you control at least five other mountains",
     "", _count(">=", _n(5), subtypes=frozenset({"mountain"}), other=True,
                controller="you"), ()),
    ("Concealed Courtyard", "unless you control two or fewer other lands", "",
     Condition(ConditionKind.NOT, children=(
         _count("<=", _n(2), types=frozenset({"land"}), other=True,
                controller="you"),)), ()),
    ("Castle Ardenvale", "unless you control a plains", "",
     Condition(ConditionKind.NOT, children=(
         _count(">=", _n(1), subtypes=frozenset({"plains"}), controller="you"),)),
     ()),
    ("Cori Mountain Monastery", "unless you control a plains or an island", "",
     Condition(ConditionKind.NOT, children=(Condition(
         ConditionKind.ANY_OF, children=(
             _count(">=", _n(1), subtypes=frozenset({"plains"}), controller="you"),
             _count(">=", _n(1), subtypes=frozenset({"island"}),
                    controller="you"))),)), ()),
    ("Force of Negation", "if it's not your turn", "",
     Condition(ConditionKind.TURN, pred="not_your_turn"), ()),
    ("Day's Undoing", "if it's your turn", "",
     Condition(ConditionKind.TURN, pred="your_turn"), ()),
    ("Dragon's Rage Channeler",
     "as long as there are four or more card types among cards in your graveyard",
     "delirium",
     Condition(ConditionKind.STATE, pred="card_types", op=">=", n=_n(4),
               filter=CardFilter(zone="graveyard", owner="you")), ()),
    ("Elvish Reclaimer", "as long as there are three or more land cards in your graveyard",
     "", _count(">=", _n(3), zone="graveyard", types=frozenset({"land"}),
                owner="you"), ()),
    ("Quantum Riddler", "as long as you have one or fewer cards in hand", "",
     _count("<=", _n(1), zone="hand", owner="you"), ()),
    ("Fatal Push", "if it has mana value 2 or less", "",
     Condition(ConditionKind.OBJECT, pred="mana_value", op="<=", n=_n(2)),
     (("ref", "it"),)),
    ("Fatal Push",
     "if a permanent left the battlefield under your control this turn", "revolt",
     Condition(ConditionKind.HISTORY, pred="permanent_left", op=">=", n=_n(1),
               filter=CardFilter(zone="", controller="you")), ()),
    ("Haliya, Guided by Light", "if you've gained 3 or more life this turn", "",
     Condition(ConditionKind.HISTORY, pred="life_gained", payer=_YOU, op=">=",
               n=_n(3)), ()),
    ("Ocelot Pride", "if you gained life this turn", "",
     Condition(ConditionKind.HISTORY, pred="life_gained", payer=_YOU, op=">=",
               n=_n(1)), ()),
    ("Ocelot Pride", "if you have the city's blessing", "",
     Condition(ConditionKind.STATE, pred="citys_blessing", payer=_YOU), ()),
    ("Omnath, Locus of Creation",
     "if this is the first time this ability has resolved this turn", "",
     Condition(ConditionKind.RESOLUTION_ORDINAL, pred="resolved", op="==",
               n=_n(1)), ()),
    ("Vexing Bauble", "if no mana was spent to cast it", "",
     Condition(ConditionKind.CAST_FACT, pred="mana_spent_total", op="==",
               n=_n(0)), (("ref", "it"),)),
    ("Wistfulness", "if {g}{g} was spent to cast it", "",
     Condition(ConditionKind.CAST_FACT, pred="mana_spent", cost=_cost("{g}{g}")),
     (("ref", "it"),)),
    ("Requiting Hex", "if ~'s additional cost was paid", "",
     Condition(ConditionKind.CAST_FACT, pred="additional_cost_paid",
               ref=Ref(RefKind.SELF)), ()),
    ("Orim's Chant", "if ~ was kicked", "",
     Condition(ConditionKind.CAST_FACT, pred="kicked", ref=Ref(RefKind.SELF)), ()),
    ("Sowing Mycospawn", "if it was kicked", "",
     Condition(ConditionKind.CAST_FACT, pred="kicked"), (("ref", "it"),)),
    ("Risen Reef", "if it's a land card", "",
     Condition(ConditionKind.OBJECT, pred="is",
               filter=CardFilter(zone="", types=frozenset({"land"}))),
     (("ref", "it"),)),
    ("Blade of the Bloodchief", "if equipped creature is a vampire", "",
     Condition(ConditionKind.OBJECT, pred="is",
               ref=Ref(RefKind.ATTACHED, noun="creature"),
               filter=CardFilter(zone="", subtypes=frozenset({"vampire"}))), ()),
    ("Trinisphere", "as long as ~ is untapped", "",
     Condition(ConditionKind.OBJECT, pred="is", ref=Ref(RefKind.SELF),
               filter=CardFilter(zone="", state=frozenset({"untapped"}))), ()),
    ("Beza, the Bounding Spring", "if an opponent has more life than you", "",
     Condition(ConditionKind.STATE, pred="life_total", payer=_OPPONENTS, op=">",
               n=Amount(AmountKind.EQUAL_TO, quantity=Quantity(
                   QuantityKind.LIFE_TOTAL, player="you"))), ()),
    ("Flusterstorm", "unless its controller pays {1}", "",
     dict(_ITS_CONTROLLER_PAYS, cost=_cost("{1}")), (("ref", "its"),)),
    ("Mana Tithe", "unless its controller pays {1}", "",
     dict(_ITS_CONTROLLER_PAYS, cost=_cost("{1}")), (("ref", "its"),)),
    ("Spell Pierce", "unless its controller pays {2}", "",
     dict(_ITS_CONTROLLER_PAYS, cost=_cost("{2}")), (("ref", "its"),)),
    ("Metallic Rebuke", "unless its controller pays {3}", "",
     dict(_ITS_CONTROLLER_PAYS, cost=_cost("{3}")), (("ref", "its"),)),
)

# Printed conditions the closed table must refuse rather than type as
# something broader.
_REFUSED = (
    ("Leyline of Sanctity", "if ~ is in your opening hand", "condition.opening_hand"),
    ("Mystical Dispute", "if it targets a blue spell", "condition.targets"),
    ("Ral, Monsoon Mage // Ral, Leyline Prodigy", "if you win the flip",
     "condition.coin_flip"),
    # Energy is a player counter (CR 122.1), not a mana or life payment the
    # cost snapshot holds.
    ("Static Prison", "unless you pay {e}", "condition.unless_cost"),
)

# Printed phrases that are structure, never conditions (section 6).
_STRUCTURAL = (
    ("Force of Negation", "if that spell is countered this way"),
    ("Rest in Peace", "if a card or token would be put into a graveyard from anywhere"),
    ("Summoner's Pact", "if you don't"),
    ("Nihil Spellbomb", "if you do"),
)


def _printed(card_db, card, slot):
    from decks.modern_meta import MODERN_DECKS
    assert any(card in (d.get("mainboard") or {}) or
               card in (d.get("sideboard") or {})
               for d in MODERN_DECKS.values()), card
    text = _mask_names(_normalise(card_db.cards[card]))
    assert slot in text, (card, slot, text)


def _raw_free(obj):
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


@pytest.mark.timeout(120)
def test_registered_deck_condition_witnesses_type_exactly_the_printed_rule(card_db):
    """Each witness is the predicate its printed phrase states: the kind,
    the measured set with every qualifier, the comparison, the payer and
    cost of an unless, the reference, and the anaphors left for the
    linker (rule 0)."""
    from engine.effect_grammar.sub import condition as C
    for card, slot, label, expected, pending in _WITNESSES:
        _printed(card_db, card, slot)
        r = C.parse_condition(slot, (0, len(slot)), lemma="x", label=label)
        assert r is not None and r.value is not None, (card, r)
        assert r.span == (0, len(slot)) and r.rest_spans == (), (card, r)
        assert r.value.raw == slot and r.value.label == label, (card, r)
        if isinstance(expected, dict):
            for field, want in expected.items():
                assert getattr(r.value, field) == want, (card, field, r)
        else:
            assert _raw_free(r.value) == dataclasses.replace(
                _raw_free(expected), label=label), (card, r)
        assert r.pending == pending, (card, r)
    for card, slot, detail in _REFUSED:
        _printed(card_db, card, slot)
        r = C.parse_condition(slot, (0, len(slot)), lemma="x")
        assert r is not None and r.value is None, (card, r)
        assert r.unmodelled.detail.split(":")[0] == detail, (card, r)
    for card, slot in _STRUCTURAL:
        _printed(card_db, card, slot)
        assert C.parse_condition(slot, (0, len(slot)), lemma="x") is None, card
