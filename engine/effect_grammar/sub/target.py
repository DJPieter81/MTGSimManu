"""Target sub-grammar (design doc 2026-09-29, section 5; F3, F4, F11, A20,
A21, M4).

A closed table over L0 output (the leaf contract in
`engine.effect_grammar.sub`). It runs at load, never at resolution, and
reads no game.

**One owner.** `target_solver` is the single owner of TargetRequirement
production. ``parse_target`` runs `target_solver.parse_spans` on the
slot's clause (F3: the whole clause, never the slot alone and never the
whole host) and keeps the requirements whose printed phrase lies inside the
slot, unmodified, in printed order (CR 601.2c). The spine stamps
``mode_group`` inside a modal block; the leaf never edits a requirement.

**Count (F11).** Each printed target word must produce exactly one
requirement. Noun and verb uses of "target" do not count ("the target of",
"a single target", "new targets", "spells that target ~", "each target
beyond the first"); "any target", "another target" and an ordinal ("a
third target") count, and so does the imperative "copy target <X>"; a
plural counts only after a printed count ("up to two other targets"). A
quoted ability is already masked ``⟨qk⟩`` by L0 and is counted in its
granted host. `target_words` returns the counted words. A
slot whose words have no requirement is ``UNMODELLED(TARGET)``; a different
number of requirements is ``UNMODELLED(TARGET_COUNT)``; a printed count the
requirement does not carry ("x target creatures") is TARGET_COUNT too.

**Consumption and residue (A21).** Every token of the slot is consumed by a
requirement's printed phrase or by a recognised feature of this table. A
feature the requirement carries (its controller scope, graveyard owner,
mana-value ceiling, count) is consumed silently; a feature it does not
carry becomes a residue code of `effect_spec.RESIDUE_CODES` with its
polarity (WIDENING: the requirement admits objects the text excludes;
NARROWING: a union member the requirement dropped). A token no feature
reads, or a qualifier with no residue code of its own, is the UNPARSED
``target.unparsed``: never tolerable (F4). No row is a wildcard: a
comparison operand, an extreme's comparison set and a history verb's
complement are closed grammars, so an unknown word is left over and is
unparsed, never absorbed into a WIDENING code. A listed restriction ("with
flying or reach", "power or toughness") is one code per member; negation
("without flying") and the passive ("was dealt damage") have codes of
their own. A requirement read inside its own
phrase is checked too: "target nonbasic land" typed as any land is
``target.unparsed``.

**Zones.** The zones the slot names are read through the destination
leaf's object reader (`dest.source_zones`, the one zone reader of an object
span). A requirement in one zone of a printed union ("target spell or
permanent") is ``UNMODELLED(TARGET)`` ``target.zone_union``; a requirement
in a zone the slot does not name ("target nonland permanent card from your
graveyard" read as a battlefield permanent) is ``target.zone_mismatch``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Tuple

from engine.cards import Keyword
from engine.effect_grammar.sub import (CACHE_SIZE, COUNT_WORDS, NUMBER_WORDS,
                                       SlotResult, Span, rest_spans_after,
                                       unmodelled)
from engine.effect_grammar.sub import dest as _dest
from engine.effect_spec import Amount, AmountKind, Stage
from engine.target_solver import ANY_NUMBER, TargetRequirement, parse_spans

__all__ = ["LEAF", "DETAIL_CODES", "POSSESSIVE", "TargetSlot",
           "target_words", "parse_target", "clear_caches"]

LEAF = "target"
DETAIL_CODES = frozenset({
    "no_requirement", "count_mismatch", "count_unread", "crosses_slot",
    "zone_union", "zone_mismatch"})
# SlotResult flag: the slot is "<target NP>'s <noun>"; the possessed noun is
# the rest ("target player's graveyard").
POSSESSIVE = "possessive"

# Residue codes this leaf emits (each is a key of effect_spec.RESIDUE_CODES;
# pinned by the tests).
_SCOPE_OPPONENT = "target.scope:opponent"
_SCOPE_NOT_YOU = "target.scope:not_you"
_EXCLUDE_SOURCE = "target.exclude_source"
_COLORED = "target.colored"
_NONTOKEN = "target.nontoken"
_HISTORIC = "target.historic"
_SINGLE_GRAVEYARD = "target.single_graveyard"
_DEPENDENT_CONTROLLER = "target.dependent_controller"
_TOTAL_MV = "target.total_mv"
_CONJUNCTIVE = "target.conjunctive_types"
_UNPARSED = "target.unparsed"


def _um(stage: Stage, lemma: str, code: str, param: str = ""):
    return unmodelled(stage, lemma, LEAF, code, DETAIL_CODES, param)


@dataclass(frozen=True, slots=True)
class TargetSlot:
    """The typed target slot: the solver's requirements in printed order,
    each one's printed phrase in host coordinates, and the slot's residue
    codes (sorted, unique; `effect_spec.RESIDUE_CODES`)."""
    requirements: Tuple[TargetRequirement, ...]
    spans: Tuple[Span, ...]
    residue: Tuple[str, ...] = ()


# ── F11: counted target words ──────────────────────────────────────────

_COUNT_WORD = r"(?:%s|\d+|x)" % "|".join(COUNT_WORDS)
_TARGET_WORD = re.compile(r"(?<![\w'])(?P<w>target(?P<pl>s)?)(?![\w'])")
# Determiners and adjectives that make "target" a noun ("the target of",
# "a single target", "choose new targets", "each target beyond the first").
_NOUN_BEFORE = re.compile(
    r"(?<![\w'])(?:the|a|an|its|their|his|her|~'s|new|single|same|"
    r"different|legal|each)\s+$")
# Subjects and auxiliaries that make "target(s)" a verb ("spells that
# target ~", "if it targets a creature", "the spell could target").
_VERB_BEFORE = re.compile(
    r"(?<![\w'])(?:that|which|it|they|would|could|can|can't|cannot|"
    r"doesn't|don't|also|spells?|abilit(?:y|ies))\s+$")
# "any target", and the further targets of one ability: "another target",
# "a second target", "a third target" (a bare noun that still targets).
_ANY_BEFORE = re.compile(r"(?<![\w'])(?:any (?:other )?|another |"
                         r"an? (?:second|third|fourth|fifth) )$")
# "target" followed by what a verb takes ("that target a creature").
_OBJECT_AFTER = re.compile(r"\s+(?:of|a|an|~|you|only|it|them|that|your|"
                           r"exactly)(?![\w'])")
# A plural "targets" is counted only after a printed count ("one or two
# targets", "any number of targets", "each of up to two targets").
_COUNT_BEFORE_PLURAL = re.compile(
    r"(?<![\w'])(?:one, two,? (?:and|or) three|one or (?:two|three)|"
    r"any number of|up to %s|%s)\s+(?:other\s+)?$" % (_COUNT_WORD, _COUNT_WORD))
_WINDOW = 24         # characters looked back for a determiner or count


def _counted(text: str, m) -> bool:
    before = text[max(0, m.start() - _WINDOW):m.start()]
    after = text[m.end():]
    if m.group("pl"):
        return (bool(_COUNT_BEFORE_PLURAL.search(before))
                and not _VERB_BEFORE.search(before))
    if _ANY_BEFORE.search(before):
        return True
    if _NOUN_BEFORE.search(before) or _VERB_BEFORE.search(before):
        return False
    if _OBJECT_AFTER.match(after):
        return False
    # A word, a self-form, a masked quote or a bracketed perpetual word
    # ("target [attacking] creature") follows a counted target word.
    return bool(re.match(r"\s+[\w~⟨\[]", after))


@lru_cache(maxsize=CACHE_SIZE)
def _words_rel(text: str) -> Tuple[Span, ...]:
    return tuple(m.span("w") for m in _TARGET_WORD.finditer(text)
                 if _counted(text, m))


def target_words(host: str, span: Optional[Span] = None) -> Tuple[Span, ...]:
    """The counted target words of ``host[span]`` (default: the whole host),
    as host spans (F11). Context is read from the whole host, so a word at
    the slot's edge keeps its determiner."""
    a, b = (0, len(host)) if span is None else span
    return tuple(s for s in _words_rel(host) if a <= s[0] and s[1] <= b)


