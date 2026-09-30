"""Target placement has one owner: parse()'s own claimed-span bookkeeping.

Design doc 2026-09-29 (F3, A20). The clause grammar needs to know WHERE in a
clause each TargetRequirement was read, so it can keep the requirements whose
span lies inside a verb's object slot. That placement must come from the one
place that already decides it -- the claimed-span dict inside
`target_solver.parse()`, which is also where each requirement reads its count
("up to two target ...") -- never from a second search that could land on a
different occurrence of the same phrase.

Rules pinned:
* `parse()` is re-expressed as `[r for r, _ in parse_located(text)]` and its
  output is canonically identical, text for text, to a capture taken before
  the refactor over every oracle text, line and sentence in the card pool
  (fixture `tests/fixtures/target_solver_parse_capture.json`);
* a text without the word "target" yields no requirement;
* `parse_spans` places each requirement on the printed phrase it was read
  from, in input-text coordinates, including sentences whose requirements
  `parse()` returns out of printed order;
* a trailing "with mana value N/X or less" ceiling is read at that same
  placed occurrence (CR 601.2c), never at a first-occurrence search.

Regenerating the capture (`python tests/test_target_solver_located_parse.py
--capture`) is only legitimate in a commit that deliberately changes what
`parse()` returns, with the behaviour change named in that commit.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

CAPTURE_PATH = (Path(__file__).parent / "fixtures"
                / "target_solver_parse_capture.json")
_KEY_HEX = 12   # 48-bit digests: collision odds ~1e-6 over ~17k entries


def pool_texts(db) -> set:
    """Every oracle text, line and sentence of every template face."""
    texts = set()
    for t in {id(v): v for v in db.cards.values()}.values():
        for s in (t.oracle_text or "", getattr(t, "back_face_oracle", "") or ""):
            if not s:
                continue
            texts.add(s)
            for line in s.split("\n"):
                texts.add(line)
                for sent in re.split(r"(?<=[.])\s+", line):
                    texts.add(sent)
    texts.discard("")
    return texts


def canonical_requirement(r) -> tuple:
    """Field-by-field, frozensets sorted: independent of hash seed."""
    import dataclasses
    out = []
    for f in dataclasses.fields(r):
        v = getattr(r, f.name)
        if isinstance(v, frozenset):
            v = tuple(sorted(v))
        out.append((f.name, v))
    return tuple(out)


def _digest(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:_KEY_HEX]


def output_digest(reqs) -> str:
    return _digest(repr([canonical_requirement(r) for r in reqs]))


def capture(db) -> dict:
    from engine.target_solver import parse
    return {_digest(t): output_digest(parse(t))
            for t in sorted(pool_texts(db)) if "target" in t.lower()}


@pytest.fixture(scope="module")
def db():
    from tests._card_db_cache import shared_card_database
    return shared_card_database()


def test_target_solver_parse_is_unchanged_by_the_located_refactor_pool_wide(db):
    from engine.target_solver import parse
    pinned = json.loads(CAPTURE_PATH.read_text())["entries"]
    texts = pool_texts(db)
    checked, diffs = 0, []
    for t in texts:
        if "target" not in t.lower():
            continue
        want = pinned.get(_digest(t))
        if want is None:          # a text added by a later DB refresh
            continue
        checked += 1
        if output_digest(parse(t)) != want:
            diffs.append(t)
    # The capture must still describe the pool, or the pin is vacuous.
    assert checked >= 0.95 * len(pinned), (checked, len(pinned))
    assert not diffs, f"{len(diffs)} texts changed, e.g. {diffs[:3]}"


def test_a_text_without_the_word_target_yields_no_requirement(db):
    from engine.target_solver import parse
    hits = [t for t in pool_texts(db)
            if "target" not in t.lower() and parse(t)]
    assert hits == []


def test_parse_is_the_located_parse_without_its_positions(db):
    from engine.target_solver import parse, parse_located
    for t in pool_texts(db):
        if "target" in t.lower():
            assert parse(t) == [r for r, _ in parse_located(t)]


def test_parse_spans_places_each_requirement_on_its_printed_phrase(db):
    from engine.target_solver import _singularize_targets, parse_spans
    for t in pool_texts(db):
        if "target" not in t.lower():
            continue
        norm = _singularize_targets(t.lower())
        for r, start, end in parse_spans(t):
            if start < 0:
                assert end < 0
                continue
            assert norm[start:end] == r.raw_phrase, (t, r.raw_phrase)


def test_parse_spans_places_requirements_where_parse_counted_them(db):
    """Sentences whose requirements parse() returns out of printed order map
    to their printed positions, and a counted requirement's span is the
    occurrence its count was read before."""
    from engine.target_solver import _count_before, _singularize_targets, parse_spans
    out_of_order = 0
    for t in pool_texts(db):
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


def _by_scope(text):
    from engine.target_solver import parse_spans
    return {r.owner_scope: r for r, _, _ in parse_spans(text)}


def test_a_trailing_mana_value_ceiling_binds_to_the_occurrence_the_requirement_is_placed_at():
    """CR 601.2c: a printed "with mana value N/X or less" is part of the
    legality of the target phrase it follows. When a requirement's phrase
    also occurs inside a longer, earlier phrase, parse() places the
    requirement at its own occurrence; the ceiling is read there, never at
    the phrase's first occurrence (a second placement search)."""
    reqs = _by_scope("Return target creature you control to its owner's hand. "
                     "Destroy another target creature with mana value 3 or less.")
    assert reqs["any"].max_mana_value == 3
    assert reqs["you"].max_mana_value is None
    reqs = _by_scope("Return target creature you control with mana value 2 or "
                     "less to its owner's hand. Destroy another target creature.")
    assert reqs["you"].max_mana_value == 2
    assert reqs["any"].max_mana_value is None
    reqs = _by_scope("Return target creature you control to its owner's hand. "
                     "Destroy another target creature with mana value X or less.")
    assert reqs["any"].max_mana_value_is_x
    assert not reqs["you"].max_mana_value_is_x


