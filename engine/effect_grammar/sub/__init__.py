"""Closed sub-grammars of the clause grammar (design doc 2026-09-29, L4):
target, participant, filter, amount, quantity, condition, duration,
destination and payload. Each is a closed table over normalised clause
text; an unknown phrase yields a typed UNMODELLED stage, never a guess.

This module is the one leaf contract. Every leaf follows it, so the L4
spine calls every leaf the same way and the section 3 coverage invariant
(every character of an effect-bearing host is covered by a spec, frame or
rider span) can be checked uniformly.

**Calling convention.** A slot parser (every public ``parse_*`` that
returns a `SlotResult`, in every grammar module) takes
``(host, span=None, *, lemma="", <leaf keywords>)``: ``host`` is the WHOLE
normalised host text and ``span`` the slot inside it
(``host[span[0]:span[1]]``), None for the whole host. Every span a leaf
returns indexes ``host``, never the slot and never a rewritten string. The
one exception is payload's ``parse_payload(entry, host, span=None,
facts=None, *, lemma="")`` and ``parse_modification(entry, host, ...)``:
the lexicon entry L4 read comes first because it selects the payload
grammar, and its lemma is the default printed lemma.

**Nothing here.** A slot parser returns ``None`` -- the one "absent"
encoding -- when its slot holds nothing of its kind: a verb that takes no
payload, no duration, no delay, no condition, no counted target word, no
chooser, no leading "for each", no keyword line, no cost rule, no loyalty
cost, no cost modifier.

**Result.** Otherwise a slot parser returns one `SlotResult`:

* ``value`` XOR ``unmodelled`` on a parse; both None only in two closed
  cases: an A19 choice whose options are in ``alternatives``, or a
  deferred count -- a phrase holding no count of its own because a
  trailing scaler holds it ("a number of cards equal to ..."), flagged
  `SCALED`, with the phrase as ``span``;
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
exposes ``clear_caches()``; `clear_caches` here clears the sub-grammars'
only, and the package's `engine.effect_grammar.clear_caches` -- the one
entry point the load driver calls once the grammar pass finishes -- clears
these and every leaf beside them (normalize, keywords, lexicon).

**Names.** A leaf's `LEAF` is its module name; the census groups details
by it and `LEAF_EDGES` is keyed by it.

**Refusal propagation.** When a leaf reads part of its slot through
another leaf and that callee REFUSES the phrase, the caller returns the
callee's `Unmodelled` unchanged -- its stage and its
``<leaf>.<code>[:<param>]`` detail, so the refused token survives -- with
the caller's lemma (pass it as the ``code`` of `unmodelled`). One root
cause is then one census bucket whatever the caller, the L4 rule
"UNMODELLED(deepest failure)". A caller uses a code of its own only when
the callee TYPED the phrase and the caller refuses the typed value's shape
(a counter choice where one kind is required, a variable count where an
entry count is required).

**Count words.** `NUMBER_WORDS` (and `COUNT_WORDS`, longest first) is the
one count-word table of every leaf; no leaf builds its own, so a printed
count is typed in every slot that counts or in none.

**Dependency edges.** A leaf may import another only along `LEAF_EDGES`
(pinned by a test): destination reads counter noun phrases through
payload's counter parser (one count table, one kind vocabulary); payload
reads the duration boundary from duration (one duration table).
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Any, FrozenSet, Optional, Tuple

from engine.effect_spec import Amount, Ref, RefKind, Unmodelled
from engine.target_solver import _NUMBER_WORDS as _SOLVER_NUMBER_WORDS

__all__ = ["Span", "SlotResult", "unmodelled", "rest_spans_after",
           "join_spans", "SELF_NOUNS", "CACHE_SIZE", "LEAF_EDGES", "SCALED",
           "NUMBER_WORDS", "COUNT_WORDS", "POSSESSIVES", "POSSESSIVE",
           "OWNER_POSSESSIVE", "possessive_player", "clear_caches"]

Span = Tuple[int, int]

# Object nouns whose "this <noun>" L0 rewrites to ~ (L0 step 3): the
# card types, and the permanent subtypes the pool prints of its own source
# ("this Saga", "this Class", "this Case", "this Room", "this Spacecraft").
# "this door" names one half of a Room (CR 709.5), not the source, and
# stays.
SELF_NOUNS = ("creature", "artifact", "enchantment", "land", "planeswalker",
              "permanent", "battle", "spell", "card", "equipment", "aura",
              "vehicle", "token", "saga", "class", "case", "room",
              "spacecraft")

# The one count-word table of every leaf (amount, payload, filter,
# participant, target, condition): the target solver's words extended
# through twenty and its hyphenated compounds ("twenty-five"), the highest
# count words the pool prints. A count word is typed in every slot or in
# none -- a damage count and a token count read the same table.
NUMBER_WORDS = dict(_SOLVER_NUMBER_WORDS, eleven=11, twelve=12, thirteen=13,
                    fourteen=14, fifteen=15, sixteen=16, seventeen=17,
                    eighteen=18, nineteen=19, twenty=20)
NUMBER_WORDS.update({"twenty-" + w: 20 + n
                     for w, n in _SOLVER_NUMBER_WORDS.items() if 1 <= n <= 9})
# The words longest first, so a regex alternation tries "twenty-one"
# before "twenty".
COUNT_WORDS = tuple(sorted(NUMBER_WORDS, key=len, reverse=True))

# The one possessive vocabulary of every leaf: what a zone noun ("into <p>
# graveyard", "from <p> hand", "on top of <p> library") or "under <p>
# control" prints, by the player it names. Destination, filter, quantity
# and the verb lexicon read it; a determiner ("the", "a", "all") is not a
# possessive and stays with the leaf that reads it.
_OBJECT_OWNERS = ("its", "their", "his", "her", "~'s", "that card's",
                  "that creature's", "that permanent's")
POSSESSIVES = {
    "your": "you",
    "an opponent's": "opponents", "each opponent's": "opponents",
    "your opponents'": "opponents", "opponents'": "opponents",
    "target player's": "target", "target opponent's": "target",
    "defending player's": "defending",
    "each player's": "any", "a player's": "any",
    # Anaphors the linker binds (CR 608.2b): a player named earlier, or
    # an object's owner / controller (A9: "~'s owner's").
    "their": "anaphor", "his or her": "anaphor", "its": "anaphor",
    "~'s": "anaphor", "that player's": "anaphor",
    "that opponent's": "anaphor", "its controller's": "anaphor",
}
POSSESSIVES.update({"%s %s" % (o, form): "anaphor" for o in _OBJECT_OWNERS
                    for form in ("owner's", "owners'")})


def _alternation(words) -> str:
    return "(?:%s)" % "|".join(
        re.escape(w) for w in sorted(words, key=len, reverse=True))


POSSESSIVE = _alternation(POSSESSIVES)
# An object owner's possessive ("its owner's", "~'s owner's"): the
# "under <owner> control" of a returned card (CR 110.2).
OWNER_POSSESSIVE = _alternation(
    w for w in POSSESSIVES if w.endswith(("owner's", "owners'")))


def possessive_player(poss: str):
    """(player value, anaphor text) of a printed possessive -- the
    `CardFilter` controller / owner value ("you", "opponents", "any" or a
    `Ref`) and, for an anaphor, the text the linker binds -- or None when
    `poss` is not in `POSSESSIVES`."""
    kind = POSSESSIVES.get(poss)
    if kind is None:
        return None
    if kind == "target":
        return Ref(RefKind.TARGET, noun=poss.split()[1][:-2]), None
    if kind == "defending":
        return Ref(RefKind.DEFENDING_PLAYER), None
    if kind == "anaphor":
        return "any", poss
    return kind, None


# The deferred-count flag (see "Result"): the slot's count is a trailing
# scaler's, so the slot holds neither a value nor a refusal.
SCALED = "scaled"

# One memo bound for every leaf cache: the grammar runs once at load and a
# pool pass has near-zero hit rate, so a cache only needs to absorb repeats
# inside one pass (the pool holds ~29k distinct sentences).
CACHE_SIZE = 1 << 15

# leaf -> leaves it may import (pinned by
# tests/test_effect_grammar_leaf_contract.py).
LEAF_EDGES = {
    # The leaves beside the sub-grammars: L0, the CR 701/702 tables and
    # the verb lexicon.
    "normalize": frozenset(),
    "keywords": frozenset(),
    "duration": frozenset(),
    # Every leaf that types a CR 702 keyword reads the keywords leaf's one
    # table and spelling (`keywords.typed_keyword`).
    "payload": frozenset({"duration", "keywords"}),
    "dest": frozenset({"payload"}),
    "filter": frozenset({"payload", "keywords"}),
    # Target: zones through destination's object reader, comparison
    # operands through the quantity leaf, keyword qualifiers through the
    # keywords leaf.
    "target": frozenset({"dest", "quantity", "keywords"}),
    # The verb lexicon (beside the sub-grammars) reads the CR 701 action
    # names from payload's table, the CR 702 names from keywords' and the
    # zone possessives from destination's.
    "lexicon": frozenset({"payload", "keywords", "dest"}),
    "quantity": frozenset({"filter", "payload", "duration"}),
    # Participants: object groups are the filter leaf's CardFilter, and a
    # counted "target" word is routed by the target leaf's count (F11).
    "participant": frozenset({"filter", "target"}),
    # Amount reads "for each <Q>" / "equal to <Q>" through the quantity
    # leaf (one quantity table).
    "amount": frozenset({"quantity"}),
    # Conditions: counts over the filter leaf's CardFilter, comparands and
    # set measures through the quantity leaf, numbers from the amount
    # leaf's count table, players and object references from the
    # participant leaf, and printed payments ("unless ... pays", "{c} was
    # spent") through payload's PAY payload (A31).
    "condition": frozenset({"amount", "filter", "participant", "payload",
                            "quantity"}),
    # L1, the structure spine (not a leaf): it reads L0 through normalize,
    # keyword lines and loyalty costs through keywords / lexicon, the A5
    # delay through duration, A8 cost deltas and the A6 ADD_MANA test
    # through payload, intervening-ifs and alternative-cost conditions
    # through condition, and the A6 no-target test through target's F11
    # counted target words.
    # The land-subject test (LANDFALL) reads the filter leaf's CR 205.3i
    # land subtypes.
    "structure": frozenset({"normalize", "keywords", "lexicon", "duration",
                            "payload", "condition", "target", "filter"}),
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
    # ('copy_of', np), ('entry', 'tapped'), ('amount', 'a number of'),
    # ('operator', 'plus 2') -- a piece the leaf consumed outside ``span``
    # because unconsumed text sits between (``span`` and ``rest_spans``
    # never overlap) ...
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


def unmodelled(stage, lemma: str, leaf: str, code,
               codes: FrozenSet[str], param: str = "") -> Unmodelled:
    """The one Unmodelled constructor of the leaves: the lemma is the
    caller's, the detail is '<leaf>.<code>[:<param>]' with `code` from the
    leaf's closed list and `param` one word.

    Refusal propagation: when `code` is a callee leaf's `Unmodelled`, it is
    returned unchanged -- its stage and detail -- with the caller's lemma
    (see "Refusal propagation" in the module docstring)."""
    if isinstance(code, Unmodelled):
        return code if code.lemma == lemma else dataclasses.replace(
            code, lemma=lemma)
    if code not in codes:
        raise ValueError("%s: detail code %r is not in DETAIL_CODES" % (leaf, code))
    detail = "%s.%s" % (leaf, code)
    words = param.split()
    if words:
        detail += ":" + words[0]
    return Unmodelled(stage=stage, lemma=lemma, detail=detail)


def clear_caches() -> None:
    """Clear every sub-grammar's memo caches. The leaves beside the
    sub-grammars (normalize, keywords, lexicon) are cleared by the
    package's `engine.effect_grammar.clear_caches`, the load driver's one
    entry point."""
    from engine.effect_grammar.sub import (
        amount, condition, dest, duration, filter, participant, payload,
        quantity, target,
    )
    for leaf in (duration, payload, dest, filter, target, quantity,
                 participant, amount, condition):
        leaf.clear_caches()
