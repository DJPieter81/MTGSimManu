"""L2 and L3 of the clause grammar: sentence frames and the clause split
(design doc 2026-09-29, section 3 "L2, sentence frames" and "L3, clauses";
section 7 "Links" and "Absorbed riders"; A8, A13-A17, A30; E0 step 10).

L2 and L3 read one L1 host's ``body`` spans once, at LOAD, and hand L4 the
clause spans it types and L5 the frame tokens it links. They type nothing
themselves: every phrase is typed by the leaf that owns it (conditions by
`condition`, delays and durations by `duration`, for-each and where-X by
`amount`, the instead-of override by `dest`, the cost rule by `keywords`,
the mana spend rider by `payload`), and a verb is recognised only through
the lexicon's `verb_at` / `find_verb`.

**L2, frames.** A sentence ends at a ``.`` at quote depth 0 (L0 masked every
quote, so every ``.`` left is depth 0). Each sentence becomes one `Frame`
by consuming, in a loop, its leading phrases --

* a connective, sentence-wide (A14): ``then``; ``if <player> do(es)``;
  ``if <player> don't`` / ``if no one does``; ``if <player> don't <VP>,``
  and ``if <player> <VP> this way,`` (the named performed test: the VP is
  a frame token whose lemma L5 matches against an earlier spec; an object
  subject -- "if a red card is discarded this way" -- is refused, and an
  "if ... would ..." test is a CR 614 replacement, refused); ``otherwise``.
  <player> is any player participant the participant leaf reads;
* a sub-ability opener (A30): ``when you do[, if <COND>],`` opens a
  REFLEXIVE sub-ability (CR 603.12) whose "if" is the sub-ability head's
  intervening-if (CR 603.4, F9), never the frame's condition; a delay
  prefix opens a DELAYED one (CR 603.7);
* ``if <COND>,`` (a kicked frame is the condition leaf's CAST_FACT);
* ``instead`` (A15);
* ``for each <Q>,`` -- FOR_EACH(Q) on the counted verb, or
  UNMODELLED(ITERATION) when the body refers to the element (A16);
* a duration prefix ("until end of turn, ...");

-- and its trailing phrases: ``, where x is <Q>`` (the definition ends
where the amount leaf's expression ends; clauses printed after it are
split like any others, the definition a hole in the body); a delay suffix; the
instead-of destination override ``If <ref> is <verb>ed this way, <move>
instead of putting it into <zone>`` (A15: the whole sentence is the
override, never a sibling clause); `` unless <player> pays <cost>`` (the
cost read from the printed span by the condition leaf, A31); `` instead[ if
<COND>]``; a trailing `` if <COND>``. Durations after a clause stay with the
clause (L4 reads them per clause).

**Riders** (a closed list, section 7) are absorbed, never effects: "<it>
can't be regenerated" (``no_regeneration``, CR 701.15), "it's still a land"
(retained types), "~ can't be countered" (``uncounterable``), "spend this
mana only ..." (`ManaSpec.restriction`), "this ability costs ..." (A8,
`cost_modifiers`), "the <keyword> cost is equal to its mana cost" (A8,
`KeywordSpec.cost_rule`), and "reveal it" after a search or move
(``reveal``). A rider sentence joins the frame before it.

A ``whenever`` / ``when`` inside resolution text is a triggered ability the
model has no host for: the frame is UNMODELLED(TRIGGER_EMBEDDED).

**L3, clauses.** The frame body is split at depth 0 on ``, then ``; ``; ``;
``, and <lemma>`` / `` and <lemma>``; the lemma-gated serial ``, <lemma>``
(A13: the token after the comma is an inflected lexicon verb and the text
before it holds its own verb, so a comma in a type list never splits); the
gapped `` and <count|REST NP> <destination PP>`` after a put or return
clause (A13); the elliptical `` and <AMOUNT> damage to`` and the recipient
union `` and each <FILTER>`` after a damage clause (A17). The last three
are simultaneous siblings sharing one ``group`` with their antecedent. A
clause with no subject before its lemma inherits the previous clause's
subject. An inherited part is handed to L4 as ``prefix`` -- host spans of
the antecedent's text (subject; subject and verb; subject, verb, amount and
"to") -- which L4 reads but never counts as the clause's own printed text,
so an inherited target is never a second requirement.

**Coverage.** Every non-space character of an effect-bearing host is in an
L1 consumed span, a frame token, a frame refusal or a clause span:
`uncovered` reports what is not (section 3, the coverage invariant).
"""
from __future__ import annotations

import re
from typing import Any, List, NamedTuple, Optional, Tuple

