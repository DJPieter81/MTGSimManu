"""Quantity sub-grammar (design doc 2026-09-29, section 2 ``Quantity``,
section 6 "Quantity"; A16, A27).

Types the number an amount counts or reads, once, at LOAD (never at
resolution), over L0 output, under the one leaf contract in
`engine.effect_grammar.sub` (``(host, span, *, lemma="")``, one
`SlotResult`, host-absolute spans). The slot is the quantity phrase the
amount sub-grammar hands on:

* the counted object of "for each <Q>" ("artifact you control");
* the operand of "equal to <Q>" / "where x is <Q>" ("the number of cards in
  your hand", "its power", "your devotion to black").

"for each <Q>" counts the same set as "the number of <Q>", so both forms
yield one value. Amount operators ("twice", "1 plus", "half") and the
leading-"for each" iteration test (A16) are the amount sub-grammar's; a
slot that opens with an element anaphor ("of those creatures") is refused
here so the census sees it.

**Closed table** (`QuantityKind`):

* COUNT -- objects on the battlefield or the stack, typed by the filter
  leaf ("creatures you control"), or a player set ("opponent");
* CARDS_IN -- cards in a hand, graveyard, library or exile; ``player`` is
  the filter's owner;
* RESULT_SIZE -- a filtered result of an earlier spec ("nonland card
  discarded this way"); ``ref`` is an unbound RESULT the linker indexes;
* POWER / TOUGHNESS / MANA_VALUE -- of a reference ("its power", "the mana
  value of the exiled card");
* COUNTERS_ON -- counters of one kind (or any kind) on a reference or a
  filtered set, the kind read through payload's one counter noun-phrase
  parser (CR 122);
* LIFE_TOTAL (CR 119), DEVOTION (CR 700.5; ``stat`` holds the mana
  letters), BASIC_LAND_TYPES (CR 305.6), COLORS_SPENT (CR 601.2h),
  CARD_TYPES_IN_GRAVEYARD (CR 205.2a), GREATEST and TOTAL of a
  characteristic over a set, TIMES_KICKED (CR 702.33);
* HISTORY -- a tally of what happened this turn, ``event`` from the closed
  `HISTORY_EVENTS`. "this turn" closing a quantity is history and the
  quantity consumes it; it is never left as a duration (section 6).
  ``player`` is the actor of the event (who cast, drew, discarded, lost
  life) and "any" for an event no player performs; who controlled the
  object that died or entered is the filter's ``controller`` whichever
  word order prints it ("<noun> you control that died" = "<noun> that
  died under your control"). The filter describes the object at the
  event, so it names no current zone: a spell cast this turn has
  resolved, a discarded card may since have been exiled.

**Sets.** Every kind over a set (COUNT, RESULT_SIZE, COUNTERS_ON, GREATEST,
TOTAL, BASIC_LAND_TYPES) has ``player="any"``: who controls the set is the
filter's. A set that is an earlier spec's result ("... this way") carries
``ref=Ref(RESULT)`` whatever the kind reads of it, for the linker to bind.

**References.** "~" is ``Ref(SELF)``; "enchanted / equipped <noun>" is
``Ref(ATTACHED)``; pronouns, "that <noun>", "the <participle> <noun>" and
"target ..." are left to the linker in ``pending`` as ``("ref", text)``
(CR 608.2b), and anaphoric players as ``("player", text)``. A27 / CR
608.2h: when the caller says the source left as part of the cost
(``source_left``), the SELF reference reads last-known information
(``lki=True``); a sacrificed referent -- "the sacrificed <noun>" or a
set "<noun>s sacrificed this way" -- carries the `LKI` flag.

**Spans.** The slot is read whole first. Only when it is no quantity is it
cut before a closed tail word ("to", "plus", "rather than", a printed
duration from `duration.DURATION_START`, ...); the longest prefix that is a
quantity is consumed and the tail is handed on as ``rest_spans``. A phrase
no row places is ``UNMODELLED(QUANTITY)`` over the whole slot with detail
``quantity.<code>[:<param>]`` -- never zero, never a broader count.

The leaf reads no card name and no game state.
"""
from __future__ import annotations

import dataclasses
import re
from functools import lru_cache
from typing import Optional, Tuple

from engine.effect_grammar.sub import (CACHE_SIZE, SELF_NOUNS, SlotResult,
                                       Span, rest_spans_after, unmodelled)
