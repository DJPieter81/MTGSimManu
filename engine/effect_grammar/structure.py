"""L1 of the clause grammar: paragraph structure (design doc 2026-09-29,
section 3 "L1, structure" and "F1 merge"; A1-A8, A11, A12, M3, M7; E0 step 9).

L1 reads one face's L0 output once, at LOAD, and cuts it into ability
hosts: which paragraph is which kind of ability (CR 113), what each host's
head, cost and riders are, and which spans of its text are the effect text
L2 frames and L3 splits. It types nothing below the paragraph: heads,
costs, conditions and keywords are typed by the leaves that own them, and
the effect text is handed on as ``body`` spans.

**Paragraphs.** L0 keeps paragraphs on single newlines and drops empty
ones, the `oracle_clauses.split_abilities` semantics. Each paragraph is
classified with first-match precedence (section 3):

1. LOYALTY -- ``[+N]:`` / ``[-N]:`` / ``[0]:`` and the variable
   ``[+X]:`` / ``[-X]:`` (`lexicon.parse_loyalty_cost`, A12). The slot comes
   from `oracle_parser.loyalty_slot_for`, the one owner of the slot rule,
   over the face's printed loyalty lines (an X line takes no slot).
2. CHAPTER -- ``i, ii - ``: the chapter numbers (CR 714.2b).
3. KEYWORD -- a list of CR 702 keyword abilities
   (`keywords.parse_keyword_line`, A1) gated by the face's CR 702 keyword
   set (M3), costs typed from the printed span (A7). A sentence after the
   list is classified on its own; a where-X frame stays the keyword's.
4. Ability-word strip -- ``<label> - `` is the host's (or, merged, the
   part's) ``label`` (CR 207.2c); ``channel`` sets ``from_zone='hand'``.
   Classification continues on the rest.
5. ALTERNATIVE_COST -- "rather than pay ~'s mana cost" and "you may cast ~
   without paying its mana cost" (CR 118.9, A2): the cost and its
   condition, no effect text.
6. Modal -- a header in the text ("choose one -"; its count word is read
   from the leaves' one count table, `sub.COUNT_WORDS`, plus "one or
   both", "one or more" and "any number" -- a superset of the legacy
   `oracle_parser._MODAL_HEADER_RE`, which stays the game path's until its
   family migrates), or Tiered / Spree through the face keywords or the
   removed reminder (A4). The header paragraph keeps its own kind (a
   trigger head stays a trigger, CR 700.2); its bullets are MODE hosts with
   ``mode_index`` and, for tiered and spree bullets, ``mode_cost``.
7. Delay-prefixed paragraph (A5, M7) -- on an instant or sorcery a part of
   the SPELL host marked with its delayed timing (CR 603.7); on a
   permanent ``UNMODELLED(STRUCTURE)`` (`duration.delay_paragraph`).
8. TRIGGERED -- the head runs to the first depth-0 comma that is not
   inside a serial list; ``event_hints`` holds every disjunct (A11); a
   leading ``if <cond>,`` is the head's intervening-if (CR 603.4, F9); a
   "this ability triggers only ... each turn" sentence is the head's
   frequency; a mana-event head with no target and an ADD_MANA body is a
   triggered mana ability (flag ``mana_ability``, CR 605.1b, A6).
9. ACTIVATED -- a colon before the first sentence end (quotes are masked,
   so a granted ability's colon is never ours): the cost is
   `oracle_parser.parse_activation_cost` of the PRINTED head (A7), the
   restrictions `oracle_parser.split_activation_riders` of the printed
   body, a "this ability costs ..." sentence a COST_DELTA in
   ``cost_modifiers`` (`payload.parse_cost_modifier`, A8), the ordinal the
   `parse_activated_abilities` rule (a line holding a quote takes none),
   and the kind MANA_ABILITY by CR 605.1a (no target anywhere, an ADD_MANA
   anywhere, riders allowed; A6).
10. ADDITIONAL_COST -- "as an additional cost to cast ~, <cost>".
11. Spell statics on any face (A3) -- "~ can't be countered", "~ costs ...
    to cast", "you may cast ~ as though it had flash", ...:
    ``STATIC(from_zone='stack')``, a cost delta in ``cost_modifiers``.
12. REPLACEMENT -- one ``UNMODELLED(REPLACEMENT)`` over the paragraph
    (CR 614).
13. UNKNOWN -- level structure, power/toughness lines and die-roll tables:
    ``UNMODELLED(STRUCTURE)``. A level gate -- a leveler band
    ``level n-m`` / ``level n+`` (CR 711), a Class level line
    ``{cost}: level n`` (CR 716) or a station threshold row ``n+ | ...``
    on a face with a station line -- opens a block that runs to the next
    gate or the end of the face; the gate and every paragraph in the block
    are ONE refusal host (``level_band`` / ``class_level`` /
    ``station_threshold``), so a gated ability is never emitted as an
    always-on host. The face pass finds the gates before the cascade.
14. Anything else -- SPELL on an instant or sorcery face, STATIC otherwise.

**F1 merge.** The SPELL hosts of an instant or sorcery face (rule 14,
label-prefixed paragraphs, A5 delayed parts and the modal spell host)
merge into one SPELL host, in printed order, at the first one's position:
CR 113.3a / 608.2c, so a later paragraph's "instead" finds its antecedent.
Hosts from rules 1-5 and 8-13 never merge.

**Output.** `parse_face_structure` returns a `FaceStructure`: the L0
output and one `L1Host` per ability. A host's ``text`` is its paragraphs'
normalised text (merged parts joined by newlines) and every span indexes
it. The L1 coverage invariant (section 3): every non-space character of a
host's text is in a ``body`` span (L2's), a ``consumed`` span (a head,
cost, label, rider, keyword, header or bullet), a ``pending`` span (a cost
delta's scaler, typed by L4) or an ``unmodelled`` span -- `uncovered`
reports what is not.

The parse is a pure function of ``(text, facts, face)`` and memoised on it
in a bounded memo (`FACE_CACHE_SIZE`); the printed spans it reads are a
function of the text, so the key is complete (A32).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, FrozenSet, List, NamedTuple, Optional, Tuple

from engine.delayed_triggers import DelayedTriggerTiming
from engine.effect_grammar import keywords, lexicon, normalize
from engine.effect_grammar.sub import (CACHE_SIZE, COUNT_WORDS, NUMBER_WORDS,
                                       Span, condition, duration,
                                       participant, payload, target,
                                       unmodelled)
from engine.effect_grammar.sub import filter as _filter
from engine.effect_model import Modification
from engine.effect_spec import (Amount, Condition, CostSnapshot, EventHint,
                                HostKind, KeywordSpec, Stage, TriggerHead,
                                Unmodelled, freeze_cost)
from engine.oracle_parser import (loyalty_slot_for, parse_activation_cost,
                                  split_activation_riders,
                                  strip_reminder_text)

__all__ = ["LEAF", "DETAIL_CODES", "FACE_CACHE_SIZE", "Part", "L1Host",
           "FaceStructure", "parse_face_structure", "uncovered",
           "clear_caches"]

LEAF = "structure"
DETAIL_CODES = frozenset({
    "orphan_mode",            # a bullet with no modal header before it
    "unterminated_head",      # a trigger head with no comma ending it
    "replacement_static",     # a CR 614 static (rule 12)
    "level_band",             # "level 1-3": a leveler band (CR 711)
    "class_level",            # "{2}{r}: level 2": a Class level (CR 716)
    "station_threshold",      # "5+ | flying": a station threshold row
    "pt_line",                # "3/3": a power/toughness line outside a band
    "die_table",              # "1-9 | ...": a die-roll result row (CR 706)
})

_SPELL_TYPES = frozenset({"instant", "sorcery"})


def _um(stage: Stage, code: str) -> Unmodelled:
    return unmodelled(stage, "", LEAF, code, DETAIL_CODES)


@lru_cache(maxsize=CACHE_SIZE)
def _cost(printed: str) -> Tuple[bool, Optional[CostSnapshot]]:
    """(the cost owner reads it, its frozen image): one
    `parse_activation_cost` per distinct printed cost text (A7, A31)."""
    cost = parse_activation_cost(printed)
    return cost is not None, freeze_cost(cost)


# ── Output ──────────────────────────────────────────────────────────────

class Part(NamedTuple):
    """One paragraph (or paragraph piece) of a host. A merged SPELL host
    keeps one part per paragraph: its label and its A5 delayed timing are
    the part's, so L2 frames each part with them."""
    paragraph: int
    span: Span                                   # the piece in L1Host.text
    body: Tuple[Span, ...] = ()
    label: str = ""
    delay: Optional[DelayedTriggerTiming] = None


