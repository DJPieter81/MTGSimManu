"""Payload sub-grammar (design doc 2026-09-29, section 4 payload column;
section 2 ``Payload`` union).

Types the object of a payload verb, once, at LOAD (never at resolution),
over L0 output (the leaf contract in `engine.effect_grammar.sub`: every
public parser takes ``(host, span)`` and returns the shared `SlotResult`
with spans into ``host``):

* mana (CR 106): a ``ManaSpec`` -- a SYMBOL MULTISET, not an amount.
  "{G}{G}" is ``symbols=('G','G')``; "three mana of any one color" is three
  wildcard units ``('*','*','*')`` with ``one_color``. A variable count ("X
  mana", "that much") is the per-unit multiset plus a multiplier `amount`;
  a scaler ("for each ...", "equal to ...") is left in ``rest_spans`` for
  the amount grammar. "Spend this mana only ..." is ``ManaSpec.restriction``.
  `adds_mana` is the ADD_MANA recognition the CR 605.1a/b mana-ability rule
  reads (A6);
* counters (CR 122): a ``CounterSpec`` whose ``kinds`` is the counter
  multiset (literal counts expand, as mana symbols do; a variable count is
  one kind plus an `amount`); an "or" kind list is ``choice``. Energy
  symbols are energy counters on a player (CR 107.14, 122.1), also as the
  PAY payload;
* tokens (CR 111): a ``TokenSpec``. Granted ``⟨qk⟩`` abilities and a copy's
  object are handed to the linker in ``pending``; predefined tokens are the
  CR 111.10 kinds;
* continuous predicates (CR 611-613): one ``effect_model.Modification``
  (MODIFY_PT, SET_BASE_PT, ADD/REMOVE_KEYWORDS, REMOVE_ALL_ABILITIES,
  GRANT_ABILITY, SET/ADD_TYPES, SET_COLORS, PROHIBIT, PERMIT, LIMIT,
  REQUIRE, COST_DELTA, PREVENT_DAMAGE, SET_CONTROLLER, SWITCH_PT).
  APPLIED_MODKINDS is not consulted: ``can_execute`` gates it;
* activation cost modifiers (A8): "This ability costs {N} less to activate
  ..." is a COST_DELTA Modification the structure layer absorbs into
  ``AbilityEffects.cost_modifiers`` (CR 602.2b applying 601.2f);
* CR 701 keyword actions: a ``KeywordAction`` (name, amount, subtype). Its
  ``expansion`` is filled by the full grammar, not here (no cycle);
* alternatives (A19): "your choice of X or Y" and "a Food token or a
  Treasure token" -- each option typed, for ``EffectSpec.alternatives``.

Closed tables: an unknown phrase is a typed ``Unmodelled``, never a guess.
The leaf reads no card name and no game state.
"""
from __future__ import annotations

import dataclasses
import re
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple, Union

from engine.effect_grammar.sub import (CACHE_SIZE, NUMBER_WORDS,
                                       SlotResult, Span, join_spans,
                                       rest_spans_after, unmodelled)
from engine.effect_grammar.sub.duration import DURATION_START
from engine.effect_model import ModKind, Modification
from engine.effect_spec import (Amount, AmountKind, CostSnapshot, CounterSpec,
                                Granted, KeywordAction, ManaSpec, Ref, RefKind,
                                Stage, TokenSpec, Unmodelled, Verb,
                                freeze_cost)

__all__ = ["LEAF", "DETAIL_CODES", "parse_payload", "parse_mana",
           "adds_mana", "parse_mana_restriction", "parse_counters",
           "parse_token", "parse_modification", "parse_cost_modifier",
           "parse_keyword_action", "parse_alternatives",
           "KEYWORD_ACTION_NAMES", "UNSUPPORTED_KEYWORD_ACTIONS",
           "PREDEFINED_TOKENS", "WILDCARD", "clear_caches"]

LEAF = "payload"
DETAIL_CODES = frozenset({
    "mana_no_symbols", "mana_count", "mana_tail", "counter_kind",
    "counter_choice_count", "counter_mixed_variable", "counter_no_counter",
    "counter_alternative", "token_no_token", "token_type_phrase",
    "token_no_type", "token_alternative", "modification_unknown",
    "prohibit_action", "prohibit_qualifier", "cost_delta_subject",
    "keyword_action_unsupported", "keyword_action_unknown",
    "keyword_action_param", "keyword_action_nothing_consumed", "pay_cost",
    "emblem_no_quote"})

PayloadValue = Union[Modification, TokenSpec, CounterSpec, ManaSpec,
                     KeywordAction, Granted, CostSnapshot]

# A relative parse: (value, unmodelled, consumed_end, amount, pending, alts).
# `consumed_end` indexes the stripped slot text; `_finish` shifts it into
# the host and cuts the rest span. The relative parsers are memoised on the
# slot text alone, so their Unmodelled carries no lemma: `_finish` stamps
# the caller's.
_Rel = Tuple[Optional[PayloadValue], Optional[Unmodelled], int,
             Optional[Amount], Tuple[Tuple[str, str], ...],
             Tuple[SlotResult, ...]]

WILDCARD = "*"     # one mana / counter unit whose colour or kind is chosen


def _um(code: str, stage: Stage = Stage.CLAUSE, param: str = "") -> Unmodelled:
    return unmodelled(stage, "", LEAF, code, DETAIL_CODES, param)


def _stamp(u: Optional[Unmodelled], lemma: str) -> Optional[Unmodelled]:
    return u if u is None or u.lemma == lemma else dataclasses.replace(u, lemma=lemma)


def _finish(slot: str, offset: int, rel: _Rel, lemma: str = "") -> SlotResult:
    """`rel` over the stripped `slot` (which sits at `offset` in the host)
    as a SlotResult in host coordinates. On failure the span is the whole
    trimmed slot."""
    value, um, end, amount, pending, alts = rel
    lead = len(slot) - len(slot.lstrip())
    body = slot.strip()
    start = offset + lead
    if value is None and um is not None:
        return SlotResult(unmodelled=_stamp(um, lemma),
                          span=(start, start + len(body)))
    rest = rest_spans_after(slot, lead + end, lead + len(body), " ,.;")
    return SlotResult(value=value, span=(start, start + end),
                      rest_spans=tuple((a + offset, b + offset) for a, b in rest),
                      amount=amount, pending=pending, alternatives=alts)


def _slot(host: str, span: Span) -> Tuple[str, int]:
    return host[span[0]:span[1]], span[0]


# ── Counts ─────────────────────────────────────────────────────────────

_WORD_COUNTS: Dict[str, int] = dict(NUMBER_WORDS, a=1, an=1)
_COUNT_WORDS = sorted(_WORD_COUNTS, key=len, reverse=True)
_COUNT_RE = r"(?:%s|\d+|x)" % "|".join(_COUNT_WORDS)
_X_UNIT = Amount(AmountKind.X, n=1)


def _count(word: str) -> Optional[Amount]:
    """A literal or X count word; None when the word is no count."""
    if word == "x":
        return _X_UNIT
    if word.isdigit():
        return Amount(AmountKind.LITERAL, n=int(word))
    n = _WORD_COUNTS.get(word)
    return None if n is None else Amount(AmountKind.LITERAL, n=n)


def _signed(sign: str, word: str) -> Optional[Amount]:
    """A signed P/T component: '+2' -> LITERAL 2, '-x' -> X with sign -1."""
    s = -1 if sign == "-" else 1
    if word == "x":
        return Amount(AmountKind.X, n=s)
    if word.isdigit():
        return Amount(AmountKind.LITERAL, n=s * int(word))
    return None


# ── Mana (CR 106) ──────────────────────────────────────────────────────

