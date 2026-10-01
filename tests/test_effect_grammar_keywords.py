"""The keyword tables of the clause grammar (design doc 2026-09-29, G12,
A1, A8, M3; E0 step 9).

`engine/effect_grammar/keywords.py` holds:

* the CR 702 keyword-ability table L1 uses to recognise a KEYWORD host (A1):
  a paragraph that is a list of keyword abilities in any A1 form -- keyword,
  keyword N, keyword {cost}, keyword-<non-mana cost> (verbs allowed), a
  multi-word keyword, a noun-parameter keyword;
* the M3 filter: a face's MTGJSON ``keywords`` field intersected with that
  table, so CR 701 keyword actions (scry, mill, investigate) and ability
  words (CR 207.2c) never make an effect paragraph a keyword line;
* the A8 cost rule: "the <keyword> cost is equal to its mana cost";
* the CR 701 / 702 / 111.10 rules-English expansions the full grammar
  parses into ``KeywordAction.expansion`` and predefined-token abilities.

Synthetic L0 text only (lowercased, dashes unified, self-forms ``~``); no
card names. The registered-deck witnesses live in the pool test.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from engine.effect_spec import KeywordSpec, Stage

REPO = Path(__file__).resolve().parent.parent
LEAF_PATH = REPO / "engine" / "effect_grammar" / "keywords.py"


def _kw():
    from engine.effect_grammar import keywords
    return keywords


def _line(text, span=None, **kw):
    return _kw().parse_keyword_line(text, span, **kw)


def _items(r):
    return [(s.name, s.n, s.param, s.cost) for s in r.value]


def _snap(spec):
    return dict(spec.cost_snapshot.items)


# ── A1: what a keyword line is ─────────────────────────────────────────

@pytest.mark.parametrize("text,items", [
    ("flying", [("flying", None, None, None)]),
    ("flying, haste", [("flying", None, None, None), ("haste", None, None, None)]),
    ("flash; convoke", [("flash", None, None, None), ("convoke", None, None, None)]),
    ("first strike, double strike, trample",
     [("first strike", None, None, None), ("double strike", None, None, None),
      ("trample", None, None, None)]),
    ("split second", [("split second", None, None, None)]),
    ("start your engines!", [("start your engines!", None, None, None)]),
])
def test_a_list_of_keyword_abilities_types_every_item(text, items):
    r = _line(text)
    assert r is not None and r.unmodelled is None
    assert _items(r) == items
    assert r.span == (0, len(text)) and r.rest_spans == ()


@pytest.mark.parametrize("text,item", [
    ("crew 3", ("crew", 3, None, None)),
    ("bushido 2", ("bushido", 2, None, None)),
    ("annihilator 6", ("annihilator", 6, None, None)),
    ("vanishing", ("vanishing", None, None, None)),         # CR 702.63b: no N
    ("vanishing 3", ("vanishing", 3, None, None)),
    ("bloodthirst x", ("bloodthirst", None, "x", None)),
])
def test_a_numeric_keyword_carries_its_n_and_x_is_a_parameter(text, item):
    (got,) = _items(_line(text))
    assert got == item


def test_a_numeric_keyword_without_its_number_is_refused_and_a_sentence_about_it_is_not_a_keyword_line():
    """A1: an item whose keyword has no number is a keyword line the table
    cannot type (UNMODELLED, never resolution text); a sentence that names
    the keyword's abilities is ability text."""
    r = _line("crew")
    assert r.value is None and r.unmodelled.detail == "keywords.unconsumed:crew"
    assert _line("crew abilities you activate cost {1} less to activate.") is None