from engine.delayed_triggers import DelayedTriggerTiming
from engine.effect_grammar import keywords as _keywords
from engine.effect_grammar import lexicon as _lexicon
from engine.effect_grammar.sub import COUNT_WORDS, Span, unmodelled
from engine.effect_grammar.sub import amount as _amount
from engine.effect_grammar.sub import condition as _condition
from engine.effect_grammar.sub import dest as _dest
from engine.effect_grammar.sub import duration as _duration
from engine.effect_grammar.sub import participant as _participant
from engine.effect_grammar.sub import payload as _payload
from engine.effect_grammar.sub import target as _target
from engine.effect_model import Duration
from engine.effect_spec import (Amount, Condition, ConditionKind, Destination,
                                Stage, SubAbilityKind, Unmodelled)

__all__ = ["LEAF", "DETAIL_CODES", "CONNECTIVES", "RIDERS", "Opener",
           "Clause", "Frame", "frame_host", "frame_sentence", "host_sentences",
           "with_rider", "uncovered",
           "clear_caches"]

LEAF = "clauses"
DETAIL_CODES = frozenset({
    "trigger_embedded",       # a whenever/when inside resolution text
    "delay_not_trailing",     # a delay phrase in the middle of a sentence
    "empty_body",             # a frame whose phrases left no clause
    "structure_condition",    # a leading "if ..." the condition table calls structure
})

# The closed connective vocabulary (A14), the value of Frame.connective.
CONNECTIVES = frozenset({"", "then", "if_you_do", "if_you_dont", "otherwise"})
# The closed rider vocabulary (section 7), the names in Frame.riders.
RIDERS = frozenset({"no_regeneration", "still_land", "uncounterable",
                    "mana_restriction", "cost_modifier", "cost_rule",
                    "reveal"})


def _um(stage: Stage, code: str, param: str = "") -> Unmodelled:
    return unmodelled(stage, "", LEAF, code, DETAIL_CODES, param)


# ── Output ──────────────────────────────────────────────────────────────

class Opener(NamedTuple):
    """A sub-ability opener (A30): "when you do" (REFLEXIVE, CR 603.12)
    with its head intervening-if (CR 603.4), or a delay prefix / suffix
    (DELAYED, CR 603.7) with the phrase table's timing."""
    kind: SubAbilityKind
    timing: Optional[DelayedTriggerTiming] = None
    intervening_if: Optional[Condition] = None
    span: Span = (0, 0)


class Clause(NamedTuple):
    """One L3 clause: its host span, how it joins the clause before it,
    its simultaneity group and the inherited part L4 reads first."""
    span: Span
    joiner: str = ""                 # '' | 'then' | 'and' | 'serial' | 'semicolon'
    group: Optional[int] = None      # shared with a gapped / elliptical / union sibling
    gap: str = ""                    # '' | 'subject' | 'verb' | 'damage' | 'recipient'
    prefix: Tuple[Span, ...] = ()    # host spans of the inherited text
    flags: frozenset = frozenset()   # absorbed clause riders ('reveal')


class Frame(NamedTuple):
    """One sentence after L2: its frame tokens and its L3 clauses. Spans
    index the host text."""
    span: Span
    part: int = -1
    label: str = ""
    connective: str = ""
    named_vp: Optional[Span] = None
    named_lemma: str = ""
    opener: Optional[Opener] = None
    condition: Optional[Condition] = None
    instead: bool = False
    duration: Optional[Duration] = None
    for_each: Optional[Amount] = None
    unless: Optional[Condition] = None
    where_x: Optional[Amount] = None
    dest_override: Optional[Destination] = None
    dest_object: Optional[Span] = None
    riders: Tuple[Tuple[str, Any], ...] = ()
    consumed: Tuple[Tuple[str, Span], ...] = ()
    unmodelled: Tuple[Tuple[Unmodelled, Span], ...] = ()
    pending: Tuple[Tuple[str, str], ...] = ()
    clauses: Tuple[Clause, ...] = ()


# ── Closed phrase tables (over L0 output) ──────────────────────────────

# The connectives and openers name the player whose action they test
# ("if you do", "if that player doesn't", "when they do"): the subject is
# any player the participant leaf reads (`_player_subject`); the spine
# keeps no subject vocabulary of its own.
_WHO = r"(?P<who>[a-z~'][a-z~' ]*?)"
_THEN_RE = re.compile(r"then,? ")
_IF_DO_RE = re.compile(r"if %s (?:do|does|did), " % _WHO)
_IF_DONT_RE = re.compile(
    r"if (?:%s (?:don't|doesn't|didn't|do not|does not|can't|cannot)|"
    r"no one does), " % _WHO)
_IF_DONT_VP_RE = re.compile(r"if %s (?:don't|doesn't|didn't) (?=[a-z])"
                            % _WHO)
_OTHERWISE_RE = re.compile(r"otherwise,? ")
_WHEN_DO_RE = re.compile(r"when %s (?:do|does),? " % _WHO)
_WHEN_DO_WORD_RE = re.compile(r"when %s (?:do|does)\b" % _WHO)
_INSTEAD_LEAD_RE = re.compile(r"instead,? ")
_DURATION_LEAD_RE = re.compile(_duration.DURATION_START)
# A trigger word inside resolution text; the reflexive opener "when <player>
# do(es)" is not one (`_embedded`).
_TRIGGER_WORD_RE = re.compile(r"(?<![\w'~-])(?:whenever|when)\b")