class L1Host(NamedTuple):
    """One ability host as L1 cuts it: the `AbilityEffects` fields L1 owns,
    plus the spans L2 reads (``body``) and the spans L1 consumed. Spans
    index ``text``. A NamedTuple: immutable and hashable like the schema's
    frozen dataclasses, and built once per host at a tuple's cost (the
    pool builds ~41k)."""
    kind: HostKind
    face: int
    index: int
    paragraphs: Tuple[int, ...]
    text: str
    body: Tuple[Span, ...] = ()
    consumed: Tuple[Tuple[str, Span], ...] = ()
    pending: Tuple[Tuple[str, Span], ...] = ()
    unmodelled: Tuple[Tuple[Unmodelled, Span], ...] = ()
    parts: Tuple[Part, ...] = ()
    trigger: Optional[TriggerHead] = None
    cost: Optional[CostSnapshot] = None
    cost_modifiers: Tuple[Modification, ...] = ()
    cost_condition: Optional[Condition] = None
    activation_index: Optional[int] = None
    loyalty_cost: Optional[Amount] = None
    loyalty_slot: str = ""
    chapters: Tuple[int, ...] = ()
    modes: Tuple["L1Host", ...] = ()
    choose: Tuple[int, int] = (0, 0)
    mode_index: int = -1
    mode_cost: str = ""
    label: str = ""
    keywords: Tuple[KeywordSpec, ...] = ()
    from_zone: str = "battlefield"
    flags: FrozenSet[str] = frozenset()
    restrictions: Tuple[str, ...] = ()


class FaceStructure(NamedTuple):
    face: int
    normalized: normalize.Normalized
    hosts: Tuple[L1Host, ...]


# ── Builders (mutable while one paragraph is cut) ───────────────────────

@dataclass
class _B:
    kind: HostKind
    paragraph: int
    text: str
    body: List[Span] = field(default_factory=list)
    consumed: List[Tuple[str, Span]] = field(default_factory=list)
    pending: List[Tuple[str, Span]] = field(default_factory=list)
    unmodelled: List[Tuple[Unmodelled, Span]] = field(default_factory=list)
    label: str = ""
    delay: Optional[DelayedTriggerTiming] = None
    paragraphs: List[int] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    modes: List["_B"] = field(default_factory=list)
    flags: set = field(default_factory=set)

    def shift(self, prefix: str) -> None:
        """Prepend `prefix` to the text, moving every span."""
        n = len(prefix)
        if not n:
            return
        self.text = prefix + self.text
        mv = lambda s: (s[0] + n, s[1] + n)
        self.body = [mv(s) for s in self.body]
        self.consumed = [(k, mv(s)) for k, s in self.consumed]
        self.pending = [(k, mv(s)) for k, s in self.pending]
        self.unmodelled = [(u, mv(s)) for u, s in self.unmodelled]


def _trim(text: str, a: int, b: int) -> Optional[Span]:
    while a < b and text[a] in " \n":
        a += 1
    while b > a and text[b - 1] in " \n":
        b -= 1
    return (a, b) if a < b else None


def _subtract(text: str, region: Span, holes) -> List[Span]:
    """`region` minus `holes`, as trimmed spans."""
    out, pos = [], region[0]
    for a, b in sorted(holes):
        if b <= pos or a >= region[1]:
            continue
        s = _trim(text, pos, min(a, region[1]))
        if s:
            out.append(s)
        pos = max(pos, b)
    s = _trim(text, pos, region[1])
    if s:
        out.append(s)
    return out


# ── Patterns (closed, over L0 output) ──────────────────────────────────