# ── The feature table (A21) ────────────────────────────────────────────

_TYPE = (r"(?:creature|artifact|enchantment|planeswalker|land|battle|"
         r"permanent|spell|card|player|opponent|activated ability|"
         r"triggered ability|ability|vehicle|equipment|aura|token)")
_KEYWORDS = sorted({k.value.replace("_", " ") for k in Keyword},
                   key=lambda s: (-len(s), s))
_KW = "(?:%s)" % "|".join(re.escape(k) for k in _KEYWORDS)
_STAT_WORD = re.compile(r"power|toughness|mana value")
_STAT = (r"(?:base )?(?P<stat>(?:power|toughness)"
         r"(?: (?:or|and) (?:power|toughness))?)")
# Comparison operands: a closed grammar, never a wildcard (A21). A quantity
# is "the number of" a counted noun with a closed complement; anything else
# is left unread, so it is target.unparsed.
_QUANTITY = (r"the number of (?:[\w+/-]+ ){0,2}?[\w-]+s(?: (?:you control|"
             r"an opponent controls|on the battlefield|on ~|removed this way|"
             r"of mana spent to cast ~|in (?:your|its controller's|"
             r"that player's|their|all) (?:graveyards?|hands?)))?(?![\w'])")
_OPERAND = (r"(?:(?:\d+|x)(?: plus \d+)?(?![\w'])|(?:~'s |its |that \w+'s |"
            r"\w+'s )(?:power|toughness|mana value|loyalty)(?![\w'])|"
            r"your life total|that number|%s)" % _QUANTITY)
