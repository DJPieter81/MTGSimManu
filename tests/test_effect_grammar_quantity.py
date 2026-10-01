"""The quantity sub-grammar (design doc 2026-09-29, section 6 "Quantity";
A16, A27; E0 step 11).

A quantity is the number an amount counts or reads: "the number of
creatures you control", the counted object of "for each <Q>", "its power",
"your devotion to black". It is a closed table over L0 output that runs at
load, never at resolution, under the one leaf contract
(`engine.effect_grammar.sub`). These tests pin the rules:

* a count of objects is COUNT / CARDS_IN over the filter leaf's CardFilter,
  and "for each <Q>" counts the same thing as "the number of <Q>";
* a count of a filtered result ("each nonland card discarded this way") is
  RESULT_SIZE, bound by the linker;
* a characteristic of a referenced object reads the reference, and a
  source that left as part of the cost reads last-known information
  (A27, CR 608.2h);
* "this turn" inside a quantity is history, consumed by the quantity, never
  a duration;
* an unknown phrase is UNMODELLED(QUANTITY) over the whole slot, never zero.

Synthetic phrases only; no card names.
"""
from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

import pytest

from engine.effect_spec import (CardFilter, QuantityKind, Ref, RefKind,
                                Stage)
from engine.effect_grammar.sub import quantity as Q

REPO = Path(__file__).resolve().parent.parent
LEAF_PATH = REPO / "engine" / "effect_grammar" / "sub" / "quantity.py"


def _q(text, **kw):
    return Q.parse_quantity(text, (0, len(text)), lemma="deal", **kw)


def _filter(r):
    return r.value.filter.as_tuple()


# ── Counts of objects ──────────────────────────────────────────────────

def test_the_number_of_and_for_each_count_the_same_filtered_set():
    a = _q("the number of creatures you control")
    b = _q("creature you control")
    for r in (a, b):
        assert r.value.kind is QuantityKind.COUNT
        assert _filter(r) == (("types", ("creature",)), ("controller", "you"))
        assert r.unmodelled is None and r.rest_spans == ()
    assert a.span == (0, len(a.value.raw)) and a.value.raw.startswith("the number of")


def test_a_count_of_cards_in_a_hidden_or_public_zone_is_cards_in_its_owner():
    r = _q("the number of cards in your hand")
    assert r.value.kind is QuantityKind.CARDS_IN
    assert r.value.filter.zone == "hand" and r.value.player == "you"
    r = _q("creature card in your graveyard")
    assert r.value.kind is QuantityKind.CARDS_IN
    assert r.value.filter.zone == "graveyard"
    assert r.value.filter.types == frozenset({"creature"})
    # An anaphoric owner is left for the linker, never guessed.
    r = _q("the number of cards in its controller's graveyard")
    assert r.value.kind is QuantityKind.CARDS_IN
    assert ("owner", "its controller's") in r.pending


def test_a_count_of_a_filtered_result_is_a_result_size_quantity():
    """Section 6: RESULT_SIZE(ref, filter) counts a filtered result of an
    earlier spec of the ability; the linker binds the RESULT index."""
    r = _q("nonland card discarded this way")
    assert r.value.kind is QuantityKind.RESULT_SIZE
    assert r.value.ref == Ref(RefKind.RESULT)
    assert r.value.filter.not_types == frozenset({"land"})
    assert ("result", "discarded") in r.pending
    r = _q("the number of creatures exiled this way")
    assert r.value.kind is QuantityKind.RESULT_SIZE
    assert r.value.filter.types == frozenset({"creature"})


def test_a_count_of_players_is_a_count_over_a_player_set():
    for text, who in (("opponent", "opponents"), ("the number of opponents you have", "opponents"),
                      ("player", "any")):
        r = _q(text)
        assert r.value.kind is QuantityKind.COUNT
        assert r.value.filter is None and r.value.player == who, text


# ── Characteristics of a referenced object ─────────────────────────────

