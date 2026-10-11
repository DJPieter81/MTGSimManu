"""Pool invariants of the payload sub-grammar (design doc 2026-09-29,
section 4; E0).

Runs the payload leaf over every payload-verb clause of every oracle text in
the card DB (clauses cut by a light stand-in for L0-L3: reminder text
stripped, self-name and "this <noun>" -> ``~`` (the L0 contract in
`engine.effect_grammar.sub`), double quotes masked ``⟨qk⟩``, lowercased,
sentences split on ``.``/newlines, the slot cut after the verb). The leaf is
called the way the spine calls it: the sentence is the host and the slot a
span inside it. It asserts that the leaf

* never raises,
* is deterministic (a second pass after clearing its memo is canonically
  identical, F10),
* returns a well-formed SlotResult (value XOR unmodelled, span inside the
  slot), and
* types at least the measured share of each payload family (a regression
  floor, recorded with its measurement below).

The coverage table is printed (``pytest -s``) for the census.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

import pytest

from engine.effect_model import ModKind
from engine.effect_spec import Amount, AmountKind, Verb, canonical
from engine.effect_grammar.sub import SELF_NOUNS
from engine.effect_grammar.sub import payload as P
from engine.oracle_parser import strip_reminder_text


@dataclass(frozen=True)
class _Entry:
    verb: Verb
    lemma: str = ""
    mod_kind: Optional[ModKind] = None


_QUOTE_RE = re.compile(r'"[^"]*"')
_THIS_NOUN_RE = re.compile(r"\bthis (?:%s)\b" % "|".join(SELF_NOUNS))
_SENT_RE = re.compile(r"(?<=[.])\s+|\n")
_COUNT = r"(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+|x)"

# (family, verb, lemma, pattern whose group 1 is the slot text)
_FAMILIES = (
    ("mana", Verb.ADD_MANA, "add", re.compile(r"\badds? (.+)$")),
    ("token", Verb.CREATE_TOKEN, "create", re.compile(r"\bcreates? (.+)$")),
    ("counters", Verb.PUT_COUNTERS, "put", re.compile(
        r"\bputs? ((?:%s|that many|all|a number of|an additional)\b"
        r"[^.]*?\bcounters?\b.*)$" % _COUNT)),
    ("energy", Verb.PLAYER_COUNTERS, "get",
     re.compile(r"\bgets? ((?:an amount of )?\{e\}.*)$")),
    ("pt", Verb.CONTINUOUS, "get", re.compile(r"\b(gets? [+-].*)$")),
    ("keywords", Verb.CONTINUOUS, "gain",
     re.compile(r"\b((?:gains?|loses?) (?!control|life|\d|x |that much).*)$")),
    ("prohibit", Verb.CONTINUOUS, "can't", re.compile(r"\b(can't .*)$")),
    ("becomes", Verb.CONTINUOUS, "become", re.compile(r"\b(becomes? .*)$")),
    ("pay", Verb.PAY, "pay", re.compile(
        r"\bpays? ((?:\{[^}]+\}|\d+ life|an amount of \{e\}"
        r"|any amount of \{e\}).*)$")),
    ("keyword_action", Verb.KEYWORD_ACTION, "",
     re.compile(r"(?:^|\bthen |\byou |, )((?:%s)\b.*)$" % "|".join(
         re.escape(n) for n in sorted(P.KEYWORD_ACTION_NAMES, key=len,
                                      reverse=True)))),
)
# An ability word ("domain - ") is stripped by L2 before the cost modifier
# is read; the stand-in strips a leading "<words> - " the same way.
_COST_MOD_RE = re.compile(
    r"^(?:[a-z' ]+ - )?"
    r"([^.]*? costs? (?:\{[^}]+\})+ (?:less|more) to (?:activate|cast).*)$")

# Typed share per family, measured 2026-09-30 on this branch's DB (22.7k
# cards): mana 179/200 (89.5%), token 1100/1154 (95.3%), counters 806/838
# (96.2%), energy 15/15, pt 1158/1160 (99.8%), keywords 322/407 (79.1%),
# prohibit 186/324 (57.4%), becomes 285/643 (44.3%: "becomes tapped" and
# "becomes a copy of" are a state and COPY, correctly not a type change),
# keyword_action 108/111 (97.3%), cost_modifier 347/376 (92.3%), pay
# 268/274 (97.8%: hybrid and phyrexian payments are Unmodelled until the
# cost owner represents them). The slot cut is a stand-in for L0-L3, so
# the denominators include non-payload text. Floors sit a few points under
# the measurement: a regression fails, a DB refresh does not.
#
# prohibit fell from 260/324 when qualified prohibitions stopped counting
# as typed: "can't be blocked by <filter>", "except by", "can't attack you",
# "can't block alone" were full prohibitions with the qualifier in `rest`
# (74 are now Unmodelled prohibit:qualifier). cost_modifier fell from
# 377/379 when the subject table closed ("<ability word> - this spell" and
# prefixed subjects were global reducers); the stand-in now strips an
# ability word the way L2 does.
#
# Re-measured 2026-09-30 after the stand-in applied the L0 contract ("this
# <noun>" -> ~, the leaf contract in engine.effect_grammar.sub): the slot
# set shifts slightly (e.g. counters 784/816, token 1097/1151), and
# cost_modifier is 341/376 (90.7%): "activated abilities of ~ cost ..."
# names the object itself and is refused, where "... of this creature
# cost ..." used to pass the global-subject table as a static reducer for
# all abilities.
_FLOORS = {
    "mana": 0.85, "token": 0.90, "counters": 0.92, "energy": 0.90,
    "pt": 0.95, "keywords": 0.72, "prohibit": 0.52, "becomes": 0.38,
    "keyword_action": 0.90, "cost_modifier": 0.88, "pay": 0.92,
}


# Connectives a typed payload must have consumed or refused: a second
# payload option (A19), "alone" (CR 506.5 attack/block alone), and an
# evasion qualifier ("except by", "by more than one").
_OWNED_CONNECTIVE_RE = re.compile(
    r"^(?:or (?:a|an|%s|your choice)\b|alone\b|except by\b"
    r"|by more than\b)" % _COUNT)


def _normalise(template) -> str:
    text = strip_reminder_text(template.oracle_text or "")
    k = [0]

    def _mask(_m):
        k[0] += 1
        return "⟨Q%d⟩" % (k[0] - 1)
    text = _QUOTE_RE.sub(_mask, text)
    for nm in sorted({template.name, *template.name.split(" // ")},
                     key=len, reverse=True):
        if nm:
            text = text.replace(nm, "~")
    text = text.lower().replace("—", "-").replace("’", "'")
    return _THIS_NOUN_RE.sub("~", text)


def _slots(card_db):
    seen = set()
    for template in card_db.cards.values():
        if not template.oracle_text:
            continue
        for sentence in _SENT_RE.split(_normalise(template)):
            s = sentence.strip().rstrip(".")
            for fam, verb, lemma, rx in _FAMILIES:
                m = rx.search(s)
                if m:
                    key = (fam, m.group(1))
                    if key not in seen:
                        seen.add(key)
                        yield fam, verb, lemma, s, m.span(1)
            m = _COST_MOD_RE.search(s)
            if m and ("cost_modifier", m.group(1)) not in seen:
                seen.add(("cost_modifier", m.group(1)))
                yield "cost_modifier", None, "", s, m.span(1)


def _run(slots):
    out = []
    for fam, verb, lemma, host, span in slots:
        if fam == "cost_modifier":
            r = P.parse_cost_modifier(host, span)
        else:
            r = P.parse_payload(_Entry(verb, lemma), host, span, None)
        out.append((fam, host, span, r))
    return out


def _clear():
    P.clear_caches()


# Pool-wide (~9k distinct payload slots). Measured 2026-09-30 on this
# container (quiet, 4 cores): ~1.5 s for two passes, plus ~16 s when it is
# the first test of the process to load the shared card DB. 120 s bounds a
# hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_the_payload_leaf_types_every_pool_payload_slot_deterministically(card_db):
    slots = list(_slots(card_db))
    assert len(slots) > 1000, len(slots)
    _clear()
    first = _run(slots)
    _clear()
    second = _run(slots)
    assert [canonical(r) for *_, r in first] == \
        [canonical(r) for *_, r in second]

    total, typed = Counter(), Counter()
    unmodelled = Counter()
    swallowed = []
    for fam, text, span, r in first:
        total[fam] += 1
        if r is None:
            continue
        assert not (r.value is not None and r.unmodelled is not None), text
        assert span[0] <= r.span[0] <= r.span[1] <= span[1], (text, r.span)
        for typed_r in ((r,) if r.value is not None else ()) + r.alternatives:
            assert span[0] <= typed_r.span[0] <= typed_r.span[1] <= span[1], (
                text, typed_r.span)
            if typed_r.value is not None and _OWNED_CONNECTIVE_RE.match(
                    typed_r.rest_text(text)):
                swallowed.append((fam, text, typed_r.rest_text(text)))
        if r.value is not None or r.alternatives:
            typed[fam] += 1
        elif r.unmodelled is not None:
            unmodelled[(fam, r.unmodelled.detail)] += 1

    print("\npayload leaf pool coverage (typed / slots):")
    for fam in sorted(total):
        share = typed[fam] / total[fam]
        print(f"  {fam:15s} {typed[fam]:6d} / {total[fam]:6d}  {share:6.1%}")
    print("  top unmodelled:", unmodelled.most_common(12))
    # A typed value is never plausible-but-broader: a connective the leaf
    # owns (a second option, a qualifier) is never left in `rest`.
    assert not swallowed, swallowed[:10]
    for fam, floor in _FLOORS.items():
        assert total[fam], fam
        assert typed[fam] / total[fam] >= floor, (
            fam, typed[fam], total[fam])


# Witnesses: payload slots printed by registered-deck cards, with the exact
# value the leaf must return. The share floors above count a slot as typed
# whenever a value exists; these pin that the value is the printed rule, not
# a plausible broader one (A19 shared tails, A8 cost rules and self-scoped
# cost modifiers, qualified prohibitions). The card name only locates the
# printed text in the DB; the leaf never sees it.
def _witness(verb, lemma, text):
    return P.parse_payload(_Entry(verb, lemma), text, (0, len(text)), None)


@pytest.mark.timeout(120)
def test_registered_deck_payload_witnesses_type_exactly_the_printed_rule(
        card_db):
    from engine.effect_model import Modification

    def printed(card, slot):
        text = _normalise(card_db.cards[card])
        assert slot in text, (card, slot, text)
        return slot

    # A19: the duration is shared by both options (Practiced Offense).
    slot = printed("Practiced Offense", "gains your choice of double strike "
                   "or lifelink until end of turn")
    r = _witness(Verb.CONTINUOUS, "gain", slot)
    assert r.value is None and r.rest_text(slot) == "until end of turn"
    assert [a.value for a in r.alternatives] == [
        Modification(ModKind.ADD_KEYWORDS,
                     data=(("keywords", (("double_strike", None),)),)),
        Modification(ModKind.ADD_KEYWORDS,
                     data=(("keywords", (("lifelink", None),)),))]
    assert [a.rest_text(slot) for a in r.alternatives] == [
        "until end of turn"] * 2

    # A8: a costed keyword grant leaves its cost to the rider.
    for card in ("Snapcaster Mage", "Past in Flames"):
        slot = printed(card, "gains flashback until end of turn")
        r = _witness(Verb.CONTINUOUS, "gain", slot)
        assert r.value == Modification(
            ModKind.ADD_KEYWORDS, data=(("keywords", (("flashback", None),)),))
        assert r.pending == (("cost_rule", "flashback"),)
        assert r.rest_text(slot) == "until end of turn"

    # A8: a self cost reduction under an ability word is never a global
    # reducer; once L2 strips the ability word it is this spell's own (L0
    # has already rewritten "this spell" to ~).
    for card, n in (("Leyline Binding", 1), ("Scion of Draco", 2)):
        tail = ("~ costs {%d} less to cast for each basic land "
                "type among lands you control" % n)
        slot = printed(card, "domain - " + tail)
        assert P.parse_cost_modifier(slot, (0, len(slot))).value is None
        r = P.parse_cost_modifier(tail, (0, len(tail)))
        assert r.value == Modification(ModKind.COST_DELTA, data=(
            ("amount", Amount(AmountKind.LITERAL, n=n)), ("cost_of", "cast"),
            ("scope", "this_spell"), ("sign", -1)))
        assert r.rest_text(tail) == "for each basic land type among lands you control"

    # PROHIBIT: a qualified prohibition is Unmodelled, a two-action one
    # names both actions.
    for text in ("can't attack or block alone",
                 "can't be blocked except by creatures with flying",
                 "can't be blocked by more than one creature"):
        r = _witness(Verb.CONTINUOUS, "can't", text)
        assert r.value is None and r.unmodelled is not None, text
    r = _witness(Verb.CONTINUOUS, "can't", "can't block or be blocked "
                 "this turn")
    assert r.value == Modification(ModKind.PROHIBIT, data=(
        ("actions", ("block", "be_blocked")),))
