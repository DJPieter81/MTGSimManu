"""Closed sub-grammars of the clause grammar (design doc 2026-09-29, L4):
target, participant, filter, amount, quantity, condition, duration,
destination and payload. Each is a closed table over normalised clause
text; an unknown phrase yields a typed UNMODELLED stage, never a guess.

This module is the one leaf contract. Every leaf follows it, so the L4
spine calls every leaf the same way and the section 3 coverage invariant
(every character of an effect-bearing host is covered by a spec, frame or
rider span) can be checked uniformly.

**Calling convention.** A slot parser takes ``(host, span, *, lemma="")``:
``host`` is the WHOLE normalised host text and ``span`` the slot inside it
(``host[span[0]:span[1]]``). ``span`` defaults to the whole host where a
leaf allows it. Every span a leaf returns indexes ``host``, never the slot
and never a rewritten string.

**Result.** A slot parser returns one `SlotResult`:

* ``value`` XOR ``unmodelled`` on a parse; both None only for "no payload
  here" or an A19 choice whose options are in ``alternatives``;
* ``span``: the consumed phrase on success; the WHOLE (whitespace-trimmed)
  slot on failure, so the census and the coverage invariant see the text
  the leaf did not consume;
* ``rest_spans``: the unconsumed text the caller's other sub-grammars read
  (an object, a scaler, the clause around a duration), as spans of
  ``host`` in order. A leaf never hands text on as a rewritten string;
  `SlotResult.rest_text` joins the spans for display and tests;
* ``flags``, ``pending``, ``amount``, ``alternatives``, ``object_span``: see
  the field comments.

**Unmodelled.** ``lemma`` is the caller-supplied printed lemma, never a leaf
default ('' when the caller has none). ``detail`` is
``"<leaf>.<code>[:<param>]"``: ``<leaf>`` is the leaf's `LEAF` name,
``<code>`` comes from the leaf's closed `DETAIL_CODES`, and the optional
``<param>`` is a single word (the refused token, a keyword-action name). The
census groups by ``(stage, lemma, <leaf>.<code>)``.

**L0 output contract.** Every leaf reads L0 output (design section 3,
L0 steps 1-5) and none re-normalises it:

* lowercased; dashes unified to ``-``; curly apostrophes (U+2019) unified
  to ``'``; whitespace collapsed;
* self-forms already ``~``: the card's names, and "this <noun>" for the
  object nouns in `SELF_NOUNS` ("this creature", "this spell", ...).
  "this ability" is not a self-form (it names the ability, CR 113.1) and
  stays;
* quoted spans masked ``⟨qk⟩``; "named X" masked ``⟨nk⟩``.

**Caches.** A leaf's memo caches are bounded (`CACHE_SIZE`) and every leaf
exposes ``clear_caches()``; `clear_caches` here clears them all, and the
load driver calls it once the grammar pass finishes.

**Dependency edges.** A leaf may import another only along `LEAF_EDGES`
(pinned by a test): destination reads counter noun phrases through
payload's counter parser (one count table, one kind vocabulary); payload
reads the duration boundary from duration (one duration table).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet, Optional, Tuple

from engine.effect_spec import Amount, Unmodelled

__all__ = ["Span", "SlotResult", "unmodelled", "rest_spans_after",
           "join_spans", "SELF_NOUNS", "CACHE_SIZE", "LEAF_EDGES",
           "clear_caches"]

Span = Tuple[int, int]

# Object nouns whose "this <noun>" L0 rewrites to ~ (L0 step 3).
SELF_NOUNS = ("creature", "artifact", "enchantment", "land", "planeswalker",
              "permanent", "battle", "spell", "card", "equipment", "aura",
              "vehicle", "token")

# One memo bound for every leaf cache: the grammar runs once at load and a
# pool pass has near-zero hit rate, so a cache only needs to absorb repeats
# inside one pass (the pool holds ~29k distinct sentences).
CACHE_SIZE = 1 << 15

# leaf -> leaves it may import (pinned by
# tests/test_effect_grammar_leaf_contract.py).
LEAF_EDGES = {
    "duration": frozenset(),
    "payload": frozenset({"duration"}),
    "dest": frozenset({"payload"}),
    "filter": frozenset({"payload"}),
}


@dataclass(frozen=True, slots=True)
class SlotResult:
    """One typed slot of a leaf (see the module docstring)."""
    value: Any = None
    unmodelled: Optional[Unmodelled] = None
    span: Span = (0, 0)
    rest_spans: Tuple[Span, ...] = ()
    flags: FrozenSet[str] = frozenset()
    # Named sub-slot texts left for the linker: ('granted', '⟨qk⟩'),
    # ('copy_of', np), ('entry', 'tapped'), ('amount', 'a number of'), ...
    pending: Tuple[Tuple[str, str], ...] = ()
    # A count / multiplier the phrase printed (token count, variable counter
    # or mana count, keyword-action N). Literal multisets are never amounts.
    amount: Optional[Amount] = None
    # A19 options, each a full SlotResult over the same host.
    alternatives: Tuple["SlotResult", ...] = ()
    # The moved object's span when the slot held one (instead-of override).
    object_span: Optional[Span] = None

    @property
    def ok(self) -> bool:
        return self.value is not None

    def rest_text(self, host: str) -> str:
        """The rest spans' text, joined for display and tests."""
        return join_spans(host, self.rest_spans)


def join_spans(host: str, spans: Tuple[Span, ...]) -> str:
    """The text of `spans` joined by one space, with no space before a
    piece that opens with punctuation ("gets +2/+2" + "." -> "gets +2/+2.")."""
    out = ""
    for a, b in spans:
        piece = host[a:b]
        if out and piece[:1] not in (",", ".", ";", ":"):
            out += " "
        out += piece
    return out


def rest_spans_after(host: str, pos: int, end: int,
                     strip: str = " ") -> Tuple[Span, ...]:
    """The rest span ``host[pos:end]`` with `strip` characters trimmed from
    both ends; () when nothing is left."""
    while pos < end and host[pos] in strip:
        pos += 1
    while end > pos and host[end - 1] in strip:
        end -= 1
    return ((pos, end),) if pos < end else ()


def unmodelled(stage, lemma: str, leaf: str, code: str,
               codes: FrozenSet[str], param: str = "") -> Unmodelled:
    """The one Unmodelled constructor of the leaves: the lemma is the
    caller's, the detail is '<leaf>.<code>[:<param>]' with `code` from the
    leaf's closed list and `param` one word."""
    if code not in codes:
        raise ValueError("%s: detail code %r is not in DETAIL_CODES" % (leaf, code))
    detail = "%s.%s" % (leaf, code)
    words = param.split()
    if words:
        detail += ":" + words[0]
    return Unmodelled(stage=stage, lemma=lemma, detail=detail)


def clear_caches() -> None:
    """Clear every leaf's memo caches (the load driver calls this once the
    grammar pass finishes)."""
    from engine.effect_grammar.sub import dest, duration, filter, payload
    for leaf in (duration, payload, dest, filter):
        leaf.clear_caches()