_COMPARE = (r"(?:(?:\d+|x) or (?:less|greater|more|fewer)(?![\w'])|"
            r"(?:less|greater) than(?: or equal to)? %s|equal to %s)"
            % (_OPERAND, _OPERAND))
# "with the greatest power among creatures you control": the extreme's
# comparison set is closed.
_AMONG = (r"(?: or tied for the (?:greatest|least|highest|lowest) "
          r"(?:power|toughness|mana value))?"
          r"(?: among (?:other )?(?:creatures|permanents|artifacts|lands|"
          r"creatures and planeswalkers|nonland permanents)"
          r"(?: (?:you control|they control|an opponent controls|"
          r"that player controls|your opponents control|"
          r"on the battlefield))?)?(?![\w'])")
# "that attacked or blocked this turn": each verb with its closed
# complement; the time is optional (an unread remainder is unparsed).
_HIST_VERB = (r"(?:attacked|blocked|(?:was|were) blocked|"
              r"(?:was|were) dealt damage|dealt (?:combat )?damage|entered|"
              r"came under your control)"
              r"(?: (?:to you|by (?:it|~|an? [\w-]+ creature)|"
              r"the battlefield(?: under your control)?))?")
_HIST_CODE = ((re.compile(r"^(?:was|were) blocked"), "was_blocked"),
              (re.compile(r"^(?:was|were) dealt"), "was_dealt"),
              (re.compile(r"^dealt"), "dealt"),
              (re.compile(r"^came under"), "came_under"),
              (re.compile(r"^(\w+)"), None))
_STATES = ("attacking or blocking", "attacking", "blocking", "tapped",
           "untapped", "blocked", "unblocked", "enchanted", "equipped",
           "modified", "suspected", "saddled", "face-down", "face down")
_ZONE_NOUN = r"(?:graveyards?|hands?|librar(?:y|ies)|exile)"
_POSS = r"(?:[\w'~]+ ){0,3}?"
# Zone possessives: an opponent's zone, another player's zone (a
# dependency on a player named elsewhere), and possessives naming no owner.
_OPP_POSS = frozenset({"an opponent's", "opponents'", "an opponent's single"})
_DEPENDENT_POSS = frozenset({"that player's", "defending player's",
                             "that opponent's", "the chosen player's",
                             "its controller's", "their"})
_ANY_POSS = frozenset({"", "a", "all", "any", "any one", "the"})