_CHAPTER_RE = re.compile(r"(?P<ch>[ivx]+(?:, [ivx]+)*) - ")
_ROMAN = {"i": 1, "v": 5, "x": 10}
# An ability word / flavor word / labelled keyword ability: a short run of
# words before " - " at paragraph start (CR 207.2c).
_LABEL_RE = re.compile(r"(?P<label>[a-z][a-z'!&]*(?: [a-z0-9'!&]+){0,5}) - (?=\S)")
# The header's count word comes from the leaves' one count table
# (`sub.COUNT_WORDS`, longest first), so a printed count is a header count
# in every size or in none.
_COUNT_ALT = "|".join(COUNT_WORDS)
_HEADER_COUNT = (r"one or both|one or more|any number|up to (?:%s)|%s"
                 % (_COUNT_ALT, _COUNT_ALT))
# A modal header (A4, CR 700.2): the paragraph's last "choose <N>",
# ending it or followed by sentences about the choice ("if this spell was
# kicked, choose both instead.", "you may choose the same mode more than
# once."), which stay the header host's body. "up to x, where x is ..."
# keeps its where-X frame in the header.
_HEADER_RE = re.compile(
    r"(?:^|(?<=[ ,.:]))choose (?P<n>%s|up to x)(?: that hasn't been chosen"
    r"(?: this turn)?)?(?: at random)?(?:, where x is [^.]*?)?"
    r"(?: -|\.|:)(?=$| )" % _HEADER_COUNT)
# The removed reminder of Tiered / Spree (A4).
_REMINDER_HEADER_RE = re.compile(r"choose (?P<n>%s) additional costs?\b"
                                 % _HEADER_COUNT)
_TIERED_BULLET_RE = re.compile(
    r"• (?P<name>[^{}\-•]+?) - (?P<cost>(?:\{[^{}]+\})+) - ")
_SPREE_BULLET_RE = re.compile(r"\+ (?P<cost>(?:\{[^{}]+\})+) - ")
_BULLET_RE = re.compile(r"• ")
_TRIGGER_RE = re.compile(r"(?:when|whenever|at)\b")
_FREQ_RE = re.compile(r"(?:^|(?<=\. ))this ability triggers only "
                      r"(?P<f>once|twice|[a-z]+ times) each turn\.")
_RIDER_RE = re.compile(r"(?:^|(?<=[ .]))activate only[^.]*\.")
_SENT_RE = re.compile(r"[^.]+(?:\.|$)")
_ALT_COST_RE = re.compile(
    r"(?:(?P<cond>if [^.]*?), )?(?:you may (?P<cost>[^.]*?) rather than pay "
    r"(?:~'s|its) mana cost|you may cast ~ without paying its mana cost"
    r"(?P<tail>[^.]*))\.")
_ADD_COST_RE = re.compile(r"as an additional cost to cast ~, (?P<cost>[^.]+)\.")
_SPELL_STATIC_RE = re.compile(
    r"(?:~ can't be countered[^.]*|~ costs [^.]*? to cast[^.]*|"
    r"you may cast ~ as though it had flash[^.]*|~ can be cast[^.]*|"
    r"cast ~ only[^.]*|you can't cast ~[^.]*|~ can't be copied[^.]*)\.")
_REPLACEMENT_RE = re.compile(
    r"(?:as ~ enters|as ~ is turned face up|if [^.]*\bwould\b|"
    r"[^.]*\bwould\b[^.]*\binstead\b|prevent all [^.]*\bdamage\b[^.]*\bwould\b|"
    r"[^.]*\benters? (?:the battlefield )?(?:tapped|with)\b)")
_SPELL_REPLACEMENT_RE = re.compile(r"(?:if ~ would|as ~ enters)\b")
_UNKNOWN_RE = (
    (re.compile(r"\d+/\d+$"), "pt_line"),
    (re.compile(r"\d+(?:-\d+|\+)? \| "), "die_table"),
)


# Level gates (rule 13): each opens a block of gated paragraphs.
_LEVEL_BAND_RE = re.compile(r"level \d+(?:-\d+|\+)$")
_CLASS_LEVEL_RE = re.compile(r"(?:\{[^{}]+\})+: level \d+$")
_STATION_ROW_RE = re.compile(r"\d+\+ \| ")
_STATION_LINE_RE = re.compile(r"station\b")


def _level_gates(paragraphs) -> dict:
    """paragraph index -> detail code of every level gate on the face."""
    station = any(_STATION_LINE_RE.match(t) for t in paragraphs)
    out = {}
    for p, t in enumerate(paragraphs):
        if t.startswith("level ") and _LEVEL_BAND_RE.match(t):
            out[p] = "level_band"
        elif ": level " in t and _CLASS_LEVEL_RE.match(t):
            out[p] = "class_level"
        elif station and t[:1].isdigit() and _STATION_ROW_RE.match(t):
            out[p] = "station_threshold"
    return out


# ── Trigger heads (A11) ────────────────────────────────────────────────

# A second trigger event sharing the body (A11): "when ~ enters and
# whenever ~ attacks", "... or at the beginning of ...". "and at least two
# other creatures attack" is one event.
_DISJUNCT_RE = re.compile(
    r",? (?:and|or) (?=(?:whenever|when|at the beginning of) )")
_TRIGGER_WORD_RE = re.compile(r"(?:whenever|when|at) ")
# The event verbs, singular and plural: "one or more <objects> die /
# enter / attack / leave the battlefield" is the singular's event, once
# per object (CR 603.2).
_VERB_RE = re.compile(r"\b(?:enters|enter|dies|die|attacks|attack|"
                      r"leaves the battlefield|leave the battlefield|"
                      r"is put into a graveyard from the battlefield|"
                      r"are put into a graveyard from the battlefield)\b")
_SINGULAR = {"enter": "enters", "die": "dies", "attack": "attacks",
             "leave the battlefield": "leaves the battlefield"}
_SELF_HINT = {"enters": EventHint.SELF_ENTERS, "dies": EventHint.SELF_DIES,
              "attacks": EventHint.SELF_ATTACKS,
              "leaves the battlefield": EventHint.SELF_LEAVES}
_OTHER_HINT = {"enters": EventHint.OTHER_ENTERS, "dies": EventHint.OTHER_DIES,
               "attacks": EventHint.ATTACKS_OTHER,
               "leaves the battlefield": EventHint.OTHER}
_LAND_WORDS = frozenset({"land", "lands"}) | _filter.LAND_SUBTYPES | {
    w + "s" for w in _filter.LAND_SUBTYPES if not w.endswith("s")}
