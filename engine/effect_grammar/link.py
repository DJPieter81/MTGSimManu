"""L5 of the clause grammar: linking (design doc 2026-09-29, section 3
"L5, link", section 7 "References, links and riders"; A14-A16, A23-A30,
A32-A34, M2, M8; E0 step 12).

L5 reads one L1 host and its L2-L4 output (`patterns.match_host`) once, at
LOAD, and builds the host's `AbilityEffects`: the specs in printed order
with their seqs, targets, references, branches and sub-abilities. It types
no phrase itself -- every phrase was typed by the leaf that owns it -- and
only places what the earlier layers handed on:

1. **seq.** Every spec of the host, sub-ability hosts included, gets the
   next seq in printed order (one counter per ability, so a sub-ability's
   RESULT ref to its parent's spec always points backwards, A34).
2. **Sub-abilities first (A30).** A reflexive opener ("when you do[, if
   C]", CR 603.12) absorbs the rest of the ability -- a mode is a host of
   its own, so it stops at the end of its mode (M8) -- and its
   CREATE_TRIGGER goes in the previous spec's ``then``. A delayed opener
   (CR 603.7) absorbs a later sentence only when that sentence's
   references or connective bind to the delayed specs' results; one that
   binds to both the delayed part and the immediate part is
   UNMODELLED(DELAY). Each sub host owns its targets and its head
   (``TriggerHead`` with the reflexive intervening-if, CR 603.4).
3. **Targets.** Each host collects its own requirements in printed order
   (``mode_group=1`` stamped inside a modal block, CR 700.2) and places
   ``target``/``target_slot`` on the principal; a targeted actor or source
   is ``Ref(TARGET, k)``. A clause with two principal requirements ("exile
   target creature and target artifact") is one simultaneous sibling per
   requirement, in one group.
4. **References (section 7).** Rule 0 (A23): a pronoun in a frame
   modifier of the spec (condition, unless, equal-to, where-X) binds to
   the spec's own principal -- ``Ref(TARGET, k)``, its ref, or
   ``Ref(MEMBER)`` for a quantified subject. Otherwise the nearest
   compatible earlier mention (any participant slot of an earlier spec:
   principal, ``other``, ``actor``, condition subject; A24), where a
   zone-changed object is the moving spec's RESULT (CR 400.7, A25: the
   TARGET and RESULT of one spec are one candidate), searching this
   host, then the host that created it. Then the host antecedent: the
   trigger head's object (self-only head SELF, any other object event
   EVENT_OBJECT -- a mixed "~ or another" head included, M2 -- player
   heads EVENT_PLAYER), or SELF for an ACTIVATED / STATIC / LOYALTY "it";
   a SPELL has none. Two equally near candidates (simultaneous siblings)
   or none is UNMODELLED(REFERENCE) -- never a guess. "the exiled card" is
   an in-ability EXILE's RESULT before it is LINKED (A26, CR 607).
5. **Connectives (A14).** "if you do" nests every clause of its sentence
   in the previous (or named) spec's ``then``; "if you don't [<VP>]" and
   "otherwise" in its ``otherwise``.
6. **May-scope (A29).** In the sentence of an optional head, a follower
   that binds to the head's results, shuffles the library it searched, or
   only inherits its "may" subject nests under the head (flag
   ``may_scope``, CR 608.2d); a REST of an earlier result stays a sibling.
7. **Instead (A15, G9).** An instead clause ``replaces`` the nearest
   earlier spec with its verb (and that spec's simultaneous siblings),
   inherits the arguments it does not restate, and records a restated
   target as ``target_alts``; the instead-of form folds into the named
   action's ``dest`` (flag ``dest_override``, CR 701.5a).
8. **Results (A28).** A RESULT ref names the producing seq; REST / OTHER
   / ONE parts stay resolution-time set differences; a result of a
   multi-player actor read by a spec of the same actor is ``per_actor``
   (CR 101.4).
9. **Granted hosts.** Each ``⟨qk⟩`` a token, an emblem or a grant prints is
   parsed recursively into its own hosts (CR 113.1a): a token's go in
   ``TokenSpec.granted``, an emblem's in ``Granted.hosts``, a continuous
   grant's as the ``granted`` entry of its Modification.
10. **Last-known information (A27, CR 608.2h).** A SELF ref of an ability
    whose cost sacrifices or exiles its source, or whose head is the
    source leaving, reads last-known information; so does the operand of
    "its controller" after the object left.
11. **Validation.** Every spec passes `effect_spec.validate_spec` with its
    creating hosts in view; a violation lowers that spec alone to
    UNMODELLED(INVALID) (`enforce_invariants`), a linking failure to
    UNMODELLED(REFERENCE) / (DELAY).

The face parse is a pure function of ``(text, facts, face)`` -- the
complete fact key (A32) -- memoised in a bounded memo (`FACE_CACHE_SIZE`).
"""
from __future__ import annotations

import dataclasses
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from engine.effect_grammar import lexicon as _lexicon
from engine.effect_grammar import normalize as _normalize
from engine.effect_grammar import patterns as _patterns
from engine.effect_grammar import structure as _structure
from engine.effect_grammar.sub import filter as _filter
from engine.effect_grammar.sub import participant as _participant
from engine.effect_grammar.sub import unmodelled
from engine.effect_model import Modification, ModKind, Selector, SelectorKind
from engine.effect_spec import (AbilityEffects, Amount, AmountKind,
                                CardFilter, Condition, ConditionKind,
                                EffectSpec, EventHint, Granted, HostKind,
                                Quantity, QuantityKind, Ref, RefKind,
                                RefPart, Stage, SubAbility, SubAbilityKind,
                                TokenSpec, TriggerHead, Unmodelled, Verb,
                                enforce_invariants, iter_specs,
                                validate_spec)
from engine.target_solver import TargetRequirement

__all__ = ["LEAF", "DETAIL_CODES", "FACE_CACHE_SIZE", "link_host",
           "parse_face_hosts", "clear_caches"]

LEAF = "link"
DETAIL_CODES = frozenset({
    "unbound",            # a reference with no compatible antecedent
    "ambiguous",          # two equally near antecedents (simultaneous siblings)
    "no_antecedent",      # a connective / instead / override with no earlier spec
    "delay_mixed",        # a sentence bound to a delayed part and the immediate part
    "target",             # a "target ..." possessive with no requirement of its own
})


def _um(stage: Stage, lemma: str, code: str) -> Unmodelled:
    return unmodelled(stage, lemma, LEAF, code, DETAIL_CODES)


# ── Closed vocabularies (section 7) ────────────────────────────────────

# Verbs whose principal changes zones: a later reference to the moved
# object is the spec's RESULT, the new object (CR 400.7, A25).
_MOVERS = frozenset({Verb.MOVE, Verb.EXILE})
# Verbs after which the object no longer is where it was: an "its
# controller" read of it uses last-known information (CR 608.2h).
_LEAVERS = frozenset({Verb.MOVE, Verb.EXILE, Verb.DESTROY, Verb.SACRIFICE,
                      Verb.COUNTER, Verb.DISCARD, Verb.MILL})
# Verbs whose result is a set of cards or objects a later "it" / "them" /
# "that card" names when they have no principal of their own.
_PRODUCERS = frozenset({Verb.SEARCH, Verb.LOOK, Verb.REVEAL,
                        Verb.REVEAL_UNTIL, Verb.CREATE_TOKEN, Verb.DISCARD,
                        Verb.MILL, Verb.CHOOSE, Verb.SACRIFICE, Verb.EXILE,
                        Verb.MOVE, Verb.DESTROY})
