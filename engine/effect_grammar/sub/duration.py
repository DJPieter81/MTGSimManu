"""Duration and delay sub-grammar (design doc 2026-09-29, section 6).

Closed tables over L0 output (the leaf contract in
`engine.effect_grammar.sub`). Runs at load, never at resolution.

Duration (CR 611.2). A printed duration maps onto the existing
`effect_model.DurationKind` vocabulary. F6: E0 adds no DurationKind, so a
printed duration the model cannot expire is ``UNMODELLED(DURATION)`` -- it is
never dropped to ``duration=None``, which `default_duration` would read as
"lasts indefinitely". "this turn" inside a condition or quantity is history
(what happened this turn), not a duration.

Delay (CR 603.7, A30). "at the beginning of <next ...>" opens a delayed
sub-ability; it is never a duration. G14: the timing phrases come only from
`oracle_parser._DELAY_TIMING_PHRASES`. A delay the table lacks ("at end of
combat") is ``UNMODELLED(DELAY)``. A5 / M7: a delay-prefixed paragraph is a
delayed sub-ability on an instant or sorcery and ``UNMODELLED(STRUCTURE)``
on a permanent.

`DURATION_START` is the one duration boundary: other leaves that end a
phrase where a duration begins read it from here, never from a list of
their own.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional, Tuple

from engine.delayed_triggers import DelayedTriggerTiming
from engine.effect_grammar.sub import (CACHE_SIZE, SlotResult, Span,
                                       rest_spans_after, unmodelled)
from engine.effect_model import Duration, DurationKind
from engine.effect_spec import HostKind, Stage, Verb
from engine.oracle_parser import _DELAY_TIMING_PHRASES, _DURATION_MARKERS

__all__ = ["LEAF", "DETAIL_CODES", "DURATION_START", "parse_duration",
           "default_duration", "parse_delay", "delay_paragraph",
           "clear_caches"]

LEAF = "duration"
DETAIL_CODES = frozenset({
    "until_end_of_your_next_turn", "until_end_of_combat",
    "during_next_untap_step", "until_beginning_of", "for_as_long_as",
    "this_combat", "delay_next_step", "delay_end_of_combat",
    "delay_prefixed_permanent_paragraph"})
LEADING = "leading"            # SlotResult flag: the prefix form "X, <effect>"


def _um(stage: Stage, lemma: str, code: str):
    return unmodelled(stage, lemma, LEAF, code, DETAIL_CODES)


# ── Duration table ─────────────────────────────────────────────────────

# Modelled: (pattern, kind). Longest / most specific phrases are tried
# first; overlapping matches resolve leftmost-longest.
_MODELLED = (
    (r"until end of turn", DurationKind.THIS_TURN),
    (r"until your next turn", DurationKind.UNTIL_YOUR_NEXT_TURN),
    (r"until ~ leaves the battlefield", DurationKind.UNTIL_LEAVES),
    (r"for as long as ~ remains on the battlefield", DurationKind.UNTIL_LEAVES),
    # CR 611.2a: an effect that lasts for the rest of the game has no end.
    (r"for the rest of the game", DurationKind.PERMANENT),
    (r"this turn", DurationKind.THIS_TURN),
)

# Printed durations `effect_model` cannot expire yet (F6). The code names
# the shape so the census groups them.
_UNMODELLED = (
    (r"until (?:the )?end of your next turn", "until_end_of_your_next_turn"),
    (r"until (?:the )?end of combat", "until_end_of_combat"),
    (r"during [\w' ~]+? next untap step", "during_next_untap_step"),
    (r"(?:%s)[^,.;]+" % "|".join(re.escape(m) for m in _DURATION_MARKERS),
     "until_beginning_of"),
    (r"for as long as [^,.;]+", "for_as_long_as"),
    (r"this combat", "this_combat"),
)

_COMPILED = tuple((re.compile(r"\b%s\b" % p if p[0].isalpha() else p), k, None)
                  for p, k in _MODELLED) + tuple(
    (re.compile(r"\b%s" % p), None, d) for p, d in _UNMODELLED)

# Phrases that are history rather than a duration when they close a
# condition or a quantity ("creatures that attacked this combat").
_HISTORY_PHRASES = frozenset({"this turn", "this combat"})


def _lead(pattern: str) -> str:
    """The literal leading words of a table pattern ('until ~ leaves ...'
    -> 'until ~ leaves the battlefield'; 'during [\\w...' -> 'during')."""
    m = re.match(r"[a-z~' ]+", pattern)
    words = (m.group(0) if m else "").split()
    if m and m.end() < len(pattern):
        words = words[:-1] if pattern[m.end() - 1] != " " else words
    return " ".join(words)


def _duration_leads() -> Tuple[str, ...]:
    leads = {_lead(p) for p, _ in _MODELLED + _UNMODELLED if _lead(p)}
    leads.update(_DURATION_MARKERS)
    minimal = {lead for lead in leads
               if not any(o != lead and (lead + " ").startswith(o + " ")
                          for o in leads)}
    return tuple(sorted(minimal, key=lambda s: (-len(s), s)))


# Where a printed duration begins: the minimal leading words of every row
# of the tables above ("until", "during", "for as long as", "this turn",
# ...). A phrase ending at this boundary leaves the duration to this leaf.
DURATION_START = r"(?:%s)\b" % "|".join(re.escape(w) for w in _duration_leads())

# "this turn" is history when it closes a condition, a quantity or a
# relative clause about past events ("if you gained life this turn",
# "for each creature that died this turn", "spells you've cast this turn").
# Looked for between the last clause boundary and the phrase. A frame whose
# predicate is prospective ("if a source would deal damage this turn") is a
# duration: "would" says what may happen, not what did. "any number of" is
# a determiner, not the quantity "the number of".
_HISTORY_FRAME = re.compile(
    r"\b(?:if|unless|as long as|for each|(?<!\bany )number of|equal to|"
    r"where x is|you've|you have|who|whose|"
    r"that (?:\w+ )?(?:was|were|wasn't|weren't|has|had|have|hasn't|haven't)|"
    r"that \w+ed)(?![\w'])")
_PROSPECTIVE = re.compile(r"\bwould\b")
# Past-tense event verbs. Active forms only: a passive "be blocked" /
# "be countered" is a prohibition or a prevention scope, not history.
_HISTORY_EVENT = re.compile(
    r"(?<!\bbe )(?<!\bwould be )\b(?:died|attacked|blocked|lost life|gained life|"
    r"dealt (?:combat )?damage|entered(?: the battlefield)?|sacrificed|"
    r"discarded|left the battlefield|milled|countered|descended|"
    r"committed a crime|put into [\w' ]*graveyards?)\b")
# Verbs that are history only as the phrase's immediate predicate
# ("spells you cast this turn"), since "you may cast that card this turn"
# is a permission with a duration.
_HISTORY_TAIL = re.compile(
    r"\b(?:cast|played|drawn|drew)(?: before it)?\s*$")
# A prohibition, permission, requirement or grant verb after the last
# history frame or event is the predicate the phrase closes: the phrase is
# its duration ("creatures dealt damage this way can't block this turn",
# "any creature with power equal to ... can't block this turn"). has/have
# are not stops: as auxiliaries ("has cast", "have lost") they head the
# history itself.
_PREDICATE_STOP = re.compile(
    r"\b(?:can't|cannot|can|may|must|gets?|gains?|loses?)(?![\w'])")
_BOUNDARY = re.compile(r"[,.;:]")


def _is_history(clause: str, start: int) -> bool:
    head = clause[:start]
    cut = max((m.end() for m in _BOUNDARY.finditer(head)), default=0)
    seg = head[cut:]
    if _PROSPECTIVE.search(seg):
        return False
    if _HISTORY_TAIL.search(seg):
        return True
    last = max((m.end() for rx in (_HISTORY_FRAME, _HISTORY_EVENT)
                for m in rx.finditer(seg)), default=None)
    if last is None:
        return False
    return not any(m.start() >= last for m in _PREDICATE_STOP.finditer(seg))


def _around(host: str, a: int, b: int, span: Span,
            final: str = " ") -> Tuple[Span, ...]:
    """The rest spans of ``host[a:b]`` around ``span``: the text before and
    after it, trimmed, with the comma that set a leading or mid-clause
    phrase off dropped ("if you do, until end of turn, X" -> "if you do,"
    + "X"). `final` is stripped from the end of the last piece."""
    s, e = span
    before = rest_spans_after(host, a, s)
    after_start = e
    while after_start < b and host[after_start] == " ":
        after_start += 1
    if after_start < b and host[after_start] == "," and (
            not before or host[before[0][1] - 1] == ","):
        after_start += 1                 # leading, or the comma is doubled
    after = rest_spans_after(host, after_start, b, final)
    if before and host[before[0][1] - 1] == "," and (
            not after or host[after[0][0]] in ".;"):
        before = rest_spans_after(host, before[0][0], before[0][1], " ,")
    if not after and final != " " and before:
        before = rest_spans_after(host, before[0][0], before[0][1], final)
    return before + after


def _slot(host: str, span: Optional[Span]) -> Span:
    return (0, len(host)) if span is None else span


def _delay_span(clause: str) -> Optional[Span]:
    # Not memoised: its one caller, `_duration_rel`, is, on the same key.
    m = _DELAY_RE.search(clause) or _DELAY_UNMODELLED_RE.search(clause)
    return None if m is None else (m.start(), m.end())


# Every table row opens with one of the DURATION_START leads, so a clause
# holding none of them holds no duration: one search gates the twelve.
_ANY_LEAD = re.compile(r"\b" + DURATION_START)


@lru_cache(maxsize=CACHE_SIZE)
def _duration_rel(clause: str):
    if _ANY_LEAD.search(clause) is None:
        return None
    delay = _delay_span(clause)
    best = None
    for rx, kind, code in _COMPILED:
        for m in rx.finditer(clause):
            if m.group(0) in _HISTORY_PHRASES and _is_history(clause, m.start()):
                continue
            if delay is not None and delay[0] <= m.start() < delay[1]:
                continue                 # inside a delay phrase, not a duration
            key = (m.start(), -(m.end() - m.start()))
            if best is None or key < best[0]:
                best = (key, m.start(), m.end(), kind, code)
            break
    return None if best is None else best[1:]


def parse_duration(host: str, span: Optional[Span] = None, *,
                   lemma: str = "") -> Optional[SlotResult]:
    """The printed duration of one clause ``host[span]`` (default: the
    whole host), or None when it prints none. Leading ("until end of turn,
    ...") and trailing ("... until end of turn") forms are the same
    duration. ``rest_spans`` is the clause around the duration, so a scaler
    after it ("... until end of turn for each ...") reaches the amount
    sub-grammar with its host offsets."""
    a, b = _slot(host, span)
    rel = _duration_rel(host[a:b])
    if rel is None:
        return None
    s, e, kind, code = rel
    s, e = s + a, e + a
    rest = _around(host, a, b, (s, e))
    if kind is not None:
        return SlotResult(value=Duration(kind), span=(s, e), rest_spans=rest)
    return SlotResult(unmodelled=_um(Stage.DURATION, lemma, code),
                      span=(s, e), rest_spans=rest)


# Hosts whose continuous effects come from a resolving spell or ability
# (CR 611.2a: no printed duration means it lasts indefinitely).
_RESOLVING_HOSTS = frozenset({
    HostKind.SPELL, HostKind.MODE, HostKind.ACTIVATED, HostKind.MANA_ABILITY,
    HostKind.LOYALTY, HostKind.TRIGGERED, HostKind.CHAPTER})


def default_duration(host_kind: HostKind, verb: Verb) -> Optional[Duration]:
    """The duration of a spec that prints none.

    Only CONTINUOUS has an unprinted default (invariant 6): PERMANENT on a
    resolving host (CR 611.2a), WHILE_SOURCE_ON_BATTLEFIELD on a static
    ability (CR 611.3a). Any other host kind returns None, which the caller
    must lower to UNMODELLED(DURATION) rather than guess."""
    if verb is not Verb.CONTINUOUS:
        return None
    if host_kind is HostKind.STATIC:
        return Duration(DurationKind.WHILE_SOURCE_ON_BATTLEFIELD)
    if host_kind in _RESOLVING_HOSTS:
        return Duration(DurationKind.PERMANENT)
    return None


# ── Delay table (G14) ──────────────────────────────────────────────────

_DELAY_WHEN = "|".join(re.escape(p) for p in
                       sorted(_DELAY_TIMING_PHRASES, key=len, reverse=True))
# The printed step may carry the word 'step' ("your next upkeep step"); the
# timing table is unchanged.
_DELAY_RE = re.compile(
    r"\bat the beginning of (?P<when>%s)(?: step)?\b" % _DELAY_WHEN)
# A delay the phrase table lacks: another "next" point in the turn, or the
# end of this combat.
_DELAY_UNMODELLED_RE = re.compile(
    r"\bat the beginning of (?:the|your|that player's|its controller's|"
    r"its owner's) next [\w' ]+?(?=[,.;]|$)|(?P<eoc>\bat end of combat\b)")


def parse_delay(host: str, span: Optional[Span] = None, *,
                lemma: str = "") -> Optional[SlotResult]:
    """The delay of one clause ``host[span]``, or None. Only "at the
    beginning of" opens a delay; "until/before the beginning of ..."
    (`_DURATION_MARKERS`) is a duration, never a delay.

    ``value`` is the `DelayedTriggerTiming`; ``rest_spans`` is the delayed
    effect text (its final period dropped); the ``leading`` flag marks the
    prefix form "at the beginning of ..., <effect>"."""
    a, b = _slot(host, span)
    clause = host[a:b]
    m = _DELAY_RE.search(clause)
    value = code = None
    if m is not None:
        value = DelayedTriggerTiming[_DELAY_TIMING_PHRASES[m.group("when")]]
    else:
        m = _DELAY_UNMODELLED_RE.search(clause)
        if m is None:
            return None
        code = "delay_end_of_combat" if m.group("eoc") else "delay_next_step"
    s, e = m.start() + a, m.end() + a
    flags = frozenset({LEADING}) if not clause[:m.start()].strip() else frozenset()
    rest = _around(host, a, b, (s, e), final=" .")
    if value is not None:
        return SlotResult(value=value, span=(s, e), rest_spans=rest, flags=flags)
    return SlotResult(unmodelled=_um(Stage.DELAY, lemma, code), span=(s, e),
                      rest_spans=rest, flags=flags)


def delay_paragraph(paragraph: str, is_spell: bool) -> Optional[SlotResult]:
    """A5 / M7. A paragraph that OPENS with a "next ..." delay phrase is a
    delayed sub-ability of an instant or sorcery's SPELL host (CR 603.7);
    on a permanent it is UNMODELLED(STRUCTURE) over the whole paragraph,
    since a printed triggered ability never says "the next". Any other
    paragraph returns None."""
    d = parse_delay(paragraph)
    if d is None or LEADING not in d.flags:
        return None
    if is_spell:
        return d
    return SlotResult(
        unmodelled=_um(Stage.STRUCTURE, "", "delay_prefixed_permanent_paragraph"),
        span=rest_spans_after(paragraph, 0, len(paragraph))[0])


def clear_caches() -> None:
    _duration_rel.cache_clear()
