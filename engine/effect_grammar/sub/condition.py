"""Condition sub-grammar (design doc 2026-09-29, section 2 ``Condition``,
section 6 "Condition"; F9, A2, A23, A27, A31).

Types the predicate a clause, a trigger head or an alternative cost is
gated on, once, at LOAD (never at resolution), over L0 output, under the
one leaf contract in `engine.effect_grammar.sub` (``(host, span, *,
lemma="")``, one `SlotResult`, host-absolute spans). The slot is the
condition phrase the frame layer cut: an ``if <COND>`` / ``only if
<COND>`` / ``as long as <COND>`` / ``unless <COND>`` phrase (the connective
is read and consumed) or a bare condition. A leading condition's slot may
run on into the clause it gates ("if you control an artifact, draw a
card"): the condition ends at the comma and the clause is handed on as
``rest_spans``.

**Never conditions** (section 6). `parse_condition` returns None -- the
phrase is the caller's structure, not a predicate -- for performed-gating
("if you do", "if they don't", "if a player does", A14), any "... this
way" result or destination override ("if that spell is countered this
way", A15), a replacement's "would" (CR 614.1a), the requirement rider "if
able" (CR 508.1d) and "for as long as" (the duration leaf's). Trigger-side
filters stay in ``TriggerHead.frequency_raw`` and a reflexive "when you
do, if ..." is the sub-ability head's intervening-if (F9): the caller
passes only the "if ..." part.

**Closed table** (`ConditionKind`, `PREDICATES`):

* STATE -- the game state now: ``count`` (objects a player controls, cards
  in a zone, "there are N <objects>"; the filter leaf's CardFilter holds
  the set with its controller or owner), ``card_types`` (CR 205.2a among a
  graveyard's cards), ``basic_land_types`` (CR 305.6), ``total_power`` /
  ``total_toughness`` of a set (CR 208), ``life_total`` (CR 119), ``poison`` (CR 122.1f), the player designations
  ``monarch`` (CR 724), ``citys_blessing`` (CR 702.131), ``initiative``
  (CR 725), ``completed_dungeon`` (CR 309.7), and ``day`` / ``night`` /
  ``neither_day_nor_night`` (CR 730). The player whose state is read is
  ``payer`` (a player set) or ``ref`` (a player reference); a count's
  player is the filter's controller or owner;
* OBJECT -- an object the ability already knows (``ref``, or a pronoun the
  linker binds): ``is`` (a characteristic, state or controller, as a
  CardFilter with no zone), ``in_zone``, ``power`` / ``toughness`` /
  ``mana_value`` (CR 208, 202.3) and ``counters`` (one kind, CR 122);
* CAST_FACT -- how the spell was cast: ``kicked`` (CR 702.33),
  ``bargained`` (CR 702.166), ``gift_promised`` (CR 702.174),
  ``evidence_collected`` (CR 701.59), ``cast`` / ``cast_from_hand`` /
  ``cast_from_graveyard``, ``mana_spent`` (the printed symbols, a frozen
  CostSnapshot in ``cost``, CR 601.2h), ``mana_spent_total``,
  ``additional_cost_paid`` (CR 601.2f) and ``x`` (the chosen X, CR 107.3);
* TURN -- ``your_turn``, ``not_your_turn`` (A2), ``your_main_phase``;
* HISTORY -- what happened this turn (`HISTORY_PREDS`, the quantity leaf's
  event vocabulary plus ``permanent_left``, revolt). "this turn" closing a
  condition is history and the condition consumes it; it is never left as
  a duration (section 6). The actor is ``payer`` / ``ref``; the object's
  controller at the event is the filter's controller, and the filter names
  no zone (the object as it was at the event);
* RESOLUTION_ORDINAL -- "this is the Nth time this ability has resolved
  this turn": ``resolved`` with ``op`` "==" and ``n`` = N;
* UNLESS -- "unless <player> pays <cost>": ``pays`` with the payer from the
  participant leaf and the cost through the payload leaf's PAY payload,
  i.e. the activation-cost parser (A31); every other "unless <COND>" is
  NOT(COND);
* ALL_OF / ANY_OF / NOT -- conjunctions, disjunctions and negations of the
  above, in ``children``. A conjunct the table refuses refuses the whole
  condition: never a broader condition.

A comparison is ``op`` (`OPS`) and ``n`` (an Amount): "three or more" is
``>=`` 3, "two or fewer" ``<=`` 2, "no" ``==`` 0, a determiner ``>=`` 1,
and a comparison against another quantity ("more lands than you", "less
than or equal to the number of ...") is ``Amount(EQUAL_TO, quantity=Q)``
read through the quantity leaf. A STATE or OBJECT predicate with no
comparison (a designation, ``is``) keeps the default ``op`` and no ``n``.

**References** (rule 0, A23). "~" is ``Ref(SELF)`` and "enchanted /
equipped <noun>" ``Ref(ATTACHED)``; every pronoun or demonstrative, and an
anaphoric player, is left in ``pending`` for the linker exactly as the
participant leaf reports it. CR 608.2h: a past-tense object condition ("if
it was a creature card") reads last-known information: a SELF / ATTACHED
reference gets ``lki=True`` and a pending one the `LKI` flag.

**Ability words** are the caller's ``label`` (CR 207.2c), informational
only. An unknown phrase is ``UNMODELLED(CONDITION)`` over the whole slot
with detail ``condition.<code>[:<param>]`` from `DETAIL_CODES`. The leaf
reads no card name and no game state.
"""
from __future__ import annotations

import dataclasses
import re
from functools import lru_cache
from typing import Optional, Tuple

from engine.effect_grammar.sub import (CACHE_SIZE, SlotResult, Span,
                                       join_spans, rest_spans_after,
                                       unmodelled)
from engine.effect_grammar.sub import amount as _amount
from engine.effect_grammar.sub import filter as _filter
from engine.effect_grammar.sub import participant as _participant
from engine.effect_grammar.sub import payload as _payload
from engine.effect_grammar.sub import quantity as _Q
from engine.effect_model import Selector, SelectorKind
from engine.effect_spec import (Amount, AmountKind, CardFilter, Condition,
                                ConditionKind, CostSnapshot, Quantity,
                                QuantityKind, Ref, RefKind, Stage, Unmodelled,
                                Verb)

__all__ = ["LEAF", "DETAIL_CODES", "PREDICATES", "HISTORY_PREDS", "OPS",
           "LKI", "parse_condition", "clear_caches"]

LEAF = "condition"
DETAIL_CODES = frozenset({
    "empty", "unparsed", "subject", "filter", "quantity", "comparison",
    "quantifier", "object_state", "object_zone", "keyword_fact",
    "cast_fact", "history_event", "history_zone", "turn", "counter",
    "unless_cost", "unless_scaled", "unless_action", "opening_hand",
    "coin_flip", "last_turn", "starting_life", "party", "devotion",
    "targets", "shares", "mana_source", "chosen", "remains", "extremum"})

LKI = _Q.LKI       # SlotResult flag: a pending referent read as it last existed

HISTORY_PREDS = frozenset({
    "cast", "drawn", "discarded", "sacrificed", "died", "entered",
    "life_gained", "life_lost", "descended", "attacked", "permanent_left"})