# Verbs whose result is a number a later "that much" / "that many" reads.
_DEST_VERBS = frozenset({Verb.COUNTER, Verb.DESTROY, Verb.SACRIFICE,
                         Verb.DISCARD, Verb.MILL, Verb.EXILE, Verb.MOVE})
_SELF_HINTS = frozenset({EventHint.SELF_ENTERS, EventHint.SELF_DIES,
                         EventHint.SELF_LEAVES, EventHint.SELF_ATTACKS,
                         EventHint.SELF_CAST})
_OBJECT_HINTS = frozenset({EventHint.OTHER_ENTERS, EventHint.OTHER_DIES,
                           EventHint.ATTACKS_OTHER, EventHint.SPELL_CAST,
                           EventHint.LANDFALL, EventHint.COUNTERS_PUT,
                           EventHint.CYCLE, EventHint.TAPPED_FOR_MANA})
_LEAVE_HINTS = frozenset({EventHint.SELF_DIES, EventHint.SELF_LEAVES})
_SELF_HOSTS = frozenset({HostKind.ACTIVATED, HostKind.MANA_ABILITY,
                         HostKind.LOYALTY, HostKind.STATIC, HostKind.CHAPTER,
                         HostKind.KEYWORD})
# The card-type nouns a reference may name (the filter leaf's table).
_TYPE_NOUNS = _filter.CARD_TYPES
_WIDE_TYPES = frozenset({"permanent", "permanent_nonland", "card", "any",
                         "spell", "object"})
_MULTI_PLAYER = frozenset({SelectorKind.ALL_PLAYERS, SelectorKind.OPPONENTS})


def _produced_by(node: "_Node", participle: str) -> bool:
    """Did the spec perform the action `participle` names ("the exiled
    card", "dealt damage this way")? The lexicon owns the inflection."""
    lemma = _lexicon.participle_lemma(participle)
    return bool(lemma) and node.lemma.split(" ")[0] == lemma


# ── The mutable linking state ───────────────────────────────────────────

class _Mention:
    """One antecedent candidate: an earlier spec's participant slot."""
    __slots__ = ("node", "ref", "pre", "player", "plural", "types", "zone")

    def __init__(self, node, ref, *, pre=None, player=False, plural=None,
                 types=frozenset(), zone=""):
        self.node, self.ref, self.pre = node, ref, pre
        self.player, self.plural = player, plural
        self.types, self.zone = types, zone


class _Node:
    """One spec while the host is linked."""
    __slots__ = ("spec", "cm", "fi", "lemma", "seq", "host", "container",
                 "then", "otherwise", "fields", "slot", "role_slots",
                 "mentions", "bound", "failed", "frame", "clause")

    def __init__(self, spec, cm, fi, host, frame=None, clause=None):
        self.spec, self.cm, self.fi, self.host = spec, cm, fi, host
        self.frame, self.clause = frame, clause
        self.lemma = cm.lemma if cm is not None else ""
        self.seq = 0
        self.container: Optional[list] = None
        self.then: List["_Node"] = []
        self.otherwise: List["_Node"] = []
        self.fields: Dict[str, Any] = {}
        self.slot: Optional[int] = None            # principal target slot
        self.role_slots: Dict[str, int] = {}      # role -> target slot
        self.mentions: List[_Mention] = []
        self.bound: List["_Node"] = []            # antecedent nodes it binds to
        self.failed: Optional[Unmodelled] = None

    def get(self, name):
        return self.fields[name] if name in self.fields else getattr(self.spec, name)

    def fail(self, um: Unmodelled) -> None:
        if self.failed is None:
            self.failed = um


class _Host:
    """One host (the ability, or a sub-ability it creates) while linked."""
    __slots__ = ("kind", "parent", "top", "nodes", "targets", "alts",
                 "trigger", "mode", "l1", "opener")

    def __init__(self, kind, parent=None, trigger=None, mode=False, l1=None):
        self.kind, self.parent, self.trigger = kind, parent, trigger
        self.mode, self.l1 = mode, l1
        self.top: List[_Node] = []
        self.nodes: List[_Node] = []              # printed order, all depths
        self.targets: List[TargetRequirement] = []
        self.alts: List[Tuple[int, int]] = []
        self.opener: Optional["_Node"] = None     # the CREATE_TRIGGER that opened it

    def add_target(self, req: TargetRequirement) -> int:
        if self.mode and req.mode_group is None:
            req = dataclasses.replace(req, mode_group=1)
        self.targets.append(req)
        return len(self.targets) - 1

    def chain(self):
        h = self
        while h is not None:
            yield h
            h = h.parent


class _Ctx:
    """Per-ability facts the linker reads: the host antecedent and LKI."""
    __slots__ = ("kind", "trigger", "source_left", "type_class", "seq",
                 "quotes", "facts", "face")

    def __init__(self, l1, facts, quotes, face, antecedent=None):
        # A mode reads its modal host's antecedent (the trigger head of a
        # modal trigger, CR 700.2).
        self.kind, self.trigger = antecedent or (l1.kind, l1.trigger)
        self.facts, self.quotes, self.face = facts, quotes, face
        self.type_class = facts.type_class
        cost = dict(l1.cost.items) if l1.cost is not None else {}
        hints = set(self.trigger.event_hints) if self.trigger is not None \
            else set()
        self.source_left = bool(cost.get("sacrifice_self")
                                or cost.get("exile_self")
                                or hints & _LEAVE_HINTS)
        self.seq = 0

    def next_seq(self) -> int:
        s = self.seq
        self.seq += 1
        return s


# ── Field walking: references inside a spec's own fields ───────────────

_FIELDS_CACHE: Dict[type, Tuple[str, ...]] = {}
_OPAQUE = (EffectSpec, AbilityEffects, TargetRequirement, Selector, Unmodelled)


def _fields(cls) -> Tuple[str, ...]:
    f = _FIELDS_CACHE.get(cls)
    if f is None:
        f = tuple(x.name for x in dataclasses.fields(cls))
        _FIELDS_CACHE[cls] = f
    return f


def _map(obj: Any, fn) -> Any:
    """`obj` rebuilt bottom-up with `fn(value, parent)` applied to every
    Ref, Condition, Quantity and Amount (pre-order hook returns the
    replacement or None to keep walking). Shares unchanged subtrees."""
    if obj is None or isinstance(obj, (str, int, float, bool, _OPAQUE)):
        return obj
    if isinstance(obj, tuple):
        new = tuple(_map(x, fn) for x in obj)
        return obj if all(a is b for a, b in zip(new, obj)) else new
    if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
        return obj
    hit = fn(obj)
    if hit is not None:
        return hit                       # a bound value is final
    changes = {}
    for name in _fields(type(obj)):
        v = getattr(obj, name)
        if v is None or isinstance(v, (str, int, float, bool, frozenset)):
            continue
        nv = _map(v, fn)
        if nv is not v:
            changes[name] = nv
    return dataclasses.replace(obj, **changes) if changes else obj


# ── Mentions ────────────────────────────────────────────────────────────

def _plural(amount: Optional[Amount]) -> Optional[bool]:
    if amount is None:
        return None
    k = amount.kind
    if k is AmountKind.LITERAL:
        return amount.n != 1
    if k is AmountKind.UP_TO:
        return None if amount.n == 0 else amount.n > 1
    if k in (AmountKind.ALL, AmountKind.ANY_NUMBER, AmountKind.WHOLE_ZONE):
        return True
    return None


def _req_player(req: TargetRequirement):
    t = req.types
    if t <= {"player", "opponent"}:
        return True
    if t & {"player", "any"}:
        return None
    return False