@pytest.mark.parametrize("text,item,generic,colored", [
    ("flashback {2}{r}", ("flashback", None, None, "{2}{r}"), 2, ("red", 1)),
    ("kicker {w}", ("kicker", None, None, "{w}"), 0, ("white", 1)),
    ("cycling {2}", ("cycling", None, None, "{2}"), 2, None),
    ("equip {1}", ("equip", None, None, "{1}"), 1, None),
    ("ward {2}", ("ward", None, None, "{2}"), 2, None),
])
def test_a_mana_cost_keyword_types_its_cost_through_the_cost_owner(
        text, item, generic, colored):
    r = _line(text)
    (spec,) = r.value
    assert (spec.name, spec.n, spec.param, spec.cost) == item
    mana = dict(_snap(spec)["mana"])
    assert mana["generic"] == generic
    if colored:
        assert mana[colored[0]] == colored[1]
    assert _snap(spec)["unpayable"] == ()


@pytest.mark.parametrize("text,name,cost,unpayable,extra", [
    ("flashback-sacrifice a mountain.", "flashback", "sacrifice a mountain",
     ("sacrifice",), {}),
    ("escape-{3}{b}, exile five other cards from your graveyard.", "escape",
     "{3}{b}, exile five other cards from your graveyard", ("exile",),
     {"generic": 3, "black": 1}),
    ("evoke-exile a white card from your hand.", "evoke",
     "exile a white card from your hand", ("exile",), {}),
    ("cycling-pay 2 life.", "cycling", "pay 2 life", (), {}),
    ("ward-discard a card.", "ward", "discard a card", None, {}),
    ("kicker - sacrifice an artifact or creature.", "kicker",
     "sacrifice an artifact or creature", ("sacrifice",), {}),
])
def test_a_keyword_dash_non_mana_cost_is_one_cost_to_the_sentence_end(
        text, name, cost, unpayable, extra):
    """CR 702 em-dash costs may contain verbs and commas; the whole cost runs
    to the end of the sentence and is typed by `parse_activation_cost`."""
    r = _line(text)
    assert r is not None and r.unmodelled is None, r
    (spec,) = r.value
    assert (spec.name, spec.cost) == (name, cost)
    snap = _snap(spec)
    if unpayable is not None:
        assert snap["unpayable"] == unpayable
    mana = dict(snap["mana"])
    for field, n in extra.items():
        assert mana[field] == n
    if name == "cycling":
        assert snap["life"] == 2
    assert r.span == (0, len(text))


@pytest.mark.parametrize("text,item", [
    ("splice onto arcane {1}{r}", ("splice", None, "arcane", "{1}{r}")),
    ("splice onto instant or sorcery {2}{u}",
     ("splice", None, "instant or sorcery", "{2}{u}")),
    ("splice onto arcane-exile four cards from your graveyard.",
     ("splice", None, "arcane", "exile four cards from your graveyard")),
    ("gift a tapped fish", ("gift", None, "a tapped fish", None)),
    ("gift a card", ("gift", None, "a card", None)),
    ("enchant creature you control", ("enchant", None, "creature you control", None)),
    ("enchant artifact or creature", ("enchant", None, "artifact or creature", None)),
    ("enchant artifact, creature, or planeswalker",
     ("enchant", None, "artifact, creature, or planeswalker", None)),
    ("affinity for artifacts", ("affinity", None, "artifacts", None)),
    ("champion a goblin or shaman", ("champion", None, "a goblin or shaman", None)),
    ("craft with artifact {3}{b}", ("craft", None, "artifact", "{3}{b}")),
    ("emerge from artifact {5}{u}{u}", ("emerge", None, "artifact", "{5}{u}{u}")),
    ("devour artifact 1", ("devour", 1, "artifact", None)),
    ("modular-sunburst", ("modular", None, "sunburst", None)),
    ("prototype {1}{r} - 2/1", ("prototype", None, "2/1", "{1}{r}")),
    ("suspend 4-{u}", ("suspend", 4, None, "{u}")),
    ("awaken 3-{4}{w}", ("awaken", 3, None, "{4}{w}")),
])
def test_multi_word_and_noun_parameter_keywords_keep_their_parameter(text, item):
    r = _line(text)
    assert r is not None and r.unmodelled is None, r
    (got,) = _items(r)
    assert got == item


