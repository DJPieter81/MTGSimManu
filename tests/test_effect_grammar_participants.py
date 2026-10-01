"""Participants of the typed effect model: targets, subjects and choices
(design doc 2026-09-29, section 5; E0).

TargetRequirements come only from `target_solver`, and where each was read
has one owner: `parse_located` / `parse_spans`, from `parse()`'s own
claimed-span bookkeeping (F3, A20). The grammar keeps the requirements whose
span lies inside a verb's object slot, so a span must be the occurrence the
requirement was actually read from -- including its count ("up to two
target ...") -- even where `parse()` returns requirements out of printed
order.

The grammar-level participant tests (slot consumption and residue, noun uses
of "target", counted phrases, subjects, untargeted choices, recipient
unions) join this file with `engine/effect_grammar/`.
"""
from __future__ import annotations

import pytest

from tests.test_target_solver_located_parse import pool_texts


# Pool-wide (~17k pool texts containing "target"). Measured 2026-09-30 on
# this container (quiet, 4 cores): ~0.9 s for the body, plus ~16 s when it is
# the first test of the process to load the shared card DB. 120 s bounds a
# hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_parse_spans_places_requirements_where_parse_counted_them(card_db):
    """Sentences whose requirements parse() returns out of printed order map
    to their printed positions, and a counted requirement's span is the
    occurrence its count was read before."""
    from engine.target_solver import (_count_before, _singularize_targets,
                                      parse_spans)
    out_of_order = 0
    for t in pool_texts(card_db):
        if "target" not in t.lower():
            continue
        spans = parse_spans(t)
        starts = [s for _, s, _ in spans if s >= 0]
        if starts != sorted(starts):
            out_of_order += 1
        norm = _singularize_targets(t.lower())
        for r, start, _ in spans:
            if start < 0:
                continue
            counts = _count_before(norm, start)
            if counts is not None:
                assert (r.count_min, r.count_max) == counts, (t, r)
    assert out_of_order > 0     # the class the located parse exists for


# ── Clause-level participants (L3/L4, E0 step 10-11) ───────────────────

def _clause_specs(text, types=("instant",)):
    from engine.effect_grammar import normalize as N
    from engine.effect_grammar import patterns as PT
    from engine.effect_grammar import structure as S
    tc = frozenset(types)
    facts = N.Facts(type_class=tc, is_spell=bool({"instant", "sorcery"} & tc),
                    names=N.self_names("Some Card"))
    out = []
    for h in S.parse_face_structure(text, facts).hosts:
        for fm in PT.match_host(h):
            out.extend(fm.clauses)
    return out


def test_the_solvers_you_may_optional_window_survives_clause_level_parsing():
    """The solver reads "you may" before a target as an optional
    requirement (CR 601.2c "up to" / optional targeting); the clause the
    target leaf hands it keeps that window, including a clause whose
    "you may" subject is inherited."""
    from engine.target_solver import parse_spans
    (cm,) = _clause_specs("You may exile target creature.")
    (req,) = [r for _, r, _ in cm.targets]
    assert req.is_optional
    assert req == parse_spans("you may exile target creature")[0][0]
    assert cm.spec.optional


def test_a_clause_needs_one_requirement_per_printed_target_word():
    """F11: a slot whose printed target words outnumber the solver's
    requirements is UNMODELLED(TARGET_COUNT), never a collapsed target."""
    (cm,) = _clause_specs("Return target creature card from your graveyard "
                          "and target artifact to your hand.")
    assert cm.spec.verb.name == "UNMODELLED"
    assert cm.spec.payload.stage.name == "TARGET_COUNT"


def test_an_elided_subject_is_inherited_by_the_next_coordinated_clause_without_a_second_target():
    """"Target player draws two cards and loses 2 life": one requirement;
    the second clause's actor is the inherited subject, not a new target."""
    a, b = _clause_specs("Target player draws two cards and loses 2 life.")
    assert [s.spec.verb.name for s in (a, b)] == ["DRAW", "LOSE_LIFE"]
    assert [role for role, _, _ in a.targets] == ["actor"]
    assert b.targets == ()
    assert ("actor", "inherited") in [(r, v) for r, v, _ in b.participants]


def test_a_recipient_union_splits_into_simultaneous_damage_siblings():
    """A17: "damage to each opponent and each planeswalker you don't
    control" is two DAMAGE siblings sharing one simultaneity group and the
    printed amount."""
    a, b = _clause_specs("~ deals 4 damage to each opponent and each "
                         "planeswalker you don't control.")
    assert a.spec.verb.name == b.spec.verb.name == "DAMAGE"
    assert a.spec.amount == b.spec.amount and a.spec.amount.n == 4
    assert a.spec.group == b.spec.group is not None
    assert b.spec.filter is not None and \
        b.spec.filter.types == frozenset({"planeswalker"})
