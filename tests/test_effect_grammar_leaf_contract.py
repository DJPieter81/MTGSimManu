"""The one leaf contract of the clause grammar's sub-grammars (design doc
2026-09-29, section 3 L4 and the coverage invariant; E0 step 11).

`engine/effect_grammar/sub/__init__.py` states it; these tests pin that
every leaf (duration, destination, payload) follows it, so the L4 spine can
call every leaf the same way:

* one calling convention: ``(host, span)``, every returned span indexes the
  host;
* one result shape, `sub.SlotResult`, whose failure span is the whole slot;
* the rest is handed on as host spans, never as a rewritten string, so a
  later leaf's spans compose with an earlier one's;
* the printed lemma comes from the caller (or the lexicon entry), never a
  leaf default, and details follow ``<leaf>.<code>[:<param>]`` over a
  closed code list;
* one counter noun-phrase parser and one duration boundary;
* bounded memo caches, a clear_caches per leaf, and declared import edges.

Synthetic phrases only; no card names.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from engine.effect_model import Duration, DurationKind, ModKind
from engine.effect_spec import (Amount, AmountKind, Destination,
                                KeywordAction, TokenSpec, Verb)

REPO = Path(__file__).resolve().parent.parent
SUB = REPO / "engine" / "effect_grammar" / "sub"


@dataclass(frozen=True)
class _Entry:
    """A section-4 lexicon entry: verb, family, roles, ... and the printed
    lemma."""
    verb: Verb
    lemma: str = ""
    mod_kind: Optional[ModKind] = None


@dataclass(frozen=True)
class _EntryWithoutLemma:
    verb: Verb


def _leaves():
    """Every grammar leaf -- each sub-grammar and each leaf beside them
    (normalize, keywords, lexicon) -- found by walking the package, so a
    new leaf inherits every contract pin below."""
    import importlib
    import pkgutil

    import engine.effect_grammar as pkg
    out = {}
    for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        if info.ispkg:
            continue
        mod = importlib.import_module(info.name)
        out[info.name.rsplit(".", 1)[1]] = mod
    assert {"duration", "dest", "payload", "filter", "target", "participant",
            "quantity", "amount", "condition", "normalize", "keywords",
            "lexicon"} <= set(out), sorted(out)
    return out


def _leaf_path(name: str) -> Path:
    path = SUB / (name + ".py")
    return path if path.exists() else SUB.parent / (name + ".py")


def test_every_leaf_is_named_by_its_module():
    """The census groups details by `LEAF`, and `LEAF_EDGES` is keyed by
    it: one name per leaf."""
    from engine.effect_grammar.sub import LEAF_EDGES
    for name, leaf in _leaves().items():
        assert leaf.LEAF == name, (name, leaf.LEAF)
        assert name in LEAF_EDGES, name


# ── One calling convention and one result shape ────────────────────────

def test_every_leaf_reads_the_whole_host_and_a_slot_span_and_returns_host_spans():
    from engine.effect_grammar.sub import SlotResult, dest, duration, payload
    host = ("return target creature to its owner's hand. create a 2/2 black "
            "zombie creature token until end of turn.")
    d_slot = (host.index("to its"), host.index("."))
    p_slot = (host.index("a 2/2"), len(host) - 1)
    d = dest.parse_destination(host, d_slot, lemma="return")
    p = payload.parse_payload(_Entry(Verb.CREATE_TOKEN, "create"), host,
                              p_slot, None)
    u = duration.parse_duration(host, p_slot)
    for r in (d, p, u):
        assert isinstance(r, SlotResult)
    assert d.value == Destination("hand")
    assert host[slice(*d.span)] == "to its owner's hand"
    assert isinstance(p.value, TokenSpec) and p.value.subtypes == ("zombie",)
    assert host[slice(*p.span)] == "a 2/2 black zombie creature token"
    assert u.value == Duration(DurationKind.THIS_TURN)
    assert host[slice(*u.span)] == "until end of turn"
    # The payload's rest and the duration are the same host span.
    assert p.rest_spans == (u.span,)


def test_a_failed_slot_reports_the_whole_slot_as_its_span():
    """The census and the coverage invariant see the text the leaf did not
    consume."""
    from engine.effect_grammar.sub import dest, payload
    host = "put it into your hand blorp. put blorp +1/+1 counters on it."
    slot = (host.index("into"), host.index("."))
    d = dest.parse_destination(host, slot, lemma="put")
    assert d.value is None and d.span == slot
    slot = (host.index("blorp +1"), len(host) - 1)
    p = payload.parse_payload(_Entry(Verb.PUT_COUNTERS, "put"), host, slot, None)
    assert p.value is None and p.unmodelled is not None
    assert p.span == slot


def test_every_leaf_result_is_the_one_shared_slot_result():
    from engine.effect_grammar import sub
    for name, leaf in _leaves().items():
        assert not hasattr(leaf, "SlotResult") or leaf.SlotResult is sub.SlotResult, name
        assert "SlotResult" not in getattr(leaf, "__all__", ()), name
    assert _leaves()["duration"].parse_delay(
        "at the beginning of the next end step, draw a card").__class__ is sub.SlotResult


def _slot_parsers():
    """Every public parse_* of every grammar module that returns a
    SlotResult (or None)."""
    import inspect
    out = []
    for mod in _grammar_modules():
        for name, fn in vars(mod).items():
            if not (name.startswith("parse_") and callable(fn)
                    and fn.__module__ == mod.__name__):
                continue
            ret = str(inspect.signature(fn).return_annotation)
            if "SlotResult" in ret:
                out.append((mod.__name__.rsplit(".", 1)[1], name, fn))
    return out


def test_every_slot_parser_takes_host_span_and_a_keyword_lemma():
    """One calling convention: (host, span=None, *, lemma="", ...); the
    entry-first payload dispatchers are the documented exception."""
    import inspect
    parsers = _slot_parsers()
    assert len(parsers) >= 20
    for leaf, name, fn in parsers:
        params = list(inspect.signature(fn).parameters.values())
        if (leaf, name) in {("payload", "parse_payload"),
                            ("payload", "parse_modification")}:
            assert params[0].name == "entry", name
            params = params[1:]
        assert [p.name for p in params[:2]] == ["host", "span"], (leaf, name)
        assert params[1].default is None, (leaf, name)
        lemma = inspect.signature(fn).parameters["lemma"]
        assert lemma.kind is inspect.Parameter.KEYWORD_ONLY, (leaf, name)
        assert lemma.default == "", (leaf, name)


@pytest.mark.parametrize("call", [
    lambda: __import__("engine.effect_grammar.sub.payload", fromlist=["x"]
                       ).parse_payload(_Entry(Verb.DRAW, "draw"), "two cards"),
    lambda: __import__("engine.effect_grammar.sub.duration", fromlist=["x"]
                       ).parse_duration("draw two cards"),
    lambda: __import__("engine.effect_grammar.sub.condition", fromlist=["x"]
                       ).parse_condition("for as long as you control ~"),
    lambda: __import__("engine.effect_grammar.sub.target", fromlist=["x"]
                       ).parse_target("each creature you control"),
    lambda: __import__("engine.effect_grammar.lexicon", fromlist=["x"]
                       ).parse_loyalty_cost("draw a card."),
    lambda: __import__("engine.effect_grammar.keywords", fromlist=["x"]
                       ).parse_keyword_line("scry 2."),
])
def test_nothing_here_is_none_the_one_absent_encoding(call):
    assert call() is None


# ── The rest is host spans, so leaves compose ──────────────────────────

def test_a_later_leaf_reads_an_earlier_leafs_rest_spans_with_host_offsets():
    from engine.effect_grammar.sub import duration, payload
    host = "until end of turn, target creature gets +1/+1 for each artifact you control"
    u = duration.parse_duration(host)
    assert host[slice(*u.span)] == "until end of turn"
    (rest,) = u.rest_spans
    assert host[slice(*rest)] == "target creature gets +1/+1 for each artifact you control"
    slot = (host.index("gets", rest[0]), rest[1])
    p = payload.parse_payload(_Entry(Verb.CONTINUOUS, "get"), host, slot, None)
    assert p.value.kind is ModKind.MODIFY_PT
    assert host[slice(*p.span)] == "gets +1/+1"
    assert p.rest_text(host) == "for each artifact you control"


def test_a_mid_clause_duration_leaves_the_clause_around_it_as_spans():
    from engine.effect_grammar.sub import duration
    host = "target creature gets +2/+2 until end of turn, and it gains flying"
    u = duration.parse_duration(host)
    assert [host[a:b] for a, b in u.rest_spans] == [
        "target creature gets +2/+2", ", and it gains flying"]
    assert u.rest_text(host) == "target creature gets +2/+2, and it gains flying"


# ── The printed lemma and the detail convention ────────────────────────

def test_the_payload_leaf_reads_the_printed_lemma_from_the_entry_or_the_caller():
    from engine.effect_grammar.sub import payload
    text = "your choice of flying or first strike until end of turn"
    for entry, kw in ((_Entry(Verb.CONTINUOUS, "gain"), {}),
                      (_EntryWithoutLemma(Verb.CONTINUOUS), {"lemma": "gain"})):
        r = payload.parse_payload(entry, text, (0, len(text)), None, **kw)
        assert [a.value.get("keywords") for a in r.alternatives] == [
            (("flying", None),), (("first_strike", None),)]
    r = payload.parse_payload(_EntryWithoutLemma(Verb.KEYWORD_ACTION),
                              "zombies 2", (0, 9), None, lemma="amass")
    assert r.value == KeywordAction("amass", Amount(AmountKind.LITERAL, n=2),
                                    "zombie")


def test_an_unmodelled_slot_carries_the_callers_lemma_never_a_leaf_default():
    from engine.effect_grammar.sub import dest, duration, payload
    text = "blorp +1/+1 counters on it"
    r = payload.parse_payload(_Entry(Verb.PUT_COUNTERS, "put"), text,
                              (0, len(text)), None)
    assert r.unmodelled.lemma == "put"
    text = "exile it instead of putting it into its owner's graveyard"
    assert dest.parse_instead_of(text, (0, len(text))).unmodelled.lemma == ""
    assert dest.parse_instead_of(text, (0, len(text)),
                                 lemma="exile").unmodelled.lemma == "exile"
    assert dest.parse_destination("into your hand blorp", (0, 20)
                                  ).unmodelled.lemma == ""
    text = "venture into the dungeon"
    r = payload.parse_keyword_action(text, (0, len(text)), lemma="venture")
    assert r.unmodelled.lemma == "venture"
    assert r.unmodelled.detail == "payload.keyword_action_unsupported:venture_into_the_dungeon"
    d = duration.parse_delay("exile it at the beginning of your next main phase",
                             lemma="exile")
    assert d.unmodelled.lemma == "exile"


_DETAIL_RE = re.compile(r"^(?P<leaf>[a-z]+)\.(?P<code>[a-z_]+)(?::\S+)?$")


@pytest.mark.parametrize("call", [
    lambda L: L["payload"].parse_payload(_Entry(Verb.PUT_COUNTERS, "put"),
                                         "blorp +1/+1 counters", (0, 20), None),
    lambda L: L["payload"].parse_payload(_Entry(Verb.CREATE_TOKEN, "create"),
                                         "a 2/2 blorp to it token", (0, 23), None),
    lambda L: L["payload"].parse_payload(_Entry(Verb.CONTINUOUS, "can't"),
                                         "can't attack alone", (0, 18), None),
    lambda L: L["dest"].parse_destination("into your hand blorp", (0, 20)),
    lambda L: L["dest"].parse_instead_of(
        "exile them instead of putting them anywhere else", (0, 48)),
    lambda L: L["duration"].parse_duration("it gets +1/+0 until end of combat"),
    lambda L: L["duration"].parse_delay(
        "at the beginning of your next main phase this turn, add {c}"),
    lambda L: L["duration"].parse_delay("exile that token at end of combat"),
    lambda L: L["duration"].delay_paragraph(
        "at the beginning of the next end step, sacrifice ~", False),
])
def test_an_unmodelled_detail_is_leaf_dot_code_from_a_closed_list(call):
    leaves = _leaves()
    u = call(leaves).unmodelled
    m = _DETAIL_RE.match(u.detail)
    assert m, u.detail
    by_name = {leaf.LEAF: leaf for leaf in leaves.values()}
    assert m.group("code") in by_name[m.group("leaf")].DETAIL_CODES, u.detail


def test_a_slot_with_neither_value_nor_refusal_is_one_of_the_declared_cases():
    """The contract's both-None cases are closed: an A19 choice whose
    options are in ``alternatives``, or a deferred count -- a
    phrase that holds no count of its own because a trailing scaler holds
    it ("a number of cards equal to ..."), flagged with the contract's one
    `SCALED` marker. A leaf never returns both-None any other way, so the
    L4 spine's "value XOR unmodelled" check reads one rule."""
    from engine.effect_grammar import sub
    from engine.effect_grammar.sub import amount
    assert amount.SCALED is sub.SCALED
    assert "SCALED" in sub.__all__
    assert "SCALED" in (sub.SlotResult.__doc__ or "") + sub.__doc__
    host = "draw a number of cards equal to the number of creatures you control"
    r = amount.parse_amount(host, (host.index("a number"), len(host)), lemma="draw")
    assert r.value is None and r.unmodelled is None
    assert sub.SCALED in r.flags and not r.alternatives
    assert host[slice(*r.span)] == "a number of"
    for text in ("3 damage", "blorp cards", "that many cards", "6 or more damage"):
        r = amount.parse_amount(text, lemma="deal")
        assert (r.value is None) != (r.unmodelled is None), (text, r)
        assert sub.SCALED not in r.flags, text