@pytest.mark.parametrize("text, kind", [
    ("~'s power", QuantityKind.POWER),
    ("the toughness of ~", QuantityKind.TOUGHNESS),
    ("~'s mana value", QuantityKind.MANA_VALUE),
])
def test_a_characteristic_of_the_source_reads_the_source(text, kind):
    r = _q(text)
    assert r.value.kind is kind
    assert r.value.ref == Ref(RefKind.SELF)
    assert r.pending == ()


@pytest.mark.parametrize("text, kind, ref_text", [
    ("its power", QuantityKind.POWER, "its"),
    ("that creature's toughness", QuantityKind.TOUGHNESS, "that creature"),
    ("the sacrificed creature's power", QuantityKind.POWER, "the sacrificed creature"),
    ("the mana value of the exiled card", QuantityKind.MANA_VALUE, "the exiled card"),
    ("that spell's mana value", QuantityKind.MANA_VALUE, "that spell"),
    ("the power of target creature you control", QuantityKind.POWER,
     "target creature you control"),
])
def test_a_characteristic_of_an_anaphor_is_left_for_the_linker(text, kind, ref_text):
    r = _q(text)
    assert r.value.kind is kind
    assert r.value.ref is None
    assert ("ref", ref_text) in r.pending


def test_a_characteristic_of_the_attached_object_reads_the_attachment():
    r = _q("enchanted creature's power")
    assert r.value == r.value.__class__(
        QuantityKind.POWER, ref=Ref(RefKind.ATTACHED, noun="creature"),
        stat="power", raw="enchanted creature's power")


def test_a_cost_sacrificed_referent_is_marked_last_known_information():
    """CR 608.2h: the sacrificed object is gone; its characteristics are
    read as it last existed."""
    assert Q.LKI in _q("the sacrificed creature's power").flags
    assert Q.LKI not in _q("that creature's power").flags


@pytest.mark.parametrize("text, kind", [
    ("the number of charge counters on ~", QuantityKind.COUNTERS_ON),
    ("~'s power", QuantityKind.POWER),
    ("the number of oil counters on ~", QuantityKind.COUNTERS_ON),
])
def test_a_quantity_on_a_source_that_left_as_a_cost_reads_last_known_information(text, kind):
    """A27, CR 608.2h: when the source was sacrificed as part of the cost,
    its counters and characteristics are its last-known information. The
    caller knows the cost; the leaf marks the SELF reference."""
    left = _q(text, source_left=True)
    assert left.value.kind is kind
    assert left.value.ref == Ref(RefKind.SELF, lki=True)
    stayed = _q(text)
    assert stayed.value.ref == Ref(RefKind.SELF, lki=False)


# ── Counters ───────────────────────────────────────────────────────────

def test_counters_on_an_object_read_the_one_counter_kind_vocabulary():
    a = _q("the number of +1/+1 counters on ~")
    b = _q("+1/+1 counter on ~")
    for r in (a, b):
        assert r.value.kind is QuantityKind.COUNTERS_ON
        assert r.value.counter_kind == "+1/+1"
        assert r.value.ref == Ref(RefKind.SELF)
    # "coin" is a counter kind (CR 122.1); only a coin-flip tally is refused.
    for text in ("coin counter on ~", "the number of coin counters on ~"):
        r = _q(text)
        assert r.value.kind is QuantityKind.COUNTERS_ON, text
        assert r.value.counter_kind == "coin"
    r = _q("counter on it")
    assert r.value.kind is QuantityKind.COUNTERS_ON
    assert r.value.counter_kind is None and ("ref", "it") in r.pending


def test_counters_on_a_set_of_objects_count_over_its_filter():
    r = _q("+1/+1 counter on other creatures you control")
    assert r.value.kind is QuantityKind.COUNTERS_ON
    assert r.value.ref is None
    assert r.value.filter.other and r.value.filter.controller == "you"


# ── The other closed kinds ─────────────────────────────────────────────

