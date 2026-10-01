"""The filter sub-grammar (design doc 2026-09-29, sections 2 CardFilter, 5
subjects and untargeted choices; A19 classes, A21 full consumption, A22 /
F5 value-typed keyword exclusions; E0 step 11).

A filter slot is an untargeted object description: a FILTER subject
("creatures you control"), the selection of an untargeted choice
("sacrifices a nontoken creature"), a search ("a basic land card") or the
counted object of a quantity ("each artifact you control"). The leaf types
it as one `CardFilter` under the one leaf contract
(`engine.effect_grammar.sub`), and it requires FULL consumption: a token it
cannot place makes the slot ``UNMODELLED(FILTER)``, never a broader filter
that silently drops the qualifier.

Synthetic phrases only; no card names.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from engine.effect_spec import (Amount, AmountKind, CardFilter, Ref, RefKind,
                                Stage)

REPO = Path(__file__).resolve().parent.parent
SUB = REPO / "engine" / "effect_grammar" / "sub"


def _f(text, **kw):
    from engine.effect_grammar.sub import filter as F
    return F.parse_filter(text, (0, len(text)), **kw)


def _ok(text, **kw):
    r = _f(text, **kw)
    assert r.value is not None and r.unmodelled is None, (text, r)
    assert isinstance(r.value, CardFilter)
    return r


# ── Types, supertypes, subtypes and derived classes ────────────────────

def test_a_type_list_joined_by_or_is_a_union_of_types():
    r = _ok("a creature or planeswalker")
    assert r.value.types == frozenset({"creature", "planeswalker"})
    assert r.value.all_types == frozenset()
    assert r.amount == Amount(AmountKind.LITERAL, n=1)


def test_a_serial_type_list_with_a_shared_head_noun_is_a_union():
    r = _ok("each artifact, creature, and enchantment")
    assert r.value.types == frozenset({"artifact", "creature", "enchantment"})
    assert "each" in r.flags


def test_adjacent_card_types_are_a_conjunction():
    """CR 205.2: an "artifact creature" has both types."""
    r = _ok("all artifact creatures")
    assert r.value.all_types == frozenset({"artifact", "creature"})
    assert r.value.types == frozenset()
    assert "all" in r.flags


def test_negated_types_and_supertypes_are_exclusions_that_conjoin():
    r = _ok("each nonland, nontoken permanent")
    assert r.value.not_types == frozenset({"land"})
    assert r.value.token is False
    assert r.value.zone == "battlefield"
    r = _ok("a nonbasic land")
    assert r.value.not_supertypes == frozenset({"basic"})
    assert r.value.types == frozenset({"land"})


def test_a_land_type_list_is_a_union_of_subtypes():
    r = _ok("a forest or island card", zone="library")
    assert r.value.subtypes == frozenset({"forest", "island"})
    assert r.value.zone == "library"


def test_plural_subtypes_are_read_as_their_subtype():
    r = _ok("elves you control")
    assert r.value.subtypes == frozenset({"elf"})
    assert r.value.controller == "you"
    assert _ok("other zombies").value.subtypes == frozenset({"zombie"})
    assert _ok("non-human creatures").value.not_subtypes == frozenset({"human"})


def test_derived_object_classes_are_closed_filter_classes():
    """A19: historic (CR 700.6), colored, multicolored and monocolored are
    derived classes, never subtypes or colours."""
    assert _ok("a historic card", zone="library").value.classes == frozenset({"historic"})
    assert _ok("each multicolored creature").value.classes == frozenset({"multicolored"})
    r = _ok("all permanents they control that are one or more colors")
    assert r.value.classes == frozenset({"colored"})
    assert r.pending == (("controller", "they"),)


def test_colour_words_are_colour_sets_and_colorless_is_its_own_field():
    r = _ok("a green creature card", zone="library")
    assert r.value.colors == frozenset({"G"})
    assert r.value.types == frozenset({"creature"})
    r = _ok("any number of colorless nonland cards", zone="library")
    assert r.value.colorless is True
    assert r.value.not_types == frozenset({"land"})
    assert r.amount == Amount(AmountKind.ANY_NUMBER)
    assert _ok("each nonblack creature").value.not_colors == frozenset({"B"})
    r = _ok("all cards that are black or red from all graveyards")
    assert r.value.colors == frozenset({"B", "R"})
    assert r.value.zone == "graveyard"


def test_a_token_word_is_the_token_field():
    r = _ok("a creature token")
    assert r.value.token is True and r.value.types == frozenset({"creature"})
    assert _ok("a nontoken creature").value.token is False


# ── Premodifier scope and coordinated descriptors (A21) ──────────────

@pytest.mark.parametrize("text", [
    "a creature or basic land card",
    "a creature card or basic land card",
    "each red creature or white artifact",
    "each creature or white artifact",
    "a creature or legendary planeswalker",
    "each creature or nontoken artifact",
    "each creature token or artifact",
])
def test_a_premodifier_on_one_union_member_does_not_constrain_the_other_members(text):
    """A21: a descriptor printed on one member of an 'or' / 'and' union
    states a rule about that member only. The one CardFilter has one set of
    descriptor fields, so the leaf refuses rather than apply the member's
    descriptor to every member (a broader or narrower filter)."""
    r = _f(text, zone="library")
    assert r.value is None, (text, r.value)
    assert r.unmodelled.detail == "filter.modifier_scope", r.unmodelled


def test_premodifiers_before_the_first_union_member_are_shared_by_every_member():
    r = _ok("each legendary creature or planeswalker")
    assert r.value.types == frozenset({"creature", "planeswalker"})
    assert r.value.supertypes == frozenset({"legendary"})
    r = _ok("each nonland, nontoken permanent")
    assert r.value.not_types == frozenset({"land"}) and r.value.token is False
    r = _ok("each nontoken creature or planeswalker")
    assert r.value.token is False
    r = _ok("each white creature or white artifact")
    assert r.value.colors == frozenset({"W"})
    assert r.value.types == frozenset({"creature", "artifact"})


def test_colours_and_states_joined_by_or_are_a_union_of_that_field():
    """``colors`` and ``state`` are disjunctive fields: a CardFilter matches
    an object with any one of the values ('black or red', 'attacking or
    blocking')."""
    assert _ok("a white or blue creature").value.colors == frozenset({"W", "U"})
    assert _ok("each white, blue, or black creature").value.colors == \
        frozenset({"W", "U", "B"})
    assert _ok("an attacking or blocking creature").value.state == \
        frozenset({"attacking", "blocking"})
    assert _ok("each tapped and/or attacking creature").value.state == \
        frozenset({"tapped", "attacking"})


@pytest.mark.parametrize("text", [
    "a white and blue creature",          # both colours: a conjunction
    "each red and green artifact",
    "each untapped attacking creature",   # both states: a conjunction
    "each tapped attacking creature",
    "each attacking creature that's tapped",
    "each white creature that's black or red",
])
def test_stacked_or_and_joined_colours_or_states_are_refused(text):
    """A conjunction of two values of a disjunctive field is not a
    CardFilter; the leaf refuses it rather than type the union."""
    r = _f(text)
    assert r.value is None, (text, r.value)
    assert r.unmodelled.detail == "filter.modifier_join", r.unmodelled


def test_stacked_exclusions_supertypes_and_classes_are_a_conjunction():
    """``not_*``, ``supertypes`` and ``classes`` are conjunctive fields:
    every listed value holds ('noncreature, nonland' is neither)."""
    r = _ok("each noncreature, nonland permanent")
    assert r.value.not_types == frozenset({"creature", "land"})
    r = _ok("a legendary snow permanent")
    assert r.value.supertypes == frozenset({"legendary", "snow"})
    r = _ok("each untapped red creature")
    assert r.value.state == frozenset({"untapped"})
    assert r.value.colors == frozenset({"R"})


@pytest.mark.parametrize("text", [
    "each noncreature or nonland permanent",
    "a legendary or basic land",
    "each multicolored or colorless creature",
    "each red or colorless creature",
    "each tapped or red creature",
    "each historic or monocolored permanent",
])
def test_or_between_conjunctive_or_different_descriptor_fields_is_refused(text):
    """'or' is a union only inside one disjunctive field; between exclusions,
    supertypes or classes, or across two fields, it is not a CardFilter."""
    r = _f(text)
    assert r.value is None, (text, r.value)
    assert r.unmodelled.detail == "filter.modifier_join", r.unmodelled


def test_a_descriptor_with_no_head_after_a_connector_is_refused():
    r = _f("each creature or white")
    assert r.value is None and r.unmodelled.detail == "filter.no_head"


# ── Determiners are amounts and flags, never filter entries ────────────

@pytest.mark.parametrize("text,amount,flags,other", [
    ("a creature", Amount(AmountKind.LITERAL, n=1), frozenset(), False),
    ("two creatures", Amount(AmountKind.LITERAL, n=2), frozenset(), False),
    ("x creatures", Amount(AmountKind.X, n=1), frozenset(), False),
    ("up to two land cards", Amount(AmountKind.UP_TO, n=2), frozenset(), False),
    ("another creature", Amount(AmountKind.LITERAL, n=1), frozenset(), True),
    ("each other creature", None, frozenset({"each"}), True),
    ("all creatures", None, frozenset({"all"}), False),
    ("creatures", None, frozenset(), False),
])
def test_a_determiner_is_the_selection_amount_or_a_quantifier_flag(
        text, amount, flags, other):
    r = _ok(text, zone="library")
    assert r.amount == amount
    assert r.flags == flags
    assert r.value.other is other


def test_up_to_x_is_an_up_to_amount_over_x():
    r = _ok("up to x creature cards", zone="library")
    assert r.amount == Amount(AmountKind.UP_TO, inner=Amount(AmountKind.X, n=1))


# ── Controllers, owners and zones ──────────────────────────────────────

@pytest.mark.parametrize("phrase,controller", [
    ("creatures you control", "you"),
    ("creatures you don't control", "not_you"),
    ("creatures your opponents control", "opponents"),
    ("each creature an opponent controls", "opponents"),
    ("each creature target player controls", Ref(RefKind.TARGET, noun="player")),
    ("each creature defending player controls", Ref(RefKind.DEFENDING_PLAYER)),
])
def test_a_controller_clause_types_the_controller(phrase, controller):
    assert _ok(phrase).value.controller == controller


def test_an_anaphoric_controller_is_left_to_the_linker():
    r = _ok("all creatures they control")
    assert r.value.controller == "any"
    assert r.pending == (("controller", "they"),)


def test_a_zone_phrase_types_the_zone_and_its_owner():
    r = _ok("each creature card in your graveyard")
    assert (r.value.zone, r.value.owner) == ("graveyard", "you")
    r = _ok("all creature cards from their graveyard")
    assert r.value.zone == "graveyard"
    assert r.pending == (("owner", "their"),)
    r = _ok("all cards from target player's graveyard")
    assert r.value.owner == Ref(RefKind.TARGET, noun="player")
    assert _ok("a card in exile").value.zone == "exile"


def test_a_card_head_without_a_zone_takes_the_callers_zone_or_is_unmodelled():
    """A 'card' is never on the battlefield (CR 108.3): the zone is printed
    or given by the caller (a search reads its library)."""
    assert _ok("a land card", zone="library").value.zone == "library"
    r = _f("a land card")
    assert r.unmodelled is not None
    assert r.unmodelled.detail == "filter.card_zone"


def test_a_spell_head_is_on_the_stack():
    assert _ok("each instant or sorcery spell").value.zone == "stack"


def test_from_among_a_result_is_left_to_the_linker():
    r = _ok("a historic card from among them")
    assert r.pending == (("among", "them"),)


# ── Stats, keywords, counters, names ───────────────────────────────────

@pytest.mark.parametrize("phrase,bound", [
    ("each artifact with mana value 3 or less",
     ("mana_value", "<=", Amount(AmountKind.LITERAL, n=3))),
    ("a creature card with mana value x or less",
     ("mana_value", "<=", Amount(AmountKind.X, n=1))),
    ("each creature with power 4 or greater",
     ("power", ">=", Amount(AmountKind.LITERAL, n=4))),
    ("each creature with toughness 2 or less",
     ("toughness", "<=", Amount(AmountKind.LITERAL, n=2))),
    ("each creature with mana value less than or equal to two",
     ("mana_value", "<=", Amount(AmountKind.LITERAL, n=2))),
])
def test_a_characteristic_bound_is_a_stat_bound(phrase, bound):
    assert _ok(phrase, zone="library").value.stat_bounds == (bound,)


def test_a_bound_the_stat_table_cannot_read_is_unmodelled_stat():
    r = _f("each nonland permanent with mana value equal to the number of "
           "charge counters on ~")
    assert r.unmodelled.detail == "filter.stat"


def test_a_keyword_exclusion_is_typed_as_the_keyword_value_covers_object_compares():
    """F5 / A22: 'first strike' is typed 'first_strike', the value
    `Selector.covers_object` evaluates, so the entry can execute."""
    from engine.effect_model import is_supported_filter_entry
    r = _ok("each creature without first strike")
    assert r.value.without_keywords == frozenset({"first_strike"})
    entry = dict(r.value.as_tuple())
    assert entry["without_keyword"] == "first_strike"
    entry = ("without_keyword", entry["without_keyword"])
    assert is_supported_filter_entry(*entry)
    assert _ok("each creature with flying").value.with_keywords == frozenset({"flying"})


def test_a_keyword_the_table_lacks_is_unmodelled_keyword():
    r = _f("each creature without blorpwalk")
    assert r.unmodelled.detail == "filter.keyword:blorpwalk"


def test_a_counter_qualifier_reads_the_one_counter_parser():
    r = _ok("each nonland permanent without a fate counter on it")
    assert r.value.counters == (("fate", False),)
    r = _ok("each creature with one or more +1/+1 counters on it")
    assert r.value.counters == (("+1/+1", True),)


def test_names_and_the_other_than_self_form():
    r = _ok("any number of cards named ⟨n0⟩", zone="library")
    assert r.value.named == "⟨n0⟩"
    assert _ok("cards named ~ in your graveyard").value.named == Ref(RefKind.SELF)
    assert _ok("each creature other than ~").value.other is True
    assert _ok("creature cards with different names",
               zone="graveyard").value.different_names is True


# ── Full consumption (A21) ─────────────────────────────────────────────

@pytest.mark.parametrize("text,code", [
    ("each creature with the same name as that creature", "unparsed"),
    ("creatures you control that entered this turn", "unparsed"),
    ("a basic plains card or a creature card", "np_union"),
    ("a creature card or an aura card", "np_union"),
    ("an artifact or goblin", "type_mix"),
    ("target creature", "targeted"),
    ("enchanted creature", "reference"),
    ("each opponent", "player_head"),
    ("", "empty"),
])
def test_a_token_the_filter_cannot_place_makes_the_slot_unmodelled(text, code):
    """A21: no broader filter that drops the qualifier; the failure span is
    the whole trimmed slot."""
    r = _f(text, zone="library")
    assert r.value is None and r.unmodelled is not None, (text, r)
    assert r.unmodelled.stage is Stage.FILTER
    assert r.unmodelled.detail.split(":")[0] == "filter." + code, r.unmodelled
    assert r.span == (0, len(text))


# ── The leaf contract ──────────────────────────────────────────────────

def test_the_filter_reads_a_slot_of_the_whole_host_and_returns_host_spans():
    from engine.effect_grammar.sub import SlotResult
    from engine.effect_grammar.sub import filter as F
    host = "destroy all creatures you control with flying. draw a card."
    slot = (host.index("all"), host.index("."))
    r = F.parse_filter(host, slot, lemma="destroy")
    assert isinstance(r, SlotResult)
    assert r.span == slot and r.rest_spans == ()
    assert host[slice(*r.span)] == "all creatures you control with flying"
    assert r.value.raw == "all creatures you control with flying"
    bad = (host.index("draw"), len(host) - 1)
    u = F.parse_filter(host, bad, lemma="draw")
    assert u.unmodelled.lemma == "draw" and u.span == bad


def test_an_unmodelled_filter_carries_the_callers_lemma_and_a_closed_code():
    from engine.effect_grammar.sub import filter as F
    text = "each creature blorp"
    assert F.parse_filter(text, (0, len(text))).unmodelled.lemma == ""
    u = F.parse_filter(text, (0, len(text)), lemma="destroy").unmodelled
    assert u.lemma == "destroy"
    m = re.match(r"^filter\.(?P<code>[a-z_]+)(?::\S+)?$", u.detail)
    assert m and m.group("code") in F.DETAIL_CODES
    assert u.detail == "filter.unparsed:blorp"


def test_the_filter_caches_are_bounded_and_cleared_by_the_package():
    from engine.effect_grammar import sub
    from engine.effect_grammar.sub import filter as F
    caches = [a for a in vars(F).values()
              if callable(a) and hasattr(a, "cache_info")
              and getattr(a, "__module__", "") == F.__name__]
    assert caches
    assert all(c.cache_info().maxsize == sub.CACHE_SIZE for c in caches)
    _f("creatures you control")
    assert any(c.cache_info().currsize for c in caches)
    sub.clear_caches()
    assert all(c.cache_info().currsize == 0 for c in caches)


def test_the_filter_imports_another_leaf_only_along_its_declared_edge():
    """The counter qualifier reads the one counter noun-phrase parser, so
    filter -> payload is the one edge."""
    from engine.effect_grammar.sub import LEAF_EDGES
    out = set()
    for node in ast.walk(ast.parse((SUB / "filter.py").read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("engine.effect_grammar.sub."):
                out.add(node.module.rsplit(".", 1)[1])
            elif node.module == "engine.effect_grammar.sub":
                out.update(a.name for a in node.names
                           if (SUB / (a.name + ".py")).exists())
    assert out == set(LEAF_EDGES["filter"]) == {"payload"}


def test_the_filter_does_not_re_normalise_l0_output():
    src = (SUB / "filter.py").read_text()
    assert "’" not in src
    assert ".lower()" not in src
    assert not re.search(r"this \(\?:creature", src)
