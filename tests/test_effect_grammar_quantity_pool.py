"""The quantity sub-grammar over the whole card pool (design doc
2026-09-29, section 6 "Quantity"; A16, A27; E0 step 11).

Every quantity slot the pool prints in the four amount frames runs through
the leaf:

* "for each <Q>" -- the counted object;
* "equal to <Q>" and "where x is <Q>" -- the operand;
* "the number of <Q>" printed anywhere else (a condition, a bound).

The slots are cut by a deliberately crude stand-in for L0-L3 (the payload
pool's L0 stand-in, then the frame patterns below, with a leading amount
operator -- "twice", "half", "1 plus" -- stripped the way the amount leaf
would). A slot the stand-in cuts badly is refused or consumed only up to a
tail word, which lowers coverage but never hides an exception or yields a
broader count.

It asserts that the leaf never raises, returns exactly one of a typed
Quantity or an UNMODELLED(QUANTITY) stage with a closed detail code, keeps
every span inside the slot, is deterministic across a cache clear, and
types at least the measured share of each frame. The coverage table is
printed (``pytest -s``) for the census.
"""
from __future__ import annotations

import dataclasses
import re
from collections import Counter

import pytest

from engine.effect_spec import (CardFilter, Quantity, QuantityKind, Ref,
                                RefKind, Stage, canonical)
from tests.test_effect_grammar_filter_pool import _mask_names
from tests.test_effect_grammar_payload_pool import _SENT_RE, _normalise

_END = (r"(?=[,;:]|$| then | unless | if | instead| where |\.)")
_FRAMES = (
    ("for_each", re.compile(r"\bfor each (.+?)%s" % _END)),
    ("equal_to", re.compile(r"\bequal to (.+?)%s" % _END)),
    ("where_x", re.compile(r"\bwhere x is (.+?)(?=[,;:]|$| then | unless "
                           r"| if | instead|\.)")),
    ("number_of", re.compile(
        r"(?<!equal to )(?<!where x is )(?<!twice )(?<!half )(?<!plus )"
        r"(?<!minus )\b(the number of .+?)%s" % _END)),
)
# Amount operators the amount sub-grammar strips before the quantity.
_OPERATOR_RE = re.compile(
    r"(?:twice |half |three times |(?:\d+|one|two|three|x) (?:plus|minus) )")

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
                    a, b = m.span(1)
                    op = _OPERATOR_RE.match(s, a)
                    if op is not None:
                        a = op.end()
                    key = (frame, s[a:b])
                    if key in seen:
                        continue
                    seen.add(key)
                    yield frame, s, (a, b)


def _run(slots):
    from engine.effect_grammar.sub import quantity as Q
    out = []
    for frame, host, span in slots:
        r = Q.parse_quantity(host, span, lemma="x")
        out.append((frame, host, span, r))
    return out


# Typed share per frame, measured 2026-10-01 on this branch's DB (22.7k
# cards, 1191 distinct slots): for_each 289/452 (63.9%), equal_to 323/437
# (73.9%), where_x 191/271 (70.5%), number_of 20/31 (64.5%); 823/1191
# (69.1%) overall. 177 typed slots leave a tail as rest (mostly "to <any
# target>" after an equal-to operand). The refusals are
# the closed table working, not gaps: element anaphors ("for each of those
# creatures", A16 iteration), "for each target beyond the first", party and
# colour-among counts the model has no kind for, mana spent, result amounts
# ("the damage dealt this way"), computed / relative filters ("that could
# produce", "with the same name as"), and stand-in cuts that land inside a
# clause. A typed slot may leave a tail (an operator, a recipient) as rest;
# that share is printed apart. Floors sit a few points under the
# measurement: the parse is deterministic, so a fall below a floor is a
# closed-table regression, while a DB refresh moves the share by far less.
_FLOORS = {"for_each": 0.60, "equal_to": 0.70, "where_x": 0.66,
           "number_of": 0.55}