_COMBAT_TO_PLAYER_RE = re.compile(
    r"deals? combat damage to (?:a player|an opponent|one or more players|"
    r"that player|defending player|you|your opponent)\b")
_SUBJECT_NOUN_RE = re.compile(r"[a-z]")


def _verb_key(v: str) -> str:
    if v.endswith("from the battlefield"):
        return "dies"
    return _SINGULAR.get(v, v)


def _names_land(subject: str) -> bool:
    """Does a trigger subject name a land (CR 205.3i: "a land", "a
    Mountain", "one or more Forests"), and no creature?"""
    words = subject.split()
    return "creature" not in subject and any(w in _LAND_WORDS for w in words)


def _part_hints(part: str) -> Tuple[Tuple[EventHint, ...], str]:
    """The event hints of one trigger disjunct and its step phrase."""
    m = _TRIGGER_WORD_RE.match(part)
    body = part[m.end():] if m else part
    if body.startswith("the beginning of "):
        return (EventHint.BEGINNING_OF,), body[len("the beginning of "):]
    if re.search(r"\bfor mana\b", body):
        return (EventHint.TAPPED_FOR_MANA,), ""
    if re.search(r"\bcasts? ~(?![\w'])", body):
        return (EventHint.SELF_CAST,), ""
    if re.search(r"\bcasts?\b", body):
        return (EventHint.SPELL_CAST,), ""
    if re.search(r"\bcycles?\b|\bcycling\b", body):
        return (EventHint.CYCLE,), ""
    if _COMBAT_TO_PLAYER_RE.search(body):
        return (EventHint.COMBAT_DAMAGE_TO_PLAYER,), ""
    if re.search(r"\bcounters? (?:is|are) put on\b", body):
        return (EventHint.COUNTERS_PUT,), ""
    verbs = list(_VERB_RE.finditer(body))
    if not verbs:
        return (EventHint.OTHER,), ""
    subject = body[:verbs[0].start()]
    is_self = "~" in subject
    other = subject.replace("~", "").replace(" or ", " ").replace(" and ", " ")
    is_other = bool(_SUBJECT_NOUN_RE.search(other)) or not is_self
    land = _names_land(subject)
    hints = []
    for v in verbs:
        key = _verb_key(v.group())
        if is_self and key in _SELF_HINT:
            hints.append(_SELF_HINT[key])
        if is_other:
            if key == "enters" and land:
                hints.append(EventHint.LANDFALL)
            else:
                hints.append(_OTHER_HINT.get(key, EventHint.OTHER))
    return tuple(hints), ""


def _event_hints(head: str) -> Tuple[Tuple[EventHint, ...], str]:
    out, step = [], ""
    for part in _DISJUNCT_RE.split(head):
        hints, s = _part_hints(part)
        step = step or s
        for h in hints:
            if h not in out:
                out.append(h)
    return tuple(out), step


_WORD_START_RE = re.compile(r"(?<![\w'~-])[a-z]")


def _holds_verb(t: str, a: int, b: int) -> bool:
    """Does ``t[a:b]`` hold a lexicon verb at any token (A13, read in
    the whole host so the verb reader sees its left context)?"""
    return any(lexicon.verb_at(t, m.start()) is not None
               for m in _WORD_START_RE.finditer(t, a, b))


# A head list member is a short noun phrase: "orc", "mana value",
# "non-angel creature". A longer segment is a clause, not a member.
_MEMBER_MAX_WORDS = 3
_CONJ = ("or ", "and ", "and/or ")


def _list_continues(t: str, pos: int) -> bool:
    """Does a serial list run on from ``t[pos:]`` and close inside the
    head: members of at most `_MEMBER_MAX_WORDS` words holding no
    lexicon verb, then a member opened by "or" / "and" / "and/or" that
    a further comma follows (the head's own end)? A list that reaches the
    sentence end is the body's ("..., and rats you control get +1/+1.")."""
    while True:
        nxt = t.find(", ", pos)
        stop = t.find(".", pos)
        if nxt < 0 or 0 <= stop < nxt:
            return False
        if t.startswith(_CONJ, pos):
            return True
        if t.count(" ", pos, nxt) >= _MEMBER_MAX_WORDS or \
                _holds_verb(t, pos, nxt):
            return False
        pos = nxt + 2


def _head_end(t: str) -> Optional[int]:
    """The comma ending a trigger head (rule 8): the first depth-0 comma
    after which the remainder is a sentence (quotes are masked by L0).
    The remainder is no sentence when it continues a list: it opens with
    "or" / "and" / "and/or", it is the next of coordinate negated
    adjectives ("a nontoken, non-angel creature", CR 205.4b), or it is a
    member of a serial list that closes inside the head (`_list_continues`:
    subtypes, colours, numbers, keyword actions alike). An intervening
    "if" always starts the remainder."""
    for m in re.finditer(r",(?: |$)", t):
        pos = m.end()
        if t.startswith(_CONJ, pos):
            continue
        if t.startswith("if ", pos):
            return m.start()
        if t.startswith("non", pos) and t.startswith(
                "non", t.rfind(" ", 0, m.start()) + 1):
            continue
        if _list_continues(t, pos):
            continue
        return m.start()
    return None


# ── The face context ────────────────────────────────────────────────────

class _Ctx:
    __slots__ = ("facts", "face", "is_spell", "printed", "starts", "cands",
                 "ordinal", "loyalty")

    def __init__(self, facts, face, printed, paragraphs):
        self.facts = facts
        self.face = face
        self.is_spell = bool(_SPELL_TYPES & facts.type_class) or facts.is_spell
        self.printed = printed
        self.starts = []
        pos = 0
        for p in paragraphs:
            self.starts.append(pos)
            pos += len(p) + 1
        # M3: the face's CR 702 set gates keyword lines; a face with none
        # (a synthetic template has no keyword data) reads the table alone.
        self.cands = facts.keywords702 or None
        self.ordinal = self._ordinals(paragraphs)
        self.loyalty = self._loyalty_slots(paragraphs)

    def printed_at(self, p: int, off: int) -> Callable[[Span], str]:
        base = self.starts[p] + off
        return lambda s: self.printed((s[0] + base, s[1] + base))

    def _ordinals(self, paragraphs):
        """`parse_activated_abilities` ordinals by paragraph: a line with a
        colon, no quote, and a cost the cost owner reads (loyalty lines
        give None there) takes the next index."""
        out, idx = {}, 0
        for p, para in enumerate(paragraphs):
            c = para.find(":")
            if c < 0 or "⟨q" in para or '"' in para:
                continue
            printed = strip_reminder_text(self.printed_at(p, 0)((0, len(para))))
            if '"' in printed or "“" in printed or ":" not in printed:
                continue
            if not _cost(printed.partition(":")[0])[0]:
                continue
            out[p] = idx
            idx += 1
        return out

    def _loyalty_slots(self, paragraphs):
        lines = []
        for p, para in enumerate(paragraphs):
            r = lexicon.parse_loyalty_cost(para) if para[:1] == "[" else None
            if r is None:
                continue
            lines.append((p, lexicon.loyalty_slot_cost(r.value)
                          if r.value is not None else "x"))
        costs = [c for _, c in lines]
        return {p: loyalty_slot_for(costs, i) for i, (p, _) in enumerate(lines)}