PREDICATES = {
    ConditionKind.STATE: frozenset({
        "count", "card_types", "basic_land_types", "total_power",
        "total_toughness", "life_total", "poison", "monarch", "citys_blessing", "initiative",
        "completed_dungeon", "day", "night", "neither_day_nor_night"}),
    ConditionKind.OBJECT: frozenset({
        "is", "in_zone", "power", "toughness", "mana_value", "counters"}),
    ConditionKind.CAST_FACT: frozenset({
        "kicked", "bargained", "gift_promised", "evidence_collected", "cast",
        "cast_from_hand", "cast_from_graveyard", "mana_spent",
        "mana_spent_total", "additional_cost_paid", "x"}),
    ConditionKind.TURN: frozenset({"your_turn", "not_your_turn",
                                   "your_main_phase"}),
    ConditionKind.HISTORY: HISTORY_PREDS,
    ConditionKind.RESOLUTION_ORDINAL: frozenset({"resolved"}),
    ConditionKind.UNLESS: frozenset({"pays"}),
    ConditionKind.ALL_OF: frozenset({""}),
    ConditionKind.ANY_OF: frozenset({""}),
    ConditionKind.NOT: frozenset({""}),
}
OPS = frozenset({">=", "<=", "==", ">", "<"})

_DEFAULT_OP = ">="     # Condition.op's schema default (no comparison printed)


def _um(code: str, lemma: str, param: str = "") -> Unmodelled:
    return unmodelled(Stage.CONDITION, lemma, LEAF, code, DETAIL_CODES, param)


# A relative parse of a condition body: (value, failure, pending, flags).
_W = Tuple[Optional[Condition], Optional[Tuple[str, str]],
           Tuple[Tuple[str, str], ...], frozenset]


def _ok(value: Condition, pending=(), flags=frozenset()) -> _W:
    return value, None, tuple(pending), frozenset(flags)


def _fail(code: str, param: str = "") -> _W:
    return None, (code, param), (), frozenset()


def _neg(w: _W, raw: str, negated: bool) -> _W:
    if not negated or w[0] is None:
        return w
    return (Condition(ConditionKind.NOT, children=(w[0],), raw=raw),) + w[1:]


def _code_of(r: SlotResult) -> str:
    """The refusing leaf's detail code, the condition's param."""
    return r.unmodelled.detail.split(".", 1)[1].split(":")[0]


# ── Numbers and comparisons ────────────────────────────────────────────

_WORDS = sorted(_amount.NUMBER_WORDS, key=len, reverse=True)
_NUM = r"(?:\d+|x|%s)(?![\w-])" % "|".join(_WORDS)
_X = Amount(AmountKind.X, n=1)


def _number(word: str) -> Optional[Amount]:
    if word == "x":
        return _X
    if word.isdigit():
        return Amount(AmountKind.LITERAL, n=int(word))
    n = _amount.NUMBER_WORDS.get(word)
    return None if n is None else Amount(AmountKind.LITERAL, n=n)


_ZERO = Amount(AmountKind.LITERAL, n=0)
_ONE = Amount(AmountKind.LITERAL, n=1)

# A comparison before a counted noun phrase: "three or more", "at least
# five", "no", ... (op, word group).
_CMP_PRE_RE = re.compile(
    r"(?:(?P<a>%s) or (?P<adir>more|greater|fewer|less)"
    r"|at least (?P<b>%s)|at most (?P<c>%s)|exactly (?P<d>%s)"
    r"|(?P<edir>more|fewer|less) than (?P<e>%s)|(?P<no>no)) "
    % ((_NUM,) * 5))
_UP = {"more": ">=", "greater": ">=", "higher": ">=", "fewer": "<=",
       "less": "<=", "lower": "<="}
_STRICT = {"more": ">", "greater": ">", "fewer": "<", "less": "<"}


def _cmp_pre(t: str, pos: int = 0):
    """(op, n, end) of a comparison at ``t[pos:]``, or None."""
    m = _CMP_PRE_RE.match(t, pos)
    if m is None:
        return None
    if m.group("no"):
        return "==", _ZERO, m.end()
    if m.group("a"):
        return _UP[m.group("adir")], _number(m.group("a")), m.end()
    if m.group("b"):
        return ">=", _number(m.group("b")), m.end()
    if m.group("c"):
        return "<=", _number(m.group("c")), m.end()
    if m.group("d"):
        return "==", _number(m.group("d")), m.end()
    return _STRICT[m.group("edir")], _number(m.group("e")), m.end()


# A comparison closing a stat or a total: "4 or greater", "less than or
# equal to <Q>", "exactly 20", "x".
_CMP_POST_RE = re.compile(
    r"(?:(?P<a>%s) or (?P<adir>greater|more|higher|less|fewer|lower)"
    r"|(?P<sdir>less|fewer|greater|more) than (?P<oreq>or equal to )?(?P<rhs>.+)"
    r"|exactly (?P<d>%s)|equal to (?P<eq>.+)|(?P<bare>%s))$"
    % (_NUM, _NUM, _NUM))


def _operand(t: str, a: int, b: int):
    """(n, pending, failure) of a comparison operand ``t[a:b]``: a number
    or a quantity (an EQUAL_TO amount) read whole by the quantity leaf."""
    n = _number(t[a:b])
    if n is not None:
        return n, (), None
    q = _Q.parse_quantity(t, (a, b))
    if q.value is None:
        return None, (), ("quantity", _code_of(q))
    if q.rest_spans or q.span != (a, b):
        return None, (), ("quantity", "")
    return Amount(AmountKind.EQUAL_TO, quantity=q.value), q.pending, None


def _cmp_post(t: str, pos: int):
    """(op, n, pending, failure) of the comparison ``t[pos:]``."""
    m = _CMP_POST_RE.fullmatch(t, pos)
    if m is None:
        return None, None, (), ("comparison", "")
    if m.group("a"):
        return _UP[m.group("adir")], _number(m.group("a")), (), None
    if m.group("d"):
        return "==", _number(m.group("d")), (), None
    if m.group("bare"):
        return "==", _number(m.group("bare")), (), None
    if m.group("eq") is not None:
        n, pending, failure = _operand(t, *m.span("eq"))
        return "==", n, pending, failure
    op = _STRICT[m.group("sdir")]
    if m.group("oreq"):
        op += "="
    n, pending, failure = _operand(t, *m.span("rhs"))
    return op, n, pending, failure


# ── Players and objects ────────────────────────────────────────────────

_SELECTOR_PLAYER = {SelectorKind.PLAYER: "you",
                    SelectorKind.OPPONENTS: "opponents",
                    SelectorKind.ALL_PLAYERS: "any"}


class _Player:
    """A printed player: ``selector`` (a set), ``ref`` (a reference) or an
    anaphor (``text`` left to the linker); ``value`` is its spelling in a
    CardFilter controller / owner and a Quantity player."""
    __slots__ = ("selector", "ref", "pending", "text", "value")

    def __init__(self, selector, ref, pending, text):
        self.selector, self.ref, self.pending, self.text = (
            selector, ref, tuple(pending), text)
        if selector is not None:
            self.value = _SELECTOR_PLAYER[selector.kind]
        elif ref is not None:
            self.value = ref
        else:
            self.value = "any"

    def anaphor_pending(self, key: str) -> Tuple[Tuple[str, str], ...]:
        """The pending entries of this player as a set's controller /
        owner (`key`) or a state's player: a reference keeps the
        participant leaf's possessor anaphor, an anaphor is handed on
        whole."""
        if self.selector is None and self.ref is None:
            return ((key, self.text),)
        return self.pending


_POSSESSIVE_PLAYERS = {"your": "you", "their": "they", "his or her": "they"}


