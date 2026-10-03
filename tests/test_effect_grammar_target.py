"""The target sub-grammar (design doc 2026-09-29, section 5; F3, F4, F11,
A20, A21, M4; E0 step 11).

`engine/effect_grammar/sub/target.py` types one target slot of a clause:

* the requirements come only from `target_solver` (its one owner): the leaf
  runs `parse_spans` on the slot's clause and keeps the requirements placed
  inside the slot, unmodified, in printed order (CR 601.2c);
* each printed target word yields exactly one requirement; noun and verb
  uses of "target" do not count (F11);
* every token of the slot is consumed by a requirement's printed phrase or
  a recognised feature; a feature the requirement does not carry is a
  residue code with a polarity (A21), never a silent widening;
* a slot the requirements cannot describe is UNMODELLED(TARGET) or
  UNMODELLED(TARGET_COUNT), never an untargeted parse.

Synthetic L0 phrases only (lowercased, self-forms ``~``); no card names.
The pool run and the registered-deck witnesses live in
tests/test_effect_grammar_target_pool.py.
"""
from __future__ import annotations

import re

import pytest

from engine.effect_spec import (NARROWING, UNPARSED, WIDENING, Amount,
                                AmountKind, Stage, residue_polarity)


def _T():
    from engine.effect_grammar.sub import target
    return target


def _slot(host: str, phrase: str, start: int = 0):
    a = host.index(phrase, start)
    return (a, a + len(phrase))


def _parse(host: str, phrase: str, **kw):
    return _T().parse_target(host, _slot(host, phrase), **kw)


# ── The requirement is the solver's, unmodified ────────────────────────

def test_a_target_requirement_is_an_unmodified_element_of_target_solver_parse_of_its_clause():
    from engine.target_solver import parse
    host = ("destroy target creature an opponent controls. "
            "you gain 2 life.")
    r = _parse(host, "target creature an opponent controls")
    (req,) = r.value.requirements
    clause = "destroy target creature an opponent controls"
    assert req in parse(clause)
    assert req.owner_scope == "opponent" and req.zone == "battlefield"
    assert r.value.residue == ()
    # The span is the printed phrase in HOST coordinates.
    (span,) = r.value.spans
    assert host[slice(*span)] == "target creature an opponent controls"
    assert host[slice(*r.span)] == "target creature an opponent controls"
    assert r.rest_spans == ()


def test_the_requirement_is_parsed_on_its_own_clause_not_the_whole_host():
    """F3: a zone hint in another sentence of the host must not reach the
    slot's requirement (the solver's loose graveyard fallback reads any
    graveyard phrase in the text it is given)."""
    host = ("return target creature to its owner's hand. "
            "you may cast spells from your graveyard.")
    r = _parse(host, "target creature")
    (req,) = r.value.requirements
    assert req.zone == "battlefield"


def test_an_explicit_clause_span_bounds_the_solver_text():
    host = "tap target creature, then untap target land."
    slot = _slot(host, "target land")
    clause = (host.index("untap"), len(host) - 1)
    r = _T().parse_target(host, slot, clause=clause)
    (req,) = r.value.requirements
    assert req.types == frozenset({"land"})
    assert host[slice(*r.value.spans[0])] == "target land"


def test_a_slot_without_a_counted_target_word_is_not_a_target_slot():
    T = _T()
    for host in ("destroy all creatures.", "draw two cards.",
                 "whenever ~ becomes the target of a spell, sacrifice it.",
                 "spells that target ~ cost {2} less to cast.",
                 "you may choose new targets for the copy.",
                 "creatures you control have ⟨q0⟩."):
        assert T.parse_target(host) is None, host


# ── F11: one requirement per printed target word ───────────────────────

