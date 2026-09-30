"""The filter sub-grammar over the whole card pool (design doc 2026-09-29,
section 5 subjects and untargeted choices; A19, A21, A22; E0 step 11).

Every untargeted object description the pool prints in the four filter
positions runs through the leaf:

* search: "search <player's> library for <FILTER>" (the caller's zone is
  the library);
* mass: "destroy / exile / tap / untap / sacrifice all|each <FILTER>",
  "return all|each <FILTER> to ...";
* choice: "sacrifice(s) a|an|another|N <FILTER>" (the "of their choice"
  chooser is the caller's);
* quantity: "for each <FILTER>".

The slots are cut by a deliberately crude stand-in for L0-L3 (the payload
pool's L0 stand-in, then the verb patterns below). A slot the stand-in cuts
badly is refused by the leaf -- full consumption (A21) -- which lowers
coverage but never hides an exception or yields a broader filter.

It asserts that the leaf never raises, returns exactly one of a typed
CardFilter or an UNMODELLED(FILTER) stage with a closed detail code, spans
inside the slot, is deterministic across a cache clear, and types at least
the measured share of each family. The coverage table is printed (``pytest
-s``) for the census.
"""
from __future__ import annotations

import dataclasses
import re
from collections import Counter

import pytest

from engine.effect_spec import (Amount, AmountKind, CardFilter, Stage,
                                canonical)
from tests.test_effect_grammar_payload_pool import _SENT_RE, _normalise

_PLAYER_POSS = (r"(?:your|their|his or her|its owner's|its controller's"
                r"|that player's|target player's|target opponent's"
                r"|an opponent's|each opponent's)")
_END = (r"(?=[,;:]|$| then | unless | if | at the beginning | until "
        r"| this turn| instead| where )")
_FAMILIES = (
    ("search", "search", "library", re.compile(
        r"\bsearch(?:es)? %s library for (.+?)(?=,| and (?:put|reveal|exile"
        r"|cast|you)\b| then |$)" % _PLAYER_POSS)),
    ("mass", "", "", re.compile(
        r"\b(?:destroy|exile|tap|untap|sacrifice)s? ((?:all|each) .+?)"
        r"(?= and (?:put|return|create|draw|exile|you|then|each|they|it)\b"
        r"|[,;:]|$| then | unless | if | at the beginning | until "
        r"| this turn| instead| where )")),
    ("mass", "return", "", re.compile(
        r"\breturns? ((?:all|each) .+?)(?= to | onto |[,;:]|$)")),
    ("choice", "sacrifice", "", re.compile(
        r"\bsacrifices? ((?:a|an|another|one|two|three|x|up to \w+) .+?)"
        r"(?= of (?:their|his or her|your) choice| and |[,;:]|$| then "
        r"| unless | if | at the beginning | until | this turn| instead)")),
    ("quantity", "for each", "", re.compile(r"\bfor each (.+?)(?=[,;:]|$)")),
)


# L0 step 2 stand-in: "named <Name>" is masked ⟨nk⟩. The stand-in knows no
# card names, so the name runs to the first word that cannot continue one.
_NAME_STOP = (r"and|or|and/or|with|from|in|that|you|to|onto|into|on|this|each"
              r"|if|then|unless|instead|at|for|as|until|where|except|other"
              r"|among|exiled|they|their|your")
_NAMED_RE = re.compile(r"\bnamed (?!~|⟨)[a-z0-9'\-]+(?: (?!(?:%s)\b)[a-z0-9'\-]+)*"
                       % _NAME_STOP)


def _mask_names(sentence: str) -> str:
    k = iter(range(1 << 16))
    return _NAMED_RE.sub(lambda _m: "⟨n%d⟩" % next(k), sentence)


def _slots(card_db):
    seen = set()
    for template in {id(v): v for v in card_db.cards.values()}.values():
        if not template.oracle_text:
            continue
        for sentence in _SENT_RE.split(_normalise(template)):
            s = _mask_names(sentence.strip().rstrip("."))
            for fam, lemma, zone, rx in _FAMILIES:
                for m in rx.finditer(s):
                    key = (fam, m.group(1), zone)
                    if key in seen:
                        continue
                    seen.add(key)
                    lemma_ = lemma or s[m.start():].split()[0].rstrip("s")
                    yield fam, lemma_, zone, s, m.span(1)


def _run(slots):
    from engine.effect_grammar.sub import filter as F
    out = []
    for fam, lemma, zone, host, span in slots:
        r = F.parse_filter(host, span, lemma=lemma, zone=zone)
        out.append((fam, host, span, r))
    return out


