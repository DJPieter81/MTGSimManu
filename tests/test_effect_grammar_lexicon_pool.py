"""The verb lexicon over the whole card pool (design doc 2026-09-29, section
4; A12, A13, A18, A19; E0 step 11).

Every effect sentence of every face's L0 text (`effect_grammar.normalize`,
the real L0) runs through `lexicon.find_verb`, and every loyalty line
through `lexicon.parse_loyalty_cost`. The clause frame is cut by a crude
stand-in for L1/L2 (the structure layer is another leaf): a keyword line
is skipped, a loyalty cost, an activation cost before a depth-0 colon, an
ability-word label and a trigger head up to its first comma are dropped,
and the paragraph is split into sentences at '. '. A frame word the
stand-in leaves in ("if you do, ...") is read past by the lexicon like any
non-verb word, so the measure is the lexicon's own.

The leaf must not raise, must return exactly one of a typed entry or an
Unmodelled (never a guess), and must be deterministic across a cache
clear. The coverage counts are printed (``pytest -s``) and pinned below
their measurement. The registered-deck witnesses at the end pin the exact
reading of printed text from decks/modern_meta.py cards. Card names appear
only in the witness fixtures, never in `engine/`.
"""
from __future__ import annotations

import re
from collections import Counter

import pytest

from engine.effect_model import ModKind
from engine.effect_spec import Amount, AmountKind, Stage, Verb, canonical

# Measured 2026-10-01 on this branch's DB (22.7k cards; see the printed
# report): 39,676 effect sentences after the stand-in frame; 37,751 (95.1%)
# read a typed verb, 431 (1.1%) a recognised-unsupported action, 7 a
# verb-only word no reading accepts, 1,487 (3.7%) no lexicon verb (mostly
# characteristic-defining statics "~'s power is equal to ...", frames the
# stand-in leaves whole, and CR 701 actions missing from payload's table:
# earthbend, airbend, distribute). All 820 loyalty lines are read; 17 of
# them are variable [+X]/[-X] lines on 288 planeswalker faces. The floors
# sit a little under the measurement: the parse is deterministic, so a
# fall below them is a closed-table regression, not noise.
TYPED_FLOOR = 0.94
VERB_FOUND_FLOOR = 0.95

_TRIGGER_RE = re.compile(r"^(?:when|whenever|at)\b")
_LABEL_RE = re.compile(r"^[a-z' ,]+ - (?!\{)")
_SENT_RE = re.compile(r"[^.]+\.?")
# Sentences L1/L2 absorb before L4 reads a lemma: the closed rider list
# (activation and mana-spend restrictions, "it's still a land", trigger
# frequency), CR 614 entry replacements ("~ enters tapped / with ...",
# REPLACEMENT hosts), level-up and d20 table rows and bare P/T lines
# (UNKNOWN hosts). They are counted apart, never in the lexicon's share.
_NOT_L4_RE = re.compile(
    r"^(?:activate (?:this ability )?only|spend (?:this mana )?only|"
    r"(?:it's|they're) still|this ability triggers only|(?:~|it) enters\b|"
    r"level \d|\d+(?:-\d+|\+)? \||[\dx*+]+/[\dx*+]+\.?$)")


def _faces(db):
    """(printed text, Facts, MTGJSON keywords) for every face."""
    from engine.effect_grammar import normalize as N
    out = []
    for t in {id(v): v for v in db.cards.values()}.values():
        legendary = any(getattr(s, "value", s) == "legendary"
                        for s in t.supertypes)
        faces = ((t.oracle_text or "", t.card_types, t.subtypes),
                 (getattr(t, "back_face_oracle", "") or "",
                  t.back_face_types, t.back_face_subtypes))
        for text, types, subs in faces:
            if not text:
                continue
            tc = frozenset(getattr(c, "value", str(c)) for c in types)
            character = "planeswalker" in tc or (legendary and "creature" in tc)
            out.append((text, N.Facts(
                names=N.self_names(t.name, is_legendary=legendary,
                                   is_character=character,
                                   subtypes=tuple(subs) if "creature" in tc else ()),
                type_class=tc, is_spell=bool({"instant", "sorcery"} & tc),
                is_legendary=legendary, is_planeswalker="planeswalker" in tc)))
    return out


def _l0_hosts(faces):
    """Every L0 host text: face paragraphs and quoted-ability texts."""
    from engine.effect_grammar import normalize as N
    hosts = []
    for text, facts in faces:
        r = N.normalize(text, facts)
        hosts.extend(r.paragraphs)
        for q in r.quotes:
            hosts.extend(q.split("\n"))
    return hosts


@pytest.fixture(scope="module")
def hosts():
    from tests._card_db_cache import shared_card_database
    return _l0_hosts(_faces(shared_card_database()))


