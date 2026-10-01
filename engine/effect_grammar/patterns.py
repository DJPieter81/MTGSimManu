"""L4 of the clause grammar: the pattern cascade (design doc 2026-09-29,
section 3 "L4, pattern cascade" and the coverage invariant, section 4,
section 5; A13, A16-A19, A21; E0 step 11, the patterns.py part).

`match_clause` types ONE L3 clause, once, at LOAD:

1. **Lemma.** The clause's verb comes from the lexicon leaf
   (`lexicon.find_verb`): its bucket order is the section-4
   disambiguation, so "shuffle <object> into <library>" is MOVE before
   SHUFFLE (A18) and "put ... counters on" is PUT_COUNTERS before a zone
   move. A recognised-unsupported action or a clause with no verb is the
   lexicon's own refusal.
2. **Row.** The verb selects one row of `ROWS` -- the slot skeleton of
   that verb family (an actor and a counted noun; a source, an amount and
   a recipient; an object and a destination PP; a payload; a subject and a
   continuous predicate; ...). The rows are disjoint by verb, so no row
   shadows another; a verb with no row is ``UNMODELLED(CLAUSE)``
   ``patterns.no_row:<verb>``.
3. **Slots.** Each slot is filled by the leaf that owns it, through the
   one leaf contract (`engine.effect_grammar.sub`): a counted target word
   by the target leaf (the solver's requirement, unmodified, F3/F11), any
   other participant by the participant leaf, an untargeted search by the
   filter leaf, counts and scalers by the amount leaf, destinations by the
   destination leaf, durations by the duration leaf, payloads by the
   payload leaf. A leaf's refusal is the clause's refusal, unchanged
   (UNMODELLED of the deepest failure); the spine never re-reads a phrase
   a leaf owns.
4. **Coverage.** Every non-space character of the clause must lie in a
   consumed slot (structure punctuation aside). A leftover word makes the
   clause ``UNMODELLED(CLAUSE)`` ``patterns.unconsumed:<word>`` -- never a
   typed spec that silently ignores printed text.

**Roles.** The subject (text before the verb, a "may" stripped and flagged
optional) is the ACTOR of an acting verb ("each player sacrifices", "target
player draws": `effect_spec.ACTOR_ONLY_VERBS` have no principal), the
``other`` participant of DAMAGE and FIGHT (the source, the first fighter),
and the PRINCIPAL of a continuous predicate ("target creature gets
+2/+2"). A target requirement is never stored on the spec here: the spec's
``target``/``target_slot`` pair is placed by the linker (L5) against the
host's target list, so L4 hands each requirement with its role in
`ClauseMatch.targets`. Anaphors ("it", "the rest") are handed in
``participants`` for L5 to bind.

**Prefix.** L3 hands a gapped, elliptical, union or elided-subject clause
its antecedent's text as ``prefix``. The prefix is parsed with the clause
(so the verb, subject and amount it supplies are typed), but it is not the
clause's printed text: its spans are never consumed for coverage, and a
target it holds is the antecedent's requirement -- recorded as an
``inherited`` role, never a second requirement.

`match_clause` is memoised on ``(text, host_kind, has_x, prefix,
x_defined)`` in a bounded cache (`CLAUSE_CACHE_SIZE`); its spans index the
clause text.
`match_host` runs L2/L3 (`clauses.frame_host`) and L4 over one L1 host and
applies each frame's tokens to its clause specs (condition, unless,
for-each, frame duration, riders, group), returning host-absolute spans.
"""
from __future__ import annotations

import dataclasses
import re
from functools import lru_cache
from typing import Any, List, NamedTuple, Optional, Tuple

from engine.effect_grammar import clauses as _clauses
from engine.effect_grammar import lexicon as _lexicon
from engine.effect_grammar.sub import (POSSESSIVE, SCALED, Span,
                                       possessive_player, unmodelled)
from engine.effect_grammar.sub import amount as _amount
from engine.effect_grammar.sub import dest as _dest
from engine.effect_grammar.sub import duration as _duration
from engine.effect_grammar.sub import filter as _filter
from engine.effect_grammar.sub import participant as _participant
from engine.effect_grammar.sub import payload as _payload
from engine.effect_grammar.sub import target as _target
from engine.effect_model import DurationKind, ModKind, Selector
from engine.effect_spec import (ACTOR_ONLY_VERBS, Amount, AmountKind,
                                CardFilter, Condition, ConditionKind,
                                EffectSpec, HostKind, Ref, Stage, Unmodelled,
                                Verb)
from engine.target_solver import TargetRequirement

__all__ = ["LEAF", "DETAIL_CODES", "ROWS", "CLAUSE_CACHE_SIZE",
           "ClauseMatch", "FrameMatch", "match_clause", "match_host",
           "unconsumed", "clear_caches"]