# The absorbed riders' subjects are object references the participant
# leaf reads ("it", "that artifact", "those creatures", "~").
_NO_REGEN_RE = re.compile(r"(?P<subj>.+?) can't be regenerated$")
_STILL_LAND_RE = re.compile(r"(?:it's|they're|it is|they are) still "
                            r"(?:a land|lands)$")
_UNCOUNTERABLE_RE = re.compile(r"~ can't be countered$")
_REVEAL_RE = re.compile(r"reveal (?:it|them|that card|those cards)$")
_REVEAL_AFTER = frozenset({"search", "put", "return"})


def _actor(rx: "re.Pattern[str]", t: str, pos: int, end: int):
    """`rx` matched at `pos` whose ``who`` group (when it printed one) is a
    player participant; None otherwise."""
    m = rx.match(t, pos, end)
    if m is None or m.group("who") is None:
        return m
    return m if _player_subject(t, m.start("who"), m.end("who")) else None


def _player_subject(t: str, a: int, b: int) -> bool:
    """Is ``t[a:b]`` a player participant (the participant leaf's PLAYER
    reading: "you", "they", "that player", "each opponent", ...)?"""
    a, b = _trim(t, a, b)
    if a >= b:
        return False
    r = _participant.parse_participant(t, (a, b))
    return r.value is not None and _participant.PLAYER in r.flags


def _object_subject(t: str, a: int, b: int) -> bool:
    """Is ``t[a:b]`` an object reference the participant leaf reads?"""
    r = _participant.parse_participant(t, (a, b))
    return r.value is not None and _participant.OBJECT in r.flags


def _embedded(t: str, s: int, e: int) -> bool:
    """Does ``t[s:e]`` hold a trigger word other than the reflexive opener
    "when <player> do(es)"?"""
    for m in _TRIGGER_WORD_RE.finditer(t, s, e):
        if m.group(0) == "when" and _actor(_WHEN_DO_WORD_RE, t, m.start(), e):
            continue
        return True
    return False


def _trim(t: str, a: int, b: int, chars: str = " ") -> Span:
    while a < b and t[a] in chars:
        a += 1
    while b > a and t[b - 1] in chars:
        b -= 1
    return a, b


# ── Riders ─────────────────────────────────────────────────────────────

def _rider(t: str, s: int, e: int) -> Optional[Tuple[str, Any]]:
    """The absorbed rider a whole sentence ``t[s:e]`` is, or None."""
    sent = t[s:e]
    m = _NO_REGEN_RE.match(sent)
    if m is not None and _object_subject(t, s + m.start("subj"),
                                         s + m.end("subj")):
        return ("no_regeneration", True)
    if _STILL_LAND_RE.match(sent):
        return ("still_land", True)
    if _UNCOUNTERABLE_RE.match(sent):
        return ("uncounterable", True)
    if sent.startswith("spend this mana only"):
        r = _payload.parse_mana_restriction(sent)
        if r is not None:
            return ("mana_restriction", r)
    if " cost" in sent and sent.startswith(("this ability costs",
                                             "activating this ability costs")):
        r = _payload.parse_cost_modifier(t, (s, e))
        if r is not None and r.value is not None:
            return ("cost_modifier", r.value)
    if sent.startswith("the ") and " cost is " in sent:
        r = _keywords.parse_cost_rule(t, (s, e))
        if r is not None and r.value is not None:
            return ("cost_rule", r.value)
    return None


# ── L2: one sentence ───────────────────────────────────────────────────

class _F:
    """The mutable frame while one sentence is cut."""
    __slots__ = ("fields", "consumed", "unm", "pending", "riders")

    def __init__(self):
        self.fields = {}
        self.consumed: List[Tuple[str, Span]] = []
        self.unm: List[Tuple[Unmodelled, Span]] = []
        self.pending: List[Tuple[str, str]] = []
        self.riders: List[Tuple[str, Any]] = []


def _cond(t: str, a: int, b: int):
    """parse_condition over ``t[a:b]``; None when the phrase is structure."""
    return _condition.parse_condition(t, (a, b))


def _set_condition(f: _F, c: Condition) -> None:
    old = f.fields.get("condition")
    f.fields["condition"] = c if old is None else Condition(
        ConditionKind.ALL_OF, children=(old, c), raw=old.raw + "; " + c.raw)


def _performed_test(t: str, a: int, b: int) -> Optional[str]:
    """The lemma of a "<player> <VP> this way" performed test at
    ``t[a:b]`` (A14), or None when its subject is not a player: the test
    is on the player's own action, never on an object the action
    produced."""
    v = _lexicon.find_verb(t, (a, b))
    if v.value is not None:
        return v.value.lemma if _player_subject(t, a, v.span[0]) else None
    # A past-tense VP the lexicon does not inflect ("if you exiled a card
    # this way"): the subject is the first word; the lemma stays open.
    sp = t.find(" ", a, b)
    return "" if sp > a and _player_subject(t, a, sp) else None