@pytest.mark.parametrize("host,counted", [
    ("destroy target creature.", ["target"]),
    ("~ deals 3 damage to any target.", ["target"]),
    ("~ deals 2 damage to any other target.", ["target"]),
    ("change the target of target spell or ability with a single target.",
     ["target"]),
    ("whenever ~ becomes the target of a spell or ability, draw a card.", []),
    ("spells your opponents cast that target a creature you control cost {1} more.", []),
    ("if it targets a creature, draw a card.", []),
    ("~ costs {1} more to cast for each target beyond the first.", []),
    ("you may choose new targets for the copy.", []),
    ("~ deals 3 damage divided as you choose among one or two targets.",
     ["targets"]),
    ("~ deals x damage divided as you choose among any number of targets.",
     ["targets"]),
    ("tap target creature target player controls.", ["target", "target"]),
    ("creatures you control have ⟨q0⟩.", []),
    # "copy" is an imperative verb before a counted target word; the noun
    # "copy" as a subject makes "targets" a verb (a plural needs a count).
    ("{2}, {t}: copy target instant or sorcery spell you control.",
     ["target"]),
    ("copy target activated or triggered ability you control.", ["target"]),
    ("the copy targets a creature.", []),
    ("each copy targets a different creature.", []),
    # "another" and ordinals introduce further targets of one ability.
    ("~ deals 1 damage to any target, 2 damage to another target, and 3 "
     "damage to a third target.", ["target", "target", "target"]),
    ("choose a second target.", ["target"]),
    ("~ deals 2 damage to each of up to two other targets.", ["targets"]),
    ("~ deals 1 damage to each of two other targets.", ["targets"]),
])
def test_noun_uses_of_the_word_target_do_not_count_as_requirements(host, counted):
    words = _T().target_words(host)
    assert [host[a:b] for a, b in words] == counted


def test_a_clause_needs_one_requirement_per_printed_target_word():
    """The solver's graveyard reading stops at the first graveyard target,
    so a two-target slot gets one requirement: TARGET_COUNT, never a
    silently collapsed target."""
    host = ("return target creature card from your graveyard and target "
            "artifact to your hand.")
    slot = _slot(host, "target creature card from your graveyard and "
                       "target artifact")
    r = _T().parse_target(host, slot, lemma="return")
    assert r.value is None
    assert r.unmodelled.stage is Stage.TARGET_COUNT
    assert r.unmodelled.detail == "target.count_mismatch"
    assert r.unmodelled.lemma == "return"
    assert r.span == slot


def test_a_target_phrase_the_solver_cannot_parse_is_unmodelled_not_untargeted():
    T = _T()
    host = "exile target attacking creature."
    r = _parse(host, "target attacking creature", lemma="exile")
    assert r.value is None
    assert r.unmodelled.stage is Stage.TARGET
    assert r.unmodelled.detail == "target.no_requirement:attacking"
    host = "~ deals 3 damage divided as you choose among one or two targets."
    r = _parse(host, "one or two targets", lemma="deal")
    assert r.unmodelled.stage is Stage.TARGET
    assert r.unmodelled.detail.startswith("target.no_requirement")


# ── A21: consumption and residue with a polarity ───────────────────────

@pytest.mark.parametrize("host,phrase,code,polarity", [
    ("exile up to one target permanent that's one or more colors.",
     "up to one target permanent that's one or more colors",
     "target.colored", WIDENING),
    ("destroy target artifact an opponent controls.",
     "target artifact an opponent controls", "target.scope:opponent", WIDENING),
    ("destroy target creature you don't control.",
     "target creature you don't control", "target.scope:not_you", WIDENING),
    ("destroy target creature with flying.", "target creature with flying",
     "target.keyword:flying", WIDENING),
    ("destroy target creature with first strike.",
     "target creature with first strike", "target.keyword:first_strike",
     WIDENING),
    ("exile target creature with power 3 or greater.",
     "target creature with power 3 or greater", "target.stat:power", WIDENING),
    ("exile another target creature.", "another target creature",
     "target.exclude_source", WIDENING),
    ("destroy target creature other than ~.", "target creature other than ~",
     "target.exclude_source", WIDENING),
    ("destroy target artifact creature.", "target artifact creature",
     "target.conjunctive_types", WIDENING),
    ("destroy target creature that's a nontoken.",
     "target creature that's a nontoken", "target.nontoken", WIDENING),
    ("destroy target creature that's historic.",
     "target creature that's historic", "target.historic", WIDENING),
    ("tap target artifact that player controls.",
     "target artifact that player controls", "target.dependent_controller",
     WIDENING),
    ("return target instant or sorcery card from your graveyard to your hand.",
     "target instant or sorcery card from your graveyard",
     "target.union:sorcery", NARROWING),
    ("~ deals 2 damage to target creature or player.",
     "target creature or player", "target.union:player", NARROWING),
    ("destroy target land you control blorp.",
     "target land you control blorp", "target.unparsed", UNPARSED),
    ("destroy target nonbasic land.", "target nonbasic land",
     "target.unparsed", UNPARSED),
    # One code per listed member, negation and passive kept apart.
    ("destroy target creature without flying.",
     "target creature without flying", "target.keyword:without_flying",
     WIDENING),
    ("destroy target creature with flying or reach.",
     "target creature with flying or reach", "target.keyword:reach",
     WIDENING),
    ("destroy target creature with power or toughness 1 or less.",
     "target creature with power or toughness 1 or less",
     "target.stat:toughness", WIDENING),
    ("destroy target creature that was dealt damage this turn.",
     "target creature that was dealt damage this turn",
     "target.state:was_dealt", WIDENING),
    ("destroy target creature that attacked or blocked this turn.",
     "target creature that attacked or blocked this turn",
     "target.state:blocked", WIDENING),
])
def test_every_token_of_a_target_slot_is_consumed_or_recorded_as_residue(
        host, phrase, code, polarity):
    r = _parse(host, phrase)
    assert r.value is not None, r
    assert code in r.value.residue, r.value.residue
    assert residue_polarity(code) == polarity
    # The requirement is still the solver's own, unmodified.
    from engine.target_solver import parse
    clause = host[:-1]
    for req in r.value.requirements:
        assert req in parse(clause)
    # Every residue entry is a declared code, sorted and unique.
    assert all(residue_polarity(c) for c in r.value.residue)
    assert list(r.value.residue) == sorted(set(r.value.residue))
    assert r.span == _slot(host, phrase)


