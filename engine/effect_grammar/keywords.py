"""Keyword tables of the clause grammar (design doc 2026-09-29, G12, A1, A8,
M3; E0 step 9).

Closed CR tables over L0 output (lowercased, dashes unified to ``-``,
apostrophes unified, self-forms ``~``). The module follows the leaf
contract of `engine.effect_grammar.sub` -- ``(host, span, *, lemma="")``,
the shared `SlotResult`, host-absolute spans, ``keywords.<code>[:<param>]``
details over the closed `DETAIL_CODES`, bounded caches and
``clear_caches()`` -- but it is not a sub-grammar: L1 (structure) reads it
to classify a paragraph, and the full grammar reads its expansions. It
imports no sub-grammar leaf and no game state.

It holds:

* `KEYWORD_ABILITIES` -- the CR 702 keyword abilities a KEYWORD host can
  list (A1), each with its closed parameter shape. `cards.Keyword` is not
  this vocabulary (it lacks kicker, flashback, splice, escape, gift, ...).
  Keywords whose text is a labelled activated or static ability
  ("boast - {1}: ...", "exhaust - ...", "max speed - ...") are not list
  items: L1 strips their label like an ability word;
* `keywords702` -- M3: a face's MTGJSON ``keywords`` field intersected with
  that table. The field also lists CR 701 keyword actions (scry, mill,
  investigate) and ability words (CR 207.2c: landfall, delirium), which
  must never make an effect paragraph a keyword line;
* `parse_keyword_line` -- A1: a paragraph that is a list of keyword
  abilities, each item one of keyword, keyword N, keyword {cost},
  keyword-<non-mana cost> (verbs and commas allowed, to the sentence end),
  a multi-word keyword (splice onto <quality> {cost}) or a noun-parameter
  keyword (gift a tapped fish, enchant <object>, affinity for <type>).
  Costs are typed through `oracle_parser.parse_activation_cost` (A7: on the
  printed span when the caller has the L0 offset map);
* `parse_cost_rule` -- A8: "the <keyword> cost is equal to its mana cost"
  is the cost rule of a granted keyword, never an effect;
* `EXPANSIONS` -- the CR 701, CR 702 and CR 111.10 rules-English
  expansions, written as L0 text for the full grammar to parse into
  ``KeywordAction.expansion`` and predefined-token abilities.
"""
from __future__ import annotations

import re
from functools import lru_cache
from string import Template
from typing import (Callable, FrozenSet, Iterable, Mapping, NamedTuple,
                    Optional, Tuple)

from engine.effect_grammar.sub import (CACHE_SIZE, SlotResult, Span,
                                       rest_spans_after, unmodelled)
from engine.effect_spec import KeywordSpec, Stage, freeze_cost
from engine.oracle_parser import parse_activation_cost

__all__ = ["LEAF", "DETAIL_CODES", "KEYWORD_ABILITIES", "Expansion",
           "EXPANSIONS", "canonical_keyword", "keywords702",
           "parse_keyword_line", "parse_cost_rule", "expansion_text",
           "clear_caches"]

LEAF = "keywords"
DETAIL_CODES = frozenset({
    "unconsumed",              # a keyword item's parameter runs past the closed forms
    "cost_choice",             # "{g} or {w}": a cost choice the cost owner cannot hold
    "cost_self_form",          # a cost naming ~ with no printed span to read (A7)
    "face_keywords_disagree",  # a later list item the face's MTGJSON keywords lack
    "cost_rule_modified",      # "... equal to its mana cost reduced by {2}"
})


def _um(stage: Stage, lemma: str, code: str, param: str = ""):
    return unmodelled(stage, lemma, LEAF, code, DETAIL_CODES, param)


# ── The CR 702 table (A1) ──────────────────────────────────────────────