def _leading(t: str, pos: int, end: int, f: _F) -> Optional[int]:
    """Consume leading frame phrases from `pos`; the new position, or None
    when the rest of the sentence was refused."""
    while pos < end:
        if "connective" not in f.fields and "opener" not in f.fields:
            m = (_THEN_RE.match(t, pos, end) or _actor(_IF_DO_RE, t, pos, end)
                 or _actor(_IF_DONT_RE, t, pos, end)
                 or _OTHERWISE_RE.match(t, pos, end))
            if m is not None:
                rx = m.re
                f.fields["connective"] = (
                    "then" if rx is _THEN_RE else "if_you_do"
                    if rx is _IF_DO_RE else "if_you_dont"
                    if rx is _IF_DONT_RE else "otherwise")
                f.consumed.append(("connective", (pos, m.end())))
                pos = m.end()
                continue
            m = _actor(_IF_DONT_VP_RE, t, pos, end)
            if m is not None:
                comma = t.find(", ", m.end(), end)
                v = _lexicon.find_verb(t, (m.end(), comma)) if comma > 0 \
                    else None
                entry = v.value if v is not None and \
                    v.span[0] == m.end() else None
                if entry is not None:
                    f.fields["connective"] = "if_you_dont"
                    f.fields["named_vp"] = (m.end(), comma)
                    f.fields["named_lemma"] = entry.lemma
                    f.consumed.append(("connective", (pos, m.end())))
                    f.consumed.append(("named_vp", (m.end(), comma + 1)))
                    pos = comma + 2
                    continue
            m = _actor(_WHEN_DO_RE, t, pos, end)
            if m is not None:
                f.consumed.append(("opener", (pos, m.end())))
                pos = m.end()
                iff = None
                if t.startswith("if ", pos):
                    c = _cond(t, pos, end)
                    if c is not None and c.value is not None and c.rest_spans:
                        iff = c.value
                        f.consumed.append(("intervening_if", c.span))
                        f.pending.extend(c.pending)
                        pos = c.rest_spans[0][0]
                    elif c is not None and c.unmodelled is not None:
                        cut = t.find(", ", pos, end)
                        cut = end if cut < 0 else cut + 1
                        f.unm.append((c.unmodelled, (pos, cut)))
                        pos = cut + 1
                f.fields["opener"] = Opener(SubAbilityKind.REFLEXIVE,
                                            intervening_if=iff,
                                            span=(m.start(), m.end()))
                continue
        if t.startswith(("if ", "as long as "), pos):
            c = _cond(t, pos, end)
            if c is None:
                cut = t.find(", ", pos, end)
                if cut < 0 or t.find(_INSTEAD_OF, pos, end) >= 0:
                    return pos               # the A15 override: _trailing
                v = _performed_test(t, pos + 3, cut) \
                    if " this way" in t[pos:cut] and \
                    " would " not in t[pos:cut] else None
                if v is not None:
                    # "If you search your library this way," -- the
                    # performed test of the player's own named earlier
                    # action (A14), a connective like "if you do" whose VP
                    # L5 matches.
                    f.fields.setdefault("connective", "if_you_do")
                    f.fields["named_vp"] = (pos + 3, cut)
                    f.fields["named_lemma"] = v
                    f.consumed.append(("named_vp", (pos, cut + 1)))
                else:
                    # A CR 614 "would" test (tested first: "if a creature
                    # dealt damage this way would die" is a replacement),
                    # an object-qualified result test ("if an insect card
                    # was milled this way": a filter the connective cannot
                    # carry) or another structure phrase the condition
                    # table does not type: refused, never a clause.
                    stage = Stage.REPLACEMENT if " would " in t[pos:cut] \
                        else Stage.CONDITION
                    f.unm.append((_um(stage, "structure_condition"),
                                  (pos, cut + 1)))
                pos = cut + 2
                continue
            if c.value is not None and c.rest_spans:
                _set_condition(f, c.value)
                f.consumed.append(("condition", c.span))
                f.pending.extend(c.pending)
                pos = c.rest_spans[0][0]
                continue
            cut = t.find(", ", pos, end)
            if cut < 0:
                return pos
            f.unm.append((c.unmodelled or _um(Stage.CONDITION, "empty_body"),
                          (pos, cut + 1)))
            pos = cut + 2
            continue
        m = _INSTEAD_LEAD_RE.match(t, pos, end)
        if m is not None and not t.startswith("instead of", pos):
            f.fields["instead"] = True
            f.consumed.append(("instead", (pos, m.end())))
            pos = m.end()
            continue
        if t.startswith("for each ", pos):
            r = _amount.parse_leading_for_each(t, (pos, end))
            if r is not None and r.value is not None and r.rest_spans:
                f.fields["for_each"] = r.value
                f.consumed.append(("for_each", r.span))
                f.pending.extend(r.pending)
                pos = r.rest_spans[0][0]
                continue
            if r is not None and r.unmodelled is not None:
                f.unm.append((r.unmodelled, (pos, end)))
                return None
        if t.startswith("at the beginning of ", pos):
            r = _duration.parse_delay(t, (pos, end))
            if r is not None and r.value is not None and \
                    _duration.LEADING in r.flags and r.rest_spans:
                if "opener" not in f.fields:
                    f.fields["opener"] = Opener(SubAbilityKind.DELAYED,
                                                timing=r.value, span=r.span)
                    f.consumed.append(("delay", r.span))
                    pos = r.rest_spans[0][0]
                    continue
            if r is not None and r.unmodelled is not None:
                f.unm.append((r.unmodelled, (pos, end)))
                return None
        if _DURATION_LEAD_RE.match(t, pos, end):
            r = _duration.parse_duration(t, (pos, end))
            if r is not None and r.span[0] == pos and r.rest_spans:
                if r.value is not None:
                    f.fields["duration"] = r.value
                    f.consumed.append(("duration", r.span))
                else:
                    f.unm.append((r.unmodelled, r.span))
                pos = r.rest_spans[0][0]
                continue
        return pos
    return pos