def test_a_delay_detail_is_a_bounded_code_not_the_printed_clause():
    from engine.effect_grammar.sub import duration
    text = "add {c}{c} at the beginning of your next main phase this turn"
    d = duration.parse_delay(text)
    assert d.unmodelled.detail == "duration.delay_next_step"


# ── One counter noun phrase, one duration boundary ─────────────────────

@pytest.mark.parametrize("np,entry", [
    ("two +1/+1 counters", (("+1/+1", Amount(AmountKind.LITERAL, n=2)),)),
    ("that many +1/+1 counters",
     (("+1/+1", Amount(AmountKind.THAT_MUCH)),)),
    ("x +1/+1 counters", (("+1/+1", Amount(AmountKind.X, n=1)),)),
    ("a +1/+1 counter and a flying counter",
     (("+1/+1", Amount(AmountKind.LITERAL, n=1)),
      ("flying", Amount(AmountKind.LITERAL, n=1)))),
])
def test_an_entry_counter_phrase_reads_through_the_one_counter_parser(np, entry):
    """Destination entry counters and a PUT_COUNTERS payload are the same
    counter noun phrase: one count table, one kind vocabulary."""
    from engine.effect_grammar.sub import dest, payload
    text = "onto the battlefield with %s on it" % np
    r = dest.parse_destination(text, (0, len(text)))
    assert r.value == Destination("battlefield", entry_counters=entry), r
    p = payload.parse_counters(np, (0, len(np)))
    assert p.value is not None and p.rest_spans == ()


