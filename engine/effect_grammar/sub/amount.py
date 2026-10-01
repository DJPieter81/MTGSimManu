"""Amount sub-grammar (design doc 2026-09-29, section 2 ``Amount``,
section 6 "Amount"; A12, A16, A19, A35).

Types how many, once, at LOAD (never at resolution), over L0 output, under
the one leaf contract in `engine.effect_grammar.sub` (``(host, span, *,
lemma="")``, one `SlotResult`, host-absolute spans). Four slots:

* `parse_amount` -- the count at the start of a counted noun phrase ("3
  damage", "two cards", "that many cards plus one", "half x cards, rounded
  down"). The count is consumed; the counted noun is handed on in
  ``rest_spans`` (an operator the noun brackets -- "plus one", ", rounded
  down" -- is consumed with the count, so ``span`` is the hull of the
  consumed words and the noun inside it stays rest);
* `parse_scaler` -- a trailing scaler of a counted verb: "for each <Q>",
  "equal to <EXPR>", "divided as you choose among ..." (CR 601.2d);
* `parse_where_x` -- the definition of X, ", where x is <EXPR>";
* `parse_leading_for_each` -- the leading "for each <Q>, <counted VP>"
  frame (A16).

**Closed table** (`AmountKind`):

* LITERAL -- digits ("1,000" included), the number words through twenty,
  "a" / "an"; "an additional" / "N additional" carry `ADDITIONAL`;
* X -- an X bound from the cost: ``x_bound`` is the caller's (a {X} in the
  mana or activation cost, or a loyalty X, A12); a defined X (``x_defined``,
  from `parse_where_x`) replaces it. An unbound X is UNMODELLED(AMOUNT)
  (section 6), never zero;
* X_DEFINED -- "where x is <EXPR>": ``inner`` is the defining expression;
* EQUAL_TO -- "equal to <Q>": ``quantity`` from the quantity leaf;
* FOR_EACH -- "for each <Q>": ``n`` is the per-unit count the verb printed
  (1 when none), ``quantity`` the counted quantity;
* THAT_MUCH -- "that many", "that much", and a result amount named
  outright ("the damage dealt this way"): one number, one encoding; the
  linker binds it;
* MULTIPLY -- "twice", "double that", "triple that", "<N> times";
  ``n`` the factor, ``inner`` the scaled amount;
* HALF -- "half <amount>" with the printed ``rounding`` ("up" / "down");
* PLUS -- "<amount> plus N", "N plus <amount>", "<amount> minus N"
  (``n`` signed);
* UP_TO -- "up to N" (``n`` the literal bound) or "up to <amount>"
  (``inner`` the variable bound);
* ANY_NUMBER -- "any number of", and "any amount of" (A19); the choice is
  `choose_amount`'s at resolution (A35);
* ALL -- "all";
* DIVIDED -- the printed total (``inner``) divided among the targets, with
  ``evenly`` and ``rounding`` when printed.

"a number of" / "an amount of" hold no count of their own: the count is
the trailing scaler's, so the slot is no amount (``value`` and
``unmodelled`` both None) and carries `SCALED`.

Mana is a symbol multiset (the payload leaf's, CR 106), never an amount:
a slot that opens with a mana symbol is refused. A quantity the quantity
leaf cannot count makes the amount that leaf's UNMODELLED(QUANTITY) (the
deepest failure), never zero. A16: a leading "for each" whose body refers
to the element ("a copy of it", "of that type") is UNMODELLED(ITERATION).

Every other refusal is UNMODELLED(AMOUNT) over the whole trimmed slot with
detail ``amount.<code>[:<param>]`` from `DETAIL_CODES`. The leaf reads no
card name and no game state.
"""
from __future__ import annotations

import dataclasses
import re
from functools import lru_cache
from typing import Optional, Tuple