def _clause_start(p: str, start: int) -> int:
    """The stand-in frame: past an activation cost and a trigger head."""
    colon = p.find(": ", start)
    period = p.find(". ", start)
    if colon >= 0 and (period < 0 or colon < period) and "⟨" not in p[start:colon]:
        start = colon + 2
    if _TRIGGER_RE.match(p, start):
        comma = p.find(", ", start)
        if comma >= 0 and (period < 0 or comma < period):
            start = comma + 2
    return start


def _run(hosts):
    """(counts, canonical digest) over every effect sentence and loyalty
    line."""
    from engine.effect_grammar import keywords as K
    from engine.effect_grammar import lexicon as L
    counts, digest = Counter(), []
    for p in hosts:
        if not p or K.parse_keyword_line(p) is not None:
            continue
        loyal = L.parse_loyalty_cost(p)
        start = 0
        if loyal.value is not None or loyal.unmodelled is not None:
            counts["loyalty_lines"] += 1
            counts["loyalty_typed"] += loyal.value is not None
            start = loyal.span[1]
            digest.append(canonical((p, loyal.value, loyal.span)))
        m = _LABEL_RE.match(p, start)
        if m:
            start = m.end()
        start = _clause_start(p, start)
        for m in _SENT_RE.finditer(p, start):
            span = m.span()
            if not m.group(0).strip(" ."):
                continue
            if _NOT_L4_RE.match(m.group(0).strip()):
                counts["not_l4"] += 1
                continue
            r = L.find_verb(p, span)
            assert (r.value is None) != (r.unmodelled is None), (p, span, r)
            assert span[0] <= r.span[0] <= r.span[1] <= span[1], (p, span, r)
            counts["sentences"] += 1
            if r.value is not None:
                assert isinstance(r.value, L.LexEntry)
                assert all(span[0] <= a <= b <= span[1] for a, b in r.rest_spans)
                counts["typed"] += 1
                counts["verb:" + r.value.verb.value] += 1
            else:
                u = r.unmodelled
                assert u.detail.split(".")[0] == "lexicon", u
                assert u.detail.split(".")[1].split(":")[0] in L.DETAIL_CODES
                counts[u.stage.value] += 1
                if u.stage is not Stage.NO_LEMMA:
                    assert u.lemma, (p, r)
            digest.append(canonical((p, span, r.value, r.unmodelled, r.span,
                                     r.rest_spans, r.flags, r.amount)))
    return counts, digest


# Pool-wide (~23k faces, ~40k effect sentences). Measured 2026-10-01 on this
# container (quiet, 4 cores): ~7 s for the L0 build plus two passes, plus
# ~16 s when it is the first test of the process to load the shared card
# DB. 120 s bounds a hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_the_lexicon_reads_or_refuses_every_pool_effect_sentence_deterministically(hosts):
    from engine.effect_grammar import lexicon as L
    L.clear_caches()
    counts, first = _run(hosts)
    L.clear_caches()
    _, second = _run(hosts)
    assert first == second

    n = counts["sentences"]
    typed = counts["typed"] / n
    found = (counts["typed"] + counts["recognized_unsupported"]) / n
    print("\nlexicon over %d effect sentences: typed=%d (%.1f%%) "
          "unsupported=%d no_reading=%d no_lemma=%d" % (
              n, counts["typed"], 100 * typed,
              counts["recognized_unsupported"], counts["clause"],
              counts["no_lemma"]))
    print("loyalty lines: %d, typed %d; rider/replacement sentences left "
          "to L1/L2: %d" % (counts["loyalty_lines"], counts["loyalty_typed"],
                            counts["not_l4"]))
    print("verbs:", sorted(((k[5:], v) for k, v in counts.items()
                            if k.startswith("verb:")), key=lambda kv: -kv[1]))
    assert typed >= TYPED_FLOOR
    assert found >= VERB_FOUND_FLOOR
    assert counts["loyalty_lines"] and \
        counts["loyalty_typed"] == counts["loyalty_lines"]


# ── A12: one slot owner, a superset of legacy's line set ───────────────