@pytest.mark.parametrize("phrase,codes", [
    ("target creature without flying", ["target.keyword:without_flying"]),
    ("target creature with flying", ["target.keyword:flying"]),
    ("target creature with flying or reach",
     ["target.keyword:flying", "target.keyword:reach"]),
    ("target creature with power or toughness 1 or less",
     ["target.stat:power", "target.stat:toughness"]),
    ("target creature with power 3 or greater", ["target.stat:power"]),
    ("target creature that was dealt damage this turn",
     ["target.state:was_dealt"]),
    ("target creature that dealt damage this turn", ["target.state:dealt"]),
    ("target creature that attacked or blocked this turn",
     ["target.state:attacked", "target.state:blocked"]),
    ("target creature with the greatest power among creatures you control",
     ["target.stat:greatest_power"]),
])
def test_distinct_printed_restrictions_get_distinct_residue_codes(phrase, codes):
    """The residue names what a later modelling step must add: every listed
    keyword or stat is its own code, "without" is not "with", and a passive
    history ("was dealt damage") is not the active one."""
    r = _parse("exile %s." % phrase, phrase)
    assert r.value is not None, r
    assert list(r.value.residue) == sorted(codes)


@pytest.mark.parametrize("phrase", [
    "target creature with the greatest power among creatures you control blorp",
    "target creature with the greatest power blorp",
    "target spell with mana value less than or equal to blorp",
    "target creature with power less than blorp",
    "target creature with power less than or equal to the number of blorp",
    "target creature that attacked or blorp this turn",
    "target creature that dealt damage blorp",
    "target creature that blocked or",
    "target creature with blorp +1/+1 counters on it",
])
def test_a_token_no_feature_reads_is_unparsed_never_absorbed_by_a_widening_row(phrase):
    """A21 full consumption: a wildcard must not swallow a word nothing
    reads into a tolerable WIDENING code."""
    r = _parse("exile %s." % phrase, phrase)
    assert r.value is not None, r
    assert "target.unparsed" in r.value.residue, r.value.residue


@pytest.mark.parametrize("phrase,code", [
    ("target spell with mana value less than or equal to ~'s power",
     "target.stat:mana_value"),
    ("target creature with power less than or equal to the number of "
     "cards in your hand", "target.stat:power"),
    ("target creature with power less than or equal to the number of "
     "+1/+1 counters removed this way", "target.stat:power"),
    ("target creature with power greater than or equal to your life total",
     "target.stat:power"),
    ("target creature with the greatest power among creatures target "
     "opponent controls", "target.stat:greatest_power"),
    ("target creature that dealt damage to you this turn",
     "target.state:dealt"),
    ("target creature that entered this turn", "target.state:entered"),
    ("target creature with two +1/+1 counters on it", "target.state:counter"),
])
def test_closed_operands_and_complements_are_consumed(phrase, code):
    r = _parse("exile %s." % phrase, phrase)
    assert r.value is not None, r
    assert code in r.value.residue
    assert "target.unparsed" not in r.value.residue, r.value.residue