def _player(t: str, a: int, b: int) -> Optional[_Player]:
    """The player ``t[a:b]``, or None when it is no player."""
    text = t[a:b]
    if text in _POSSESSIVE_PLAYERS:
        text = _POSSESSIVE_PLAYERS[text]
        r = _participant.parse_participant(text)
    else:
        r = _participant.parse_participant(t, (a, b))
    if r.value is None or _participant.PLAYER not in r.flags:
        return None
    if _participant.ANY in r.flags:
        return None
    v = r.value
    if isinstance(v, Selector):
        return _Player(v, None, r.pending, text)
    if isinstance(v, Ref):
        return _Player(None, v, r.pending, text)
    return _Player(None, None, r.pending, text)


def _object(t: str, a: int, b: int):
    """(ref, pending) of the object reference ``t[a:b]``, or None."""
    r = _participant.parse_participant(t, (a, b))
    if r.value is None or _participant.OBJECT not in r.flags \
            or _participant.GROUP in r.flags or r.rest_spans:
        return None
    if isinstance(r.value, Ref):
        return r.value, r.pending
    return None, r.pending


def _lki(ref: Optional[Ref], pending, past: bool):
    """(ref, flags) of a referent read in the past tense (CR 608.2h)."""
    if not past:
        return ref, frozenset()
    if ref is not None:
        return dataclasses.replace(ref, lki=True), frozenset()
    return ref, frozenset({LKI}) if pending else frozenset()


def _with_player(f: CardFilter, field: str, player: _Player):
    """`f` with its controller / owner set to `player`, or None when the
    noun phrase printed its own."""
    if getattr(f, field) != "any":
        return None
    return dataclasses.replace(f, **{field: player.value})


# ── Counted noun phrases ───────────────────────────────────────────────

# A join of determiner-led noun phrases: "an artifact and an enchantment",
# "a plains or an island".
_NP_JOIN_RE = re.compile(
    r"(?:,? (?P<j>and|or) |, )(?=(?:a|an|another|no|%s)\b)" % _NUM)


def _single_np(t: str, a: int, b: int, zone: str):
    """(op, n, filter, pending, failure) of one counted noun phrase."""
    cmp = _cmp_pre(t, a)
    if cmp is not None:
        op, n, a = cmp
    f = _filter.parse_filter(t, (a, b), zone=zone)
    if f.value is None:
        return None, None, None, (), ("filter", _code_of(f))
    if f.flags:
        return None, None, None, (), ("quantifier", "")
    if cmp is None:
        amount = f.amount
        if amount is None:
            op, n = ">=", _ONE
        elif amount.kind in (AmountKind.LITERAL, AmountKind.X):
            op, n = ">=", amount
        else:
            return None, None, None, (), ("comparison", "")
    elif f.amount is not None:
        return None, None, None, (), ("comparison", "")
    return op, n, f.value, f.pending, None


def _counted(t: str, a: int, b: int, zone: str = ""):
    """``[(op, n, filter, pending)]`` and the join word ('' for one noun
    phrase, 'and' / 'or'), or the failure."""
    op, n, f, pending, failure = _single_np(t, a, b, zone)
    if failure is None:
        return [(op, n, f, pending)], "", None
    joins = [m for m in _NP_JOIN_RE.finditer(t, a, b)]
    words = {m.group("j") for m in joins if m.group("j")}
    if not joins or len(words) != 1:
        return None, "", failure
    items, pos = [], a
    for m in joins + [None]:
        end = b if m is None else m.start()
        op, n, f, pending, fail = _single_np(t, pos, end, zone)
        if fail is not None:
            return None, "", failure
        items.append((op, n, f, pending))
        pos = 0 if m is None else m.end()
    return items, words.pop(), None


def _combine(join: str, children, raw: str) -> Condition:
    if not join:
        return children[0]
    kind = ConditionKind.ALL_OF if join == "and" else ConditionKind.ANY_OF
    return Condition(kind, children=tuple(children), raw=raw)


def _count_condition(t: str, a: int, b: int, field: str,
                     player: Optional[_Player], zone: str = "",
                     pred: str = "count") -> _W:
    """A STATE count over the noun phrase ``t[a:b]``; the player (if any)
    is the filter's ``field`` (controller / owner)."""
    items, join, failure = _counted(t, a, b, zone)
    if failure is not None:
        return _fail(*failure)
    children, pending = [], []
    for op, n, f, p in items:
        if player is not None:
            f = _with_player(f, field, player)
            if f is None:
                return _fail("subject", field)
        children.append(Condition(ConditionKind.STATE, pred=pred, filter=f,
                                  op=op, n=n, raw=f.raw))
        pending.extend(p)
    if player is not None:
        pending.extend(player.anaphor_pending(field))
    return _ok(_combine(join, children, t[a:b]), pending)


# ── Refusals: printed conditions the model cannot state ────────────────

_REFUSALS = tuple((code, re.compile(p)) for code, p in (
    ("opening_hand", r"\bopening hand\b"),
    ("coin_flip", r"\b(?:win|won|lose|lost) (?:the|a) (?:flip|coin flip)\b"
                  r"|\bflips? (?:heads|tails)\b"),
    ("last_turn", r"\blast turn\b"),
    ("starting_life", r"\bstarting life total\b"),
    ("party", r"\bparty\b"),
    ("devotion", r"\bdevotion\b"),
    ("targets", r"(?:^|\b(?:it|~|spell|ability) )targets?\b"),
    ("shares", r"\bshares?\b"),
    ("mana_source", r"\bmana from\b"),
    ("chosen", r"\bchosen\b"),
    ("remains", r"\bremains?\b"),
    ("extremum", r"\b(?:greatest|the least|tied for|the most|the fewest)\b"),
))


# ── Rows ───────────────────────────────────────────────────────────────

_TURN = {
    "it's your turn": "your_turn", "it is your turn": "your_turn",
    "it's not your turn": "not_your_turn", "it isn't your turn": "not_your_turn",
    "it is not your turn": "not_your_turn",
    "it's your main phase": "your_main_phase",
}
_TURN_SHAPE_RE = re.compile(r"^it(?:'s| is| isn't) .*\b(?:turn|phase|step)$")
_DAY_NIGHT = {"it's day": "day", "it's night": "night",
              "it's neither day nor night": "neither_day_nor_night"}

_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
             "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
_ORDINAL_RE = re.compile(
    r"(?:this|it)(?: is|'s) the (?P<o>%s) time"
    r"(?: this ability has resolved this turn)?" % "|".join(_ORDINALS))


def _fixed(t: str) -> Optional[_W]:
    """TURN, day/night and the resolution ordinal: whole-text rows."""
    if t in _TURN:
        return _ok(Condition(ConditionKind.TURN, pred=_TURN[t], raw=t))
    if t in _DAY_NIGHT:
        return _ok(Condition(ConditionKind.STATE, pred=_DAY_NIGHT[t], raw=t))
    m = _ORDINAL_RE.fullmatch(t)
    if m is not None:
        n = Amount(AmountKind.LITERAL, n=_ORDINALS[m.group("o")])
        return _ok(Condition(ConditionKind.RESOLUTION_ORDINAL, pred="resolved",
                             op="==", n=n, raw=t))
    if _TURN_SHAPE_RE.match(t):
        return _fail("turn")
    return None