# Tail features: read after a requirement's phrase. Each row is
# (row name, pattern); `_tail_code` maps a hit to a residue code or None
# (carried by the requirement).
_TAIL = tuple((n, re.compile(p)) for n, p in (
    # "target player's graveyard", "target players' graveyards".
    ("possessive", r"(?:'s|(?<=s)')(?![\w'])"),
    ("scope_opponent", r"(?:an opponent|your opponents|opponents) controls?"),
    ("scope_you", r"you control"),
    ("scope_not_you", r"you don't control"),
    ("dependent", r"(?:that player|target player|target opponent|"
                  r"defending player|the defending player|its controller|"
                  r"that opponent|the chosen player|chosen player|"
                  r"enchanted player|that creature's controller|"
                  r"each opponent|each player|they|it) controls?"),
    ("controls", r"controls?(?![\w'])"),
    ("owns", r"owns?(?![\w'])"),
    ("owner", r"(?:you|an opponent|that player|target player) "
              r"(?:own|owns|don't own|doesn't own)(?![\w'])"),
    ("zone", r"(?:cards? )?(?:from|in) (?P<poss>%s)%s"
             r"(?:(?:,| or| and| and/or) %s%s)*" % (
                 _POSS, _ZONE_NOUN, _POSS, _ZONE_NOUN)),
    ("card", r"cards?"),
    ("mv", r"with (?P<total>total )?mana value (?:(?P<n>\d+|x) or less|%s|"
           r"\d+|x)(?![\w'])" % _COMPARE),
    ("stat", r"with %s %s" % (_STAT, _COMPARE)),
    ("stat_extreme", r"with the (?P<ext>greatest|least|highest|lowest) "
                     r"(?P<stat>power|toughness|mana value)" + _AMONG),
    ("keyword", r"with(?P<neg>out)? (?P<kw>%s(?:(?:,| or| and|, or|, and) %s)*)"
                r"(?![\w'])" % (_KW, _KW)),
    ("counter", r"with (?:a|an|one or more|%s) (?:[\w+/-]+ )?counters? on it"
                % _COUNT_WORD),
    ("colored", r"that's (?:one or more colors|multicolored|monocolored|"
                r"colored|a multicolored permanent)"),
    ("thats", r"that's (?:an? )?(?P<w>\w+)"),
    ("history", r"that (?P<w>%s(?: or %s)*)(?: this turn| since your last "
                r"turn ended)?(?![\w'])" % (_HIST_VERB, _HIST_VERB)),
    ("other_than", r"other than (?:~|it|that \w+|the \w+|target \w+|"
                   r"enchanted \w+|equipped \w+)"),
    ("state_rel", r"(?P<w>attacking|blocking|blocked by|blocking or blocked by)"
                  r" (?:~|you|it|that creature|enchanted creature|"
                  r"equipped creature)"),
    ("union", r"(?:(?:, ?)?(?:or|and/or)|,) (?:an? )?(?P<adj>(?:[\w-]+ ){0,2}?)"
              r"(?P<noun>%s)s?(?![\w'])" % _TYPE),
    ("conj_type", r"(?P<noun>creature|artifact|enchantment|land|planeswalker|"
                  r"battle)s?(?![\w'])"),
    ("token", r"tokens?(?![\w'])"),
))
_THATS = {"nontoken": _NONTOKEN, "historic": _HISTORIC, "token":
          "target.state:token", "colorless": "target.color:colorless"}
_TYPE_WORDS = frozenset({"creature", "artifact", "enchantment", "land",
                         "planeswalker", "battle", "permanent"})

# Prefix features: read before a requirement's phrase.
_PREFIX = tuple((n, re.compile(p)) for n, p in (
    ("count", r"(?:each of )?(?P<c>up to (?P<up>%s)|one or (?P<oor>two|three)|"
              r"one, two,? (?:and|or) three|(?P<any>any number of)|"
              r"(?P<n>%s))(?![\w'])" % (_COUNT_WORD, _COUNT_WORD)),
    ("other", r"(?:another|other)(?![\w'])"),
    ("connective", r"(?:, ?)?(?:and/or|and|or|then)(?![\w'])|,"),
))
_SEP = re.compile(r" *")


_KW_RE = re.compile(r"(?<![\w'])%s(?![\w'])" % _KW)
_EXTREME = {"highest": "greatest", "lowest": "least"}


def _tail_codes(row: str, m, req: TargetRequirement) -> Tuple[str, ...]:
    """The residue codes of a tail feature: one per printed restriction the
    requirement does not carry (a listed keyword or stat is one code per
    member); () when the requirement carries it."""
    code = _tail_code(row, m, req)
    if isinstance(code, tuple):
        return code
    return (code,) if code else ()