from engine.effect_grammar.sub import filter as _filter
from engine.effect_grammar.sub import payload as _payload
from engine.effect_grammar.sub.duration import DURATION_START
from engine.effect_spec import (Quantity, QuantityKind, Ref, RefKind,
                                Stage, Unmodelled)

__all__ = ["LEAF", "DETAIL_CODES", "HISTORY_EVENTS", "LKI", "STATS",
           "parse_quantity", "clear_caches"]

LEAF = "quantity"
DETAIL_CODES = frozenset({
    "empty", "filter", "reference", "determiner", "history_control",
    "element_anaphor", "anaphoric_number", "stat_pair", "party",
    "colors_among", "mana_spent", "mana_cost", "mana_symbols",
    "starting_life_total", "greatest_number", "extremum", "counter",
    "counter_kinds", "result_amount", "damage_amount", "different_values",
    "card_types_zone", "coin_flip", "player_counters",
    "zone_controller"})

LKI = "lki"        # SlotResult flag: the referent left as part of the cost

# HISTORY tallies (section 6): what happened this turn.
HISTORY_EVENTS = frozenset({
    "cast", "drawn", "discarded", "sacrificed", "died", "entered",
    "life_gained", "life_lost", "players_lost_life", "descended",
    "attacked"})

# Characteristics a quantity reads (CR 208, 202.3).
STATS = {"power": QuantityKind.POWER, "toughness": QuantityKind.TOUGHNESS,
         "mana value": QuantityKind.MANA_VALUE}
_STAT = r"(?P<stat>power|toughness|mana value)"

_COLORS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
_WUBRG = "WUBRG"

# Zones whose objects are cards a player owns (CARDS_IN).
_CARD_ZONES = frozenset({"hand", "graveyard", "library", "exile"})


def _um(code: str, lemma: str, param: str = "") -> Unmodelled:
    return unmodelled(Stage.QUANTITY, lemma, LEAF, code, DETAIL_CODES, param)


# ── References (CR 608.2b) ─────────────────────────────────────────────

# Object nouns a reference names: the L0 self nouns plus the card forms.
_NOUNS = sorted(set(SELF_NOUNS) | {
    "creature card", "artifact card", "land card", "permanent card",
    "enchantment card", "instant card", "sorcery card", "army", "object",
    "source"}, key=len, reverse=True)
_NOUN = r"(?:%s)" % "|".join(re.escape(n) for n in _NOUNS)
# Past participles naming an object an earlier action of the ability
# handled ("the sacrificed creature", "the exiled card").
_PARTICIPLES = r"(?:sacrificed|exiled|discarded|revealed|returned|destroyed" \
               r"|chosen|milled|copied|countered|targeted|amassed|tapped" \
               r"|attacking|blocking|blocked)"
# A referent that left the battlefield as part of the cost or the effect,
# read as it last existed (CR 608.2h), whether one object ("the sacrificed
# creature") or a result set ("creatures sacrificed this way").
_LEFT_AS_COST = frozenset({"sacrificed"})
_REF_RE = re.compile(
    r"(?P<self>~)"
    r"|(?P<att>enchanted|equipped|fortified) (?P<att_noun>%s)"
    r"|(?P<pron>it|its|them|their|his|her)"
    r"|(?P<that>(?:that|the)(?: (?P<part>%s))? %s)"
    r"|(?P<this_way>the %s (?P<part2>%s) this way)"
    r"|(?P<target>target [a-z][a-z' \-/+]*)"
    % (_NOUN, _PARTICIPLES, _NOUN, _NOUN, _PARTICIPLES))


def _ref(text: str, source_left: bool):
    """(ref, pending, flags) of an object reference, or None outside the
    closed reference table."""
    m = _REF_RE.fullmatch(text)
    if m is None:
        return None
    if m.group("self"):
        return Ref(RefKind.SELF, lki=source_left), (), frozenset()
    if m.group("att"):
        return Ref(RefKind.ATTACHED, noun=m.group("att_noun")), (), frozenset()
    part = m.group("part") or m.group("part2")
    flags = frozenset({LKI}) if part in _LEFT_AS_COST else frozenset()
    return None, (("ref", text),), flags