# Parameter shapes (closed). Every printed item is the keyword name followed
# by exactly its shape's parameter.
PLAIN = "plain"                # flying
N = "n"                        # crew 3 / bloodthirst x
N_OPT = "n_opt"                # vanishing [N] (CR 702.63b)
COST = "cost"                  # kicker {2}{r} / kicker-sacrifice a creature
COST_OPT = "cost_opt"          # mayhem [{cost}]
N_DASH_COST = "n_dash_cost"    # suspend 4-{u}
FROM = "from"                  # protection from <q>[, from <q>][, and from <q>]
FROM_OPT = "from_opt"          # hexproof [from <q>]
PARAM = "param"                # enchant <object> / gift <object> / champion <object>
PARAM_FOR = "param_for"        # affinity for <type>
PARAM_WITH_OPT = "param_with_opt"  # partner [with <name>]
SPLICE = "splice"              # splice onto <quality> {cost}
CRAFT = "craft"                # craft with <materials> {cost}
EMERGE = "emerge"              # emerge [from <type>] {cost}
PROTOTYPE = "prototype"        # prototype {cost} - p/t
DEVOUR = "devour"              # devour [<type>] N
MODULAR = "modular"            # modular N / modular-sunburst
DASH_PARAM = "dash_param"      # companion - <condition>
EQUIP = "equip"                # equip [<quality>] {cost} (CR 702.6e)
OVER_OPT = "over_opt"          # trample [over planeswalkers] (CR 702.19c)
TYPECYCLING = "typecycling"    # <type>cycling {cost} (CR 702.29e)
LANDWALK = "landwalk"          # <type>walk (CR 702.14c)

_SHAPES = {
    PLAIN: (
        "deathtouch", "defender", "double strike", "first strike", "flash",
        "flying", "haste", "indestructible", "intimidate", "lifelink",
        "reach", "shroud", "vigilance", "menace", "prowess",
        "fear", "shadow", "horsemanship", "flanking", "phasing", "banding",
        "storm", "cascade", "convoke", "delve", "improvise", "rebound",
        "split second", "fuse", "aftermath", "persist", "undying", "wither",
        "infect", "exalted", "devoid", "changeling", "cipher", "haunt",
        "conspire", "evolve", "extort", "exploit", "ingest", "myriad",
        "melee", "skulk", "soulbond", "sunburst", "epic", "gravestorm",
        "retrace", "jump-start", "living weapon", "totem armor",
        "umbra armor", "undaunted", "unleash", "riot", "mentor", "training",
        "ascend", "assist", "dethrone", "provoke", "decayed", "daybound",
        "nightbound", "ravenous", "compleated", "enlist", "read ahead",
        "for mirrodin!", "bargain", "start your engines!", "job select",
        "station", "tiered", "spree", "paradigm", "increment", "battle cry",
        "demonstrate", "hidden agenda", "double agenda", "living metal",
        "double team", "friends forever"),
    N: (
        "crew", "bushido", "toxic", "afflict", "annihilator", "rampage",
        "absorb", "afterlife", "fabricate", "renown", "frenzy", "poisonous",
        "bloodthirst", "tribute", "fading", "amplify", "graft", "dredge",
        "ripple", "casualty", "backup", "saddle", "mobilize", "firebending",
        "soulshift", "hideaway", "teamwork"),
    N_OPT: ("vanishing",),
    COST: (
        "flashback", "escape", "unearth", "embalm", "eternalize", "madness",
        "cycling", "kicker", "multikicker", "buyback", "evoke", "dash",
        "blitz", "disturb", "overload", "ninjutsu", "commander ninjutsu",
        "echo", "megamorph", "morph", "disguise", "foretell", "plot",
        "bestow", "entwine", "miracle", "outlast", "scavenge", "transmute",
        "reconfigure", "surge", "encore", "spectacle", "prowl", "mutate",
        "ward", "fortify", "replicate", "recover", "cumulative upkeep",
        "level up", "escalate", "cleave", "freerunning", "offspring",
        "sneak", "harmonize", "warp", "web-slinging", "squad",
        "more than meets the eye"),
    COST_OPT: ("mayhem",),
    N_DASH_COST: ("suspend", "reinforce", "awaken", "impending"),
    FROM: ("protection",),
    FROM_OPT: ("hexproof",),
    PARAM: ("enchant", "gift", "champion"),
    PARAM_FOR: ("affinity",),
    PARAM_WITH_OPT: ("partner",),
    SPLICE: ("splice",),
    CRAFT: ("craft",),
    EMERGE: ("emerge",),
    PROTOTYPE: ("prototype",),
    DEVOUR: ("devour",),
    MODULAR: ("modular",),
    DASH_PARAM: ("companion",),
    EQUIP: ("equip",),
    OVER_OPT: ("trample",),
    TYPECYCLING: ("typecycling",),
    LANDWALK: ("landwalk",),
}

