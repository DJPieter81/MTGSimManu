"""The matrix dashboard and the showcase are built from ONE typed data file
by ONE generator (tools/meta_site).

Rules pinned:
* no deck present in a matrix results file is ever dropped, including a
  deck the previous site data never saw;
* a deck's archetype is read from its gameplan file, never from a table in
  Python source;
* flat and weighted win rates are computed once, in the data step, by the
  documented formula (weighted = mean of the deck's cells weighted by each
  opponent's meta share, opponents with no share excluded);
* drawn and aborted matches are carried per cell and in total;
* a deck's band verdict reads tools/calibration_bands.json;
* the pages embed the data as one JSON block, so a deck name with an
  apostrophe can never break a script;
* the generator writes inside the repository it is given (a run from a
  worktree must not overwrite another checkout);
* metagame_data.jsx is still produced, with every deck, for its readers
  (build_guide.py, meta_audit.py).
Deck names are fixture carriers only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools.meta_site import build_data, render
from tools.meta_site.schema import SiteData

DECKS = ["Alpha Aggro", "Bravo's Control", "Charlie Combo"]


def _results(names=DECKS, n=20):
    # matrix[d1|d2] = % of the n matches d1 won; draws / aborts per cell.
    m = {
        "Alpha Aggro|Bravo's Control": 60, "Bravo's Control|Alpha Aggro": 35,
        "Alpha Aggro|Charlie Combo": 40, "Charlie Combo|Alpha Aggro": 60,
        "Bravo's Control|Charlie Combo": 70, "Charlie Combo|Bravo's Control": 30,
    }
    draws = {k: 0 for k in m}
    draws["Alpha Aggro|Bravo's Control"] = 1
    draws["Bravo's Control|Alpha Aggro"] = 1
    aborted = {k: 0 for k in m}
    extra = [x for x in names if x not in DECKS]
    for x in extra:
        for o in DECKS:
            for a, b in ((x, o), (o, x)):
                m[f"{a}|{b}"] = 50
                draws[f"{a}|{b}"] = 0
                aborted[f"{a}|{b}"] = 0
    return {
        "type": "matrix", "timestamp": "2026-10-03T00:00:00", "names": list(names),
        "matrix": m, "cell_draws": draws, "cell_aborted": aborted,
        "n_games": n, "format": "bo3", "draws": sum(draws.values()) // 2,
        "aborted": 0, "seed_geometry": {"grid": "parallel-matrix",
                                        "seed_start": 50000, "step": 500},
        "generated_by": {"command": "fixture"},
    }


def _repo(tmp_path, names=DECKS, archetypes=None, shares=None):
    root = tmp_path / "repo"
    (root / "decks" / "gameplans").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "templates").mkdir()
    archetypes = archetypes or {"Alpha Aggro": "aggro",
                                "Bravo's Control": "control",
                                "Charlie Combo": "combo"}
    for name, arch in archetypes.items():
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        (root / "decks" / "gameplans" / f"{slug}.json").write_text(
            json.dumps({"deck_name": name, "archetype": arch}))
    (root / "tools" / "calibration_bands.json").write_text(json.dumps({
        "schema": 1.0,
        "field_bands": [{"deck": "Alpha Aggro", "expected_wr": [55, 70],
                         "provenance": "fixture"}],
        "default_field_band": {"expected_wr": [30, 70], "provenance": "fixture"},
        "matchup_bands": [],
    }))
    (root / "metagame_results.json").write_text(json.dumps(_results(names)))
    shares = shares if shares is not None else {
        "Alpha Aggro": 10.0, "Bravo's Control": 5.0, "Charlie Combo": 0.0}
    return root, shares


def _site(tmp_path, **kw):
    root, shares = _repo(tmp_path, **kw)
    return build_data.build_site_data(root, shares=shares), root


def test_every_deck_in_a_results_file_appears_in_the_site_data(tmp_path):
    names = DECKS + ["Delta Newcomer"]
    data, _ = _site(tmp_path, names=names,
                    archetypes={"Alpha Aggro": "aggro", "Bravo's Control": "control",
                                "Charlie Combo": "combo", "Delta Newcomer": "midrange"})
    assert [d.name for d in data.decks] == names
    assert len(data.cells) == len(names) * (len(names) - 1)


def test_a_decks_archetype_is_read_from_its_gameplan_file(tmp_path):
    data, _ = _site(tmp_path, archetypes={"Alpha Aggro": "tempo",
                                          "Bravo's Control": "control"})
    by = {d.name: d for d in data.decks}
    assert by["Alpha Aggro"].archetype == "tempo"
    assert by["Bravo's Control"].archetype == "control"
    assert by["Charlie Combo"].archetype == "unknown"   # no gameplan file
    src = Path(build_data.__file__).read_text() + Path(render.__file__).read_text()
    for name in DECKS:
        assert name not in src, "deck knowledge must live in data, not source"


def test_flat_and_weighted_win_rates_follow_the_documented_formula(tmp_path):
    data, _ = _site(tmp_path)
    by = {d.name: d for d in data.decks}
    # Alpha: cells 60 (vs Bravo, share 5) and 40 (vs Charlie, share 0).
    assert by["Alpha Aggro"].flat_wr == pytest.approx(50.0)
    assert by["Alpha Aggro"].weighted_wr == pytest.approx(60.0)
    # Bravo: 35 vs Alpha (share 10), 70 vs Charlie (share 0).
    assert by["Bravo's Control"].flat_wr == pytest.approx(52.5)
    assert by["Bravo's Control"].weighted_wr == pytest.approx(35.0)


def test_drawn_and_aborted_matches_are_carried_per_cell_and_in_total(tmp_path):
    data, _ = _site(tmp_path)
    cell = data.cell("Alpha Aggro", "Bravo's Control")
    assert (cell.wins, cell.draws, cell.aborted, cell.n) == (12, 1, 0, 20)
    assert data.provenance.draws == 1 and data.provenance.aborted == 0


def test_a_decks_band_verdict_reads_the_calibration_bands(tmp_path):
    data, _ = _site(tmp_path)
    by = {d.name: d for d in data.decks}
    assert by["Alpha Aggro"].band == (55.0, 70.0)
    assert by["Alpha Aggro"].band_verdict == "below"      # 50.0 < 55
    assert by["Bravo's Control"].band == (30.0, 70.0)     # default band
    assert by["Bravo's Control"].band_verdict == "in"


def _data_block(html):
    m = re.search(r'<script type="application/json" id="site-data">(.*?)</script>',
                  html, re.S)
    assert m, "the page carries one JSON data block"
    return json.loads(m.group(1))


@pytest.mark.parametrize("page", ["matrix", "showcase"])
def test_a_page_embeds_its_data_as_one_json_block_every_deck_once(tmp_path, page):
    data, _ = _site(tmp_path)
    html = render.render_page(page, data)
    block = _data_block(html)
    assert [d["name"] for d in block["decks"]] == DECKS
    assert "/*@TOKENS*/" not in html and "<!--@DATA-->" not in html, \
        "every template marker is filled"
    # The apostrophe name never appears inside executable script source.
    for script in re.findall(r"<script(?![^>]*application/json)[^>]*>(.*?)</script>",
                             html, re.S):
        assert "Bravo's" not in script


def test_the_generator_writes_inside_the_repository_it_is_given(tmp_path):
    data, root = _site(tmp_path)
    written = render.render_all(data, root)
    assert written, "pages were written"
    for p in written:
        assert Path(p).resolve().is_relative_to(root.resolve()), p
    assert (root / "modern_meta_matrix_full.html").exists()
    assert (root / "templates" / "reference_showcase.html").exists()
    assert (root / "mtgsimmanu_showcase.html").exists()


def test_the_legacy_jsx_is_still_produced_with_every_deck(tmp_path):
    data, root = _site(tmp_path)
    render.render_all(data, root)
    text = (root / "metagame_data.jsx").read_text()
    body = text[text.index("const D = ") + len("const D = "):text.index(";\nconst N")]
    D = json.loads(body)
    assert D["decks"] == DECKS
    assert D["matches_per_pair"] == 20
    assert len(D["wins"]) == len(DECKS)


def test_the_committed_site_data_validates_against_the_schema():
    path = Path(__file__).resolve().parents[1] / "data" / "meta_site.json"
    data = SiteData.model_validate_json(path.read_text())
    from decks.modern_meta import MODERN_DECKS
    assert {d.name for d in data.decks} == set(MODERN_DECKS)


def test_a_partial_matrix_run_never_replaces_the_site_data(tmp_path):
    """A --decks N run covers only some decks; merging it would drop every
    other deck from the pages. The merge refuses it and writes nothing."""
    import build_dashboard
    data, root = _site(tmp_path)
    build_data.write_site_data(data, root)
    before = (root / "data" / "meta_site.json").read_text()
    partial = _results(names=DECKS[:2])
    partial["matrix"] = {k: v for k, v in partial["matrix"].items()
                         if "Charlie Combo" not in k}
    (root / "metagame_results.json").write_text(json.dumps(partial))
    assert build_dashboard.merge(root=root) == []
    assert (root / "data" / "meta_site.json").read_text() == before


def test_the_provenance_command_is_shown_relative_to_the_repository(tmp_path):
    """A run on a CI runner records its absolute script path; the pages show
    the command from the repository root, never a machine's directories."""
    root, shares = _repo(tmp_path)
    res = json.loads((root / "metagame_results.json").read_text())
    res["generated_by"] = {"command": "/home/runner/work/R/R/tools/resumable_matrix.py -n 60"}
    (root / "metagame_results.json").write_text(json.dumps(res))
    data = build_data.build_site_data(root, shares=shares)
    assert data.provenance.command == "tools/resumable_matrix.py -n 60"