# Typed share per family, measured 2026-09-30 on this branch's DB (22.7k
# cards): search 187/260 (71.9%), choice 147/209 (70.3%), mass 162/256
# (63.3%), quantity 210/453 (46.4%); 706/1178 (59.9%) overall. The refusals
# are A21 working as intended, not table gaps: history and relative clauses
# ("that died this turn", "you've cast"), "with the same name as",
# computed bounds ("mana value equal to the number of ..."), alternatives
# the stand-in cut into the slot ("a creature or pay <cost>"), NP unions ("a
# basic land card or a gate card"), and quantity slots that are no object
# ("color among ...", "1 life you lost", "+1/+1 counter on ..."). The floors
# sit a few points under the measurement: the parse is deterministic, so a
# fall below a floor is a closed-table regression, while a DB refresh moves
# the share by far less.
_FLOORS = {"search": 0.68, "choice": 0.67, "mass": 0.60, "quantity": 0.43}


# Pool-wide (~1.2k distinct filter slots). Measured 2026-09-30 on this
# container (quiet, 4 cores): ~0.8 s for the slot cut and two passes, plus
# ~16 s when it is the first test of the process to load the shared card DB. 120 s bounds a hang with room for a slower 2-core
# CI runner.
@pytest.mark.timeout(120)
def test_the_filter_leaf_types_or_refuses_every_pool_filter_slot_deterministically(card_db):
    from engine.effect_grammar.sub import filter as F
    slots = list(_slots(card_db))
    assert len(slots) > 1000, len(slots)
    F.clear_caches()
    first = _run(slots)
    F.clear_caches()
    second = _run(slots)
    assert [canonical(r) for *_, r in first] == \
        [canonical(r) for *_, r in second]

    total, typed = Counter(), Counter()
    unmodelled = Counter()
    for fam, host, span, r in first:
        total[fam] += 1
        assert (r.value is None) != (r.unmodelled is None), (host, r)
        assert span[0] <= r.span[0] <= r.span[1] <= span[1], (host, span, r)
        if r.value is not None:
            assert isinstance(r.value, CardFilter)
            assert r.value.raw == host[slice(*r.span)]
            typed[fam] += 1
        else:
            u = r.unmodelled
            assert u.stage is Stage.FILTER and u.lemma, (host, u)
            code = u.detail.split(":")[0].split(".", 1)[1]
            assert u.detail.startswith("filter.") and code in F.DETAIL_CODES
            unmodelled[u.detail.split(":")[0]] += 1

    print("\nfilter leaf pool coverage (typed / slots):")
    for fam in sorted(total):
        print(f"  {fam:10s} {typed[fam]:6d} / {total[fam]:6d}  "
              f"{typed[fam] / total[fam]:6.1%}")
    all_typed, all_total = sum(typed.values()), sum(total.values())
    print(f"  {'all':10s} {all_typed:6d} / {all_total:6d}  "
          f"{all_typed / all_total:6.1%}")
    print("  unmodelled:", unmodelled.most_common())
    for fam, floor in _FLOORS.items():
        assert total[fam], fam
        assert typed[fam] / total[fam] >= floor, (fam, typed[fam], total[fam])


# ── Registered-deck witnesses ──────────────────────────────────────────
# Filter slots printed by registered-deck cards (decks/modern_meta.py), with
# the exact CardFilter the leaf must return. The share floors above count a
# slot as typed whenever a value exists; these pin that the value is the
# printed rule, not a plausible broader one. The card name only locates the
# printed text in the DB; the leaf never sees it.

def _lit(n):
    return Amount(AmountKind.LITERAL, n=n)


_ONE = _lit(1)
_X = Amount(AmountKind.X, n=1)