def test_every_pool_mana_value_ceiling_is_read_at_its_requirements_placed_phrase(db):
    """Pool-wide: a battlefield requirement carries exactly the ceiling that
    follows the phrase parse_spans places it on, and none when unplaced."""
    from engine.target_solver import (_MV_BOUND_AFTER_RE, _singularize_targets,
                                      parse_spans)
    checked = 0
    for t in pool_texts(db):
        if "target" not in t.lower():
            continue
        norm = _singularize_targets(t.lower())
        for r, start, end in parse_spans(t):
            if r.zone != "battlefield":
                continue
            m = _MV_BOUND_AFTER_RE.match(norm[end:]) if start >= 0 else None
            is_x = bool(m) and m.group(1) == "x"
            n = int(m.group(1)) if m and not is_x else None
            assert (r.max_mana_value_is_x, r.max_mana_value) == (is_x, n), (t, r)
            checked += bool(m)
    assert checked > 0      # the pool has ceilings, or the pin is vacuous


def test_parse_spans_are_in_input_text_coordinates_when_lowering_changes_length():
    """A character whose lowercase form is longer (U+0130) must not shift
    the span of a later target phrase."""
    from engine.target_solver import parse_spans
    text = "İstanbul. Destroy target creature."
    (r, start, end), = parse_spans(text)
    assert text[start:end].lower() == r.raw_phrase


if __name__ == "__main__" and "--capture" in sys.argv:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from engine.card_database import CardDatabase
    entries = capture(CardDatabase())
    CAPTURE_PATH.write_text(json.dumps({
        "comment": ("target_solver.parse() output per pool text containing "
                    "'target': sha256(text)[:12] -> sha256(repr(canonical "
                    "requirements))[:12]. Captured before the parse_located "
                    "refactor (design doc 2026-09-29, F3/A20)."),
        "entries": entries,
    }, indent=0, sort_keys=True) + "\n")
    print(f"captured {len(entries)} texts")