_WHERE_X = ", where x is "
_UNLESS = " unless "
_INSTEAD_OF = " instead of "


def _trailing(t: str, pos: int, end: int, f: _F) -> Optional[int]:
    """Consume trailing frame phrases before `end`; the new end, or None
    when the sentence was consumed whole (an override) or refused."""
    changed = True
    while changed and end > pos:
        changed = False
        i = t.find(_WHERE_X, pos, end)
        if i >= 0 and "where_x" not in f.fields:
            r = _amount.parse_where_x(t, (i, end))
            if r.value is None:
                f.unm.append((r.unmodelled, (i + 2, end)))
                f.consumed.append(("where_x", (i, end)))
                end = i
                changed = True
                continue
            f.fields["where_x"] = r.value
            f.pending.extend(r.pending)
            f.consumed.append(("where_x", (i, r.span[1])))
            if r.rest_spans:
                # Text printed after the definition is clauses of the
                # body, never part of the definition: the definition is a
                # hole the clause split skips, and the trailing phrases
                # stop here (they would read across the hole).
                f.fields["_hole"] = (i, r.span[1])
                return end
            end = i
            changed = True
            continue
        if " at the beginning of " in t[pos:end] and "opener" not in f.fields:
            r = _duration.parse_delay(t, (pos, end))
            if r is not None:
                if r.value is None:
                    f.unm.append((r.unmodelled, (pos, end)))
                    return None
                if r.span[1] != end or not r.rest_spans:
                    f.unm.append((_um(Stage.DELAY, "delay_not_trailing"),
                                  (pos, end)))
                    return None
                f.fields["opener"] = Opener(SubAbilityKind.DELAYED,
                                            timing=r.value, span=r.span)
                f.consumed.append(("delay", r.span))
                end = r.rest_spans[-1][1]
                changed = True
                continue
        i = t.find(_INSTEAD_OF, pos, end)
        if i >= 0:
            r = _dest.parse_instead_of(t, (pos, end))
            if r.value is not None and "dest_override" in r.flags:
                f.fields["dest_override"] = r.value
                f.fields["dest_object"] = r.object_span
                f.consumed.append(("dest_override", r.span))
            else:
                f.unm.append((r.unmodelled, (pos, end)))
            return None
        i = t.find(_UNLESS, pos, end)
        if i >= 0:
            c = _cond(t, i + 1, end)
            if c is not None and c.value is not None and not c.rest_spans:
                f.fields["unless"] = c.value
                f.pending.extend(c.pending)
                f.consumed.append(("unless", c.span))
            elif c is not None:
                f.unm.append((c.unmodelled or _um(Stage.CONDITION,
                                                  "empty_body"), (i + 1, end)))
            else:
                break
            end = i
            changed = True
            continue
        i = t.find(" instead if ", pos, end)
        if i >= 0:
            c = _cond(t, i + 9, end)
            f.fields["instead"] = True
            f.consumed.append(("instead", (i + 1, i + 8)))
            if c is not None and c.value is not None and not c.rest_spans:
                _set_condition(f, c.value)
                f.pending.extend(c.pending)
                f.consumed.append(("condition", c.span))
            else:
                f.unm.append((c.unmodelled if c is not None and c.unmodelled
                              else _um(Stage.CONDITION, "empty_body"),
                              (i + 9, end)))
            end = i
            changed = True
            continue
        if t.endswith(" instead", pos, end):
            f.fields["instead"] = True
            f.consumed.append(("instead", (end - 7, end)))
            end -= 8
            changed = True
            continue
        i = t.find(" as long as ", pos, end)
        if i > pos:
            c = _cond(t, i + 1, end)
            if c is not None and c.value is not None and not c.rest_spans:
                _set_condition(f, c.value)
                f.pending.extend(c.pending)
                f.consumed.append(("condition", c.span))
            else:
                f.unm.append((c.unmodelled if c is not None and c.unmodelled
                              else _um(Stage.CONDITION, "structure_condition"),
                              (i + 1, end)))
            end = i
            changed = True
            continue
        i = t.rfind(" if ", pos, end)
        if i > pos:
            c = _cond(t, i + 1, end)
            if c is not None and c.value is not None and not c.rest_spans:
                _set_condition(f, c.value)
                f.pending.extend(c.pending)
                f.consumed.append(("condition", c.span))
                end = i
                changed = True
                continue
            if c is not None and c.unmodelled is not None:
                f.unm.append((c.unmodelled, (i + 1, end)))
                end = i
                changed = True
                continue
    return end