# CR 702 keyword ability -> parameter shape.
KEYWORD_ABILITIES: Mapping[str, str] = {
    name: shape for shape, names in _SHAPES.items() for name in names}

# Variant families printed as one word (<type>cycling, <type>walk) are
# matched by their own patterns, never by name.
_FAMILIES = frozenset({TYPECYCLING, LANDWALK})
_COSTED = frozenset({COST, COST_OPT, N_DASH_COST, SPLICE, CRAFT, EMERGE,
                     PROTOTYPE, EQUIP, TYPECYCLING})


def canonical_keyword(name: str) -> Optional[str]:
    """An MTGJSON ``keywords`` entry as its CR 702 table name, or None when
    it is not a CR 702 keyword ability (a CR 701 keyword action, an ability
    word, a token name, a mode name). Variants fold onto their keyword:
    'Swampcycling' / 'Basic landcycling' -> 'typecycling', 'Islandwalk' ->
    'landwalk', 'Hexproof from' -> 'hexproof', 'Partner with' -> 'partner'."""
    k = " ".join(name.split()).casefold()
    if k.endswith(" from") or k.endswith(" with"):
        k = k[:-5]
    if k not in KEYWORD_ABILITIES:
        if k.endswith("cycling") and k != "cycling":
            k = TYPECYCLING
        elif k.endswith("walk"):
            k = LANDWALK
    return k if k in KEYWORD_ABILITIES else None


def keywords702(names: Iterable[str]) -> FrozenSet[str]:
    """M3: the face's MTGJSON keywords intersected with the CR 702 table --
    the candidate set `parse_keyword_line` is gated by."""
    return frozenset(k for k in map(canonical_keyword, names or ()) if k)


# ── Keyword lines (A1) ─────────────────────────────────────────────────

_MANA = r"(?:\{[^{}\s]+\})+"
_END = r"(?=\.(?:\s|$)|$)"           # a sentence end inside the slot
# A cost: a mana run (CR 702.33c "and/or" second cost; an "or" choice is
# refused), or a dash and a non-mana cost running to the sentence end. A
# colon in the dash text makes it a labelled activated ability, not a cost.
_COST = (r"(?: (?P<mc>%s)(?: and/or (?P<mc2>%s))?(?P<orc> or %s)?"
         r"|\s*-\s*(?P<dc>[^.:]+?)%s)" % (_MANA, _MANA, _MANA, _END))
_NUM = r"(?P<num>\d+|x)"
# A free parameter; a serial list ("artifact, creature, or planeswalker")
# is one parameter, since a keyword item never follows ", or".
_FREE = r"(?P<p>(?:[^.;,]+?, )+(?:or|and) [^.;,]+?|[^.;,]+?)(?=[.;,]|$)"
_QUALITY = r"[^.;,]+?(?=,? and from |, from |[.;,]|$)"