def test_an_entry_counter_phrase_the_counter_parser_refuses_keeps_its_refusal():
    """Refusal propagation: payload's refusal reaches the census unchanged
    through the destination leaf."""
    from engine.effect_grammar.sub import dest, payload
    np = "one fewer revival counter"
    own = payload.parse_counters(np, (0, len(np))).unmodelled
    text = "onto the battlefield with %s on it" % np
    r = dest.parse_destination(text, (0, len(text)), lemma="return")
    assert r.value is None
    assert (r.unmodelled.stage, r.unmodelled.detail) == (own.stage, own.detail)
    assert r.unmodelled.lemma == "return"


def test_a_typed_counter_phrase_that_is_no_entry_count_is_the_destinations_refusal():
    from engine.effect_grammar.sub import dest
    text = "onto the battlefield with any number of +1/+1 counters on it"
    r = dest.parse_destination(text, (0, len(text)))
    assert r.value is None
    assert r.unmodelled.detail == "dest.entry_counters"


def _refused_filter_set():
    from engine.effect_grammar.sub import filter
    u = filter.parse_filter("blorp you control").unmodelled
    assert u.detail == "filter.unparsed:blorp"
    return u


@pytest.mark.parametrize("call", [
    lambda: __import__("engine.effect_grammar.sub.quantity", fromlist=["x"]
                       ).parse_quantity("the number of blorp you control",
                                        lemma="draw"),
    lambda: __import__("engine.effect_grammar.sub.amount", fromlist=["x"]
                       ).parse_scaler("for each blorp you control", lemma="draw"),
    lambda: __import__("engine.effect_grammar.sub.participant", fromlist=["x"]
                       ).parse_participant("each blorp you control", lemma="draw"),
    lambda: __import__("engine.effect_grammar.sub.condition", fromlist=["x"]
                       ).parse_condition("if you control three or more blorp you control",
                                         lemma="draw"),
])
def test_a_refusal_from_a_callee_leaf_reaches_the_census_unchanged(call):
    """Refusal propagation (the contract's one rule): a caller passes the
    deepest leaf's stage and '<leaf>.<code>:<param>' through, stamped with
    its own lemma, so one root cause is one census bucket whatever the
    caller, and the refused token survives."""
    own = _refused_filter_set()
    u = call().unmodelled
    assert (u.stage, u.detail) == (own.stage, own.detail), u
    assert u.lemma == "draw"