def _mentions(node: _Node) -> List[_Mention]:
    """The antecedent candidates a spec offers, in printed order (subject,
    principal, condition subject): later ones are nearer."""
    out: List[_Mention] = []
    host = node.host
    spec = node.spec
    verb = spec.verb
    mover = verb in _MOVERS
    for role in ("actor", "other"):
        v = node.get(role)
        if role in node.role_slots:
            req = host.targets[node.role_slots[role]]
            out.append(_Mention(node, Ref(RefKind.TARGET, node.role_slots[role]),
                                player=_req_player(req),
                                plural=req.count_max > 1, types=req.types,
                                zone=req.zone))
        elif isinstance(v, Ref) and v.kind is not RefKind.MEMBER:
            out.append(_Mention(node, v, player=v.kind in (
                RefKind.CONTROLLER_OF, RefKind.OWNER_OF, RefKind.EVENT_PLAYER,
                RefKind.DEFENDING_PLAYER) or v.noun == "player"))
        elif role == "actor" and isinstance(v, Selector) and \
                v.kind in _MULTI_PLAYER:
            # "Each opponent may discard a card. If they don't, they lose 3
            # life": a later "they" is each of those players (CR 101.4).
            out.append(_Mention(node, v, player=True, plural=True))
    if verb is Verb.UNMODELLED:
        # A refused clause's result is an object set whose kind is unknown;
        # it is never a player, so a player reference ("that player") skips
        # it for the nearest player mention (section 7 "That player").
        out.append(_Mention(node, Ref(RefKind.RESULT, node.seq), player=False))
        return out
    principal = None
    if node.slot is not None:
        req = host.targets[node.slot]
        ref = Ref(RefKind.TARGET, node.slot)
        principal = _Mention(node, ref, player=_req_player(req),
                             plural=req.count_max > 1, types=req.types,
                             zone=req.zone)
    elif isinstance(node.get("ref"), Ref) and \
            node.get("ref").kind is not RefKind.MEMBER:
        r = node.get("ref")
        principal = _Mention(node, r, player=r.noun == "player",
                             plural=None if r.part is RefPart.ALL else None)
    if principal is not None:
        if mover:
            principal = _Mention(node, Ref(RefKind.RESULT, node.seq),
                                 pre=principal.ref, player=False,
                                 plural=principal.plural,
                                 types=principal.types)
        out.append(principal)
    elif node.get("subject") is not None and verb not in _PRODUCERS:
        # A quantified group the spec acts on ("creatures you control get
        # +1/+2. Untap those creatures."): the affected set, its RESULT.
        f = node.get("filter")
        out.append(_Mention(node, Ref(RefKind.RESULT, node.seq), player=False,
                            plural=True,
                            types=f.types if isinstance(f, CardFilter)
                            else frozenset()))
    elif verb in _PRODUCERS:
        f = node.get("filter")
        types = f.types if isinstance(f, CardFilter) else frozenset()
        zone = f.zone if isinstance(f, CardFilter) else ""
        subject = node.get("subject")
        plural = True if subject is not None else _plural(node.get("amount"))
        if verb is Verb.CREATE_TOKEN:
            p = node.get("payload")
            types = frozenset(p.types) if isinstance(p, TokenSpec) else types
        out.append(_Mention(node, Ref(RefKind.RESULT, node.seq), player=False,
                            plural=plural, types=types,
                            zone="battlefield" if verb is Verb.CREATE_TOKEN
                            else zone))
    c = node.get("condition")
    if isinstance(c, Condition) and isinstance(c.ref, Ref) and \
            c.kind is ConditionKind.OBJECT and c.ref.kind is not RefKind.MEMBER:
        out.append(_Mention(node, c.ref, player=False))
    return out


# ── Binding ─────────────────────────────────────────────────────────────

class _Want:
    """What a reference needs: a player, an object or either; a noun; a
    number; a producing participle."""
    __slots__ = ("player", "noun", "plural", "participle", "part", "n")

    def __init__(self, player=False, noun="", plural=None, participle="",
                 part=RefPart.ALL, n=None):
        self.player, self.noun, self.plural = player, noun, plural
        self.participle, self.part, self.n = participle, part, n


def _compatible(w: _Want, m: _Mention) -> bool:
    if w.player is True and m.player is False:
        return False
    if w.player is False and m.player is True:
        return False
    if w.part is RefPart.ALL and w.plural is not None and \
            m.plural is not None and w.plural != m.plural:
        return False
    noun = w.noun
    if noun in _TYPE_NOUNS and m.types and not (m.types & _WIDE_TYPES) \
            and noun not in m.types:
        return False
    if noun == "spell" and m.zone and m.zone != "stack":
        return False
    return True


def _scopes(node: _Node):
    """(host, nodes before `node` nearest first) for the node's host, then
    each creating host (before the sub-ability's opener)."""
    host = node.host
    nodes = host.nodes
    i = nodes.index(node) if node in nodes else len(nodes)
    yield host, nodes[:i][::-1]
    h = host
    while h.parent is not None:
        p = h.parent
        j = p.nodes.index(h.opener) if h.opener in p.nodes else len(p.nodes)
        yield p, p.nodes[:j][::-1]
        h = p


def _for_scope(host: _Host, m: _Mention, ref: Ref) -> Optional[Ref]:
    """`ref` as seen from `host`: a target of a creating host is that
    spec's snapshot, its RESULT (A34)."""
    if m.node.host is host:
        return ref
    if ref.kind is RefKind.TARGET:
        return Ref(RefKind.RESULT, m.node.seq)
    return ref


def _search(node: _Node, w: _Want):
    """(ref, antecedent node, mention) by rules 1-2, or (None, None, None);
    raises _Ambiguous on two equally near candidates."""
    for host, earlier in _scopes(node):
        for cand in earlier:
            if w.part is not RefPart.ALL and _selection(cand):
                # A partitive ("one of them", "the rest") names the set an
                # earlier selection was taken from, never that selection.
                continue
            if w.participle:
                if cand.spec.verb is Verb.UNMODELLED and not cand.lemma:
                    continue
                if not _produced_by(cand, w.participle):
                    continue
                ref = Ref(RefKind.RESULT, cand.seq)
                return _for_scope(node.host, _Mention(cand, ref), ref), cand, None
            ms = [m for m in cand.mentions if _compatible(w, m)]
            if not ms:
                continue
            if w.noun == "card":
                off = [m for m in ms if not (m.ref.kind is RefKind.TARGET
                                             and m.zone == "battlefield")]
                ms = off or ms
            m = ms[-1]
            # Simultaneous siblings: another compatible mention in an
            # earlier spec of the same group is equally near.
            g = cand.get("group")
            if g is not None:
                for other in earlier[earlier.index(cand) + 1:]:
                    if other.get("group") != g:
                        break
                    if any(_compatible(w, x) and x.ref != m.ref
                           for x in other.mentions):
                        raise _Ambiguous()
            return _for_scope(node.host, m, m.ref), cand, m
    return None, None, None


class _Ambiguous(Exception):
    pass


def _selection(node: _Node) -> bool:
    """Is the spec a selection from an earlier result ("put one of them",
    "reveal a card from among them")?"""
    return bool(node.bound) and node.spec.verb in _PRODUCERS