_PARAM_RE = {
    PLAIN: r"",
    N: r" %s\b" % _NUM,
    N_OPT: r"(?: %s\b)?" % _NUM,
    COST: _COST,
    COST_OPT: r"(?:%s)?" % _COST,
    N_DASH_COST: r" %s\s*-\s*(?P<mc>%s)" % (_NUM, _MANA),
    FROM: r" from (?P<p>%s)(?P<more>(?:(?:,? and |, )from %s)*)" % (
        _QUALITY, _QUALITY),
    FROM_OPT: r"(?: from (?P<p>%s)(?P<more>(?:(?:,? and |, )from %s)*))?" % (
        _QUALITY, _QUALITY),
    PARAM: r" " + _FREE,
    PARAM_FOR: r" for " + _FREE,
    PARAM_WITH_OPT: r"(?: with %s)?" % _FREE,
    SPLICE: r" onto (?P<p>[a-z ]+?)" + _COST,
    CRAFT: r" with (?P<p>[^.;]+?) (?P<mc>%s)(?=[.;,]|$)" % _MANA,
    EMERGE: r"(?: from (?P<p>[a-z ]+?))?" + _COST,
    PROTOTYPE: r" (?P<mc>%s)\s*-\s*(?P<p>\d+/\d+)" % _MANA,
    DEVOUR: r" (?:(?P<p>[a-z]+) )?%s\b" % _NUM,
    MODULAR: r"(?: %s\b|\s*-\s*(?P<p>sunburst)\b)" % _NUM,
    DASH_PARAM: r"\s*-\s*(?P<p>[^.]+?)%s" % _END,
    # The quality is the words before a mana cost that ends the item, so a
    # sentence about equip abilities ("... cost {1} less") never reads as one.
    EQUIP: r"(?: (?P<p>[a-z][a-z ]*?)(?= %s(?:[.;,]|$)))?%s" % (_MANA, _COST),
    OVER_OPT: r"(?: over (?P<p>[a-z]+))?",
    TYPECYCLING: _COST,
    LANDWALK: r"",
}
_BOUNDARY = r"(?![\w'])"
_NAME_RE = re.compile(r"(?P<name>%s)%s" % ("|".join(
    re.escape(n) for n in sorted(
        (n for n, s in KEYWORD_ABILITIES.items() if s not in _FAMILIES),
        key=len, reverse=True)), _BOUNDARY))
_FAMILY_RE = {
    TYPECYCLING: re.compile(
        r"(?P<p>(?:basic |artifact |snow )?[a-z]+)cycling" + _BOUNDARY),
    LANDWALK: re.compile(
        r"(?P<p>(?:nonbasic |legendary |snow )?[a-z]+)walk" + _BOUNDARY),
}
_ITEM_RE = {shape: re.compile(p) for shape, p in _PARAM_RE.items()}
_FROM_EACH_RE = re.compile(r"(?:,? and |, )from (%s)" % _QUALITY)
_SEP_RE = re.compile(r"[,;] ")
_WHERE_X_RE = re.compile(r",? (?=where x is )")
_REST_RE = re.compile(r"\.\s+(?=\S)")

# One typed item, relative to the slot: (name, n, param, cost span).
_RelItem = Tuple[str, Optional[int], Optional[str], Optional[Span]]


class _Item(NamedTuple):
    items: Tuple[_RelItem, ...]
    end: int
    bare: bool                    # no parameter printed
    x: bool                       # the parameter is X
    cost_choice: bool


def _head(t: str, pos: int):
    """The keyword name of slot text `t` at `pos`: (name, shape, end,
    head_param), or None when no CR 702 name starts there."""
    m = _NAME_RE.match(t, pos)
    if m is not None:
        name = m.group("name")
        return name, KEYWORD_ABILITIES[name], m.end(), None
    for shape, rx in _FAMILY_RE.items():
        m = rx.match(t, pos)
        if m is not None:
            return shape, shape, m.end(), m.group("p")
    return None