def test_devotion_names_its_colours_as_mana_letters():
    """CR 700.5."""
    r = _q("your devotion to black")
    assert (r.value.kind, r.value.stat, r.value.player) == (
        QuantityKind.DEVOTION, "B", "you")
    r = _q("your devotion to black and red")
    assert r.value.stat == "BR"


def test_basic_land_types_and_card_types_count_over_their_filter():
    r = _q("the number of basic land types among lands you control")
    assert r.value.kind is QuantityKind.BASIC_LAND_TYPES
    assert r.value.filter.types == frozenset({"land"})
    r = _q("card type among cards in your graveyard")
    assert r.value.kind is QuantityKind.CARD_TYPES_IN_GRAVEYARD
    assert r.value.filter.zone == "graveyard"
    # A card-type count outside a graveyard is not that kind.
    r = _q("the number of card types among other nonland permanents you control")
    assert r.value is None and r.unmodelled.detail == "quantity.card_types_zone"


def test_greatest_and_total_name_their_characteristic_and_set():
    r = _q("the greatest power among creatures you control")
    assert (r.value.kind, r.value.stat) == (QuantityKind.GREATEST, "power")
    assert r.value.filter.types == frozenset({"creature"})
    # "highest" is the same quantity as "greatest".
    h = _q("the highest power among creatures you control")
    assert h.value == dataclasses.replace(
        r.value, raw=h.value.raw,
        filter=dataclasses.replace(r.value.filter, raw=h.value.filter.raw))
    r = _q("the total power of creatures you control")
    assert (r.value.kind, r.value.stat) == (QuantityKind.TOTAL, "power")
    r = _q("their total power")
    assert (r.value.kind, r.value.stat) == (QuantityKind.TOTAL, "power")
    assert ("ref", "their") in r.pending


@pytest.mark.parametrize("text, kind", [
    ("the greatest power among creatures they control", QuantityKind.GREATEST),
    ("the greatest power among creatures you control", QuantityKind.GREATEST),
    ("the total power of creatures you control", QuantityKind.TOTAL),
    ("the number of +1/+1 counters on creatures you control",
     QuantityKind.COUNTERS_ON),
    ("the number of basic land types among lands you control",
     QuantityKind.BASIC_LAND_TYPES),
    ("the number of creatures you control", QuantityKind.COUNT),
])
def test_a_quantity_over_a_set_names_no_player_its_filter_holds_control(text, kind):
    """One encoding for every set-based kind: who controls the set is the
    filter's (or a pending anaphor's), and ``player`` is "any" -- never a
    default "you" the printed text does not say."""
    r = _q(text)
    assert r.value.kind is kind
    assert r.value.player == "any"


@pytest.mark.parametrize("text, kind", [
    ("the greatest mana value among cards discarded this way", QuantityKind.GREATEST),
    ("the total power of creatures exiled this way", QuantityKind.TOTAL),
    ("the number of +1/+1 counters on creatures exiled this way",
     QuantityKind.COUNTERS_ON),
    ("the number of creatures exiled this way", QuantityKind.RESULT_SIZE),
])
def test_a_quantity_over_a_filtered_result_carries_the_result_ref(text, kind):
    """Section 6: a set that is an earlier spec's result is bound by the
    linker through ``Ref(RESULT)`` whatever the kind reads of it."""
    r = _q(text)
    assert r.value.kind is kind
    assert r.value.ref == Ref(RefKind.RESULT)
    assert any(k == "result" for k, _ in r.pending)
    assert Q.LKI not in r.flags


@pytest.mark.parametrize("text", [
    "the total power of creatures sacrificed this way",
    "the number of +1/+1 counters on creatures sacrificed this way",
    "the greatest power among creatures sacrificed this way",
    "the number of creatures sacrificed this way",
])
def test_a_sacrificed_result_set_is_marked_last_known_information(text):
    """CR 608.2h: objects sacrificed as part of the cost or effect are gone;
    the quantity reads them as they last existed, plural as singular."""
    assert Q.LKI in _q(text).flags