from engine.effect_grammar.sub import CACHE_SIZE, SlotResult, Span, unmodelled
from engine.effect_grammar.sub import quantity as _Q
from engine.effect_spec import (Amount, AmountKind, Quantity, QuantityKind,
                                Stage, Unmodelled)
from engine.target_solver import _NUMBER_WORDS

__all__ = ["LEAF", "DETAIL_CODES", "ADDITIONAL", "SCALED", "NUMBER_WORDS",
           "parse_amount", "parse_scaler", "parse_where_x",
           "parse_leading_for_each", "clear_caches"]

LEAF = "amount"
DETAIL_CODES = frozenset({
    "empty", "no_count", "ordinal", "x_unbound", "mana_symbols", "exponent",
    "exactly", "operand", "operator", "sum", "scaler", "for_each_base",
    "equal_to_base", "divided_base", "divided_chooser", "where_x",
    "for_each_comma", "element_anaphor"})

ADDITIONAL = "additional"   # flag: "an additional card" (one more)
SCALED = "scaled"           # flag: "a number of" -- the scaler holds the count

# The target solver's number words (one table) extended through twenty,
# the highest count word the pool prints.
NUMBER_WORDS = dict(_NUMBER_WORDS, eleven=11, twelve=12, thirteen=13,
                    fourteen=14, fifteen=15, sixteen=16, seventeen=17,
                    eighteen=18, nineteen=19, twenty=20)
_WORDS = sorted(NUMBER_WORDS, key=len, reverse=True)
_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+|%s)" % "|".join(_WORDS)

_X = Amount(AmountKind.X, n=1)
_THAT = Amount(AmountKind.THAT_MUCH)
_FACTORS = {"twice": 2, "double": 2, "triple": 3}


def _number(word: str) -> Optional[int]:
    if word.replace(",", "").isdigit():
        return int(word.replace(",", ""))
    return NUMBER_WORDS.get(word)


# A relative parse over the stripped slot text: (value, failure, pieces,
# pending, flags). `pieces` are the consumed (start, end) spans of the
# text; failure is (stage, code, param) or a quantity Unmodelled.
_Rel = Tuple[Optional[Amount], object, Tuple[Span, ...],
             Tuple[Tuple[str, str], ...], frozenset]


def _fail(code: str, param: str = "", stage: Stage = Stage.AMOUNT) -> _Rel:
    return None, (stage, code, param), (), (), frozenset()


def _finish(host: str, span: Optional[Span], rel_fn, lemma: str) -> SlotResult:
    """Run the memoised relative parser over the trimmed slot and lift its
    result into host coordinates. On failure the span is the whole trimmed
    slot; on success the span is the hull of the consumed pieces and the
    rest is every unconsumed stretch of the slot."""
    a, b = (0, len(host)) if span is None else span
    slot = host[a:b]
    lead = len(slot) - len(slot.lstrip())
    trimmed = slot.strip()
    body = trimmed.rstrip(" .,;")
    start = a + lead
    value, failure, pieces, pending, flags = rel_fn(body)
    if value is None and failure is not None:
        if isinstance(failure, Unmodelled):
            um = dataclasses.replace(failure, lemma=lemma)
        else:
            stage, code, param = failure
            um = unmodelled(stage, lemma, LEAF, code, DETAIL_CODES, param)
        return SlotResult(unmodelled=um, span=(start, start + len(trimmed)))
    hull = (start + pieces[0][0], start + pieces[-1][1]) if pieces else (start, start)
    rest, pos = [], 0
    for p, q in pieces + ((len(body), len(body)),):
        _rest_piece(body, pos, p, start, rest)
        pos = max(pos, q)
    return SlotResult(value=value, span=hull, rest_spans=tuple(rest),
                      flags=flags, pending=pending)


def _rest_piece(body: str, pos: int, end: int, offset: int, out: list) -> None:
    while pos < end and body[pos] in " ,":
        pos += 1
    while end > pos and body[end - 1] in " ,":
        end -= 1
    if pos < end:
        out.append((offset + pos, offset + end))


