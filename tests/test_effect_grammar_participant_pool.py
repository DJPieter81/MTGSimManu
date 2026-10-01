"""The participant sub-grammar over the whole card pool (design doc
2026-09-29, section 5 "Subjects" and "Untargeted choices", section 7;
A9 / M1, A17, A26, A28; E0 step 11).

Every participant slot the pool prints in four positions runs through the
leaf:

* subject -- the noun phrase that opens a clause before its verb ("each
  opponent loses 2 life", "enchanted creature gets +2/+2");
* recipient -- "damage to <P>";
* object -- the object of destroy / exile / sacrifice / tap / return;
* payer -- "unless <P> pays".

The printed chooser of every "of <possessor> choice" / "at random" phrase
runs through `parse_chooser`.

The slots are cut by a deliberately crude stand-in for L0-L3 (the payload
pool's L0 stand-in, ability-word labels stripped, then the patterns
below). A slot the stand-in cuts badly is refused by the leaf -- full
consumption -- which lowers coverage but never hides an exception or yields
a broader participant. A slot that prints a counted "target" word is the
target leaf's: it must be refused ``participant.targeted`` and is counted
apart from the coverage share.

It asserts that the leaf never raises, returns exactly one of a typed
participant or an UNMODELLED stage with a closed detail code, keeps every
span inside the slot, is deterministic across a cache clear, and types at
least the measured share of each position. The coverage table is printed
(``pytest -s``) for the census.
"""
from __future__ import annotations

import re
from collections import Counter

import pytest

from engine.effect_model import Selector, SelectorKind
from engine.effect_spec import (CardFilter, Chooser, Ref, RefKind, RefPart,
                                Stage, canonical)
from tests.test_effect_grammar_filter_pool import _mask_names
from tests.test_effect_grammar_payload_pool import _SENT_RE, _normalise

# Ability-word and chapter labels (CR 207.2c, 714.2) are structure.
_LABEL_RE = re.compile(r"^(?:[ivx]+(?:, [ivx]+)*|[a-z][a-z' ]*) - ")
_VERB = (r"(?:draw|discard|lose|gain|sacrifice|mill|search(?:es)?|shuffle"
         r"|create|reveal|put|return|exile|deal|get|have|has|may|can't|can"
         r"|attack|block|become|fight|pay|look|choose|cast|tap|untap|scry"
         r"|surveil|skip|enter|die|do|does|doesn't|don't|is|are|explore"
         r"|connive|proliferate|investigate|add|win|cost)s?")
# Words that open a frame, a connective or an imperative, never a subject.
_NOT_SUBJECT = (r"when|whenever|at|if|as|until|for|unless|this|instead"
                r"|otherwise|each time|during|only|activate|spend|then|and|or"
                r"|except|where|enchant|put|return|tap|untap|exile|destroy"
                r"|sacrifice|counter|attach|choose|reveal|search|shuffle|add"
                r"|create|draw|discard|look|gain|lose|mill|scry|surveil|cast"
                r"|play|pay|exchange|prevent|copy|regenerate|transform|remove"
                r"|double|deal|fight|there|x")
_POSITIONS = (
    ("subject", re.compile(
        r"(?:^|, (?:then )?|\bthen |; )(?!(?:%s)\b)"
        r"(?P<s>[a-z~][^,;:.]*?) (?=%s\b)" % (_NOT_SUBJECT, _VERB))),
    ("recipient", re.compile(
        r"\bdamage to (?P<s>.+?)(?=[,;:]|$| and \w+ damage| for each"
        r"| equal to| instead| unless| if | this turn| where )")),
    ("object", re.compile(
        r"\b(?:destroy|exile|sacrifice|tap|return)s? (?P<s>.+?)(?=[,;:]|$"
        r"| and | then | unless | if | at the beginning | until | this turn"
        r"| instead| where |(?<!\bup) to | onto | into | with | under "
        r"| of (?:their|your|his or her|an opponent's) choice| at random)")),
    ("payer", re.compile(r"\bunless (?P<s>.+?) pays?\b")),
)
_CHOICE_RE = re.compile(r"\bchoice\b|\bat random\b")
# A subject cut that stops at the verb of a relative clause ("spells you
# cast cost ...") is the stand-in's error, not a slot.
_RELATIVE_CUT_RE = re.compile(r". (?:you|opponents?|players?)$")

def _closed_detail(detail: str) -> bool:
    """'<leaf>.<code>[:<param>]' whose code is in that leaf's closed
    DETAIL_CODES, for any grammar leaf (a callee's refusal propagates
    unchanged through its caller)."""
    import importlib
    leaf, code = detail.split(":")[0].split(".")
    name = {"destination": "dest"}.get(leaf, leaf)
    mod = importlib.import_module("engine.effect_grammar.sub." + name)
    return mod.LEAF == leaf and code in mod.DETAIL_CODES