def _possessed(t: str, source_left: bool, tail: str):
    """The reference of ``<possessor> <tail>`` where the possessor is a
    possessive pronoun or ``<ref>'s``: (ref, pending, flags), None when
    the phrase is not of that shape, or the string "reference" when the
    possessor is outside the reference table."""
    if not t.endswith(" " + tail):
        return None
    head = t[:-len(tail) - 1]
    if head in ("its", "their", "his", "her"):
        return None, (("ref", head),), frozenset()
    if head.endswith("'s"):
        r = _ref(head[:-2], source_left)
        return "reference" if r is None else r
    return None


# ── Players ────────────────────────────────────────────────────────────

_WHO = (r"you|they|your opponents|opponents|an opponent|each opponent"
        r"|target opponent|target player|that player|that opponent|a player"
        r"|each player|its controller|its owner|defending player")
_OPPONENTS = frozenset({"your opponents", "opponents", "an opponent",
                        "each opponent", "an opponent's", "your opponents'",
                        "opponents'"})


def _player(who: Optional[str]):
    """(player, pending) of a printed player; an anaphor is the linker's."""
    if not who:
        return "any", ()
    if who in ("you", "your"):
        return "you", ()
    if who in _OPPONENTS:
        return "opponents", ()
    if who in ("a player", "each player"):
        return "any", ()
    if who.startswith("target "):
        return Ref(RefKind.TARGET, noun=who.split()[1].rstrip("'s")), ()
    if who.endswith("'s"):
        who = who[:-2]
    return "any", (("player", who),)


# ── Refusals: printed quantities the model cannot state ────────────────

# (code, pattern searched in the slot). Checked before the positive rows
# so a named shape is reported as itself, not as a filter failure.
_REFUSALS = tuple((code, re.compile(p)) for code, p in (
    ("element_anaphor", r"^of\b"),
    ("anaphoric_number", r"^(?:the difference|that number|the chosen number"
                         r"|that much|the result)\b"),
    ("party", r"\bin (?:your|their|a) party\b"),
    ("colors_among", r"^(?:the number of )?colou?rs? (?:among|of)\b(?! mana spent)"),
    ("mana_symbols", r"\bmana symbols?\b"),
    ("mana_spent", r"^(?:the )?(?:total )?(?:amount of )?(?:mana|(?:\{[a-z]\})+"
                   r"(?: or (?:\{[a-z]\})+)?) (?:you )?spent\b"
                   r"|^(?:the )?amount of mana\b|^mana from\b"),
    # "coin" is also a counter kind (CR 122.1): "coin counters" is no flip.
    ("coin_flip", r"^(?:the number of )?(?:flips?|coins?)\b(?! counters?\b)"),
    ("player_counters", r"^(?:the number of )?poison counters? (?:%s) ha(?:ve|s)$"
                        % _WHO),
    ("starting_life_total", r"\bstarting life total\b"),
    ("greatest_number", r"^the (?:greatest|highest|least|lowest|most|fewest)"
                        r" number\b"),
    # "the highest <stat> among <set>" is GREATEST; every other extremum
    # (a lowest, a highest life total) has no kind.
    ("extremum", r"^the (?:lowest|least|smallest|largest)\b"
                 r"|^the highest\b(?! (?:power|toughness|mana value) among\b)"),
    ("stat_pair", r"\bpower and toughness\b|\bpower plus toughness\b"),
    ("counter_kinds", r"\bkinds? of counters?\b"),
    ("result_amount", r"^(?:the |that )?(?:total )?(?:amount of )?(?:1 )?"
                      r"(?:excess damage|damage|life|\{e\}|mana|counters?)(?!\w)"
                      r"[^,;]* this way$|^(?:the |that )?(?:amount of )?"
                      r"excess damage\b|\bcounters? removed this way$"),
    ("damage_amount", r"^(?:the )?(?:total )?(?:amount of )?damage\b"),
    ("different_values", r"^(?:the number of )?(?:differently|different)\b"),
))
_MANA_COST_RE = re.compile(r"\bmana costs?$")


# ── History (section 6) ────────────────────────────────────────────────

_HAVE = r"(?:'ve| have| has)?"
_UNDER = (r"(?: under (?P<under>your|an opponent's|your opponents'|their"
          r"|its owner's|its controller's) control)?")