# ── Rules 1-14 over one paragraph piece ────────────────────────────────

def _sentences(t: str, a: int, b: int) -> List[Span]:
    return [(a + m.start(), a + m.end()) for m in _SENT_RE.finditer(t[a:b])
            if t[a + m.start():a + m.end()].strip(" .")]


def _rest_after(ctx, p, t, off, end, label):
    """Classify the text after `end` as hosts of its own."""
    s = _trim(t, end, len(t))
    if s is None:
        return []
    return _classify(ctx, p, t[s[0]:], off + s[0], label)


def _classify(ctx: _Ctx, p: int, t: str, off: int, label: str = "") -> List[_B]:
    n = len(t)
    # 1. LOYALTY
    r = lexicon.parse_loyalty_cost(t) if t[:1] == "[" else None
    if r is not None:
        b = _B(HostKind.LOYALTY, p, t, label=label)
        if r.unmodelled is not None:
            b.unmodelled.append((r.unmodelled, r.span))
            b.extra["loyalty_slot"] = ctx.loyalty.get(p, "")
            return [b]
        b.extra["loyalty_cost"] = r.value
        b.extra["loyalty_slot"] = ctx.loyalty.get(p, "")
        b.consumed.append(("loyalty_cost", r.span))
        b.body.extend(r.rest_spans)
        return [b]
    # 2. CHAPTER. Each rule below is pre-gated on a literal its pattern
    # cannot match without, so the cascade's order is unchanged.
    m = _CHAPTER_RE.match(t) if t[:1] in "ivx" else None
    if m:
        b = _B(HostKind.CHAPTER, p, t, label=label)
        b.extra["chapters"] = tuple(_roman(x) for x in m.group("ch").split(", "))
        b.consumed.append(("chapter", (0, m.end())))
        s = _trim(t, m.end(), n)
        if s:
            b.body.append(s)
        return [b]
    # 3. KEYWORD (A1, M3, A7)
    r = keywords.parse_keyword_line(t, candidates=ctx.cands,
                                    printed=ctx.printed_at(p, off))
    if r is not None:
        b = _B(HostKind.KEYWORD, p, t, label=label)
        if r.unmodelled is not None:
            b.unmodelled.append((r.unmodelled, r.span))
            return [b]
        b.extra["keywords"] = r.value
        b.consumed.append(("keyword", r.span))
        if not r.rest_spans:
            return [b]
        rs = r.rest_spans[0][0]
        if t.startswith("where x is", rs):
            b.body.extend(r.rest_spans)
            return [b]
        # A keyword ability's own riders ("equip {0}. activate only once
        # each turn.", "this ability costs {1} less to activate ...") stay
        # on it; any other sentence is an ability of its own.
        end = _absorb_riders(ctx, p, t, off, b, rs)
        b.text = t[:end]
        return [b] + _rest_after(ctx, p, t, off, end, "")
    # 4. Ability-word strip (CR 207.2c)
    m = _LABEL_RE.match(t) if " - " in t else None
    if m and not label and not _HEADER_RE.match(t) and \
            not m.group("label").startswith("choose "):
        lab = m.group("label")
        rest = _classify(ctx, p, t[m.end():], off + m.end(), lab)
        if rest:
            first = rest[0]
            first.shift(t[:m.end()])
            first.consumed.insert(0, ("label", (0, m.end())))
            first.label = lab
            if lab == "channel":
                first.extra["from_zone"] = "hand"
        return rest
    # 5. ALTERNATIVE_COST (CR 118.9, A2)
    m = _ALT_COST_RE.match(t) if "mana cost" in t else None
    if m:
        b = _B(HostKind.ALTERNATIVE_COST, p, t, label=label)
        if m.group("cond"):
            c = condition.parse_condition(t, m.span("cond"))
            if c is not None and c.value is not None:
                b.extra["cost_condition"] = c.value
            elif c is not None:
                b.unmodelled.append((c.unmodelled, m.span("cond")))
        if m.group("cost"):
            b.extra["cost"] = _cost(ctx.printed_at(p, off)(m.span("cost")))[1]
        if (m.group("tail") or "").strip():
            c = condition.parse_condition(t, m.span("tail"))
            if c is not None and c.value is not None:
                b.extra["cost_condition"] = c.value
            elif c is not None:
                b.unmodelled.append((c.unmodelled, m.span("tail")))
        b.consumed.append(("alternative_cost", (0, m.end())))
        b.text = t[:m.end()]
        return [b] + _rest_after(ctx, p, t, off, m.end(), "")
    # 7. Delay-prefixed paragraph (A5, M7)
    if t.startswith("at the beginning of "):
        d = duration.delay_paragraph(t, ctx.is_spell)
        if d is not None:
            if d.unmodelled is not None:
                b = _B(HostKind.UNKNOWN, p, t, label=label)
                b.unmodelled.append((d.unmodelled, (0, n)))
                return [b]
            b = _B(HostKind.SPELL, p, t, label=label, delay=d.value)
            b.consumed.append(("delay", d.span))
            b.body.extend(d.rest_spans)
            return [b]
    # 8. TRIGGERED
    if _TRIGGER_RE.match(t):
        return [_triggered(ctx, p, t, label)]
    # 9. ACTIVATED / MANA_ABILITY
    c = t.find(":")
    e = t.find(". ")
    if c > 0 and (e < 0 or c < e):
        b = _activated(ctx, p, t, off, c, label)
        if b is not None:
            return [b]
    # 10. ADDITIONAL_COST
    m = _ADD_COST_RE.match(t) if t.startswith("as an additional") else None
    if m:
        b = _B(HostKind.ADDITIONAL_COST, p, t, label=label)
        b.extra["cost"] = _cost(ctx.printed_at(p, off)(m.span("cost")))[1]
        b.consumed.append(("additional_cost", (0, m.end())))
        b.text = t[:m.end()]
        return [b] + _rest_after(ctx, p, t, off, m.end(), "")
    # 11. Spell statics on any face (A3)
    m = _SPELL_STATIC_RE.match(t) if "~" in t else None
    if m:
        end = m.end()
        while True:
            s = _trim(t, end, n)
            m2 = _SPELL_STATIC_RE.match(t, s[0]) if s else None
            if m2 is None:
                break
            end = m2.end()
        return [_spell_static(t, p, end, label)] + _rest_after(
            ctx, p, t, off, end, "")
    # 12. REPLACEMENT (CR 614)
    if ("would" in t or "enter" in t or "as ~ is turned" in t) and (
            _SPELL_REPLACEMENT_RE.match(t) if ctx.is_spell
            else _REPLACEMENT_RE.match(t)):
        b = _B(HostKind.REPLACEMENT, p, t, label=label)
        b.unmodelled.append((_um(Stage.REPLACEMENT, "replacement_static"), (0, n)))
        return [b]
    # 13. UNKNOWN
    for rx, code in (_UNKNOWN_RE if t[:1].isdigit() or t[:1] == "l" else ()):
        if rx.match(t):
            b = _B(HostKind.UNKNOWN, p, t, label=label)
            b.unmodelled.append((_um(Stage.STRUCTURE, code), (0, n)))
            return [b]
    # 14. SPELL / STATIC
    b = _B(HostKind.SPELL if ctx.is_spell else HostKind.STATIC, p, t,
           label=label)
    s = _trim(t, 0, n)
    if s:
        b.body.append(s)
    return [b]