def _tail_code(row: str, m, req: TargetRequirement):
    """The residue code (or codes) of a tail feature, or None when the
    requirement carries it."""
    if row == "scope_opponent":
        return None if req.owner_scope == "opponent" else _SCOPE_OPPONENT
    if row == "scope_you":
        # effect_spec.RESIDUE_CODES has no "you control" code, so a dropped
        # one is unparsed (never tolerable) rather than a guessed polarity.
        return None if req.owner_scope == "you" else _UNPARSED
    if row == "scope_not_you":
        return _SCOPE_NOT_YOU
    if row in ("dependent", "controls"):
        return _DEPENDENT_CONTROLLER
    if row in ("owner", "owns"):
        return _UNPARSED
    if row == "zone":
        poss = (m.group("poss") or "").strip()
        if "single" in poss.split():
            return _SINGLE_GRAVEYARD
        if poss == "your":
            return None if req.owner_scope == "you" else _UNPARSED
        if poss in _OPP_POSS:
            return None if req.owner_scope == "opponent" else _SCOPE_OPPONENT
        if poss in _DEPENDENT_POSS:
            return _DEPENDENT_CONTROLLER
        # The zone itself is checked on the NP; an owner no row reads is
        # unparsed.
        return None if poss in _ANY_POSS else _UNPARSED
    if row == "card":
        return None               # the zone is checked on the NP
    if row == "mv":
        if m.group("total"):
            return _TOTAL_MV
        n = m.group("n")
        if n == "x" and req.max_mana_value_is_x:
            return None
        if n is not None and n != "x" and req.max_mana_value == int(n):
            return None
        return "target.stat:mana_value"
    if row == "stat":
        return tuple("target.stat:" + w for w in
                      dict.fromkeys(_STAT_WORD.findall(m.group("stat"))))
    if row == "stat_extreme":
        ext = _EXTREME.get(m.group("ext"), m.group("ext"))
        return "target.stat:%s_%s" % (ext, m.group("stat").replace(" ", "_"))
    if row == "keyword":
        neg = "without_" if m.group("neg") else ""
        return tuple(dict.fromkeys(
            "target.keyword:" + neg + k.replace(" ", "_")
            for k in _KW_RE.findall(m.group("kw"))))
    if row == "counter":
        return "target.state:counter"
    if row == "colored":
        return _COLORED
    if row == "thats":
        w = m.group("w")
        if w in _THATS:
            return _THATS[w]
        if w in _TYPE_WORDS:
            return _CONJUNCTIVE
        if w in _STATES:
            return "target.state:" + w
        return _UNPARSED
    if row == "history":
        codes = []
        for v in re.finditer(_HIST_VERB, m.group("w")):
            for rx, name in _HIST_CODE:
                h = rx.match(v.group(0))
                if h:
                    codes.append("target.state:" + (name or h.group(1)))
                    break
        return tuple(dict.fromkeys(codes))
    if row == "other_than":
        return _EXCLUDE_SOURCE
    if row == "state_rel":
        w = m.group("w")
        return tuple(dict.fromkeys(
            "target.state:" + ("blocked_by" if x.startswith("blocked") else x)
            for x in re.findall(r"attacking|blocking|blocked by", w)))
    if row == "union":
        noun, adj = m.group("noun").split()[0], m.group("adj").split()
        if noun in ("card", "spell") and adj:
            noun = adj[-1]            # "or sorcery card" -> sorcery
        return "target.union:" + noun
    if row == "conj_type":
        return _CONJUNCTIVE
    if row == "token":
        return "target.state:token"
    return _UNPARSED


# Words the solver's own phrase may read but not carry: "target nonbasic
# land" is typed as any land.
_PHRASE_DROPS = re.compile(r"\btarget non(?!land\b)[\w-]+ land\b")


def _printed_count(m) -> Optional[Tuple[int, int, Optional[Amount], str]]:
    """(min, max, amount, word) of a count prefix; max None = unbounded."""
    if m.group("up"):
        w = m.group("up")
        n = NUMBER_WORDS.get(w) or (int(w) if w.isdigit() else None)
        return (0, n, None if n is None else Amount(AmountKind.UP_TO, n=n), w)
    if m.group("oor"):
        return (1, NUMBER_WORDS[m.group("oor")], None, "range")
    if m.group("any"):
        return (0, ANY_NUMBER, Amount(AmountKind.ANY_NUMBER), "any")
    w = m.group("n")
    if w is None:                                   # one, two, or three
        return (1, NUMBER_WORDS["three"], None, "range")
    n = NUMBER_WORDS.get(w) or (int(w) if w.isdigit() else None)
    if n is None:
        return (0, None, None, w)                   # x: not a literal
    return (n, n, None if n == 1 else Amount(AmountKind.LITERAL, n=n), w)


