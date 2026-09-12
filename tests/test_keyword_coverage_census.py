"""The mechanic coverage census is derived from the code, and the
committed table is what the tool generates.

- A word with a `Keyword` enum member is "enum"; a word whose typed
  field the loader populates is "typed field"; a word with neither, on a
  registered-deck card with no dedicated handler, is "none".
- `docs/design/rules_coverage.md` carries frontmatter and the same rows
  the tool produces now (a stale table is a lie about coverage).
"""
from __future__ import annotations

import os
import re

from decks.modern_meta import MODERN_DECKS
from tools import keyword_coverage as kc


def _rows(card_db):
    return {r["word"]: r for r in kc.build_rows(card_db, MODERN_DECKS)}


def test_status_is_derived_from_the_engines_own_vocabulary(card_db):
    rows = _rows(card_db)
    assert rows["first strike"]["status"] == "enum"
    assert rows["ward"]["status"].startswith("typed field")
    assert rows["flashback"]["status"].startswith("typed field")
    assert rows["ferocious"]["status"] == "none", rows["ferocious"]
    assert rows["ferocious"]["deck_cards"] >= 1, "Stubborn Denial is registered"


def test_registered_deck_usage_counts_copies_across_mainboard_and_sideboard(card_db):
    rows = _rows(card_db)
    # Domain Zoo alone runs four Stubborn Denial; the count is copies, not cards.
    assert rows["ferocious"]["deck_copies"] >= 4


def test_the_committed_table_matches_the_generator(card_db):
    path = kc.DOC_PATH
    assert os.path.exists(path), "run python tools/keyword_coverage.py"
    text = open(path).read()
    assert text.startswith("---\ntitle:") and "\nstatus: active\n" in text
    generated = kc.render(kc.build_rows(card_db, MODERN_DECKS), len(MODERN_DECKS))
    strip = lambda s: "\n".join(l for l in s.splitlines()
                                if not re.match(r"^(session: |Generated )", l))
    assert strip(text) == strip(generated), (
        "docs/design/rules_coverage.md is stale — regenerate it")