# ── Operands through the quantity leaf ─────────────────────────────────

_ROUNDING_RE = re.compile(r",? rounded (?P<r>up|down)\b")
# A result amount named outright is "that much" (one number, one encoding).
_RESULT_AMOUNT_RE = re.compile(
    r"(?:the |that )(?:amount of )?(?:damage|life)"
    r"(?: (?:dealt|gained|lost|prevented)(?: [a-z~' ]+?)? this way)?")


_CLAUSE_COMMA_RE = re.compile(r", (?!rounded\b)")


def _operand_end(t: str, pos: int) -> int:
    """Where an operand at ``t[pos:]`` may end: before its printed
    rounding, when the rounding closes the operand's own clause (no clause
    comma comes first), else the end of the text."""
    m = _ROUNDING_RE.search(t, pos)
    if m is None:
        return len(t)
    c = _CLAUSE_COMMA_RE.search(t, pos, m.start())
    return len(t) if c is not None else m.start()


def _read_quantity(t: str, pos: int, end: int, source_left: bool) -> SlotResult:
    """The quantity leaf over ``t[pos:end]``. When the whole stretch is no
    quantity it is retried up to each clause comma, longest first: a
    quantity's own commas ("artifact, creature, and land you control") are
    read whole first, and a slot that runs on past its clause (", then
    shuffle") is cut where the quantity ends. The failure of the whole
    stretch is the one reported."""
    r = _Q.parse_quantity(t, (pos, end), source_left=source_left)
    if r.value is not None:
        return r
    cuts = [m.start() for m in _CLAUSE_COMMA_RE.finditer(t, pos, end)]
    for cut in reversed(cuts):
        c = _Q.parse_quantity(t, (pos, cut), source_left=source_left)
        if c.value is not None:
            return c
    return r


def _quantity_operand(t: str, pos: int, end: int, source_left: bool):
    """(amount, end, pending, flags, failure) of a quantity at
    ``t[pos:end]`` through the quantity leaf."""
    r = _read_quantity(t, pos, end, source_left)
    if r.value is None:
        return None, pos, (), frozenset(), r.unmodelled
    return (Amount(AmountKind.EQUAL_TO, quantity=r.value), r.span[1],
            r.pending, r.flags, None)


def _operand(t: str, pos: int, x, source_left: bool):
    """The operand of an expression at ``t[pos:]``: "that many", "that
    much", a named result amount, X, or a quantity. Returns (amount, end,
    pending, flags, failure)."""
    end = _operand_end(t, pos)
    for phrase in ("that many", "that much"):
        if t.startswith(phrase, pos) and (pos + len(phrase) == len(t) or
                                          not t[pos + len(phrase)].isalpha()):
            return _THAT, pos + len(phrase), (), frozenset(), None
    m = _RESULT_AMOUNT_RE.match(t, pos)
    if m is not None and m.end() == end:
        return _THAT, m.end(), (), frozenset(), None
    if re.match(r"x\b", t[pos:]):
        if isinstance(x, tuple):
            return None, pos, (), frozenset(), x
        return x, pos + 1, (), frozenset(), None
    return _quantity_operand(t, pos, end, source_left)


def _x_value(x_bound: bool, x_defined: Optional[Amount]):
    """The value X takes here, or the failure tuple when it is unbound."""
    if x_defined is not None:
        return x_defined
    if x_bound:
        return _X
    return (Stage.AMOUNT, "x_unbound", "")


_PLUS_RE = re.compile(r" (?P<op>plus|minus) (?P<n>%s)(?![\w])" % _NUM)
_PLUS_ANY_RE = re.compile(r" (?:plus|minus) ")
_LEAD_PLUS_RE = re.compile(r"(?P<n>%s) plus " % _NUM)
_FACTOR_RE = re.compile(r"(?:(?P<w>twice)|(?P<n>%s) times) " % _NUM)


