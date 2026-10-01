"""Destination sub-grammar (design doc 2026-09-29: section 4 MOVE row and
the put/return disambiguation; section 3 L2 instead-of trailer; A9, A13,
A15, A18).

A closed table over L0 output (the leaf contract in
`engine.effect_grammar.sub`). It runs at load, never at resolution, and
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
  action sends the object; it never replaces the action. The override must
  attach to a named action (the "this way" rider, or the caller's link);
  a free-standing CR 614 replacement ("would <event>", "anywhere else") is
  refused.
* ``source_zones`` / ``source_zone`` read a move's source zone(s) from its
  OBJECT span. "return" names no zone of its own, so the zone is never
  inferred from verb plus destination (section 4).

Zones (CR 400.1) are the `CardInstance.zone` strings: battlefield, hand,
graveyard, library, exile, stack. A card put into a hand, graveyard or
library goes to its owner's (CR 400.3), so the printed possessive of those
zones is consumed but not stored; only battlefield control ("under your
control") is a `Destination.controller`.
"""
from __future__ import annotations

import dataclasses
import re
from functools import lru_cache
from typing import FrozenSet, Optional, Tuple

from engine.effect_grammar.sub import (CACHE_SIZE, OWNER_POSSESSIVE,
                                       POSSESSIVE, SlotResult, Span,
                                       unmodelled)
from engine.effect_grammar.sub import payload as _payload
from engine.effect_spec import (Amount, AmountKind, Destination, Ref, RefKind,
                                Stage, Unmodelled)

__all__ = ["LEAF", "DETAIL_CODES", "ZONE_POSSESSIVE", "parse_destination",
           "locate_destination", "parse_instead_of", "source_zone",
           "source_zones", "clear_caches"]

LEAF = "destination"
DETAIL_CODES = frozenset({
    "top_or_bottom", "back", "shuffle_into_non_library", "unconsumed",
    "no_head", "controller", "attached_to", "entry_counters",
    "duplicate_modifier", "instead_of_replacement_effect",
    "instead_of_unlinked", "instead_of_no_replaced_zone",
    "instead_of_no_move_action", "instead_of_no_destination",
    "instead_of_no_object", "instead_of_unconsumed"})
_DEST_OVERRIDE = "dest_override"     # effect_spec.SPEC_FLAGS


# ── Closed vocabulary ──────────────────────────────────────────────────

# Possessives a destination zone may carry: the contract's one possessive
# vocabulary (`sub.POSSESSIVES`; A9: '~'s owner's' is the normalised
# "his/her owner's" on legendary and planeswalker faces), or the
# determiner "the". CR 400.3: a card put into a hand, library or graveyard
# goes to its owner's, whatever the possessive names.
_OWNER_POSS = OWNER_POSSESSIVE
_ZONE_POSS = r"(?:%s|the)" % POSSESSIVE
# The zone-noun possessive the verb lexicon reads for "shuffle <object>
# into <library>" (A18).
ZONE_POSSESSIVE = _ZONE_POSS

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
# "put X back on top of <library>": the adverb 'back' before any zoned head
# belongs to the destination PP (the head names the zone explicitly); only
# a bare 'back' is the zone-it-came-from row above.
_BACK = r"(?:back )?"
_HEAD_RES = tuple(
    (re.compile(r"%s%s(?![\w'])" % ("" if pos == "back" else _BACK, p)), z, pos)
    for p, z, pos in _HEADS)
_ANY_HEAD = re.compile(r"(?<![\w'])(?:%s)(?![\w'])" % "|".join(
    p if pos == "back" else _BACK + p for p, _, pos in _HEADS))