@pytest.mark.parametrize("host,phrase", [
    ("exile target creature an opponent controls with mana value 2 or less.",
     "target creature an opponent controls with mana value 2 or less"),
    ("destroy target creature you control.", "target creature you control"),
    ("return target creature card from your graveyard to your hand.",
     "target creature card from your graveyard"),
    ("counter target noncreature spell.", "target noncreature spell"),
    ("destroy target nonland permanent.", "target nonland permanent"),
    ("tap up to two target creatures.", "up to two target creatures"),
])
def test_a_feature_the_requirement_carries_leaves_no_residue(host, phrase):
    r = _parse(host, phrase)
    assert r.value is not None and r.value.residue == (), r


def test_a_printed_target_qualifier_the_solver_drops_is_recorded_as_residue_never_silently_widened():
    """A mana-value ceiling the requirement does not carry is residue; one
    it carries is consumed."""
    host = "exile target creature with mana value 3 or greater."
    r = _parse(host, "target creature with mana value 3 or greater")
    (req,) = r.value.requirements
    assert req.max_mana_value is None
    assert r.value.residue == ("target.stat:mana_value",)
    host = "exile target creature with mana value 3 or less."
    r = _parse(host, "target creature with mana value 3 or less")
    assert r.value.requirements[0].max_mana_value == 3
    assert r.value.residue == ()


def test_a_target_zone_union_the_requirement_cannot_carry_is_unmodelled_with_a_zone_union_code():
    host = ("return target spell or nonland permanent an opponent controls "
            "to its owner's hand.")
    r = _parse(host, "target spell or nonland permanent an opponent controls",
               lemma="return")
    assert r.value is None
    assert r.unmodelled.stage is Stage.TARGET
    assert r.unmodelled.detail == "target.zone_union"
    assert residue_polarity(r.unmodelled.detail) == UNPARSED


def test_a_requirement_in_a_zone_the_slot_does_not_name_is_unmodelled():
    """The solver reads "target nonland permanent card from <graveyard>" as a
    battlefield permanent; the slot names the graveyard (CR 115.1: the
    target is the card in that zone)."""
    host = "return target nonland permanent card from your graveyard to your hand."
    r = _parse(host, "target nonland permanent card from your graveyard")
    assert r.value is None
    assert r.unmodelled.stage is Stage.TARGET
    assert r.unmodelled.detail == "target.zone_mismatch"


# ── Counts (CR 115.1) and order (CR 601.2c) ────────────────────────────

def test_counted_target_phrases_keep_their_count():
    from engine.target_solver import ANY_NUMBER
    host = "tap up to two target creatures."
    r = _parse(host, "up to two target creatures")
    (req,) = r.value.requirements
    assert (req.count_min, req.count_max) == (0, 2)
    assert r.amount == Amount(AmountKind.UP_TO, n=2)
    host = "tap two target creatures."
    r = _parse(host, "two target creatures")
    assert r.amount == Amount(AmountKind.LITERAL, n=2)
    host = "tap any number of target creatures."
    r = _parse(host, "any number of target creatures")
    (req,) = r.value.requirements
    assert req.count_max == ANY_NUMBER          # the solver's, unmodified
    assert r.amount == Amount(AmountKind.ANY_NUMBER)  # never the sentinel
    assert r.amount.n != ANY_NUMBER
    host = "tap target creature."
    assert _parse(host, "target creature").amount is None


def test_a_printed_count_the_requirement_does_not_carry_is_unmodelled_target_count():
    host = "tap x target creatures."
    r = _parse(host, "x target creatures", lemma="tap")
    assert r.value is None
    assert r.unmodelled.stage is Stage.TARGET_COUNT
    assert r.unmodelled.detail == "target.count_unread:x"


def test_target_phrases_of_one_ability_keep_printed_order():
    """parse() returns the creature before the player; the slot lists them
    in printed order (CR 601.2c)."""
    from engine.target_solver import parse
    host = "target player and target creature an opponent controls."
    phrase = "target player and target creature an opponent controls"
    solver_order = [sorted(r.types) for r in parse(host[:-1])]
    assert solver_order == [["creature"], ["player"]]
    r = _parse(host, phrase)
    assert [sorted(q.types) for q in r.value.requirements] == [
        ["player"], ["creature"]]
    assert [host[a:b] for a, b in r.value.spans] == [
        "target player", "target creature an opponent controls"]
    assert r.value.residue == ()


def test_a_possessive_target_leaves_the_possessed_noun_as_rest():
    host = "exile target player's graveyard."
    r = _parse(host, "target player's graveyard")
    (req,) = r.value.requirements
    assert req.types == frozenset({"player"})
    assert "possessive" in r.flags
    assert r.rest_text(host) == "graveyard"