def _expression(t: str, pos: int, x, source_left: bool) -> _Rel:
    """EXPR := [N plus] [twice | N times | half] OPERAND [, rounded up|down]
    [plus N | minus N], over ``t[pos:]``; the rest after it is left."""
    lead = _LEAD_PLUS_RE.match(t, pos)
    addend = 0
    if lead is not None:
        addend = _number(lead.group("n"))
        pos = lead.end()
    factor, half = 1, False
    m = _FACTOR_RE.match(t, pos)
    if m is not None:
        factor = 2 if m.group("w") else _number(m.group("n"))
        pos = m.end()
    elif t.startswith("half ", pos):
        half = True
        pos += len("half ")
    value, end, pending, flags, failure = _operand(t, pos, x, source_left)
    if failure is not None:
        return None, failure, (), (), frozenset()
    if factor != 1:
        value = Amount(AmountKind.MULTIPLY, n=factor, inner=value)
    rounding = _ROUNDING_RE.match(t, end)
    if half:
        value = Amount(AmountKind.HALF, inner=value,
                       rounding=rounding.group("r") if rounding else None)
        if rounding is not None:
            end = rounding.end()
    tail = _PLUS_RE.match(t, end)
    if tail is not None:
        n = _number(tail.group("n"))
        addend += n if tail.group("op") == "plus" else -n
        end = tail.end()
    elif _PLUS_ANY_RE.match(t, end):
        return _fail("sum")
    if addend:
        value = Amount(AmountKind.PLUS, n=addend, inner=value)
    return value, None, ((0, end),), pending, flags


# ── Counts ─────────────────────────────────────────────────────────────

_ORDINAL_RE = re.compile(r"(?:(?:your|their|his or her|a|the) )?"
                         r"(?:first|second|third|fourth|fifth)\b")
_HALF_LIFE_RE = re.compile(
    r"half (?P<who>your|their|his or her|that player's|its controller's"
    r"|target player's|target opponent's|each opponent's) life(?: total)?\b")
_MULT_RE = re.compile(
    r"(?:(?P<w>twice|double|triple)|(?P<n>%s) times) "
    r"(?P<op>that many|that much|that|x|%s)(?![\w])" % (_NUM, _NUM))
_HALF_RE = re.compile(r"half (?P<op>that many|that much|that|x)(?![\w])")
_UP_TO_RE = re.compile(r"up to (?P<op>that many|that much|x|%s)(?![\w])" % _NUM)
_SIMPLE_RE = re.compile(
    r"(?P<p>any number of|any amount of|a number of|an amount of|that many"
    r"|that much|an additional|all|x|(?P<n>%s)(?: (?P<add>additional))?"
    r"|an|a)(?!\w|,\d)" % _NUM)
# The counted noun an operator may bracket ("cards" in "that many cards
# plus one"): up to three words that are no clause boundary.
_NOUN = (r"(?: (?!(?:to|and|or|then|for|equal|where|from|on|onto|into|at"
         r"|in|of|among|divided|instead|unless|if|this|that|each|plus|minus)"
         r"(?![\w']))[a-z0-9+/\-'{}~]+){0,3}")
_TRAIL_PLUS_RE = re.compile(_NOUN + r" (?P<op>plus|minus) (?P<n>\S+?)(?=$|[ ,.;])")
_TRAIL_ROUND_RE = re.compile(_NOUN + r",? rounded (?P<r>up|down)\b")
# What may follow a trailing "plus N": the end of the counted phrase. A
# noun after N ("2 life plus 2 life for each ...") makes it a second
# amount, a sum the table has no kind for.
_PHRASE_END_RE = re.compile(
    r"$|[,.;:]| (?:instead|to|on|onto|into|from|among|unless|if|this|until"
    r"|and|then|where|divided|at)\b")