def test_the_audit_count_is_read_from_the_results_it_describes(tmp_path):
    """The violation count shown is the one the results file records for its
    own run; an audit file left from another run is never counted."""
    root, shares = _repo(tmp_path)
    res = json.loads((root / "metagame_results.json").read_text())
    res["rules_audit"] = {"violations": 119, "findings": 626}
    (root / "metagame_results.json").write_text(json.dumps(res))
    (root / "audits").mkdir()
    (root / "audits" / "rules_audit_20200101T000000Z.jsonl").write_text(
        json.dumps({"kind": "census"}) + "\n")
    assert build_data.build_site_data(root, shares=shares).provenance \
        .rules_audit_violations == 119
    del res["rules_audit"]
    (root / "metagame_results.json").write_text(json.dumps(res))
    assert build_data.build_site_data(root, shares=shares).provenance \
        .rules_audit_violations is None, "an unaudited run shows no count"


# ── The rules foundation, read from the tree (tools/meta_site/foundation) ──

def test_the_ratchet_board_reads_each_baseline_it_finds(tmp_path):
    """Each ratchet's count is its baseline file's, with the direction CI
    allows; a ratchet whose baseline is absent is left out, never guessed."""
    from tools.meta_site.foundation import ratchet_board
    root, _ = _repo(tmp_path)
    (root / "tools" / "card_name_registry_baseline.json").write_text(
        json.dumps({"total": 5, "description": "fixture"}))
    (root / "tools" / "abstraction_baseline.json").write_text(
        json.dumps({"hardcoded_name_count": 0, "deck_gate_count": 0,
                    "_comment": "fixture"}))
    (root / "tools" / "single_owner_baseline.json").write_text(
        json.dumps({"_comment": "x", "target_pick": 1, "damage_write": 2}))
    rows = {r["key"]: r for r in ratchet_board(root)}
    assert set(rows) == {"registry", "names", "deck_gates", "single_owner"}
    assert (rows["registry"]["now"], rows["registry"]["rule"]) == (5, "may only fall")
    assert (rows["names"]["now"], rows["names"]["rule"]) == (0, "pinned at 0")
    assert rows["single_owner"]["now"] == 3, "a category baseline is summed"


