"""The amount sub-grammar over the whole card pool (design doc 2026-09-29,
section 6 "Amount"; A12, A16, A19; E0 step 11).

Every amount slot the pool prints in the four amount frames runs through
the leaf:

* count -- the count after a counted verb ("deals 3 damage", "draw two
  cards", "gain 4 life", "mill three cards", "discard a card", "scry 2");
* scaler -- a trailing "for each <Q>", "equal to <EXPR>" or "divided ...
  among" after the counted verb;
* where_x -- ", where x is <EXPR>";
* leading -- a frame-initial "for each <Q>, <body>" (A16): at the start of
  the sentence or right after a leading frame's comma ("when ~ enters, for
  each ...").

The slots are cut by a deliberately crude stand-in for L0-L3 (the payload
pool's L0 stand-in, then the frame patterns below, each slot running to
the end of its sentence). X binding follows section 6: the stand-in binds
X when the face's mana cost or the ability's cost holds {X} or the line is
a loyalty X ability, and hands a "where x is" definition of the same
sentence to the count. A slot the stand-in cuts badly is refused, which
lowers coverage but never hides an exception or yields a wrong number.

It asserts that the leaf never raises, returns exactly one of a typed
Amount or a closed-detail UNMODELLED stage (or the SCALED "no amount of its
own" marker), keeps every span inside the slot, is deterministic across a
cache clear, and types at least the measured share of each frame. The
coverage table is printed (``pytest -s``) for the census.
"""
from __future__ import annotations

import dataclasses
import re
from collections import Counter

import pytest

from engine.effect_spec import (Amount, AmountKind, CardFilter, Quantity,
                                QuantityKind, Ref, RefKind, Stage, canonical)
from tests.test_effect_grammar_filter_pool import _mask_names
from tests.test_effect_grammar_payload_pool import _SENT_RE, _normalise

_COUNT_RE = re.compile(
    r"\b(?:deals? (?!(?:combat |noncombat |excess )?damage\b)"
    r"|draws? (?=[^,;]*\bcards?\b)|(?:gains?|loses?|pays?) (?=[^,;]*\blife\b)"
    r"|mills? |discards? (?=[^,;]*\bcards?\b)|scry |surveil )")
_SCALER_RE = re.compile(r"(?<=\S) (for each|equal to|divided) ")
_WHERE_X_RE = re.compile(r",? where x is ")
_DIVIDED_BASE_RE = re.compile(r"\bdeals? (\S+(?: \S+)?) damage divided\b")
_LOYALTY_X_RE = re.compile(r"^\[[+\-−]x\]")
_X_WORD_RE = re.compile(r"\bx\b")
_FRAMES = ("count", "scaler", "where_x", "leading")
# A slot whose verb printed no count ("gain life equal to ...") is the
# scaler's, not a count slot.
_NO_COUNT_RE = re.compile(r"(?:cards?|life|damage)\b")


def _x_binding(template, text: str, sentence: str) -> bool:
    """Section 6: X is bound by a cost -- {X} in the mana cost (the typed
    ``x_cost_data`` the load pass parses from it) or in any other printed
    cost of the face (madness, kicker, "you may pay {x}"), an X in the
    ability's own cost ("remove x counters:"), an additional cost that
    names X, or a loyalty X (A12)."""
    if template.x_cost_data is not None or "{x}" in text:
        return True
    if _LOYALTY_X_RE.match(sentence):
        return True
    if ":" in sentence and _X_WORD_RE.search(sentence.split(":", 1)[0]):
        return True
    return any("additional cost" in s and _X_WORD_RE.search(s)
               for s in _SENT_RE.split(text))