def _atom(op: str, x) -> object:
    """An operand word: that many / that much / that, X or a number."""
    if op in ("that many", "that much", "that"):
        return _THAT
    if op == "x":
        return x
    return Amount(AmountKind.LITERAL, n=_number(op))


@lru_cache(maxsize=CACHE_SIZE)
def _count_rel(t: str, x_bound: bool, x_defined: Optional[Amount]) -> _Rel:
    if not t:
        return _fail("empty")
    if t.startswith("{"):
        return _fail("mana_symbols")
    if "ˣ" in t.split(" ", 1)[0]:
        return _fail("exponent")
    if t.startswith("exactly "):
        return _fail("exactly")
    x = _x_value(x_bound, x_defined)
    flags = frozenset()
    pending: Tuple[Tuple[str, str], ...] = ()
    m = _HALF_LIFE_RE.match(t)
    if m is not None:
        player, pending = _Q._player(m.group("who"))
        q = Quantity(QuantityKind.LIFE_TOTAL, player=player, raw=m.group(0)[5:])
        inner = Amount(AmountKind.EQUAL_TO, quantity=q)
        end = m.end()
        r = _ROUNDING_RE.match(t, end)
        value = Amount(AmountKind.HALF, inner=inner,
                       rounding=r.group("r") if r else None)
        return value, None, ((0, r.end() if r else end),), pending, flags
    if t.startswith("half the "):
        v, end, pending, flags, failure = _quantity_operand(
            t, len("half the "), _operand_end(t, len("half the ")), False)
        if failure is not None:
            return None, failure, (), (), frozenset()
        r = _ROUNDING_RE.match(t, end)
        value = Amount(AmountKind.HALF, inner=v,
                       rounding=r.group("r") if r else None)
        return value, None, ((0, r.end() if r else end),), pending, flags
    m = _MULT_RE.match(t) or _HALF_RE.match(t) or _UP_TO_RE.match(t)
    if m is not None:
        inner = _atom(m.group("op"), x)
        if isinstance(inner, tuple):
            return None, inner, (), (), frozenset()
        if m.re is _MULT_RE:
            factor = _FACTORS.get(m.group("w")) or _number(m.group("n"))
            value = Amount(AmountKind.MULTIPLY, n=factor, inner=inner)
        elif m.re is _HALF_RE:
            value = Amount(AmountKind.HALF, inner=inner)
        elif inner.kind is AmountKind.LITERAL:
            value = Amount(AmountKind.UP_TO, n=inner.n)
        else:
            value = Amount(AmountKind.UP_TO, inner=inner)
        end = m.end()
    else:
        if t.startswith("half "):
            return _fail("operand")
        m = _SIMPLE_RE.match(t)
        if m is None:
            if _ORDINAL_RE.match(t):
                return _fail("ordinal")
            return _fail("no_count", t.split(" ", 1)[0])
        p, end = m.group("p"), m.end()
        if p in ("a number of", "an amount of"):
            return None, None, ((0, end),), (), frozenset({SCALED})
        if p in ("any number of", "any amount of"):
            value = Amount(AmountKind.ANY_NUMBER)
        elif p in ("that many", "that much"):
            value = _THAT
        elif p == "all":
            value = Amount(AmountKind.ALL)
        elif p == "x":
            if isinstance(x, tuple):
                return None, x, (), (), frozenset()
            value = x
        elif p == "an additional":
            value, flags = Amount(AmountKind.LITERAL, n=1), frozenset({ADDITIONAL})
        elif p in ("a", "an"):
            if _ORDINAL_RE.match(t):
                return _fail("ordinal")
            value = Amount(AmountKind.LITERAL, n=1)
        else:
            value = Amount(AmountKind.LITERAL, n=_number(m.group("n")))
            if m.group("add"):
                flags = frozenset({ADDITIONAL})
    pieces = [(0, end)]
    # An operator on the count the counted noun brackets.
    if value.kind is AmountKind.HALF:
        r = _TRAIL_ROUND_RE.match(t, end)
        if r is not None:
            value = dataclasses.replace(value, rounding=r.group("r"))
            pieces.append((_round_start(t, r), r.end()))
    else:
        r = _TRAIL_PLUS_RE.match(t, end)
        if r is not None:
            n = _number(r.group("n"))
            if n is None:
                return _fail("operator", r.group("n"))
            if not _PHRASE_END_RE.match(t, r.end()):
                return _fail("sum")
            value = Amount(AmountKind.PLUS,
                           n=n if r.group("op") == "plus" else -n, inner=value)
            pieces.append((r.start("op") - 1, r.end()))
    return value, None, tuple(pieces), pending, flags