# Cast facts (how the spell was cast).
_SPELL_REF = r"(?P<ref>~|it|that spell|this spell|the spell|they|them)"
_WAS_FACT_RE = re.compile(
    r"%s(?P<cop> was| wasn't| was not| were| weren't|'s| is| isn't) "
    r"(?P<fact>[a-z]+)(?P<tail>.*)"
    % _SPELL_REF)
_FACT_WORDS = {"kicked": "kicked", "bargained": "bargained"}
# Participles naming a CR 702 cast mechanic the table has no fact for.
_KEYWORD_FACTS = frozenset({
    "foretold", "evoked", "blitzed", "dashed", "plotted", "warped",
    "prototyped", "bestowed", "suspended", "disturbed", "escaped", "spree",
    "overloaded", "emerged", "surged", "spectacled", "prowled", "madnessed",
    "cleaved", "squadded", "offspring", "tributed"})
_CAST_TAIL = {"": "cast", " from your hand": "cast_from_hand",
              " from your graveyard": "cast_from_graveyard",
              " from a graveyard": "cast_from_graveyard"}
_YOU_CAST_RE = re.compile(
    r"you (?P<neg>didn't |did not )?cast %s(?P<tail>.*)" % _SPELL_REF)
_GIFT_RE = re.compile(r"the gift (?P<cop>was|wasn't|was not) promised")
_EVIDENCE_RE = re.compile(r"evidence (?P<cop>was|wasn't|was not) collected")
_COST_PAID_RE = re.compile(
    r"(?P<poss>~'s|its|that spell's|the spell's) "
    r"(?:additional|(?P<kw>[a-z]+)) cost "
    r"(?P<cop>was|wasn't|was not) paid"
    r"|(?P<kw2>[a-z]+) (?P<cop2>was|wasn't|was not) paid")
_MANA_SPENT_RE = re.compile(
    r"(?P<sym>(?:\{[a-z0-9/]+\})+) was spent to cast %s" % _SPELL_REF)
_MANA_TOTAL_RE = re.compile(r"mana was spent to cast %s" % _SPELL_REF)
_X_RE = re.compile(r"x is ")


@dataclasses.dataclass(frozen=True)
class _PayEntry:
    """The lexicon entry the payload leaf's PAY payload reads (verb,
    lemma): the cost of "unless ... pays" and the symbols of "{c} was
    spent" are both printed payments (A31)."""
    verb: Verb = Verb.PAY
    lemma: str = "pay"


_PAY = _PayEntry()


def _negated(cop: Optional[str]) -> bool:
    return bool(cop) and ("n't" in cop or " not" in cop)


def _spell_ref(t: str, m):
    ref_a, ref_b = m.span("ref")
    o = _object(t, ref_a, ref_b)
    if o is None:
        return None, (("ref", t[ref_a:ref_b]),)
    return o


def _cast_fact(t: str) -> Optional[_W]:
    m = _GIFT_RE.fullmatch(t)
    if m is not None:
        return _neg(_ok(Condition(ConditionKind.CAST_FACT, pred="gift_promised",
                                  raw=t)), t, _negated(m.group("cop")))
    m = _EVIDENCE_RE.fullmatch(t)
    if m is not None:
        return _neg(_ok(Condition(ConditionKind.CAST_FACT,
                                  pred="evidence_collected", raw=t)),
                    t, _negated(m.group("cop")))
    m = _COST_PAID_RE.fullmatch(t)
    if m is not None:
        if m.group("kw2") or m.group("kw"):
            return _fail("keyword_fact", m.group("kw2") or m.group("kw"))
        poss = m.group("poss")
        head = poss[:-2] if poss.endswith("'s") else "it"
        o = _object(head, 0, len(head))
        ref, pending = o if o is not None else (None, (("ref", poss),))
        return _neg(_ok(Condition(ConditionKind.CAST_FACT,
                                  pred="additional_cost_paid", ref=ref, raw=t),
                        pending), t, _negated(m.group("cop")))
    m = _MANA_SPENT_RE.fullmatch(t)
    if m is not None:
        pay = _payload.parse_payload(_PAY, t, m.span("sym"), None)
        if not isinstance(pay.value, CostSnapshot) or pay.rest_spans:
            return _fail("cast_fact", "mana")
        ref, pending = _spell_ref(t, m)
        return _ok(Condition(ConditionKind.CAST_FACT, pred="mana_spent",
                             ref=ref, cost=pay.value, raw=t), pending)
    cmp = _cmp_pre(t)
    if cmp is not None or t.startswith("mana was spent"):
        op, n, pos = cmp if cmp is not None else (">=", _ONE, 0)
        m = _MANA_TOTAL_RE.fullmatch(t, pos)
        if m is not None:
            ref, pending = _spell_ref(t, m)
            return _ok(Condition(ConditionKind.CAST_FACT,
                                 pred="mana_spent_total", ref=ref, op=op, n=n,
                                 raw=t), pending)
    if _X_RE.match(t):
        op, n, pending, failure = _cmp_post(t, 5)
        if failure is not None:
            return _fail(*failure)
        return _ok(Condition(ConditionKind.CAST_FACT, pred="x", op=op, n=n,
                             raw=t), pending)
    m = _YOU_CAST_RE.fullmatch(t)
    if m is not None:
        pred = _CAST_TAIL.get(m.group("tail"))
        if pred is None:
            return _fail("cast_fact", "cast")
        ref, pending = _spell_ref(t, m)
        return _neg(_ok(Condition(ConditionKind.CAST_FACT, pred=pred, ref=ref,
                                  raw=t), pending), t, bool(m.group("neg")))
    m = _WAS_FACT_RE.fullmatch(t)
    if m is not None:
        fact, tail = m.group("fact"), m.group("tail")
        if fact == "cast" and tail in _CAST_TAIL:
            pred = _CAST_TAIL[tail]
        elif fact in _FACT_WORDS and not tail:
            pred = _FACT_WORDS[fact]
        elif fact in _FACT_WORDS or fact == "cast":
            return _fail("cast_fact", fact)
        elif fact in _KEYWORD_FACTS:
            return _fail("keyword_fact", fact)
        else:
            return None
        ref, pending = _spell_ref(t, m)
        return _neg(_ok(Condition(ConditionKind.CAST_FACT, pred=pred, ref=ref,
                                  raw=t), pending), t, _negated(m.group("cop")))
    return None


# History (this turn).
_HAVE_NEG = r"(?:haven't|hasn't|didn't|have not|has not|did not)"
_ACTOR_EVENT_RE = re.compile(
    r"(?P<subj>.+?)(?:'ve| have| has)?(?: (?P<neg>%s))? "
    r"(?P<ev>gained|gain|lost|lose|cast|drawn|drew|draw|discarded|discard"
    r"|sacrificed|sacrifice|attacked|attack|descended|descend)"
    r"(?P<rest>(?: .+?)?) this turn" % _HAVE_NEG)
_EVENTS = {"gained": "life_gained", "gain": "life_gained",
           "lost": "life_lost", "lose": "life_lost",
           "cast": "cast", "drawn": "drawn", "drew": "drawn", "draw": "drawn",
           "discarded": "discarded", "discard": "discarded",
           "sacrificed": "sacrificed", "sacrifice": "sacrificed",
           "attacked": "attacked", "attack": "attacked",
           "descended": "descended", "descend": "descended"}
# The zone the filter leaf needs for an event's counted object ("card" has
# no battlefield reading); the event's filter then names no zone.
_EVENT_ZONE = {"cast": "stack", "drawn": "hand", "discarded": "graveyard",
               "sacrificed": ""}