@pytest.mark.parametrize("text,item", [
    ("equip {2}", ("equip", None, None, "{2}")),
    ("equip legendary creature {3}", ("equip", None, "legendary creature", "{3}")),
    ("equip halfling {1}", ("equip", None, "halfling", "{1}")),
    ("equip creature token {1}", ("equip", None, "creature token", "{1}")),
    ("equip soldier {w}", ("equip", None, "soldier", "{w}")),
    ("equip planeswalker {1}", ("equip", None, "planeswalker", "{1}")),
])
def test_an_equip_quality_parameter_is_the_keywords_parameter(text, item):
    """CR 702.6e: 'equip [quality] {cost}' is equip whose parameter is the
    quality; the cost is typed the same as an unqualified equip's."""
    r = _line(text, candidates=frozenset({"equip"}))
    assert r is not None and r.unmodelled is None, r
    (spec,) = r.value
    assert (spec.name, spec.n, spec.param, spec.cost) == item
    assert _snap(spec)["unpayable"] == ()


@pytest.mark.parametrize("text", [
    "equip abilities you activate cost {1} less to activate.",
    "equip abilities you activate that target ~ cost {2} less to activate.",
    "equip costs you pay cost {1} less.",
])
def test_a_sentence_about_equip_abilities_is_not_an_equip_quality(text):
    assert _line(text, candidates=frozenset({"equip"})) is None


def test_trample_over_planeswalkers_is_trample_with_its_parameter():
    """CR 702.19c."""
    assert _items(_line("trample over planeswalkers",
                        candidates=frozenset({"trample"}))) == [
        ("trample", None, "planeswalkers", None)]
    assert _items(_line("trample")) == [("trample", None, None, None)]


@pytest.mark.parametrize("text,name", [
    ("kicker blorp", "kicker"),
    ("ward blorp", "ward"),
    ("swampcycling blorp", "typecycling"),
])
def test_a_face_keyword_whose_parameter_is_not_its_shape_is_refused_never_dropped(
        text, name):
    """A1: a paragraph that opens with one of the face's own keywords and is
    not a sentence is a keyword line; when the parameter is not the
    keyword's closed shape it is UNMODELLED, never None -- None would hand
    it to the later static and spell rules as resolution text."""
    r = _line(text, candidates=frozenset({name}))
    assert r.value is None and r.unmodelled.stage is Stage.STRUCTURE
    assert r.unmodelled.detail == "keywords.unconsumed:" + name.split()[0]
    assert r.span == (0, len(text))
    # Not the face's keyword: not this face's keyword line.
    assert _line(text, candidates=frozenset({"flying"})) is None


@pytest.mark.parametrize("text,item", [
    ("aura swap {2}{u}", ("aura swap", None, None, "{2}{u}")),
    ("transfigure {1}{b}{b}", ("transfigure", None, None, "{1}{b}{b}")),
    ("goblin offering", ("offering", None, "goblin", None)),
    ("moonfolk offering", ("offering", None, "moonfolk", None)),
])
def test_aura_swap_transfigure_and_offering_are_cr_702_keyword_lines(text, item):
    """CR 702.65 aura swap and 702.71 transfigure are costed keywords; CR
    702.48 '<subtype> offering' is one keyword whose parameter is the
    subtype (like typecycling and landwalk)."""
    kw = _kw()
    face = kw.keywords702(["Aura Swap", "Transfigure", "Offering"])
    assert face == frozenset({"aura swap", "transfigure", "offering"})
    r = _line(text, candidates=face)
    assert r is not None and r.unmodelled is None, r
    assert _items(r) == [item]


@pytest.mark.parametrize("text,params", [
    ("protection from red", ["red"]),
    ("protection from black and from red", ["black", "red"]),
    ("protection from vampires, from werewolves, and from zombies",
     ["vampires", "werewolves", "zombies"]),
    ("hexproof from monocolored", ["monocolored"]),
])
def test_each_protection_quality_is_its_own_keyword_instance(text, params):
    """CR 702.16g: protection from A and from B is two instances."""
    r = _line(text)
    head = text.split()[0]
    assert [(s.name, s.param) for s in r.value] == [(head, p) for p in params]