# ── The slot ───────────────────────────────────────────────────────────

@lru_cache(maxsize=CACHE_SIZE)
def _solver_spans(clause: str) -> Tuple[Tuple[TargetRequirement, int, int], ...]:
    return tuple(parse_spans(clause))


def _sentence(host: str, a: int, b: int) -> Span:
    """The sentence of ``host`` holding ``host[a:b]``: bounded by a '.' or
    a newline (quotes are masked by L0, so every '.' is at depth 0)."""
    s = max(host.rfind(".", 0, a), host.rfind("\n", 0, a)) + 1
    ends = [i for i in (host.find(".", b), host.find("\n", b)) if i >= 0]
    return s, (min(ends) if ends else len(host))


def _skip(text: str, p: int, end: int) -> int:
    m = _SEP.match(text, p, end)
    return m.end() if m else p


_REFUSED = re.compile(r"[\w/+-]+")


@lru_cache(maxsize=CACHE_SIZE)
def _slot_rel(clause: str, a: int, b: int, lemma: str):
    """The slot ``clause[a:b]`` (already trimmed): (value, unmodelled,
    amount, rest, flags, consumed_end), or None when it holds no counted
    target word. Spans are relative to ``clause``."""
    words = _words_rel(clause)
    in_words = [w for w in words if a <= w[0] and w[1] <= b]
    if not in_words:
        return None
    placed = []
    for req, s, e in _solver_spans(clause):
        if s < 0 or not (a <= s < b):
            continue
        # A plural noun's "s" is outside the solver's singular phrase.
        if e < len(clause) and clause[e] == "s" and (
                e + 1 == len(clause) or not clause[e + 1].isalnum()):
            e += 1
        if e > b:
            return (None, _um(Stage.TARGET, lemma, "crosses_slot"), None, (), (), b)
        placed.append((s, e, req))
    placed.sort(key=lambda x: (x[0], x[1]))
    if not placed:
        # The refused token is one word, punctuation stripped (the leaf
        # contract's param).
        tok = _REFUSED.search(clause, in_words[0][1], b)
        return (None, _um(Stage.TARGET, lemma, "no_requirement",
                          tok.group(0) if tok else ""), None, (), (), b)
    if len(placed) != len(in_words):
        return (None, _um(Stage.TARGET_COUNT, lemma, "count_mismatch"),
                None, (), (), b)

    residue = set()
    amounts: List[Optional[Amount]] = []
    rest: Tuple[Span, ...] = ()
    flags = set()
    p = a
    for k, (s, e, req) in enumerate(placed):
        # Tail of the previous requirement, then this one's prefix.
        prev = placed[k - 1][2] if k else None
        count = None
        in_prefix = prev is None
        while True:
            p = _skip(clause, p, s)
            if p >= s:
                break
            hit = None
            if not in_prefix and prev is not None:
                for row, rx in _TAIL:
                    m = rx.match(clause, p, s)
                    if m and row not in ("possessive", "union", "conj_type"):
                        hit = m
                        residue.update(_tail_codes(row, m, prev))
                        break
            if hit is None:
                for row, rx in _PREFIX:
                    m = rx.match(clause, p, s)
                    if m:
                        hit = m
                        in_prefix = True
                        if row == "count":
                            count = _printed_count(m)
                        elif row == "other":
                            residue.add(_EXCLUDE_SOURCE)
                        break
            if hit is None or hit.end() == p:
                residue.add(_UNPARSED)
                p = s
                break
            p = hit.end()
        # The requirement's own phrase.
        if _PHRASE_DROPS.search(clause, s, e) and req.supertype is None:
            residue.add(_UNPARSED)
        amount = None
        if count is not None:
            lo, hi, amount, word = count
            if hi is None or (req.count_min, req.count_max) != (lo, hi):
                return (None, _um(Stage.TARGET_COUNT, lemma, "count_unread",
                                  word), None, (), (), b)
        amounts.append(amount)
        p = e
    # The tail of the last requirement runs to the end of the slot.
    last = placed[-1][2]
    # A player target juxtaposed after an object target names that object's
    # controller ("target creature target player controls"); a player
    # target on its own is the subject of a relative clause ("each creature
    # target player controls"), whose verb is the caller's rest.
    juxtaposed = len(placed) > 1 and not clause[placed[-2][1]:placed[-1][0]].strip()
    np_end = consumed_end = b
    while True:
        p = _skip(clause, p, b)
        if p >= b:
            break
        hit = stop = None
        for row, rx in _TAIL:
            m = rx.match(clause, p, b)
            if not m:
                continue
            hit = m
            if row == "possessive":
                flags.add(POSSESSIVE)
                np_end, consumed_end, stop = p, m.end(), m.end()
            elif (row in ("controls", "owns") and _is_player(last)
                  and not juxtaposed):
                # "each permanent target player controls/owns": the verb is
                # the relative clause's, handed on as rest.
                np_end = consumed_end = stop = p
            else:
                residue.update(_tail_codes(row, m, last))
            break
        if stop is not None:
            rest = rest_spans_after(clause, stop, b)
            while consumed_end > a and clause[consumed_end - 1] == " ":
                consumed_end -= 1
            np_end = consumed_end if np_end > consumed_end else np_end
            break
        if hit is None or hit.end() == p:
            residue.add(_UNPARSED)
            break
        p = hit.end()

    # Zones: every requirement against the zones its NP names.
    bounds = [x[0] for x in placed[1:]] + [np_end]
    for (s, _e, req), end in zip(placed, bounds):
        bad = _zone_check(req, clause[s:end])
        if bad is not None:
            return (None, _um(Stage.TARGET, lemma, bad), None, (), (), b)

    value = TargetSlot(tuple(r for _, _, r in placed),
                       tuple((s, e) for s, e, _ in placed),
                       tuple(sorted(residue)))
    amount = amounts[0] if len(amounts) == 1 else None
    return (value, None, amount, rest, tuple(sorted(flags)), consumed_end)