def _match_item(t: str, pos: int, head=None) -> Optional[_Item]:
    """One keyword item of slot text `t` at `pos`, or None (no keyword name
    there, or its parameter is not the keyword's closed shape)."""
    head = head or _head(t, pos)
    if head is None:
        return None
    name, shape, name_end, head_param = head
    p = _ITEM_RE[shape].match(t, name_end)
    if p is None:
        return None
    g = p.groupdict()
    num = g.get("num")
    n = int(num) if num and num.isdigit() else None
    param = g.get("p") or head_param
    if num == "x":
        param = "x"
    cost = None
    if g.get("mc"):
        cost = p.span("mc")
    elif g.get("dc"):
        cost = p.span("dc")
    items = [(name, n, param, cost)]
    if g.get("mc2"):
        items.append((name, n, param, p.span("mc2")))
    if g.get("more"):
        for q in _FROM_EACH_RE.finditer(g["more"]):
            items.append((name, None, q.group(1), None))
    bare = p.end() == name_end and head_param is None
    return _Item(tuple(items), p.end(), bare, num == "x", bool(g.get("orc")))


@lru_cache(maxsize=CACHE_SIZE)
def _line_rel(t: str, candidates: Optional[FrozenSet[str]]):
    """The keyword list of trimmed slot text `t`:
    ('none',) | ('um', code, param) |
    ('ok', items, consumed_end, rest_start or None)."""
    head = _head(t, 0)
    if head is None:
        return ("none",)
    if candidates is not None and head[0] not in candidates:
        return ("none",)
    item = _match_item(t, 0, head)
    if item is None:
        # The face's own keyword opens the paragraph but its parameter is
        # not the keyword's shape. A sentence ("equip abilities you
        # activate cost {1} less.") is ability text; a keyword item -- no
        # sentence end -- is a keyword line the table cannot type, and it
        # is refused rather than handed on as resolution text (A1).
        if t.endswith("."):
            return ("none",)
        return ("um", "unconsumed", head[0])
    items = []
    first = True
    while True:
        name = item.items[0][0]
        if candidates is not None and name not in candidates:
            return ("um", "face_keywords_disagree", name)
        if item.cost_choice:
            return ("um", "cost_choice", "")
        items.extend(item.items)
        last, single_bare = item, first and item.bare
        first = False
        sep = _SEP_RE.match(t, last.end)
        nxt = _match_item(t, sep.end()) if sep else None
        if nxt is None:
            break
        item = nxt
    end = last.end
    tail = t[end:]
    if tail in ("", "."):
        return ("ok", tuple(items), len(t), None)
    rest = _REST_RE.match(t, end)
    if rest is not None:
        return ("ok", tuple(items), end + 1, rest.end())
    if last.x:
        w = _WHERE_X_RE.match(t, end)
        if w is not None:
            return ("ok", tuple(items), end, w.end())
    if single_bare and len(items) == 1:
        return ("none",)              # a sentence that opens with a keyword word
    return ("um", "unconsumed", "")


@lru_cache(maxsize=CACHE_SIZE)
def _cost_snapshot(printed_cost: str):
    return freeze_cost(parse_activation_cost(printed_cost))


def _trim(host: str, span: Optional[Span]) -> Span:
    a, b = (0, len(host)) if span is None else span
    while a < b and host[a].isspace():
        a += 1
    while b > a and host[b - 1].isspace():
        b -= 1
    return a, b