def _sentences(card_db):
    for template in {id(v): v for v in card_db.cards.values()}.values():
        if not template.oracle_text:
            continue
        for sentence in _SENT_RE.split(_normalise(template)):
            s = _mask_names(sentence.strip().rstrip("."))
            yield _LABEL_RE.sub("", s)


def _slots(card_db):
    seen = set()
    chooser = []
    for s in _sentences(card_db):
        for position, rx in _POSITIONS:
            for m in rx.finditer(s):
                key = (position, m.group("s"))
                if key in seen or (position == "subject" and
                                   _RELATIVE_CUT_RE.search(m.group("s"))):
                    continue
                seen.add(key)
                yield position, s, m.span("s")
        if _CHOICE_RE.search(s) and ("chooser", s) not in seen:
            seen.add(("chooser", s))
            chooser.append(s)
    for s in chooser:
        yield "chooser", s, (0, len(s))


def _run(slots):
    from engine.effect_grammar.sub import participant as P
    out = []
    for position, host, span in slots:
        if position == "chooser":
            r = P.parse_chooser(host, span, lemma="x")
        else:
            r = P.parse_participant(host, span, lemma="x")
        out.append((position, host, span, r))
    return out


def _key(r):
    if r is None:
        return None
    return canonical((repr(r.value), r.unmodelled, r.span, r.rest_spans,
                      r.pending, r.flags, r.amount))


# Typed share per position over distinct slots, measured 2026-10-01 on this
# branch's DB (22.7k cards; 3.6k distinct slots, 844 of them
# target-routed and excluded from the share): subject 472/1052 (44.9%),
# recipient 74/145 (51.0%), object 525/912 (57.6%), payer 8/9 (88.9%),
# chooser 321/323 (99.4%); 1400/2441 (57.4%) overall. Most refusals
# (765) are object groups the filter leaf refuses (the filter pool's own
# measure) and stand-in cuts that land inside a clause ("spells you",
# "it also", "the same"); the rest are the closed table working --
# characteristics ("~'s power and toughness"), A17 unions ("a permanent or
# player"), library positions ("the top card of your library"), player
# sets with relative clauses ("each opponent who ..."), and chooser
# possessors the table lacks ("of its controller's choice"). Floors sit a
# few points under the measurement: the parse is deterministic, so a fall
# below a floor is a closed-table regression, while a DB refresh moves the
# share by far less.
_FLOORS = {"subject": 0.41, "recipient": 0.47, "object": 0.53,
           "payer": 0.75, "chooser": 0.95}


# Pool-wide (~3.6k distinct participant slots). Measured 2026-10-01 on
# this container (quiet, 4 cores): ~1.2 s for the slot cut and two passes,
# plus ~17 s when it is the first test of the process to load the shared
# card DB. 120 s bounds a hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_the_participant_leaf_types_or_refuses_every_pool_participant_slot_deterministically(card_db):
    from engine.effect_grammar.sub import participant as P
    slots = list(_slots(card_db))
    assert len(slots) > 3000, len(slots)
    P.clear_caches()
    first = _run(slots)
    P.clear_caches()
    second = _run(slots)
    assert [(h, s, _key(r)) for _, h, s, r in first] == \
        [(h, s, _key(r)) for _, h, s, r in second]

    typed, total, routed = Counter(), Counter(), Counter()
    kinds, codes = Counter(), Counter()
    for position, host, (a, b), r in first:
        if position == "chooser":
            if r is None:
                continue
            total[position] += 1
            assert (r.value is None) != (r.unmodelled is None), (host, r)
            assert a <= r.span[0] <= r.span[1] <= b
            assert all(a <= x <= y <= b for x, y in r.rest_spans)
            if r.value is not None:
                assert isinstance(r.value, Chooser)
                typed[position] += 1
                kinds["chooser." + r.value.name] += 1
            else:
                codes[r.unmodelled.detail.split(":")[0]] += 1
            continue
        assert (r.value is None) != (r.unmodelled is None), (host, r)
        assert a <= r.span[0] <= r.span[1] <= b, (host, r)
        # The one rest is a moved reference's printed source zone.
        assert all(host.startswith("from ", x) for x, _ in r.rest_spans), (host, r)
        assert all(r.span[1] <= x <= y <= b for x, y in r.rest_spans), (host, r)
        if r.value is not None:
            assert isinstance(r.value, (Selector, Ref, P.Anaphor,
                                        CardFilter)), (host, r)
            kind_flags = r.flags & {P.PLAYER, P.OBJECT, P.GROUP}
            # An anaphor that may name a player or an object ("they",
            # "them") is both; every other participant is exactly one.
            if isinstance(r.value, P.Anaphor) and r.value.player is None:
                assert kind_flags == {P.PLAYER, P.OBJECT}, (host, r)
            else:
                assert len(kind_flags) == 1, (host, r)
            assert (P.GROUP in r.flags) == isinstance(r.value, CardFilter)
            total[position] += 1
            typed[position] += 1
            kinds[type(r.value).__name__] += 1
            continue
        u = r.unmodelled
        leaf, code = u.detail.split(":")[0].split(".")
        assert _closed_detail(u.detail), u.detail
        assert leaf in (P.LEAF, "filter"), u.detail
        assert u.lemma == "x"
        assert u.stage is (Stage.FILTER if leaf == "filter"
                           else Stage.REFERENCE), u
        assert host[slice(*r.span)] == host[a:b].strip(), (host, r)
        if code == "targeted":
            routed[position] += 1
            continue
        total[position] += 1
        codes[u.detail.split(":")[0]] += 1

    print("\nparticipant slots by position (typed / total; target-routed apart):")
    for position in sorted(total):
        print("  %-10s %5d / %5d (%.1f%%), %d target-routed" % (
            position, typed[position], total[position],
            100 * typed[position] / total[position], routed[position]))
    print("  overall    %5d / %5d (%.1f%%)" % (
        sum(typed.values()), sum(total.values()),
        100 * sum(typed.values()) / sum(total.values())))
    print("typed kinds:", dict(kinds.most_common()))
    print("unmodelled codes:", dict(codes.most_common()))
    for position, floor in _FLOORS.items():
        assert total[position], position
        assert typed[position] / total[position] >= floor, (
            position, typed[position], total[position])