_UNDER = r"(?: under (?P<under>your|an opponent's|your opponents'|their) control)?"
_UNDER_CONTROLLER = {"your": "you", "an opponent's": "opponents",
                     "your opponents'": "opponents", "their": "any"}
_OBJECT_EVENT_RE = re.compile(
    r"(?P<np>.+?) (?P<ev>died|entered(?: the battlefield)?|left the battlefield)"
    r"%s this turn" % _UNDER)
_OBJECT_EVENTS = {"died": "died", "entered": "entered",
                  "entered the battlefield": "entered",
                  "left the battlefield": "permanent_left"}


def _history_np(t: str, a: int, b: int, zone: str):
    """(op, n, filter, pending, failure) of an event's counted object; the
    filter names no zone (the object as it was at the event)."""
    if re.search(r" (?:from|in|into|onto) ", t[a:b]):
        return None, None, None, (), ("history_zone", "")
    op, n, f, pending, failure = _single_np(t, a, b, zone)
    if failure is not None:
        return None, None, None, (), failure
    return op, n, dataclasses.replace(f, zone=""), pending, None


def _actor_event(t: str, m) -> Optional[_W]:
    player = _player(t, *m.span("subj"))
    if player is None:
        return None
    event = _EVENTS[m.group("ev")]
    rest = m.group("rest").strip()
    rest_a = m.end("rest") - len(rest)
    negated = bool(m.group("neg"))
    op, n, filt, pending = ">=", _ONE, None, []
    if event in ("life_gained", "life_lost"):
        cmp = _cmp_pre(t, rest_a) if rest else None
        tail = t[cmp[2]:m.end("rest")] if cmp else rest
        if tail != "life":
            return _fail("history_event", m.group("ev"))
        if cmp is not None:
            op, n, _ = cmp
    elif event == "attacked":
        if rest.startswith("with "):
            op, n, filt, p, failure = _history_np(t, rest_a + 5, m.end("rest"), "")
            if failure is not None:
                return _fail(*failure)
            pending.extend(p)
        elif rest:
            return _fail("history_event", "attacked")
    elif event == "descended":
        if rest:
            return _fail("history_event", "descended")
    else:
        if not rest:
            return _fail("history_event", event)
        op, n, filt, p, failure = _history_np(t, rest_a, m.end("rest"),
                                              _EVENT_ZONE[event])
        if failure is not None:
            return _fail(*failure)
        pending.extend(p)
    payer = player.selector
    ref = player.ref
    pending.extend(player.anaphor_pending("player"))
    value = Condition(ConditionKind.HISTORY, pred=event, payer=payer, ref=ref,
                      filter=filt, op=op, n=n, raw=t)
    return _neg(_ok(value, pending), t, negated)


def _object_event(t: str, m) -> Optional[_W]:
    event = _OBJECT_EVENTS[m.group("ev")]
    a, b = m.span("np")
    o = _object(t, a, b)
    if o is not None:
        if m.group("under"):
            return _fail("history_event", event)
        ref, pending = o
        return _ok(Condition(ConditionKind.HISTORY, pred=event, ref=ref,
                             op=">=", n=_ONE, raw=t), pending)
    op, n, filt, pending, failure = _history_np(t, a, b, "")
    if failure is not None:
        return _fail(*failure)
    if m.group("under"):
        if filt.controller != "any":
            return _fail("subject", "controller")
        filt = dataclasses.replace(filt,
                                   controller=_UNDER_CONTROLLER[m.group("under")])
        if m.group("under") == "their":
            pending = pending + (("controller", "their"),)
    return _ok(Condition(ConditionKind.HISTORY, pred=event, filter=filt, op=op,
                         n=n, raw=t), pending)


def _history(t: str) -> Optional[_W]:
    if not t.endswith(" this turn"):
        return None
    m = _OBJECT_EVENT_RE.fullmatch(t)
    if m is not None:
        return _object_event(t, m)
    m = _ACTOR_EVENT_RE.fullmatch(t)
    if m is not None:
        w = _actor_event(t, m)
        if w is not None:
            return w
    return _fail("history_event")


# Control.
_CONTROL_RE = re.compile(
    r"(?P<subj>.+?) (?P<neg>(?:don't|doesn't|do not|does not|didn't) )?"
    r"(?P<v>controls?|controlled) (?P<obj>.+)")
_MORE_THAN_RE = re.compile(r"(?P<dir>more|fewer) (?P<np>.+?) than (?P<p2>.+)")


def _comparand_count(t: str, m, field: str, zone: str, player: _Player,
                     kind: QuantityKind):
    """The condition "<player> has more <np> than <p2>": op '>' / '<' and a
    quantity comparand over the same set held by p2."""
    p2 = _player(t, *m.span("p2"))
    if p2 is None:
        return _fail("subject", "comparand")
    f = _filter.parse_filter(t, m.span("np"), zone=zone)
    if f.value is None:
        return _fail("filter", _code_of(f))
    if f.amount is not None or f.flags:
        return _fail("comparison")
    mine = _with_player(f.value, field, player)
    theirs = _with_player(f.value, field, p2)
    if mine is None or theirs is None:
        return _fail("subject", field)
    q_player = p2.value if kind is QuantityKind.CARDS_IN else "any"
    q = Quantity(kind, filter=theirs, player=q_player, raw=t[m.start("np"):])
    n = Amount(AmountKind.EQUAL_TO, quantity=q)
    pending = f.pending + player.anaphor_pending(field) + p2.anaphor_pending(field)
    op = ">" if m.group("dir") == "more" else "<"
    return _ok(Condition(ConditionKind.STATE, pred="count", filter=mine,
                         op=op, n=n, raw=t), pending)


def _control(t: str) -> Optional[_W]:
    m = _CONTROL_RE.fullmatch(t)
    if m is None:
        return None
    player = _player(t, *m.span("subj"))
    if player is None:
        return None
    negated = bool(m.group("neg"))
    past = m.group("v") == "controlled"
    a, b = m.span("obj")
    o = _object(t, a, b)
    if o is not None:
        ref, pending = o
        ref, flags = _lki(ref, pending, past)
        f = CardFilter(controller=player.value, raw=t[a:b])
        w = _ok(Condition(ConditionKind.OBJECT, pred="is", ref=ref, filter=f,
                          raw=t), pending + player.anaphor_pending("controller"),
                flags)
        return _neg(w, t, negated)
    mt = _MORE_THAN_RE.fullmatch(t, a)
    if mt is not None:
        return _neg(_comparand_count(t, mt, "controller", "", player,
                                     QuantityKind.COUNT), t, negated)
    w = _count_condition(t, a, b, "controller", player)
    if w[0] is not None:
        for c in _leaves(w[0]):
            if c.filter.zone != "battlefield":
                return _fail("filter", "zone")
    return _neg(w, t, negated)


def _leaves(c: Condition):
    if c.kind in (ConditionKind.ALL_OF, ConditionKind.ANY_OF, ConditionKind.NOT):
        for child in c.children:
            yield from _leaves(child)
    else:
        yield c


# "there is / are ...".
_THERE_RE = re.compile(r"there(?:'s| is| are) (?P<np>.+?)(?P<bf> on the battlefield)?")
_ON_BF_RE = re.compile(r"(?P<np>.+?) (?:is|are) on the battlefield")
_QUANTITY_STATE = {QuantityKind.CARD_TYPES_IN_GRAVEYARD: "card_types",
                   QuantityKind.BASIC_LAND_TYPES: "basic_land_types"}