# (card, printed slot, caller zone, amount, flags, pending, CardFilter)
_WITNESSES = (
    ("Misty Rainforest", "a forest or island card", "library", _ONE, (), (),
     CardFilter(zone="library", subtypes=frozenset({"forest", "island"}))),
    ("Green Sun's Zenith", "a green creature card with mana value x or less",
     "library", _ONE, (), (),
     CardFilter(zone="library", types=frozenset({"creature"}),
                colors=frozenset({"G"}),
                stat_bounds=(("mana_value", "<=", _X),))),
    ("Summoner's Pact", "a green creature card", "library", _ONE, (), (),
     CardFilter(zone="library", types=frozenset({"creature"}),
                colors=frozenset({"G"}))),
    ("Primeval Titan", "up to two land cards", "library",
     Amount(AmountKind.UP_TO, n=2), (), (),
     CardFilter(zone="library", types=frozenset({"land"}))),
    ("Ugin, Eye of the Storms", "any number of colorless nonland cards",
     "library", Amount(AmountKind.ANY_NUMBER), (), (),
     CardFilter(zone="library", not_types=frozenset({"land"}),
                colorless=True)),
    ("Brotherhood's End", "all artifacts with mana value 3 or less", "", None,
     ("all",), (),
     CardFilter(types=frozenset({"artifact"}),
                stat_bounds=(("mana_value", "<=", _lit(3)),))),
    ("Wrath of God", "all creatures", "", None, ("all",), (),
     CardFilter(types=frozenset({"creature"}))),
    ("Pyroclasm", "each creature", "", None, ("each",), (),
     CardFilter(types=frozenset({"creature"}))),
    ("Rough // Tumble", "each creature without flying", "", None, ("each",),
     (), CardFilter(types=frozenset({"creature"}),
                    without_keywords=frozenset({"flying"}))),
    ("Oblivion Stone", "each nonland permanent without a fate counter on it",
     "", None, ("each",), (),
     CardFilter(not_types=frozenset({"land"}), counters=(("fate", False),))),
    ("Violent Outburst", "creatures you control", "", None, (), (),
     CardFilter(types=frozenset({"creature"}), controller="you")),
    ("Sheoldred's Edict", "a nontoken creature", "", _ONE, (), (),
     CardFilter(types=frozenset({"creature"}), token=False)),
    ("Sheoldred's Edict", "a creature token", "", _ONE, (), (),
     CardFilter(types=frozenset({"creature"}), token=True)),
    ("Lorehold Charm", "a nontoken artifact", "", _ONE, (), (),
     CardFilter(types=frozenset({"artifact"}), token=False)),
    ("Archon of Cruelty", "a creature or planeswalker", "", _ONE, (), (),
     CardFilter(types=frozenset({"creature", "planeswalker"}))),
    ("Living End", "all creature cards from their graveyard", "", None,
     ("all",), (("owner", "their"),),
     CardFilter(zone="graveyard", types=frozenset({"creature"}))),
    ("Living End", "all creatures they control", "", None, ("all",),
     (("controller", "they"),), CardFilter(types=frozenset({"creature"}))),
    ("All Is Dust", "all permanents they control that are one or more colors",
     "", None, ("all",), (("controller", "they"),),
     CardFilter(classes=frozenset({"colored"}))),
    ("Sanctifier en-Vec", "all cards that are black or red from all graveyards",
     "", None, ("all",), (),
     CardFilter(zone="graveyard", colors=frozenset({"B", "R"}))),
    ("Fiend Artisan", "creature card in your graveyard", "", None, (), (),
     CardFilter(zone="graveyard", types=frozenset({"creature"}), owner="you")),
    ("Cranial Plating", "artifact you control", "", None, (), (),
     CardFilter(types=frozenset({"artifact"}), controller="you")),
    ("Kaheera, the Orphanguard",
     "each other creature you control that's a cat, elemental, nightmare, "
     "dinosaur, or beast", "", None, ("each",), (),
     CardFilter(types=frozenset({"creature"}), other=True, controller="you",
                subtypes=frozenset({"cat", "elemental", "nightmare",
                                    "dinosaur", "beast"}))),
    ("Monumental Henge", "a historic card from among them", "", _ONE, (),
     (("among", "them"),), CardFilter(zone="", classes=frozenset({"historic"}))),
    ("Seasoned Pyromancer", "nonland card discarded this way", "", None, (),
     (("result", "discarded"),),
     CardFilter(zone="", not_types=frozenset({"land"}))),
)

# Printed slots the tables must refuse rather than type broader (A21).
_REFUSED = (
    ("Engineered Explosives", "each nonland permanent with mana value equal "
     "to the number of charge counters on ~", "filter.stat"),
    ("Urza's Saga", "an artifact card with mana cost {0} or {1}", "filter.stat"),
    ("Starfield Shepherd", "a basic plains card or a creature card with mana "
     "value 1 or less", "filter.np_union"),
    ("Boseiju, Who Endures", "a land card with a basic land type",
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
def test_registered_deck_filter_witnesses_type_exactly_the_printed_rule(card_db):
    """Each witness is the value its printed phrase states: the determiner
    is the amount or a quantifier flag, the anaphors are left pending, and
    no qualifier is dropped."""
    from engine.effect_grammar.sub import filter as F
    from engine.effect_model import is_supported_filter_entry
    for card, slot, zone, amount, flags, pending, expected in _WITNESSES:
        _printed(card_db, card, slot)
        r = F.parse_filter(slot, (0, len(slot)), lemma="x", zone=zone)
        assert r.value == dataclasses.replace(expected, raw=slot), (card, r)
        assert (r.amount, r.flags, r.pending) == (
            amount, frozenset(flags), pending), (card, r)
    # A22: the keyword exclusion executes; "creatures you control" is typed
    # but its controller entry is not one covers_object evaluates in E0.
    sweep = F.parse_filter("each creature without flying").value
    assert ("without_keyword", "flying") in sweep.as_tuple()
    assert is_supported_filter_entry("without_keyword", "flying")
    team = F.parse_filter("creatures you control").value
    assert ("controller", "you") in team.as_tuple()
    assert not is_supported_filter_entry("controller", "you")
    for card, slot, detail in _REFUSED:
        _printed(card_db, card, slot)
        r = F.parse_filter(slot, (0, len(slot)), lemma="x", zone="library")
        assert r.value is None, (card, r)
        assert r.unmodelled.detail.split(":")[0] == detail, (card, r)