# "under <possessor> control" as the filter's controller (value, anaphor).
_UNDER_CONTROLLER = {"your": ("you", None), "an opponent's": ("opponents", None),
                     "your opponents'": ("opponents", None),
                     "their": ("any", "their"),
                     "its owner's": ("any", "its owner's"),
                     "its controller's": ("any", "its controller's")}
# (event, pattern, default zone of the counted object). np is the counted
# object (a filter phrase), who the actor, under the controller.
_HISTORY = tuple((event, re.compile(p), zone) for event, p, zone in (
    ("players_lost_life",
     r"(?P<players>opponents?|players?) who lost life this turn", None),
    ("life_lost", r"(?:the )?(?:total )?(?:amount of )?(?:1 )?life "
                  r"(?P<who>%s)%s lost this turn" % (_WHO, _HAVE), None),
    ("life_gained", r"(?:the )?(?:total )?(?:amount of )?(?:1 )?life "
                    r"(?P<who>%s)%s gained this turn" % (_WHO, _HAVE), None),
    ("cast", r"(?P<np>.+?) (?:(?P<who>%s)%s )?cast this turn" % (_WHO, _HAVE),
     "stack"),
    ("drawn", r"(?P<np>.+?) (?:(?P<who>%s)%s )?drawn this turn" % (_WHO, _HAVE),
     "hand"),
    # CR 702.29a: cycling's cost discards the card, so a cycled card is a
    # discarded card and "cycled or discarded" is the discard tally.
    ("discarded", r"(?P<np>.+?) (?:(?P<who>%s)%s )?(?:cycled or )?discarded "
                  r"this turn" % (_WHO, _HAVE), "graveyard"),
    ("sacrificed", r"(?P<np>.+?) (?:(?P<who>%s)%s )?sacrificed this turn"
                   % (_WHO, _HAVE), ""),
    ("died", r"(?P<np>.+?) that died%s this turn" % _UNDER, ""),
    ("entered", r"(?P<np>.+?) that entered(?: the battlefield)?%s this turn"
                % _UNDER, ""),
    ("descended", r"times? (?P<who>you)%s descended this turn" % _HAVE, None),
    ("attacked", r"times? (?P<ref>it|~) (?:has )?attacked this turn", None),
))
_THE_NUMBER_OF = re.compile(r"^the (?:total )?number of ")


def _history(t: str, source_left: bool):
    """A HISTORY quantity of the whole of ``t`` as a _Whole, or None."""
    body = _THE_NUMBER_OF.sub("", t)
    for event, rx, zone in _HISTORY:
        m = rx.fullmatch(body)
        if m is None:
            continue
        gd = m.groupdict()
        pending: Tuple[Tuple[str, str], ...] = ()
        if gd.get("players"):
            who = "opponents" if gd["players"].startswith("opponent") else "any"
            return (Quantity(QuantityKind.HISTORY, player=who, event=event,
                             raw=t), None, (), frozenset())
        player, pending = _player(gd.get("who"))
        ref = None
        if gd.get("ref"):
            ref, rp, _ = _ref(gd["ref"], source_left)
            pending += rp
        filt = None
        if gd.get("np"):
            f = _filter.parse_filter(gd["np"], zone=zone or "")
            if f.value is None:
                return _fail("filter", _filter_code(f))
            if f.amount is not None or f.flags:
                return _fail("determiner")
            filt = dataclasses.replace(f.value, zone="")
            pending = f.pending + pending
            if gd.get("under"):
                if filt.controller != "any" or any(
                        k == "controller" for k, _ in f.pending):
                    return _fail("history_control")
                controller, anaphor = _UNDER_CONTROLLER[gd["under"]]
                filt = dataclasses.replace(filt, controller=controller)
                if anaphor:
                    pending = pending + (("controller", anaphor),)
        return (Quantity(QuantityKind.HISTORY, filter=filt, player=player,
                         ref=ref, event=event, raw=t), None, pending,
                frozenset())
    return None


# ── The closed rows ────────────────────────────────────────────────────

# A whole-slot parse: (value, (code, param) | None, pending, flags).
_Whole = Tuple[Optional[Quantity], Optional[Tuple[str, str]],
               Tuple[Tuple[str, str], ...], frozenset]


def _filter_code(f: SlotResult) -> str:
    """The filter leaf's detail code of a refused set (the census groups a
    quantity the filter refused by it)."""
    return f.unmodelled.detail.split(".", 1)[1].split(":")[0]