def _slots(card_db):
    from engine.effect_grammar.sub import amount as A
    seen = set()
    for template in {id(v): v for v in card_db.cards.values()}.values():
        if not template.oracle_text:
            continue
        text = _normalise(template)
        for sentence in _SENT_RE.split(text):
            s = _mask_names(sentence.strip().rstrip("."))
            x_bound = _x_binding(template, text, s)
            where = _WHERE_X_RE.search(s)
            x_defined = None
            if where is not None:
                key = ("where_x", s[where.start():])
                if key not in seen:
                    seen.add(key)
                    yield "where_x", s, (where.start(), len(s)), {}
                x_defined = A.parse_where_x(s, (where.start(), len(s))).value
            end = where.start() if where is not None else len(s)
            if s.startswith("for each "):
                key = ("leading", s)
                if key not in seen:
                    seen.add(key)
                    yield "leading", s, (0, len(s)), {}
            for m in _COUNT_RE.finditer(s, 0, end):
                if m.end() >= end or _NO_COUNT_RE.match(s, m.end()):
                    continue
                key = ("count", s[m.end():end], x_bound, x_defined)
                if key in seen:
                    continue
                seen.add(key)
                yield "count", s, (m.end(), end), {
                    "x_bound": x_bound, "x_defined": x_defined}
            for m in _SCALER_RE.finditer(s, 0, end):
                a = m.start(1)
                if s.startswith("for each ", a) and s[max(0, a - 2):a] == ", ":
                    # A frame-initial "for each" after a leading frame
                    # ("when ~ enters, for each ...,") is A16's.
                    key = ("leading", s[a:])
                    if key not in seen:
                        seen.add(key)
                        yield "leading", s, (a, len(s)), {}
                    continue
                per = None
                if m.group(1) == "divided":
                    base = _DIVIDED_BASE_RE.search(s)
                    if base is not None:
                        per = A.parse_amount(base.group(1), lemma="deal",
                                             x_bound=x_bound,
                                             x_defined=x_defined).value
                key = ("scaler", s[a:end], per, x_bound, x_defined)
                if key in seen:
                    continue
                seen.add(key)
                yield "scaler", s, (a, end), {
                    "per": per, "x_bound": x_bound, "x_defined": x_defined}


def _run(slots):
    from engine.effect_grammar.sub import amount as A
    fns = {"count": A.parse_amount, "scaler": A.parse_scaler,
           "where_x": A.parse_where_x, "leading": A.parse_leading_for_each}
    return [(frame, host, span, fns[frame](host, span, lemma="x", **kw))
            for frame, host, span, kw in slots]


def _key(r):
    return canonical((r.value, r.unmodelled, r.span, r.rest_spans,
                      r.pending, r.flags))


# Typed share per frame, measured 2026-10-01 on this branch's DB (22.7k
# cards, 3226 distinct slots): count 1712/1850 (92.5%), scaler 701/961
# (72.9%), where_x 210/298 (70.5%), leading 14/117 (12.0%); 2637/3226
# (81.7%) overall, plus 4 "a number of" SCALED markers. The count refusals
# are the closed table working: "no_count" where the stand-in's verb has
# no count ("discard another card", "draw ... ~: draw"), ordinals ("your
# second card each turn"), an X no cost binds, "exactly", exponent X. The
# scaler and where_x refusals are almost all the quantity leaf's
# UNMODELLED(QUANTITY) (filters it refuses, result amounts, references
# outside its table, mana spent, colour-among counts) plus sums of two
# quantities. The leading frame is mostly A16 iteration: the body names
# the element ("a copy of that creature", "of that type") or any other
# anaphor the linker would have to bind first, so the refusal is
# deliberately conservative. Floors sit a few points under the
# measurement: the parse is deterministic, so a fall below a floor is a
# closed-table regression, while a DB refresh moves the share by far less.
_FLOORS = {"count": 0.90, "scaler": 0.70, "where_x": 0.67, "leading": 0.10}


# Pool-wide (~3.2k distinct amount slots). Measured 2026-10-01 on this
# container (quiet, 4 cores): ~1.4 s for the slot cut and two passes, plus
# ~16 s when it is the first test of the process to load the shared card
# DB. 120 s bounds a hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_the_amount_leaf_types_or_refuses_every_pool_amount_slot_deterministically(card_db):
    from engine.effect_grammar import clear_caches
    from engine.effect_grammar.sub import amount as A
    slots = list(_slots(card_db))
    assert len(slots) > 3000, len(slots)
    clear_caches()
    first = _run(slots)
    clear_caches()
    second = _run(slots)
    assert [_key(r) for *_, r in first] == [_key(r) for *_, r in second]

    typed, total, scaled = Counter(), Counter(), Counter()
    kinds, codes = Counter(), Counter()
    for frame, host, (a, b), r in first:
        total[frame] += 1
        assert r is not None, (frame, host)
        if A.SCALED in r.flags:
            assert r.value is None and r.unmodelled is None, (host, r)
            scaled[frame] += 1
            continue
        assert (r.value is None) != (r.unmodelled is None), (host, r)
        assert a <= r.span[0] <= r.span[1] <= b, (host, r)
        assert all(a <= x < y <= b for x, y in r.rest_spans), (host, r)
        if r.value is not None:
            assert isinstance(r.value, Amount)
            hash(r.value)
            typed[frame] += 1
            kinds[r.value.kind.name] += 1
        else:
            u = r.unmodelled
            assert u.lemma == "x"
            leaf, code = u.detail.split(":")[0].split(".")
            if u.stage is Stage.QUANTITY:
                from engine.effect_grammar.sub import quantity as Q
                assert leaf == Q.LEAF and code in Q.DETAIL_CODES, u.detail
            else:
                assert u.stage in (Stage.AMOUNT, Stage.ITERATION), u
                assert leaf == A.LEAF and code in A.DETAIL_CODES, u.detail
            assert host[slice(*r.span)] == host[a:b].strip(), (host, r)
            codes[u.detail.split(":")[0]] += 1

    print("\namount slots by frame (typed / total, SCALED markers):")
    for frame in _FRAMES:
        print("  %-8s %4d / %4d (%.1f%%), %d scaled" % (
            frame, typed[frame], total[frame],
            100 * typed[frame] / total[frame], scaled[frame]))
    print("  overall  %4d / %4d (%.1f%%)" % (
        sum(typed.values()), sum(total.values()),
        100 * sum(typed.values()) / sum(total.values())))
    print("typed kinds:", dict(kinds.most_common()))
    print("unmodelled codes:", dict(codes.most_common(30)))
    for frame, floor in _FLOORS.items():
        assert total[frame], frame
        assert typed[frame] / total[frame] >= floor, (
            frame, typed[frame], total[frame])