def test_colors_spent_and_times_kicked_read_the_cast():
    """CR 601.2h (colors spent), CR 702.33 (kicked)."""
    r = _q("the number of colors of mana spent to cast ~")
    assert r.value.kind is QuantityKind.COLORS_SPENT
    assert r.value.ref == Ref(RefKind.SELF)
    r = _q("time it was kicked")
    assert r.value.kind is QuantityKind.TIMES_KICKED and ("ref", "it") in r.pending
    r = _q("the number of times ~ was kicked")
    assert r.value.kind is QuantityKind.TIMES_KICKED and r.value.ref == Ref(RefKind.SELF)


def test_a_life_total_names_its_player():
    r = _q("your life total")
    assert (r.value.kind, r.value.player) == (QuantityKind.LIFE_TOTAL, "you")
    r = _q("that player's life total")
    assert r.value.kind is QuantityKind.LIFE_TOTAL
    assert ("player", "that player") in r.pending


# ── History ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, event, player", [
    ("the number of spells you've cast this turn", "cast", "you"),
    ("creature that died this turn", "died", "any"),
    ("the number of nontoken creatures that died this turn", "died", "any"),
    ("card you've drawn this turn", "drawn", "you"),
    ("card you've cycled or discarded this turn", "discarded", "you"),
    ("opponent who lost life this turn", "players_lost_life", "opponents"),
    ("the amount of life you gained this turn", "life_gained", "you"),
    ("1 life your opponents have lost this turn", "life_lost", "opponents"),
    ("token you control that entered this turn", "entered", "any"),
    # The printed actor is kept: "you" is never widened to any player.
    ("the number of times you descended this turn", "descended", "you"),
    ("time you've descended this turn", "descended", "you"),
])
def test_this_turn_inside_a_quantity_is_history_not_a_duration(text, event, player):
    """Section 6: "this turn" closing a quantity says what happened, so the
    quantity consumes it; it is never left over as a duration. Cycling's
    cost discards the card (CR 702.29a), so "cycled or discarded" is the
    discard tally."""
    from engine.effect_grammar.sub import duration
    r = _q(text)
    assert r.value.kind is QuantityKind.HISTORY
    assert (r.value.event, r.value.player) == (event, player)
    assert text[slice(*r.span)].endswith("this turn") and r.rest_spans == ()
    # The quantity's frame makes the phrase history for the duration leaf
    # too: neither leaf leaves it as a duration of the clause.
    assert duration.parse_duration("deal damage equal to " + text) is None
    assert r.value.event in Q.HISTORY_EVENTS


@pytest.mark.parametrize("a, b", [
    ("creature that died under your control this turn",
     "creature you control that died this turn"),
    ("creature that died under an opponent's control this turn",
     "creature an opponent controls that died this turn"),
    ("token that entered the battlefield under your control this turn",
     "token you control that entered this turn"),
])
def test_control_of_a_history_object_has_one_encoding_whatever_the_word_order(a, b):
    """Who controlled the object that died or entered is the filter's
    controller; ``player`` is the actor of the event (who cast, drew,
    discarded) and is "any" for an event no player performs."""
    ra, rb = _q(a), _q(b)
    va, vb = ra.value, rb.value
    assert va.kind is vb.kind is QuantityKind.HISTORY
    assert va.player == vb.player == "any"
    assert va.filter.as_tuple() == vb.filter.as_tuple()
    assert va.filter.controller != "any"
    assert ra.pending == rb.pending


def test_an_anaphoric_controller_of_a_history_object_is_left_for_the_linker():
    r = _q("creature that died under their control this turn")
    assert r.value.player == "any"
    assert ("controller", "their") in r.pending


@pytest.mark.parametrize("text", [
    "the number of spells you've cast this turn",
    "card you've drawn this turn",
    "card you've cycled or discarded this turn",
    "creature that died this turn",
    "token you control that entered this turn",
])
def test_a_history_filter_names_no_current_zone(text):
    """A tally counts past events: a spell cast this turn has resolved, a
    discarded card may since have been exiled. The filter describes the
    object at the event, so it carries no current zone (as a RESULT_SIZE
    filter does not)."""
    assert _q(text).value.filter.zone == ""