def _there(t: str) -> Optional[_W]:
    m = _THERE_RE.fullmatch(t) or _ON_BF_RE.fullmatch(t)
    if m is None:
        return None
    a, b = m.span("np")
    cmp = _cmp_pre(t, a)
    if cmp is not None:
        op, n, qa = cmp
        q = _Q.parse_quantity(t, (qa, b))
        if q.value is not None and q.span == (qa, b) and not q.rest_spans:
            v = q.value
            if v.kind in _QUANTITY_STATE:
                return _ok(Condition(ConditionKind.STATE,
                                     pred=_QUANTITY_STATE[v.kind],
                                     filter=v.filter, op=op, n=n, raw=t),
                           q.pending, q.flags)
            if v.kind is QuantityKind.COUNTERS_ON and v.filter is None:
                kind = v.counter_kind or _payload.WILDCARD
                return _ok(Condition(
                    ConditionKind.OBJECT, pred="counters", ref=v.ref,
                    filter=CardFilter(zone="", counters=((kind, True),),
                                      raw=t[qa:b]),
                    op=op, n=n, raw=t), q.pending, q.flags)
    return _count_condition(t, a, b, "controller", None)


# "creatures you control have total power 8 or greater" (CR 208).
_TOTAL_RE = re.compile(
    r"(?P<np>.+?) (?:have|has) total (?P<stat>power|toughness) ")


def _total(t: str) -> Optional[_W]:
    m = _TOTAL_RE.match(t)
    if m is None:
        return None
    f = _filter.parse_filter(t, m.span("np"))
    if f.value is None:
        return _fail("filter", _code_of(f))
    if f.amount is not None or f.flags:
        return _fail("quantifier")
    op, n, pending, failure = _cmp_post(t, m.end())
    if failure is not None:
        return _fail(*failure)
    return _ok(Condition(ConditionKind.STATE, pred="total_" + m.group("stat"),
                         filter=f.value, op=op, n=n, raw=t),
               f.pending + pending)


# Player state ("you have ...", "an opponent has ...").
_HAS_RE = re.compile(
    r"(?P<subj>.+?)(?:'ve| (?P<neg>don't|doesn't|do not|does not))? "
    r"(?P<v>has|have|had|completed) (?P<obj>.+)")
_IS_PLAYER_RE = re.compile(
    r"(?P<subj>.+?)(?:'re| is| are)(?P<neg> not)? (?P<obj>the monarch|poisoned)")
_LIFE_TOTAL_RE = re.compile(r"(?P<poss>your|their|his or her|.+?'s) life total is ")
_ZONE_NP_RE = re.compile(
    r"(?P<np>.+?) in (?:(?P<poss>your|their|his or her) )?"
    r"(?P<zone>hand|graveyard|library)")
_LIFE_RE = re.compile(r"(?P<cmp>.+ )?life")
_MORE_LIFE_RE = re.compile(r"(?P<dir>more|less) life than (?P<p2>.+)")
_MORE_HAND_RE = re.compile(
    r"(?P<dir>more|fewer) (?P<np>cards) in (?:your |their |his or her )?hand"
    r" than (?P<p2>.+)")
_POISON_RE = re.compile(r"(?P<cmp>.+ )?poison counters?")
_DESIGNATIONS = {"the city's blessing": "citys_blessing",
                 "the initiative": "initiative",
                 "a dungeon": "completed_dungeon"}


def _player_state(pred: str, player: _Player, raw: str, op: str = _DEFAULT_OP,
                  n: Optional[Amount] = None, pending=()) -> _W:
    return _ok(Condition(ConditionKind.STATE, pred=pred,
                         payer=player.selector, ref=player.ref, op=op, n=n,
                         raw=raw),
               tuple(pending) + player.anaphor_pending("player"))


def _player_has(t: str) -> Optional[_W]:
    m = _IS_PLAYER_RE.fullmatch(t)
    if m is not None:
        player = _player(t, *m.span("subj"))
        if player is not None:
            if m.group("obj") == "the monarch":
                w = _player_state("monarch", player, t)
            else:
                w = _player_state("poison", player, t, ">=", _ONE)
            return _neg(w, t, bool(m.group("neg")))
    m = _LIFE_TOTAL_RE.match(t)
    if m is not None:
        poss = m.group("poss")
        head = poss[:-2] if poss.endswith("'s") else poss
        player = _player(head, 0, len(head))
        if player is None:
            return _fail("subject", "life")
        op, n, pending, failure = _cmp_post(t, m.end())
        if failure is not None:
            return _fail(*failure)
        return _player_state("life_total", player, t, op, n, pending)
    m = _HAS_RE.fullmatch(t)
    if m is None:
        return None
    player = _player(t, *m.span("subj"))
    if player is None:
        return None
    negated = bool(m.group("neg"))
    a, b = m.span("obj")
    obj = t[a:b]
    if m.group("v") == "completed":
        if obj != "a dungeon":
            return _fail("unparsed", "completed")
        return _player_state("completed_dungeon", player, t)
    if obj in _DESIGNATIONS:
        return _neg(_player_state(_DESIGNATIONS[obj], player, t), t, negated)
    mm = _MORE_LIFE_RE.fullmatch(t, a)
    if mm is not None:
        p2 = _player(t, *mm.span("p2"))
        if p2 is None:
            return _fail("subject", "comparand")
        q = Quantity(QuantityKind.LIFE_TOTAL, player=p2.value, raw=t[mm.start("p2"):])
        op = ">" if mm.group("dir") == "more" else "<"
        return _player_state("life_total", player, t, op,
                             Amount(AmountKind.EQUAL_TO, quantity=q),
                             p2.anaphor_pending("player"))
    mm = _MORE_HAND_RE.fullmatch(t, a)
    if mm is not None:
        return _neg(_comparand_count(t, mm, "owner", "hand", player,
                                     QuantityKind.CARDS_IN), t, negated)
    for rx, pred in ((_LIFE_RE, "life_total"), (_POISON_RE, "poison")):
        mm = rx.fullmatch(t, a)
        if mm is None:
            continue
        if mm.group("cmp"):
            cmp = _cmp_pre(t, a)
            if cmp is None or cmp[2] != mm.end("cmp"):
                n = _number(mm.group("cmp").strip())
                if n is None:
                    return _fail("comparison")
                cmp = ("==", n, mm.end("cmp"))
            op, n, _ = cmp
        else:
            op, n = ">=", _ONE
        return _neg(_player_state(pred, player, t, op, n), t, negated)
    mm = _ZONE_NP_RE.fullmatch(t, a)
    if mm is not None:
        poss = mm.group("poss")
        if poss == "your" and player.value != "you":
            return _fail("subject", "owner")
        return _neg(_count_condition(t, mm.start("np"), mm.end("np"), "owner",
                                     player, zone=mm.group("zone")), t, negated)
    return None


# Objects.
_STAT = r"(?P<stat>power|toughness|mana value)"
_STAT_PRED = {"power": "power", "toughness": "toughness",
              "mana value": "mana_value"}
_POSS_STAT_RE = re.compile(
    r"(?P<poss>its|their|.+?'s) %s (?P<cop>is|was|are|were) " % _STAT)
_HAS_STAT_RE = re.compile(r"(?P<subj>.+?) (?P<v>has|had) %s " % _STAT)
_COUNTERS_RE = re.compile(
    r"(?P<subj>.+?) (?P<v>has|had|doesn't have|didn't have|does not have"
    r"|did not have) (?P<np>.+?) on (?:it|him|her|them|~)")
