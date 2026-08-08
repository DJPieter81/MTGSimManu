"""Unit tests for tools/fetch_tier1_decklists.py — parsers only, no network.

All fixtures are trimmed HTML snippets under tests/fixtures/ mirroring the
real mtgtop8.com markup shapes observed 2026-08-08.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import fetch_tier1_decklists as fd  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


def test_parse_metagame_breakdown_excludes_category_headers_and_other_bucket():
    html = _read("mtgtop8_format_page.html")
    archetypes = fd.parse_metagame_breakdown(html)
    names = {a.name for a in archetypes}

    assert "Boros Aggro" in names
    assert "Dimir Control" in names
    # category totals ("AGGRO 34%") must not be captured as archetypes
    assert "AGGRO" not in names
    assert "CONTROL" not in names
    # long-tail bucket rows are noise, not real archetypes
    assert "Other - Control" not in names


def test_parse_metagame_breakdown_handles_slash_in_name():
    html = _read("mtgtop8_format_page.html")
    archetypes = {a.name: a.pct for a in fd.parse_metagame_breakdown(html)}
    assert archetypes["4/5c Aggro"] == 4.0


def test_parse_archetype_ids_maps_name_to_ids():
    html = _read("mtgtop8_format_page.html")
    ids = fd.parse_archetype_ids(html)
    assert ids["Boros Aggro"] == ("193", "54")
    assert ids["Affinity"] == ("189", "54")


def test_parse_archetype_events_dedupes_and_respects_limit():
    html = _read("mtgtop8_archetype_page.html")
    events = fd.parse_archetype_events(html, limit=2)
    assert events == [("89289", "877594"), ("89263", "877433")]


def test_parse_decklist_splits_main_and_sideboard():
    html = _read("mtgtop8_deck_page.html")
    deck = fd.parse_decklist(html, "Pinnacle Affinity", "89289", "877594")

    assert deck.mainboard == [
        (4, "Urza's Saga"),
        (4, "Kappa Cannoneer"),
        (4, "Mox Opal"),
    ]
    assert deck.sideboard == [
        (3, "Consign to Memory"),
        (2, "Swan Song"),
    ]


def test_format_decklist_text_is_import_deck_compatible():
    deck = fd.Decklist(
        archetype_name="Pinnacle Affinity", event_id="1", deck_id="2",
        mainboard=[(4, "Urza's Saga")], sideboard=[(3, "Consign to Memory")],
    )
    text = fd.format_decklist_text(deck)
    lines = [l for l in text.split("\n") if l and not l.startswith("//")]
    assert "4 Urza's Saga" in lines
    assert "Sideboard" in lines
    assert "3 Consign to Memory" in lines
    # Sideboard line must come after the mainboard card, matching import_deck.py's
    # expected 'Sideboard' header format (see parse_decklist in import_deck.py).
    assert lines.index("Sideboard") > lines.index("4 Urza's Saga")
    assert lines.index("3 Consign to Memory") > lines.index("Sideboard")


def test_slugify_produces_filesystem_safe_names():
    assert fd.slugify("4/5c Aggro") == "4_5c_aggro"
    assert fd.slugify("Goryo's Vengeance") == "goryo_s_vengeance"


def test_registered_deck_names_reads_top_level_keys_only(tmp_path):
    decks_dir = tmp_path / "decks"
    decks_dir.mkdir()
    (decks_dir / "modern_meta.py").write_text(
        'from typing import Dict\n'
        'MODERN_DECKS: Dict[str, dict] = {\n'
        '    "Boros Energy": {\n'
        '        "mainboard": {"Some Card": 4},\n'
        '    },\n'
        '    "Affinity": {\n'
        '        "mainboard": {"Other Card": 4},\n'
        '    },\n'
        '}\n'
    )
    names = fd.registered_deck_names(str(tmp_path))
    assert names == ["Boros Energy", "Affinity"]
    # must not pick up nested keys like "mainboard" or card names
    assert "mainboard" not in names
    assert "Some Card" not in names


def test_build_diff_report_flags_gaps_and_fuzzy_matches_substrings():
    tier1 = [
        fd.Archetype(name="Jeskai Blink", pct=10.0),
        fd.Archetype(name="Boros Aggro", pct=8.0),
    ]
    registered = ["Blink"]  # substring match should count as registered
    report = fd.build_diff_report(tier1, registered, top_n=2)
    assert "Jeskai Blink" in report
    assert "Boros Aggro" in report
    assert "**NO — gap**" in report  # Boros Aggro has no match
    assert "Gaps (1)" in report