def test_the_audit_rule_ids_are_read_from_the_engine_source(tmp_path):
    """An id passed to census(...) or ending in _unresolved is a census row;
    every other CR-phrased id is an invariant, counted by CR chapter."""
    from tools.meta_site.foundation import audit_rules
    root, _ = _repo(tmp_path)
    (root / "engine").mkdir()
    (root / "engine" / "x.py").write_text(
        'rules_audit.check("101.2/cast_limit", ok)\n'
        'rules_audit.check("603.2/step_trigger_once", ok)\n'
        'rules_audit.census("608.2/uncountable_scaler", key)\n'
        'rule = "603.2/attack_trigger_unresolved"\n')
    a = audit_rules(root)
    assert a["invariants"] == ["101.2/cast_limit", "603.2/step_trigger_once"]
    assert a["census"] == ["603.2/attack_trigger_unresolved",
                           "608.2/uncountable_scaler"]
    assert [(c["chapter"], c["count"]) for c in a["by_chapter"]] == [("1", 1), ("6", 1)]


def test_the_unit_ledger_is_read_from_the_tracker_headings(tmp_path):
    """A heading "## Unit <code>: <rule> (<CR cites>; <date>)" is one unit;
    a heading without a date in its last parenthesis is not."""
    from tools.meta_site.foundation import ledger
    root, _ = _repo(tmp_path)
    (root / "docs" / "design").mkdir(parents=True)
    (root / "docs" / "design" / "rules-foundation-sweep-tracker.md").write_text(
        "## Unit AB: a rule with (a parenthesis) in it (CR 101.2, 603.3b; 2026-10-25)\n"
        "## Unit CD — a choice the AI makes (AI, 2026-10-24)\n"
        "## Units EF and GH: two units in one heading (CR 702.21a; 2026-10-23)\n"
        "## Unit XY: no date here (CR 101.2)\n"
        "### Unit ZZ: a sub-heading is not a unit (2026-10-22)\n")
    units = ledger(root)
    assert [(u["code"], u["date"]) for u in units] == [
        ("AB", "2026-10-25"), ("CD", "2026-10-24"), ("EF and GH", "2026-10-23")]
    assert units[0]["rule"] == "a rule with (a parenthesis) in it"
    assert units[0]["cr"] == ["101.2", "603.3b"]
    assert (units[0]["ai"], units[1]["ai"]) == (False, True)