def parse_keyword_line(host: str, span: Optional[Span] = None, *,
                       lemma: str = "",
                       candidates: Optional[FrozenSet[str]] = None,
                       printed: Optional[Callable[[Span], str]] = None
                       ) -> Optional[SlotResult]:
    """A1: the paragraph ``host[span]`` (default: the whole host) as a list
    of CR 702 keyword abilities, or None when it is not a keyword line.

    ``candidates`` is the face's `keywords702` set (M3); None reads the CR
    702 table alone (a synthetic template with no keyword data). The first
    item must be a candidate, so a keyword action ("scry 2.") or a sentence
    that merely opens with a keyword word ("flying creatures you control
    ...") is never a keyword line.

    ``value`` is the tuple of `KeywordSpec` in printed order: one per item,
    two for a kicker "and/or" cost, one per protection quality. A cost is
    the slot's text in ``cost`` and the cost owner's image in
    ``cost_snapshot``, read from ``printed(span)`` when the caller has the
    L0 offset map (A7) and from the host text otherwise -- a cost naming
    ``~`` without ``printed`` is refused, since the owner reads "this
    creature", not "~". A sentence after the list ("x can't be 0.") or a
    where-X frame is handed back in ``rest_spans``."""
    a, b = _trim(host, span)
    rel = _line_rel(host[a:b], candidates)
    if rel[0] == "none":
        return None
    if rel[0] == "um":
        return SlotResult(unmodelled=_um(Stage.STRUCTURE, lemma, rel[1], rel[2]),
                          span=(a, b))
    _, items, consumed, rest_start = rel
    specs = []
    for name, n, param, cost in items:
        if cost is None:
            specs.append(KeywordSpec(name=name, n=n, param=param))
            continue
        cs, ce = cost[0] + a, cost[1] + a
        text = host[cs:ce]
        if printed is not None:
            source = printed((cs, ce))
        elif "~" in text:
            return SlotResult(
                unmodelled=_um(Stage.STRUCTURE, lemma, "cost_self_form"),
                span=(a, b))
        else:
            source = text
        specs.append(KeywordSpec(name=name, n=n, param=param, cost=text,
                                 cost_snapshot=_cost_snapshot(source)))
    rest = () if rest_start is None else rest_spans_after(host, a + rest_start, b)
    return SlotResult(value=tuple(specs), span=(a, a + consumed),
                      rest_spans=rest)


# ── The cost rule rider (A8) ───────────────────────────────────────────

_COST_RULE_RE = re.compile(
    r"(?:the|its) (?P<kw>[a-z][a-z' -]*?) cost is equal to "
    r"(?:its|that card's|~'s) mana cost(?P<tail>[^.]*)\.?")


@lru_cache(maxsize=CACHE_SIZE)
def _cost_rule_rel(t: str):
    m = _COST_RULE_RE.fullmatch(t)
    if m is None:
        return None
    name = canonical_keyword(m.group("kw"))
    if name is None or KEYWORD_ABILITIES[name] not in _COSTED:
        return None
    if m.group("tail").strip():
        return ("um", name)
    return ("ok", name)


def parse_cost_rule(host: str, span: Optional[Span] = None, *,
                    lemma: str = "") -> Optional[SlotResult]:
    """A8: "The <keyword> cost is equal to its mana cost." is the cost
    parameter of the keyword the host grants (``KeywordSpec(name,
    cost_rule='mana_cost')``), absorbed as a rider and never an effect.
    A modified rule ("... reduced by {2}") is UNMODELLED; any other
    sentence returns None."""
    a, b = _trim(host, span)
    rel = _cost_rule_rel(host[a:b])
    if rel is None:
        return None
    if rel[0] == "um":
        return SlotResult(
            unmodelled=_um(Stage.CLAUSE, lemma, "cost_rule_modified"),
            span=(a, b))
    return SlotResult(value=KeywordSpec(name=rel[1], cost_rule="mana_cost"),
                      span=(a, b))


# ── Rules-English expansions (G12) ─────────────────────────────────────

class Expansion(NamedTuple):
    """One rules-English expansion.

    ``section``: the CR section that defines it ('701' keyword action,
    '702' keyword ability, '111.10' predefined token). ``host``: 'effect'
    when the text is resolution text (an action performed), 'ability' when
    it is the text of abilities (a triggered keyword ability, a token's
    abilities). ``template`` is L0 text with ``$n``, ``$subtype`` and
    ``$object`` parameters."""
    section: str
    host: str
    template: str

    @property
    def params(self) -> FrozenSet[str]:
        return frozenset(m.group("named") or m.group("braced")
                         for m in Template.pattern.finditer(self.template)
                         if m.group("named") or m.group("braced"))


def _x(section: str, host: str, template: str) -> Expansion:
    return Expansion(section, host, template)


