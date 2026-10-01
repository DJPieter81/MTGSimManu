"""L0 of the clause grammar: normalisation with an offset map (design doc
2026-09-29, section 3 L0; A4, A7, A9, A10, A40; E0 step 8).

L0 is the one owner of the text every later layer reads (the leaf
contract's "L0 output" bullet in `engine.effect_grammar.sub`). It runs at
LOAD, once per face, and is a pure function of ``(text, facts)``. The steps
run in this order:

1. **Reminder text.** Parenthesised reminder text is removed by the rule of
   `oracle_parser.strip_reminder_text` (every balanced group not inside
   another, innermost-first removal gives the same result), and every
   removed span is recorded with its paragraph (A4: a choose header, Tiered
   or Spree may live there). The deletion also takes the spacing step 5
   would drop around it.
2. **Named.** ``named <Name>`` masks the printed name to ``⟨nk⟩``: a name
   there is data (a card to search for, a token's name), never a
   self-reference. A name is a run of capitalised words joined by spaces,
   hyphens, a comma before a capitalised word, or the lowercase name
   particles in `_NAME_PARTICLES` (an elided "l'Cie" word is a name word);
   ``or`` / ``and`` / ``and/or`` before another capitalised run starts a
   second name. In a serial list (", and" / ", or") every ", " before a
   capital starts a new name; a bare "A and B" (one card or two) is
   flagged ``ambiguous_name``. The face's own full and face names are
   taken exactly; its short name is not (it may begin another card's
   name, "<short>'s <Noun>").
3. **Self-forms** become ``~``, longest first (steps 2 and 3 read the same
   text; a self-form inside a masked name is dropped):

   * the face's names (`Facts.names`: the full name, the face names and the
     gated legendary short name, see `self_names`), word-bounded on printed
     case. A short name that begins a longer proper name ("<short> the
     <Word>", "<short>, <Word>", "<short>'s <Word>") is a different object
     and stays. A one-word name that reads
     as an imperative verb at clause start (it takes an object noun phrase,
     `_OBJECT_NP_START`) is that verb and stays there;
   * "this <noun>" for the object nouns in `SELF_NOUNS` ("this ability"
     names the ability, CR 113.1, and stays);
   * on planeswalker and legendary-creature faces (A9, M1), the
     self-pronouns by grammatical case: subject he/she and object
     him/her/himself/herself -> ``~``; possessive his/her -> ``~'s``;
     he's/she's -> ``~ is``. "her" is possessive before a closed possessed
     noun (`_POSSESSED_NOUNS`), the object at punctuation, the text's end
     or a word that ends its noun phrase (`_OBJECT_FOLLOWERS`); before any
     other word it is left and flagged ``pronoun_case``.
4. **Quotes.** Each double-quoted span is masked ``⟨qk⟩`` (quote marks
   included). Inside it, a single quote opens a nested quote only right
   after with/gains/gain/has/have (or "' and " after a nested quote that
   just closed) and before a capital letter, ``{``, ``~`` or a mask; it
   closes at the last apostrophe before the enclosing quote's end that is
   not between two letters, or earlier where "' and '" coordinates a second
   nested quote (A10). Apostrophes (owner's, can't, owners') never open, and
   never close while a later candidate exists. Masks are numbered in printed
   order, outer before inner, to depth `QUOTE_DEPTH`.
5. **Surface.** Dashes (em, en, minus) -> ``-``, U+2019 -> ``'``,
   whitespace collapsed (paragraphs on single newlines, no space at a line
   edge or before ``. , ; :``, empty paragraphs dropped), lowercased.

The output is a `Normalized`: the face text, the quote table (each quote's
own normalised text, nested quotes masked inside it), the name table, the
recorded reminders, and closed `FLAGS` for what L0 refused or could not
decide (an unbalanced quote or a quote nested past `QUOTE_DEPTH`, left
unmasked; a pronoun whose case is open, left unrewritten; a name-list
boundary that may be one card or two).

**Offset map.** Every step keeps breakpoints ``(normalised offset, printed
span)`` for the length of one call; the map is never stored. `printed_span`
recomputes it to map a normalised span back to the printed text, for the
load-time views that need printed case or printed self-forms (A7: costs
are typed from "Sacrifice this land", not "~"; A40: the kicked clause is
the exact printed span).
"""
from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from typing import FrozenSet, List, NamedTuple, Optional, Sequence, Tuple

