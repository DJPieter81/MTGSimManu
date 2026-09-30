"""Duration and delay sub-grammar (design doc 2026-09-29, section 6).

Closed tables over NORMALISED clause text (lowercased, dashes unified,
self-references replaced by ``~``). Runs at load, never at resolution.

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
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple, Union

from engine.delayed_triggers import DelayedTriggerTiming
from engine.effect_model import Duration, DurationKind
from engine.effect_spec import HostKind, Stage, Unmodelled, Verb
from engine.oracle_parser import _DELAY_TIMING_PHRASES, _DURATION_MARKERS

__all__ = ["DurationMatch", "DelayMatch", "parse_duration",
           "default_duration", "parse_delay", "delay_paragraph"]


@dataclass(frozen=True, slots=True)
class DurationMatch:
    """A printed duration. Exactly one of `duration` / `unmodelled` is set."""
    duration: Optional[Duration]
    unmodelled: Optional[Unmodelled]
    span: Tuple[int, int]          # into the clause text passed in
    rest: str                      # the clause with the duration removed


@dataclass(frozen=True, slots=True)
class DelayMatch:
    """A printed delay. Exactly one of `timing` / `unmodelled` is set."""
    timing: Optional[DelayedTriggerTiming]
    unmodelled: Optional[Unmodelled]
    span: Tuple[int, int]
    inner: str                     # the delayed effect text
    leading: bool                  # "at the beginning of ..., X" (prefix form)


# ── Duration table ─────────────────────────────────────────────────────

_SELF = r"(?:~|this (?:creature|artifact|enchantment|permanent|land|planeswalker))"

# Modelled: (pattern, kind). Longest / most specific phrases are tried
# first; overlapping matches resolve leftmost-longest.
_MODELLED = (
    (r"until end of turn", DurationKind.THIS_TURN),
    (r"until your next turn", DurationKind.UNTIL_YOUR_NEXT_TURN),
    (r"until %s leaves the battlefield" % _SELF, DurationKind.UNTIL_LEAVES),
    (r"for as long as %s remains on the battlefield" % _SELF,
     DurationKind.UNTIL_LEAVES),
    (r"this turn", DurationKind.THIS_TURN),
)

# Printed durations `effect_model` cannot expire yet (F6). The detail names
# the shape so the census groups them.
_UNMODELLED = (
    (r"until (?:the )?end of your next turn", "until_end_of_your_next_turn"),
    (r"until (?:the )?end of combat", "until_end_of_combat"),
    (r"during [\w' ~]+? next untap step", "during_next_untap_step"),
    (r"(?:%s)[^,.;]+" % "|".join(re.escape(m) for m in _DURATION_MARKERS),
     "until_beginning_of"),
    (r"for as long as [^,.;]+", "for_as_long_as"),
)

_COMPILED = tuple((re.compile(r"\b%s\b" % p if p[0].isalpha() else p), k, None)
                  for p, k in _MODELLED) + tuple(
    (re.compile(r"\b%s" % p), None, d) for p, d in _UNMODELLED)

# "this turn" is history when it closes a condition, a quantity or a
# relative clause about past events ("if you gained life this turn",
# "for each creature that died this turn", "spells you've cast this turn").
# Looked for between the last clause boundary and the phrase. A frame whose
# predicate is prospective ("if a source would deal damage this turn") is a
# duration: "would" says what may happen, not what did.
_HISTORY_FRAME = re.compile(
    r"\b(?:if|unless|as long as|for each|number of|equal to|where x is|"
    r"you've|you have|who|whose|that (?:\w+ )?(?:was|were|has|had|have))\b")
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
_BOUNDARY = re.compile(r"[,.;:]")


def _is_history(clause: str, start: int) -> bool:
    head = clause[:start]
    cut = max((m.end() for m in _BOUNDARY.finditer(head)), default=0)
    seg = head[cut:]
    if _PROSPECTIVE.search(seg):
        return False
    return bool(_HISTORY_FRAME.search(seg) or _HISTORY_EVENT.search(seg)
                or _HISTORY_TAIL.search(seg))


def _strip(clause: str, span: Tuple[int, int]) -> str:
    before, after = clause[:span[0]], clause[span[1]:]
    if not before.strip():                    # leading "until end of turn, X"
        after = after.lstrip()
        if after[:1] == ",":
            after = after[1:]
        return after.strip()
    return re.sub(r"\s+", " ", (before.rstrip() + " " + after.lstrip())).strip(
        ).replace(" ,", ",").replace(" .", ".")


def parse_duration(clause: str) -> Optional[DurationMatch]:
    """The printed duration of one normalised clause, or None when the
    clause prints none. Leading ("until end of turn, ...") and trailing
    ("... until end of turn") forms are the same duration, and a scaler
    after the duration ("... until end of turn for each ...") stays in
    `rest` for the amount sub-grammar."""
    best = None
    for rx, kind, detail in _COMPILED:
        for m in rx.finditer(clause):
            if kind is DurationKind.THIS_TURN and m.group(0) == "this turn" \
                    and _is_history(clause, m.start()):
                continue
            key = (m.start(), -(m.end() - m.start()))
            if best is None or key < best[0]:
                best = (key, m, kind, detail)
            break
    if best is None:
        return None
    _, m, kind, detail = best
    span = (m.start(), m.end())
    if kind is not None:
        return DurationMatch(Duration(kind), None, span, _strip(clause, span))
    return DurationMatch(None, Unmodelled(Stage.DURATION, detail=detail),
                         span, _strip(clause, span))


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
_DELAY_RE = re.compile(r"\bat the beginning of (?P<when>%s)\b" % _DELAY_WHEN)
# A delay the phrase table lacks: another "next" point in the turn, or the
# end of this combat.
_DELAY_UNMODELLED_RE = re.compile(
    r"\bat the beginning of (?:the|your|that player's|its controller's|"
    r"its owner's) next [\w' ]+?(?=[,.;]|$)|\bat end of combat\b")


def _delay_inner(clause: str, span: Tuple[int, int]) -> Tuple[str, bool]:
    leading = not clause[:span[0]].strip()
    return _strip(clause, span).rstrip("."), leading


def parse_delay(clause: str) -> Optional[DelayMatch]:
    """The delay of one normalised clause, or None. Only "at the
    beginning of" opens a delay; "until/before the beginning of ..."
    (`_DURATION_MARKERS`) is a duration, never a delay."""
    m = _DELAY_RE.search(clause)
    if m is not None:
        span = (m.start(), m.end())
        inner, leading = _delay_inner(clause, span)
        return DelayMatch(DelayedTriggerTiming[_DELAY_TIMING_PHRASES[m.group("when")]],
                          None, span, inner, leading)
    m = _DELAY_UNMODELLED_RE.search(clause)
    if m is None:
        return None
    span = (m.start(), m.end())
    inner, leading = _delay_inner(clause, span)
    return DelayMatch(None, Unmodelled(Stage.DELAY, detail=m.group(0)),
                      span, inner, leading)


def delay_paragraph(paragraph: str, is_spell: bool
                    ) -> Union[DelayMatch, Unmodelled, None]:
    """A5 / M7. A paragraph that OPENS with a "next ..." delay phrase is a
    delayed sub-ability of an instant or sorcery's SPELL host (CR 603.7);
    on a permanent it is UNMODELLED(STRUCTURE), since a printed triggered
    ability never says "the next". Any other paragraph returns None."""
    d = parse_delay(paragraph)
    if d is None or not d.leading:
        return None
    if is_spell:
        return d
    return Unmodelled(Stage.STRUCTURE, detail="delay_prefixed_permanent_paragraph")
