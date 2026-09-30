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