def _roman(s: str) -> int:
    total = 0
    for i, ch in enumerate(s):
        v = _ROMAN[ch]
        total += -v if i + 1 < len(s) and _ROMAN[s[i + 1]] > v else v
    return total


def _head(hints, raw: str, **kw) -> TriggerHead:
    """A trigger head, with what its event names typed by the participant
    leaf (the one noun table)."""
    player, obj = participant.head_names(raw)
    return TriggerHead(event_hints=hints, raw=raw, names_player=player,
                       names_object=obj, **kw)


def _triggered(ctx: _Ctx, p: int, t: str, label: str) -> _B:
    b = _B(HostKind.TRIGGERED, p, t, label=label)
    n = len(t)
    end = _head_end(t)
    if end is None:
        b.unmodelled.append((_um(Stage.STRUCTURE, "unterminated_head"), (0, n)))
        b.extra["trigger"] = _head(_event_hints(t)[0], t)
        return b
    head = t[:end]
    hints, step = _event_hints(head)
    b.consumed.append(("head", (0, end + 1)))
    pos = end + 1
    while pos < n and t[pos] == " ":
        pos += 1
    intervening = None
    if t.startswith("if ", pos):
        cnd = condition.parse_condition(t, (pos, n))
        if cnd is not None and cnd.value is not None:
            intervening = cnd.value
            b.consumed.append(("intervening_if", cnd.span))
            pos = cnd.rest_spans[0][0] if cnd.rest_spans else n
        elif cnd is not None:
            cut = t.find(", ", pos)
            cut = n if cut < 0 else cut + 1
            b.unmodelled.append((cnd.unmodelled, (pos, cut)))
            pos = cut
    once, freq = False, ""
    holes = []
    for fm in _FREQ_RE.finditer(t, pos):
        once = fm.group("f") == "once"
        freq = t[fm.start():fm.end() - 1]
        holes.append(fm.span())
        b.consumed.append(("frequency", fm.span()))
    b.body.extend(_subtract(t, (pos, n), holes))
    b.extra["trigger"] = _head(hints, head, step=step,
                               intervening_if=intervening,
                               once_each_turn=once, frequency_raw=freq)
    if EventHint.TAPPED_FOR_MANA in hints and \
            not target.target_words(t, (pos, n)) and \
            payload.adds_mana(t, (pos, n)):
        b.flags.add("mana_ability")
    return b


def _riders_and_cost_modifiers(ctx: _Ctx, p: int, t: str, off: int, b: _B,
                               a: int, z: int) -> List[Span]:
    """Absorb the activation riders and cost deltas of ``t[a:z]`` onto the
    host: "Activate only ..." sentences are restrictions read from the
    printed text by `split_activation_riders` (sorcery speed a host flag,
    once each turn a restriction), and "This ability costs ..." a
    COST_DELTA in ``cost_modifiers`` (A8) whose scaler is left pending for
    L4. Returns the absorbed spans."""
    holes = []
    for m in _RIDER_RE.finditer(t, a, z):
        holes.append(m.span())
        b.consumed.append(("rider", m.span()))
    if holes:
        _, restrictions, sorcery, once = split_activation_riders(
            ctx.printed_at(p, off)((a, z)))
        b.extra["restrictions"] = tuple(restrictions) + (
            ("once_each_turn",) if once else ())
        if sorcery:
            b.flags.add("sorcery_speed")
    mods = list(b.extra.get("cost_modifiers", ()))
    for s, e in _sentences(t, a, z):
        if "costs" not in t[s:e]:
            continue
        r = payload.parse_cost_modifier(t, (s, e))
        if r is None:
            continue
        if r.value is None:
            b.unmodelled.append((r.unmodelled, r.span))
        else:
            mods.append(r.value)
            b.consumed.append(("cost_modifier", r.span))
            b.pending.extend(("cost_modifier", sp) for sp in r.rest_spans)
        holes.append((s, e))
    b.extra["cost_modifiers"] = tuple(mods)
    return holes