_SYM = r"\{(?:[wubrgcs]|\d+|x|[wubrg2]/[wubrgp])\}"
_SYM_RE = re.compile(_SYM)
_RUN = r"(?:%s)+" % _SYM
_RUN_CHOICE_RE = re.compile(r"(?P<runs>%s(?:(?:, or |, | or )%s)*)" % (_RUN, _RUN))
_RUN_SEP_RE = re.compile(r", or |, | or ")
_MANA_PREFIX_RE = re.compile(r"(?:an additional |additional |an amount of )")
_MANA_COUNT_RE = re.compile(r"(?P<count>%s|that much) (?:additional )?mana\b"
                            % _COUNT_RE)
_THAT_MUCH = Amount(AmountKind.THAT_MUCH)
_TWICE_THAT_MUCH = Amount(AmountKind.MULTIPLY, n=2, inner=_THAT_MUCH)
# "<count> {C}" / "that much {G}": a counted symbol run.
_COUNTED_RUN_RE = re.compile(r"(?P<count>twice that much|that much|%s) (?P<run>%s)"
                             % (_COUNT_RE, _RUN))

# (tail pattern, ManaSpec field overrides). Tried in order after "<N> mana ".
_MANA_TAILS: Tuple[Tuple["re.Pattern[str]", Dict[str, Any]], ...] = (
    (re.compile(r" of any one color\b"), {"one_color": True}),
    (re.compile(r" of any color\b(?! (?:that|in|among|a |an ))"),
     {"any_color": True}),
    (re.compile(r" of any type that (?:(?:that |the )?(?:land|permanent)|it)"
                r" (?:produced|could produce)\b"),
     {"mirror": Ref(RefKind.EVENT_OBJECT)}),
    (re.compile(r" of the chosen color\b"), {"chosen_color": True}),
    (re.compile(r" in any combination of colors\b"), {"combination": True}),
    (re.compile(r" in any combination of (?P<syms>%s(?:(?: and/or |, and/or |, )%s)*)"
                % (_SYM, _SYM)), {"combination": True}),
)
_RESTRICTION_RE = re.compile(
    r"\s*[.,]?\s*(?:spend this mana only (?P<only>[^.;]+)"
    r"|(?P<cant>this mana can't be spent [^.;]+))\.?")


def _symbols(run: str) -> Tuple[str, ...]:
    return tuple(s[1:-1].upper() for s in _SYM_RE.findall(run))


def _braced(run: str) -> str:
    return "".join("{%s}" % s for s in _symbols(run))


def parse_mana_restriction(text: str) -> Optional[str]:
    """The "Spend this mana only ..." rider, as ManaSpec.restriction."""
    m = _RESTRICTION_RE.match(text)
    if m is None:
        return None
    return m.group("only").strip() if m.group("only") else m.group("cant").strip()


@lru_cache(maxsize=CACHE_SIZE)
def _mana_rel(t: str) -> _Rel:
    pos = 0
    m = _MANA_PREFIX_RE.match(t)
    if m:
        pos = m.end()
    fields: Dict[str, Any] = {}
    amount: Optional[Amount] = None
    m = _COUNTED_RUN_RE.match(t, pos)
    if m:
        word, syms = m.group("count"), _symbols(m.group("run"))
        n = (_TWICE_THAT_MUCH if word == "twice that much" else
             _THAT_MUCH if word == "that much" else _count(word))
        if n is not None and n.kind is AmountKind.LITERAL:
            fields["symbols"] = syms * n.n
        else:
            fields["symbols"], amount = syms, n
        end = m.end()
    elif _RUN_CHOICE_RE.match(t, pos):
        m = _RUN_CHOICE_RE.match(t, pos)
        runs = _RUN_SEP_RE.split(m.group("runs"))
        if len(runs) == 1:
            fields["symbols"] = _symbols(runs[0])
        else:
            fields["choice"] = tuple(_braced(r) for r in runs)
        end = m.end()
    else:
        m = _MANA_COUNT_RE.match(t, pos)
        if m is None:
            return (None, _um("mana_no_symbols"), 0, None, (), ())
        word = m.group("count")
        n = _THAT_MUCH if word == "that much" else _count(word)
        if n is None:
            return (None, _um("mana_count"), 0, None, (), ())
        for tail, overrides in _MANA_TAILS:
            mt = tail.match(t, m.end())
            if mt:
                fields.update(overrides)
                if "syms" in (mt.groupdict() or {}):
                    fields["choice"] = tuple(
                        "{%s}" % s for s in _symbols(mt.group("syms")))
                end = mt.end()
                break
        else:
            return (None, _um("mana_tail"), 0, None, (), ())
        if n.kind is AmountKind.LITERAL:
            fields["symbols"] = (WILDCARD,) * n.n
        else:
            fields["symbols"] = (WILDCARD,)
            amount = n
    restriction = parse_mana_restriction(t[end:])
    if restriction is not None:
        fields["restriction"] = restriction
        end += _RESTRICTION_RE.match(t[end:]).end()
    return (ManaSpec(**fields), None, end, amount, (), ())


def parse_mana(text: str, span: Span, *, lemma: str = "") -> SlotResult:
    """The object of "add" in ``text[span]`` as a ManaSpec (a symbol
    multiset)."""
    slot, offset = _slot(text, span)
    return _finish(slot, offset, _mana_rel(slot.strip()), lemma)


_ADD_RE = re.compile(r"\badds?\s+")


def adds_mana(text: str) -> bool:
    """Does the clause text hold an ADD_MANA clause (CR 605.1a/b, A6)?"""
    for m in _ADD_RE.finditer(text):
        tail = text[m.end():]
        if _mana_rel(tail.split(".")[0].strip())[0] is not None:
            return True
        if re.match(r"(?:an additional |additional |an amount of |that much )?"
                    r"(?:%s|(?:\w+ )?(?:additional )?mana\b)" % _SYM, tail):
            return True
    return False


# ── Counters (CR 122) ──────────────────────────────────────────────────

_COUNTER_COUNT = (r"(?P<count>an additional|a number of|any number of|"
                  r"that many|all|%s)" % _COUNT_RE)
_KIND = r"(?:[+-]\d+/[+-]\d+|(?:first|double) strike|[a-z][a-z'\-]*)"
_KIND_LIST = r"(?P<kinds>%s(?:(?:, or |, | or )%s)*)" % (_KIND, _KIND)
# "two additional +1/+1 counters": 'additional' after a count qualifies the
# count, it is no kind.
_COUNTER_GROUP_RE = re.compile(
    r"(?:%s )?(?:additional )?(?:%s )?counters?\b" % (_COUNTER_COUNT, _KIND_LIST))
_KIND_SEP_RE = re.compile(r", or |, | or ")
_ENERGY_RE = re.compile(r"(?P<pre>an amount of |any amount of )?(?P<run>(?:\{e\})+)")
_NOT_A_KIND = frozenset(_WORD_COUNTS) | {"the", "of", "each", "all", "that",
                                          "many", "number", "additional", "x",
                                          "any", "kind"}


def _energy_rel(t: str) -> Optional[_Rel]:
    m = _ENERGY_RE.match(t)
    if m is None:
        return None
    n = m.group("run").count("{e}")
    pre = m.group("pre")
    if pre == "any amount of ":
        return (CounterSpec(kinds=("energy",) * n), None, m.end(),
                Amount(AmountKind.ANY_NUMBER), (), ())
    if pre == "an amount of ":
        return (CounterSpec(kinds=("energy",) * n), None, m.end(), None,
                (("amount", "an amount of"),), ())
    return (CounterSpec(kinds=("energy",) * n), None, m.end(), None, (), ())