# Pool-wide (~1.2k distinct quantity slots). Measured 2026-10-01 on this
# container (quiet, 4 cores): ~1 s for the slot cut and two passes, plus
# ~16 s when it is the first test of the process to load the shared card
# DB. 120 s bounds a hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_the_quantity_leaf_types_or_refuses_every_pool_quantity_slot_deterministically(card_db):
    from engine.effect_grammar.sub import quantity as Q
    slots = list(_slots(card_db))
    assert len(slots) > 1000, len(slots)
    Q.clear_caches()
    first = _run(slots)
    Q.clear_caches()
    second = _run(slots)
    assert [canonical((h, s, r.value, r.unmodelled, r.span, r.rest_spans,
                       r.pending, r.flags)) for _, h, s, r in first] == \
        [canonical((h, s, r.value, r.unmodelled, r.span, r.rest_spans,
                    r.pending, r.flags)) for _, h, s, r in second]

    typed, total, with_rest = Counter(), Counter(), Counter()
    kinds, codes = Counter(), Counter()
    for frame, host, (a, b), r in first:
        total[frame] += 1
        assert (r.value is None) != (r.unmodelled is None), (host, r)
        assert a <= r.span[0] <= r.span[1] <= b, (host, r)
        assert all(r.span[1] <= x <= y <= b for x, y in r.rest_spans), (host, r)
        if r.value is not None:
            assert isinstance(r.value, Quantity)
            assert r.value.raw == host[slice(*r.span)]
            if r.value.kind is QuantityKind.HISTORY:
                assert r.value.event in Q.HISTORY_EVENTS
            typed[frame] += 1
            kinds[r.value.kind.name] += 1
            if r.rest_spans:
                with_rest[frame] += 1
        else:
            u = r.unmodelled
            assert u.lemma == "x"
            leaf, code = u.detail.split(":")[0].split(".")
            # The quantity leaf's own refusal, or a callee's propagated
            # unchanged (the leaf contract's refusal propagation).
            assert u.stage is Stage.QUANTITY or leaf != Q.LEAF, u
            assert _closed_detail(u.detail), u.detail
            assert host[slice(*r.span)] == host[a:b].strip().rstrip(" .,;") \
                or host[slice(*r.span)] == host[a:b].strip(), (host, r)
            codes[u.detail.split(":")[0]] += 1

    print("\nquantity slots by frame (typed / total, typed with a rest tail):")
    for frame in sorted(total):
        print("  %-10s %4d / %4d (%.1f%%), %d with rest" % (
            frame, typed[frame], total[frame],
            100 * typed[frame] / total[frame], with_rest[frame]))
    print("  overall    %4d / %4d (%.1f%%)" % (
        sum(typed.values()), sum(total.values()),
        100 * sum(typed.values()) / sum(total.values())))
    print("typed kinds:", dict(kinds.most_common()))
    print("unmodelled codes:", dict(codes.most_common()))
    for frame, floor in _FLOORS.items():
        assert total[frame], frame
        assert typed[frame] / total[frame] >= floor, (
            frame, typed[frame], total[frame])


# ── Registered-deck witnesses ──────────────────────────────────────────
# Quantity slots printed by registered-deck cards (decks/modern_meta.py),
# with the exact Quantity the leaf must return. The share floors above
# count a slot as typed whenever a value exists; these pin that the value
# is the printed rule. The card name only locates the printed text in the
# DB; the leaf never sees it.

_SELF = Ref(RefKind.SELF)
_SELF_LKI = Ref(RefKind.SELF, lki=True)
_RESULT = Ref(RefKind.RESULT)
_CREATURES_YOU_CONTROL = CardFilter(types=frozenset({"creature"}),
                                    controller="you")
_LANDS_YOU_CONTROL = CardFilter(types=frozenset({"land"}), controller="you")