@pytest.mark.timeout(120)
def test_loyalty_slots_from_the_grammar_superset_agree_with_legacy_on_every_walker():
    """For every planeswalker face, the slots `loyalty_slot_for` assigns to
    the lexicon's loyalty costs (fixed and variable lines) are legacy's
    `loyalty_abilities` slots with legacy's costs: an X line takes no slot
    and moves no fixed line's slot (A12)."""
    from engine.effect_grammar import lexicon as L
    from engine.effect_grammar import normalize as N
    from engine.oracle_parser import loyalty_slot_for
    from tests._card_db_cache import shared_card_database
    db = shared_card_database()
    walkers = variable = 0
    for t in {id(v): v for v in db.cards.values()}.values():
        for text, types, legacy in (
                (t.oracle_text, t.card_types, t.loyalty_abilities),
                (getattr(t, "back_face_oracle", ""), t.back_face_types,
                 getattr(t, "back_face_loyalty_abilities", None))):
            tc = {getattr(c, "value", str(c)) for c in (types or ())}
            if not text or "planeswalker" not in tc:
                continue
            paras = N.normalize(text, N.Facts(is_planeswalker=True)).paragraphs
            costs = []
            for p in paras:
                r = L.parse_loyalty_cost(p)
                if r.value is not None:
                    costs.append(L.loyalty_slot_cost(r.value))
            walkers += 1
            variable += sum(not isinstance(c, int) for c in costs)
            slots = {}
            for i, c in enumerate(costs):
                s = loyalty_slot_for(costs, i)
                if s:
                    slots[s] = c
            assert slots == {s: a.cost for s, a in (legacy or {}).items()}, (
                t.name, costs, legacy)
    print("\nplaneswalker faces %d, variable-cost lines %d" % (walkers, variable))
    assert walkers and variable


# ── Registered-deck witnesses (decks/modern_meta.py) ──────────────────
# (card, [(anchor in the L0 text, expected verb, expected mod_kind)]). The
# anchor starts the slot; the slot runs to the end of its sentence.

_C = Verb.CONTINUOUS
WITNESSES = (
    ("Path to Exile", [
        ("exile target creature", Verb.EXILE, None),
        ("its controller may search", Verb.SEARCH, None)]),
    ("Green Sun's Zenith", [
        ("search your library", Verb.SEARCH, None),
        ("shuffle ~ into its owner's library", Verb.MOVE, None)]),
    ("Galvanic Discharge", [
        ("choose target creature", Verb.CHOOSE, None),
        ("you get {e}{e}{e}", Verb.PLAYER_COUNTERS, None),
        ("then you may pay any amount", Verb.PAY, None),
        ("~ deals that much damage", Verb.DAMAGE, None)]),
    ("Practiced Offense", [
        ("put a +1/+1 counter", Verb.PUT_COUNTERS, None),
        ("target creature gains your choice", _C, ModKind.ADD_KEYWORDS)]),
    ("Day's Undoing", [
        ("each player shuffles their hand", Verb.MOVE, None),
        ("if it's your turn, end the turn", Verb.END_TURN, None)]),
    ("Living End", [
        ("each player exiles all creature cards", Verb.EXILE, None)]),
    ("Thoughtseize", [
        ("target player reveals", Verb.REVEAL, None),
        ("you choose a nonland card", Verb.CHOOSE, None),
        ("that player discards", Verb.DISCARD, None),
        ("you lose 2 life", Verb.LOSE_LIFE, None)]),
    ("Expressive Iteration", [
        ("look at the top three", Verb.LOOK, None),
        ("put one of them into your hand", Verb.MOVE, None),
        ("you may play the exiled card", _C, ModKind.PERMIT)]),
    ("Stubborn Denial", [
        ("counter target noncreature spell", Verb.COUNTER, None),
        ("counter that spell instead", Verb.COUNTER, None)]),
    ("Guide of Souls", [
        ("you gain 1 life", Verb.GAIN_LIFE, None),
        ("you may pay {e}{e}{e}", Verb.PAY, None),
        ("put two +1/+1 counters and a flying counter", Verb.PUT_COUNTERS, None),
        ("it becomes an angel", _C, ModKind.ADD_TYPES)]),
    ("Wrath of the Skies", [
        ("you get x {e}", Verb.PLAYER_COUNTERS, None),
        ("destroy each artifact", Verb.DESTROY, None)]),
    ("Kappa Cannoneer", [
        ("put a +1/+1 counter on ~", Verb.PUT_COUNTERS, None),
        ("it can't be blocked", _C, ModKind.PROHIBIT)]),
    ("Teferi, Time Raveler", [
        ("you may cast sorcery spells as though", _C, ModKind.PERMIT),
        ("return up to one target", Verb.MOVE, None),
        ("draw a card", Verb.DRAW, None)]),
    ("Chandra, Awakened Inferno", [
        ("each opponent gets an emblem", Verb.CREATE_EMBLEM, None),
        ("~ deals 3 damage", Verb.DAMAGE, None),
        ("~ deals x damage", Verb.DAMAGE, None)]),
    ("Grist, the Hunger Tide", [
        ("create a 1/1 black and green insect", Verb.CREATE_TOKEN, None),
        ("you may sacrifice a creature", Verb.SACRIFICE, None),
        ("each opponent loses life equal", Verb.LOSE_LIFE, None)]),
)