LEAF = "patterns"
DETAIL_CODES = frozenset({
    "unconsumed",             # a printed word no slot consumed
    "no_row",                 # a lexicon verb with no slot skeleton
    "no_destination",         # a zone move with no destination PP
    "no_noun",                # a counted verb whose counted noun is missing
    "no_count",               # a counted verb with no count or scaler
    "no_recipient",           # damage with no "to <recipient>"
    "possessive_object",      # "target player's graveyard": a zone, not an object
    "duration_on_verb",       # a printed duration on a verb that takes none
    "no_default_duration",    # a continuous spec with no printed duration on a host that has none
    "for_each_uncounted",     # a leading "for each" over a verb with no count
    "search_zone",            # a search of anything but one library
    "amount_operator",        # an arithmetic operator on a printed count
    "subject",                # a subject on a verb that takes none
})


def _um(stage: Stage, lemma: str, code: str, param: str = "") -> Unmodelled:
    return unmodelled(stage, lemma, LEAF, code, DETAIL_CODES,
                      param.replace(" ", "_"))


# ── Output ──────────────────────────────────────────────────────────────

class ClauseMatch(NamedTuple):
    """One typed clause. ``spec`` is the EffectSpec (UNMODELLED on a
    refusal); ``row`` the row that typed it ('' when refused);
    ``consumed`` the slots (name, span); ``targets`` each requirement with
    its role ('principal' | 'actor' | 'other') and printed span;
    ``participants`` the roles L5 binds (an `Anaphor`, or 'inherited' for
    a prefix-supplied participant); ``pending`` the leaves' pending
    references (section 7). Spans index the clause text (match_clause) or
    the host text (match_host)."""
    spec: EffectSpec
    row: str = ""
    lemma: str = ""                  # the printed lemma ('' with none)
    consumed: Tuple[Tuple[str, Span], ...] = ()
    targets: Tuple[Tuple[str, TargetRequirement, Span], ...] = ()
    participants: Tuple[Tuple[str, Any, Optional[Span]], ...] = ()
    pending: Tuple[Tuple[str, str], ...] = ()


class FrameMatch(NamedTuple):
    frame: _clauses.Frame
    clauses: Tuple[ClauseMatch, ...] = ()


class _Refuse(Exception):
    def __init__(self, um: Unmodelled):
        super().__init__(um.detail)
        self.um = um


# ── The clause builder ─────────────────────────────────────────────────

class _M:
    __slots__ = ("h", "off", "lemma", "entry", "entry_amount", "has_x",
                 "xdef", "host_kind", "fields", "consumed", "targets",
                 "parts", "pending", "residue", "flags")

    def __init__(self, h, off, has_x, xdef, host_kind):
        self.h, self.off = h, off
        self.has_x, self.xdef, self.host_kind = has_x, xdef, host_kind
        self.lemma = ""
        self.entry = None
        self.entry_amount: Optional[Amount] = None
        self.fields = {}
        self.consumed: List[Tuple[str, Span]] = []
        self.targets: List[Tuple[str, TargetRequirement, Span]] = []
        self.parts: List[Tuple[str, Any, Optional[Span]]] = []
        self.pending: List[Tuple[str, str]] = []
        self.residue: List[str] = []
        self.flags: set = set()

    def take(self, name: str, span: Span) -> None:
        if span[1] > span[0]:
            self.consumed.append((name, span))

    def refuse(self, stage: Stage, code: str, param: str = ""):
        raise _Refuse(_um(stage, self.lemma, code, param))

    def leaf(self, r):
        """A leaf result: its refusal is the clause's, unchanged (the
        caller's lemma stamped)."""
        if r is not None and r.value is None and r.unmodelled is not None:
            um = r.unmodelled
            raise _Refuse(um if um.lemma == self.lemma or not self.lemma
                          else dataclasses.replace(um, lemma=self.lemma))
        return r


def _trim(h: str, a: int, b: int, chars: str = " ,") -> Span:
    while a < b and h[a] in chars:
        a += 1
    while b > a and h[b - 1] in chars:
        b -= 1
    return a, b


def _first_word(h: str, a: int, b: int) -> str:
    m = re.compile(r"\S+").search(h, a, b)
    return m.group(0) if m else ""


# ── Slots ──────────────────────────────────────────────────────────────

_SET = {"actor": "actor", "other": "other"}