_KEYWORD_HAS_RE = re.compile(
    r"(?P<subj>.+?) (?P<v>has|had|doesn't have|does not have) (?P<kw>[a-z ]+)")
_COPULA_RE = re.compile(
    r"(?P<subj>.+?)(?P<cop>'s not|'s| is not| isn't| is| was not| wasn't| was"
    r"| are not| aren't| are| were not| weren't| were) (?P<pred>.+)")
_PAST = frozenset({"was", "wasn't", "was not", "were", "weren't", "were not",
                   "had", "didn't have", "did not have"})
_ZONE_RE = re.compile(
    r"in (?P<poss>your|its owner's|their owner's|a|an opponent's) "
    r"(?P<zone>graveyard|hand|library)|(?P<exile>in exile)"
    r"|(?P<bf>on the battlefield)")
_ZONE_OWNER = {"your": "you", "an opponent's": "opponents", "a": "any"}
_COLOR_WORDS = {"white": "W", "blue": "U", "black": "B", "red": "R",
                "green": "G"}


def _subject_object(t: str, m, past: bool):
    o = _object(t, *m.span("subj"))
    if o is None:
        return None
    ref, pending = o
    ref, flags = _lki(ref, pending, past)
    return ref, pending, flags


def _possessor(t: str, poss: str, past: bool):
    head = poss[:-2] if poss.endswith("'s") else poss
    if head in ("its", "their"):
        return None, (("ref", head),), _lki(None, (("ref", head),), past)[1]
    o = _object(head, 0, len(head))
    if o is None:
        return None
    ref, pending = o
    ref, flags = _lki(ref, pending, past)
    return ref, pending, flags


def _adjective(word: str) -> Optional[CardFilter]:
    """The CardFilter of one characteristic or state adjective (the filter
    leaf's closed tables), or None."""
    if word in _filter.STATES:
        return CardFilter(zone="", state=frozenset({word}), raw=word)
    if word in _COLOR_WORDS:
        return CardFilter(zone="", colors=frozenset({_COLOR_WORDS[word]}), raw=word)
    if word in _filter.SUPERTYPES:
        return CardFilter(zone="", supertypes=frozenset({word}), raw=word)
    if word in _filter.CLASSES:
        return CardFilter(zone="", classes=frozenset({word}), raw=word)
    if word == "colorless":
        return CardFilter(zone="", colorless=True, raw=word)
    return None


# The zone the filter leaf reads a "card" noun in when the object's own
# zone is the condition's referent (the CardFilter then names no zone).
_ANY_CARD_ZONE = "exile"


def _object_predicate(t: str, a: int, b: int):
    """(pred, filter, failure) of a copula predicate ``t[a:b]``."""
    pred = t[a:b]
    m = _ZONE_RE.fullmatch(pred)
    if m is not None:
        if m.group("bf"):
            return "in_zone", CardFilter(zone="battlefield", raw=pred), None
        if m.group("exile"):
            return "in_zone", CardFilter(zone="exile", raw=pred), None
        owner = _ZONE_OWNER.get(m.group("poss"))
        if owner is None:
            return None, None, ("object_zone", m.group("poss").split("'")[0])
        return "in_zone", CardFilter(zone=m.group("zone"), owner=owner,
                                     raw=pred), None
    f = _adjective(pred)
    if f is not None:
        return "is", f, None
    words = pred.split(" or ")
    if len(words) > 1:
        # A disjunction of colours or of states is the filter's own
        # disjunctive field ("black or red", "attacking or blocking").
        fs = [_adjective(w) for w in words]
        if all(x is not None and x.colors for x in fs):
            return "is", CardFilter(zone="", colors=frozenset().union(
                *(x.colors for x in fs)), raw=pred), None
        if all(x is not None and x.state for x in fs):
            return "is", CardFilter(zone="", state=frozenset().union(
                *(x.state for x in fs)), raw=pred), None
    if re.match(r"(?:a|an) ", pred):
        if re.search(r" (?:in|from|on|onto) ", pred):
            return None, None, ("object_zone", "")
        r = _filter.parse_filter(t, (a, b), zone=_ANY_CARD_ZONE)
        if r.value is None:
            return None, None, ("filter", _code_of(r))
        if r.flags or r.pending or r.amount != _ONE:
            return None, None, ("filter", "determiner")
        return "is", dataclasses.replace(r.value, zone=""), None
    return None, None, ("object_state", pred.split(" ")[0])


def _object_rows(t: str) -> Optional[_W]:
    m = _POSS_STAT_RE.match(t)
    if m is not None:
        past = m.group("cop") in _PAST
        o = _possessor(t, m.group("poss"), past)
        if o is None:
            return _fail("subject", "stat")
        ref, pending, flags = o
        op, n, p2, failure = _cmp_post(t, m.end())
        if failure is not None:
            return _fail(*failure)
        return _ok(Condition(ConditionKind.OBJECT, pred=_STAT_PRED[m.group("stat")],
                             ref=ref, op=op, n=n, raw=t), pending + p2, flags)
    m = _HAS_STAT_RE.match(t)
    if m is not None:
        o = _subject_object(t, m, m.group("v") in _PAST)
        if o is not None:
            ref, pending, flags = o
            op, n, p2, failure = _cmp_post(t, m.end())
            if failure is not None:
                return _fail(*failure)
            return _ok(Condition(ConditionKind.OBJECT,
                                 pred=_STAT_PRED[m.group("stat")], ref=ref,
                                 op=op, n=n, raw=t), pending + p2, flags)
    m = _COUNTERS_RE.fullmatch(t)
    if m is not None:
        v = m.group("v")
        o = _subject_object(t, m, v in _PAST)
        if o is not None:
            ref, pending, flags = o
            a, b = m.span("np")
            cmp = _cmp_pre(t, a)
            if cmp is not None:
                op, n, a = cmp
            c = _payload.parse_counters(t, (a, b))
            if c.value is None or c.rest_spans or c.amount is not None \
                    or c.value.choice or len(set(c.value.kinds)) != 1:
                return _fail("counter")
            if cmp is None:
                if len(c.value.kinds) != 1:
                    return _fail("comparison")
                op, n = ">=", _ONE
            elif len(c.value.kinds) != 1:
                return _fail("counter")
            f = CardFilter(zone="", counters=((c.value.kinds[0], True),),
                           raw=t[m.start("np"):b])
            return _neg(_ok(Condition(ConditionKind.OBJECT, pred="counters",
                                      ref=ref, filter=f, op=op, n=n, raw=t),
                            pending, flags), t, "n't" in v or " not" in v)
    m = _KEYWORD_HAS_RE.fullmatch(t)
    if m is not None and m.group("kw") in _filter.QUALIFIER_KEYWORDS:
        v = m.group("v")
        o = _subject_object(t, m, v in _PAST)
        if o is not None:
            ref, pending, flags = o
            f = CardFilter(zone="", raw=m.group("kw"), with_keywords=frozenset(
                {m.group("kw").replace(" ", "_")}))
            return _neg(_ok(Condition(ConditionKind.OBJECT, pred="is", ref=ref,
                                      filter=f, raw=t), pending, flags),
                        t, "n't" in v or " not" in v)
    m = _COPULA_RE.fullmatch(t)
    if m is not None:
        cop = m.group("cop").strip()
        o = _subject_object(t, m, cop in _PAST)
        if o is None:
            return None
        ref, pending, flags = o
        pred, f, failure = _object_predicate(t, *m.span("pred"))
        if failure is not None:
            return _fail(*failure)
        return _neg(_ok(Condition(ConditionKind.OBJECT, pred=pred, ref=ref,
                                  filter=f, raw=t), pending, flags),
                    t, _negated(cop) or cop == "'s not")
    return None