def test_the_choice_points_name_who_answers_each_engine_question(tmp_path):
    """Every method of the callbacks protocol is a choice point; the AI
    callbacks' method names the ai.* functions it calls, or the seat's own
    AI; a method the AI does not override is the engine's default."""
    from tools.meta_site.foundation import choice_points
    root, _ = _repo(tmp_path)
    (root / "engine").mkdir()
    (root / "engine" / "callbacks.py").write_text(
        "class GameCallbacks(Protocol):\n"
        "    def choose_cards(self, ctx, spec, pool, n):\n"
        "        '''Pick up to `n` cards. More words.'''\n"
        "    def choose_amount(self, ctx, spec, lo, hi):\n"
        "        '''Pick an amount in [lo, hi].'''\n"
        "    def choose_cast_targets(self, game, idx, card):\n"
        "        '''Which targets does a spell take?'''\n")
    (root / "engine" / "game_runner.py").write_text(
        "from ai.board_eval import evaluate_action, Action\n"
        "class AICallbacks(GameCallbacks):\n"
        "    def choose_cards(self, ctx, spec, pool, n):\n"
        "        from ai.resolution_choices import pick_cards\n"
        "        return pick_cards(ctx.game, ctx, spec, pool, n)\n"
        "    def choose_cast_targets(self, game, idx, card):\n"
        "        return getattr(self, '_players', {}).get(idx)._choose_targets(game, card)\n")
    rows = {r["method"]: r for r in choice_points(root)}
    assert rows["choose_cards"]["by"] == "ai"
    assert rows["choose_cards"]["answers"] == ["ai.resolution_choices.pick_cards"]
    assert rows["choose_cards"]["asks"] == "Pick up to `n` cards."
    assert rows["choose_amount"]["by"] == "default"
    assert rows["choose_cast_targets"]["by"] == "player"


def test_the_site_data_carries_the_trace_and_the_foundation(tmp_path):
    """The trace file is content like the narrative; the foundation is
    computed at build time, and the layer files' line counts are the
    tree's."""
    root, shares = _repo(tmp_path)
    content = root / "tools" / "meta_site" / "content"
    content.mkdir(parents=True)
    (content / "trace.json").write_text(json.dumps({"commit": "abc1234", "cards": []}))
    (content / "architecture.json").write_text(json.dumps(
        {"layers": [{"files": [{"path": "tools/calibration_bands.json"}]}]}))
    data = build_data.build_site_data(root, shares=shares)
    assert data.content.trace == {"commit": "abc1234", "cards": []}
    f = data.content.foundation
    assert set(f) >= {"ratchets", "proofs", "audit", "ledger", "choices", "lines"}
    lines = len((root / "tools" / "calibration_bands.json").read_text().splitlines())
    assert f["lines"] == {"tools/calibration_bands.json": lines}