def _fail(code: str, param: str = "") -> _Whole:
    return (None, (code, param), (), frozenset())


def _set(text: str, zone: str = ""):
    """(filter, ref, pending, flags, failure) of a set phrase through the
    filter leaf; a determiner on the set is no quantity. The one place a
    set's result binding and last-known information are decided: a set
    that is an earlier spec's result ("... this way") is ``Ref(RESULT)``,
    and one that left as a cost or an effect (CR 608.2h) carries `LKI`."""
    f = _filter.parse_filter(text, zone=zone)
    if f.value is None:
        return None, None, (), frozenset(), ("filter", _filter_code(f))
    if f.amount is not None or f.flags:
        return None, None, (), frozenset(), ("determiner", "")
    results = [v for k, v in f.pending if k == "result"]
    ref = Ref(RefKind.RESULT) if results else None
    flags = frozenset({LKI}) if any(v in _LEFT_AS_COST for v in results) \
        else frozenset()
    return f.value, ref, f.pending, flags, None


_LIFE_RE = re.compile(r"(?P<who>your|.+?'s) life total")
_DEVOTION_RE = re.compile(
    r"your devotion to (?P<cols>(?:white|blue|black|red|green)"
    r"(?:(?:,? and |, )(?:white|blue|black|red|green))*)")
_COLORS_SPENT_RE = re.compile(
    r"(?:the number of )?colou?rs? of mana spent to cast (?P<ref>.+)")
_BASIC_TYPES_RE = re.compile(r"(?:the number of )?basic land types? among (?P<f>.+)")
_CARD_TYPES_RE = re.compile(r"(?:the number of )?card types? among (?P<f>.+)")
_GREATEST_RE = re.compile(r"the (?:greatest|highest) %s among (?P<f>.+)" % _STAT)
_TOTAL_OF_RE = re.compile(r"the total %s of (?P<x>.+)" % _STAT)
_STAT_OF_RE = re.compile(r"the %s of (?P<x>.+)" % _STAT)
_KICKED_RE = re.compile(
    r"(?:the number of )?times? (?P<ref>~|it|that spell) (?:was|were) kicked")
_COUNTERS_RE = re.compile(r"(?:the (?:total )?number of )?(?P<np>.+?) on (?P<on>.+)")
_PLAYERS_RE = re.compile(
    r"(?:the number of )?(?P<p>opponents?|players?)(?: you have)?")


def _counted(t: str) -> _Whole:
    """COUNT / CARDS_IN / RESULT_SIZE of a counted set ("[the number of]
    <filter>")."""
    body = _THE_NUMBER_OF.sub("", t)
    filt, ref, pending, flags, failure = _set(body)
    if failure is not None:
        return _fail(*failure)
    if ref is not None:
        return (Quantity(QuantityKind.RESULT_SIZE, filter=filt, ref=ref,
                         player="any", raw=t), None, pending, flags)
    if filt.zone in _CARD_ZONES and filt.controller != "any":
        # CR 108.4a: a card outside the battlefield and the stack has no
        # controller, so "<permanents> you control from your hand" is no
        # set; a tail cut may still find the count before "from".
        return _fail("zone_controller")
    if filt.zone in _CARD_ZONES:
        return (Quantity(QuantityKind.CARDS_IN, filter=filt,
                         player=filt.owner, raw=t), None, pending, frozenset())
    return (Quantity(QuantityKind.COUNT, filter=filt, player="any", raw=t),
            None, pending, frozenset())


def _counters(t: str, source_left: bool) -> Optional[_Whole]:
    m = _COUNTERS_RE.fullmatch(t)
    if m is None:
        return None
    np, on = m.group("np"), m.group("on")
    c = _payload.parse_counters(np, (0, len(np)))
    if c.value is None or c.rest_spans or c.span != (0, len(np)):
        return None                      # not a counter noun phrase
    if c.value.choice or len(c.value.kinds) != 1 or c.amount is not None \
            or c.pending:
        return _fail("counter")
    kind = c.value.kinds[0]
    counter_kind = None if kind == _payload.WILDCARD else kind
    r = _ref(on, source_left)
    if r is not None:
        ref, pending, flags = r
        return (Quantity(QuantityKind.COUNTERS_ON, ref=ref,
                         counter_kind=counter_kind, raw=t), None, pending, flags)
    filt, ref, pending, flags, failure = _set(on)
    if failure is not None:
        return _fail(*failure)
    return (Quantity(QuantityKind.COUNTERS_ON, filter=filt, ref=ref,
                     player="any", counter_kind=counter_kind, raw=t), None,
            pending, flags)