@pytest.mark.parametrize("duration_phrase", [
    "until end of turn", "this turn", "until your next turn",
    "for as long as ~ remains on the battlefield", "this combat",
    "for the rest of the game", "during your next untap step"])
def test_every_payload_phrase_ends_where_a_printed_duration_begins(duration_phrase):
    """The payload leaf reads its duration boundary from the duration leaf:
    a type change, a prohibition and a shared A19 tail all stop at the same
    printed durations."""
    from engine.effect_grammar.sub import duration, payload
    assert duration.parse_duration("x " + duration_phrase) is not None
    for lemma, head in (("become", "becomes a 4/4 elemental creature"),
                        ("can't", "can't block"),
                        ("gain", "gains your choice of flying or first strike")):
        text = "%s %s" % (head, duration_phrase)
        r = payload.parse_payload(_Entry(Verb.CONTINUOUS, lemma), text,
                                  (0, len(text)), None)
        assert r.value is not None or r.alternatives, (text, r)
        assert r.rest_text(text) == duration_phrase, (text, r)


@pytest.mark.parametrize("host", [
    "that much damage plus 2 instead.",
    "it deals that much damage plus 1 to that permanent or player instead.",
    "two damage plus 1 to any target"])
def test_a_slots_span_and_rest_never_overlap(host):
    """span is the consumed phrase and rest the unconsumed text: an operator
    printed after the counted noun is consumed outside the span (pending
    'operator'), and the noun stays rest for the caller's leaf."""
    from engine.effect_grammar.sub import amount
    a = host.index("that much") if "that much" in host else 0
    r = amount.parse_amount(host, (a, len(host)), lemma="deal")
    assert r.value is not None, r
    s, e = r.span
    for x, y in r.rest_spans:
        assert y <= s or x >= e, (r.span, r.rest_spans)
    assert "damage" in [host[x:y] for x, y in r.rest_spans]
    assert [k for k, _ in r.pending] == ["operator"]