def _host_antecedent(ctx: _Ctx, node: _Node, w: _Want) -> Optional[Ref]:
    """Rule 3: the trigger head's object or player; an ACTIVATED / STATIC
    "it" whose noun fits the card is SELF; a SPELL has none."""
    host = node.host
    while host.parent is not None:
        host = host.parent
    kind = ctx.kind
    if kind is HostKind.TRIGGERED and ctx.trigger is not None:
        hints = set(ctx.trigger.event_hints)
        if w.player is True:
            if ctx.trigger.names_player:
                return Ref(RefKind.EVENT_PLAYER)
            return None
        if hints & _OBJECT_HINTS or (EventHint.OTHER in hints and
                                     ctx.trigger.names_object):
            return Ref(RefKind.EVENT_OBJECT)
        if hints & _SELF_HINTS or "~" in ctx.trigger.raw:
            return Ref(RefKind.SELF)
        return None
    if kind in _SELF_HOSTS and w.player is not True:
        noun = w.noun
        if noun in _TYPE_NOUNS and ctx.type_class and noun not in ctx.type_class:
            return None
        return Ref(RefKind.SELF)
    return None


def _bind(ctx: _Ctx, node: _Node, w: _Want, *, last_known=False) -> Ref:
    """The reference `w` names from `node` (rules 1-3); raises _Unbound.
    `last_known`: the reference reads the object's controller or a
    characteristic, so a moved or destroyed antecedent is read as it last
    existed (CR 608.2h), not as the new object."""
    try:
        ref, cand, m = _search(node, w)
    except _Ambiguous:
        raise _Unbound("ambiguous")
    if ref is None:
        if w.participle == "exiled":
            return Ref(RefKind.LINKED, noun=w.noun)        # CR 607 (A26)
        if w.participle:
            raise _Unbound("unbound")
        ref = _host_antecedent(ctx, node, w)
        if ref is None:
            raise _Unbound("unbound")
        return _with_part(ref, w)
    node.bound.append(cand)
    if last_known and m is not None and m.pre is not None:
        # The controller of a moved object is read from the object as it
        # last existed (CR 608.2h), not from the new object.
        ref = dataclasses.replace(m.pre, lki=True) \
            if m.node.host is node.host else Ref(RefKind.RESULT, m.node.seq,
                                                  lki=True)
    elif last_known and cand.spec.verb in _LEAVERS and isinstance(ref, Ref):
        ref = dataclasses.replace(ref, lki=True)
    return _with_part(ref, w)


def _with_part(ref: Ref, w: _Want) -> Ref:
    """The bound antecedent with the anaphor's partitive (A28); the
    antecedent's own noun is kept. A player selector is bound whole."""
    if not isinstance(ref, Ref) or (w.part is RefPart.ALL and w.n is None):
        return ref
    return dataclasses.replace(ref, part=w.part, n=w.n)