from engine.effect_grammar.sub import CACHE_SIZE, SELF_NOUNS, Span

__all__ = ["LEAF", "FLAGS", "QUOTE_DEPTH", "SELF", "Facts", "Reminder",
           "Normalized", "self_names", "normalize", "printed_span",
           "clear_caches"]

LEAF = "normalize"
SELF = "~"
FLAGS = frozenset({
    "unbalanced_quote",     # a double or nested single quote never closes
    "quote_depth",          # a quote nested past QUOTE_DEPTH
    "ambiguous_name",       # "named A and B": one card or two (step 2)
    "pronoun_case",         # "her" whose case the next word does not fix
})
# A10: a double-quoted span (depth 1) may hold single-quoted abilities
# (depth 2), which may hold their own (depth 3).
QUOTE_DEPTH = 3


class Facts(NamedTuple):
    """The face facts the grammar reads (design section 3, "Facts"). L0
    reads `names`, `type_class`, `is_legendary` and `is_planeswalker`."""
    names: Tuple[str, ...] = ()
    type_class: FrozenSet[str] = frozenset()
    is_spell: bool = False
    is_legendary: bool = False
    is_planeswalker: bool = False
    has_x_cost: bool = False
    keywords702: FrozenSet[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Reminder:
    """One removed reminder span (A4)."""
    host: int           # -1: the face text; k: quote k's text
    paragraph: int      # face-text paragraph it sat in; -1 if that vanished
    at: int             # offset in the host text where it was removed
    printed: Span       # its printed span, parentheses included
    text: str           # its content, surface-normalised (step 5 only)


@dataclass(frozen=True, slots=True)
class Normalized:
    """L0 output for one face."""
    text: str
    quotes: Tuple[str, ...] = ()
    quote_parents: Tuple[int, ...] = ()     # -1 for a top-level quote
    names: Tuple[str, ...] = ()             # printed name behind ⟨nk⟩
    reminders: Tuple[Reminder, ...] = ()
    flags: FrozenSet[str] = frozenset()

    @property
    def paragraphs(self) -> Tuple[str, ...]:
        return tuple(self.text.split("\n")) if self.text else ()


# ── Self names (one owner of the legendary short-name gate) ────────────

_ARTICLES = frozenset({"The", "A", "An"})
# A character's epithet starts at its first " the " / " of ".
_EPITHET_RE = re.compile(r"(.+?) (?:the|of) ")
_POSSESSIVE_ENDS = ("'s", "s'", "’s", "s’")


def _short_name(face: str, is_character: bool,
                subtypes: Sequence[str]) -> Optional[str]:
    comma = ", " in face
    if comma:
        head = face.split(",", 1)[0]
    elif is_character:
        m = _EPITHET_RE.match(face)
        words = face.split()
        if m:
            head = m.group(1)
        elif len(words) > 1:
            head = words[0]
        else:
            return None
    else:
        return None
    first = head.split()[0] if head.split() else ""
    # The article gate reads derived heads only: a printed comma head led by
    # "The" is the character's name. A possessive word is never a name.
    if not head or head == face or (not comma and first in _ARTICLES) \
            or not first[:1].isupper() or head.endswith(_POSSESSIVE_ENDS):
        return None
    if not comma and head.lower() in {s.lower() for s in subtypes}:
        return None         # "<Subtype> <Epithet>": the word is the type
    return head


def self_names(name: str, face_name: str = "", *, is_legendary: bool = False,
               is_character: bool = False, subtypes: Sequence[str] = (),
               meld: bool = False) -> Tuple[str, ...]:
    """`Facts.names` for a face: the full name, the face names (both halves
    of "A // B") and, on a legendary face, the short name of each half: the
    part before its comma; on a character face (creature or planeswalker)
    without a comma, the part before " the " / " of ", else the first word.
    A short name is never an article, and a comma-less one is never one of
    the face's creature subtypes (`subtypes`: pass a creature face's
    subtypes; a planeswalker's subtype is its character name). A short name
    never ends in a possessive ("<Name>'s <Noun>" is not a character).

    `meld` is the card's layout fact: a meld card's "A // B" second half is
    the melded permanent, a different object (CR 712.4), so only the face's
    own half (`face_name`, else the first half) is a self-name. Longest
    first."""
    halves = [h for h in name.split(" // ") if h]
    if meld:
        halves = [face_name or halves[0]] if halves else []
        names = set(halves)
    else:
        names = {name, face_name, *halves}
    if is_legendary:
        for h in ({*halves} if meld else {face_name, *halves}) - {""}:
            s = _short_name(h, is_character, subtypes)
            if s:
                names.add(s)
    names.discard("")
    return tuple(sorted(names, key=lambda n: (-len(n), n)))


# ── Offset map: segments of (out, printed span, atomic) ────────────────

class _Seg(NamedTuple):
    out: str
    ps: int
    pe: int
    atomic: bool        # False: out[i] is printed[ps + i]; True: one unit


def _join(segs: Sequence[_Seg]) -> str:
    if len(segs) == 1:
        return segs[0].out
    return "".join([s.out for s in segs])


class _Map:
    """Normalised offset -> printed offset over one segment list."""
    __slots__ = ("segs", "starts", "n")

    def __init__(self, segs: Sequence[_Seg]):
        self.segs = segs
        self.starts = []
        n = 0
        for s in segs:
            self.starts.append(n)
            n += len(s.out)
        self.n = n

    def _seg(self, off: int) -> int:
        i = bisect_right(self.starts, off) - 1
        return i if i > 0 else 0

    def start(self, off: int) -> int:
        if not self.segs:
            return 0
        if off >= self.n:
            return self.segs[-1].pe
        i = self._seg(off)
        s = self.segs[i]
        return s.ps if s.atomic else s.ps + off - self.starts[i]

    def end(self, off: int) -> int:
        if not self.segs:
            return 0
        if off <= 0:
            return self.segs[0].ps
        i = self._seg(off - 1)
        s = self.segs[i]
        return s.pe if s.atomic else s.ps + off - self.starts[i]


def _slice(segs: Sequence[_Seg], starts: Sequence[int], a: int,
           b: int) -> List[_Seg]:
    """The segments covering normalised [a, b), copy segments cut."""
    out = []
    i = bisect_right(starts, a) - 1
    if i < 0:
        i = 0
    n = len(segs)
    while i < n and starts[i] < b:
        s, o = segs[i], starts[i]
        e = o + len(s.out)
        lo = a if a > o else o
        hi = b if b < e else e
        if hi > lo:
            if lo == o and hi == e:
                out.append(s)
            elif s.atomic:
                out.append(_Seg(s.out[lo - o:hi - o], s.ps, s.pe, True))
            else:
                out.append(_Seg(s.out[lo - o:hi - o], s.ps + lo - o,
                                s.ps + hi - o, False))
        i += 1
    return out


def _apply(segs: List[_Seg], repls: Sequence[Tuple[int, int, str]]) -> List[_Seg]:
    """Replace normalised ranges [a, b) (sorted, disjoint) by atomic text;
    an empty replacement deletes."""
    if not repls:
        return segs
    m = _Map(segs)
    out: List[_Seg] = []
    pos = 0
    for a, b, new in repls:
        out.extend(_slice(segs, m.starts, pos, a))
        if new:
            out.append(_Seg(new, m.start(a), m.end(b), True))
        pos = b
    out.extend(_slice(segs, m.starts, pos, m.n))
    return out


def _pick(cands: List[Tuple[int, int, str]]) -> List[Tuple[int, int, str]]:
    """Leftmost-longest disjoint replacements."""
    if len(cands) < 2:
        return cands
    cands.sort(key=lambda c: (c[0], c[0] - c[1]))
    out, end = [], -1
    for c in cands:
        if c[0] >= end:
            out.append(c)
            end = c[1]
    return out


# ── Step 1: reminder text ──────────────────────────────────────────────

_PAREN_RE = re.compile(r"[()]")


def _reminder_spans(text: str) -> List[Span]:
    """Balanced parenthesised groups not inside another balanced group:
    what `strip_reminder_text`'s innermost-first removal deletes."""
    if "(" not in text:
        return []
    stack, pairs = [], []
    for m in _PAREN_RE.finditer(text):
        i = m.start()
        if m.group() == "(":
            stack.append(i)
        elif stack:
            pairs.append((stack.pop(), i + 1))
    pairs.sort()
    out, end = [], -1
    for a, b in pairs:
        if a >= end:
            out.append((a, b))
            end = b
    return out


def _with_spacing(text: str, a: int, b: int) -> Tuple[int, int, str]:
    """The deletion for reminder span [a, b), taking the spacing step 5
    would drop anyway (before line end or punctuation, at line start), so
    most faces need no second pass."""
    a2 = a
    while a2 > 0 and text[a2 - 1] in " \t":
        a2 -= 1
    if a2 < a and (b == len(text) or text[b] in " \t\n.,;:"):
        return a2, b, ""
    if a2 == 0 or text[a2 - 1] == "\n":
        while b < len(text) and text[b] in " \t":
            b += 1
    return a, b, ""


# ── Step 2: named <Name> ───────────────────────────────────────────────

_WORD_RE = re.compile(r"[^\W_][\w'\-]*")
# Lowercase particles inside a printed card name ("of the", "de la").
_NAME_PARTICLES = frozenset({"of", "the", "a", "an", "to", "from", "in", "on",
                             "at", "for", "with", "de", "da", "du", "la",
                             "le", "von", "van", "der"})
_NAMED_RE = re.compile(r"(?<![\w-])named ")
_NAME_LIST_RE = re.compile(r",? (?:and/or|and|or) ")


# A lowercase-led name word with a capital after its elision ("l'Cie").
_ELIDED_NAME_WORD_RE = re.compile(r"[a-z]{1,2}'[A-Z]")


def _is_cap(word: str) -> bool:
    return (word[:1].isupper() or word[:1].isdigit()
            or _ELIDED_NAME_WORD_RE.match(word) is not None)


def _name_end(text: str, pos: int) -> int:
    """End of the printed name starting at `pos`, or `pos` if none: a
    capitalised word, then more joined by a space or ", ", each optionally
    led by name particles."""
    end = i = pos
    first = True
    while True:
        j = i
        if not first:
            if text.startswith(", ", j):
                j += 2
            elif text.startswith(" ", j):
                j += 1
            else:
                break
            while True:
                m = _WORD_RE.match(text, j)
                if not (m and m.group() in _NAME_PARTICLES
                        and text.startswith(" ", m.end())):
                    break
                j = m.end() + 1
        m = _WORD_RE.match(text, j)
        if not m or not _is_cap(m.group()):
            break
        end = i = m.end()
        first = False
    return end


_SERIAL_SPLIT_RE = re.compile(r", (?=[A-Z0-9])")


def _named_repls(text: str, own: Sequence[str], flags: set
                 ) -> Tuple[List[Tuple[int, int, str]], List[str]]:
    """Step 2 replacements and the printed names. A full or face name of the
    face's own (`own`, longest first; never a short name, which may begin
    another card's name) printed there and not followed by a possessive is
    taken exactly, so a cost list after it ("named <Own Name>, Sacrifice
    ...") is not read into it. In a serial list (one that continues with
    ", and" / ", or"), every ", " before a capital starts a new name. A bare
    "A and B" may be one card or two: it reads as two and is flagged
    ``ambiguous_name``."""
    repls, names = [], []
    if "named " not in text:
        return repls, names
    own = [n for n in own if n and not _is_short(n, own)]
    for m in _NAMED_RE.finditer(text):
        pos = m.end()
        runs = []           # (start, end, taken exactly)
        serial = False
        join = ""
        while True:
            end = next((pos + len(n) for n in own if text.startswith(n, pos)
                        and _bounded(text, pos, pos + len(n))
                        and not text.startswith("'", pos + len(n))), None)
            exact = end is not None
            if not exact:
                end = _name_end(text, pos)
            if end == pos:
                break
            if join.startswith(","):
                serial = True
            elif join == " and ":
                flags.add("ambiguous_name")
            runs.append((pos, end, exact))
            lm = _NAME_LIST_RE.match(text, end)
            if not lm:
                break
            join = lm.group()
            pos = lm.end()
        for a, b, exact in runs:
            cuts = [a]
            if serial and not exact:
                cuts += [x.end() for x in _SERIAL_SPLIT_RE.finditer(text, a, b)]
            ends = [c - 2 for c in cuts[1:]] + [b]
            for x, y in zip(cuts, ends):
                repls.append((x, y, "⟨n%d⟩" % len(names)))
                names.append(text[x:y])
    return repls, names


# ── Step 3: self-forms ─────────────────────────────────────────────────

# The noun is printed capitalised for some ("this Aura", "this Equipment").
_THIS_NOUN_RE = re.compile(r"(?<![\w-])[Tt]his (?i:%s)(?![\w-])"
                           % "|".join(SELF_NOUNS))
# A9 pronouns by case. "he"/"she" never match inside he's/she's or a
# hyphenated word.
_PRONOUN_RE = re.compile(
    r"(?<![\w'\-~])(?P<w>[Hh]e's|[Ss]he's|[Hh]imself|[Hh]erself|[Hh]im|"
    r"[Hh]is|[Hh]er|[Hh]e|[Ss]he)(?![\w'\-])")
_PRONOUN_FORM = {"he's": SELF + " is", "she's": SELF + " is",
                 "himself": SELF, "herself": SELF, "him": SELF, "he": SELF,
                 "she": SELF, "his": SELF + "'s"}
# Nouns "her" possesses on character faces (closed; "her" before any other
# word that is not an object follower is refused, `pronoun_case`).
_POSSESSED_NOUNS = frozenset({
    "owner", "controller", "power", "toughness", "mana", "loyalty", "face",
    "morph", "sneak", "base", "own", "name", "abilities", "ability", "hand",
    "library", "graveyard", "color", "colors", "counters", "life"})
# Words that end the noun phrase after "her": "her" there is the object.
_OBJECT_FOLLOWERS = frozenset({
    "to", "into", "onto", "on", "from", "and", "or", "with", "until", "at",
    "as", "in", "under", "then", "this", "that", "the", "a", "an", "if",
    "unless", "instead", "for", "each", "where", "when", "except", "by"})
_NEXT_WORD_RE = re.compile(r" ([A-Za-z~]+)")
# Words that open an object noun phrase: a one-word name followed by one at
# clause start is the imperative verb it collides with.
_OBJECT_NP_START = frozenset({
    "target", "a", "an", "all", "each", "another", "any", "up", "x", "one",
    "two", "three", "that", "those", "it", "them", "half", "your", "their",
    "~"})
# A short name followed by this begins a longer proper name: "<short> the
# <Word>", "<short>, <Word>" (a token or another card; the face's own full
# name is a longer candidate and wins), "<short>'s <Word>".
_LONGER_NAME_RE = re.compile(r"(?:(?: (?:the|of))* |, |'s )[A-Z]")
_CLAUSE_OPEN = frozenset("\n.:;•—\"'")


def _object_follows(text: str, j: int) -> bool:
    nxt = _NEXT_WORD_RE.match(text, j)
    return bool(nxt) and nxt.group(1).lower() in _OBJECT_NP_START


def _at_clause_start(text: str, i: int) -> bool:
    j = i - 1
    while j >= 0 and text[j] in " \t":
        j -= 1
    return j < 0 or text[j] in _CLAUSE_OPEN


def _bounded(text: str, a: int, b: int) -> bool:
    """`text[a:b]` is a whole word run: no word character or hyphen on
    either side."""
    return not ((a > 0 and (text[a - 1].isalnum() or text[a - 1] in "_-"))
                or (b < len(text) and (text[b].isalnum() or text[b] in "_-")))


def _is_short(n: str, names: Sequence[str]) -> bool:
    """A short name: a proper prefix of another name before a comma or a
    space."""
    return any(o != n and (o.startswith(n + ",") or o.startswith(n + " "))
               for o in names)


def _self_repls(text: str, facts: Facts,
                flags: set) -> List[Tuple[int, int, str]]:
    cands: List[Tuple[int, int, str]] = []
    for n in facts.names:
        i = text.find(n) if n else -1
        short = None
        while i >= 0:
            j = i + len(n)
            if short is None:
                short = _is_short(n, facts.names)
            if _bounded(text, i, j) and not (
                    short and _LONGER_NAME_RE.match(text, j)) and not (
                    " " not in n and _at_clause_start(text, i)
                    and _object_follows(text, j)):
                cands.append((i, j, SELF))
            i = text.find(n, j)
    if "his " in text:
        for m in _THIS_NOUN_RE.finditer(text):
            cands.append((m.start(), m.end(), SELF))
    if facts.is_planeswalker or (facts.is_legendary
                                 and "creature" in facts.type_class):
        low = text.lower()
        for m in _PRONOUN_RE.finditer(text):
            w = m.group("w").lower()
            if w == "her":
                nxt = _NEXT_WORD_RE.match(low, m.end())
                word = nxt.group(1) if nxt else ""
                if word in _POSSESSED_NOUNS:
                    form = SELF + "'s"
                elif not word or word in _OBJECT_FOLLOWERS:
                    form = SELF
                else:
                    flags.add("pronoun_case")
                    continue
            else:
                form = _PRONOUN_FORM[w]
            cands.append((m.start(), m.end(), form))
    return _pick(cands)


# ── Step 4: quotes ─────────────────────────────────────────────────────

_NEST_OPEN_RE = re.compile(
    r"(?:(?<=\b[Ww]ith )|(?<=\b[Gg]ains )|(?<=\b[Gg]ain )|(?<=\b[Hh]as )"
    r"|(?<=\b[Hh]ave )|(?<=' and ))'(?=[A-Z{~⟨])")
_NEST_COORD_RE = re.compile(r"' and '(?=[A-Z{~⟨])")


def _letterlike(ch: str) -> bool:
    return ch.isalpha() or ch == SELF


def _nested_spans(s: str, flags: set) -> List[Span]:
    """A10 nested single-quoted spans (quote marks included) in `s`."""
    out = []
    i = 0
    while True:
        m = _NEST_OPEN_RE.search(s, i)
        if not m:
            return out
        o = m.start()
        cands = [p for p in range(o + 1, len(s)) if s[p] == "'" and not (
            p > 0 and p + 1 < len(s) and _letterlike(s[p - 1])
            and _letterlike(s[p + 1]))]
        if not cands:
            flags.add("unbalanced_quote")
            return out
        close = next((p for p in cands if _NEST_COORD_RE.match(s, p)),
                     cands[-1])
        out.append((o, close + 1))
        i = close + 1


class _Quote(NamedTuple):
    parent: int
    segs: List[_Seg]
    printed: Span


def _mask_quotes(segs: List[_Seg], spans: Sequence[Span], depth: int,
                 parent: int, table: List[_Quote], flags: set) -> List[_Seg]:
    """Mask `spans` (quote marks included) of `segs` as ⟨qk⟩, recording
    each quote's inner segments (its own nested quotes masked) in `table`,
    numbered in printed order, outer before inner."""
    if not spans:
        return segs
    if depth > QUOTE_DEPTH:
        flags.add("quote_depth")
        return segs
    m = _Map(segs)
    repls = []
    for a, b in spans:
        k = len(table)
        table.append(None)          # reserve k before the nested quotes
        inner = _slice(segs, m.starts, a + 1, b - 1)
        nested = _nested_spans(_join(inner), flags)
        inner = _mask_quotes(inner, nested, depth + 1, k, table, flags)
        table[k] = _Quote(parent, inner, (m.start(a), m.end(b)))
        repls.append((a, b, "⟨q%d⟩" % k))
    return _apply(segs, repls)


# ── Step 5: surface ────────────────────────────────────────────────────

_SURFACE = str.maketrans({"—": "-", "–": "-", "−": "-", "’": "'"})
_WS_RE = re.compile(r"(?P<nl>[ \t]*\n[ \t\n]*)|(?P<sp>[ \t]+)")
_PUNCT_AFTER_SPACE = frozenset(".,;:")
# Any whitespace _WS_RE would change (most faces have none): a run, a tab,
# a space before punctuation, or whitespace at either edge.
_WS_FIX_RE = re.compile(r"[ \n\t][ \n\t.,;:]|\t")


def _ws_to_fix(text: str) -> bool:
    return bool(text) and (text[0] in " \n\t" or text[-1] in " \n\t"
                           or _WS_FIX_RE.search(text) is not None)


def _surface(segs: List[_Seg]) -> Tuple[List[_Seg], str]:
    """Step 5 over one host: (segments, surface text). Case and dash
    unification keep lengths, so the segments keep their printed case; a
    character whose lowercase changes length makes its segment atomic."""
    text = _join(segs)
    n = len(text)
    repls = []
    for m in (_WS_RE.finditer(text) if _ws_to_fix(text) else ()):
        a, b = m.start(), m.end()
        edge = a == 0 or b == n
        if m.group("nl") is not None:
            new = "" if edge else "\n"
        elif edge or text[b:b + 1] in _PUNCT_AFTER_SPACE:
            new = ""
        else:
            new = " "
        if text[a:b] != new:
            repls.append((a, b, new))
    if repls:
        segs = _apply(segs, repls)
        text = _join(segs)
    low = text.translate(_SURFACE).lower()
    if len(low) != len(text):
        segs = [_Seg(o, s.ps, s.pe, s.atomic or len(o) != len(s.out))
                for s in segs for o in (s.out.translate(_SURFACE).lower(),)]
        low = _join(segs)
    return segs, low


_ANY_WS_RE = re.compile(r"\s+")


def _surface_text(text: str) -> str:
    """Step 5 for recorded text that is never mapped (reminder content)."""
    return _ANY_WS_RE.sub(" ", text).strip().translate(_SURFACE).lower()


# ── The pipeline ───────────────────────────────────────────────────────

class _Result(NamedTuple):
    normalized: Normalized
    segs: List[_Seg]
    quote_segs: Tuple[List[_Seg], ...]


def _run(text: str, facts: Facts) -> _Result:
    flags: set = set()
    segs = [_Seg(text, 0, len(text), False)] if text else []
    joined = text
    # 1. reminder text
    rem_spans = _reminder_spans(text)
    if rem_spans:
        dels, prev = [], 0
        for a, b in rem_spans:
            a2, b2, _ = _with_spacing(text, a, b)
            dels.append((max(a2, prev), b2, ""))
            prev = b2
        segs = _apply(segs, dels)
        joined = _join(segs)
    # 2. named <Name> and 3. self-forms, over the same text: a self-form
    # candidate inside a masked name is dropped (the name is data).
    repls, names = _named_repls(joined, facts.names, flags)
    selfs = _self_repls(joined, facts, flags)
    if repls and selfs:
        selfs = [c for c in selfs
                 if not any(a < c[1] and c[0] < b for a, b, _ in repls)]
    if selfs:
        repls = sorted(repls + selfs) if repls else selfs
    if repls:
        segs = _apply(segs, repls)
        joined = _join(segs)
    # 4. quotes
    marks = []
    i = joined.find('"')
    while i >= 0:
        marks.append(i)
        i = joined.find('"', i + 1)
    if len(marks) % 2:
        flags.add("unbalanced_quote")
        marks = marks[:-1]
    table: List[_Quote] = []
    if marks:
        segs = _mask_quotes(segs, [(marks[i], marks[i + 1] + 1)
                                   for i in range(0, len(marks), 2)],
                            1, -1, table, flags)
    # 5. surface
    segs, out = _surface(segs)
    surfaced = tuple(_surface(q.segs) for q in table)
    quote_segs = tuple(q[0] for q in surfaced)
    quotes = tuple(q[1] for q in surfaced)

    # Reminders: host, offset and paragraph.
    para_lines = []     # printed line of each normalised paragraph
    pos = 0
    m = _Map(segs) if rem_spans else None
    for para in out.split("\n") if out and rem_spans else ():
        para_lines.append(text.count("\n", 0, m.start(pos)))
        pos += len(para) + 1
    reminders = []
    for a, b in rem_spans:
        host, inner = -1, None
        for k, q in enumerate(table):
            if q.printed[0] < a < q.printed[1] and (
                    inner is None or q.printed[0] >= inner[0]):
                host, inner = k, q.printed
        hsegs = segs if host < 0 else quote_segs[host]
        at = sum(len(s.out) for s in hsegs if s.pe <= a)
        top = host
        while top >= 0 and table[top].parent >= 0:
            top = table[top].parent
        if top < 0:
            line = text.count("\n", 0, a)
            paragraph = (para_lines.index(line) if line in para_lines
                         else -1)
        else:
            mask_at = out.index("⟨q%d⟩" % top)
            paragraph = out.count("\n", 0, mask_at)
        reminders.append(Reminder(host, paragraph, at, (a, b),
                                  _surface_text(text[a + 1:b - 1])))
    norm = Normalized(text=out, quotes=quotes,
                      quote_parents=tuple(q.parent for q in table),
                      names=tuple(names), reminders=tuple(reminders),
                      flags=frozenset(flags))
    return _Result(norm, segs, quote_segs)


@lru_cache(maxsize=CACHE_SIZE)
def normalize(text: str, facts: Facts = Facts()) -> Normalized:
    """L0 output for one face's printed text (see the module docstring)."""
    return _run(text or "", facts).normalized


def printed_span(text: str, facts: Facts, span: Span, *,
                 host_index: int = -1) -> str:
    """The printed text behind normalised `span` of one face's L0 host:
    the face text (`host_index` -1) or quote k's text (`host_index` k, the
    `Reminder.host` numbering). The offset map is recomputed for this call
    and dropped (A40); a span that cuts a self-form or a mask widens to it.

    This is the L0 primitive over ONE face's printed text. The design's
    package entry point ``effect_grammar.printed_span(oracle, facts, face,
    host_index, span)`` selects the face's printed text from the card's
    oracle and delegates here; it lands with the face splitter, so the A40
    view (E0 step 14) has this one owner of the offset map."""
    r = _run(text or "", facts)
    segs = r.segs if host_index < 0 else r.quote_segs[host_index]
    m = _Map(segs)
    a, b = span
    return (text or "")[m.start(a):m.end(b)]


def clear_caches() -> None:
    normalize.cache_clear()