def _slot(m: _M, span: Span, role: str, zone: str = "") -> None:
    """A participant slot: a counted target word through the target leaf,
    anything else through the participant leaf."""
    h = m.h
    a, b = _trim(h, *span)
    if a >= b:
        return
    inherited = b <= m.off
    z = _WHOLE_ZONE.match(h, a, b) if role == "principal" else None
    if z is not None:
        owner, anaphor = possessive_player(z.group("poss"))
        m.fields["filter"] = CardFilter(zone=z.group("zone"), owner=owner,
                                        raw=h[a:b])
        m.fields["amount"] = Amount(AmountKind.WHOLE_ZONE)
        if anaphor:
            m.pending.append(("player", anaphor))
        m.take(role, (a, b))
        return
    if _target.target_words(h, (a, b)):
        r = m.leaf(_target.parse_target(h, (a, b), lemma=m.lemma))
        if r.rest_spans:
            m.refuse(Stage.TARGET, "possessive_object")
        if inherited:
            m.parts.append((role, "inherited", None))
            return
        for req, sp in zip(r.value.requirements, r.value.spans):
            m.targets.append((role, req, sp))
        m.residue.extend(r.value.residue)
        m.take(role, r.span)
        return
    r = m.leaf(_participant.parse_participant(h, (a, b), lemma=m.lemma,
                                              zone=zone))
    m.pending.extend(r.pending)
    if not inherited:
        m.take(role, r.span)
    else:
        m.parts.append((role, "inherited", None))
    for rs in r.rest_spans:
        if h.startswith("from ", rs[0]):
            m.take("source", rs)
    v = r.value
    if isinstance(v, _participant.Anaphor):
        if not inherited:
            m.parts.append((role, v, r.span))
        return
    if isinstance(v, CardFilter):
        if role == "principal":
            # A quantified or bare plural group is the whole set (the
            # principal); a counted one ("a creature", "up to two cards")
            # is a selection of that many within it.
            m.fields["filter"] = v
            if r.amount is None or _filter.EACH in r.flags or \
                    "all" in r.flags:
                m.fields["subject"] = v.as_selector()
            else:
                m.fields.setdefault("amount", r.amount)
            return
        m.fields[_SET[role]] = v.as_selector()
        return
    if isinstance(v, Selector):
        m.fields["subject" if role == "principal" else _SET[role]] = v
        if r.amount is not None and role == "principal":
            m.fields.setdefault("amount", r.amount)
        return
    if isinstance(v, Ref):
        m.fields["ref" if role == "principal" else _SET[role]] = v
        return
    m.refuse(Stage.REFERENCE, "subject", type(v).__name__)


_SCALER_START = (" equal to ", " for each ", " divided ")


def _scaler_cut(h: str, a: int, b: int) -> int:
    """Where a trailing scaler starts in ``h[a:b]`` (b when none)."""
    cut = b
    for w in _SCALER_START:
        i = h.find(w, a, b)
        if 0 <= i < cut:
            cut = i
    return cut


def _scaler(m: _M, a: int, b: int, per: Optional[Amount]) -> Tuple[Optional[Amount], int]:
    """A trailing scaler at ``h[a:b]``: (its amount, the end of what it
    consumed); (None, a) when there is no scaler text."""
    a, b = _trim(m.h, a, b)
    if a >= b:
        return None, a
    r = m.leaf(_amount.parse_scaler(m.h, (a, b), lemma=m.lemma, per=per,
                                    x_bound=m.has_x, x_defined=m.xdef))
    m.take("amount", r.span)
    m.pending.extend(r.pending)
    end = r.rest_spans[0][0] if r.rest_spans else b
    return r.value, end


def _counted(m: _M, a: int, b: int, noun: Optional["re.Pattern[str]"]):
    """A count, then the counted noun: (amount or None for a scaled count,
    end). The noun may come first when the count is a trailing scaler
    ("cards equal to ...", "damage equal to ...")."""
    h = m.h
    a, b = _trim(h, a, b)
    if noun is not None:
        nm = noun.match(h, a, b)
        if nm is not None:
            m.take("noun", (a, nm.end()))
            return None, nm.end()
    if a >= b:
        m.refuse(Stage.AMOUNT, "no_count")
    r = m.leaf(_amount.parse_amount(h, (a, b), lemma=m.lemma,
                                    x_bound=m.has_x, x_defined=m.xdef))
    if any(k == "operator" for k, _ in r.pending):
        m.refuse(Stage.AMOUNT, "amount_operator")
    m.take("amount", r.span)
    amt = None if SCALED in r.flags else r.value
    p = r.rest_spans[0][0] if r.rest_spans else r.span[1]
    if noun is not None:
        nm = noun.match(h, p, b)
        if nm is None:
            m.refuse(Stage.AMOUNT, "no_noun", _first_word(h, p, b))
        m.take("noun", (p, nm.end()))
        p = nm.end()
    return amt, p


# ── Rows (one per verb family; disjoint by verb) ───────────────────────

_CARDS = re.compile(r"cards?\b")
_LIFE = re.compile(r"life\b")
_DAMAGE = re.compile(r"damage\b")
_ACTOR_NOUNS = {Verb.DRAW: _CARDS, Verb.MILL: _CARDS, Verb.GAIN_LIFE: _LIFE,
                Verb.LOSE_LIFE: _LIFE, Verb.SCRY: None, Verb.SURVEIL: None}