# ── One count-word table ────────────────────────────────────────────────

@pytest.mark.parametrize("word,n", [("ten", 10), ("eleven", 11),
                                    ("thirteen", 13), ("twenty", 20),
                                    ("twenty-one", 21)])
def test_a_printed_count_word_is_typed_in_every_slot_that_counts(word, n):
    """One count table: a count word typed in a damage slot is typed in a
    counter, token, entry-counter, filter, participant and target slot."""
    from engine.effect_grammar import sub
    from engine.effect_grammar.sub import (amount, dest, filter, participant,
                                           payload, target)
    lit = Amount(AmountKind.LITERAL, n=n)
    assert sub.NUMBER_WORDS[word] == n
    assert amount.NUMBER_WORDS is sub.NUMBER_WORDS
    assert amount.parse_amount("%s damage" % word, lemma="deal").value == lit
    np = "%s +1/+1 counters" % word
    c = payload.parse_counters(np, (0, len(np)))
    assert c.value is not None and len(c.value.kinds) == n, c
    np = "%s tapped 2/2 black zombie creature tokens" % word
    t = payload.parse_token(np, (0, len(np)))
    assert isinstance(t.value, TokenSpec) and t.amount == lit, t
    text = "onto the battlefield with %s +1/+1 counters on it" % word
    d = dest.parse_destination(text, (0, len(text)))
    assert d.value == Destination("battlefield",
                                  entry_counters=(("+1/+1", lit),)), d
    f = filter.parse_filter("%s creatures you control" % word)
    assert f.value is not None and f.amount == lit, f
    text = "up to %s target creatures" % word
    ten = participant.parse_participant("up to ten target creatures")
    p = participant.parse_participant(text)
    assert (p.value, p.unmodelled) == (ten.value, ten.unmodelled), p
    # The target leaf reads the count from the same table. Above ten the
    # target solver (the TargetRequirement owner, unchanged in E0) carries
    # no such count, so the slot is the typed TARGET_COUNT refusal naming
    # the word -- never target.unparsed.
    host = "destroy up to %s target creatures." % word
    r = target.parse_target(host, (host.index("up"), len(host) - 1))
    if n <= 10:
        assert r.value.requirements[0].count_max == n, r
        assert r.amount == Amount(AmountKind.UP_TO, n=n), r
    else:
        assert r.unmodelled.detail == "target.count_unread:" + word, r