def test_a_history_filter_keeps_its_printed_qualifiers():
    r = _q("the number of nontoken creatures that died this turn")
    assert r.value.filter.types == frozenset({"creature"})
    assert r.value.filter.token is False
    r = _q("other spell that player has cast this turn")
    assert r.value.filter.other and ("player", "that player") in r.pending


# ── Refusals ───────────────────────────────────────────────────────────

_REFUSED_CASES = [
    ("the difference", "anaphoric_number"),
    ("that number", "anaphoric_number"),
    ("creature in your party", "party"),
    ("the number of colors among permanents you control", "colors_among"),
    ("the amount of mana spent to cast ~", "mana_spent"),
    ("your starting life total", "starting_life_total"),
    ("its mana cost", "mana_cost"),
    ("the greatest number of cards a player discarded this way", "greatest_number"),
    ("~'s power and toughness", "stat_pair"),
    ("of those creatures", "element_anaphor"),
    ("kind of counter on target permanent or player", "counter_kinds"),
    ("the amount of {e} paid this way", "result_amount"),
    ("the number of red mana symbols in the mana costs of permanents you control",
     "mana_symbols"),
    ("different power among creatures you control", "different_values"),
    ("blorp's power", "reference"),
    ("flip you win", "coin_flip"),
    ("poison counter your opponents have", "player_counters"),
    ("charge counter removed this way", "result_amount"),
    ("{g}{g} spent to cast ~", "mana_spent"),
    ("lands you control in your graveyard", "zone_controller"),
    ("", "empty"),
    ("two creatures you control", "determiner"),
    ("+1/+1 or -1/-1 counter on ~", "counter"),
    ("the lowest life total among players", "extremum"),
    ("the highest life total among players", "extremum"),
    ("the damage dealt to ~ this turn", "damage_amount"),
    ("the number of card types among other nonland permanents you control",
     "card_types_zone"),
    # Control printed twice ("you control" and "under your control").
    ("creature you control that died under your control this turn",
     "history_control"),
]


@pytest.mark.parametrize("text, code", _REFUSED_CASES)
def test_an_uncountable_quantity_makes_the_clause_unmodelled_never_zero(text, code):
    """Section 6: an unknown phrase is UNMODELLED(QUANTITY), never a zero
    or a broader count; the failure span is the whole slot."""
    host = "deal damage equal to %s." % text
    slot = (host.index("to ") + 3, len(host) - 1)
    r = Q.parse_quantity(host, slot, lemma="deal")
    assert r.value is None
    assert r.unmodelled.stage is Stage.QUANTITY
    assert r.unmodelled.lemma == "deal"
    assert r.unmodelled.detail.split(":")[0] == "quantity." + code
    assert r.unmodelled.detail.split(".")[1].split(":")[0] in Q.DETAIL_CODES
    trimmed = host[slot[0]:slot[1]].strip()
    assert host[slice(*r.span)] == trimmed


def test_every_closed_detail_code_is_one_the_leaf_emits():
    """The census buckets are the leaf's refusals: a closed code no phrase
    reaches would claim a bucket that is always empty."""
    assert {code for _, code in _REFUSED_CASES} == set(Q.DETAIL_CODES)


@pytest.mark.parametrize("text", [
    "land your opponents control that could produce {c}", "color pair"])
def test_a_counted_phrase_the_filter_refuses_carries_the_filters_refusal(text):
    """Refusal propagation: the filter leaf's stage and detail (with the
    refused token) reach the census unchanged, stamped with the lemma."""
    from engine.effect_grammar.sub import filter as F
    r = _q(text)
    assert r.value is None
    own = F.parse_filter(text).unmodelled
    assert (r.unmodelled.stage, r.unmodelled.detail) == (own.stage, own.detail)
    assert re.match(r"^filter\.[a-z_]+(:\S+)?$", r.unmodelled.detail)