def test_hexproof_and_partner_are_keywords_with_or_without_a_parameter():
    assert _items(_line("hexproof")) == [("hexproof", None, None, None)]
    assert _items(_line("partner")) == [("partner", None, None, None)]
    assert _items(_line("partner with someone else")) == [
        ("partner", None, "someone else", None)]


@pytest.mark.parametrize("text,item", [
    ("swampcycling {2}", ("typecycling", None, "swamp", "{2}")),
    ("basic landcycling {1}{g}", ("typecycling", None, "basic land", "{1}{g}")),
    ("landcycling {2}", ("typecycling", None, "land", "{2}")),
    ("artifact landcycling {2}", ("typecycling", None, "artifact land", "{2}")),
    ("islandwalk", ("landwalk", None, "island", None)),
    ("nonbasic landwalk", ("landwalk", None, "nonbasic land", None)),
])
def test_typecycling_and_landwalk_are_one_keyword_with_a_type_parameter(text, item):
    """CR 702.29e / 702.14c: <type>cycling and <type>walk are variants of
    one keyword whose parameter is the type."""
    (got,) = _items(_line(text))
    assert got == item


def test_kicker_and_or_prints_two_kicker_costs():
    """CR 702.33c: 'kicker {a} and/or {b}' is two kicker costs."""
    r = _line("kicker {1}{w} and/or {2}{r}")
    assert _items(r) == [("kicker", None, None, "{1}{w}"),
                         ("kicker", None, None, "{2}{r}")]


def test_a_cost_choice_the_cost_owner_cannot_hold_is_unmodelled():
    r = _line("cumulative upkeep {g} or {w}")
    assert r.value is None
    assert r.unmodelled.stage is Stage.STRUCTURE
    assert r.unmodelled.detail == "keywords.cost_choice"


# ── What stays out of a keyword line ───────────────────────────────────

@pytest.mark.parametrize("text", [
    "equip abilities you activate cost {1} less to activate.",
    "flying creatures you control get +1/+1.",
    "enchanted creature gets +2/+2.",
    "equipped creature has flying.",
    "flashback costs you pay cost {1} less.",
    # A keyword followed by an activated ability is a labelled ability,
    # not a keyword list: the dash text holds the ability's colon.
    "kicker - {2}: draw a card.",
    "draw a card.",
])
def test_a_sentence_that_opens_with_a_keyword_word_is_not_a_keyword_line(text):
    assert _line(text) is None


@pytest.mark.parametrize("text", ["scry 2.", "investigate.", "mill 3.",
                                  "surveil 1.", "earthbend 2.",
                                  "proliferate."])
def test_keyword_actions_are_never_keyword_abilities(text):
    """M3 (CR 701 vs 702): a keyword action is an effect, so a paragraph
    that is one stays resolution text even when MTGJSON lists the action in
    the face's keywords."""
    kw = _kw()
    face = kw.keywords702(["Scry", "Investigate", "Mill", "Surveil",
                           "Earthbend", "Proliferate", "Flying"])
    assert face == frozenset({"flying"})
    assert _line(text) is None
    assert _line(text, candidates=face) is None


def test_the_face_keyword_set_is_mtgjson_keywords_intersected_with_cr_702():
    kw = _kw()
    assert kw.keywords702([
        "Flying", "Landfall", "Treasure", "Scry", "Swampwalk", "Landwalk",
        "Basic landcycling", "Typecycling", "Hexproof from", "Partner with",
        "Level Up", "Delirium", "Enchant", "Fire", "Tiered"]) == frozenset({
            "flying", "landwalk", "typecycling", "hexproof", "partner",
            "level up", "enchant", "tiered"})
    assert kw.keywords702([]) == frozenset()
    assert kw.canonical_keyword("Landfall") is None
    assert kw.canonical_keyword("Mountainwalk") == "landwalk"
    assert kw.canonical_keyword("Slivercycling") == "typecycling"
    assert kw.canonical_keyword("Cycling") == "cycling"