EXPANSIONS: Mapping[str, Expansion] = {
    # CR 701 keyword actions: what the action instructs.
    "investigate": _x("701", "effect", "create a clue token."),
    "proliferate": _x("701", "effect",
                      "choose any number of permanents and/or players, then "
                      "give each another counter of each kind already there."),
    "populate": _x("701", "effect",
                   "create a token that's a copy of a creature token you control."),
    "explore": _x("701", "effect",
                  "reveal the top card of your library. if it's a land card, "
                  "put it into your hand. otherwise, put a +1/+1 counter on ~, "
                  "then you may put that card into your graveyard."),
    "connive": _x("701", "effect",
                  "draw a card, then discard a card. if you discarded a "
                  "nonland card this way, put a +1/+1 counter on ~."),
    "forage": _x("701", "effect",
                 "exile three cards from your graveyard or sacrifice a food."),
    "time travel": _x("701", "effect",
                      "for each permanent you control with a time counter on "
                      "it and each suspended card you own, you may put a time "
                      "counter on it or remove a time counter from it."),
    "manifest dread": _x("701", "effect",
                         "look at the top two cards of your library. put one "
                         "of them onto the battlefield face down as a 2/2 "
                         "creature and the other into your graveyard."),
    "harness": _x("701", "effect", "~ becomes harnessed."),
    "goad": _x("701", "effect",
               "until your next turn, $object attacks each combat if able and "
               "attacks a player other than you if able."),
    "suspect": _x("701", "effect", "$object becomes suspected."),
    "detain": _x("701", "effect",
                 "until your next turn, $object can't attack or block and its "
                 "activated abilities can't be activated."),
    "exert": _x("701", "effect",
                "$object won't untap during your next untap step."),
    "collect evidence": _x("701", "effect",
                           "exile any number of cards with total mana value "
                           "$n or greater from your graveyard."),
    "discover": _x("701", "effect",
                   "exile cards from the top of your library until you exile "
                   "a nonland card with mana value $n or less. cast it without "
                   "paying its mana cost or put it into your hand. put the "
                   "rest on the bottom of your library in a random order."),
    "incubate": _x("701", "effect",
                   "create an incubator token with $n +1/+1 counters on it."),
    "adapt": _x("701", "effect",
                "if ~ has no +1/+1 counters on it, put $n +1/+1 counters on it."),
    "bolster": _x("701", "effect",
                  "choose a creature with the least toughness among creatures "
                  "you control and put $n +1/+1 counters on it."),
    "support": _x("701", "effect",
                  "put a +1/+1 counter on each of up to $n other target creatures."),
    "monstrosity": _x("701", "effect",
                      "if ~ isn't monstrous, put $n +1/+1 counters on it and "
                      "it becomes monstrous."),
    "fateseal": _x("701", "effect",
                   "look at the top $n cards of target opponent's library, "
                   "then put any number of them on the bottom of that "
                   "player's library and the rest on top in any order."),
    "endure": _x("701", "effect",
                 "put $n +1/+1 counters on ~ or create a $n/$n white spirit "
                 "creature token."),
    "amass": _x("701", "effect",
                "if you don't control an army creature, create a 0/0 black "
                "$subtype army creature token. put $n +1/+1 counters on an "
                "army you control. it becomes a $subtype in addition to its "
                "other types."),
    # CR 702 keyword abilities whose meaning is a printed-shape ability.
    "prowess": _x("702", "ability",
                  "whenever you cast a noncreature spell, ~ gets +1/+1 until "
                  "end of turn."),
    "exalted": _x("702", "ability",
                  "whenever a creature you control attacks alone, that "
                  "creature gets +1/+1 until end of turn."),
    "annihilator": _x("702", "ability",
                      "whenever ~ attacks, defending player sacrifices $n "
                      "permanents."),
    "afflict": _x("702", "ability",
                  "whenever ~ becomes blocked, defending player loses $n life."),
    "bushido": _x("702", "ability",
                  "whenever ~ blocks or becomes blocked, it gets +$n/+$n "
                  "until end of turn."),
    "rampage": _x("702", "ability",
                  "whenever ~ becomes blocked, it gets +$n/+$n until end of "
                  "turn for each creature blocking it beyond the first."),
    "renown": _x("702", "ability",
                 "when ~ deals combat damage to a player, if it isn't "
                 "renowned, put $n +1/+1 counters on it and it becomes "
                 "renowned."),
    "training": _x("702", "ability",
                   "whenever ~ attacks with another creature with greater "
                   "power, put a +1/+1 counter on ~."),
    "mentor": _x("702", "ability",
                 "whenever ~ attacks, put a +1/+1 counter on target attacking "
                 "creature with lesser power."),
    "afterlife": _x("702", "ability",
                    "when ~ dies, create $n 1/1 white and black spirit "
                    "creature tokens with flying."),
    "undying": _x("702", "ability",
                  "when ~ dies, if it had no +1/+1 counters on it, return it "
                  "to the battlefield under its owner's control with a +1/+1 "
                  "counter on it."),
    "persist": _x("702", "ability",
                  "when ~ dies, if it had no -1/-1 counters on it, return it "
                  "to the battlefield under its owner's control with a -1/-1 "
                  "counter on it."),
    "extort": _x("702", "ability",
                 "whenever you cast a spell, you may pay {w/b}. if you do, "
                 "each opponent loses 1 life and you gain that much life."),
    "battle cry": _x("702", "ability",
                     "whenever ~ attacks, each other attacking creature gets "
                     "+1/+0 until end of turn."),
    "dethrone": _x("702", "ability",
                   "whenever ~ attacks the player with the most life or tied "
                   "for most life, put a +1/+1 counter on ~."),
    "fabricate": _x("702", "ability",
                    "when ~ enters, put $n +1/+1 counters on it or create $n "
                    "1/1 colorless servo artifact creature tokens."),
    "flanking": _x("702", "ability",
                   "whenever a creature without flanking blocks ~, the "
                   "blocking creature gets -1/-1 until end of turn."),
    # CR 111.10 predefined tokens: the abilities each one has ('' = none).
    "treasure": _x("111.10", "ability",
                   "{t}, sacrifice ~: add one mana of any color."),
    "food": _x("111.10", "ability", "{2}, {t}, sacrifice ~: you gain 3 life."),
    "gold": _x("111.10", "ability", "sacrifice ~: add one mana of any color."),
    "walker": _x("111.10", "ability", ""),
    "shard": _x("111.10", "ability",
                "{2}, sacrifice ~: scry 1, then draw a card."),
    "clue": _x("111.10", "ability", "{2}, sacrifice ~: draw a card."),
    "blood": _x("111.10", "ability",
                "{1}, {t}, discard a card, sacrifice ~: draw a card."),
    "powerstone": _x("111.10", "ability",
                     "{t}: add {c}. this mana can't be spent to cast a "
                     "nonartifact spell."),
    "incubator": _x("111.10", "ability", "{2}: transform ~."),
    "map": _x("111.10", "ability",
              "{1}, {t}, sacrifice ~: target creature you control explores. "
              "activate only as a sorcery."),
    "junk": _x("111.10", "ability",
               "{t}, sacrifice ~: exile the top card of your library. you may "
               "play that card this turn. activate only as a sorcery."),
    "lander": _x("111.10", "ability",
                 "{2}, {t}, sacrifice ~: search your library for a basic land "
                 "card, put it onto the battlefield tapped, then shuffle."),
    "mutagen": _x("111.10", "ability",
                  "{1}, {t}, sacrifice ~: put a +1/+1 counter on target "
                  "creature. activate only as a sorcery."),
}


def expansion_text(name: str, **params: str) -> Optional[str]:
    """The L0 expansion of keyword action / ability / predefined token
    `name` with its parameters filled ("adapt", n="3"), or None when the
    table has none. A missing parameter raises KeyError: an expansion is
    never parsed with a placeholder left in it."""
    e = EXPANSIONS.get(name)
    if e is None:
        return None
    return Template(e.template).substitute(params)


def clear_caches() -> None:
    _line_rel.cache_clear()
    _cost_snapshot.cache_clear()
    _cost_rule_rel.cache_clear()