def test_a_player_target_heading_a_relative_clause_hands_its_verb_on_as_rest():
    """"each creature target player controls": the target is the player;
    "controls" is the relative clause's verb, not a qualifier of it."""
    host = "~ deals 2 damage to each creature target player controls."
    r = _parse(host, "target player controls")
    (req,) = r.value.requirements
    assert req.types == frozenset({"player"})
    assert r.value.residue == ()
    assert host[slice(*r.span)] == "target player"
    assert r.rest_text(host) == "controls"


def test_a_player_target_heading_an_owns_relative_clause_hands_its_verb_on_as_rest():
    host = "return each nonland permanent target player owns to its owner's hand."
    r = _parse(host, "target player owns")
    (req,) = r.value.requirements
    assert req.types == frozenset({"player"})
    assert r.value.residue == ()
    assert host[slice(*r.span)] == "target player"
    assert r.rest_text(host) == "owns"


def test_a_plural_possessive_target_leaves_the_possessed_noun_as_rest():
    host = "exile any number of target players' graveyards."
    r = _parse(host, "any number of target players' graveyards")
    (req,) = r.value.requirements
    assert req.types == frozenset({"player"})
    assert "possessive" in r.flags
    assert "target.unparsed" not in r.value.residue
    assert r.rest_text(host) == "graveyards"


def test_a_juxtaposed_player_target_names_the_objects_controller_as_residue():
    """"target creature target player controls": the creature's controller
    is the targeted player, a dependency the requirement cannot carry."""
    host = "tap target creature target player controls."
    r = _parse(host, "target creature target player controls")
    assert [sorted(q.types) for q in r.value.requirements] == [
        ["creature"], ["player"]]
    assert r.value.residue == ("target.dependent_controller",)
    assert residue_polarity("target.dependent_controller") == WIDENING
    assert r.rest_spans == ()


# ── The leaf contract ──────────────────────────────────────────────────

def test_a_failed_target_slot_reports_the_whole_trimmed_slot_and_the_callers_lemma():
    T = _T()
    host = "exile target attacking creature."
    a = host.index("target") - 1           # a leading space in the slot
    r = T.parse_target(host, (a, host.index(".")))
    assert r.span == (a + 1, host.index("."))
    assert r.unmodelled.lemma == ""
    m = re.match(r"^target\.([a-z_]+)(?::\S+)?$", r.unmodelled.detail)
    assert m and m.group(1) in T.DETAIL_CODES


def test_a_refused_token_param_is_one_word_without_punctuation():
    host = "counter target noncreature, nonland spell."
    r = _parse(host, "target noncreature, nonland spell")
    if r.value is None:
        assert re.match(r"^target\.[a-z_]+(?::[\w/+-]+)?$", r.unmodelled.detail)
    host = "exile target attacking, blocking creature."
    r = _parse(host, "target attacking, blocking creature")
    assert r.unmodelled.detail == "target.no_requirement:attacking"


def test_the_target_leaf_caches_are_bounded_and_cleared_by_the_package():
    from engine.effect_grammar import sub
    T = _T()
    caches = [f for f in vars(T).values()
              if callable(f) and hasattr(f, "cache_info")
              and getattr(f, "__module__", "") == T.__name__]
    assert caches
    assert all(c.cache_info().maxsize == sub.CACHE_SIZE for c in caches)
    T.parse_target("destroy target creature.")
    assert any(c.cache_info().currsize for c in caches)
    sub.clear_caches()
    assert all(c.cache_info().currsize == 0 for c in caches)


def test_the_target_leaf_imports_only_its_declared_leaf_edges():
    import ast
    from pathlib import Path
    from engine.effect_grammar.sub import LEAF_EDGES
    path = Path(_T().__file__)
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("engine.effect_grammar.sub."):
                found.add(node.module.rsplit(".", 1)[1])
            elif node.module == "engine.effect_grammar.sub":
                found.update(a.name for a in node.names
                             if (path.parent / (a.name + ".py")).exists())
            elif node.module == "engine.effect_grammar":
                found.update(a.name for a in node.names
                             if (path.parent.parent / (a.name + ".py")).exists())
    assert found == set(LEAF_EDGES["target"]) == {"dest", "quantity", "keywords"}
    src = path.read_text()
    assert "’" not in src and ".lower()" not in src