# ── Registered-deck witnesses ──────────────────────────────────────────
# Amount slots printed by registered-deck cards (decks/modern_meta.py),
# with the exact Amount the leaf must return. The share floors above count
# a slot as typed whenever a value exists; these pin that the value is the
# printed rule. The card name only locates the printed text in the DB; the
# leaf never sees it.

_X = Amount(AmountKind.X, n=1)
_THAT = Amount(AmountKind.THAT_MUCH)
_CREATURES_YOU_CONTROL = CardFilter(types=frozenset({"creature"}),
                                    controller="you")


def _lit(n):
    return Amount(AmountKind.LITERAL, n=n)


def _eq(kind, **kw):
    return Amount(AmountKind.EQUAL_TO, quantity=Quantity(kind, **kw))


def _strip_raw(a):
    """The Amount with every Quantity's raw and filter raw blanked: a
    witness pins the rule, not the echoed text."""
    if a is None:
        return None
    q = a.quantity
    if q is not None:
        f = q.filter
        if f is not None:
            f = dataclasses.replace(f, raw="")
        q = dataclasses.replace(q, raw="", filter=f)
    return dataclasses.replace(a, quantity=q, inner=_strip_raw(a.inner))


# (card, printed slot, parser, kwargs, Amount (raws blanked), rest)
_WITNESSES = (
    ("Lightning Bolt", "3 damage to any target", "count", {}, _lit(3),
     "damage to any target"),
    ("Faithless Looting", "two cards, then discard two cards", "count", {},
     _lit(2), "cards, then discard two cards"),
    ("Thoughtseize", "2 life", "count", {}, _lit(2), "life"),
    # A12: a loyalty X is bound by the paid loyalty cost.
    ("Chandra, Awakened Inferno", "x damage to target creature or planeswalker",
     "count", {"x_bound": True}, _X, "damage to target creature or planeswalker"),
    ("Galvanic Discharge", "that much damage to that permanent", "count", {},
     _THAT, "damage to that permanent"),
    # A19: "any amount of" is ANY_NUMBER, chosen at resolution (A35).
    ("Galvanic Discharge", "any amount of {e}", "count", {},
     Amount(AmountKind.ANY_NUMBER), "{e}"),
    ("Fable of the Mirror-Breaker // Reflection of Kiki-Jiki",
     "that many cards", "count", {}, _THAT, "cards"),
    ("Scapeshift", "up to that many land cards", "count", {},
     Amount(AmountKind.UP_TO, inner=_THAT), "land cards"),
    ("Primeval Titan", "up to two land cards", "count", {},
     Amount(AmountKind.UP_TO, n=2), "land cards"),
    ("Scapeshift", "any number of lands", "count", {},
     Amount(AmountKind.ANY_NUMBER), "lands"),
    ("Valakut Awakening // Valakut Stoneforge", "that many cards plus one",
     "count", {}, Amount(AmountKind.PLUS, n=1, inner=_THAT), "cards"),
    ("Wan Shi Tong, Librarian", "half x cards, rounded down", "count",
     {"x_bound": True}, Amount(AmountKind.HALF, inner=_X, rounding="down"),
     "cards"),
    ("Cranial Plating", "for each artifact you control", "scaler", {},
     Amount(AmountKind.FOR_EACH, n=1, quantity=Quantity(
         QuantityKind.COUNT, player="any", filter=CardFilter(
             types=frozenset({"artifact"}), controller="you"))), ""),
    ("Thraben Charm",
     "equal to twice the number of creatures you control to target creature",
     "scaler", {},
     Amount(AmountKind.MULTIPLY, n=2, inner=_eq(
         QuantityKind.COUNT, player="any", filter=_CREATURES_YOU_CONTROL)),
     "to target creature"),
    ("Grist, the Hunger Tide",
     "equal to the number of creature cards in your graveyard", "scaler", {},
     _eq(QuantityKind.CARDS_IN, player="you", filter=CardFilter(
         zone="graveyard", types=frozenset({"creature"}), owner="you")), ""),
    ("Solitude", "equal to its power", "scaler", {},
     _eq(QuantityKind.POWER, stat="power"), ""),
    ("Craterhoof Behemoth", "where x is the number of creatures you control",
     "where_x", {},
     Amount(AmountKind.X_DEFINED, inner=_eq(
         QuantityKind.COUNT, player="any", filter=_CREATURES_YOU_CONTROL)), ""),
    ("Tyvar, the Pummeler", "where x is the greatest power among creatures you control",
     "where_x", {},
     Amount(AmountKind.X_DEFINED, inner=_eq(
         QuantityKind.GREATEST, player="any", stat="power",
         filter=_CREATURES_YOU_CONTROL)), ""),
    # A16: no anaphor to the element -- the count of the counted verb.
    ("Seasoned Pyromancer",
     "for each nonland card discarded this way, create a 1/1 red elemental "
     "creature token", "leading", {},
     Amount(AmountKind.FOR_EACH, n=1, quantity=Quantity(
         QuantityKind.RESULT_SIZE, player="any", ref=Ref(RefKind.RESULT),
         filter=CardFilter(zone="", not_types=frozenset({"land"})))),
     "create a 1/1 red elemental creature token"),
)