def _round_start(t: str, m) -> int:
    """Where the ", rounded up|down" of a trailing-round match begins."""
    i = t.rindex("rounded", 0, m.end())
    i -= 1                       # the space before "rounded"
    if i > 0 and t[i - 1] == ",":
        i -= 1
    return i


def parse_amount(host: str, span: Optional[Span] = None, *, lemma: str = "",
                 x_bound: bool = False,
                 x_defined: Optional[Amount] = None) -> SlotResult:
    """The count at the start of ``host[span]`` (default: the whole host).

    ``x_bound`` is True when the host's cost binds X (a {X} in the mana or
    activation cost, a loyalty X); ``x_defined`` is the host's
    `parse_where_x` value, which replaces X. ``lemma`` is the caller's
    printed lemma. On success ``span`` is the hull of the consumed count
    and ``rest_spans`` the counted noun and the text after it; for "a
    number of" the slot holds no amount (`SCALED`). Otherwise
    UNMODELLED(AMOUNT) over the whole trimmed slot."""
    return _finish(host, span, lambda b: _count_rel(b, x_bound, x_defined),
                   lemma)


# ── Scalers ────────────────────────────────────────────────────────────

_DIVIDED_RE = re.compile(
    r"divided (?:(?P<choose>as you choose)|(?P<other>as [a-z' ]+? chooses?)"
    r"|(?P<evenly>evenly)(?:,? rounded (?P<r>up|down),?)?)(?= among\b|$)")


@lru_cache(maxsize=CACHE_SIZE)
def _scaler_rel(t: str, per: Optional[Amount], x_bound: bool,
                x_defined: Optional[Amount], source_left: bool) -> _Rel:
    if t.startswith("for each "):
        if per is not None and per.kind is not AmountKind.LITERAL:
            return _fail("for_each_base")
        n = 1 if per is None else per.n
        pos = len("for each ")
        r = _read_quantity(t, pos, len(t), source_left)
        if r.value is None:
            return None, r.unmodelled, (), (), frozenset()
        value = Amount(AmountKind.FOR_EACH, n=n, quantity=r.value)
        return value, None, ((0, r.span[1]),), r.pending, r.flags
    if t.startswith("equal to "):
        if per is not None:
            return _fail("equal_to_base")
        value, failure, pieces, pending, flags = _expression(
            t, len("equal to "), _x_value(x_bound, x_defined), source_left)
        return value, failure, pieces, pending, flags
    m = _DIVIDED_RE.match(t)
    if m is not None:
        if m.group("other"):
            return _fail("divided_chooser")
        if per is None:
            return _fail("divided_base")
        value = Amount(AmountKind.DIVIDED, inner=per,
                       evenly=bool(m.group("evenly")), rounding=m.group("r"))
        return value, None, ((0, m.end()),), (), frozenset()
    return _fail("scaler", t.split(" ", 1)[0])