def frame_sentence(t: str, s: int, e: int, *, part: int = -1,
                   label: str = "",
                   delay: Optional[DelayedTriggerTiming] = None) -> Frame:
    """L2 and L3 for the sentence ``t[s:e]`` (its final period excluded).
    `delay` is an A5 delayed part's timing (L1 consumed its prefix): the
    sentence opens a DELAYED sub-ability."""
    f = _F()
    if delay is not None:
        f.fields["opener"] = Opener(SubAbilityKind.DELAYED, timing=delay,
                                    span=(s, s))
    if _embedded(t, s, e):
        f.unm.append((_um(Stage.TRIGGER_EMBEDDED, "trigger_embedded"), (s, e)))
        return _freeze(f, (s, e), part, label, ())
    pos = _leading(t, s, e, f)
    if pos is None:
        return _freeze(f, (s, e), part, label, ())
    end = _trailing(t, pos, e, f)
    if end is None:
        return _freeze(f, (s, e), part, label, ())
    a, b = _trim(t, pos, end, " ,")
    clauses: Tuple[Clause, ...] = ()
    if a < b:
        clauses, consumed = _split(t, a, b, f.fields.pop("_hole", None))
        f.consumed.extend(consumed)
    elif not f.unm and f.fields.get("dest_override") is None:
        f.unm.append((_um(Stage.SPLIT, "empty_body"), (s, e)))
    return _freeze(f, (s, e), part, label, clauses)


def _freeze(f: _F, span: Span, part: int, label: str,
            clauses: Tuple[Clause, ...]) -> Frame:
    x = f.fields
    return Frame(span=span, part=part, label=label,
                 connective=x.get("connective", ""),
                 named_vp=x.get("named_vp"), named_lemma=x.get("named_lemma", ""),
                 opener=x.get("opener"), condition=x.get("condition"),
                 instead=x.get("instead", False), duration=x.get("duration"),
                 for_each=x.get("for_each"), unless=x.get("unless"),
                 where_x=x.get("where_x"),
                 dest_override=x.get("dest_override"),
                 dest_object=x.get("dest_object"),
                 riders=tuple(f.riders), consumed=tuple(f.consumed),
                 unmodelled=tuple(f.unm), pending=tuple(f.pending),
                 clauses=clauses)


# ── L3: the clause split ───────────────────────────────────────────────

_SEP_RE = re.compile(r", then |; |, and (?!/)| and (?!/)|, ")
_WORD_START_RE = re.compile(r"(?<![\w'~-])[a-z~]")
_COUNT_ALT = "|".join(COUNT_WORDS)
# A gapped put / return object (A13): a count or a REST / OTHER partitive.
_GAP_OBJECT_RE = re.compile(
    r"(?:the rest|the other|all other|the others|(?:up to )?(?:%s|x|\d+)\b)"
    % _COUNT_ALT)
# The elliptical damage amount (A17): "and 2 damage to", "and x damage to".
_ELLIPTICAL_RE = re.compile(r"(?:%s|x|\d+|that much|half that much|twice "
                            r"that much) damage to " % _COUNT_ALT)
_UNION_RE = re.compile(r"(?:to )?each ")
_DAMAGE_TO = " damage to "
_GAP_LEMMAS = frozenset({"put", "return"})


# A subject of a coordinated clause is a short noun phrase.
_SUBJECT_MAX_WORDS = 6


def _coordinated_subject(t: str, a: int, b: int) -> bool:
    """Does ``t[a:b]`` open with a clause of its own -- a short subject the
    participant or target leaf reads, then a lexicon verb ("draw a card
    and you lose 1 life", "... and attacking player loses that much
    life")? The subject vocabulary is the leaves' (the spine keeps none);
    a type list ("artifacts and creatures") has no verb after its
    member."""
    v = _lexicon.find_verb(t, (a, b))
    if v.value is None or v.span[0] <= a:
        return False
    s, e = _trim(t, a, v.span[0], " ,")
    if s >= e or "," in t[s:e] or \
            t.count(" ", s, e) >= _SUBJECT_MAX_WORDS:
        return False
    if _target.target_words(t, (s, e)):
        return _target.parse_target(t, (s, e)).value is not None
    r = _participant.parse_participant(t, (s, e))
    return r.value is not None