def test_a_face_keyword_set_gates_the_line_and_a_synthetic_face_uses_the_table():
    """A1/M3: candidates come from the face keywords; a template without
    keyword data (candidates None) reads the CR 702 table alone."""
    face = frozenset({"flash", "lifelink"})
    assert _line("flying", candidates=face) is None
    assert _items(_line("flying")) == [("flying", None, None, None)]
    r = _line("flash, flying", candidates=face)
    assert r.value is None
    assert r.unmodelled.detail == "keywords.face_keywords_disagree:flying"


def test_a_keyword_whose_parameter_runs_past_the_closed_forms_is_unmodelled():
    text = "protection from creatures, flying blorp"
    r = _line(text, lemma="")
    assert r.value is None and r.unmodelled.stage is Stage.STRUCTURE
    assert r.unmodelled.detail == "keywords.unconsumed"
    assert r.span == (0, len(text))


def test_an_unmodelled_line_carries_the_callers_lemma():
    r = _line("cumulative upkeep {g} or {w}", lemma="upkeep")
    assert r.unmodelled.lemma == "upkeep"
    assert _line("cumulative upkeep {g} or {w}").unmodelled.lemma == ""


# ── Following sentences and frames are the caller's ───────────────────

def test_a_sentence_after_the_keyword_list_is_handed_back_as_rest():
    text = "suspend x-{3}{r}. x can't be 0."
    r = _line(text)
    assert _items(r) == [("suspend", None, "x", "{3}{r}")]
    assert r.rest_text(text) == "x can't be 0."
    assert text[slice(*r.span)] == "suspend x-{3}{r}."
    text = "crew 1. activate only once each turn."
    r = _line(text)
    assert _items(r) == [("crew", 1, None, None)]
    assert r.rest_text(text) == "activate only once each turn."


def test_a_where_x_frame_after_an_x_keyword_is_left_to_the_amount_grammar():
    text = "mobilize x, where x is the number of creature cards in your graveyard."
    r = _line(text)
    assert _items(r) == [("mobilize", None, "x", None)]
    assert r.rest_text(text) == "where x is the number of creature cards in your graveyard."


def test_spans_index_the_host_and_a_slot_inside_it():
    host = "~ deals 1 damage to any target.\nflashback-sacrifice a mountain."
    a = host.index("flashback")
    r = _line(host, (a, len(host)))
    assert r.span == (a, len(host))
    (spec,) = r.value
    assert spec.cost == "sacrifice a mountain"
    assert _line(host, (0, a - 1)) is None


# ── A7: costs come from the printed span ───────────────────────────────

def test_a_cost_naming_the_card_itself_is_read_from_the_printed_span():
    """A7: `parse_activation_cost` recognises 'sacrifice this creature',
    not '~'. A caller with the L0 offset map passes `printed`; without it a
    self-form cost is refused rather than typed wrong."""
    text = "echo-sacrifice ~."
    r = _line(text)
    assert r.value is None and r.unmodelled.detail == "keywords.cost_self_form"
    printed = {(text.index("sacrifice"), len(text) - 1): "Sacrifice this creature"}
    r = _line(text, printed=lambda span: printed[span])
    (spec,) = r.value
    assert spec.cost == "sacrifice ~"
    assert _snap(spec)["sacrifice_self"] is True


# ── A8: the cost rule rider ────────────────────────────────────────────

@pytest.mark.parametrize("text,name", [
    ("the flashback cost is equal to its mana cost.", "flashback"),
    ("the flashback cost is equal to that card's mana cost.", "flashback"),
    ("its harmonize cost is equal to its mana cost.", "harmonize"),
    ("the embalm cost is equal to its mana cost", "embalm"),
])
def test_a_keyword_cost_equal_to_mana_cost_sentence_is_the_keywords_cost_rule(text, name):
    r = _kw().parse_cost_rule(text)
    assert r.value == KeywordSpec(name=name, cost_rule="mana_cost")
    assert r.span == (0, len(text))