# A HISTORY filter names no current zone (the object as it was at the
# event), and every kind over a set has player "any" (control is the
# filter's).
# (card, printed slot, source_left, Quantity (raw and filter raw ignored),
#  pending, consumed prefix or None for the whole slot)
_WITNESSES = (
    # A27: the source is sacrificed as the activation cost (CR 608.2h).
    ("Engineered Explosives", "the number of charge counters on ~", True,
     Quantity(QuantityKind.COUNTERS_ON, ref=_SELF_LKI, counter_kind="charge"),
     (), None),
    ("The Filigree Sylex", "the number of oil counters on ~", True,
     Quantity(QuantityKind.COUNTERS_ON, ref=_SELF_LKI, counter_kind="oil"),
     (), None),
    ("Chalice of the Void", "the number of charge counters on ~", False,
     Quantity(QuantityKind.COUNTERS_ON, ref=_SELF, counter_kind="charge"),
     (), None),
    ("Cranial Plating", "artifact you control", False,
     Quantity(QuantityKind.COUNT, player="any", filter=CardFilter(
         types=frozenset({"artifact"}), controller="you")),
     (), None),
    ("Otawara, Soaring City", "legendary creature you control", False,
     Quantity(QuantityKind.COUNT, player="any", filter=CardFilter(
         types=frozenset({"creature"}), controller="you",
         supertypes=frozenset({"legendary"}))),
     (), None),
    ("Craterhoof Behemoth", "the number of creatures you control", False,
     Quantity(QuantityKind.COUNT, player="any", filter=_CREATURES_YOU_CONTROL),
     (), None),
    ("Fiend Artisan", "creature card in your graveyard", False,
     Quantity(QuantityKind.CARDS_IN, player="you", filter=CardFilter(
         zone="graveyard", types=frozenset({"creature"}), owner="you")),
     (), None),
    ("Drown in the Loch", "the number of cards in its controller's graveyard",
     False,
     Quantity(QuantityKind.CARDS_IN, player="any",
              filter=CardFilter(zone="graveyard")),
     (("owner", "its controller's"),), None),
    ("Murktide Regent", "instant and sorcery card exiled with it", False,
     Quantity(QuantityKind.CARDS_IN, player="any", filter=CardFilter(
         zone="exile", types=frozenset({"instant", "sorcery"}))),
     (("exiled_with", "it"),), None),
    ("Seasoned Pyromancer", "nonland card discarded this way", False,
     Quantity(QuantityKind.RESULT_SIZE, player="any", ref=_RESULT,
              filter=CardFilter(zone="", not_types=frozenset({"land"}))),
     (("result", "discarded"),), None),
    ("March of Otherworldly Light", "card exiled this way", False,
     Quantity(QuantityKind.RESULT_SIZE, player="any", ref=_RESULT,
              filter=CardFilter(zone="")),
     (("result", "exiled"),), None),
    ("Leyline Binding", "basic land type among lands you control", False,
     Quantity(QuantityKind.BASIC_LAND_TYPES, player="any",
              filter=_LANDS_YOU_CONTROL),
     (), None),
    ("Territorial Kavu",
     "the number of basic land types among lands you control", False,
     Quantity(QuantityKind.BASIC_LAND_TYPES, player="any",
              filter=_LANDS_YOU_CONTROL),
     (), None),
    ("Prismatic Ending", "the number of colors of mana spent to cast ~", False,
     Quantity(QuantityKind.COLORS_SPENT, ref=_SELF), (), None),
    ("Emrakul, the Promised End", "card type among cards in your graveyard",
     False,
     Quantity(QuantityKind.CARD_TYPES_IN_GRAVEYARD, player="you",
              filter=CardFilter(zone="graveyard", owner="you")),
     (), None),
    ("Tyvar, the Pummeler", "the greatest power among creatures you control",
     False,
     Quantity(QuantityKind.GREATEST, player="any", stat="power",
              filter=_CREATURES_YOU_CONTROL),
     (), None),
    ("Solitude", "its power", False,
     Quantity(QuantityKind.POWER, stat="power"), (("ref", "its"),), None),
    ("Karn, the Great Creator", "its mana value", False,
     Quantity(QuantityKind.MANA_VALUE, stat="mana value"),
     (("ref", "its"),), None),
    ("Kaito, Bane of Nightmares", "opponent who lost life this turn", False,
     Quantity(QuantityKind.HISTORY, player="opponents",
              event="players_lost_life"),
     (), None),
    # CR 702.29a: a cycled card was discarded to pay the cycling cost.
    ("Hollow One", "card you've cycled or discarded this turn", False,
     Quantity(QuantityKind.HISTORY, player="you", event="discarded",
              filter=CardFilter(zone="")),
     (), None),
    ("Damping Sphere", "other spell that player has cast this turn", False,
     Quantity(QuantityKind.HISTORY, player="any", event="cast",
              filter=CardFilter(zone="", other=True)),
     (("player", "that player"),), None),
    ("Ocelot Pride", "token you control that entered this turn", False,
     Quantity(QuantityKind.HISTORY, player="any", event="entered",
              filter=CardFilter(zone="", token=True, controller="you")),
     (), None),
    ("Thraben Charm",
     "the number of creatures you control to target creature", False,
     Quantity(QuantityKind.COUNT, player="any", filter=_CREATURES_YOU_CONTROL),
     (), "the number of creatures you control"),
)

# Printed slots the closed table must refuse rather than count as
# something else.
_REFUSED = (
    # A flashback cost rule (A8) is the amount leaf's, not a number.
    ("Snapcaster Mage", "its mana cost", "quantity.mana_cost"),
    ("Past in Flames", "its mana cost", "quantity.mana_cost"),
    ("Wrath of the Skies", "the amount of {e} paid this way",
     "quantity.result_amount"),
    ("Obsidian Charmaw", "land your opponents control that could produce {c}",
     "filter.unparsed"),
)


def _printed(card_db, card, slot):
    from decks.modern_meta import MODERN_DECKS
    assert any(card in (d.get("mainboard") or {}) or
               card in (d.get("sideboard") or {})
               for d in MODERN_DECKS.values()), card
    text = _mask_names(_normalise(card_db.cards[card]))
    assert slot in text, (card, slot, text)


@pytest.mark.timeout(120)
def test_registered_deck_quantity_witnesses_type_exactly_the_printed_rule(card_db):
    """Each witness is the value its printed phrase states: the kind, the
    counted set with every qualifier, the reference (last-known
    information when the source was sacrificed as the cost), and the
    anaphors left for the linker."""
    from engine.effect_grammar.sub import quantity as Q
    for card, slot, left, expected, pending, consumed in _WITNESSES:
        _printed(card_db, card, slot)
        r = Q.parse_quantity(slot, (0, len(slot)), lemma="x",
                             source_left=left)
        consumed = consumed or slot
        assert r.value is not None, (card, r)
        value = expected
        if expected.filter is not None:
            value = dataclasses.replace(value, filter=dataclasses.replace(
                expected.filter, raw=r.value.filter.raw))
        assert r.value == dataclasses.replace(value, raw=consumed), (card, r)
        assert r.pending == pending, (card, r)
        assert slot[slice(*r.span)] == consumed, (card, r)
    for card, slot, detail in _REFUSED:
        _printed(card_db, card, slot)
        r = Q.parse_quantity(slot, (0, len(slot)), lemma="x")
        assert r.value is None, (card, r)
        assert r.unmodelled.detail.split(":")[0] == detail, (card, r)