def _is_player(req: TargetRequirement) -> bool:
    return req.types == frozenset({"player"})


_CARD = re.compile(r"(?<![\w'])cards?(?![\w'])")


def _zone_check(req: TargetRequirement, np: str) -> Optional[str]:
    """None when the requirement's zone is the one the NP names; else the
    detail code (``zone_union`` / ``zone_mismatch``)."""
    zones = set(_dest.source_zones(np))
    if req.zone == "any":
        # Players and "any target" (creature, planeswalker, battle or
        # player, CR 115.4): a battlefield noun is inside the requirement.
        zones.discard("battlefield")
        return None if not zones else "zone_mismatch"
    if not zones:
        if req.zone == "battlefield" and _CARD.search(np):
            return "zone_mismatch"
        return None
    if zones == {req.zone}:
        return None
    return "zone_union" if len(zones) > 1 else "zone_mismatch"


def parse_target(host: str, span: Optional[Span] = None, *,
                 lemma: str = "", clause: Optional[Span] = None
                 ) -> Optional[SlotResult]:
    """Type the target slot ``host[span]`` (default: the whole host), or
    None when it holds no counted target word.

    ``clause`` is the clause the solver reads (F3); by default the sentence
    of ``host`` holding the slot. The value is a `TargetSlot`; ``amount``
    is the printed count of a single counted requirement ("up to two" ->
    UP_TO 2, "any number of" -> ANY_NUMBER, "two" -> LITERAL 2); the
    ``possessive`` flag hands the possessed noun on as ``rest_spans``."""
    a, b = (0, len(host)) if span is None else span
    trimmed = rest_spans_after(host, a, b)
    if not trimmed:
        return None
    a, b = trimmed[0]
    ca, cb = _sentence(host, a, b) if clause is None else clause
    if not (ca <= a and b <= cb):
        raise ValueError("target slot %r is outside its clause %r"
                         % ((a, b), (ca, cb)))
    rel = _slot_rel(host[ca:cb], a - ca, b - ca, lemma)
    if rel is None:
        return None
    value, bad, amount, rest, flags, end = rel
    if bad is not None:
        return SlotResult(unmodelled=bad, span=(a, b))
    value = TargetSlot(value.requirements,
                       tuple((s + ca, e + ca) for s, e in value.spans),
                       value.residue)
    return SlotResult(value=value, span=(a, end + ca),
                      rest_spans=tuple((s + ca, e + ca) for s, e in rest),
                      flags=frozenset(flags), amount=amount)


def clear_caches() -> None:
    _words_rel.cache_clear()
    _solver_spans.cache_clear()
    _slot_rel.cache_clear()