class _Unbound(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _want_of(an) -> _Want:
    return _Want(player=an.player, noun=an.noun, plural=an.plural,
                 participle=an.participle, part=an.part, n=an.n)


def _principal_ref(node: _Node) -> Optional[Ref]:
    """Rule 0: the spec's own principal as a Ref."""
    if node.slot is not None:
        return Ref(RefKind.TARGET, node.slot)
    r = node.get("ref")
    if isinstance(r, Ref):
        return r
    if node.get("subject") is not None:
        return Ref(RefKind.MEMBER)
    return None


def _pending_noun(node: _Node) -> str:
    for k, text in tuple(node.cm.pending if node.cm else ()) + tuple(
            node.frame.pending if node.frame is not None else ()):
        if k in ("ref", "either"):
            noun = _participant.reference_noun(text)
            if noun:
                return noun
    return ""


def _result_event(node: _Node) -> str:
    for k, text in tuple(node.cm.pending if node.cm else ()) + tuple(
            node.frame.pending if node.frame is not None else ()):
        if k == "result" and text:
            return text.split()[-1]
    return ""


def _nearest_result(node: _Node, event: str) -> Optional[Ref]:
    """The RESULT a "that much" / "this way" names: the nearest earlier
    spec of the named event's verb, else the nearest earlier spec."""
    for host, earlier in _scopes(node):
        for cand in earlier:
            if event and not _produced_by(cand, event):
                continue
            node.bound.append(cand)
            return Ref(RefKind.RESULT, cand.seq)
    return None


# A clause whose text prints no reference word (the participant leaf's
# pre-gate), and whose leaves left no pending reference, holds no unbound
# reference: its fields are not walked.
_HOLE_WORDS_RE = _participant.REFERENCE_WORD_RE


def _fill_holes(ctx: _Ctx, node: _Node) -> None:
    """Bind the unbound references the leaves left in the spec's own
    fields (section 7): rule 0 for frame modifiers, mention rules for a
    subject's possessor, the named result for "this way" / "that much"."""
    f = node.frame
    if not (node.cm.pending or node.cm.targets or
            (f is not None and (f.pending or f.condition is not None
                                or f.unless is not None))
            or _HOLE_WORDS_RE.search(node.spec.raw)):
        return
    own = None
    event = _result_event(node)
    noun = _pending_noun(node)

    def result_ref(event_amount=False):
        r = _nearest_result(node, event)
        if r is None:
            # A trigger body's "that much" / "that many" with no earlier
            # action reads the triggering event's amount (CR 603.2).
            if event_amount and ctx.kind is HostKind.TRIGGERED and \
                    node.host.parent is None:
                return Ref(RefKind.EVENT_OBJECT)
            raise _Unbound("unbound")
        return r

    def target_ref(r: Ref) -> Ref:
        want = r.noun
        for role, k in sorted(node.role_slots.items()) + (
                [("principal", node.slot)] if node.slot is not None else []):
            t = node.host.targets[k].types
            if not want or want in t or (want in ("player", "opponent")
                                         and t & {"player", "opponent"}):
                return Ref(RefKind.TARGET, k)
        raise _Unbound("target")

    def operand(subject_slot: bool) -> Ref:
        if not subject_slot and own is not None and own.kind is not RefKind.MEMBER:
            return own
        return _bind(ctx, node, _Want(player=False, noun=noun),
                     last_known=True)

    def hook_for(subject_slot: bool):
        def fn(v):
            if isinstance(v, Ref):
                if v.kind is RefKind.RESULT and v.index is None:
                    return dataclasses.replace(result_ref(), part=v.part,
                                               n=v.n)
                if v.kind is RefKind.TARGET and v.index is None:
                    return target_ref(v)
                if v.kind in (RefKind.CONTROLLER_OF, RefKind.OWNER_OF) and \
                        v.of is None:
                    return dataclasses.replace(v, of=operand(subject_slot))
                return None
            if isinstance(v, Condition) and v.kind is ConditionKind.OBJECT \
                    and v.ref is None:
                r = own if not subject_slot and own is not None else \
                    _bind(ctx, node, _Want(player=False, noun=noun))
                return dataclasses.replace(v, ref=r)
            if isinstance(v, Quantity) and v.ref is None and v.filter is None \
                    and v.kind in (QuantityKind.POWER, QuantityKind.TOUGHNESS,
                                   QuantityKind.MANA_VALUE,
                                   QuantityKind.COUNTERS_ON):
                r = own if own is not None and own.kind is not RefKind.MEMBER \
                    else _bind(ctx, node, _Want(player=False, noun=noun),
                               last_known=True)
                return dataclasses.replace(v, ref=r)
            if isinstance(v, Amount) and v.kind is AmountKind.THAT_MUCH and \
                    v.ref is None:
                return dataclasses.replace(v, ref=result_ref(True))
            return None
        return fn

    for slot in ("actor", "other"):
        v = node.get(slot)
        if isinstance(v, Ref):
            nv = _map(v, hook_for(True))
            if nv is not v:
                node.fields[slot] = nv
    r = node.get("ref")
    if isinstance(r, Ref):
        nv = _map(r, hook_for(True))
        if nv is not r:
            node.fields["ref"] = nv
    # Rule 0 reads the principal once the subject slots are bound.
    own = _principal_ref(node)
    for slot in ("condition", "amount", "filter", "dest", "payload"):
        v = node.get(slot)
        if v is None or isinstance(v, (Unmodelled, SubAbility)):
            continue
        nv = _map(v, hook_for(False))
        if nv is not v:
            node.fields[slot] = nv


# ── Placing one clause ──────────────────────────────────────────────────

def _new_node(ctx, host, spec, cm, fi, frame) -> _Node:
    n = _Node(spec, cm, fi, host, frame)
    n.seq = ctx.next_seq()
    host.nodes.append(n)
    return n


def _place_targets(node: _Node) -> List[_Node]:
    """Collect the clause's requirements into its host's targets (printed
    order); a second principal requirement becomes a simultaneous
    sibling. Returns the extra sibling nodes."""
    cm, host = node.cm, node.host
    extra: List[_Node] = []
    principals = []
    for role, req, _sp in cm.targets:
        k = host.add_target(req)
        if role == "principal":
            principals.append(k)
        else:
            node.role_slots[role] = k
            node.fields[role] = Ref(RefKind.TARGET, k)
    if principals:
        node.slot = principals[0]
    extra.extend(principals[1:])
    return extra


def _inherit(node: _Node, src: Optional[_Node], role: str) -> None:
    """A role the clause does not restate takes its antecedent's
    participant (L3 gapping / an elided subject / instead, A15)."""
    if src is None:
        return
    if role == "principal":
        if node.slot is not None or node.get("ref") is not None or \
                node.get("subject") is not None:
            return
        if src.slot is not None:
            node.slot = src.slot
            return
        for slot in ("ref", "subject"):
            v = src.get(slot)
            if v is not None:
                node.fields[slot] = v
                if slot == "subject" and node.get("filter") is None:
                    node.fields["filter"] = src.get("filter")
                return
        return
    if role in src.role_slots:
        k = src.role_slots[role]
        node.role_slots[role] = k
        node.fields[role] = Ref(RefKind.TARGET, k)
    elif src.spec.verb is Verb.UNMODELLED or src.failed is not None:
        # The antecedent was refused: what it would have supplied is
        # unknown, never the default player.
        if node.get(role) is None:
            raise _Unbound("unbound")
    elif src.get(role) is not None:
        # The antecedent's participant as it was bound: the inherited text
        # names the same player or object, never a fresh antecedent.
        node.fields[role] = src.get(role)


def _bind_node(ctx: _Ctx, node: _Node, antecedent: Optional[_Node]) -> None:
    """Rule 0-3 binding of one clause's anaphors and holes."""
    cm = node.cm
    try:
        for role, v, _sp in cm.participants:
            if v == "inherited":
                if role == "principal" and not node.frame.instead:
                    _inherit(node, antecedent, role)
                elif role != "principal":
                    _inherit(node, antecedent, role)
                continue
            if v.reflexive:
                # "itself": the clause's own subject (its source or actor).
                ref = node.get("other") if role != "other" else node.get("actor")
                if not isinstance(ref, Ref):
                    raise _Unbound("unbound")
                node.fields[{"principal": "ref"}.get(role, role)] = ref
                continue
            w = _want_of(v)
            if role == "actor" and w.player is None:
                # A number-only anaphor ("they") as the clause's actor names
                # the nearest player who could perform it; only with no
                # player antecedent is it an object ("they explore").
                try:
                    ref = _bind(ctx, node, _Want(
                        player=True, noun=w.noun, plural=w.plural,
                        participle=w.participle, part=w.part, n=w.n))
                except _Unbound:
                    ref = _bind(ctx, node, w)
                    if isinstance(ref, Ref) and ref.kind is RefKind.RESULT \
                            and node.bound and _refused(node.bound[-1]):
                        # Only a refused clause's unknown result is left:
                        # who acts is unknown, never guessed.
                        raise _Unbound("unbound")
            else:
                ref = _bind(ctx, node, w)
            field = {"principal": "ref"}.get(role, role)
            if field == "ref" and (node.slot is not None
                                   or node.get("subject") is not None):
                continue
            node.fields[field] = ref
        _fill_holes(ctx, node)
    except _Unbound as u:
        node.fail(_um(Stage.REFERENCE, node.lemma, u.code))


def _mark_lki(ctx: _Ctx, node: _Node) -> None:
    """A27: every SELF ref of an ability whose source left as part of the
    cost or the event reads last-known information (CR 608.2h)."""
    if not ctx.source_left:
        return

    def fn(v):
        if isinstance(v, Ref) and v.kind is RefKind.SELF and not v.lki:
            return dataclasses.replace(v, lki=True)
        return None
    for slot in ("ref", "other", "actor", "condition", "amount", "filter",
                 "dest", "payload"):
        v = node.get(slot)
        if v is None or isinstance(v, (Unmodelled, SubAbility, str)):
            continue
        nv = _map(v, fn)
        if nv is not v:
            node.fields[slot] = nv


def _refused(node: _Node) -> bool:
    """Is the spec refused -- by its layer, or by a failed link?"""
    return node.spec.verb is Verb.UNMODELLED or node.failed is not None


def _shares_subject(node: _Node, src: _Node) -> bool:
    """Is `node` a later clause of `src`'s sentence whose subject is
    elided -- so its actor is the subject `src` printed (L3 gapping)?"""
    return node.frame is not None and src.frame is node.frame and \
        node.clause is not None and node.clause.gap == "subject"


def _mark_per_actor(node: _Node) -> None:
    """A28 / CR 101.4: a RESULT of a multi-player actor's spec read by a
    spec of the same actor is bound per actor."""
    actor = node.get("actor")
    if not (isinstance(actor, Selector) and actor.kind in _MULTI_PLAYER):
        return
    seqs = {b.seq: b for b in node.bound}

    def fn(v):
        if isinstance(v, Ref) and v.kind is RefKind.RESULT and not \
                v.per_actor and v.index in seqs:
            b = seqs[v.index]
            src = b.get("actor")
            if isinstance(src, Selector) and src.kind is actor.kind:
                return dataclasses.replace(v, per_actor=True)
            if _refused(b) and _shares_subject(node, b):
                # "Each opponent chooses ..., then sacrifices the rest": the
                # refused clause's actor is unknown, but this clause's
                # elided subject is that clause's, so it is the same actor.
                return dataclasses.replace(v, per_actor=True)
        return None
    for slot in ("ref", "condition", "amount", "filter"):
        v = node.get(slot)
        if v is None or isinstance(v, Unmodelled):
            continue
        nv = _map(v, fn)
        if nv is not v:
            node.fields[slot] = nv


# ── Granted hosts ───────────────────────────────────────────────────────

_QUOTE_RE = re.compile(r"⟨q(\d+)⟩")


def _granted_hosts(ctx: _Ctx, mark: str) -> Tuple[AbilityEffects, ...]:
    m = _QUOTE_RE.fullmatch(mark.strip())
    if m is None or ctx.quotes is None:
        return ()
    k = int(m.group(1))
    if k >= len(ctx.quotes):
        return ()
    return _granted(ctx.quotes[k], ctx.quotes, ctx.face)


def _granted(text: str, quotes: Tuple[str, ...], face: int
             ) -> Tuple[AbilityEffects, ...]:
    """A quoted ability parsed as an ability of its own (CR 113.1a); a
    nested quote reads the face's quote table. Self-references inside it
    already name the recipient (L0, A10)."""
    fs = _structure.parse_face_structure(text, _normalize.Facts(), face)
    return tuple(link_host(h, _normalize.Facts(), quotes, face)
                 for h in fs.hosts)


def _attach_granted(ctx: _Ctx, node: _Node) -> None:
    marks = [q for k, q in (node.cm.pending if node.cm else ())
             if k == "granted"]
    p = node.get("payload")
    if isinstance(p, Modification) and p.kind is ModKind.GRANT_ABILITY:
        marks = [a for a in (p.get("abilities") or ()) if isinstance(a, str)]
    if not marks:
        return
    hosts = tuple(h for q in marks for h in _granted_hosts(ctx, q))
    if not hosts:
        return
    if isinstance(p, TokenSpec):
        node.fields["payload"] = dataclasses.replace(p, granted=p.granted + hosts)
    elif isinstance(p, Granted):
        node.fields["payload"] = Granted(hosts=p.hosts + hosts)
    elif isinstance(p, Modification):
        node.fields["payload"] = dataclasses.replace(
            p, data=p.data + (("granted", Granted(hosts=hosts)),))


# ── The host pass ───────────────────────────────────────────────────────

def _attach(node: _Node, container: list) -> None:
    if node.container is not None:
        node.container.remove(node)
    node.container = container
    container.append(node)


def _named(host: _Host, before: List[_Node], lemma: str) -> Optional[_Node]:
    for n in reversed(before):
        if not lemma or n.lemma == lemma:
            return n
    return None


def _sub_host(ctx, cur: _Host, opener, frame_span, text) -> _Host:
    if opener.kind is SubAbilityKind.REFLEXIVE:
        head = TriggerHead(event_hints=(EventHint.REFLEXIVE,),
                           raw=text[opener.span[0]:opener.span[1]],
                           intervening_if=opener.intervening_if)
    else:
        raw = text[opener.span[0]:opener.span[1]] if opener.span[1] > \
            opener.span[0] else ""
        head = TriggerHead(event_hints=(EventHint.DELAYED,), raw=raw,
                           step=opener.timing.name if opener.timing else "")
    return _Host(HostKind.TRIGGERED, parent=cur, trigger=head, mode=cur.mode)


def _frame_nodes(ctx: _Ctx, host: _Host, fm, fi: int) -> List[_Node]:
    """The clause nodes of one frame in `host`, targets collected and
    references bound."""
    out: List[_Node] = []
    prev = None
    f = fm.frame
    clauses = f.clauses if len(f.clauses) == len(fm.clauses) else \
        (None,) * len(fm.clauses)
    for clause, cm in zip(clauses, fm.clauses):
        node = _new_node(ctx, host, cm.spec, cm, fi, f)
        node.clause = clause
        if cm.spec.verb is Verb.UNMODELLED:
            # A refused clause binds nothing and owns no requirement: its
            # printed participants were never typed.
            node.mentions = _mentions(node)
            out.append(node)
            prev = node
            continue
        extra = _place_targets(node)
        _among(node)
        _bind_node(ctx, node, prev)
        group = [node]
        for k in extra:
            sib = _new_node(ctx, host, cm.spec, cm, fi, f)
            sib.clause = clause
            sib.slot = k
            sib.fields = dict(node.fields)
            sib.role_slots = dict(node.role_slots)
            sib.bound = list(node.bound)
            sib.failed = node.failed
            group.append(sib)
        if extra:
            g = cm.spec.group if cm.spec.group is not None else cm.spec.span[0]
            for n in group:
                n.fields["group"] = g
        for n in group:
            # A later clause of the same sentence may name this one (rule
            # 1), so its mentions are ready before the next clause binds.
            _attach_granted(ctx, n)
            _mark_lki(ctx, n)
            _mark_per_actor(n)
            n.mentions = _mentions(n)
        out.extend(group)
        prev = node
    return out


def _among(node: _Node) -> None:
    """A selection "from among them" depends on the earlier result it is
    taken from (the source of its choice)."""
    if node.cm is None or not any(k == "among" for k, _t in node.cm.pending):
        return
    before = [n for n in node.host.nodes if n.seq < node.seq]
    for n in reversed(before):
        if n.spec.verb in _PRODUCERS or n.spec.verb is Verb.UNMODELLED:
            node.bound.append(n)
            return


def _apply_riders(host_state: dict, nodes: List[_Node], frame) -> None:
    for rider, value in frame.riders:
        if rider == "uncounterable":
            host_state["flags"].add("uncounterable")
        elif rider == "cost_modifier":
            host_state["cost_modifiers"].append(value)
        elif rider == "mana_restriction":
            for n in nodes:
                p = n.get("payload")
                if n.spec.verb is Verb.ADD_MANA and p is not None and \
                        hasattr(p, "restriction"):
                    n.fields["payload"] = dataclasses.replace(
                        p, restriction=value)
        elif rider in ("cost_rule", "still_land"):
            for n in nodes:
                p = n.get("payload")
                if isinstance(p, Modification):
                    entry = ("cost_rule", value) if rider == "cost_rule" else \
                        ("retain_types", ("land",))
                    n.fields["payload"] = dataclasses.replace(
                        p, data=p.data + (entry,))


_MAY_END_RE = re.compile(r"(?:^|\s)may$")


def _may_scope(nodes: List[_Node], text: str) -> None:
    """A29: a follower in an optional head's sentence nests under the head
    when it depends on the head's chain: it binds to a result of the chain,
    shuffles the library the chain searched, or only inherits the head's
    "may" subject. A follower bound to an earlier result outside the chain
    (a REST of an earlier look) stays a sibling."""
    for i, head in enumerate(nodes):
        if not head.get("optional") or head.failed is not None:
            continue
        chain = {id(head)}
        searched = head.spec.verb is Verb.SEARCH
        for f in nodes[i + 1:]:
            if f.container is not head.container:
                continue
            dep = any(id(b) in chain for b in f.bound)
            outside = any(id(b) not in chain for b in f.bound)
            may = f.clause is not None and f.clause.gap == "subject" and any(
                _MAY_END_RE.search(text[a:b]) for a, b in f.clause.prefix)
            if dep or (f.spec.verb is Verb.SHUFFLE and searched) or \
                    (may and not outside):
                _attach(f, head.then)
                f.fields["flags"] = frozenset(f.get("flags") | {"may_scope"})
                chain.add(id(f))
                searched = searched or f.spec.verb is Verb.SEARCH


def _link_frames(ctx: _Ctx, root: _Host, items, host_state: dict) -> None:
    """Place every frame of the ability, in printed order, into the root
    host or the sub-ability host it belongs to (A30)."""
    text = host_state["text"]
    cur = root
    delayed: Optional[_Host] = None
    delayed_parent: Optional[_Host] = None
    for fi, item in enumerate(items):
        if isinstance(item, _Refusal):                 # an L1 refusal
            um, span = item.um, item.span
            node = _Node(EffectSpec(verb=Verb.UNMODELLED, payload=um,
                                    span=span, raw=text[span[0]:span[1]]),
                         None, fi, cur)
            node.seq = ctx.next_seq()
            cur.nodes.append(node)
            _attach(node, cur.top)
            node.mentions = _mentions(node)
            continue
        fm = item
        f = fm.frame
        target = cur
        opener = f.opener
        ct = None
        if opener is not None:
            if opener.kind is SubAbilityKind.DELAYED and not fm.clauses:
                opener = None
            else:
                sub = _sub_host(ctx, cur, opener, f.span, text)
                ct = _Node(EffectSpec(verb=Verb.CREATE_TRIGGER,
                                      span=f.span, raw=text[f.span[0]:f.span[1]],
                                      flags=frozenset({"reflexive"})
                                      if opener.kind is SubAbilityKind.REFLEXIVE
                                      else frozenset()), None, fi, cur, f)
                ct.seq = ctx.next_seq()
                cur.nodes.append(ct)
                sub.opener = ct
                ct.fields["_sub"] = (opener, sub)
                if opener.kind is SubAbilityKind.REFLEXIVE:
                    before = [n for n in cur.nodes if n.fi < fi]
                    prev = before[-1] if before else None
                    _attach(ct, prev.then if prev is not None else cur.top)
                    cur = sub
                    delayed = None
                else:
                    # "If you do, <instruction> at the beginning of ..." (A14,
                    # A30): the connective gates the creation of the delayed
                    # ability, so its CREATE_TRIGGER is in the named action's
                    # branch.
                    container = cur.top
                    conn = f.connective
                    if conn in ("if_you_do", "if_you_dont", "otherwise"):
                        before = [n for n in cur.nodes if n.fi < fi]
                        anchor = _named(cur, before, f.named_lemma) \
                            if f.named_lemma else (before[-1] if before
                                                   else None)
                        if anchor is not None:
                            container = anchor.then if conn == "if_you_do" \
                                else anchor.otherwise
                            ct.bound.append(anchor)
                            if conn == "if_you_do":
                                ct.fields["flags"] = frozenset(
                                    ct.get("flags") | {"if_you_do"})
                    _attach(ct, container)
                    delayed, delayed_parent = sub, cur
                target = sub
        elif delayed is not None:
            target = _delayed_target(ctx, delayed, delayed_parent, fm, fi)
        _place_frame(ctx, target, fm, fi, ct, host_state)
        if ct is not None:
            ct.mentions = []


def _delayed_target(ctx, delayed: _Host, parent: _Host, fm, fi) -> _Host:
    """A30: a later sentence joins the open delayed sub-ability only when
    it binds to the delayed specs' results."""
    f = fm.frame
    if f.connective in ("if_you_do", "if_you_dont", "otherwise"):
        before = [n for n in delayed.nodes + parent.nodes if n.fi < fi]
        before.sort(key=lambda n: n.seq)
        anchor = _named(parent, before, f.named_lemma) if f.named_lemma \
            else (before[-1] if before else None)
        return delayed if anchor is not None and anchor.host is delayed \
            else parent
    if not fm.clauses:
        return parent
    # Probe: bind the sentence against every earlier spec in printed order
    # (the delayed part and the immediate part alike) and see where its
    # antecedents are.
    probe = _Host(HostKind.TRIGGERED, parent=parent.parent)
    probe.opener = parent.opener
    probe.nodes = sorted(delayed.nodes + [n for n in parent.nodes
                                          if n is not delayed.opener],
                         key=lambda n: n.seq)
    hit_delayed = hit_parent = False
    seq0 = ctx.seq
    for cm in fm.clauses:
        n = _Node(cm.spec, cm, fi, probe, f)
        n.seq = ctx.seq
        probe.nodes.append(n)
        _place_targets(n)
        _bind_node(ctx, n, None)
        for b in n.bound:
            if b.host is delayed:
                hit_delayed = True
            elif b.host is parent:
                hit_parent = True
    ctx.seq = seq0
    if hit_delayed and hit_parent:
        return _MixedDelay(parent)            # type: ignore[return-value]
    return delayed if hit_delayed else parent


class _Refusal:
    """An L1 refusal span (a replacement static, a level gate, ...)."""
    __slots__ = ("um", "span")

    def __init__(self, um, span):
        self.um, self.span = um, span


class _MixedDelay:
    """Marker: the sentence binds the delayed part and the immediate part."""
    __slots__ = ("parent",)

    def __init__(self, parent):
        self.parent = parent


def _place_frame(ctx, target, fm, fi, ct, host_state) -> None:
    f = fm.frame
    mixed = isinstance(target, _MixedDelay)
    host = target.parent if mixed else target
    if not fm.clauses and f.dest_override is not None:
        before = [n for n in host.nodes if n.fi < fi]
        anchor = next((n for n in reversed(before)
                       if n.spec.verb in _DEST_VERBS), None)
        if anchor is None:
            node = _Node(EffectSpec(verb=Verb.UNMODELLED, payload=_um(
                Stage.REFERENCE, "", "no_antecedent"), span=f.span,
                raw=host_state["text"][f.span[0]:f.span[1]]), None, fi, host, f)
            node.seq = ctx.next_seq()
            host.nodes.append(node)
            _attach(node, host.top)
            return
        anchor.fields["dest"] = f.dest_override
        anchor.fields["flags"] = frozenset(anchor.get("flags") |
                                           {"dest_override"})
        return
    if not fm.clauses:
        prev_nodes = [n for n in host.nodes if n.fi < fi]
        _apply_riders(host_state, prev_nodes[-1:] if prev_nodes else [], f)
        return
    before = [n for n in host.nodes if n.fi < fi]
    if ct is not None and ct.host is not host:
        before = []
    nodes = _frame_nodes(ctx, host, fm, fi)
    # A sub-ability opener's connective was placed with its CREATE_TRIGGER.
    conn = "" if ct is not None else f.connective
    _apply_riders(host_state, nodes, f)
    if mixed:
        for n in nodes:
            n.fail(_um(Stage.DELAY, n.lemma, "delay_mixed"))
    # 5. Connectives (A14), sentence-wide.
    container = host.top
    if conn in ("if_you_do", "if_you_dont", "otherwise"):
        anchor = _named(host, before, f.named_lemma) if f.named_lemma \
            else (before[-1] if before else None)
        if anchor is None and host.parent is not None and not before:
            anchor = None
        if anchor is None:
            for n in nodes:
                n.fail(_um(Stage.REFERENCE, n.lemma, "no_antecedent"))
        else:
            container = anchor.then if conn == "if_you_do" else anchor.otherwise
            if conn == "if_you_do":
                for n in nodes:
                    n.fields["flags"] = frozenset(n.get("flags") | {"if_you_do"})
            for n in nodes:
                n.bound.append(anchor)
    for n in nodes:
        _attach(n, container)
    # 6. May-scope by dependency (A29).
    _may_scope(nodes, host_state["text"])
    # 7. Instead (A15).
    if f.instead:
        for n in nodes:
            _instead(n, before)


def _same_verb(a: _Node, b: _Node) -> bool:
    """Do two clauses print one verb? A refused clause keeps its printed
    lemma, so it is compared by lemma, never by its UNMODELLED verb."""
    if a.lemma and b.lemma:
        return a.lemma == b.lemma
    return a.spec.verb is b.spec.verb and a.spec.verb is not Verb.UNMODELLED


def _instead(node: _Node, before: List[_Node]) -> None:
    """A15: the instead clause replaces the nearest earlier spec with its
    verb, refused ones included. Only when no earlier spec prints its verb
    at all does it replace the nearest earlier spec (a damage upgraded to
    a destroy). A refused antecedent leaves what is replaced unknown: the
    clause is refused, never rewired. A REPLACEMENT refusal ("if ...
    would ...", CR 614) is a replacement effect, not an instead sibling."""
    um = node.spec.payload if node.spec.verb is Verb.UNMODELLED else None
    if isinstance(um, Unmodelled) and um.stage is Stage.REPLACEMENT:
        return
    replaced = next((b for b in reversed(before) if _same_verb(b, node)),
                    None)
    if replaced is None:
        replaced = before[-1] if before else None
    if replaced is None or _refused(replaced):
        if um is None:                    # a refused clause keeps its own
            node.fail(_um(Stage.REFERENCE, node.lemma, "no_antecedent"))
        return
    seqs = [replaced.seq]
    g = replaced.get("group")
    if g is not None:
        seqs += [b.seq for b in before if b is not replaced and
                 b.get("group") == g and b.spec.verb is replaced.spec.verb]
    node.fields["replaces"] = tuple(sorted(seqs))
    if any(v == "inherited" and role == "principal"
           for role, v, _s in node.cm.participants):
        _inherit(node, replaced, "principal")
    elif node.slot is not None and replaced.slot is not None and \
            node.slot != replaced.slot:
        node.host.alts.append((replaced.slot, node.slot))
    if replaced.container is not None and node.container is not replaced.container \
            and node.container is node.host.top:
        _attach(node, replaced.container)
    node.mentions = _mentions(node)


# ── Freezing ────────────────────────────────────────────────────────────

def _freeze_node(ctx: _Ctx, node: _Node, frozen_hosts: dict,
                 lowered: dict) -> EffectSpec:
    f = dict(node.fields)
    sub = f.pop("_sub", None)
    spec = node.spec
    then = tuple(_freeze_node(ctx, c, frozen_hosts, lowered) for c in node.then)
    otherwise = tuple(_freeze_node(ctx, c, frozen_hosts, lowered)
                      for c in node.otherwise)
    if sub is not None:
        opener, sh = sub
        host = _freeze_host(ctx, sh, frozen_hosts, lowered)
        f["payload"] = SubAbility(kind=opener.kind,
                                  timing=opener.timing
                                  if opener.kind is SubAbilityKind.DELAYED
                                  else None, host=host)
    um = node.failed or lowered.get(node.seq)
    if um is not None:
        out = EffectSpec(verb=Verb.UNMODELLED, payload=um, seq=node.seq,
                         span=spec.span, raw=spec.raw or "<%s>" % node.lemma,
                         group=spec.group, then=then, otherwise=otherwise)
        return out
    f.pop("target", None)
    tgt = None
    if node.slot is not None:
        tgt = frozen_hosts["_targets"][id(node.host)][node.slot]
    return dataclasses.replace(spec, seq=node.seq, then=then,
                               otherwise=otherwise, target=tgt,
                               target_slot=node.slot if tgt is not None else None,
                               **f)


def _freeze_host(ctx: _Ctx, h: _Host, frozen_hosts: dict, lowered: dict,
                 base: Optional[dict] = None) -> AbilityEffects:
    targets = tuple(h.targets)
    frozen_hosts["_targets"][id(h)] = targets
    specs = tuple(_freeze_node(ctx, n, frozen_hosts, lowered) for n in h.top)
    kw = dict(base or {})
    kw.update(specs=specs, targets=targets, target_alts=tuple(h.alts))
    if base is None:
        kw.update(kind=h.kind, face=ctx.face, index=0, trigger=h.trigger,
                  text=frozen_hosts["_text"])
    out = AbilityEffects(**kw)
    frozen_hosts[id(h)] = out
    return out


def _violations(root: AbilityEffects) -> Dict[int, Unmodelled]:
    from engine.effect_spec import _walk_hosts
    bad: Dict[int, Unmodelled] = {}
    for h, creators in _walk_hosts(((root,),), include_granted=False,
                                   include_sub=True):
        for s in iter_specs(h.specs):
            rule = validate_spec(s, h, creators)
            if rule is not None:
                bad[s.seq] = enforce_invariants(s, h, creators).payload
    return bad


def link_host(l1, facts: _normalize.Facts = _normalize.Facts(),
              quotes: Optional[Tuple[str, ...]] = None, face: int = 0,
              *, mode: bool = False, antecedent=None) -> AbilityEffects:
    """L2-L5 for one L1 host (see the module docstring): its
    `AbilityEffects`, modes linked as hosts of their own (each its own
    instruction, CR 700.2, so a sub-ability stops at its mode's end, M8)."""
    ctx = _Ctx(l1, facts, quotes, face, antecedent)
    fms = _patterns.match_host(l1, has_x=facts.has_x_cost)
    items: List[Any] = list(fms)
    if l1.unmodelled:
        # L1 refusals (a replacement static, a level gate, an unread
        # loyalty cost) are UNMODELLED specs in printed order.
        keyed = [(fm.frame.span[0], 1, i, fm) for i, fm in enumerate(fms)]
        keyed += [(s[0], 0, i, _Refusal(um, s))
                  for i, (um, s) in enumerate(l1.unmodelled)]
        items = [x[3] for x in sorted(keyed, key=lambda x: x[:3])]
    root = _Host(l1.kind, trigger=l1.trigger, mode=mode, l1=l1)
    state = {"text": l1.text, "flags": set(l1.flags),
             "cost_modifiers": list(l1.cost_modifiers)}
    _link_frames(ctx, root, items, state)
    modes = tuple(link_host(m, facts, quotes, face, mode=True,
                            antecedent=(ctx.kind, ctx.trigger))
                  for m in l1.modes)
    base = dict(kind=l1.kind, face=l1.face, index=l1.index,
                paragraphs=l1.paragraphs, text=l1.text, trigger=l1.trigger,
                cost=l1.cost, cost_modifiers=tuple(state["cost_modifiers"]),
                cost_condition=l1.cost_condition,
                activation_index=l1.activation_index,
                loyalty_cost=l1.loyalty_cost, loyalty_slot=l1.loyalty_slot,
                chapters=l1.chapters, modes=modes, choose=l1.choose,
                mode_index=l1.mode_index, mode_cost=l1.mode_cost,
                label=l1.label, keywords=l1.keywords, from_zone=l1.from_zone,
                flags=frozenset(state["flags"]),
                restrictions=l1.restrictions)
    # Lower each violating spec and re-validate: a lowering can expose a
    # new violation (a spec that read the lowered one), so the pass repeats
    # until no spec violates. Lowering only grows the lowered set, so this
    # ends; a violation that survives lowering every spec it names lowers
    # the whole host, never returning it with a violation.
    lowered: Dict[int, Unmodelled] = {}
    while True:
        frozen = {"_targets": {}, "_text": l1.text}
        out = _freeze_host(ctx, root, frozen, lowered, base)
        # Modes were validated when they were linked (their seqs are their
        # own).
        bad = _violations(dataclasses.replace(out, modes=()))
        if not bad:
            return out
        new = {k: v for k, v in bad.items() if k not in lowered}
        if not new:
            break
        lowered.update(new)
    from engine.effect_spec import _walk_hosts
    um = next(iter(bad.values()))
    for h, _creators in _walk_hosts(((dataclasses.replace(out, modes=()),),),
                                    include_granted=False, include_sub=True):
        for s in iter_specs(h.specs):
            lowered.setdefault(s.seq, um)
    frozen = {"_targets": {}, "_text": l1.text}
    return _freeze_host(ctx, root, frozen, lowered, base)


# ── The face memo ───────────────────────────────────────────────────────

# The face memo's bound. Like the L1 face memo (structure.FACE_CACHE_SIZE)
# a pool pass never repeats a face, so the memo only absorbs the repeat
# parses of the faces in play; the per-template CardTemplate.effects memo
# holds the rest.
FACE_CACHE_SIZE = 1024


@lru_cache(maxsize=FACE_CACHE_SIZE)
def parse_face_hosts(text: str, facts: _normalize.Facts = _normalize.Facts(),
                     face: int = 0) -> Tuple[AbilityEffects, ...]:
    """L1-L5 for one face's printed text: its hosts in printed order. A
    pure function of ``(text, facts, face)``, the complete fact key (A32):
    every input the parse reads is in it."""
    fs = _structure.parse_face_structure(text or "", facts, face)
    quotes = fs.normalized.quotes
    return tuple(link_host(h, facts, quotes, face) for h in fs.hosts)


def clear_caches() -> None:
    parse_face_hosts.cache_clear()
