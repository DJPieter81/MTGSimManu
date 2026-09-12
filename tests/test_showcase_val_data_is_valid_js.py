"""The showcase's generated `valData` block must be valid JavaScript for
every registered deck name.

`build_showcase.build_val_data` wrote each entry as `{name:'<deck>', …,
detail:'<text>'}` with the deck name and the detail text pasted raw into
single-quoted JS literals. A deck name containing an apostrophe (Goryo's
Vengeance) ended the literal early: `name:'Goryo's Vengeance'` is a
SyntaxError, the whole inline script aborted, and every section built
after it — the heatmap, the deck profiles, the validation bars — rendered
empty (observed 2026-09-12 with headless Chromium; the committed 09-06
showcase carried the same line).

Rule: every string the builder writes into JS is a JSON-encoded literal.
"""
from __future__ import annotations

import json

import build_showcase


def _fake_D(names):
    return {'overall': [{'deck': n, 'win_rate': 50.0} for n in names]}


def test_a_deck_name_with_an_apostrophe_is_a_valid_js_string_literal(monkeypatch):
    names = ["Goryo's Vengeance", "Boros Energy"]
    monkeypatch.setattr(build_showcase, 'EXPECTED', {n: (30, 70) for n in names})
    js = build_showcase.build_val_data(_fake_D(names))
    assert "name:'Goryo's" not in js, "the apostrophe terminated the JS literal"
    assert f"name:{json.dumps(chr(71) + 'oryo' + chr(39) + 's Vengeance')}" in js
    # Every entry's name and detail are JSON literals: extract and decode them.
    for line in js.splitlines():
        if line.strip().startswith('{name:'):
            name_lit = line.split('name:', 1)[1].split(',wr:', 1)[0]
            detail_lit = line.split('detail:', 1)[1].rstrip('},')
            assert isinstance(json.loads(name_lit), str)
            assert isinstance(json.loads(detail_lit), str)


def test_every_registered_deck_name_survives_the_builder():
    from decks.modern_meta import get_all_deck_names
    names = get_all_deck_names()
    js = build_showcase.build_val_data(_fake_D(names))
    for n in names:
        if n in build_showcase.EXPECTED:
            assert f"name:{json.dumps(n)}" in js, n