# A13 witnesses: the token after a depth-0 comma is an inflected verb.
SERIAL_WITNESSES = (
    ("Path to Exile", "put that card onto the battlefield", Verb.MOVE),
    ("Path to Exile", "shuffle.", Verb.SHUFFLE),
    ("Living End", "sacrifices all creatures", Verb.SACRIFICE),
    ("Living End", "puts all cards they exiled", Verb.MOVE),
    ("Day's Undoing", "draws seven cards", Verb.DRAW),
    ("Primeval Titan", "put them onto the battlefield tapped", Verb.MOVE),
    ("Expressive Iteration", "exile one of them", Verb.EXILE),
)

# A12 witnesses: printed loyalty costs, variable lines included.
LOYALTY_WITNESSES = (
    ("Chandra, Awakened Inferno", [Amount(AmountKind.LITERAL, n=2),
                                   Amount(AmountKind.LITERAL, n=-3),
                                   Amount(AmountKind.X, n=-1)]),
    ("Grist, the Hunger Tide", [Amount(AmountKind.LITERAL, n=1),
                                Amount(AmountKind.LITERAL, n=-2),
                                Amount(AmountKind.LITERAL, n=-5)]),
    ("Wrenn and Six", [Amount(AmountKind.LITERAL, n=1),
                       Amount(AmountKind.LITERAL, n=-1),
                       Amount(AmountKind.LITERAL, n=-7)]),
)


def test_registered_deck_witness_names_are_in_a_registered_deck():
    from decks.modern_meta import MODERN_DECKS
    registered = set()
    for deck in MODERN_DECKS.values():
        for part in ("mainboard", "sideboard"):
            registered.update(deck.get(part, {}))
    names = ({n for n, _ in WITNESSES} | {n for n, _, _ in SERIAL_WITNESSES}
             | {n for n, _ in LOYALTY_WITNESSES})
    assert names <= registered, names - registered


def _l0(name):
    from engine.effect_grammar import normalize as N
    from tests._card_db_cache import shared_card_database
    t = shared_card_database().get_card(name)
    tc = frozenset(getattr(c, "value", str(c)) for c in t.card_types)
    legendary = any(getattr(s, "value", s) == "legendary" for s in t.supertypes)
    character = "planeswalker" in tc or (legendary and "creature" in tc)
    facts = N.Facts(names=N.self_names(t.name, is_legendary=legendary,
                                       is_character=character,
                                       subtypes=tuple(t.subtypes)),
                    type_class=tc, is_legendary=legendary,
                    is_planeswalker="planeswalker" in tc)
    return N.normalize(t.oracle_text, facts).text


@pytest.mark.parametrize("name,rows", WITNESSES, ids=[n for n, _ in WITNESSES])
def test_registered_deck_witness_sentences_read_their_printed_verb(name, rows):
    from engine.effect_grammar import lexicon as L
    text = _l0(name)
    for anchor, verb, mod in rows:
        assert anchor in text, (name, anchor, text)
        a = text.index(anchor)
        end = text.find(".", a)
        r = L.find_verb(text, (a, end if end >= 0 else len(text)))
        assert r.value is not None, (name, anchor, r)
        assert (r.value.verb, r.value.mod_kind) == (verb, mod), (name, anchor, r)


def test_galvanic_discharge_pays_any_amount_and_practiced_offense_offers_a_choice():
    from engine.effect_grammar import lexicon as L
    text = _l0("Galvanic Discharge")
    a = text.index("then you may pay")
    r = L.find_verb(text, (a, text.index(".", a)))
    assert r.amount == Amount(AmountKind.ANY_NUMBER) and "any_number" in r.flags
    text = _l0("Practiced Offense")
    a = text.index("target creature gains")
    r = L.find_verb(text, (a, text.index(".", a)))
    assert "alternatives" in r.flags


@pytest.mark.parametrize("name,anchor,verb", SERIAL_WITNESSES,
                         ids=[a for _, a, _ in SERIAL_WITNESSES])
def test_registered_deck_serial_list_members_open_with_an_inflected_verb(
        name, anchor, verb):
    from engine.effect_grammar import lexicon as L
    text = _l0(name)
    assert anchor in text, (name, anchor, text)
    e = L.verb_at(text, text.index(anchor))
    assert e is not None and e.verb is verb, (name, anchor, e)


@pytest.mark.parametrize("name,costs", LOYALTY_WITNESSES,
                         ids=[n for n, _ in LOYALTY_WITNESSES])
def test_registered_deck_walkers_loyalty_lines_read_their_signed_costs(name, costs):
    from engine.effect_grammar import lexicon as L
    got = [r.value for r in (L.parse_loyalty_cost(p)
                             for p in _l0(name).split("\n"))
           if r.value is not None]
    assert got == costs