@lru_cache(maxsize=CACHE_SIZE)
def _counters_rel(t: str) -> _Rel:
    energy = _energy_rel(t)
    if energy is not None:
        return energy
    kinds: Tuple[str, ...] = ()
    choice = False
    amount: Optional[Amount] = None
    pending: Tuple[Tuple[str, str], ...] = ()
    pos, groups = 0, 0
    while True:
        m = _COUNTER_GROUP_RE.match(t, pos)
        if m is None:
            break
        group_kinds = tuple(_KIND_SEP_RE.split(m.group("kinds"))) \
            if m.group("kinds") else (WILDCARD,)
        if any(k in _NOT_A_KIND for k in group_kinds):
            return (None, _um("counter_kind"), 0, None, (), ())
        word = m.group("count") or "a"
        n: Optional[Amount]
        if word in ("a", "an", "an additional"):
            n = Amount(AmountKind.LITERAL, n=1)
        elif word == "that many":
            n = _THAT_MUCH
        elif word == "all":
            n = Amount(AmountKind.ALL)
        elif word == "any number of":
            n = Amount(AmountKind.ANY_NUMBER)
        elif word == "a number of":
            n, pending = None, pending + (("amount", "a number of"),)
        else:
            n = _count(word)
        groups += 1
        if len(group_kinds) > 1:                       # "a flying or ... counter"
            if groups > 1 or n != Amount(AmountKind.LITERAL, n=1):
                return (None, _um("counter_choice_count"), 0, None, (), ())
            kinds, choice = group_kinds, True
        elif n is not None and n.kind is AmountKind.LITERAL:
            kinds += group_kinds * n.n
        else:
            if groups > 1 or amount is not None:
                return (None, _um("counter_mixed_variable"), 0, None, (), ())
            kinds += group_kinds
            amount = n
        pos = m.end()
        nxt = re.match(r"(?:,)? and (?=\S)", t[pos:])
        if nxt is None or choice or _COUNTER_GROUP_RE.match(
                t, pos + nxt.end()) is None:
            break
        if amount is not None or pending:
            return (None, _um("counter_mixed_variable"), 0, None, (), ())
        pos += nxt.end()
    if not groups:
        return (None, _um("counter_no_counter"), 0, None, (), ())
    return (CounterSpec(kinds=kinds, choice=choice), None, pos, amount,
            pending, ())


def parse_counters(text: str, span: Span, *, lemma: str = "") -> SlotResult:
    """A counter phrase in ``text[span]`` ("two +1/+1 counters ...",
    "{e}{e}") as CounterSpec. This is the one counter noun-phrase parser:
    the destination leaf reads entry counters through it."""
    slot, offset = _slot(text, span)
    return _finish(slot, offset, _counters_rel(slot.strip()), lemma)


# ── Keyword abilities a continuous effect or token can carry (CR 702) ──

_PLAIN_KEYWORDS = (
    "first strike", "double strike", "split second", "flying", "deathtouch",
    "lifelink", "trample", "haste", "vigilance", "reach", "menace",
    "hexproof", "indestructible", "defender", "shroud", "intimidate", "fear",
    "flash", "prowess", "infect", "wither", "persist", "undying",
    "horsemanship", "shadow", "flanking", "exalted", "skulk", "changeling",
    "devoid", "decayed", "cascade", "storm", "convoke", "banding", "phasing",
    "riot", "myriad", "melee", "mentor", "training", "provoke", "ascend",
    "rebound", "haunt", "unleash", "evolve", "extort", "dethrone", "soulbond",
    "fuse", "undaunted", "improvise", "assist", "jump-start", "retrace",
    "daybound", "nightbound", "ravenous", "compleated",
    "enlist", "read ahead", "for mirrodin!", "living weapon", "totem armor",
    "umbra armor", "sunburst", "epic", "delve", "affinity for artifacts",
)
_N_KEYWORDS = (
    "toxic", "afflict", "annihilator", "bushido", "rampage", "absorb",
    "afterlife", "fabricate", "modular", "renown", "frenzy", "poisonous",
    "crew", "bloodthirst", "tribute", "vanishing", "fading", "backup",
    "amplify", "graft", "dredge", "ripple", "casualty", "squad", "offspring",
)
# CR 702 keywords whose ability carries a cost. Granted without a printed
# cost ("gains flashback until end of turn") the cost is set by a rider the
# linker reads ("... cost is equal to its mana cost", A8): the item is typed
# and ("cost_rule", <name>) is left pending.
_COSTED_KEYWORDS = (
    "flashback", "escape", "unearth", "embalm", "eternalize", "madness",
    "cycling", "kicker", "buyback", "evoke", "dash", "blitz", "disturb",
    "overload", "ninjutsu", "equip", "echo", "megamorph", "morph",
    "disguise", "foretell", "plot", "bestow", "emerge", "entwine", "miracle",
    "outlast", "scavenge", "transmute", "reconfigure", "surge", "encore",
    "spectacle", "prowl", "mutate",
)
# CR 702.11 / 702.16 qualities a hexproof / protection item may name. The
# vocabulary is closed; a plural noun is a card type or subtype ("from
# artifacts", "from zombies").
_FROM_QUALITY = (
    r"(?:the colou?r of your choice|the chosen colou?r|that colou?r"
    r"|each colou?r|all colou?rs|everything|multicolou?red|monocolou?red"
    r"|colou?rless|(?:white|blue|black|red|green)"
    r"(?: (?:spells|creatures|permanents))?"
    r"|(?!(?:its|this|has|is|was|as|us|always)\b)[a-z]+s)")
# A quality ends at a boundary a later sub-grammar owns; "each color among
# ...", "colorless or from ..." are qualified qualities the leaf does not type.
_FROM_END = (r"(?=$|[,.;)]| and\b| as long as\b| %s)" % DURATION_START)
_KEYWORD_ITEM_RE = re.compile(
    r"(?:(?P<quote>⟨q\d+⟩)"
    r"|(?:protection|hexproof) from (?P<from>%s)%s" % (_FROM_QUALITY, _FROM_END)
    + r"(?P<more>(?: and from %s%s)*)" % (_FROM_QUALITY, _FROM_END)
    + r"|ward (?P<ward>(?:%s)+|\d+)" % _SYM
    + r"|(?P<walk>(?:nonbasic |snow )?(?:land|island|swamp|mountain|forest|plains|desert)walk)"
    r"|(?P<nkw>%s) (?P<n>\d+|x)" % "|".join(re.escape(k) for k in _N_KEYWORDS)
    + r"|(?P<ckw>%s)(?: (?P<ccost>(?:%s)+))?" % (
        "|".join(re.escape(k) for k in sorted(_COSTED_KEYWORDS, key=len,
                                              reverse=True)), _SYM)
    + r"|(?P<plain>%s)(?! from\b)" % "|".join(
        re.escape(k) for k in sorted(set(_PLAIN_KEYWORDS), key=len, reverse=True))
    + r")(?![\w/'+\-])")
_LIST_SEP_RE = re.compile(r"(?:,? and |, )")

KeywordList = Tuple[Tuple[str, Optional[str]], ...]


def _kw_name(printed: str) -> str:
    """Printed keyword -> canonical name (cards.Keyword values where the
    enum has one: 'first strike' -> 'first_strike')."""
    return printed.replace(" ", "_")


def _keyword_list(t: str, pos: int = 0
                  ) -> Tuple[KeywordList, Tuple[str, ...], int,
                             Tuple[Tuple[str, str], ...]]:
    """Greedy keyword list at `pos`: (keywords, quoted spans, end, pending
    cost rules for costed keywords printed without a cost)."""
    kws: list = []
    quotes: list = []
    cost_rules: list = []
    end = pos
    while True:
        m = _KEYWORD_ITEM_RE.match(t, pos)
        if m is None:
            break
        if m.group("quote"):
            quotes.append(m.group("quote"))
        elif m.group("from") is not None:
            head = "protection" if t.startswith("protection", m.start()) \
                else "hexproof"
            kws.append((head, m.group("from")))
            for extra in re.finditer(r" and from (%s)%s" % (_FROM_QUALITY,
                                                             _FROM_END),
                                     m.group("more") or ""):
                kws.append((head, extra.group(1)))
        elif m.group("ward") is not None:
            kws.append(("ward", m.group("ward")))
        elif m.group("walk") is not None:
            kws.append((_kw_name(m.group("walk")), None))
        elif m.group("nkw") is not None:
            kws.append((_kw_name(m.group("nkw")), m.group("n")))
        elif m.group("ckw") is not None:
            name = _kw_name(m.group("ckw"))
            if m.group("ccost"):
                kws.append((name, _braced(m.group("ccost"))))
            else:
                kws.append((name, None))
                cost_rules.append(("cost_rule", name))
        else:
            kws.append((_kw_name(m.group("plain")), None))
        end = m.end()
        sep = _LIST_SEP_RE.match(t, end)
        if sep is None or _KEYWORD_ITEM_RE.match(t, sep.end()) is None:
            break
        pos = sep.end()
    return tuple(kws), tuple(quotes), end, tuple(cost_rules)