def _row_actor_amount(m: _M, a: int, b: int) -> None:
    amt, p = _counted(m, a, b, _ACTOR_NOUNS[m.entry.verb])
    scaled, _ = _scaler(m, p, b, amt)
    amt = scaled if scaled is not None else amt
    if amt is None:
        m.refuse(Stage.AMOUNT, "no_count")
    m.fields["amount"] = amt


def _row_damage(m: _M, a: int, b: int) -> None:
    h = m.h
    amt, p = _counted(m, a, b, _DAMAGE)
    p = _trim(h, p, b)[0]
    if h.startswith(("equal to ", "for each "), p):
        r = m.leaf(_amount.parse_scaler(h, (p, b), lemma=m.lemma, per=amt,
                                        x_bound=m.has_x, x_defined=m.xdef))
        m.take("amount", r.span)
        m.pending.extend(r.pending)
        amt = r.value
        p = r.rest_spans[0][0] if r.rest_spans else b
    if not h.startswith("to ", p):
        if h.startswith("divided ", p):
            m.refuse(Stage.AMOUNT, "no_recipient")
        m.refuse(Stage.CLAUSE, "no_recipient", _first_word(h, p, b))
    m.take("to", (p, p + 2))
    cut = _scaler_cut(h, p + 3, b)
    _slot(m, (p + 3, cut), "principal")
    if cut < b:
        scaled, _ = _scaler(m, cut, b, amt)
        amt = scaled if scaled is not None else amt
    if amt is None:
        m.refuse(Stage.AMOUNT, "no_count")
    m.fields["amount"] = amt


_OBJECT_ZONES = {Verb.DISCARD: "hand", Verb.REVEAL: "hand"}
# A whole zone as the object ("discards their hand", "exile your
# graveyard", "shuffles their graveyard into their library"): every card
# in it (AmountKind.WHOLE_ZONE), owned by the possessive's player.
_WHOLE_ZONE = re.compile(r"(?P<poss>%s) (?P<zone>hand|graveyard)$"
                         % POSSESSIVE)
_WITHOUT_PAYING = re.compile(
    r" without paying (?:its|their|that card's|that spell's|the spell's|"
    r"~'s) mana costs?$")


def _row_object(m: _M, a: int, b: int) -> None:
    h = m.h
    verb = m.entry.verb
    a, b = _trim(h, a, b)
    if verb is Verb.CAST_FREE:
        w = _WITHOUT_PAYING.search(h, a, b)
        if w is None:
            m.refuse(Stage.CLAUSE, "unconsumed", _first_word(h, a, b))
        m.take("without_paying", (w.start() + 1, w.end()))
        b = w.start()
    r = _duration.parse_duration(h, (a, b), lemma=m.lemma) \
        if _duration_in(h, a, b) else None
    if r is not None:
        r = m.leaf(r)
        if verb is not Verb.EXILE or r.value.kind is not DurationKind.UNTIL_LEAVES:
            m.refuse(Stage.DURATION, "duration_on_verb", m.lemma)
        m.fields["duration"] = r.value
        m.take("duration", r.span)
        if r.rest_spans:
            a, b = r.rest_spans[0][0], r.rest_spans[-1][1]
    c = _participant.parse_chooser(h, (a, b), lemma=m.lemma)
    if c is not None:
        c = m.leaf(c)
        m.fields["chooser"] = c.value
        m.take("chooser", c.span)
        if not c.rest_spans:
            return
        a, b = c.rest_spans[0][0], c.rest_spans[-1][1]
    _slot(m, (a, b), "principal", _OBJECT_ZONES.get(verb, ""))


def _row_move(m: _M, a: int, b: int) -> None:
    h = m.h
    a, b = _trim(h, a, b)
    d = _dest.locate_destination(h, (a, b), lemma=m.lemma)
    if d is None:
        m.refuse(Stage.CLAUSE, "no_destination")
    r = m.leaf(_dest.parse_destination(h, d, lemma=m.lemma))
    m.fields["dest"] = r.value
    m.take("dest", r.span)
    m.pending.extend(r.pending)
    _slot(m, (a, d[0]), "principal")


_PREP = re.compile(r"(?:on|onto|from|among) ")


def _row_counters(m: _M, a: int, b: int) -> None:
    h = m.h
    r = m.leaf(_payload.parse_payload(m.entry, h, (a, b), lemma=m.lemma))
    m.fields["payload"] = r.value
    m.take("payload", r.span)
    amt = r.amount
    if not r.rest_spans:
        return
    p, e = r.rest_spans[0][0], r.rest_spans[-1][1]
    pm = _PREP.match(h, p, e)
    if pm is None:
        m.refuse(Stage.CLAUSE, "unconsumed", _first_word(h, p, e))
    m.take("prep", (p, pm.end() - 1))
    cut = _scaler_cut(h, pm.end(), e)
    _slot(m, (pm.end(), cut), "principal")
    if cut < e:
        per = amt if amt is not None else Amount(
            AmountKind.LITERAL, n=len(getattr(r.value, "kinds", ())) or 1)
        amt, _ = _scaler(m, cut, e, per)
    if amt is not None:
        m.fields["amount"] = amt