# ── Registered-deck witnesses ──────────────────────────────────────────
# Participant slots printed by registered-deck cards (decks/modern_meta.py),
# with the exact participant the leaf must return. The card name only
# locates the printed text in the DB; the leaf never sees it.
#
# Known deviation (E0 step 21, design section 18): real registered-deck
# card names are to live only in tests/fixtures/effect_grammar_witnesses.json.
# That fixture is created by step 21 with its spec-chain schema; until
# then these rows sit here, as the filter, quantity, lexicon, keywords and
# normalize pool tests' rows do, and move into the fixture with theirs.

_YOU = Selector(SelectorKind.PLAYER)
_OPPONENTS = Selector(SelectorKind.OPPONENTS)
_ALL = Selector(SelectorKind.ALL_PLAYERS)


def _anaphor(**kw):
    from engine.effect_grammar.sub import participant as P
    return P.Anaphor(**kw)


# (card, printed sentence fragment, slot inside it, value factory,
#  pending, flags beyond the kind flag)
_WITNESSES = (
    ("All Is Dust", "each player sacrifices all permanents", "each player",
     lambda: _ALL, (), {"each"}),
    ("Narset, Parter of Veils", "each opponent can't draw", "each opponent",
     lambda: _OPPONENTS, (), {"each"}),
    ("Silence", "your opponents can't cast spells", "your opponents",
     lambda: _OPPONENTS, (), set()),
    ("Glaring Fleshraker", "damage to each opponent", "each opponent",
     lambda: _OPPONENTS, (), {"each"}),
    ("Static Prison", "unless you pay {e}", "you", lambda: _YOU, (), set()),
    ("Ulamog, the Ceaseless Hunger", "defending player exiles",
     "defending player", lambda: Ref(RefKind.DEFENDING_PLAYER), (), set()),
    ("Curse of Shaken Faith", "whenever enchanted player casts",
     "enchanted player", lambda: Ref(RefKind.ATTACHED, noun="player"), (),
     set()),
    ("Cori-Steel Cutter", "equipped creature gets +1/+1", "equipped creature",
     lambda: Ref(RefKind.ATTACHED, noun="creature"), (), set()),
    ("Detective's Phoenix", "enchanted creature gets +2/+2",
     "enchanted creature", lambda: Ref(RefKind.ATTACHED, noun="creature"),
     (), set()),
    ("Abstergo Entertainment", "exile ~", "~", lambda: Ref(RefKind.SELF),
     (), set()),
    # Rule 0 (A23) binds the "its" of an unless frame to the spec's own
    # target; the leaf leaves it for the linker.
    ("Flusterstorm", "unless its controller pays {1}", "its controller",
     lambda: Ref(RefKind.CONTROLLER_OF), (("ref", "its"),), set()),
    ("Avengers Disassembled", "its controller may search", "its controller",
     lambda: Ref(RefKind.CONTROLLER_OF), (("ref", "its"),), set()),
    ("Subtlety", "its owner puts it", "its owner",
     lambda: Ref(RefKind.OWNER_OF), (("ref", "its"),), set()),
    ("Boseiju, Who Endures", "that player may search", "that player",
     lambda: _anaphor(noun="player", player=True),
     (("player", "that player"),), set()),
    ("Goryo's Vengeance", "that creature gains haste", "that creature",
     lambda: _anaphor(noun="creature"), (("ref", "that creature"),), set()),
    ("Chalice of the Void", "counter that spell", "that spell",
     lambda: _anaphor(noun="spell"), (("ref", "that spell"),), set()),
    ("Galvanic Discharge", "damage to that permanent", "that permanent",
     lambda: _anaphor(noun="permanent"), (("ref", "that permanent"),), set()),
    # "them" may be objects (plural) or one player (singular "them"), so
    # its number and kind are the linker's.
    ("Dalkovan Encampment", "sacrifice them", "them",
     lambda: _anaphor(plural=None, player=None), (("either", "them"),),
     set()),
    ("Curse of Shaken Faith", "deals 2 damage to them", "them",
     lambda: _anaphor(plural=None, player=None), (("either", "them"),),
     set()),
    # A26: the card a linked exile ability exiled.
    ("Ugin's Labyrinth", "return the exiled card", "the exiled card",
     lambda: _anaphor(noun="card", participle="exiled"),
     (("ref", "the exiled card"),), set()),
    # A28: partitives of an earlier result.
    ("Expressive Iteration", "exile one of them", "one of them",
     lambda: _anaphor(part=RefPart.ONE, n=_one()),
     (("ref", "one of them"),), set()),
    ("Devourer of Destiny", "exile the rest", "the rest",
     lambda: _anaphor(plural=True, part=RefPart.REST),
     (("ref", "the rest"),), set()),
)


