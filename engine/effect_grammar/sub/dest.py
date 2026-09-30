"""Destination sub-grammar (design doc 2026-09-29: section 4 MOVE row and
the put/return disambiguation; section 3 L2 instead-of trailer; A9, A13,
A15, A18).

A closed table over NORMALISED clause text (lowercased, dashes unified,
self-references already ``~``). It runs at load, never at resolution, and
reads no game.

* ``parse_destination`` types one destination prepositional phrase
  ("onto the battlefield tapped under your control", "on the bottom of your
  library in any order", "into its owner's library third from the top")
  into `effect_spec.Destination`. Every token of the slot must be consumed;
  an unconsumed tail or a phrase outside the table is an `Unmodelled`
  result, never a guessed destination.
* ``locate_destination`` finds the destination PP inside a slot. It is the
  standalone PP parser the gapped ``and <count|REST NP> <destination PP>``
  form needs (A13), and it splits a put/return object from its destination.
* ``parse_instead_of`` types the trailing override "<move VP> instead of
  putting it into <zone>" as ``Destination(zone, instead_of=<zone>)`` with
  the ``dest_override`` flag (A15, CR 701.5a): it changes where the named
  action sends the object; it never replaces the action.
* ``source_zone`` reads a move's source zone from its OBJECT span. "return"
  names no zone of its own, so the zone is never inferred from verb plus
  destination (section 4).

Zones (CR 400.1) are the `CardInstance.zone` strings: battlefield, hand,
graveyard, library, exile, stack. A card put into a hand, graveyard or
library goes to its owner's (CR 400.3), so the printed possessive of those
zones is consumed but not stored; only battlefield control ("under your
control") is a `Destination.controller`.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import FrozenSet, Optional, Tuple

from engine.effect_spec import (Amount, AmountKind, Destination, Ref, RefKind,
                                Stage, Unmodelled)

__all__ = ["Span", "SlotResult", "parse_destination", "locate_destination",
           "parse_instead_of", "source_zone", "clear_caches"]

Span = Tuple[int, int]

_DEFAULT_LEMMA = "move"
_DEST_OVERRIDE = "dest_override"     # effect_spec.SPEC_FLAGS


@dataclass(frozen=True, slots=True)
class SlotResult:
    """A typed slot. Exactly one of ``value`` / ``unmodelled`` is set.

    ``span`` is absolute in the text passed in: the consumed phrase on
    success, the whole (whitespace-trimmed) slot on failure.
    ``object_span`` is the moved object's span when the slot held one
    (``parse_instead_of``)."""
    value: Optional[Destination]
    span: Span
    unmodelled: Optional[Unmodelled] = None
    flags: FrozenSet[str] = frozenset()
    object_span: Optional[Span] = None

    @property
    def ok(self) -> bool:
        return self.value is not None


# ── Closed vocabulary ──────────────────────────────────────────────────

# Possessives a destination zone may carry. A9: '~'s owner's' is the
# normalised "his/her owner's" on legendary and planeswalker faces.
_OWNER_POSS = (r"(?:its|their|his|her|~'s|that card's|that creature's|"
               r"that permanent's) owners?'s?")
_ZONE_POSS = (r"(?:your|their|its|~'s|that player's|each player's|the|"
              r"%s)" % _OWNER_POSS)

_CONTROL_OWNER = re.compile(r"under %s control" % _OWNER_POSS)
_CONTROL_YOU = re.compile(r"under your control")
_CONTROL_OTHER = re.compile(r"under [^,;.]+? control")

# Heads, most specific first. Each row: (pattern, zone, position).
_HEADS = (
    (r"on (?:(?:your|their) choice of )?(?:the )?top or (?:the )?bottom"
     r"(?: of %s librar(?:y|ies))?" % _ZONE_POSS, None, "top_or_bottom"),
    (r"back(?=(?: in (?:any|a random) order)?$)", None, "back"),         # the zone it came from: linking binds it
    (r"(?:onto|to) the battlefield", "battlefield", None),
    (r"on (?:the )?top of %s librar(?:y|ies)" % _ZONE_POSS, "library", "top"),
    (r"on the bottom of %s librar(?:y|ies)" % _ZONE_POSS, "library", "bottom"),
    # The library elided after a look/reveal of it ("put the rest on the
    # bottom in a random order").
    (r"on (?:the )?top(?! of)", "library", "top"),
    (r"on the bottom(?! of)", "library", "bottom"),
    (r"into %s librar(?:y|ies)" % _ZONE_POSS, "library", None),
    (r"(?:into|to) (?:%s )?hands?" % _ZONE_POSS, "hand", None),
    (r"into (?:%s )?graveyards?" % _ZONE_POSS, "graveyard", None),
    (r"into exile", "exile", None),
)
_HEAD_RES = tuple((re.compile(r"%s(?![\w'])" % p), z, pos) for p, z, pos in _HEADS)
_ANY_HEAD = re.compile(r"(?<![\w'])(?:%s)(?![\w'])" % "|".join(p for p, _, _ in _HEADS))

_COUNT_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
                "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10}
_ORDINALS = {"second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
             "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}

_COUNTER_ITEM = re.compile(
    r"(?P<n>[a-z]+|\d+) (?:additional )?"
    r"(?P<kind>[+-]\d+/[+-]\d+|(?:[a-z]+ )?[a-z]+?) counters?")
_WITH_COUNTERS = re.compile(r"with (?P<items>.+?) on (?:it|them|each of them|~)(?![\w'])")

# Modifiers after a head, keyed by the zones that admit them.
_BF_FLAGS = (
    (re.compile(r"tapped"), "tapped"),
    (re.compile(r"attacking"), "attacking"),
    (re.compile(r"transformed"), "transformed"),
    (re.compile(r"face down"), "face_down"),
    (re.compile(r"\(?front face up\)?"), None),      # the default face
)
_ATTACHED_SELF = re.compile(r"attached to ~(?![\w'])")
_ATTACHED_OTHER = re.compile(r"attached to [^,;.]+")
_ORDER = re.compile(r"in (?P<o>any|a random) order")
_NTH = re.compile(r"(?P<o>%s) from the top" % "|".join(_ORDINALS))
_SEP = re.compile(r"(?:,? and | )")


def _unmodelled(stage: Stage, lemma: str, detail: str) -> Unmodelled:
    return Unmodelled(stage=stage, lemma=lemma or _DEFAULT_LEMMA, detail=detail)


def _amount(word: str) -> Optional[Amount]:
    if word == "x":
        return Amount(AmountKind.X, n=1)
    if word.isdigit():
        return Amount(AmountKind.LITERAL, n=int(word))
    n = _COUNT_WORDS.get(word)
    return None if n is None else Amount(AmountKind.LITERAL, n=n)


def _counters(items: str) -> Optional[Tuple[Tuple[str, Amount], ...]]:
    """'a +1/+1 counter and a flying counter' -> ((kind, Amount), ...), or
    None when any token is left over or a count is outside the table."""
    out = []
    for part in re.split(r",? and |, ", items):
        m = _COUNTER_ITEM.fullmatch(part)
        if not m:
            return None
        amt = _amount(m.group("n"))
        if amt is None:
            return None
        out.append((m.group("kind"), amt))
    return tuple(out)


# ── The destination PP ─────────────────────────────────────────────────

def _parse_modifiers(s: str, pos: int, zone: str, lemma: str, fields: dict):
    """Consume the modifiers after a head. Returns (pos, Unmodelled|None)."""
    while pos < len(s):
        m = _SEP.match(s, pos)
        if not m:
            break
        p = m.end()
        hit = None
        if zone == "battlefield":
            for rx, flag in _BF_FLAGS:
                mm = rx.match(s, p)
                if mm and (mm.end() == len(s) or not s[mm.end()].isalnum()):
                    hit = mm
                    if flag:
                        fields[flag] = True
                    break
            if hit is None:
                mm = _CONTROL_YOU.match(s, p)
                if mm:
                    fields["controller"], hit = "you", mm
                else:
                    mm = _CONTROL_OWNER.match(s, p)
                    if mm:
                        fields["controller"], hit = "owner", mm
                    elif _CONTROL_OTHER.match(s, p):
                        return pos, _unmodelled(Stage.RECOGNIZED_UNSUPPORTED,
                                                lemma, "destination.controller")
            if hit is None:
                mm = _ATTACHED_SELF.match(s, p)
                if mm:
                    fields["attached_to"], hit = Ref(RefKind.SELF), mm
                elif _ATTACHED_OTHER.match(s, p):
                    return pos, _unmodelled(Stage.REFERENCE, lemma,
                                            "destination.attached_to")
        if hit is None and zone in ("battlefield", "exile"):
            mm = _WITH_COUNTERS.match(s, p)
            if mm:
                counters = _counters(mm.group("items"))
                if counters is None:
                    return pos, _unmodelled(Stage.AMOUNT, lemma,
                                            "destination.entry_counters")
                fields["entry_counters"] = counters
                hit = mm
        if hit is None and zone == "library":
            mm = _ORDER.match(s, p)
            if mm:
                fields["order"] = "any" if mm.group("o") == "any" else "random"
                hit = mm
            elif fields.get("position") is None:
                mm = _NTH.match(s, p)
                if mm:
                    fields["position"] = "nth"
                    fields["nth"] = _ORDINALS[mm.group("o")]
                    hit = mm
        if hit is None:
            break
        pos = hit.end()
    return pos, None


@lru_cache(maxsize=None)
def _parse_rel(s: str, lemma: str):
    """(Destination|None, Unmodelled|None) for a trimmed slot string."""
    for rx, zone, position in _HEAD_RES:
        m = rx.match(s)
        if not m:
            continue
        if zone is None:
            return None, _unmodelled(Stage.RECOGNIZED_UNSUPPORTED, lemma,
                                     "destination.%s" % position)
        if lemma == "shuffle" and not (zone == "library" and position is None):
            return None, _unmodelled(Stage.CLAUSE, lemma,
                                     "destination.shuffle_into_non_library")
        fields = {"position": position}
        pos, bad = _parse_modifiers(s, m.end(), zone, lemma, fields)
        if bad is not None:
            return None, bad
        if pos != len(s):
            return None, _unmodelled(Stage.CLAUSE, lemma,
                                     "destination.unconsumed:%s" % s[pos:].strip())
        if lemma == "shuffle":
            fields["position"] = "shuffle"       # A18
        return Destination(zone, **fields), None
    return None, _unmodelled(Stage.CLAUSE, lemma, "destination.no_head")


def _trim(text: str, span: Span) -> Span:
    start, end = span
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def parse_destination(text: str, span: Span, *, lemma: str = "") -> SlotResult:
    """Type the destination PP that fills ``text[span]``.

    ``lemma`` is the move verb's lemma when the caller knows it; 'shuffle'
    makes "into <library>" a shuffle-into (A18) and admits no other zone."""
    start, end = _trim(text, span)
    value, bad = _parse_rel(text[start:end].replace("’", "'"), lemma)
    return SlotResult(value=value, span=(start, end), unmodelled=bad)


def locate_destination(text: str, span: Span, *, lemma: str = "") -> Optional[Span]:
    """The span of the destination PP inside ``text[span]``, running to the
    slot end: the first head whose phrase parses fully, else the first
    head (so its UNMODELLED result can be reported), else None."""
    start, end = _trim(text, span)
    s = text[:end].replace("’", "'")
    first = None
    for m in _ANY_HEAD.finditer(s, start, end):
        cand = (m.start(), end)
        if first is None:
            first = cand
        value, _ = _parse_rel(s[m.start():end], lemma)
        if value is not None:
            return cand
    return first


# ── The instead-of override (A15) ──────────────────────────────────────

_THIS_WAY_RIDER = re.compile(
    r"if [^,]+? (?:is|are|was|were|would be) [a-z]+ this way, ")
_INSTEAD_OF = re.compile(
    r" instead of (?:putting (?:it|them|that card|that spell|~) )?"
    r"(?:into (?:%s )?(?P<zone>graveyard|hand|library|exile)s?|"
    r"(?P<anywhere>anywhere else))(?: as it resolves)?$" % _ZONE_POSS)
_MOVE_VERB = re.compile(r"(?P<verb>exile|put|return|shuffle)s? ")
_EXILE_TAIL = re.compile(r" (?P<mods>with .+ on (?:it|them|~))$")


@lru_cache(maxsize=None)
def _instead_rel(s: str):
    """(Destination|None, Unmodelled|None, object span|None) relative to s."""
    lemma = _DEFAULT_LEMMA
    pos = 0
    m = _THIS_WAY_RIDER.match(s)
    if m:
        pos = m.end()
    tail = _INSTEAD_OF.search(s, pos)
    if tail is None:
        return None, _unmodelled(Stage.CLAUSE, lemma, "instead_of.no_replaced_zone"), None
    replaced = tail.group("zone") or "anywhere"
    vm = _MOVE_VERB.match(s, pos)
    if vm is None:
        return None, _unmodelled(Stage.CLAUSE, lemma, "instead_of.no_move_action"), None
    verb = vm.group("verb")
    body_start, body_end = vm.end(), tail.start()
    if verb == "exile":
        mods = _EXILE_TAIL.search(s, body_start, body_end)
        obj = (body_start, mods.start() if mods else body_end)
        fields = {}
        if mods:
            counters = _WITH_COUNTERS.fullmatch(mods.group("mods"))
            items = _counters(counters.group("items")) if counters else None
            if items is None:
                return None, _unmodelled(Stage.AMOUNT, verb,
                                         "instead_of.entry_counters"), obj
            fields["entry_counters"] = items
        dest = Destination("exile", **fields)
    else:
        sub_lemma = "shuffle" if verb == "shuffle" else ""
        where = locate_destination(s, (body_start, body_end), lemma=sub_lemma)
        if where is None:
            return None, _unmodelled(Stage.CLAUSE, verb, "instead_of.no_destination"), None
        obj = (body_start, where[0])
        dest, bad = _parse_rel(s[where[0]:where[1]].rstrip(), sub_lemma)
        if bad is not None:
            return None, bad, obj
    obj = _trim(s, obj)
    if obj[0] == obj[1]:
        return None, _unmodelled(Stage.CLAUSE, verb, "instead_of.no_object"), None
    return dataclasses.replace(dest, instead_of=replaced), None, obj


def parse_instead_of(text: str, span: Span) -> SlotResult:
    """Type "<move VP> instead of putting it into <zone>" (optionally led by
    the "If <ref> is <verb>ed this way," rider) as a destination override
    of the named action: ``Destination(zone, instead_of=<replaced zone>)``
    flagged ``dest_override`` (A15, CR 701.5a). "anywhere else" is the
    replaced zone 'anywhere'."""
    start, end = _trim(text, span)
    value, bad, obj = _instead_rel(text[start:end].replace("’", "'"))
    return SlotResult(
        value=value, span=(start, end), unmodelled=bad,
        flags=frozenset({_DEST_OVERRIDE}) if value is not None else frozenset(),
        object_span=None if obj is None else (start + obj[0], start + obj[1]))


# ── return: the source zone comes from the object span ─────────────────

_ZONE_PREP = re.compile(r"\b(?:from|in)\b")
_ZONE_WORD = re.compile(r"\b(graveyards?|hands?|librar(?:y|ies)|exile)\b")
_EXILED = re.compile(r"\bexiled\b")
_SPELL = re.compile(r"\bspells?\b")
_CARD = re.compile(r"\bcards?\b")
_PERMANENT_NOUN = re.compile(
    r"\b(?:creatures?|permanents?|artifacts?|enchantments?|lands?|"
    r"planeswalkers?|battles?|tokens?|attackers?|blockers?)\b")
_ZONE_OF = {"graveyard": "graveyard", "graveyards": "graveyard",
            "hand": "hand", "hands": "hand", "library": "library",
            "libraries": "library", "exile": "exile"}


@lru_cache(maxsize=None)
def source_zone(object_text: str) -> Optional[str]:
    """The zone a moved object comes from, read from its object span only.

    An explicit "from/in <zone>" names it (two different zones -- a union --
    name none); "exiled" names exile; a spell is on the stack; a card with
    no zone phrase and a pronoun name none (linking binds them); a
    permanent noun is on the battlefield (CR 110.1)."""
    s = object_text.lower().replace("’", "'")
    prep = _ZONE_PREP.search(s)
    if prep:
        # Every zone after the first preposition: "from your hand or
        # graveyard" is a union whose second zone shares the preposition.
        zones = {_ZONE_OF[w] for w in _ZONE_WORD.findall(s, prep.end())}
        if zones:
            return next(iter(zones)) if len(zones) == 1 else None
    if _EXILED.search(s):
        return "exile"
    if _SPELL.search(s) and not _CARD.search(s):
        return "stack"
    if _CARD.search(s):
        return None
    if _PERMANENT_NOUN.search(s):
        return "battlefield"
    return None


def clear_caches() -> None:
    _parse_rel.cache_clear()
    _instead_rel.cache_clear()
    source_zone.cache_clear()