# ── Tokens (CR 111) ────────────────────────────────────────────────────

# CR 111.10: predefined tokens, named by their subtype.
PREDEFINED_TOKENS = frozenset({
    "treasure", "food", "gold", "walker", "shard", "clue", "blood",
    "powerstone", "incubator", "map", "junk", "lander", "mutagen"})
_COLOR_WORDS = {"white": "W", "blue": "U", "black": "B", "red": "R",
                "green": "G"}
_CARD_TYPES = ("artifact", "creature", "enchantment", "land", "planeswalker",
               "battle", "kindred", "tribal", "instant", "sorcery")
_SUPERTYPES = ("legendary", "snow", "basic")
# Function words and object states that never name a subtype.
_NOT_A_SUBTYPE = frozenset({
    "to", "the", "of", "that", "this", "and", "or", "with", "for", "each",
    "equal", "copy", "tapped", "untapped", "blocked", "attached", "monstrous",
    "renowned", "level", "day", "night", "target", "your", "its"})
_PT_RE = re.compile(r"^(?P<ps>[+-]?)(?P<p>\d+|x|\*)/(?P<ts>[+-]?)(?P<t>\d+|x|\*)$")
_SUBTYPE_RE = re.compile(r"^[a-z][a-z'\-]*$")
_TOKEN_HEAD_RE = re.compile(
    r"(?:(?P<tname>[^,]+(?:, [^,]+)?), (?=a legendary\b))?"
    r"(?:(?P<count>a number of|that many|%s) )?(?P<body>.*?)\btokens?\b"
    % _COUNT_RE)
# The copied object ends at its first boundary: the except rider, an entry
# rider, a scaler, a following clause or sentence. What follows the object
# (and its except rider) is the entry tail, then `rest`.
_COPY_RE = re.compile(
    r"(?:(?P<count>a number of|that many|%s) )?(?P<tapped>tapped )?tokens? "
    r"that(?:'s| are) (?:a )?cop(?:y|ies) of (?P<obj>.+?)"
    r"(?=,? except\b| that(?:'s| are) tapped\b| tapped\b| for each\b"
    r"| and (?:it|they|that|those|then|you|sacrifice|exile)\b|, then\b|[.;]|$)"
    r"(?:,? except (?P<exc>[^.;]+?)(?= that(?:'s| are) tapped\b|[.;]|$))?"
    % _COUNT_RE)
_TOKEN_TAILS_RE = re.compile(
    r" (?:that's |that are )?(?P<entry>tapped and attacking|tapped)\b")
_NAMED_RE = re.compile(r" (?:named )?(?P<name>⟨n\d+⟩)| named (?P<plain>[a-z][a-z' ,\-]*?)(?= with\b|$|[.,])")


def _type_phrase(words: Tuple[str, ...]):
    """P/T, colours, supertypes, subtypes and card types of a type phrase.
    Returns a dict, or the first word outside the table."""
    out: Dict[str, Any] = {"power": None, "toughness": None, "colors": [],
                           "colorless": False, "supertypes": [],
                           "subtypes": [], "types": [], "pt_raw": None}
    for i, w in enumerate(words):
        m = _PT_RE.match(w)
        if m and out["power"] is None and out["pt_raw"] is None and not (
                out["colors"] or out["subtypes"] or out["types"]):
            if "*" in (m.group("p"), m.group("t")):
                out["pt_raw"] = w
            else:
                out["power"] = _signed(m.group("ps"), m.group("p"))
                out["toughness"] = _signed(m.group("ts"), m.group("t"))
        elif w in _COLOR_WORDS:
            out["colors"].append(_COLOR_WORDS[w])
        elif w == "colorless":
            out["colorless"] = True
        elif w == "and" and out["colors"] and 0 < i < len(words) - 1 \
                and words[i + 1] in _COLOR_WORDS:
            continue
        elif w in _SUPERTYPES:
            out["supertypes"].append(w)
        elif w in _CARD_TYPES:
            out["types"].append(w)
        elif w.endswith("s") and w[:-1] in _CARD_TYPES:      # "creatures"
            out["types"].append(w[:-1])
        elif _SUBTYPE_RE.match(w) and not out["types"]:
            out["subtypes"].append(w)
        else:
            return w
    return out


@lru_cache(maxsize=CACHE_SIZE)
def _token_rel(t: str) -> _Rel:
    m = _COPY_RE.match(t)
    if m:
        amount = _token_count(m.group("count"))
        obj = m.group("obj").strip()
        pending = [("copy_of", obj)] if obj != "~" else []
        if m.group("exc"):
            pending.append(("copy_except", m.group("exc").strip()))
        if m.group("tapped"):
            pending.append(("entry", "tapped"))
        end = m.end()
        tm = _TOKEN_TAILS_RE.match(t, end)
        if tm:
            pending.append(("entry", tm.group("entry")))
            end = tm.end()
        if m.group("count") == "a number of":
            pending.append(("amount", "a number of"))
        copy_of = Ref(RefKind.SELF) if obj == "~" else None
        return (TokenSpec(copy_of=copy_of), None, end, amount,
                tuple(pending), ())
    m = _TOKEN_HEAD_RE.match(t)
    if m is None:
        return (None, _um("token_no_token"), 0, None, (), ())
    pending = []
    words = tuple(m.group("body").split())
    if words[:3] == ("tapped", "and", "attacking"):
        pending.append(("entry", "tapped and attacking"))
        words = words[3:]
    elif words[:1] == ("tapped",):
        pending.append(("entry", "tapped"))
        words = words[1:]
    phrase = _type_phrase(words)
    if isinstance(phrase, str):
        return (None, _um("token_type_phrase", param=phrase), 0, None, (), ())
    fields: Dict[str, Any] = {}
    if not phrase["types"]:
        if (len(phrase["subtypes"]) == 1 and phrase["power"] is None
                and phrase["subtypes"][0] in PREDEFINED_TOKENS):
            fields["predefined"] = phrase["subtypes"][0]
        else:
            return (None, _um("token_no_type"), 0, None, (), ())
    else:
        fields.update(power=phrase["power"], toughness=phrase["toughness"],
                      types=tuple(phrase["types"]),
                      subtypes=tuple(phrase["subtypes"]))
    fields["colors"] = frozenset(phrase["colors"])
    if m.group("tname"):
        fields["name"] = m.group("tname")
    fields["supertypes"] = tuple(phrase["supertypes"])
    if phrase["pt_raw"]:
        pending.append(("characteristic_defined_pt", phrase["pt_raw"]))
    if m.group("count") == "a number of":
        pending.append(("amount", "a number of"))
    end = m.end()
    nm = _NAMED_RE.match(t, end)
    if nm:
        fields["name"] = nm.group("name") or nm.group("plain").strip()
        end = nm.end()
    wm = re.match(r" with ", t[end:])
    if wm:
        kws, quotes, kend, cost_rules = _keyword_list(t, end + wm.end())
        if kws or quotes:
            fields["keywords"] = kws
            pending.extend(("granted", q) for q in quotes)
            pending.extend(cost_rules)
            end = kend
    tm = _TOKEN_TAILS_RE.match(t, end)
    if tm:
        pending.append(("entry", tm.group("entry")))
        end = tm.end()
    return (TokenSpec(**fields), None, end, _token_count(m.group("count")),
            tuple(pending), ())


def _token_count(word: Optional[str]) -> Optional[Amount]:
    if word is None or word == "a number of":
        return None
    if word == "that many":
        return _THAT_MUCH
    return _count(word)