def _absorb_riders(ctx: _Ctx, p: int, t: str, off: int, b: _B,
                   start: int) -> int:
    """The end of the run of rider / cost-delta sentences at ``t[start:]``
    absorbed onto keyword host `b` (an equip or crew ability's own
    riders)."""
    end = start
    for s, e in _sentences(t, start, len(t)):
        sent = t[s:e]
        if not (_RIDER_RE.match(sent) or (
                "costs" in sent and payload.parse_cost_modifier(t, (s, e)))):
            break
        end = e
    if end > start:
        _riders_and_cost_modifiers(ctx, p, t, off, b, start, end)
    return end


def _activated(ctx: _Ctx, p: int, t: str, off: int, c: int,
               label: str) -> Optional[_B]:
    n = len(t)
    printed = ctx.printed_at(p, off)
    ok, cost = _cost(printed((0, c)))
    if not ok:
        return None
    b = _B(HostKind.ACTIVATED, p, t, label=label)
    b.extra["cost"] = cost
    b.extra["activation_index"] = ctx.ordinal.get(p)
    b.consumed.append(("cost", (0, c + 1)))
    holes = _riders_and_cost_modifiers(ctx, p, t, off, b, c + 1, n)
    b.body.extend(_subtract(t, (c + 1, n), holes))
    # CR 605.1a (A6): no target anywhere, an ADD_MANA anywhere.
    if not target.target_words(t, (c + 1, n)) and payload.adds_mana(t, (c + 1, n)):
        b.kind = HostKind.MANA_ABILITY
    return b


def _spell_static(t: str, p: int, end: int, label: str) -> _B:
    b = _B(HostKind.STATIC, p, t[:end], label=label)
    b.extra["from_zone"] = "stack"
    mods = []
    for s, e in _sentences(t, 0, end):
        sent = t[s:e]
        if sent.startswith("~ can't be countered"):
            b.flags.add("uncounterable")
            b.consumed.append(("spell_static", (s, e)))
            continue
        r = payload.parse_cost_modifier(t, (s, e)) if "costs" in sent else None
        if r is not None and r.value is not None:
            mods.append(r.value)
            b.consumed.append(("cost_modifier", r.span))
            b.pending.extend(("cost_modifier", sp) for sp in r.rest_spans)
        elif r is not None:
            b.unmodelled.append((r.unmodelled, (s, e)))
        else:
            b.body.append(_trim(t, s, e))
    b.extra["cost_modifiers"] = tuple(mods)
    return b


# ── Modal blocks (A4, CR 700.2) ────────────────────────────────────────

def _bullet(p: int, t: str) -> Optional[_B]:
    """A MODE host for a bullet paragraph, or None if `t` is no bullet."""
    if t[:1] not in "•+":
        return None
    m = _TIERED_BULLET_RE.match(t)
    cost = name = ""
    if m:
        name, cost = m.group("name"), m.group("cost")
    else:
        m = _SPREE_BULLET_RE.match(t)
        if m:
            cost = "+ " + m.group("cost")
        else:
            m = _BULLET_RE.match(t)
            if m is None:
                return None
    b = _B(HostKind.MODE, p, t, label=name)
    b.extra["mode_cost"] = cost
    b.consumed.append(("bullet", (0, m.end())))
    s = _trim(t, m.end(), len(t))
    if s:
        b.body.append(s)
    return b


def _bounds(word: str, n_modes: int) -> Tuple[int, int]:
    if word == "one or both":
        return 1, 2
    if word == "one or more":
        return 1, n_modes
    if word == "any number":
        return 0, n_modes
    if word == "up to x":
        return 0, n_modes                   # X bounds it at resolution
    if word.startswith("up to "):
        return 0, NUMBER_WORDS[word[6:]]
    return NUMBER_WORDS[word], NUMBER_WORDS[word]


def _reminder_bounds(ctx: _Ctx, norm, n_modes: int) -> Optional[Tuple[int, int]]:
    kws = ctx.facts.keywords702
    for r in norm.reminders:
        m = _REMINDER_HEADER_RE.search(r.text)
        if m:
            return _bounds(m.group("n"), n_modes)
    if "spree" in kws:
        return 1, n_modes
    if "tiered" in kws:
        return 1, 1
    return None


def _face_builders(ctx: _Ctx, norm, paragraphs) -> List[_B]:
    out: List[_B] = []
    p = 0
    np_ = len(paragraphs)
    gates = _level_gates(paragraphs)
    while p < np_:
        t = paragraphs[p]
        if p in gates:
            q = next((g for g in sorted(gates) if g > p), np_)
            b = _B(HostKind.UNKNOWN, p, "\n".join(paragraphs[p:q]))
            b.paragraphs = list(range(p, q))
            b.unmodelled.append((_um(Stage.STRUCTURE, gates[p]),
                                 (0, len(b.text))))
            out.append(b)
            p = q
            continue
        modes = []
        q = p + 1
        while q < np_:
            mb = _bullet(q, paragraphs[q])
            if mb is None:
                break
            modes.append(mb)
            q += 1
        if modes and _bullet(p, t) is None:
            hm = None
            for hm in _HEADER_RE.finditer(t):
                pass
            if hm is not None:
                head = t[:hm.start()].rstrip(" ")
                built = _classify(ctx, p, head, 0) if head else []
                if not built:
                    built = [_B(HostKind.SPELL if ctx.is_spell
                                else HostKind.STATIC, p, "")]
                host = built[-1]
                # The header ends the paragraph's last piece: append it,
                # and any sentence after it, to that piece (its text ends
                # where `head` ends).
                shift = len(host.text) - len(head)
                host.text += t[len(head):]
                host.consumed.append(("header", (hm.start() + shift,
                                                 hm.end() + shift)))
                tail = _trim(t, hm.end(), len(t))
                if tail:
                    host.body.append((tail[0] + shift, tail[1] + shift))
                host.extra["choose"] = _bounds(hm.group("n"), len(modes))
                _attach(host, modes)
                out.extend(built)
                p = q
                continue
            bounds = _reminder_bounds(ctx, norm, len(modes))
            if bounds is not None:
                out.extend(_classify(ctx, p, t, 0))
                host = _B(HostKind.SPELL if ctx.is_spell else HostKind.STATIC,
                          modes[0].paragraph, "")
                host.extra["choose"] = bounds
                _attach(host, modes)
                out.append(host)
                p = q
                continue
        mb = _bullet(p, t)
        if mb is not None:
            b = _B(HostKind.UNKNOWN, p, t)
            b.unmodelled.append((_um(Stage.STRUCTURE, "orphan_mode"), (0, len(t))))
            out.append(b)
            p += 1
            continue
        out.extend(_classify(ctx, p, t, 0))
        p += 1
    return out