def _holds_verb(t: str, a: int, b: int) -> bool:
    return any(_lexicon.verb_at(t, m.start()) is not None
               for m in _WORD_START_RE.finditer(t, a, b))


class _C:
    __slots__ = ("start", "end", "joiner", "group", "gap", "prefix", "flags",
                 "_verb")

    def __init__(self, start, joiner="", gap="", prefix=()):
        self.start, self.end = start, start
        self.joiner, self.gap, self.prefix = joiner, gap, prefix
        self.group = None
        self.flags = frozenset()
        self._verb = False

    def verb(self, t: str, end: Optional[int] = None):
        """The clause's own lexicon reading (None when its verb is
        inherited or absent): (LexEntry, verb span)."""
        end = self.end if end is None else end
        r = _lexicon.find_verb(t, (self.start, end))
        if r.value is None:
            return None
        return r.value, r.span


def _split(t: str, a: int, b: int, hole: Optional[Span] = None):
    """L3 over the frame body ``t[a:b]``. `hole` is a frame token printed
    inside the body (a where-X definition with clauses after it): no
    separator inside it splits, and the clause it falls in ends at it."""
    out: List[_C] = [_C(a)]
    consumed: List[Tuple[str, Span]] = []
    for m in _SEP_RE.finditer(t, a, b):
        cur = out[-1]
        if m.start() < cur.start:
            continue
        if hole is not None and hole[0] <= m.start() < hole[1]:
            continue
        sep, nxt = m.group(0), m.end()
        if hole is not None and m.start() == hole[1]:
            # The separator after the hole joins the next clause to the
            # clause before the hole: that clause ends at the hole.
            cut = _trim(t, cur.start, hole[0], " ,")[1]
        else:
            cut = m.start()
        new = None
        if sep in (", then ", "; "):
            new = _C(nxt, "then" if sep == ", then " else "semicolon")
        elif sep in (", and ", " and "):
            if _lexicon.verb_at(t, nxt) is not None and (
                    sep == ", and " or _holds_verb(t, cur.start, cut)):
                new = _C(nxt, "and")
            elif _holds_verb(t, cur.start, cut) and \
                    _coordinated_subject(t, nxt, b):
                new = _C(nxt, "and")
            else:
                new = _gapped(t, cur, cut, nxt, b)
        elif _lexicon.verb_at(t, nxt) is not None and \
                _holds_verb(t, cur.start, cut):
            new = _C(nxt, "serial")
        if new is None:
            continue
        cur.end = cut
        consumed.append(("joiner", (m.start(), nxt)))
        out.append(new)
    out[-1].end = b
    _inherit(t, out)
    _reveal_riders(t, out, consumed)
    return tuple(Clause(span=(c.start, c.end), joiner=c.joiner,
                        group=c.group, gap=c.gap, prefix=c.prefix,
                        flags=c.flags)
                 for c in out), consumed


def _antecedent_prefix(c: _C) -> Tuple[Span, ...]:
    return c.prefix if c.gap in ("subject", "verb", "damage") else ()


def _gapped(t: str, cur: _C, cut: int, nxt: int, b: int) -> Optional[_C]:
    """A13 / A17: the gapped, elliptical and union siblings after ``and``."""
    v = cur.verb(t, cut)
    if v is None and cur.prefix:
        return None
    lemma = v[0].lemma if v else ""
    if lemma == "deal":
        if _ELLIPTICAL_RE.match(t, nxt):
            pre = _antecedent_prefix(cur) + ((cur.start, v[1][1]),)
            return _sibling(cur, nxt, "damage", pre)
        to = t.find(_DAMAGE_TO, cur.start, cut)
        if to >= 0 and _UNION_RE.match(t, nxt):
            if t.startswith("to ", nxt):
                pre = _antecedent_prefix(cur) + ((cur.start, to + 7),)
            else:
                pre = _antecedent_prefix(cur) + (
                    (cur.start, to + len(_DAMAGE_TO) - 1),)
            return _sibling(cur, nxt, "recipient", pre)
        return None
    if lemma in _GAP_LEMMAS and _GAP_OBJECT_RE.match(t, nxt):
        if _holds_verb(t, nxt, b):
            return None
        d = _dest.locate_destination(t, (nxt, b), lemma=lemma)
        if d is None:
            return None
        r = _dest.parse_destination(t, d, lemma=lemma)
        if r.value is None:
            return None
        pre = _antecedent_prefix(cur) + ((cur.start, v[1][1]),)
        return _sibling(cur, nxt, "verb", pre)
    return None