def _row_payload(m: _M, a: int, b: int) -> None:
    """A verb whose object is its payload: a token, mana, energy, a
    payment, a keyword action, an emblem."""
    h = m.h
    verb = m.entry.verb
    if verb is Verb.KEYWORD_ACTION:
        a = m.fields.pop("_verb_start")
    r = _payload.parse_payload(m.entry, h, (a, b), lemma=m.lemma)
    if r is None:
        m.refuse(Stage.CLAUSE, "no_row", verb.value)
    if r.value is None and r.alternatives:
        alts = []
        for alt in r.alternatives:
            alt = m.leaf(alt)
            alts.append(EffectSpec(verb=verb, payload=alt.value,
                                   amount=alt.amount, span=alt.span,
                                   raw=h[alt.span[0]:alt.span[1]]))
            m.take("payload", alt.span)
        m.fields["alternatives"] = tuple(alts)
        m.take("payload", r.span)
    else:
        r = m.leaf(r)
        m.fields["payload"] = r.value
        m.take("payload", r.span)
    m.pending.extend(kv for kv in r.pending if kv[0] in ("granted", "copy_of"))
    amt = r.amount if r.amount is not None else m.entry_amount
    if r.rest_spans:
        scaled, _ = _scaler(m, r.rest_spans[0][0], r.rest_spans[-1][1],
                            amt if amt is not None else Amount(AmountKind.LITERAL, n=1))
        amt = scaled if scaled is not None else amt
    if amt is not None:
        m.fields["amount"] = amt
    elif verb is Verb.CREATE_TOKEN:
        m.fields["amount"] = Amount(AmountKind.LITERAL, n=1)


def _row_continuous(m: _M, a: int, b: int) -> None:
    h = m.h
    vs = m.fields.pop("_verb_start")
    r = m.leaf(_payload.parse_modification(m.entry, h, (vs, b), lemma=m.lemma))
    mod = r.value
    m.fields["payload"] = mod
    m.take("payload", r.span)
    m.pending.extend(kv for kv in r.pending if kv[0] in ("granted",))
    subj = m.fields.pop("_subject")
    if mod.kind is ModKind.SET_CONTROLLER:
        _slot(m, subj, "actor")
    else:
        _slot(m, subj, "principal")
    if not r.rest_spans:
        _default_duration(m)
        return
    p, e = r.rest_spans[0][0], r.rest_spans[-1][1]
    d = _duration.parse_duration(h, (p, e), lemma=m.lemma) \
        if _duration_in(h, p, e) else None
    if d is not None:
        d = m.leaf(d)
        m.fields["duration"] = d.value
        m.take("duration", d.span)
        rest = [s for s in d.rest_spans]
    else:
        rest = [(p, e)]
        _default_duration(m)
    for s in rest:
        x, y = _trim(h, *s)
        if x >= y:
            continue
        if mod.kind is ModKind.SET_CONTROLLER and "principal" not in \
                [k for k, _ in m.consumed]:
            _slot(m, (x, y), "principal")
            continue
        scaled, _ = _scaler(m, x, y, Amount(AmountKind.LITERAL, n=1))
        if scaled is not None:
            m.fields["amount"] = scaled


def _default_duration(m: _M) -> None:
    d = _duration.default_duration(m.host_kind, Verb.CONTINUOUS)
    if d is None:
        m.refuse(Stage.DURATION, "no_default_duration", m.host_kind.value)
    m.fields["duration"] = d


_SEARCH_RE = re.compile(r"(?P<poss>%s) ?library(?P<more>(?: and(?:/or)? "
                        r"[a-z' ]+?)?) for " % _dest.ZONE_POSSESSIVE)


def _row_search(m: _M, a: int, b: int) -> None:
    h = m.h
    a, b = _trim(h, a, b)
    s = _SEARCH_RE.match(h, a, b)
    if s is None:
        m.refuse(Stage.CLAUSE, "search_zone", _first_word(h, a, b))
    if s.group("more"):
        m.refuse(Stage.CLAUSE, "search_zone", "and")
    m.take("zone", (a, s.end() - 1))
    r = m.leaf(_filter.parse_filter(h, (s.end(), b), lemma=m.lemma,
                                    zone="library"))
    m.fields["filter"] = r.value
    m.fields["amount"] = r.amount if r.amount is not None else Amount(
        AmountKind.LITERAL, n=1)
    m.pending.extend(r.pending)
    m.take("filter", r.span)