def parse_scaler(host: str, span: Optional[Span] = None, *, lemma: str = "",
                 per: Optional[Amount] = None, x_bound: bool = False,
                 x_defined: Optional[Amount] = None,
                 source_left: bool = False) -> SlotResult:
    """The trailing scaler at the start of ``host[span]``: "for each <Q>",
    "equal to <EXPR>" or "divided ... among".

    ``per`` is the count the counted verb printed (its `parse_amount`
    value; None when it printed none): the per-unit count of FOR_EACH and
    the total of DIVIDED. "equal to" takes no printed count. ``source_left``
    is passed to the quantity leaf (A27, CR 608.2h). The rest after the
    scaler (a recipient, "among <targets>") is handed on."""
    return _finish(host, span,
                   lambda b: _scaler_rel(b, per, x_bound, x_defined,
                                         source_left), lemma)


_WHERE_X_RE = re.compile(r",? ?where x is ")


@lru_cache(maxsize=CACHE_SIZE)
def _where_x_rel(t: str, source_left: bool) -> _Rel:
    m = _WHERE_X_RE.match(t)
    if m is None or m.end() == len(t):
        return _fail("where_x")
    value, failure, pieces, pending, flags = _expression(
        t, m.end(), (Stage.AMOUNT, "x_unbound", ""), source_left)
    if value is None:
        return value, failure, pieces, pending, flags
    start = 0
    while t[start] in ", ":
        start += 1
    return (Amount(AmountKind.X_DEFINED, inner=value), None,
            ((start, pieces[-1][1]),), pending, flags)


def parse_where_x(host: str, span: Optional[Span] = None, *, lemma: str = "",
                  source_left: bool = False) -> SlotResult:
    """The definition of X in ``host[span]`` (", where x is <EXPR>"):
    X_DEFINED whose ``inner`` is the defining expression. The caller hands
    the value to `parse_amount` / `parse_scaler` as ``x_defined``."""
    return _finish(host, span, lambda b: _where_x_rel(b, source_left), lemma)


# ── A16: the leading "for each" frame ──────────────────────────────────

# A body word that refers back to the element being iterated.
_ELEMENT_ANAPHOR_RE = re.compile(
    r"\b(?:it|its|itself|they|them|their|themselves|that (?!many\b|much\b)"
    r"[a-z]+|those [a-z]+|the chosen [a-z]+)\b")


@lru_cache(maxsize=CACHE_SIZE)
def _leading_rel(t: str, source_left: bool) -> _Rel:
    commas = [i for i, c in enumerate(t) if c == ","]
    if not commas:
        return _fail("for_each_comma")
    pos = len("for each ")
    # The frame ends at the comma after the longest head that is a whole
    # quantity: a type list ("artifact, creature, and land you control")
    # holds commas of its own.
    chosen, q = commas[0], None
    for c in reversed(commas):
        r = _Q.parse_quantity(t, (pos, c), source_left=source_left)
        if r.value is not None and r.span[1] == c:
            chosen, q = c, r
            break
    body = t[chosen + 1:]
    if _ELEMENT_ANAPHOR_RE.search(body):
        return _fail("element_anaphor", stage=Stage.ITERATION)
    if q is None:
        r = _Q.parse_quantity(t, (pos, chosen), source_left=source_left)
        return None, r.unmodelled, (), (), frozenset()
    value = Amount(AmountKind.FOR_EACH, n=1, quantity=q.value)
    return value, None, ((0, chosen + 1),), q.pending, q.flags


def parse_leading_for_each(host: str, span: Optional[Span] = None, *,
                           lemma: str = "",
                           source_left: bool = False) -> Optional[SlotResult]:
    """A16: the leading "for each <Q>, <body>" of ``host[span]``, or None
    when the slot does not open with "for each ". FOR_EACH(1, Q) over the
    consumed "for each <Q>," with the body as rest; UNMODELLED(ITERATION)
    when the body refers to the element."""
    a, b = (0, len(host)) if span is None else span
    if not host[a:b].lstrip().startswith("for each "):
        return None
    return _finish(host, span, lambda body: _leading_rel(body, source_left),
                   lemma)


def clear_caches() -> None:
    for fn in (_count_rel, _scaler_rel, _where_x_rel, _leading_rel):
        fn.cache_clear()