def _sibling(cur: _C, nxt: int, gap: str, prefix) -> _C:
    if cur.group is None:
        cur.group = cur.start
    new = _C(nxt, "and", gap, tuple(prefix))
    new.group = cur.group
    return new


def _inherit(t: str, out: List[_C]) -> None:
    """Elided subjects: a coordinated clause that opens with its verb
    inherits the previous clause's subject (its text before its verb)."""
    for prev, c in zip(out, out[1:]):
        if c.gap or c.joiner not in ("and", "serial", "then"):
            continue
        v = c.verb(t)
        if v is None or v[1][0] != c.start:
            continue
        if prev.gap in ("verb", "damage", "recipient"):
            continue
        pv = prev.verb(t)
        if pv is None:
            continue
        subj = _trim(t, prev.start, pv[1][0], " ,")
        if subj[0] < subj[1]:
            c.prefix = (subj,)
            c.gap = "subject"
        elif prev.gap == "subject":
            c.prefix = prev.prefix
            c.gap = "subject"


def _reveal_riders(t: str, out: List[_C], consumed) -> None:
    """"reveal it" after a search or move is the ``reveal`` rider of the
    clause before it, never a clause of its own."""
    i = 1
    while i < len(out):
        c = out[i]
        if _REVEAL_RE.match(t, c.start, c.end) and \
                t[c.start:c.end].startswith("reveal "):
            pv = out[i - 1].verb(t)
            if pv is not None and pv[0].lemma in _REVEAL_AFTER:
                out[i - 1].flags = out[i - 1].flags | {"reveal"}
                consumed.append(("rider", (c.start, c.end)))
                del out[i]
                continue
        i += 1


# ── A host ─────────────────────────────────────────────────────────────

_SENT_END_RE = re.compile(r"\.(?=\s|$)")


def _sentences(t: str, a: int, b: int) -> List[Span]:
    out, pos = [], a
    for m in _SENT_END_RE.finditer(t, a, b):
        s = _trim(t, pos, m.start(), " \n")
        if s[0] < s[1]:
            out.append(s)
        pos = m.end()
    s = _trim(t, pos, b, " \n.")
    if s[0] < s[1]:
        out.append(s)
    return out


def host_sentences(host):
    """(sentence span, part index, label, delay) for every sentence of an
    L1 host's body in printed order; `delay` is an A5 delayed part's
    timing on the part's first sentence only."""
    t = host.text
    if host.parts:
        jobs = [(bs, i, p.label, p.delay) for i, p in enumerate(host.parts)
                for bs in p.body]
    else:
        jobs = [(bs, -1, host.label, None) for bs in host.body]
    for (a, b), part, label, delay in jobs:
        first = True
        for s, e in _sentences(t, a, b):
            yield (s, e), part, label, delay if first else None
            first = False


def frame_host(host) -> Tuple[Frame, ...]:
    """L2 and L3 for one L1 host (`structure.L1Host`; its modes are hosts
    of their own): one Frame per sentence of its body, in printed order.
    A rider sentence joins the frame before it."""
    t = host.text
    frames: List[Frame] = []
    for (s, e), part, label, delay in host_sentences(host):
        rider = _rider(t, s, e)
        if rider is not None and frames:
            frames[-1] = with_rider(frames[-1], rider, (s, e))
            continue
        if rider is not None:
            frames.append(Frame(span=(s, e), part=part, label=label,
                                riders=(rider,),
                                consumed=(("rider", (s, e)),)))
            continue
        frames.append(frame_sentence(t, s, e, part=part, label=label,
                                     delay=delay))
    return tuple(frames)


def with_rider(frame: Frame, rider: Tuple[str, Any], span: Span) -> Frame:
    """`frame` with the rider sentence at `span` absorbed onto it."""
    return frame._replace(riders=frame.riders + (rider,),
                          consumed=frame.consumed + (("rider", span),))


def uncovered(host, frames: Tuple[Frame, ...]) -> str:
    """The coverage invariant after L2/L3: the non-space characters of
    ``host.text`` in no L1 consumed / pending / refusal span, no frame
    token or refusal and no clause span (periods, commas and semicolons
    between spans are structure). '' when covered."""
    t = host.text
    covered = bytearray(len(t))
    spans = [s for _, s in host.consumed] + [s for _, s in host.pending] + \
        [s for _, s in host.unmodelled]
    for f in frames:
        spans += [s for _, s in f.consumed] + [s for _, s in f.unmodelled]
        spans += [c.span for c in f.clauses]
        if f.opener is not None:
            spans.append(f.opener.span)
    for a, b in spans:
        for i in range(max(a, 0), min(b, len(t))):
            covered[i] = 1
    return "".join(ch for i, ch in enumerate(t)
                   if not covered[i] and ch not in " \n.,;")


def clear_caches() -> None:
    """L2/L3 hold no memo of their own: a host is framed once per face
    parse, and the leaves they call are memoised and cleared by theirs."""