_ORDINALS = {"second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
             "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}

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


def _unmodelled(stage: Stage, lemma: str, code: str, param: str = "") -> Unmodelled:
    return unmodelled(stage, lemma, LEAF, code, DETAIL_CODES, param)


# The entry-counter amounts a Destination can carry: a literal count, X, or
# "that many" (CR 122.1; the count is bound at resolution).
_ENTRY_VARIABLE = frozenset({AmountKind.X, AmountKind.THAT_MUCH})


def _counters(items: str):
    """'a +1/+1 counter and a flying counter' -> ((kind, Amount), ...), or
    None when a token is left over or the counter phrase is not an entry
    count, or payload's own refusal (an `Unmodelled`, propagated unchanged
    by the caller) when payload refuses the phrase. The counter noun phrase
    ('<count> [additional] <kind> counter(s)') is read by payload's counter
    parser, the one count table and kind vocabulary; this only reshapes its
    multiset."""
    r = _payload.parse_counters(items, (0, len(items)))
    if r.unmodelled is not None:
        return r.unmodelled
    spec = r.value
    if (spec is None or r.rest_spans or r.pending or spec.choice
            or _payload.WILDCARD in spec.kinds):
        return None
    if r.amount is not None:
        if r.amount.kind not in _ENTRY_VARIABLE or len(spec.kinds) != 1:
            return None
        return ((spec.kinds[0], r.amount),)
    counts = {}
    for kind in spec.kinds:
        counts[kind] = counts.get(kind, 0) + 1
    return tuple((k, Amount(AmountKind.LITERAL, n=n)) for k, n in counts.items())


# ── The destination PP ─────────────────────────────────────────────────

_DUPLICATE = "duplicate_modifier"


def _parse_modifiers(s: str, pos: int, zone: str, lemma: str, fields: dict):
    """Consume the modifiers after a head. Returns (pos, Unmodelled|None).

    Each modifier field may be set once: a repeated or contradictory
    modifier ("in any order in a random order", two control phrases) is
    UNMODELLED, never last-one-wins."""
    seen = set()

    def take(key):
        if key in seen:
            return False
        seen.add(key)
        return True

    while pos < len(s):
        m = _SEP.match(s, pos)
        if not m:
            break
        p = m.end()
        hit = None
        key = None
        if zone in ("battlefield", "exile"):
            for rx, flag in _BF_FLAGS:
                if zone == "exile" and flag != "face_down":
                    continue           # exile admits face down only (CR 406.3)
                mm = rx.match(s, p)
                if mm and (mm.end() == len(s) or not s[mm.end()].isalnum()):
                    hit, key = mm, flag or "front_face_up"
                    if flag:
                        fields[flag] = True
                    break
        if hit is None and zone == "battlefield":
            mm = _CONTROL_YOU.match(s, p)
            if mm:
                fields["controller"], hit, key = "you", mm, "controller"
            else:
                mm = _CONTROL_OWNER.match(s, p)
                if mm:
                    fields["controller"], hit, key = "owner", mm, "controller"
                elif _CONTROL_OTHER.match(s, p):
                    return pos, _unmodelled(Stage.RECOGNIZED_UNSUPPORTED,
                                            lemma, "controller")
            if hit is None:
                mm = _ATTACHED_SELF.match(s, p)
                if mm:
                    fields["attached_to"], hit, key = Ref(RefKind.SELF), mm, "attached_to"
                elif _ATTACHED_OTHER.match(s, p):
                    return pos, _unmodelled(Stage.REFERENCE, lemma,
                                            "attached_to")
        if hit is None and zone in ("battlefield", "exile"):
            mm = _WITH_COUNTERS.match(s, p)
            if mm:
                counters = _counters(mm.group("items"))
                if counters is None or isinstance(counters, Unmodelled):
                    # payload's refusal propagates unchanged; a typed
                    # counter phrase that is no entry count is this leaf's.
                    return pos, _unmodelled(
                        Stage.AMOUNT, lemma, counters or "entry_counters")
                fields["entry_counters"] = counters
                hit, key = mm, "entry_counters"
        if hit is None and zone == "library":
            mm = _ORDER.match(s, p)
            if mm:
                fields["order"] = "any" if mm.group("o") == "any" else "random"
                hit, key = mm, "order"
            else:
                mm = _NTH.match(s, p)
                if mm:
                    if fields.get("position") is not None and "position" not in seen:
                        break          # the head already placed it
                    fields["position"] = "nth"
                    fields["nth"] = _ORDINALS[mm.group("o")]
                    hit, key = mm, "position"
        if hit is None:
            break
        if not take(key):
            return pos, _unmodelled(Stage.CLAUSE, lemma, _DUPLICATE)
        pos = hit.end()
    return pos, None


@lru_cache(maxsize=CACHE_SIZE)
def _parse_rel(s: str, lemma: str):
    """(Destination|None, Unmodelled|None) for a trimmed slot string."""
    for rx, zone, position in _HEAD_RES:
        m = rx.match(s)
        if not m:
            continue
        if zone is None:
            return None, _unmodelled(Stage.RECOGNIZED_UNSUPPORTED, lemma,
                                     position)
        if lemma == "shuffle" and not (zone == "library" and position is None):
            return None, _unmodelled(Stage.CLAUSE, lemma,
                                     "shuffle_into_non_library")
        fields = {"position": position}
        pos, bad = _parse_modifiers(s, m.end(), zone, lemma, fields)
        if bad is not None:
            return None, bad
        if pos != len(s):
            return None, _unmodelled(Stage.CLAUSE, lemma, "unconsumed",
                                     s[pos:])
        if lemma == "shuffle":
            fields["position"] = "shuffle"       # A18
        return Destination(zone, **fields), None
    return None, _unmodelled(Stage.CLAUSE, lemma, "no_head")


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
    value, bad = _parse_rel(text[start:end], lemma)
    return SlotResult(value=value, span=(start, end), unmodelled=bad)


def locate_destination(text: str, span: Span, *, lemma: str = "") -> Optional[Span]:
    """The span of the destination PP inside ``text[span]``, running to the
    slot end: the first head whose phrase parses fully, else the LAST head
    (the destination PP ends the slot, so an earlier head is a zone phrase
    inside the object; the last one's UNMODELLED result is the one to
    report), else None."""
    start, end = _trim(text, span)
    s = text[:end]
    last = None
    for m in _ANY_HEAD.finditer(s, start, end):
        cand = (m.start(), end)
        last = cand
        value, _ = _parse_rel(s[m.start():end], lemma)
        if value is not None:
            return cand
    return last


# ── The instead-of override (A15) ──────────────────────────────────────

_THIS_WAY_RIDER = re.compile(
    r"if [^,]+? (?:is|are|was|were|would be) [a-z]+ this way, ")
# CR 614.1a: "if <x> would <event>, ... instead" (and the discard form "if
# <x> causes you to discard <y>, ... instead") is a replacement effect of
# its own, not an override of a named action.
_WOULD_FRAME = re.compile(
    r"if [^,]+? (?:would (?![a-z]+ this way)|causes you to discard )[^,]+, ")
_INSTEAD_OF = re.compile(
    r" instead of (?:putting (?:it|them|that card|that spell|~) )?"
    r"(?:into (?:%s )?(?P<zone>graveyard|hand|library|exile)s?|"
    r"(?P<anywhere>anywhere else))(?: as it resolves)?$" % _ZONE_POSS)
_MOVE_VERB = re.compile(r"(?P<verb>exile|put|return|shuffle)s? ")
_EXILE_MOD = re.compile(r" (?=face down(?: |$)|with )")
_REPLACEMENT = "instead_of_replacement_effect"
_UNLINKED = "instead_of_unlinked"


def _exile_body(s: str, body_start: int, body_end: int, lemma: str):
    """(object span, Destination|None, Unmodelled|None) for 'exile <object>
    [<modifiers>]'. The modifiers after the object run through the same
    loop as a destination PP and must reach the body end."""
    body = s[:body_end]
    for m in _EXILE_MOD.finditer(body, body_start):
        fields = {}
        pos, bad = _parse_modifiers(body, m.start(), "exile", lemma, fields)
        if bad is not None:
            return (body_start, m.start()), None, bad
        if pos == body_end and pos > m.start():
            return (body_start, m.start()), Destination("exile", **fields), None
        if pos > m.start():
            return (body_start, m.start()), None, _unmodelled(
                Stage.CLAUSE, lemma, "instead_of_unconsumed", body[pos:])
    return (body_start, body_end), Destination("exile"), None


@lru_cache(maxsize=CACHE_SIZE)
def _instead_rel(s: str, linked: bool, lemma: str):
    """(Destination|None, Unmodelled|None, object span|None) relative to s."""
    pos = 0
    if _WOULD_FRAME.match(s):
        return None, _unmodelled(Stage.RECOGNIZED_UNSUPPORTED, lemma, _REPLACEMENT), None
    m = _THIS_WAY_RIDER.match(s)
    if m:
        pos = m.end()
    tail = _INSTEAD_OF.search(s, pos)
    if tail is None:
        return None, _unmodelled(Stage.CLAUSE, lemma, "instead_of_no_replaced_zone"), None
    if tail.group("anywhere"):
        # "instead of putting it anywhere else" overrides no named action's
        # destination: it is a leave-the-battlefield replacement (CR 614).
        return None, _unmodelled(Stage.RECOGNIZED_UNSUPPORTED, lemma, _REPLACEMENT), None
    if m is None and not linked:
        # No rider and no caller link: the leaf cannot see which action the
        # move overrides (a leading 'if <cond>, ... instead' sibling is the
        # clause linker's call, G9), so it sets no override.
        return None, _unmodelled(Stage.CLAUSE, lemma, _UNLINKED), None
    replaced = tail.group("zone")
    vm = _MOVE_VERB.match(s, pos)
    if vm is None:
        return None, _unmodelled(Stage.CLAUSE, lemma, "instead_of_no_move_action"), None
    verb = vm.group("verb")
    body_start, body_end = vm.end(), tail.start()
    if verb == "exile":
        obj, dest, bad = _exile_body(s, body_start, body_end, lemma)
        if bad is not None:
            return None, bad, obj
    else:
        sub_lemma = "shuffle" if verb == "shuffle" else ""
        where = locate_destination(s, (body_start, body_end), lemma=sub_lemma)
        if where is None:
            return None, _unmodelled(Stage.CLAUSE, lemma, "instead_of_no_destination"), None
        obj = (body_start, where[0])
        dest, bad = _parse_rel(s[where[0]:where[1]].rstrip(), sub_lemma)
        if bad is not None:
            return None, dataclasses.replace(bad, lemma=lemma), obj
    obj = _trim(s, obj)
    if obj[0] == obj[1]:
        return None, _unmodelled(Stage.CLAUSE, lemma, "instead_of_no_object"), None
    return dataclasses.replace(dest, instead_of=replaced), None, obj


def parse_instead_of(text: str, span: Span, *, linked: bool = False,
                     lemma: str = "") -> SlotResult:
    """Type "<move VP> instead of putting it into <zone>" as a destination
    override of the named action: ``Destination(zone, instead_of=<replaced
    zone>)`` flagged ``dest_override`` (A15, CR 701.5a).

    The override must attach to a named action: the slot is led by the
    "If <ref> is <verb>ed this way," rider, or the caller passes
    ``linked=True`` because it has already bound the move to the named
    spec. A "would <event>" frame or "instead of putting it anywhere else"
    is a CR 614 replacement effect, refused as
    ``instead_of_replacement_effect``; an unlinked slice without either is
    refused as ``instead_of_unlinked``. Neither carries the flag.

    ``lemma`` is the caller's printed lemma of the overriding move."""
    start, end = _trim(text, span)
    value, bad, obj = _instead_rel(text[start:end], linked, lemma)
    return SlotResult(
        value=value, span=(start, end), unmodelled=bad,
        flags=frozenset({_DEST_OVERRIDE}) if value is not None else frozenset(),
        object_span=None if obj is None else (start + obj[0], start + obj[1]))


# ── return: the source zone comes from the object span ─────────────────

_ZONE_NOUN = r"(?:graveyards?|hands?|librar(?:y|ies)|exile)"
_ZONE_OF = {"graveyard": "graveyard", "graveyards": "graveyard",
            "hand": "hand", "hands": "hand", "library": "library",
            "libraries": "library", "exile": "exile", "play": "battlefield"}
# A zone NP after "from/in" (a determiner or possessive of up to three
# words, e.g. "an opponent's", "target player's"), or a possessive "of
# <poss> <zone>" ("the top three cards of your library"). Coordinated zone
# nouns share the preposition: "from your hand or graveyard".
_ZONE_NP = (r"(?:[\w'~]+ ){0,3}?(?P<z>%s)"
            r"(?P<more>(?:(?:,| or| and| and/or) (?:[\w'~]+ ){0,3}?%s)*)"
            % (_ZONE_NOUN, _ZONE_NOUN))
_OF_POSS = (r"(?:(?:target|that|each|an?|any|chosen) )?"
            r"(?:your|their|its|his|her|the|[\w~]+'s?|[\w~]+s')")
_ZONE_PP = re.compile(r"\b(?:(?:from|in) %s|in (?P<play>play)|of %s %s)(?![\w'])" % (
    _ZONE_NP, _OF_POSS, r"(?P<z2>%s)" % _ZONE_NOUN))
_BARE_ZONE = re.compile(r"(?:%s )?%s(?:(?:,| or| and| and/or) (?:%s )?%s)*" % (
    _ZONE_POSS, _ZONE_NOUN, _ZONE_POSS, _ZONE_NOUN))
_ZONE_WORD = re.compile(r"\b(%s)\b" % _ZONE_NOUN)
# "exiled" and "suspended" (a suspended card is in exile, CR 702.62a).
_EXILED = re.compile(r"\b(?:exiled|suspended)\b")
# A qualifier after a head noun ("with mana value ... lands you control",
# "that shares a creature type with enchanted creature") names other objects;
# its nouns are not the moved object's. It runs to the object's own "from"
# PP or to the next coordinated noun phrase.
_QUALIFIER = re.compile(
    r"\b(?:with|that|whose|where|equal to|less than|greater than|among|"
    r"shares?|named)\b.*?(?= from | (?:or|and|and/or) (?:target|an?|each|all|"
    r"another|up to)\b|$)")
_WORD = re.compile(r"[a-z~/'-]+")
_SPELL_HEADS = frozenset({"spell", "spells"})
_CARD_HEADS = frozenset({"card", "cards"})
_PERMANENT_HEADS = frozenset({
    "creature", "creatures", "permanent", "permanents", "artifact",
    "artifacts", "enchantment", "enchantments", "land", "lands",
    "planeswalker", "planeswalkers", "battle", "battles", "token", "tokens",
    "attacker", "attackers", "blocker", "blockers"})
_COORD = frozenset({"or", "and", "and/or"})
# Function words never part of a type chain before 'card'/'spell'.
_CHAIN_STOP = frozenset({
    "target", "a", "an", "the", "each", "all", "another", "any", "up", "to",
    "of", "from", "in", "you", "your", "their", "its", "that", "this",
    "those", "these", "with", "number", "other"}) | _SPELL_HEADS | _CARD_HEADS


def _is_type_word(w: str) -> bool:
    """A word that can modify a card/spell noun: a card type, subtype,
    supertype, colour or 'non-' word ("vehicle", "legendary", "nonland").
    A participle ("suspended", "exiled") is not: it qualifies a noun
    phrase of its own."""
    return w not in _CHAIN_STOP and w not in _COORD and not w.endswith("ed")


_NOUN_HEADS = _SPELL_HEADS | _CARD_HEADS | _PERMANENT_HEADS


def _mask_qualifiers(s: str) -> str:
    """Blank every qualifier phrase. 'that' opens one only after a noun
    head ("a creature card that shares ..."); as a determiner ("that
    creature", "that player's graveyard") it is part of the object."""
    out, pos = [], 0
    while True:
        m = _QUALIFIER.search(s, pos)
        if m is None:
            break
        if m.group(0).startswith("that"):
            before = _WORD.findall(s[:m.start()])
            if not before or before[-1] not in _NOUN_HEADS:
                out.append(s[pos:m.start() + len("that")])
                pos = m.start() + len("that")
                continue
        out.append(s[pos:m.start()] + " ")
        pos = m.end()
    out.append(s[pos:])
    return "".join(out)


def _modifies_a_card_or_spell(words, i: int) -> bool:
    """True when words[i] is a type word inside a chain that ends in a type
    word directly before 'card'/'spell' ("artifact or creature card"). In
    "creature or spell" the chain ends in a conjunction, so 'creature' is a
    head noun of its own."""
    j = i + 1
    while j < len(words) and (words[j] in _COORD or _is_type_word(words[j])):
        j += 1
    return (j < len(words) and words[j] in _SPELL_HEADS | _CARD_HEADS
            and _is_type_word(words[j - 1]))


def _participle_in_exile(words, i: int) -> bool:
    """'the exiled creature' / 'each creature exiled with ~' is in exile."""
    return any(0 <= j < len(words) and _EXILED.fullmatch(words[j])
               for j in (i - 1, i + 1))


@lru_cache(maxsize=CACHE_SIZE)
def source_zones(object_text: str) -> FrozenSet[str]:
    """Every zone the moved object names, read from its object span only.

    An explicit "from/in <zone>" (or "of <library>") names its zones;
    "exiled" names exile; a spell head is on the stack; a permanent head is
    on the battlefield (CR 110.1) unless it is a type word modifying a card
    or spell. A bare zone object ("your graveyard") is that zone. A card
    with no zone phrase and a pronoun name none (empty set: linking binds
    them). More than one zone is a union (A21 ``target.zone_union``)."""
    s = object_text.strip()
    zones = set()
    if _BARE_ZONE.fullmatch(s):
        return frozenset(_ZONE_OF[w] for w in _ZONE_WORD.findall(s))
    exiled = bool(_EXILED.search(s))
    s = _mask_qualifiers(s)
    for m in _ZONE_PP.finditer(s):
        if m.group("play"):
            zones.add("battlefield")
        elif m.group("z2"):
            zones.add(_ZONE_OF[m.group("z2")])
        else:
            zones.add(_ZONE_OF[m.group("z")])
            zones.update(_ZONE_OF[w] for w in _ZONE_WORD.findall(m.group("more") or ""))
    if exiled:
        zones.add("exile")
    words = _WORD.findall(s)
    has_card = False
    for i, w in enumerate(words):
        if w in _SPELL_HEADS:
            zones.add("stack")
        elif w in _CARD_HEADS:
            has_card = True
        elif w in _PERMANENT_HEADS and not _modifies_a_card_or_spell(words, i) \
                and not _participle_in_exile(words, i):
            zones.add("battlefield")
    if has_card and not zones:
        return frozenset()
    return frozenset(zones)


def source_zone(object_text: str) -> Optional[str]:
    """The single zone a moved object comes from, or None when it names
    none (a pronoun or a zoneless card: linking binds it) or more than one
    (a union)."""
    zones = source_zones(object_text)
    return next(iter(zones)) if len(zones) == 1 else None


def clear_caches() -> None:
    _parse_rel.cache_clear()
    _instead_rel.cache_clear()
    source_zones.cache_clear()
