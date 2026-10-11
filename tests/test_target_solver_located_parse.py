"""Target placement has one owner: parse()'s own claimed-span bookkeeping.

Design doc 2026-09-29 (F3, A20). The clause grammar needs to know WHERE in a
clause each TargetRequirement was read, so it can keep the requirements whose
span lies inside a verb's object slot. That placement must come from the one
place that already decides it -- the claimed-span dict inside
`target_solver.parse()`, which is also where each requirement reads its count
("up to two target ...") and its mana-value ceiling -- never from a second
search that could land on a different occurrence of the same phrase.

Rules pinned here:
* `parse()` is `[r for r, _ in parse_located(text)]`;
* a text without the word "target" yields no requirement;
* `parse_spans` places each requirement on the printed phrase it was read
  from, in input-text coordinates;
* a trailing "with mana value N/X or less" ceiling is read at that same
  placed occurrence (CR 601.2c), never at a first-occurrence search.

Pinned under their E0 spec IDs elsewhere, with the helpers below:
`parse()` output unchanged against a capture taken before the refactor, over
every oracle text, line and sentence of the pool
(tests/test_effect_grammar_pool_invariants.py, fixture
`tests/fixtures/target_solver_parse_capture.json`), and counted requirements
placed where their count was read, out-of-printed-order sentences included
(tests/test_effect_grammar_participants.py).

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


# Pool-wide (every oracle text, line and sentence, ~53k). Measured 2026-09-30
# on this container (quiet, 4 cores): ~1.2 s for the body, plus ~16 s when it
# is the first test of the process to load the shared card DB. 120 s bounds a
# hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_a_text_without_the_word_target_yields_no_requirement(card_db):
    from engine.target_solver import parse
    hits = [t for t in pool_texts(card_db)
            if "target" not in t.lower() and parse(t)]
    assert hits == []


# Pool-wide (~17k pool texts containing "target"). Measured 2026-09-30 on
# this container (quiet, 4 cores): ~1.5 s for the body, plus ~16 s when it is
# the first test of the process to load the shared card DB. 120 s bounds a
# hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_parse_is_the_located_parse_without_its_positions(card_db):
    from engine.target_solver import parse, parse_located
    for t in pool_texts(card_db):
        if "target" in t.lower():
            assert parse(t) == [r for r, _ in parse_located(t)]


# Pool-wide (~17k pool texts containing "target"). Measured 2026-09-30 on
# this container (quiet, 4 cores): ~0.9 s for the body, plus ~16 s when it is
# the first test of the process to load the shared card DB. 120 s bounds a
# hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_parse_spans_places_each_requirement_on_its_printed_phrase(card_db):
    from engine.target_solver import _singularize_targets, parse_spans
    for t in pool_texts(card_db):
        if "target" not in t.lower():
            continue
        norm = _singularize_targets(t.lower())
        for r, start, end in parse_spans(t):
            if start < 0:
                assert end < 0
                continue
            assert norm[start:end] == r.raw_phrase, (t, r.raw_phrase)


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


# Pool-wide (~17k pool texts containing "target"). Measured 2026-09-30 on
# this container (quiet, 4 cores): ~0.9 s for the body, plus ~16 s when it is
# the first test of the process to load the shared card DB. 120 s bounds a
# hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_every_pool_mana_value_ceiling_is_read_at_its_requirements_placed_phrase(card_db):
    """Pool-wide: a battlefield requirement carries exactly the ceiling that
    follows the phrase parse_spans places it on, and none when unplaced."""
    from engine.target_solver import (_MV_BOUND_AFTER_RE, _singularize_targets,
                                      parse_spans)
    checked = 0
    for t in pool_texts(card_db):
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