_LIBRARY_RE = re.compile(r"(?:%s) ?library$" % _dest.ZONE_POSSESSIVE)
_EXTRA_TURN_RE = re.compile(r"[a-z]+ extra turns?(?: after this one)?$")


def _row_fixed(m: _M, a: int, b: int) -> None:
    """A verb whose object is a fixed phrase: a library shuffle, an extra
    turn, ending the turn."""
    h = m.h
    a, b = _trim(h, a, b)
    if a >= b:
        return
    rx = {Verb.SHUFFLE: _LIBRARY_RE, Verb.EXTRA_TURN: _EXTRA_TURN_RE}.get(
        m.entry.verb)
    if rx is None or not rx.match(h, a, b):
        m.refuse(Stage.CLAUSE, "unconsumed", _first_word(h, a, b))
    m.take("object", (a, b))


# verb -> (row id, row). Disjoint by verb: no row shadows another.
_ROW_FNS = {
    "actor_amount": (_row_actor_amount, (Verb.DRAW, Verb.MILL, Verb.SCRY,
                                         Verb.SURVEIL, Verb.GAIN_LIFE,
                                         Verb.LOSE_LIFE)),
    "damage": (_row_damage, (Verb.DAMAGE,)),
    "object": (_row_object, (Verb.DESTROY, Verb.EXILE, Verb.SACRIFICE,
                             Verb.TAP, Verb.UNTAP, Verb.COUNTER, Verb.DISCARD,
                             Verb.REVEAL, Verb.CAST_FREE, Verb.FIGHT,
                             Verb.TRANSFORM, Verb.COPY, Verb.LOOK,
                             Verb.CHOOSE)),
    "move": (_row_move, (Verb.MOVE,)),
    "counters": (_row_counters, (Verb.PUT_COUNTERS, Verb.REMOVE_COUNTERS,
                                 Verb.DOUBLE_COUNTERS)),
    "payload": (_row_payload, (Verb.CREATE_TOKEN, Verb.ADD_MANA,
                               Verb.PLAYER_COUNTERS, Verb.PAY,
                               Verb.KEYWORD_ACTION, Verb.CREATE_EMBLEM)),
    "continuous": (_row_continuous, (Verb.CONTINUOUS,)),
    "search": (_row_search, (Verb.SEARCH,)),
    "fixed": (_row_fixed, (Verb.SHUFFLE, Verb.EXTRA_TURN, Verb.END_TURN)),
}
ROWS = {v: (row, fn) for row, (fn, verbs) in _ROW_FNS.items() for v in verbs}

_SUBJECT_ROLE = {Verb.DAMAGE: "other", Verb.FIGHT: "other"}
_MAY_RE = re.compile(r"(?:^|(?<= ))may$")
_CAUSATIVE_RE = re.compile(r"you (?P<may>may )?have ")
_DURATION_START_RE = re.compile(r"(?<![\w'])%s" % _duration.DURATION_START)


def _duration_in(h: str, a: int, b: int) -> bool:
    return _DURATION_START_RE.search(h, a, b) is not None


# ── match_clause ───────────────────────────────────────────────────────

def _leftover(h: str, off: int, consumed) -> Tuple[int, int]:
    covered = bytearray(len(h))
    for _, (a, b) in consumed:
        for i in range(max(a, off), min(b, len(h))):
            covered[i] = 1
    for i in range(off, len(h)):
        if not covered[i] and h[i] not in " ,.;:":
            j = i
            while j < len(h) and not h[j].isspace():
                j += 1
            return i, j
    return -1, -1


def _shift(span: Span, d: int) -> Span:
    return (max(span[0] - d, 0), max(span[1] - d, 0))


# The clause memo's bound. A pool pass has ~17k distinct clauses of ~42k;
# at the leaves' CACHE_SIZE the memo kept all of them, 25 MB of specs
# (tracemalloc, a pool pass) -- most of the 40 MB memo budget (design
# section 12). At 4096 it holds 7-8 MB and keeps the repeats that matter
# ("draw a card", "you gain 1 life"); the pool pass stays at 4.5-4.6 s CPU
# (measured 2026-10-01, quiet 4-core box).
CLAUSE_CACHE_SIZE = 1 << 12