def parse_token(text: str, span: Span, *, lemma: str = "") -> SlotResult:
    """The object of "create" in ``text[span]`` as a TokenSpec; the count
    is `amount`."""
    slot, offset = _slot(text, span)
    return _finish(slot, offset, _token_rel(slot.strip()), lemma)


# ── A19: alternatives ──────────────────────────────────────────────────

_CHOICE_OF_RE = re.compile(
    r"(?:(?:gains?|has|have|puts?|creates?|gets?) )?your choice of (?P<body>.+)$")
_OR_SPLIT_RE = re.compile(r",? or |, ")
_ALT_HEAD_RE = re.compile(r"^(?:a|an|%s|x) " % _COUNT_RE)
# The first boundary after the last option that a trailing sub-grammar owns:
# duration, object ("on it"), scaler, condition, or the clause's end. The
# text from it on is shared by every option (A19), never the last one's.
_ALT_TAIL_RE = re.compile(
    r"(?: %s| as long as\b| on\b| onto\b"
    r"| for each\b| if\b| unless\b| where\b| equal to\b| instead\b"
    r"| under\b| to\b|[.,;])" % DURATION_START)

# Option offsets (start, end) in the stripped text, plus the tail offset.
_AltSplit = Tuple[Tuple[Tuple[int, int], ...], int]


def _or_split(t: str, start: int) -> _AltSplit:
    """Split t[start:] into ', '/' or '-separated options up to the shared
    tail. ((), len(t)) when there is no ' or '."""
    last_or = t.rfind(" or ", start)
    if last_or < 0:
        return ((), len(t))
    tail = _ALT_TAIL_RE.search(t, last_or + len(" or "))
    end = tail.start() if tail else len(t)
    opts = []
    pos = start
    for sep in _OR_SPLIT_RE.finditer(t, start, end):
        if sep.start() > pos:
            opts.append((pos, sep.start()))
        pos = sep.end()
    if end > pos:
        opts.append((pos, end))
    return (tuple(opts), end)


def _split_alternatives(t: str) -> _AltSplit:
    m = _CHOICE_OF_RE.match(t)
    if m:
        return _or_split(t, m.start("body"))
    opts, end = _or_split(t, 0)
    if len(opts) < 2:
        return ((), len(t))
    texts = [t[a:b] for a, b in opts]
    heads = {o.rsplit(" ", 1)[-1] for o in texts}
    if heads <= {"token", "tokens"} or heads <= {"counter", "counters"}:
        if all(_ALT_HEAD_RE.match(o) for o in texts):
            return (opts, end)
    return ((), len(t))


def parse_alternatives(text: str, span: Span) -> Tuple[Span, ...]:
    """A19: the option spans (into ``text``) of "your choice of X or Y" /
    "a Food token or a Treasure token" in ``text[span]``, chosen at
    resolution; () when the slot is one payload. Trailing text a later
    sub-grammar owns (a duration, "on <object>", a scaler) is shared by
    every option and is not part of the last one."""
    slot, offset = _slot(text, span)
    start = offset + len(slot) - len(slot.lstrip())
    opts, _ = _split_alternatives(slot.strip())
    return tuple((start + a, start + b) for a, b in opts)


# ── Modifications (CR 611-613) ─────────────────────────────────────────

_PT_MOD_RE = re.compile(
    r"gets? (?P<ps>[+-])(?P<p>\d+|x)/(?P<ts>[+-])(?P<t>\d+|x)\b")
_BASE_PT_RE = re.compile(
    r"(?:has|have) base power and toughness (?P<p>\d+|x)/(?P<t>\d+|x)\b")
_GAIN_RE = re.compile(r"(?:gains?|has|have) ")
_LOSE_ALL_RE = re.compile(r"loses? all abilities\b")
_LOSE_RE = re.compile(r"loses? ")
_CONTROL_RE = re.compile(r"gains? control of\b")
_SWITCH_RE = re.compile(r"switch(?:es)? (?:~'s|its|their|that creature's) "
                        r"power and toughness\b")
_BECOMES_RE = re.compile(
    r"(?:becomes?|is|are|is still|are still) (?P<article>an? )?")
_IN_ADDITION_RE = re.compile(
    r"(?:,)? in addition to (?:its|their) other (?:colors and )?(?:creature |card )?types\b")
_STILL_LAND_RE = re.compile(r"(?:,)? (?:that's|it's) still a land\b")
_COLOR_OF_CHOICE_RE = re.compile(
    r"becomes? the colou?r (?:or colou?rs )?of your choice\b")
# Longest rows first: a row is a prefix of the rows after it. A row's match
# must end at a boundary a later sub-grammar owns (_PROHIBIT_BOUNDARY_RE);
# anything else ("alone", "except by ...", "by more than one creature", a
# second "or" action) is a qualifier the leaf does not type, so the clause
# is Unmodelled rather than a broader prohibition than the printed rule.
_PROHIBIT_ACTIONS = (
    ("attack or block", ("attack", "block")),
    ("block or be blocked", ("block", "be_blocked")),
    ("be blocked", ("be_blocked",)),
    ("be countered", ("be_countered",)),
    ("be attacked", ("be_attacked",)),
    ("be sacrificed", ("be_sacrificed",)),
    ("be the targets? of", ("be_targeted",)),
    ("become untapped", ("untap",)),
    ("draw cards", ("draw",)),
    ("search libraries", ("search",)),
    ("get counters", ("get_counters",)),
    ("have counters put on (?:it|them)", ("get_counters",)),
    ("attack", ("attack",)),
    ("block", ("block",)),
    ("transform", ("transform",)),
    ("gain life", ("gain_life",)),
    ("activate", ("activate",)),
    ("cast", ("cast",)),
    ("play", ("play",)),
)
_PROHIBIT_ROW_RES = tuple((re.compile(phrase + r"\b"), actions)
                          for phrase, actions in _PROHIBIT_ACTIONS)
_PROHIBIT_BOUNDARY_RE = re.compile(
    r"(?:$|[,.;]| %s"
    r"| as long as\b| unless\b| if\b| while\b| each combat\b"
    r"| and (?!(?:block|attack|be)\b))" % DURATION_START)
# The object of a cast / activate / play / be-targeted prohibition runs to
# the prohibition's boundary.
_PROHIBIT_OBJECT_RE = re.compile(r" ([^,.;]+?)(?= %s|[,.;]|$)" % DURATION_START)
_CANT_RE = re.compile(r"(?:can't|cannot) ")
_LIMIT_RE = re.compile(r"(?:can't|cannot) (?P<act>draw|cast) more than "
                       r"(?P<n>%s) (?:cards?|spells?) each turn\b" % _COUNT_RE)
_UNTAP_RE = re.compile(r"(?:doesn't|don't) untap during (?:its|their) "
                       r"controllers?'s? untap steps?\b")
_REQUIRE_RE = re.compile(r"(?P<act>attacks?|blocks?) each combat if able\b"
                         r"|must be blocked\b")
_PERMIT_FLASH_RE = re.compile(r"(?:you )?may cast (?P<obj>.+?) as though "
                              r"(?:it|they) had flash\b")
_PERMIT_PLAY_RE = re.compile(r"(?:you )?may (?P<act>play|cast) (?P<obj>that card|"
                             r"those cards|it|them|the exiled cards?)\b")
_PREVENT_RE = re.compile(r"prevent all (?P<combat>combat )?damage\b"
                         r"(?: that would be dealt)?")


def _mod(kind: ModKind, action: Optional[str] = None, **data: Any) -> Modification:
    return Modification(kind, action=action,
                        data=tuple(sorted((k, v) for k, v in data.items()
                                          if v is not None)))


_TYPE_PHRASE_STOP_RE = re.compile(
    r"(?: with |,| in addition to | %s| and (?!(?:white|blue|black|red|green)\b)"
    r"|\.|$| that's)" % DURATION_START)