def _one():
    from engine.effect_spec import Amount, AmountKind
    return Amount(AmountKind.LITERAL, n=1)


# (card, printed fragment, slot, Chooser)
_CHOOSER_WITNESSES = (
    ("Archon of Cruelty", "a creature or planeswalker of their choice",
     "of their choice", Chooser.PARTICIPANT),
    ("Lorehold Charm", "a nontoken artifact of their choice",
     "of their choice", Chooser.PARTICIPANT),
    ("Sheoldred's Edict", "a nontoken creature of their choice",
     "of their choice", Chooser.PARTICIPANT),
    ("Burning Inquiry", "discards three cards at random", "at random",
     Chooser.RANDOM),
)

# (card, printed fragment, slot, refusal detail)
_REFUSED = (
    # Routed: a counted target word is the target leaf's.
    ("Archon of Cruelty", "target opponent sacrifices", "target opponent",
     "participant.targeted"),
    # A characteristic is the quantity leaf's.
    ("Past in Flames", "the flashback cost is equal to its mana cost",
     "the flashback cost", "participant.reference"),
)


def _printed(card_db, card, fragment):
    from decks.modern_meta import MODERN_DECKS
    assert any(card in (d.get("mainboard") or {}) or
               card in (d.get("sideboard") or {})
               for d in MODERN_DECKS.values()), card
    text = _mask_names(_normalise(card_db.cards[card]))
    assert fragment in text, (card, fragment, text)
    return fragment


@pytest.mark.timeout(120)
def test_registered_deck_participant_witnesses_type_exactly_the_printed_participant(card_db):
    """Each witness is the participant its printed phrase states: the
    player set relative to the resolving controller, the SELF / ATTACHED /
    DEFENDING_PLAYER reference, the possessive ref, or the anaphor with
    its noun, number, participle and part -- and the chooser of each
    printed untargeted choice."""
    from engine.effect_grammar.sub import participant as P
    for card, fragment, slot, value, pending, flags in _WITNESSES:
        host = _printed(card_db, card, fragment)
        a = host.index(slot)
        r = P.parse_participant(host, (a, a + len(slot)), lemma="x")
        assert r.value == value(), (card, r)
        assert r.pending == pending, (card, r)
        assert set(r.flags) - {P.PLAYER, P.OBJECT, P.GROUP} == flags, (card, r)
        assert host[slice(*r.span)] == slot
    for card, fragment, slot, chooser in _CHOOSER_WITNESSES:
        host = _printed(card_db, card, fragment)
        r = P.parse_chooser(host, lemma="x")
        assert r.value is chooser and host[slice(*r.span)] == slot, (card, r)
    for card, fragment, slot, detail in _REFUSED:
        host = _printed(card_db, card, fragment)
        a = host.index(slot)
        r = P.parse_participant(host, (a, a + len(slot)), lemma="x")
        assert r.value is None, (card, r)
        assert r.unmodelled.detail.split(":")[0] == detail, (card, r)