@lru_cache(maxsize=CLAUSE_CACHE_SIZE)
def match_clause(text: str, host_kind: HostKind = HostKind.SPELL,
                 has_x: bool = False, *, prefix: str = "",
                 x_defined: Optional[Amount] = None) -> ClauseMatch:
    """Type one L3 clause (see the module docstring). `host_kind` decides
    a continuous spec's unprinted duration (CR 611.2a / 611.3a); `has_x`
    says the host's cost binds X; `x_defined` is the frame's where-X;
    `prefix` is the inherited antecedent text. Spans index `text`."""
    h = prefix + " " + text if prefix else text
    off = len(prefix) + 1 if prefix else 0
    m = _M(h, off, has_x, x_defined, host_kind)
    try:
        row = _match(m, len(h))
    except _Refuse as r:
        return _refused(r.um, text, m.lemma or r.um.lemma)
    i, j = _leftover(h, off, m.consumed)
    if i >= 0:
        return _refused(_um(Stage.CLAUSE, m.lemma, "unconsumed", h[i:j]),
                        text, m.lemma)
    fields = {k: v for k, v in m.fields.items() if not k.startswith("_")}
    spec = EffectSpec(verb=m.entry.verb, flags=frozenset(m.flags),
                      residue=tuple(sorted(set(m.residue))),
                      span=(0, len(text)), raw=text, **fields)
    d = off
    keep = lambda s: s[1] > d
    return ClauseMatch(
        spec=spec, row=row, lemma=m.lemma,
        consumed=tuple((k, _shift(s, d)) for k, s in m.consumed if keep(s)),
        targets=tuple((k, req, _shift(s, d)) for k, req, s in m.targets),
        participants=tuple((k, v, _shift(s, d) if s else None)
                           for k, v, s in m.parts),
        pending=tuple(m.pending))


def _refused(um: Unmodelled, text: str, lemma: str = "") -> ClauseMatch:
    return ClauseMatch(spec=EffectSpec(verb=Verb.UNMODELLED, payload=um,
                                       span=(0, len(text)), raw=text),
                       lemma=lemma)


def _match(m: _M, end: int) -> str:
    h = m.h
    v = _lexicon.find_verb(h, (0, end))
    if v.value is None:
        raise _Refuse(v.unmodelled)
    m.entry = entry = v.value
    m.lemma = entry.lemma
    m.take("verb", v.span)
    vs, ve = v.span
    # The subject: the text before the verb, an optional "may" flagged.
    sa, sb = _trim(h, 0, vs)
    mm = _MAY_RE.search(h, sa, sb)
    if mm is not None:
        m.fields["optional"] = True
        m.take("may", (mm.start(), mm.end()))
        sa, sb = _trim(h, sa, mm.start())
    # The causative "you [may] have <NP> <verb>": the controller has NP
    # perform the action; NP is the clause's subject.
    cm = _CAUSATIVE_RE.match(h, sa, sb)
    if cm is not None and cm.end() < sb:
        if cm.group("may"):
            m.fields["optional"] = True
        m.take("causative", (sa, cm.end()))
        sa = cm.end()
    if v.amount is not None:
        m.entry_amount = v.amount
    if entry.verb not in ROWS:
        m.refuse(Stage.CLAUSE, "no_row", entry.verb.value)
    row, fn = ROWS[entry.verb]
    if entry.verb is Verb.CONTINUOUS:
        m.fields["_verb_start"] = vs
        m.fields["_subject"] = (sa, sb)
        fn(m, ve, end)
        return row
    if entry.verb is Verb.KEYWORD_ACTION:
        m.fields["_verb_start"] = vs
    if sa < sb:
        _slot(m, (sa, sb), _SUBJECT_ROLE.get(entry.verb, "actor"))
    fn(m, ve, end)
    return row


def unconsumed(cm: ClauseMatch, text: str) -> str:
    """The non-space characters of `text` no consumed slot of `cm` covers
    (structure punctuation aside): '' for a fully consumed clause."""
    i, j = _leftover(text, 0, cm.consumed)
    return "" if i < 0 else text[i:]


# ── A host ─────────────────────────────────────────────────────────────

def _host_has_x(host) -> bool:
    """Does the host's cost bind X (a loyalty X, an {X} in its cost)?"""
    lc = getattr(host, "loyalty_cost", None)
    if lc is not None and lc.kind is AmountKind.X:
        return True
    cost = getattr(host, "cost", None)
    if cost is None:
        return False
    items = dict(cost.items)
    return bool(items.get("x_count") or dict(items.get("mana", ())).get("x_count"))


def _shift_cm(cm: ClauseMatch, d: int) -> ClauseMatch:
    """The slot spans of a memoised clause match placed at host offset `d`
    (the spec's own span and group are set by `_apply_frame`)."""
    if not d:
        return cm
    mv = lambda s: (s[0] + d, s[1] + d)
    return cm._replace(
        consumed=tuple((k, mv(x)) for k, x in cm.consumed),
        targets=tuple((k, r, mv(x)) for k, r, x in cm.targets),
        participants=tuple((k, v, mv(x) if x else None)
                           for k, v, x in cm.participants))


def _lower(spec: EffectSpec, um: Unmodelled) -> EffectSpec:
    return EffectSpec(verb=Verb.UNMODELLED, payload=um, span=spec.span,
                      raw=spec.raw, group=spec.group)