def _type_change_rel(t: str) -> Optional[_Rel]:
    m = _COLOR_OF_CHOICE_RE.match(t)
    if m:
        return (_mod(ModKind.SET_COLORS, choice=True), None, m.end(), None,
                (), ())
    m = _BECOMES_RE.match(t)
    if m is None:
        return None
    # The type phrase runs to its first boundary (with / in addition / a
    # duration / , / end).
    stop = _TYPE_PHRASE_STOP_RE.search(t, m.end())
    words = tuple(t[m.end():stop.start()].split())
    if not words:
        return None
    phrase = _type_phrase(words)
    if isinstance(phrase, str):
        return None
    end = stop.start()
    data: Dict[str, Any] = {}
    if phrase["colors"]:
        data["colors"] = tuple(phrase["colors"])
    if phrase["colorless"]:
        data["colorless"] = True
    kws: KeywordList = ()
    if t.startswith(" with ", end):
        kws, quotes, kend, cost_rules = _keyword_list(t, end + len(" with "))
        if kws and not quotes and not cost_rules:
            end = kend
        else:
            kws = ()
    # Without an article only card types or colours make a type change:
    # "becomes tapped", "becomes blocked", "becomes monstrous" are states.
    if m.group("article") is None and phrase["subtypes"]:
        return None
    if _NOT_A_SUBTYPE.intersection(phrase["subtypes"]):
        return None
    typed = phrase["types"] or phrase["subtypes"] or phrase["supertypes"]
    if not typed and phrase["power"] is None:
        if phrase["colors"] or phrase["colorless"]:
            return (_mod(ModKind.SET_COLORS, **data), None, end, None, (), ())
        return None
    data.update(types=tuple(phrase["types"]) or None,
                subtypes=tuple(phrase["subtypes"]) or None,
                supertypes=tuple(phrase["supertypes"]) or None,
                power=phrase["power"], toughness=phrase["toughness"],
                keywords=kws or None)
    kind = ModKind.SET_TYPES
    am = _IN_ADDITION_RE.match(t, end)
    if am:
        kind, end = ModKind.ADD_TYPES, am.end()
    sm = _STILL_LAND_RE.match(t, end)
    if sm:
        data["still_a_land"] = True
        end = sm.end()
    if not typed:                    # "becomes a 4/4": P/T only
        kind = ModKind.SET_BASE_PT
    return (_mod(kind, **data), None, end, None, (), ())


@lru_cache(maxsize=CACHE_SIZE)
def _modification_rel(t: str) -> _Rel:
    m = _PT_MOD_RE.match(t)
    if m:
        return (_mod(ModKind.MODIFY_PT,
                     power=_signed(m.group("ps"), m.group("p")),
                     toughness=_signed(m.group("ts"), m.group("t"))),
                None, m.end(), None, (), ())
    m = _BASE_PT_RE.match(t)
    if m:
        return (_mod(ModKind.SET_BASE_PT, power=_signed("", m.group("p")),
                     toughness=_signed("", m.group("t"))),
                None, m.end(), None, (), ())
    m = _CONTROL_RE.match(t)
    if m:
        return (_mod(ModKind.SET_CONTROLLER), None, m.end(), None, (), ())
    m = _GAIN_RE.match(t)
    if m:
        kws, quotes, end, cost_rules = _keyword_list(t, m.end())
        if quotes and not kws:
            return (_mod(ModKind.GRANT_ABILITY, abilities=quotes), None, end,
                    None, (), ())
        if kws:
            return (_mod(ModKind.ADD_KEYWORDS, keywords=kws), None, end, None,
                    tuple(("granted", q) for q in quotes) + cost_rules, ())
    m = _LOSE_ALL_RE.match(t)
    if m:
        return (_mod(ModKind.REMOVE_ALL_ABILITIES), None, m.end(), None, (),
                ())
    m = _LOSE_RE.match(t)
    if m:
        kws, quotes, end, _ = _keyword_list(t, m.end())
        if kws and not quotes:
            return (_mod(ModKind.REMOVE_KEYWORDS, keywords=kws), None, end,
                    None, (), ())
    m = _SWITCH_RE.match(t)
    if m:
        return (_mod(ModKind.SWITCH_PT), None, m.end(), None, (), ())
    m = _LIMIT_RE.match(t)
    if m:
        return (_mod(ModKind.LIMIT, action=m.group("act"),
                     max=_count(m.group("n"))), None, m.end(), None, (), ())
    m = _CANT_RE.match(t)
    if m:
        code = "prohibit_action"
        for row_re, actions in _PROHIBIT_ROW_RES:
            am = row_re.match(t, m.end())
            if am is None:
                continue
            end = am.end()
            data: Dict[str, Any] = {}
            if actions in (("cast",), ("activate",), ("play",),
                           ("be_targeted",)):
                obj = _PROHIBIT_OBJECT_RE.match(t, end)
                if obj:
                    data["filter"] = obj.group(1).strip()
                    end = obj.end()
            if _PROHIBIT_BOUNDARY_RE.match(t, end) is None:
                code = "prohibit_qualifier"
                continue
            if len(actions) == 1:
                return (_mod(ModKind.PROHIBIT, action=actions[0], **data),
                        None, end, None, (), ())
            return (_mod(ModKind.PROHIBIT, actions=actions, **data), None,
                    end, None, (), ())
        return (None, _um(code), 0, None, (), ())
    m = _UNTAP_RE.match(t)
    if m:
        return (_mod(ModKind.PROHIBIT, action="untap"), None, m.end(), None,
                (), ())
    m = _REQUIRE_RE.match(t)
    if m:
        act = m.group("act")
        action = ("be_blocked" if act is None
                  else "attack" if act.startswith("attack") else "block")
        return (_mod(ModKind.REQUIRE, action=action), None, m.end(), None,
                (), ())
    m = _PERMIT_FLASH_RE.match(t)
    if m:
        return (_mod(ModKind.PERMIT, action="cast_as_flash",
                     object=m.group("obj")), None, m.end(), None, (), ())
    m = _PERMIT_PLAY_RE.match(t)
    if m:
        return (_mod(ModKind.PERMIT, action=m.group("act"),
                     object=m.group("obj")), None, m.end(), None, (), ())
    m = _PREVENT_RE.match(t)
    if m:
        return (_mod(ModKind.PREVENT_DAMAGE,
                     action="combat" if m.group("combat") else "all"),
                None, m.end(), None, (), ())
    cost = _cost_modifier_rel(t)
    if cost is not None:
        return cost
    changed = _type_change_rel(t)
    if changed is not None:
        return changed
    return (None, _um("modification_unknown"), 0, None, (), ())


def parse_modification(entry: Any, text: str, span: Span, *,
                       lemma: Optional[str] = None) -> SlotResult:
    """A continuous predicate in ``text[span]`` ("gets +2/+2", "gains
    flying", "can't block", "becomes a 3/3 ... creature") as one
    effect_model.Modification.

    `entry` is the lexicon entry; its `mod_kind` is a hint only -- the
    printed predicate decides the kind. `lemma` is the printed lemma
    (default: the entry's)."""
    slot, offset = _slot(text, span)
    return _finish(slot, offset, _modification_rel(slot.strip()),
                   _lemma(entry, lemma))


# ── A8: cost modifiers (CR 601.2f, 602.2b) ─────────────────────────────

_COST_MOD_RE = re.compile(
    r"(?P<subject>.+?) costs? (?P<amt>(?:%s)+) (?P<dir>less|more) to "
    r"(?P<act>activate|cast)\b" % _SYM)


# Closed subject table. A self subject is exactly ~ (L0 has rewritten
# "this spell" and the card's names) / it / this ability; a global subject names spells or abilities under a filter and
# never names the object itself ("this spell", "this <object>'s ...
# ability") and carries no prefix (an ability word "x - ", a cost "{t}:",
# a condition "if ..., "). Anything else is Unmodelled, never widened into a
# static reducer for all spells.
_SELF_SPELL_SUBJECTS = frozenset({"~", "it"})
_SELF_ABILITY_RE = re.compile(r"^this ability$")
_GLOBAL_SUBJECT_RE = re.compile(
    r"^(?!this\b)(?!.*(?:\bthis (?:spell|ability)\b|(?:^|\s)~(?:\s|$)"
    r"| - |[:,]))"
    r"[a-z0-9 ,'/~\-]*?\b(?P<noun>spells?|abilit(?:y|ies))\b[a-z0-9 ,'/~\-]*$")


