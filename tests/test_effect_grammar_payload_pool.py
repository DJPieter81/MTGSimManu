"""Pool invariants of the payload sub-grammar (design doc 2026-09-29,
section 4; E0).

Runs the payload leaf over every payload-verb clause of every oracle text in
the card DB (clauses cut by a light stand-in for L0-L3: reminder text
stripped, self-name -> ``~``, double quotes masked ``⟨qk⟩``, lowercased,
sentences split on ``.``/newlines, the slot cut after the verb). It asserts
that the leaf

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
from engine.effect_spec import Verb, canonical
from engine.effect_grammar.sub import payload as P
from engine.oracle_parser import strip_reminder_text


@dataclass(frozen=True)
class _Entry:
    verb: Verb
    lemma: str = ""
    mod_kind: Optional[ModKind] = None


_QUOTE_RE = re.compile(r'"[^"]*"')
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
    ("keyword_action", Verb.KEYWORD_ACTION, "",
     re.compile(r"(?:^|\bthen |\byou |, )((?:%s)\b.*)$" % "|".join(
         re.escape(n) for n in sorted(P.KEYWORD_ACTION_NAMES, key=len,
                                      reverse=True)))),
)
_COST_MOD_RE = re.compile(
    r"^([^.]*? costs? (?:\{[^}]+\})+ (?:less|more) to (?:activate|cast).*)$")

# Typed share per family, measured 2026-09-30 on this branch's DB (22.7k
# cards): mana 179/200 (89.5%), token 1100/1154 (95.3%), counters 806/838
# (96.2%), energy 15/15, pt 1158/1160 (99.8%), keywords 325/407 (79.9%),
# prohibit 260/324 (80.2%), becomes 285/643 (44.3%: "becomes tapped" and
# "becomes a copy of" are a state and COPY, correctly not a type change),
# keyword_action 108/111 (97.3%), cost_modifier 377/379 (99.5%). The slot
# cut is a stand-in for L0-L3, so the denominators include non-payload
# text. Floors sit a few points under the measurement: a regression fails,
# a DB refresh does not.
_FLOORS = {
    "mana": 0.85, "token": 0.90, "counters": 0.92, "energy": 0.90,
    "pt": 0.95, "keywords": 0.72, "prohibit": 0.72, "becomes": 0.38,
    "keyword_action": 0.90, "cost_modifier": 0.95,
}


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
    return text.lower().replace("—", "-")


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
                        yield fam, verb, lemma, m.group(1), m.start(1)
            m = _COST_MOD_RE.search(s)
            if m and ("cost_modifier", m.group(1)) not in seen:
                seen.add(("cost_modifier", m.group(1)))
                yield "cost_modifier", None, "", m.group(1), m.start(1)


def _run(slots):
    out = []
    for fam, verb, lemma, text, start in slots:
        span = (start, start + len(text))
        if fam == "cost_modifier":
            r = P.parse_cost_modifier(text, span)
        else:
            r = P.parse_payload(_Entry(verb, lemma), text, span, None)
        out.append((fam, text, span, r))
    return out


def _clear():
    for fn in (P._mana_rel, P._counters_rel, P._token_rel,
               P._modification_rel, P._cost_modifier_rel,
               P._keyword_action_rel, P._pay_rel):
        fn.cache_clear()


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
    for fam, text, span, r in first:
        total[fam] += 1
        if r is None:
            continue
        assert not (r.value is not None and r.unmodelled is not None), text
        assert span[0] <= r.span[0] <= r.span[1] <= span[1], (text, r.span)
        if r.value is not None or r.alternatives:
            typed[fam] += 1
        elif r.unmodelled is not None:
            unmodelled[(fam, r.unmodelled.detail)] += 1

    print("\npayload leaf pool coverage (typed / slots):")
    for fam in sorted(total):
        share = typed[fam] / total[fam]
        print(f"  {fam:15s} {typed[fam]:6d} / {total[fam]:6d}  {share:6.1%}")
    print("  top unmodelled:", unmodelled.most_common(12))
    for fam, floor in _FLOORS.items():
        assert total[fam], fam
        assert typed[fam] / total[fam] >= floor, (
            fam, typed[fam], total[fam])