def _apply_frame(cm: ClauseMatch, f, c, text: str) -> ClauseMatch:
    """The frame's tokens on one clause spec: its condition and unless,
    a leading for-each, a frame duration, the group and the gap / reveal /
    rider flags. One `replace` per spec."""
    spec = cm.spec
    lemma = cm.lemma
    base = dict(span=c.span, raw=text, group=c.group)
    if f.unmodelled:
        um = f.unmodelled[0][0]
        if not um.lemma and lemma:
            um = dataclasses.replace(um, lemma=lemma)
        return cm._replace(spec=EffectSpec(verb=Verb.UNMODELLED, payload=um,
                                           **base))
    if spec.verb is Verb.UNMODELLED:
        return cm._replace(spec=dataclasses.replace(spec, **base))
    ch = base
    conds = [x for x in (f.condition, f.unless) if x is not None]
    if conds:
        ch["condition"] = conds[0] if len(conds) == 1 else Condition(
            ConditionKind.ALL_OF, children=tuple(conds),
            raw="; ".join(x.raw for x in conds))
    if f.for_each is not None:
        a = spec.amount
        if a is None or a.kind is not AmountKind.LITERAL:
            return cm._replace(spec=EffectSpec(
                verb=Verb.UNMODELLED, payload=_um(
                    Stage.ITERATION, lemma, "for_each_uncounted"), **base))
        ch["amount"] = Amount(AmountKind.FOR_EACH, n=a.n,
                              quantity=f.for_each.quantity)
    if f.duration is not None:
        if spec.verb is not Verb.CONTINUOUS:
            return cm._replace(spec=EffectSpec(
                verb=Verb.UNMODELLED, payload=_um(
                    Stage.DURATION, lemma, "duration_on_verb", lemma),
                **base))
        if not any(k == "duration" for k, _ in cm.consumed):
            ch["duration"] = f.duration
    flags = set(spec.flags)
    if c.gap in ("verb", "damage", "recipient"):
        flags.add("gapped")
    if "reveal" in c.flags:
        flags.add("reveal")
    flags |= _rider_flags(spec, f.riders)
    if flags != spec.flags:
        ch["flags"] = frozenset(flags)
    return cm._replace(spec=dataclasses.replace(spec, **ch))


def _rider_flags(spec: EffectSpec, riders) -> set:
    """The spec flags a frame's absorbed riders set: "can't be
    regenerated" on its destroy specs (CR 701.15)."""
    out = set()
    for name, _v in riders:
        if name == "no_regeneration" and spec.verb is Verb.DESTROY:
            out.add("no_regeneration")
    return out


def _inherit_durations(f, cms: List[ClauseMatch]) -> None:
    """A trailing duration printed once after coordinated predicates of one
    subject ("gets +1/+1 and gains flying until end of turn") is each
    predicate's duration: it flows back from a clause to the one before it
    while the later clause inherits the earlier one's subject."""
    later = None
    for i in range(len(cms) - 1, -1, -1):
        cm, c = cms[i], f.clauses[i]
        s = cm.spec
        if s.verb is not Verb.CONTINUOUS:
            later = None
            continue
        if any(k == "duration" for k, _ in cm.consumed):
            later = s.duration
        elif later is not None:
            cms[i] = cm._replace(spec=dataclasses.replace(s, duration=later))
        if c.gap != "subject":
            later = None


def _frame_match(t: str, f, host_kind: HostKind, has_x: bool) -> FrameMatch:
    cms: List[ClauseMatch] = []
    for c in f.clauses:
        text = t[c.span[0]:c.span[1]]
        prefix = " ".join(t[a:b] for a, b in c.prefix)
        cm = match_clause(text, host_kind, has_x, prefix=prefix,
                          x_defined=f.where_x)
        cms.append(_apply_frame(_shift_cm(cm, c.span[0]), f, c, text))
    _inherit_durations(f, cms)
    if not f.clauses and f.unmodelled:
        # A frame refused before L3 cut a clause: one UNMODELLED spec over
        # the refused span, stamped with the lemma the lexicon reads there.
        um, span = f.unmodelled[0]
        v = _lexicon.find_verb(t, span)
        lemma = v.value.lemma if v.value is not None else ""
        if lemma and not um.lemma:
            um = dataclasses.replace(um, lemma=lemma)
        cms.append(ClauseMatch(spec=EffectSpec(
            verb=Verb.UNMODELLED, payload=um, span=span,
            raw=t[span[0]:span[1]]), lemma=lemma))
    return FrameMatch(frame=f, clauses=tuple(cms))


def match_host(host, has_x: bool = False) -> Tuple[FrameMatch, ...]:
    """L2-L4 over one L1 host (its modes are hosts of their own): one
    FrameMatch per frame of `clauses.frame_host`, clause specs in printed
    order with host spans."""
    t = host.text
    has_x = has_x or _host_has_x(host)
    return tuple(_frame_match(t, f, host.kind, has_x)
                 for f in _clauses.frame_host(host))


def clear_caches() -> None:
    match_clause.cache_clear()