# ── One keyword spelling ─────────────────────────────────────────────────

@pytest.mark.parametrize("printed,typed", [
    ("flying", ("flying", None)),
    ("first strike", ("first_strike", None)),
    ("islandwalk", ("landwalk", "island")),
    ("nonbasic landwalk", ("landwalk", "nonbasic land")),
    ("swampcycling", ("typecycling", "swamp")),
    ("affinity for artifacts", ("affinity", "artifacts")),
])
def test_a_keyword_is_one_typed_value_wherever_it_is_printed(printed, typed):
    """A keyword printed on a keyword line, granted by a continuous
    effect, carried by a token, named by a filter qualifier, a target's
    tail or a condition is the same typed value: the keywords leaf's CR 702
    name, variants folded onto their family with the variant as the param,
    in the `cards.Keyword` value spelling (F5 / A22)."""
    from engine.effect_grammar import keywords
    from engine.effect_grammar.sub import condition, filter, payload, target
    assert keywords.typed_keyword(printed) == typed
    key = keywords.keyword_key(*typed)
    costed = typed[0] == "typecycling"   # its items and grants print a cost
    line = keywords.parse_keyword_line(printed + (" {2}" if costed else ""))
    (spec,) = line.value
    assert (spec.name, spec.param) == typed, line
    if not costed:
        text = "gains %s until end of turn" % printed
        g = payload.parse_payload(_Entry(Verb.CONTINUOUS, "gain"), text,
                                  (0, len(text)), None)
        assert g.value.get("keywords") == (typed,), g
        np = "a 1/1 white spirit creature token with %s" % printed
        t = payload.parse_token(np, (0, len(np)))
        assert t.value.keywords == (typed,), t
    f = filter.parse_filter("creatures with %s" % printed)
    assert f.value.with_keywords == frozenset({key}), f
    host = "destroy target creature with %s." % printed
    r = target.parse_target(host, (host.index("target"), len(host) - 1))
    assert r.value.residue == ("target.keyword:" + key,), r
    c = condition.parse_condition("if it has %s" % printed)
    assert c.value.filter.with_keywords == frozenset({key}), c


def test_every_cards_keyword_value_is_a_typed_keyword_spelling():
    from engine.cards import Keyword
    from engine.effect_grammar import keywords
    for k in Keyword:
        assert keywords.typed_keyword(k.value.replace("_", " ")) == (k.value, None)


# ── One possessive vocabulary ─────────────────────────────────────────────

def _possessives():
    from engine.effect_grammar.sub import POSSESSIVES
    return sorted(POSSESSIVES)


@pytest.mark.parametrize("poss", _possessives())
def test_a_zone_possessive_is_read_by_every_leaf_that_reads_a_zone(poss):
    """One possessive vocabulary (`sub.POSSESSIVES`): a possessive the
    destination leaf accepts on a zone noun, the filter leaf accepts on the
    same zone noun, with the same player value `possessive_player` gives."""
    from engine.effect_grammar.sub import dest, filter, possessive_player
    text = "into %s graveyard" % poss
    d = dest.parse_destination(text, (0, len(text)))
    assert d.value == Destination("graveyard"), d
    text = "on top of %s library" % poss
    d = dest.parse_destination(text, (0, len(text)))
    assert d.value == Destination("library", position="top"), d
    f = filter.parse_filter("creature card from %s graveyard" % poss)
    assert f.value is not None, f
    value, anaphor = possessive_player(poss)
    assert f.value.owner == value, f
    assert (("owner", anaphor) in f.pending) == (anaphor is not None), f


def test_under_a_possessives_control_reads_the_one_vocabulary():
    from engine.effect_grammar.sub import quantity
    for poss in ("your", "an opponent's", "its owner's", "~'s owner's",
                 "target player's"):
        text = "the number of creatures that died under %s control this turn" % poss
        assert quantity.parse_quantity(text).value is not None, poss


# ── Caches, clear_caches and import edges ──────────────────────────────

