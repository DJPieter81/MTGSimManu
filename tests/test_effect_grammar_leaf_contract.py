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
    from engine.effect_grammar.sub import dest, duration, payload
    return {"duration": duration, "dest": dest, "payload": payload}


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
    host = "put it into your hand blorp. put eleven +1/+1 counters on it."
    slot = (host.index("into"), host.index("."))
    d = dest.parse_destination(host, slot, lemma="put")
    assert d.value is None and d.span == slot
    slot = (host.index("eleven"), len(host) - 1)
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
    text = "eleven +1/+1 counters on it"
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
                                         "eleven +1/+1 counters", (0, 21), None),
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


def test_an_entry_counter_phrase_the_counter_parser_refuses_is_unmodelled():
    from engine.effect_grammar.sub import dest
    text = "onto the battlefield with one fewer revival counter on it"
    r = dest.parse_destination(text, (0, len(text)))
    assert r.value is None
    assert r.unmodelled.detail == "destination.entry_counters"


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


# ── Caches, clear_caches and import edges ──────────────────────────────

def test_every_leaf_cache_is_bounded_and_cleared_by_the_package():
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
    sub.clear_caches()
    assert all(c.cache_info().currsize == 0 for c in caches)


def _sub_imports(path: Path):
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("engine.effect_grammar.sub."):
                out.add(node.module.rsplit(".", 1)[1])
            elif node.module == "engine.effect_grammar.sub":
                out.update(a.name for a in node.names
                           if (SUB / (a.name + ".py")).exists())
    return out


def test_a_leaf_imports_another_leaf_only_along_a_declared_edge():
    from engine.effect_grammar.sub import LEAF_EDGES
    for name in _leaves():
        assert _sub_imports(SUB / (name + ".py")) == set(LEAF_EDGES[name]), name


def test_no_leaf_re_normalises_l0_output():
    """L0 unifies apostrophes and rewrites 'this <noun>' to ~ (the leaf
    contract); a leaf that re-normalises would accept input the others
    refuse."""
    for name in _leaves():
        src = (SUB / (name + ".py")).read_text()
        assert "’" not in src, name
        assert ".lower()" not in src, name
        assert not re.search(r"this \(\?:creature", src), name