# ── Spans: the consumed phrase and the rest ────────────────────────────

@pytest.mark.parametrize("slot_text, consumed, rest", [
    ("its power to any target", "its power", "to any target"),
    ("the number of creatures you control plus the number of planeswalkers you control",
     "the number of creatures you control",
     "plus the number of planeswalkers you control"),
    ("its toughness rather than its power", "its toughness", "rather than its power"),
    ("~'s power until end of turn", "~'s power", "until end of turn"),
    # CR 108.4a: a card in a hand has no controller, so "lands you control
    # from your hand" is no set; the count ends before "from".
    ("the number of lands you control from your hand onto the battlefield",
     "the number of lands you control", "from your hand onto the battlefield"),
])
def test_a_quantity_hands_the_unconsumed_tail_on_as_host_spans(slot_text, consumed, rest):
    host = "it deals damage equal to " + slot_text + "."
    slot = (host.index(slot_text), host.index(slot_text) + len(slot_text))
    r = Q.parse_quantity(host, slot, lemma="deal")
    assert r.value is not None, r
    assert host[slice(*r.span)] == consumed == r.value.raw
    assert r.rest_text(host) == rest
    assert all(slot[0] <= a <= b <= slot[1] for a, b in r.rest_spans)


def test_a_full_slot_is_consumed_before_any_tail_is_cut():
    """A filter that contains a tail word ("instant and sorcery") is read
    whole: the cut is tried only when the full slot is no quantity."""
    r = _q("instant and sorcery card in your graveyard")
    assert r.value.filter.types == frozenset({"instant", "sorcery"})
    assert r.rest_spans == ()


def test_trailing_punctuation_is_structure_not_rest():
    host = "draw a card for each artifact you control."
    a = host.index("artifact")
    r = Q.parse_quantity(host, (a, len(host)))
    assert host[slice(*r.span)] == "artifact you control"
    assert r.rest_spans == ()


# ── The leaf contract ──────────────────────────────────────────────────

def test_the_quantity_leaf_imports_only_its_declared_edges():
    from engine.effect_grammar.sub import LEAF_EDGES
    out = set()
    for node in ast.walk(ast.parse(LEAF_PATH.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("engine.effect_grammar.sub."):
                out.add(node.module.rsplit(".", 1)[1])
            elif node.module == "engine.effect_grammar.sub":
                out.update(a.name for a in node.names
                           if (LEAF_PATH.parent / (a.name + ".py")).exists())
    assert out == set(LEAF_EDGES["quantity"]) == {"filter", "payload", "duration"}


def _acyclic(edges):
    state = {}

    def visit(n):
        if state.get(n) == 1:
            return False
        if state.get(n) == 2:
            return True
        state[n] = 1
        ok = all(visit(m) for m in edges.get(n, ()))
        state[n] = 2
        return ok
    return all(visit(n) for n in edges)


def test_the_leaf_edge_graph_stays_acyclic():
    from engine.effect_grammar.sub import LEAF_EDGES
    assert _acyclic(LEAF_EDGES)


def test_the_quantity_caches_are_bounded_and_cleared_by_the_package():
    from engine.effect_grammar import sub
    import engine.effect_grammar as grammar
    caches = [a for a in vars(Q).values()
              if callable(a) and hasattr(a, "cache_info")
              and getattr(a, "__module__", "") == Q.__name__]
    assert caches
    assert all(c.cache_info().maxsize == sub.CACHE_SIZE for c in caches)
    _q("the number of creatures you control")
    assert any(c.cache_info().currsize for c in caches)
    sub.clear_caches()
    assert all(c.cache_info().currsize == 0 for c in caches)
    _q("the number of creatures you control")
    grammar.clear_caches()
    assert all(c.cache_info().currsize == 0 for c in caches)


def test_the_quantity_leaf_does_not_re_normalise_l0_output():
    src = LEAF_PATH.read_text()
    assert "’" not in src and ".lower()" not in src
    assert Q.LEAF == "quantity"