def _stat_of_ref(t: str, source_left: bool) -> Optional[_Whole]:
    for stat, kind in STATS.items():
        r = _possessed(t, source_left, stat)
        if r == "reference":
            return _fail("reference", t.split("'")[0])
        if r is not None:
            ref, pending, flags = r
            return (Quantity(kind, ref=ref, stat=stat, raw=t), None, pending,
                    flags)
    m = _STAT_OF_RE.fullmatch(t)
    if m is not None:
        r = _ref(m.group("x"), source_left)
        if r is None:
            return _fail("reference", m.group("x"))
        ref, pending, flags = r
        stat = m.group("stat")
        return (Quantity(STATS[stat], ref=ref, stat=stat, raw=t), None,
                pending, flags)
    return None


def _total(t: str, source_left: bool) -> Optional[_Whole]:
    for stat in STATS:
        r = _possessed(t, source_left, "total " + stat)
        if r == "reference":
            return _fail("reference", t.split("'")[0])
        if r is not None:
            ref, pending, flags = r
            return (Quantity(QuantityKind.TOTAL, ref=ref, stat=stat, raw=t),
                    None, pending, flags)
    m = _TOTAL_OF_RE.fullmatch(t)
    if m is None:
        return None
    stat, x = m.group("stat"), m.group("x")
    r = _ref(x, source_left)
    if r is not None:
        ref, pending, flags = r
        return (Quantity(QuantityKind.TOTAL, ref=ref, stat=stat, raw=t), None,
                pending, flags)
    filt, ref, pending, flags, failure = _set(x)
    if failure is not None:
        return _fail(*failure)
    return (Quantity(QuantityKind.TOTAL, filter=filt, ref=ref, player="any",
                     stat=stat, raw=t), None, pending, flags)


def _whole(t: str, source_left: bool) -> _Whole:
    """The quantity of the whole of ``t``, or the failure of the row that
    owns its shape."""
    if not t:
        return _fail("empty")
    for code, rx in _REFUSALS:
        if rx.search(t):
            return _fail(code)
    hist = _history(t, source_left)
    if hist is not None:
        return hist
    counters = _counters(t, source_left)
    if counters is not None:
        return counters
    if _MANA_COST_RE.search(t):
        return _fail("mana_cost")
    stat = _stat_of_ref(t, source_left)
    if stat is not None:
        return stat
    total = _total(t, source_left)
    if total is not None:
        return total
    m = _LIFE_RE.fullmatch(t)
    if m is not None:
        who = m.group("who")
        player, pending = _player(who)
        return (Quantity(QuantityKind.LIFE_TOTAL, player=player, raw=t), None,
                pending, frozenset())
    m = _DEVOTION_RE.fullmatch(t)
    if m is not None:
        letters = {_COLORS[w] for w in re.findall(r"[a-z]+", m.group("cols"))
                   if w in _COLORS}
        stat = "".join(c for c in _WUBRG if c in letters)
        return (Quantity(QuantityKind.DEVOTION, player="you", stat=stat,
                         raw=t), None, (), frozenset())
    m = _COLORS_SPENT_RE.fullmatch(t)
    if m is not None:
        r = _ref(m.group("ref"), source_left)
        if r is None:
            return _fail("reference", m.group("ref"))
        ref, pending, flags = r
        return (Quantity(QuantityKind.COLORS_SPENT, ref=ref, raw=t), None,
                pending, flags)
    m = _BASIC_TYPES_RE.fullmatch(t)
    if m is not None:
        filt, ref, pending, flags, failure = _set(m.group("f"))
        if failure is not None:
            return _fail(*failure)
        return (Quantity(QuantityKind.BASIC_LAND_TYPES, filter=filt, ref=ref,
                         player="any", raw=t), None, pending, flags)
    m = _CARD_TYPES_RE.fullmatch(t)
    if m is not None:
        filt, _, pending, _, failure = _set(m.group("f"))
        if failure is not None:
            return _fail(*failure)
        if filt.zone != "graveyard":
            return _fail("card_types_zone")
        return (Quantity(QuantityKind.CARD_TYPES_IN_GRAVEYARD, filter=filt,
                         player=filt.owner, raw=t), None, pending, frozenset())
    m = _GREATEST_RE.fullmatch(t)
    if m is not None:
        filt, ref, pending, flags, failure = _set(m.group("f"))
        if failure is not None:
            return _fail(*failure)
        return (Quantity(QuantityKind.GREATEST, filter=filt, ref=ref,
                         player="any", stat=m.group("stat"), raw=t), None,
                pending, flags)
    m = _KICKED_RE.fullmatch(t)
    if m is not None:
        ref, pending, flags = _ref(m.group("ref"), source_left) or (
            None, (("ref", m.group("ref")),), frozenset())
        return (Quantity(QuantityKind.TIMES_KICKED, ref=ref, raw=t), None,
                pending, flags)
    m = _PLAYERS_RE.fullmatch(t)
    if m is not None:
        who = "opponents" if m.group("p").startswith("opponent") else "any"
        return (Quantity(QuantityKind.COUNT, player=who, raw=t), None, (),
                frozenset())
    return _counted(t)


