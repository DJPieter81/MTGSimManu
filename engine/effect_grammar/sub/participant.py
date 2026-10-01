"""Participant sub-grammar (design doc 2026-09-29, section 5 "Subjects" and
"Untargeted choices"; section 7 reference surface forms; A9 / M1, A17, A23,
A24, A26, A28).

Types who or what a clause acts on or through when it is not a target --
a clause subject or actor, a verb's untargeted object, a damage recipient,
an "unless <player> pays" payer -- once, at LOAD (never at resolution),
over L0 output, under the one leaf contract in `engine.effect_grammar.sub`
(``(host, span, *, lemma="")``, one `SlotResult`, host-absolute spans).
The participant is the scope of a spec; the filter plus its count is the
selection within it, chosen by the chooser (section 4).

**Closed tables.** A slot is consumed whole (no tail is handed on) and is
exactly one of:

* a PLAYER set -- `effect_model.Selector` with ``player=None``, bound by
  the dispatcher relative to the resolving controller: "you" (PLAYER),
  "each opponent" / "your opponents" (OPPONENTS), "each player" / "players"
  (ALL_PLAYERS). "an opponent" / "a player" / "another player" are a
  count of one from the set (``amount``); "each" / "every" set the `EACH`
  flag and "any" the `ANY` flag (every member holds an option). CR 102.3:
  in a free-for-all every other player is an opponent, so "each other
  player" / "another player" are OPPONENTS;
* a player REFERENCE -- ``Ref(DEFENDING_PLAYER)``, ``Ref(ATTACHED,
  noun="player")`` ("enchanted player"), ``Ref(CHOSEN)`` ("the chosen
  player"), and the controller or owner of an object, ``Ref(CONTROLLER_OF
  / OWNER_OF, of=<object ref>)``. A9 / M1: "~'s owner" is a possessive of
  SELF and never goes through pronoun binding; a possessor the linker must
  bind ("its", "that creature's") leaves ``of=None`` and the possessor
  text in ``pending`` as ``("ref", text)``. A plural role ("their
  owners", "the exiled cards' owners") is one player per object of the
  possessor and is flagged `PER_OBJECT`;
* an object REFERENCE -- "~" is ``Ref(SELF)``; "enchanted / equipped /
  fortified <noun>" is ``Ref(ATTACHED, noun)`` (CR 301.5, 303.4); "the
  chosen <noun>" is ``Ref(CHOSEN, noun)``;
* an `Anaphor` -- every other pronoun, demonstrative, participle or
  partitive reference ("it", "those creatures", "the exiled card", "one of
  them", "the rest"). Section 7 binds it by noun and number, so the
  anaphor carries both, plus the participle of the action that produced
  its referent (A26) and its part (A28: REST and OTHER are resolution-time
  set differences). ``pending`` holds ``("ref", text)`` for an object and
  ``("player", text)`` for a player, the quantity leaf's convention, and
  ``("either", text)`` for a word that may name either ("they", "them",
  "him"), which is flagged both `PLAYER` and `OBJECT`. Current Oracle
  wording uses singular "they" / "them" for one player, so their number
  is left open (``plural=None``) for the linker's compatibility check;
* an object GROUP -- the filter leaf's `CardFilter` (the one owner of
  object descriptions), with the filter's count and ``each`` / ``all``
  flags; `CardFilter.as_selector` is its FILTER selector.

**Routing and refusals.** A slot that prints a counted "target" word (the
target leaf's `target_words`, F11) is the target leaf's: refused
``participant.targeted`` so no requirement is ever produced here. A union
("each creature and each player", A17) is split by L3 before a leaf sees
it, so one that reaches this leaf is ``participant.union``. A player set
narrowed by a relative clause, a game designation (the monarch, the
active player), a characteristic ("~'s power") and "this ability" (CR
113.1) are refused with codes of their own. A phrase no row places is
``UNMODELLED(REFERENCE)`` (a filter group the filter leaf refused is
``UNMODELLED(FILTER)``) over the whole trimmed slot, with detail
``participant.<code>[:<param>]`` -- never a broader participant.

**Chooser** (CR 115.1, 701.21a). `parse_chooser` reads the printed chooser
of an untargeted choice ("of their choice" -> PARTICIPANT, "of your
choice" -> CONTROLLER, "of an opponent's choice" -> OPPONENT, "at random"
-> RANDOM) and hands the selection around it on as ``rest_spans``.

The leaf reads no card name and no game state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Tuple

from engine.effect_grammar.sub import (CACHE_SIZE, COUNT_WORDS, NUMBER_WORDS,
                                       SELF_NOUNS, SlotResult, Span,
                                       rest_spans_after, unmodelled)
from engine.effect_grammar.sub import filter as _filter
from engine.effect_grammar.sub import target as _target
from engine.effect_model import Selector, SelectorKind
from engine.effect_spec import (Amount, AmountKind, Chooser, Ref, RefKind,
                                RefPart, Stage, Unmodelled)

__all__ = ["LEAF", "DETAIL_CODES", "Anaphor", "PLAYER", "OBJECT", "GROUP",
           "EACH", "ANY", "PER_OBJECT", "EITHER", "parse_participant",
           "parse_chooser", "clear_caches"]

LEAF = "participant"
DETAIL_CODES = frozenset({
    "empty", "targeted", "union", "relative_clause", "designation",
    "characteristic", "ability", "reference", "player", "possessor",
    "filter", "chooser", "library_position"})

# SlotResult flags: what the participant is.
PLAYER = "player"      # a player set or reference, or a player anaphor
                       # ("that player")
OBJECT = "object"      # an object reference or object anaphor; an anaphor
                       # that may name either ("they", "them") is flagged
                       # PLAYER and OBJECT both
GROUP = "group"        # an untargeted object group (a CardFilter)
# SlotResult flags: the quantifier of a player set (a group carries the
# filter leaf's own each / all flags, whose "each" is this same word).
EACH = _filter.EACH    # "each opponent", "every player"
ANY = "any"            # "any player": every member holds the option
# SlotResult flag: a plural possessed role ("their owners") -- one player
# per object of the possessor, never collapsed into one player.
PER_OBJECT = "per_object"
# Pending key of an anaphor that may bind a player or an object.
EITHER = "either"

_ONE = Amount(AmountKind.LITERAL, n=1)


@dataclass(frozen=True, slots=True)
class Anaphor:
    """A participant the linker binds (section 7). Compatibility checks
    noun and number, so both are recorded; ``player`` is True for a
    player, False for an object and None when the word may be either
    ("they"); ``plural`` is None when the number is open (singular
    "they" / "them" names one player in current Oracle wording). ``participle`` names the earlier action that produced the
    referent ("the exiled card", A26); ``part`` and ``n`` the partitive
    (A28); ``reflexive`` marks "itself" (the clause's own subject)."""
    noun: str = ""
    plural: Optional[bool] = False
    player: Optional[bool] = False
    part: RefPart = RefPart.ALL
    n: Optional[Amount] = None
    participle: str = ""
    reflexive: bool = False


def _um(code: str, lemma: str, param: str = "",
        stage: Stage = Stage.REFERENCE) -> Unmodelled:
    return unmodelled(stage, lemma, LEAF, code, DETAIL_CODES, param)


# ── Players ────────────────────────────────────────────────────────────

# printed player set -> (selector kind, flags, amount)
_PLAYER_SETS = {
    "you": (SelectorKind.PLAYER, frozenset(), None),
    "each opponent": (SelectorKind.OPPONENTS, frozenset({EACH}), None),
    "every opponent": (SelectorKind.OPPONENTS, frozenset({EACH}), None),
    "each of your opponents": (SelectorKind.OPPONENTS, frozenset({EACH}), None),
    "your opponents": (SelectorKind.OPPONENTS, frozenset(), None),
    "all your opponents": (SelectorKind.OPPONENTS, frozenset(), None),
    "all opponents": (SelectorKind.OPPONENTS, frozenset(), None),
    "opponents": (SelectorKind.OPPONENTS, frozenset(), None),
    "an opponent": (SelectorKind.OPPONENTS, frozenset(), _ONE),
    "any opponent": (SelectorKind.OPPONENTS, frozenset({ANY}), None),
    # CR 102.3: in a two-player or free-for-all game every other player is
    # an opponent.
    "each other player": (SelectorKind.OPPONENTS, frozenset({EACH}), None),
    "another player": (SelectorKind.OPPONENTS, frozenset(), _ONE),
    "each player": (SelectorKind.ALL_PLAYERS, frozenset({EACH}), None),
    "every player": (SelectorKind.ALL_PLAYERS, frozenset({EACH}), None),
    "all players": (SelectorKind.ALL_PLAYERS, frozenset(), None),
    "players": (SelectorKind.ALL_PLAYERS, frozenset(), None),
    "a player": (SelectorKind.ALL_PLAYERS, frozenset(), _ONE),
    "any player": (SelectorKind.ALL_PLAYERS, frozenset({ANY}), None),
}
_PLAYER_REFS = {
    "defending player": Ref(RefKind.DEFENDING_PLAYER),
    "the defending player": Ref(RefKind.DEFENDING_PLAYER),
    "enchanted player": Ref(RefKind.ATTACHED, noun="player"),
    "the chosen player": Ref(RefKind.CHOSEN, noun="player"),
    "the chosen opponent": Ref(RefKind.CHOSEN, noun="opponent"),
}
# Player anaphors: text -> (noun, plural, player).
_PLAYER_ANAPHORS = {
    "that player": ("player", False, True),
    "that opponent": ("opponent", False, True),
    "the player": ("player", False, True),
    "the opponent": ("opponent", False, True),
    "those players": ("player", True, True),
    "those opponents": ("opponent", True, True),
    "he or she": ("", False, True),
    "him or her": ("", False, True),
    # May be players or objects. L0 rewrites a self-pronoun on a
    # planeswalker or legendary face to ~ (A9), so a lone he / she left
    # names a player of older wording or another object. "they" / "them"
    # are singular for one player and plural for objects, so their number
    # is open.
    "they": ("", None, None),
    "them": ("", None, None),
    "he": ("", False, None),
    "she": ("", False, None),
    "him": ("", False, None),
    "her": ("", False, None),
}
# Game designations the model has no participant for yet.
_DESIGNATION_RE = re.compile(
    r"^(?:the )?(?:monarch|active player|nonactive player|attacking player"
    r"|player who has the initiative|player with the initiative)$")
_PLAYER_HEAD = (r"(?:each|every|an?|any|all|the|that|those) (?:other )?"
                r"(?:opponents?|players?)|each of your opponents"
                r"|your opponents|opponents|players|you")
# A player set narrowed by a relative clause or a "with" phrase.
_RELATIVE_RE = re.compile(r"^(?:%s) (?:who|whose|that|with|without)\b"
                          % _PLAYER_HEAD)
_PLAYER_WORD_RE = re.compile(r"^(?:%s)(?![\w'])" % _PLAYER_HEAD)
# A bare player noun: a member of a coordinated head ("a permanent or
# player") whose determiner the first member printed.
_PLAYER_NOUNS = frozenset({"player", "players", "opponent", "opponents"})


# ── Objects ────────────────────────────────────────────────────────────

# Object nouns a reference names: the L0 self nouns, the card types and
# the CR 205.3 subtypes (the filter leaf's tables), plus the stack and
# card forms.
_EXTRA_NOUNS = frozenset({"card", "spell", "token", "object", "source",
                          "copy", "ability", "permanent"})
_NOUNS = frozenset(SELF_NOUNS) | _filter.CARD_TYPES | _filter.SUBTYPES \
    | _EXTRA_NOUNS
_FORM_NOUNS = frozenset({"card", "spell", "token", "permanent"})
# Participles of an earlier action of the ability whose object a
# reference names (A26, CR 608.2b). "chosen" is a CHOSEN ref.
_PARTICIPLES = frozenset({
    "exiled", "sacrificed", "discarded", "revealed", "returned", "destroyed",
    "milled", "copied", "countered", "tapped", "untapped", "attacking",
    "blocking", "blocked", "attached", "created", "drawn", "searched",
    "looked"})
_ATTACHED_RE = re.compile(r"^(?:enchanted|equipped|fortified) (?P<noun>.+)$")


def _singular(word: str) -> Optional[Tuple[str, bool]]:
    """(noun, plural) of a table noun or its regular plural, else None."""
    if word in _NOUNS:
        return word, False
    for suffix, repl in (("ies", "y"), ("ves", "f"), ("ves", "fe"),
                         ("es", ""), ("s", ""), ("men", "man")):
        if word.endswith(suffix):
            stem = word[:-len(suffix)] + repl
            if stem in _NOUNS:
                return stem, True
    return None


def _noun(text: str) -> Optional[Tuple[str, bool]]:
    """(noun, plural) of a one- or two-word object noun ("creature",
    "creature cards", "zombie token"), else None."""
    words = text.split(" ")
    if len(words) == 1:
        return _singular(words[0])
    if len(words) == 2:
        head = _singular(words[0])
        form = _singular(words[1])
        if head is not None and not head[1] and form is not None \
                and form[0] in _FORM_NOUNS:
            return "%s %s" % (head[0], form[0]), form[1]
    return None


_NUMBER = r"(?:%s|\d+|x)" % "|".join(COUNT_WORDS)
_PARTITIVE_RE = re.compile(
    r"^(?:(?P<each>each)|(?P<all>all|both)|(?P<anynum>any number)"
    r"|up to (?P<upto>%s)|(?P<count>%s)) of "
    r"(?P<of>them|those (?P<noun>.+)|the (?P<pnoun>.+))$" % (_NUMBER, _NUMBER))
# Any other partitive of an earlier result is a reference form, never an
# object group.
_PARTITIVE_OF_RE = re.compile(r"(?<![\w'])of (?:them|those)(?![\w'])")
_REST_RE = re.compile(r"^the (?:(?P<rest>rest)|(?P<others>others)|(?P<other>other))"
                      r"(?: of (?:them|those (?P<noun>.+)|the (?P<pnoun>.+)))?$")
_THIS_WAY_RE = re.compile(r"^the (?P<noun>.+?) (?P<part>[a-z]+ed) this way$")


def _count(word: str) -> Optional[Amount]:
    if word == "x":
        return Amount(AmountKind.X, n=1)
    if word.isdigit():
        return Amount(AmountKind.LITERAL, n=int(word))
    n = NUMBER_WORDS.get(word)
    return None if n is None else Amount(AmountKind.LITERAL, n=n)


# One parse of the whole slot: (value, flags, amount, pending, failure)
# where failure is (code, param, stage) or None.
_Rel = Tuple[object, frozenset, Optional[Amount], Tuple[Tuple[str, str], ...],
             Optional[Tuple[str, str, Stage]]]


def _ok(value, flags, amount=None, pending=()) -> _Rel:
    return value, frozenset(flags), amount, tuple(pending), None


def _fail(code: str, param: str = "", stage: Stage = Stage.REFERENCE) -> _Rel:
    return None, frozenset(), None, (), (code, param, stage)


def _object_ref(t: str) -> Optional[_Rel]:
    """An object reference or object anaphor of the whole of ``t``, or
    None outside the object tables."""
    if t == "~":
        return _ok(Ref(RefKind.SELF), {OBJECT})
    m = _ATTACHED_RE.match(t)
    if m is not None:
        noun = _noun(m.group("noun"))
        if noun is not None and not noun[1]:
            return _ok(Ref(RefKind.ATTACHED, noun=noun[0]), {OBJECT})
        return None
    pending = (("ref", t),)
    if t == "it":
        return _ok(Anaphor(), {OBJECT}, None, pending)
    if t == "itself":
        return _ok(Anaphor(reflexive=True), {OBJECT}, None, pending)
    m = _PARTITIVE_RE.match(t)
    if m is not None:
        noun, plural = "", True
        np = m.group("noun") or m.group("pnoun")
        if np is not None:
            r = _noun(np)
            if r is None or not r[1]:
                return None
            noun = r[0]
        if m.group("each"):
            return _ok(Anaphor(noun=noun, plural=True, part=RefPart.EACH),
                       {OBJECT}, None, pending)
        if m.group("all"):
            return _ok(Anaphor(noun=noun, plural=True), {OBJECT}, None, pending)
        if m.group("anynum"):
            return _ok(Anaphor(noun=noun, plural=True, part=RefPart.ONE,
                               n=Amount(AmountKind.ANY_NUMBER)),
                       {OBJECT}, None, pending)
        if m.group("upto"):
            bound = _count(m.group("upto"))
            if bound is None:
                return None
            plural = bound != _ONE
            n = Amount(AmountKind.UP_TO, n=bound.n) \
                if bound.kind is AmountKind.LITERAL \
                else Amount(AmountKind.UP_TO, inner=bound)
            return _ok(Anaphor(noun=noun, plural=plural, part=RefPart.ONE,
                               n=n), {OBJECT}, None, pending)
        n = _count(m.group("count"))
        if n is None:
            return None
        plural = n != _ONE
        return _ok(Anaphor(noun=noun, plural=plural, part=RefPart.ONE, n=n),
                   {OBJECT}, None, pending)
    m = _REST_RE.match(t)
    if m is not None:
        noun = ""
        np = m.group("noun") or m.group("pnoun")
        if np is not None:
            r = _noun(np)
            if r is None or not r[1]:
                return None
            noun = r[0]
        if m.group("rest"):
            return _ok(Anaphor(noun=noun, plural=True, part=RefPart.REST),
                       {OBJECT}, None, pending)
        plural = bool(m.group("others"))
        return _ok(Anaphor(noun=noun, plural=plural, part=RefPart.OTHER),
                   {OBJECT}, None, pending)
    m = _THIS_WAY_RE.match(t)
    if m is not None:
        r = _noun(m.group("noun"))
        if r is None or m.group("part") not in _PARTICIPLES:
            return None
        return _ok(Anaphor(noun=r[0], plural=r[1], participle=m.group("part")),
                   {OBJECT}, None, pending)
    det, _, rest = t.partition(" ")
    if det not in ("that", "those", "the", "these", "this") or not rest:
        return None
    first, _, tail = rest.partition(" ")
    if det == "the" and first == "chosen" and tail:
        r = _noun(tail)
        if r is None or r[1]:
            return None
        return _ok(Ref(RefKind.CHOSEN, noun=r[0]), {OBJECT})
    participle = ""
    if first in _PARTICIPLES and tail:
        participle, rest = first, tail
    r = _noun(rest)
    if r is None:
        return None
    noun, plural = r
    if det == "this":
        return None                      # L0 rewrote every self "this <noun>"
    if det in ("those", "these") and not plural:
        return None
    if det == "that" and plural:
        return None
    return _ok(Anaphor(noun=noun, plural=plural, participle=participle),
               {OBJECT}, None, pending)


_POSSESSED_RE = re.compile(
    r"^(?P<poss>.+?)(?:'s|') (?P<role>owner|controller)(?P<pl>s)?$")
_PRONOUN_POSSESSED_RE = re.compile(
    r"^(?P<poss>its|their|his or her|his|her) (?P<role>owner|controller)"
    r"(?P<pl>s)?$")
_ROLE = {"owner": RefKind.OWNER_OF, "controller": RefKind.CONTROLLER_OF}


def _possessed(t: str) -> Optional[_Rel]:
    """The controller or owner of an object (A9 / M1), or None when ``t``
    is not of that shape."""
    m = _PRONOUN_POSSESSED_RE.match(t) or _POSSESSED_RE.match(t)
    if m is None:
        return None
    flags = {PLAYER, PER_OBJECT} if m.group("pl") else {PLAYER}
    poss, kind = m.group("poss"), _ROLE[m.group("role")]
    if m.re is _PRONOUN_POSSESSED_RE:
        return _ok(Ref(kind), flags, None, (("ref", poss),))
    inner = _object_ref(poss)
    if inner is None:
        return _fail("possessor", poss.split(" ")[-1])
    value, _, _, pending, _ = inner
    if isinstance(value, Ref):
        return _ok(Ref(kind, of=value), flags)
    if value.reflexive or value.part is not RefPart.ALL:
        return _fail("possessor", poss.split(" ")[-1])
    return _ok(Ref(kind), flags, None, pending)


# A slot that is itself a characteristic: the whole slot is the stat, its
# possessive pronoun, or a possessor's "'s" form ("~'s power", "that
# creature's mana value"). A possessor that narrows a group ("creatures
# with power less than ~'s power") makes the slot a compared group, the
# filter leaf's.
_CHARACTERISTIC_RE = re.compile(
    r"^(?:(?P<poss>[a-z~][a-z~ ]*?)(?:'s|') |(?:its|their|your|his or her"
    r"|his|her) )?(?:base )?"
    r"(?:power|toughness|mana value|loyalty|life total|converted mana cost)"
    r"(?: and toughness)?$")
# A narrowing word after the possessor's head ("that creature" is a
# demonstrative, "creatures that ..." a relative clause).
_NARROWING_RE = re.compile(
    r"(?<= )(?:with|without|who|whose|that|less|greater|equal|among)"
    r"(?![\w'])")
_ABILITY_RE = re.compile(r"^this ability$")
# The source zone a moved reference prints after it.
_FROM_RE = re.compile(r" (?=from (?:exile|the battlefield|the stack|among"
                      r"|[a-z~' ]+ (?:graveyards?|hands?|librar(?:y|ies))$))")
# "the top <N> card(s) of <player's> library": a position in a hidden zone
# (CR 401.5), which no CardFilter row of the filter leaf states yet.
_LIBRARY_POSITION_RE = re.compile(
    r"^(?:the )?(?:top|bottom) (?:[a-z0-9]+ )?cards? of [a-z~' ]+ librar(?:y|ies)\b")
_UNION_RE = re.compile(r",? (?:and/or|and|or) ")
# Conjunctions inside one participant phrase, never a union.
_FIXED_ORS = ("he or she", "him or her", "his or her")
# First words that make an unplaced phrase a reference or a player rather
# than an object group.
_REFERENCE_HEADS = frozenset({"it", "its", "them", "they", "their", "that",
                              "those", "these", "the", "this", "he", "she",
                              "him", "her", "his", "itself", "enchanted",
                              "equipped", "fortified", "~"})


def _single(t: str) -> Optional[_Rel]:
    """A participant of the closed player and reference tables, or None."""
    if t in _PLAYER_SETS:
        kind, flags, amount = _PLAYER_SETS[t]
        return _ok(Selector(kind, player=None), flags | {PLAYER}, amount)
    if t in _PLAYER_REFS:
        return _ok(_PLAYER_REFS[t], {PLAYER})
    if t in _PLAYER_ANAPHORS:
        noun, plural, player = _PLAYER_ANAPHORS[t]
        if player:
            return _ok(Anaphor(noun=noun, plural=plural, player=True),
                       {PLAYER}, None, (("player", t),))
        return _ok(Anaphor(noun=noun, plural=plural, player=None),
                   {PLAYER, OBJECT}, None, ((EITHER, t),))
    r = _object_ref(t)
    if r is not None:
        return r
    return _possessed(t)


def _group(t: str, zone: str) -> _Rel:
    f = _filter.parse_filter(t, (0, len(t)), zone=zone)
    if f.value is None:
        code = f.unmodelled.detail.split(".", 1)[1].split(":")[0]
        return _fail("filter", code, Stage.FILTER)
    return _ok(f.value, f.flags | {GROUP}, f.amount, f.pending)


def _is_union(t: str) -> bool:
    """A coordination of two members, at least one a player or reference
    of this leaf and the other a participant of this leaf or a filter
    group ("each creature and each player", "~ or another creature you
    control", "a permanent or player"). A union of object groups alone is
    the filter leaf's."""
    for m in _UNION_RE.finditer(t):
        if any(t.startswith(fixed, i) for fixed in _FIXED_ORS
               for i in range(max(0, m.start() - 4), m.start() + 1)):
            continue
        left, right = t[:m.start()], t[m.end():]
        if (_mine(left) and _member(right)) or (_mine(right) and _member(left)):
            return True
    return False


def _mine(t: str) -> bool:
    """A player or reference participant of this leaf's own tables."""
    r = _single(t)
    return (r is not None and r[4] is None) or t in _PLAYER_NOUNS


def _member(t: str) -> bool:
    return _mine(t) or _filter.parse_filter(t, (0, len(t))).value is not None


@lru_cache(maxsize=CACHE_SIZE)
def _participant_rel(host: str, a: int, b: int, zone: str) -> _Rel:
    t = host[a:b]
    if not t:
        return _fail("empty")
    if _target.target_words(host, (a, b)):
        return _fail("targeted")
    if _DESIGNATION_RE.match(t):
        return _fail("designation")
    if _ABILITY_RE.match(t):
        return _fail("ability")
    m = _CHARACTERISTIC_RE.match(t)
    if m is not None and not (m.group("poss")
                              and _NARROWING_RE.search(m.group("poss"))):
        return _fail("characteristic")
    if _LIBRARY_POSITION_RE.match(t):
        return _fail("library_position")
    r = _single(t)
    if r is not None:
        return r
    if _is_union(t):
        return _fail("union")
    if _RELATIVE_RE.match(t):
        return _fail("relative_clause")
    if _PLAYER_WORD_RE.match(t):
        return _fail("player", t.split(" ")[0])
    if t.endswith("'s") or t.endswith("s'"):
        # A dangling possessive ("defending player's"): a possessor whose
        # possessed noun the slot cut off.
        return _fail("possessor", t.split(" ")[-1])
    if _PARTITIVE_OF_RE.search(t):
        return _fail("reference", t.split(" ")[0])
    head = t.split(" ")[0]
    if head in _REFERENCE_HEADS or head.endswith("'s"):
        return _fail("reference", head)
    return _group(t, zone)


def parse_participant(host: str, span: Optional[Span] = None, *,
                      lemma: str = "", zone: str = "") -> SlotResult:
    """The participant of the slot ``host[span]`` (default: the whole host):
    a clause subject or actor, an untargeted object, a damage recipient or
    a payer.

    ``lemma`` is the caller's printed lemma; ``zone`` the zone the
    caller's verb reads when an object group prints none (a discard passes
    "hand"), handed to the filter leaf unchanged. On success ``value`` is a
    `Selector` (player set), a `Ref`, an `Anaphor` or a `CardFilter`
    (group); ``flags`` says which (`PLAYER`, `OBJECT`, `GROUP`) and holds
    the set's quantifier; ``amount`` the count of a single choice;
    ``pending`` the possessors and anaphors the linker binds. ``span`` is
    the whole slot without trailing punctuation, which is structure and in
    neither ``span`` nor ``rest_spans``. The one rest is the source zone a
    reference prints after it ("~ from your graveyard": ``rest_spans`` is
    "from your graveyard", the destination leaf's object-span zone); a
    group reads its own zone through the filter leaf. Otherwise the slot
    is UNMODELLED over the whole trimmed slot."""
    a, b = (0, len(host)) if span is None else span
    slot = host[a:b]
    lead = len(slot) - len(slot.lstrip())
    trimmed = slot.strip()
    body = trimmed.rstrip(" .,;")
    start = a + lead
    value, flags, amount, pending, failure = _participant_rel(
        host, start, start + len(body), zone)
    rest: Tuple[Span, ...] = ()
    if failure is not None and failure[0] != "targeted":
        # A reference moved out of a zone prints its source zone ("return
        # ~ from your graveyard"); the zone is the object span's, read by
        # the destination leaf's zone reader, so it is handed on as rest.
        # A slot that prints a counted target word anywhere stays the
        # target leaf's, whole.
        m = _FROM_RE.search(body)
        if m is not None:
            cut = start + m.start()
            v, f, am, pe, fail = _participant_rel(host, start, cut, zone)
            if fail is None and GROUP not in f:
                value, flags, amount, pending, failure = v, f, am, pe, None
                rest = rest_spans_after(host, cut, start + len(body))
                body = body[:m.start()]
    if failure is not None:
        code, param, stage = failure
        return SlotResult(unmodelled=_um(code, lemma, param, stage),
                          span=(start, start + len(trimmed)))
    return SlotResult(value=value, span=(start, start + len(body)),
                      rest_spans=rest, flags=flags, pending=pending,
                      amount=amount)


# ── Chooser (CR 115.1, 701.21a) ────────────────────────────────────────

_CHOOSER_RE = re.compile(
    r"(?:\bof (?P<poss>(?:(?!of )[a-z~']+ ){1,3}?)choice"
    r"|\b(?:chosen )?at random)"
    r"(?![\w'])")
_CHOOSERS = {"their": Chooser.PARTICIPANT, "his or her": Chooser.PARTICIPANT,
             "your": Chooser.CONTROLLER, "an opponent's": Chooser.OPPONENT}


@lru_cache(maxsize=CACHE_SIZE)
def _chooser_rel(clause: str):
    m = _CHOOSER_RE.search(clause)
    if m is None:
        return None
    poss = m.group("poss")
    if poss is not None:
        poss = poss.rstrip(" ")
    if poss is None:
        return m.start(), m.end(), Chooser.RANDOM, ""
    chooser = _CHOOSERS.get(poss)
    return m.start(), m.end(), chooser, poss.split(" ")[-1]


def parse_chooser(host: str, span: Optional[Span] = None, *,
                  lemma: str = "") -> Optional[SlotResult]:
    """The printed chooser of the untargeted choice ``host[span]``
    (default: the whole host), or None when it prints none (the verb's
    default chooser applies). ``span`` is the chooser phrase and
    ``rest_spans`` the selection around it (trailing punctuation is
    structure). A possessor outside the table ("of that player's choice")
    is UNMODELLED(REFERENCE) ``participant.chooser`` over the phrase, never
    a default chooser."""
    a, b = (0, len(host)) if span is None else span
    rel = _chooser_rel(host[a:b])
    if rel is None:
        return None
    s, e, chooser, param = rel
    s, e = s + a, e + a
    rest = rest_spans_after(host, a, s, " ,") + \
        rest_spans_after(host, e, b, " ,.;")
    if chooser is None:
        return SlotResult(unmodelled=_um("chooser", lemma, param),
                          span=(s, e), rest_spans=rest)
    return SlotResult(value=chooser, span=(s, e), rest_spans=rest)


def clear_caches() -> None:
    _participant_rel.cache_clear()
    _chooser_rel.cache_clear()