# Printed slots the closed table must refuse rather than number wrongly:
# (card, slot, parser, Stage, detail prefix).
_REFUSED = (
    # A16: the body names the element -- iteration, not an amount.
    ("Atraxa, Grand Unifier",
     "for each card type, you may put a card of that type from among the "
     "revealed cards into your hand", "leading", Stage.ITERATION,
     "amount.element_anaphor"),
    ("Ocelot Pride",
     "for each token you control that entered this turn, create a token "
     "that's a copy of it", "leading", Stage.ITERATION,
     "amount.element_anaphor"),
    # An X no cost binds is never zero (section 6).
    ("Chandra, Awakened Inferno", "x damage to target creature or planeswalker",
     "count", Stage.AMOUNT, "amount.x_unbound"),
)


def _printed(card_db, card, slot):
    from decks.modern_meta import MODERN_DECKS
    assert any(card in (d.get("mainboard") or {}) or
               card in (d.get("sideboard") or {})
               for d in MODERN_DECKS.values()), card
    text = _mask_names(_normalise(card_db.cards[card]))
    assert slot in text, (card, slot, text)


def _parse(parser, slot, **kw):
    from engine.effect_grammar.sub import amount as A
    fn = {"count": A.parse_amount, "scaler": A.parse_scaler,
          "where_x": A.parse_where_x,
          "leading": A.parse_leading_for_each}[parser]
    return fn(slot, (0, len(slot)), lemma="x", **kw)


@pytest.mark.timeout(120)
def test_registered_deck_amount_witnesses_type_exactly_the_printed_rule(card_db):
    """Each witness is the value its printed phrase states: the kind, the
    operator and its operand, the quantity with every qualifier, and the
    rest the caller's other sub-grammars read."""
    for card, slot, parser, kw, expected, rest in _WITNESSES:
        _printed(card_db, card, slot)
        r = _parse(parser, slot, **kw)
        assert r.value is not None, (card, r)
        assert _strip_raw(r.value) == expected, (card, r)
        assert r.rest_text(slot) == rest, (card, r)
    for card, slot, parser, stage, detail in _REFUSED:
        _printed(card_db, card, slot)
        r = _parse(parser, slot)
        assert r.value is None, (card, r)
        assert r.unmodelled.stage is stage, (card, r)
        assert r.unmodelled.detail.split(":")[0] == detail, (card, r)