# ── Tails and the slot ─────────────────────────────────────────────────

# Words that cannot continue a quantity phrase: an amount operator, a
# recipient, a predicate or a printed duration (the one boundary,
# `duration.DURATION_START`). Cut only when the whole slot is no quantity.
_TAIL_RE = re.compile(
    r" (?=(?:to|plus|minus|rather than|times|as|from|onto|instead|where"
    r"|for each|can't|can|gets?|gains?|has|have|is|are"
    r"|and (?:you|its?|y|gains?|has|have|is|are|can't|deals?|loses?|gets?"
    r"|that|create|put|return|draw)"
    r"|%s)(?![\w']))" % DURATION_START)
# History phrases close the quantity before them (section 6); they are
# never a tail.
_HISTORY_TAIL = re.compile(r"this (?:turn|combat)\b")


_Rel = Tuple[Optional[Quantity], Optional[Tuple[str, str]], int,
             Tuple[Tuple[str, str], ...], frozenset]


@lru_cache(maxsize=CACHE_SIZE)
def _quantity_rel(t: str, source_left: bool) -> _Rel:
    value, failure, pending, flags = _whole(t, source_left)
    if value is not None:
        return value, None, len(t), pending, flags
    cuts = sorted({m.start() for m in _TAIL_RE.finditer(t)
                   if not _HISTORY_TAIL.match(t, m.start() + 1)}, reverse=True)
    for cut in cuts:
        head = t[:cut].rstrip(" ,")
        if not head:
            continue
        v, _, p, f = _whole(head, source_left)
        if v is not None:
            return v, None, len(head), p, f
    return None, failure, 0, (), frozenset()


def parse_quantity(host: str, span: Optional[Span] = None, *, lemma: str = "",
                   source_left: bool = False) -> SlotResult:
    """The `Quantity` of the phrase ``host[span]`` (default: the whole
    host).

    ``lemma`` is the caller's printed lemma (the verb whose amount this
    is). ``source_left`` is True when the source left as part of the
    ability's cost (A27): a SELF reference then reads last-known
    information. On success ``span`` is the consumed quantity phrase,
    ``rest_spans`` the tail the caller's other sub-grammars read (an
    operator, a recipient), ``pending`` the references and anaphoric
    players the linker binds, and ``flags`` may hold `LKI`. Trailing
    punctuation is structure, in neither. Otherwise the slot is
    ``UNMODELLED(QUANTITY)`` over the whole trimmed slot."""
    a, b = (0, len(host)) if span is None else span
    slot = host[a:b]
    lead = len(slot) - len(slot.lstrip())
    trimmed = slot.strip()
    body = trimmed.rstrip(" .,;")
    start = a + lead
    value, failure, end, pending, flags = _quantity_rel(body, source_left)
    if value is None:
        code, param = failure
        return SlotResult(unmodelled=_um(code, lemma, param),
                          span=(start, start + len(trimmed)))
    rest = rest_spans_after(host, start + end, start + len(body), " ,")
    return SlotResult(value=value, span=(start, start + end), rest_spans=rest,
                      flags=flags, pending=pending)


def clear_caches() -> None:
    _quantity_rel.cache_clear()