def _attach(host: _B, modes: List[_B]) -> None:
    host.modes = modes
    host.paragraphs = sorted({host.paragraph, *(m.paragraph for m in modes)})


# ── F1 merge and freezing ──────────────────────────────────────────────

def _freeze_mode(ctx: _Ctx, b: _B, i: int) -> L1Host:
    return L1Host(kind=HostKind.MODE, face=ctx.face, index=i,
                  paragraphs=(b.paragraph,), text=b.text, body=tuple(b.body),
                  consumed=tuple(b.consumed), pending=tuple(b.pending),
                  unmodelled=tuple(b.unmodelled),
                  parts=(Part(b.paragraph, (0, len(b.text)), tuple(b.body),
                              b.label),),
                  mode_index=i, mode_cost=b.extra.get("mode_cost", ""),
                  label=b.label)


def _host(ctx: _Ctx, first: _B, index: int, paragraphs, text, body,
          consumed, pending, unm, parts, modal: Optional[_B], flags,
          label: str) -> L1Host:
    x = first.extra
    return L1Host(
        first.kind, ctx.face, index, paragraphs, text, body, consumed,
        pending, unm, parts, x.get("trigger"), x.get("cost"),
        x.get("cost_modifiers", ()), x.get("cost_condition"),
        x.get("activation_index"), x.get("loyalty_cost"),
        x.get("loyalty_slot", ""), x.get("chapters", ()),
        tuple(_freeze_mode(ctx, m, i) for i, m in enumerate(modal.modes))
        if modal else (),
        modal.extra.get("choose", (0, 0)) if modal else (0, 0),
        -1, "", label, x.get("keywords", ()),
        x.get("from_zone", "battlefield"), frozenset(flags),
        x.get("restrictions", ()))


def _freeze(ctx: _Ctx, group: List[_B], index: int) -> L1Host:
    """One host from one builder, or from the SPELL builders F1 merges."""
    if len(group) == 1:
        b = group[0]
        body = tuple(b.body)
        parts = (Part(b.paragraph, (0, len(b.text)), body, b.label,
                      b.delay),) if b.text else ()
        return _host(ctx, b, index, tuple(b.paragraphs or (b.paragraph,)),
                     b.text, body, tuple(b.consumed), tuple(b.pending),
                     tuple(b.unmodelled), parts, b if b.modes else None,
                     b.flags, b.label)
    text_parts, parts = [], []
    body, consumed, pending, unm = [], [], [], []
    paragraphs = set()
    flags = set()
    pos = 0
    for b in group:
        if b.text and text_parts:
            pos += 1                       # the joining newline
        k = pos
        bb = tuple((s[0] + k, s[1] + k) for s in b.body)
        if b.text:
            text_parts.append(b.text)
            parts.append(Part(b.paragraph, (k, k + len(b.text)), bb,
                              b.label, b.delay))
        body.extend(bb)
        consumed.extend((n, (s[0] + k, s[1] + k)) for n, s in b.consumed)
        pending.extend((n, (s[0] + k, s[1] + k)) for n, s in b.pending)
        unm.extend((u, (s[0] + k, s[1] + k)) for u, s in b.unmodelled)
        paragraphs.update(b.paragraphs or (b.paragraph,))
        flags |= b.flags
        pos += len(b.text)
    modal = next((b for b in group if b.modes), None)
    return _host(ctx, group[0], index, tuple(sorted(paragraphs)),
                 "\n".join(text_parts), tuple(body), tuple(consumed),
                 tuple(pending), tuple(unm), tuple(parts), modal, flags, "")


def _merge(ctx: _Ctx, builders: List[_B]) -> Tuple[L1Host, ...]:
    groups: List[List[_B]] = []
    spell: Optional[List[_B]] = None
    for b in builders:
        if ctx.is_spell and b.kind is HostKind.SPELL:
            if spell is None:
                spell = [b]
                groups.append(spell)
                continue
            if b.modes and any(x.modes for x in spell):
                groups.append([b])          # a second modal block stays apart
                continue
            spell.append(b)
            continue
        groups.append([b])
    return tuple(_freeze(ctx, g, i) for i, g in enumerate(groups))


# The face memo's bound. A pool pass never repeats a face (0 hits over
# 23,204 faces), so the memo only absorbs repeat calls for the faces in
# play -- two 75-card lists and their sideboards touch a few hundred --
# and the per-template memo on CardTemplate.effects holds the rest. At
# the pool's size (CACHE_SIZE) it kept every FaceStructure with its L0
# output, 73 MB (design section 12), and the cyclic collector re-walked
# them, about 1 s of the pass.
FACE_CACHE_SIZE = 512


@lru_cache(maxsize=FACE_CACHE_SIZE)
def parse_face_structure(text: str, facts: normalize.Facts = normalize.Facts(),
                         face: int = 0) -> FaceStructure:
    """L1 for one face's printed text (see the module docstring)."""
    norm, printed = normalize.normalize_mapped(text or "", facts)
    paragraphs = norm.paragraphs
    ctx = _Ctx(facts, face, printed, paragraphs)
    builders = _face_builders(ctx, norm, paragraphs)
    return FaceStructure(face=face, normalized=norm,
                         hosts=_merge(ctx, builders))


def uncovered(host: L1Host) -> str:
    """The L1 coverage invariant's residue: the non-space characters of
    ``host.text`` in no body, consumed, pending or refusal span (periods,
    commas and semicolons between spans are structure). '' when covered."""
    covered = bytearray(len(host.text))
    spans = list(host.body)
    spans += [s for _, s in host.consumed]
    spans += [s for _, s in host.pending]
    spans += [s for _, s in host.unmodelled]
    for part in host.parts:
        spans += list(part.body)
    for a, b in spans:
        for i in range(max(a, 0), min(b, len(host.text))):
            covered[i] = 1
    return "".join(ch for i, ch in enumerate(host.text)
                   if not covered[i] and ch not in " \n.,;")


def clear_caches() -> None:
    parse_face_structure.cache_clear()
    _cost.cache_clear()