def test_every_leaf_cache_is_bounded_and_cleared_by_the_package():
    import engine.effect_grammar as grammar
    from engine.effect_grammar import sub
    caches = []
    for name, leaf in _leaves().items():
        assert callable(getattr(leaf, "clear_caches", None)), name
        for attr in vars(leaf).values():
            if callable(attr) and hasattr(attr, "cache_info") and \
                    getattr(attr, "__module__", "") == leaf.__name__:
                assert attr.cache_info().maxsize == sub.CACHE_SIZE, (name, attr)
                caches.append(attr)
    assert caches
    leaves = _leaves()
    leaves["dest"].parse_destination("onto the battlefield tapped", (0, 27))
    leaves["dest"].source_zones("target creature")
    leaves["duration"].parse_duration("gets +1/+1 until end of turn")
    leaves["payload"].parse_payload(_Entry(Verb.CONTINUOUS, "get"),
                                    "gets +1/+1", (0, 10), None)
    assert any(c.cache_info().currsize for c in caches)
    grammar.clear_caches()
    assert all(c.cache_info().currsize == 0 for c in caches)


def test_the_sub_package_clears_only_the_sub_grammars(monkeypatch):
    """Layering: the contract module clears the sub-grammars; the package
    entry point clears them and the leaves beside them."""
    import engine.effect_grammar as grammar
    from engine.effect_grammar import keywords, lexicon, normalize, sub
    called = []
    for leaf in (normalize, keywords, lexicon):
        monkeypatch.setattr(leaf, "clear_caches",
                            lambda leaf=leaf: called.append(leaf.LEAF))
    sub.clear_caches()
    assert called == []
    grammar.clear_caches()
    assert sorted(called) == ["keywords", "lexicon", "normalize"]


def _grammar_modules():
    """Every module of the engine.effect_grammar package, sub-grammars and
    the non-sub leaves (keywords, normalize, lexicon) alike."""
    import importlib
    import pkgutil

    import engine.effect_grammar as pkg
    out = [pkg]
    for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        out.append(importlib.import_module(info.name))
    return out


def test_one_package_entry_point_clears_every_grammar_cache(monkeypatch):
    """The leaf contract: the load driver calls one clear_caches once the
    grammar pass finishes, so every memo cache of every module under
    engine/effect_grammar -- not only the sub-grammars -- is cleared by
    engine.effect_grammar.clear_caches."""
    import engine.effect_grammar as grammar
    cleared, caches = set(), []
    for mod in _grammar_modules():
        for attr_name, attr in vars(mod).items():
            if callable(attr) and hasattr(attr, "cache_info") and \
                    getattr(attr, "__module__", "") == mod.__name__:
                key = (mod.__name__, attr_name)
                caches.append(key)
                monkeypatch.setattr(attr, "cache_clear",
                                    lambda key=key: cleared.add(key),
                                    raising=False)
    assert ("engine.effect_grammar.keywords", "_line_rel") in caches
    grammar.clear_caches()
    assert set(caches) - cleared == set()


GRAMMAR = SUB.parent


def _sub_imports(path: Path):
    """The grammar leaves a module imports: sub-grammars and the leaves
    beside them (keywords, lexicon, normalize)."""
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("engine.effect_grammar.sub."):
                out.add(node.module.rsplit(".", 1)[1])
            elif node.module == "engine.effect_grammar.sub":
                out.update(a.name for a in node.names
                           if (SUB / (a.name + ".py")).exists())
            elif node.module.startswith("engine.effect_grammar.") and \
                    node.module.count(".") == 2:
                out.add(node.module.rsplit(".", 1)[1])
            elif node.module == "engine.effect_grammar":
                out.update(a.name for a in node.names
                           if (GRAMMAR / (a.name + ".py")).exists())
    return out


def test_a_leaf_imports_another_leaf_only_along_a_declared_edge():
    from engine.effect_grammar.sub import LEAF_EDGES
    for name in _leaves():
        assert _sub_imports(_leaf_path(name)) == set(LEAF_EDGES[name]), name


def test_no_leaf_re_normalises_l0_output():
    """L0 unifies apostrophes and rewrites 'this <noun>' to ~ (the leaf
    contract); a leaf that re-normalises would accept input the others
    refuse."""
    for name in _leaves():
        if name == "normalize":
            continue                    # L0 itself
        src = _leaf_path(name).read_text()
        assert "’" not in src, name
        assert ".lower()" not in src, name
        assert not re.search(r"this \(\?:creature", src), name