def _cost_subject_scope(subject: str, act: str) -> Optional[str]:
    if _SELF_ABILITY_RE.match(subject) or (subject == "it"
                                           and act == "activate"):
        return "this_ability"
    if subject in _SELF_SPELL_SUBJECTS:
        return "this_spell"
    g = _GLOBAL_SUBJECT_RE.match(subject)
    if g is None:
        return None
    return "spells" if g.group("noun").startswith("spell") else "abilities"


@lru_cache(maxsize=CACHE_SIZE)
def _cost_modifier_rel(t: str) -> Optional[_Rel]:
    m = _COST_MOD_RE.match(t)
    if m is None:
        return None
    subject = m.group("subject")
    scope = _cost_subject_scope(subject, m.group("act"))
    if scope is None:
        return (None, _um("cost_delta_subject"), 0, None, (), ())
    syms = _symbols(m.group("amt"))
    data: Dict[str, Any] = {"scope": scope, "cost_of": m.group("act"),
                            "sign": -1 if m.group("dir") == "less" else 1}
    if len(syms) == 1 and syms[0].isdigit():
        data["amount"] = Amount(AmountKind.LITERAL, n=int(syms[0]))
    elif syms == ("X",):
        data["amount"] = _X_UNIT
    else:
        data["symbols"] = syms
    if scope in ("spells", "abilities"):
        data["subject"] = subject
    return (_mod(ModKind.COST_DELTA, **data), None, m.end(), None, (), ())


def parse_cost_modifier(text: str, span: Span, *,
                        lemma: str = "") -> Optional[SlotResult]:
    """A8: "This ability costs {N} less to activate ..." (and the spell /
    static forms) in ``text[span]`` as a COST_DELTA Modification; None when
    the slot is no cost modifier. Structure absorbs it into
    `cost_modifiers`."""
    slot, offset = _slot(text, span)
    rel = _cost_modifier_rel(slot.strip())
    return None if rel is None else _finish(slot, offset, rel, lemma)


# ── CR 701 keyword actions ─────────────────────────────────────────────

# name -> parameter shape: 'none', 'n' (a count), 'subtype_n' (amass),
# 'object' (the acted-on object follows, parsed by the caller).
KEYWORD_ACTION_NAMES: Dict[str, str] = {
    "investigate": "none", "proliferate": "none", "populate": "none",
    "explore": "none", "connive": "none", "forage": "none",
    "time travel": "none", "manifest dread": "none", "harness": "none",
    "goad": "object", "suspect": "object", "detain": "object",
    "exert": "object",
    "collect evidence": "n", "discover": "n", "incubate": "n",
    "adapt": "n", "bolster": "n", "support": "n", "monstrosity": "n",
    "fateseal": "n", "endure": "n",
    "amass": "subtype_n",
}
# Recognised CR 701 actions the model does not type (RECOGNIZED_UNSUPPORTED).
UNSUPPORTED_KEYWORD_ACTIONS = (
    "venture into the dungeon", "the ring tempts you", "open an attraction",
    "roll to visit your attractions", "manifest", "cloak", "learn", "clash",
    "vote", "planeswalk", "assemble", "meld", "abandon", "set in motion",
    "take the initiative", "roll a d20", "roll a six-sided die", "flip a coin",
)


def _inflected(name: str) -> str:
    head, _, tail = name.partition(" ")
    pat = re.escape(head) + r"(?:e?s)?"
    return pat + (r" " + re.escape(tail) if tail else "")


_KA_RE = re.compile(r"(?P<name>%s)\b" % "|".join(
    _inflected(n) for n in sorted(KEYWORD_ACTION_NAMES, key=len, reverse=True)))
_KA_UNSUPPORTED_RE = re.compile(r"(?:%s)\b" % "|".join(
    _inflected(n) for n in sorted(UNSUPPORTED_KEYWORD_ACTIONS, key=len,
                                  reverse=True)))
_TIMES_RE = re.compile(r" (?:(?P<twice>twice)|(?P<n>%s) times)\b" % _COUNT_RE)


def _canonical_action(printed: str) -> str:
    for name in KEYWORD_ACTION_NAMES:
        if re.fullmatch(_inflected(name), printed):
            return name
    return printed


def _canonical_unsupported(printed: str) -> str:
    for name in UNSUPPORTED_KEYWORD_ACTIONS:
        if re.fullmatch(_inflected(name), printed):
            return name
    return printed


@lru_cache(maxsize=CACHE_SIZE)
def _keyword_action_rel(t: str) -> _Rel:
    m = _KA_RE.match(t)
    if m is None:
        u = _KA_UNSUPPORTED_RE.match(t)
        if u:
            name = _canonical_unsupported(u.group(0)).replace(" ", "_")
            return (None, _um("keyword_action_unsupported",
                              Stage.RECOGNIZED_UNSUPPORTED, name),
                    0, None, (), ())
        return (None, _um("keyword_action_unknown"), 0, None, (), ())
    name = _canonical_action(m.group("name"))
    shape = KEYWORD_ACTION_NAMES[name]
    end = m.end()
    amount: Optional[Amount] = None
    subtype: Optional[str] = None
    if shape == "subtype_n":
        sm = re.match(r" (?:(?P<sub>[a-z][a-z'\-]*) )?(?P<n>\d+|x)\b", t[end:])
        if sm is None:
            return (None, _um("keyword_action_param"), 0, None, (), ())
        sub = sm.group("sub")
        if sub is not None:
            subtype = sub[:-1] if sub.endswith("s") else sub
        amount = _count(sm.group("n"))
        end += sm.end()
    elif shape == "n":
        nm = re.match(r" (?P<n>%s)\b" % _COUNT_RE, t[end:])
        if nm is None:
            return (None, _um("keyword_action_param"), 0, None, (), ())
        amount = _count(nm.group("n"))
        end += nm.end()
    elif shape == "none":
        tm = _TIMES_RE.match(t, end)
        if tm:
            amount = (Amount(AmountKind.LITERAL, n=2) if tm.group("twice")
                      else _count(tm.group("n")))
            end = tm.end()
    return (KeywordAction(name=name, amount=amount, subtype=subtype), None,
            end, None, (), ())


def parse_keyword_action(text: str, span: Span, *,
                         lemma: str = "") -> SlotResult:
    """A CR 701 keyword action clause in ``text[span]`` ("amass zombies
    2") as KeywordAction. The `expansion` stays empty: the full grammar
    fills it."""
    slot, offset = _slot(text, span)
    return _finish(slot, offset, _keyword_action_rel(slot.strip()), lemma)


# ── PAY and emblems ────────────────────────────────────────────────────

_PAY_COST_RE = re.compile(r"(?:%s)+|\d+ life" % _SYM)
_EMBLEM_RE = re.compile(r"an emblem with (?P<q>⟨q\d+⟩)")


@lru_cache(maxsize=CACHE_SIZE)
def _pay_rel(t: str) -> _Rel:
    energy = _energy_rel(t)
    if energy is not None:
        return energy
    m = _PAY_COST_RE.match(t)
    if m is None:
        return (None, _um("pay_cost"), 0, None, (), ())
    from engine.oracle_parser import parse_activation_cost
    printed = m.group(0)
    snapshot = freeze_cost(parse_activation_cost(
        "pay " + printed if printed.endswith("life") else printed))
    if not _cost_owner_represents(printed, snapshot):
        return (None, _um("pay_cost"), 0, None, (), ())
    return (snapshot, None, m.end(), None, (), ())


_MANA_FIELD = {"W": "white", "U": "blue", "B": "black", "R": "red",
               "G": "green", "C": "colorless"}