# ── The whole body ─────────────────────────────────────────────────────

_ROWS = (_fixed, _cast_fact, _history, _control, _there, _total,
         _player_has, _object_rows)
_JOIN_RE = re.compile(r",? (?P<j>and|or)(?: if)? ")


def _whole(t: str) -> _W:
    if not t:
        return _fail("empty")
    for code, rx in _REFUSALS:
        if rx.search(t):
            return _fail(code)
    for row in _ROWS:
        w = row(t)
        if w is not None:
            return w
    return _fail("unparsed", t.split(" ")[0])


@lru_cache(maxsize=CACHE_SIZE)
def _cond_rel(t: str) -> _W:
    """The condition of the whole of ``t`` (no connective), or its
    failure. A body no row reads whole is tried as a conjunction or
    disjunction of two conditions; a refused half refuses it."""
    w = _whole(t)
    if w[0] is not None or not _JOIN_RE.search(t):
        return w
    for m in _JOIN_RE.finditer(t):
        left, right = t[:m.start()], t[m.end():]
        lw = _cond_rel(left)
        if lw[0] is None:
            continue
        rw = _cond_rel(right)
        if rw[0] is None:
            continue
        kind = ConditionKind.ALL_OF if m.group("j") == "and" else ConditionKind.ANY_OF
        children = []
        for c in (lw[0], rw[0]):
            children.extend(c.children if c.kind is kind else (c,))
        return _ok(Condition(kind, children=tuple(children), raw=t),
                   lw[2] + rw[2], lw[3] | rw[3])
    return w


# ── unless ─────────────────────────────────────────────────────────────

_PAYS_RE = re.compile(r"(?P<payer>.+?) (?:pays|pay) (?P<cost>.+)")
_UNLESS_ACTION_RE = re.compile(
    r"(?P<payer>.+?) (?P<verb>discard|sacrifice|return|exile|tap|untap"
    r"|remove|put|reveal|mill|lose)s? ")
# A cost the payment scales ("{1} for each ...", "{2} more"): no fixed cost.
_SCALED_TAIL_RE = re.compile(r"(?:for each|plus|more|times|equal to|where)\b")
_INSTEAD = "instead"

_U = Tuple[Optional[Condition], Optional[Tuple[str, str]],
           Tuple[Tuple[str, str], ...], frozenset, int]


@lru_cache(maxsize=CACHE_SIZE)
def _unless_rel(t: str) -> _U:
    """The condition of "unless <t>" and the length of ``t`` it consumed
    (a trailing "instead" is the frame's)."""
    m = _PAYS_RE.fullmatch(t)
    if m is not None:
        player = _player(t, *m.span("payer"))
        if player is not None:
            pay = _payload.parse_payload(_PAY, t, m.span("cost"), None)
            if pay.value is None or not isinstance(pay.value, CostSnapshot):
                return None, ("unless_cost", ""), (), frozenset(), 0
            end = pay.span[1]
            tail = join_spans(t, pay.rest_spans) if pay.rest_spans else ""
            if tail and _SCALED_TAIL_RE.match(tail):
                return None, ("unless_scaled", ""), (), frozenset(), 0
            if tail and tail != _INSTEAD:
                return None, ("unless_cost", tail.split(" ")[0]), (), \
                    frozenset(), 0
            value = Condition(ConditionKind.UNLESS, pred="pays",
                              payer=player.selector, ref=player.ref,
                              cost=pay.value, raw=t[:end])
            return (value, None, player.anaphor_pending("player"), frozenset(),
                    end)
    m = _UNLESS_ACTION_RE.match(t)
    if m is not None and _player(t, *m.span("payer")) is not None:
        return None, ("unless_action", m.group("verb")), (), frozenset(), 0
    w = _cond_rel(t)
    if w[0] is None:
        return w + (0,)
    return (Condition(ConditionKind.NOT, children=(w[0],), raw=t),
            None, w[2], w[3], len(t))


# ── Structure the leaf leaves to the caller ────────────────────────────

_CONNECTIVE_RE = re.compile(
    r"(?P<c>for as long as|as long as|only if|if|unless)(?: |$)")
_PERFORMED_RE = re.compile(
    r"(?:you|they|a player|the player|that player|no one|an opponent|it"
    r"|he or she) (?:do|does|don't|doesn't|do not|does not|can't|cannot)"
    r"(?! (?:control|have|has)\b)(?=$|[ ,])")
_STRUCTURAL_RE = re.compile(r"\bthis way\b|\bwould\b")


def _structural(inner: str) -> bool:
    return bool(_PERFORMED_RE.match(inner) or _STRUCTURAL_RE.search(inner)
                or inner == "able" or inner.startswith("able,"))


def parse_condition(host: str, span: Optional[Span] = None, *,
                    lemma: str = "", label: str = "") -> Optional[SlotResult]:
    """The `Condition` of the slot ``host[span]`` (default: the whole
    host), or None when the phrase is structure, not a condition (see the
    module docstring).

    ``lemma`` is the caller's printed lemma; ``label`` the ability word
    the structure layer stripped (CR 207.2c), set on the condition as
    information only. On success ``span`` is the consumed condition
    (connective included), ``rest_spans`` the clause after a leading
    condition's comma or a trailing "instead", ``pending`` the references
    and anaphoric players the linker binds (rule 0, A23), and ``flags`` may
    hold `LKI`. Trailing punctuation is structure, in neither. Otherwise
    the slot is ``UNMODELLED(CONDITION)`` over the whole trimmed slot."""
    a, b = (0, len(host)) if span is None else span
    slot = host[a:b]
    lead = len(slot) - len(slot.lstrip())
    trimmed = slot.strip()
    body = trimmed.rstrip(" .,;")
    start = a + lead
    m = _CONNECTIVE_RE.match(body)
    conn = m.group("c") if m else ""
    if conn == "for as long as":
        return None
    pos = m.end() if m else 0
    inner = body[pos:]
    if conn != "unless" and _structural(inner):
        return None
    ends = [len(body)] + sorted(
        (c.start() for c in re.finditer(", ", body) if c.start() > pos),
        reverse=True)
    failure = None
    for end in ends:
        text = body[pos:end]
        if conn == "unless":
            value, fail, pending, flags, used = _unless_rel(text)
        else:
            value, fail, pending, flags = _cond_rel(text)
            used = len(text)
        if value is None:
            # The last cut tried is the shortest: the phrase up to the
            # first comma, the leading condition's own boundary.
            failure = fail
            continue
        cut = start + pos + used
        consumed = (start, cut)
        value = dataclasses.replace(value, raw=host[start:cut], label=label)
        rest = rest_spans_after(host, cut, start + len(body), " ,")
        return SlotResult(value=value, span=consumed, rest_spans=rest,
                          flags=flags, pending=pending)
    code, param = failure
    return SlotResult(unmodelled=_um(code, lemma, param),
                      span=(start, start + len(trimmed)))


def clear_caches() -> None:
    _cond_rel.cache_clear()
    _unless_rel.cache_clear()