def test_a_cost_rule_the_model_cannot_state_is_unmodelled_and_other_text_is_none():
    kw = _kw()
    text = "its foretell cost is equal to its mana cost reduced by {2}."
    r = kw.parse_cost_rule(text)
    assert r.value is None and r.unmodelled.detail == "keywords.cost_rule_modified"
    assert kw.parse_cost_rule("the blorp cost is equal to its mana cost.") is None
    assert kw.parse_cost_rule("draw a card.") is None


# ── G12: the rules-English expansions ──────────────────────────────────

def test_every_typed_keyword_action_and_predefined_token_has_an_expansion():
    """The payload leaf types CR 701 actions and CR 111.10 tokens by name;
    the full grammar fills `KeywordAction.expansion` and a predefined
    token's abilities from this one table."""
    from engine.effect_grammar.sub import payload
    kw = _kw()
    by_section = {}
    for name, e in kw.EXPANSIONS.items():
        by_section.setdefault(e.section, set()).add(name)
    assert by_section["701"] == set(payload.KEYWORD_ACTION_NAMES)
    assert by_section["111.10"] == set(payload.PREDEFINED_TOKENS)
    assert by_section["702"] <= set(kw.KEYWORD_ABILITIES)


def test_an_expansion_is_l0_text_with_every_parameter_filled():
    kw = _kw()
    for name, e in kw.EXPANSIONS.items():
        args = {p: "2" if p == "n" else ("zombie" if p == "subtype" else "~")
                for p in e.params}
        text = kw.expansion_text(name, **args)
        assert "$" not in text, name
        assert text == "" or text.endswith("."), name
        assert text == re.sub(r"\s+", " ", text).strip(), name
        assert not re.search(r"[A-Z’—–\"]", text), name
        assert "this creature" not in text and "this artifact" not in text, name
        assert e.host in ("effect", "ability"), name
    assert kw.expansion_text("adapt", n="3") == (
        "if ~ has no +1/+1 counters on it, put 3 +1/+1 counters on it.")
    assert kw.expansion_text("blorp") is None
    with pytest.raises(KeyError):
        kw.expansion_text("adapt")


# ── Leaf conventions ───────────────────────────────────────────────────

def test_the_keyword_caches_are_bounded_and_cleared():
    from engine.effect_grammar.sub import CACHE_SIZE
    kw = _kw()
    caches = [f for f in vars(kw).values()
              if callable(f) and hasattr(f, "cache_info")
              and getattr(f, "__module__", "") == kw.__name__]
    assert caches
    assert all(c.cache_info().maxsize == CACHE_SIZE for c in caches)
    _line("flashback {2}{r}")
    kw.parse_cost_rule("the flashback cost is equal to its mana cost.")
    assert any(c.cache_info().currsize for c in caches)
    kw.clear_caches()
    assert all(c.cache_info().currsize == 0 for c in caches)


def test_the_keyword_tables_read_l0_output_and_no_game_state():
    src = LEAF_PATH.read_text()
    assert "’" not in src and ".lower()" not in src
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    engine_mods = {m for m in mods if m.startswith("engine")}
    assert engine_mods <= {"engine.effect_spec", "engine.effect_grammar.sub",
                           "engine.oracle_parser"}, engine_mods
    # The leaf imports no sub-grammar leaf, so it adds no LEAF_EDGES edge.
    assert not any(m.startswith("engine.effect_grammar.sub.") for m in mods)


def test_every_detail_code_is_from_the_closed_list():
    kw = _kw()
    for text in ("cumulative upkeep {g} or {w}", "echo-sacrifice ~.",
                 "protection from creatures, flying blorp"):
        u = _line(text).unmodelled
        assert u.detail.split(".", 1)[0] == kw.LEAF
        assert u.detail.split(".", 1)[1].split(":")[0] in kw.DETAIL_CODES