def _cost_owner_represents(printed: str, snapshot: CostSnapshot) -> bool:
    """Does the cost owner's snapshot hold exactly the printed payment? A
    symbol it drops (snow) or folds into generic (hybrid, phyrexian), or an
    `unpayable` flag, makes the PAY payload Unmodelled rather than a
    cheaper payment than the printed one."""
    items = dict(snapshot.items)
    if items.get("unpayable"):
        return False
    if printed.endswith("life"):
        return True
    want = {f: 0 for f in _MANA_FIELD.values()}
    want["generic"] = 0
    x_count = 0
    for sym in _symbols(printed):
        if sym.isdigit():
            want["generic"] += int(sym)
        elif sym == "X":
            x_count += 1
        elif sym in _MANA_FIELD:
            want[_MANA_FIELD[sym]] += 1
        else:                        # snow, hybrid, phyrexian
            return False
    mana = dict(items.get("mana", ()))
    return (all(mana.get(f, 0) == n for f, n in want.items())
            and items.get("x_count", 0) == x_count)


def _emblem_rel(t: str) -> _Rel:
    m = _EMBLEM_RE.match(t)
    if m is None:
        return (None, _um("emblem_no_quote"), 0, None, (), ())
    return (Granted(), None, m.end(), None, (("granted", m.group("q")),), ())


# ── Dispatch ───────────────────────────────────────────────────────────

_COUNTER_VERBS = frozenset({Verb.PUT_COUNTERS, Verb.REMOVE_COUNTERS,
                            Verb.MOVE_COUNTERS, Verb.DOUBLE_COUNTERS,
                            Verb.PLAYER_COUNTERS})
_DOUBLE_PREFIX_RE = re.compile(r"the number of (?:each kind of counter\b)?")


def _lemma(entry: Any, lemma: Optional[str]) -> str:
    """The printed lemma: the caller's, else the lexicon entry's
    (section 4: every entry carries its printed `lemma`)."""
    if lemma is not None:
        return lemma
    return getattr(entry, "lemma", "") or ""


def _alternatives(entry: Any, slot: str, offset: int, lemma: str,
                  prefix: str, split: _AltSplit) -> SlotResult:
    """Each option typed on its own text (with the verb `prefix` a
    continuous predicate needs), its span in host coordinates; the shared
    tail is the outer rest and every option's rest (A19)."""
    options, tail_at = split
    lead = len(slot) - len(slot.lstrip())
    t = slot.strip()
    tail = tuple((a + offset, b + offset) for a, b in rest_spans_after(
        slot, lead + tail_at, lead + len(t), " ,.;"))
    results = []
    for a, b in options:
        opt = t[a:b]
        at = offset + lead + a
        # The option is typed as `prefix + opt` placed so that `opt` sits at
        # its host offset; spans before `at` (the prefix) are clamped.
        r = _payload(entry, prefix + opt, at - len(prefix), lemma)
        results.append(dataclasses.replace(
            r, span=(max(r.span[0], at), max(r.span[1], at)),
            rest_spans=r.rest_spans + tail))
    return SlotResult(span=(offset + lead, offset + lead + tail_at),
                      rest_spans=tail, alternatives=tuple(results))


# A disjunction of payloads left after one typed payload: the choice would
# be silently dropped, so the slot is Unmodelled instead.
_LEFTOVER_OR_RE = re.compile(r"^or (?:a|an|%s|x|your choice)\b" % _COUNT_RE)


def _no_dropped_choice(r: SlotResult, slot: str, offset: int, lemma: str,
                       code: str) -> SlotResult:
    if r.value is not None and _LEFTOVER_OR_RE.match(
            join_spans(slot, tuple((a - offset, b - offset)
                                   for a, b in r.rest_spans))):
        body = slot.strip()
        start = offset + len(slot) - len(slot.lstrip())
        return SlotResult(unmodelled=_stamp(_um(code), lemma),
                          span=(start, start + len(body)))
    return r


def parse_payload(entry: Any, text: str, span: Span, facts: Any, *,
                  lemma: Optional[str] = None) -> SlotResult:
    """Type the payload slot ``text[span]`` of a clause whose lexicon entry
    is `entry` (read: `verb`, `lemma`). `text` is the whole normalised host
    text; every returned span indexes it. `lemma` is the printed lemma
    (default: the entry's). `facts` is reserved for face-dependent payloads
    and read by none today."""
    slot, offset = _slot(text, span)
    return _payload(entry, slot, offset, _lemma(entry, lemma))


def _payload(entry: Any, text: str, offset: int, lemma: str) -> SlotResult:
    """`parse_payload` over the slot text `text` sitting at `offset`."""
    verb = getattr(entry, "verb", None)
    t = text.strip()
    lead = len(text) - len(text.lstrip())
    if verb is Verb.ADD_MANA:
        return _finish(text, offset, _mana_rel(t), lemma)
    if verb in _COUNTER_VERBS:
        if verb is Verb.DOUBLE_COUNTERS:
            m = _DOUBLE_PREFIX_RE.match(t)
            if m and m.group(0).endswith("counter"):
                return _finish(text, offset, (CounterSpec(kinds=(WILDCARD,)),
                                              None, m.end(), None, (), ()),
                               lemma)
            if m:
                cut = lead + m.end()
                r = _finish(text[cut:], offset + cut,
                            _counters_rel(text[cut:].strip()), lemma)
                start = offset + lead
                return dataclasses.replace(r, span=(start, r.span[1]))
        split = _split_alternatives(t)
        if split[0]:
            return _alternatives(entry, text, offset, lemma, "", split)
        return _no_dropped_choice(_finish(text, offset, _counters_rel(t), lemma),
                                  text, offset, lemma, "counter_alternative")
    if verb is Verb.CREATE_TOKEN:
        split = _split_alternatives(t)
        if split[0]:
            return _alternatives(entry, text, offset, lemma, "", split)
        return _no_dropped_choice(_finish(text, offset, _token_rel(t), lemma),
                                  text, offset, lemma, "token_alternative")
    if verb is Verb.CONTINUOUS:
        m = _CHOICE_OF_RE.match(t)
        split = _split_alternatives(t) if m else ((), len(t))
        if split[0]:
            verb_word = t.split(" ", 1)[0]
            prefix = (verb_word + " " if verb_word != "your"
                      else lemma + " " if lemma else "")
            return _alternatives(entry, text, offset, lemma, prefix, split)
        return _finish(text, offset, _modification_rel(t), lemma)
    if verb is Verb.KEYWORD_ACTION:
        r = _finish(text, offset, _keyword_action_rel(t), lemma)
        if r.value is None and lemma and not t.startswith(lemma):
            # The action's lemma was consumed by the clause ("amass" before
            # "zombies 2"): parse it re-joined, keep only the slot's part.
            joined = lemma + " " + t
            rel = _keyword_action_rel(joined)
            past_lemma = rel[2] - len(lemma) - 1
            start = offset + lead
            if rel[0] is not None and past_lemma > 0:
                rest = rest_spans_after(t, past_lemma, len(t), " ,.;")
                return SlotResult(
                    value=rel[0], span=(start, start + past_lemma),
                    rest_spans=tuple((a + start, b + start) for a, b in rest))
            if rel[0] is not None:
                return SlotResult(
                    unmodelled=_stamp(_um("keyword_action_nothing_consumed"),
                                      lemma),
                    span=(start, start + len(t)))
        return r
    if verb is Verb.PAY:
        return _finish(text, offset, _pay_rel(t), lemma)
    if verb is Verb.CREATE_EMBLEM:
        return _finish(text, offset, _emblem_rel(t), lemma)
    start = offset + lead
    return SlotResult(span=(start, start),
                      rest_spans=((start, start + len(t)),) if t else ())


def clear_caches() -> None:
    for fn in (_mana_rel, _counters_rel, _token_rel, _modification_rel,
               _cost_modifier_rel, _keyword_action_rel, _pay_rel):
        fn.cache_clear()
