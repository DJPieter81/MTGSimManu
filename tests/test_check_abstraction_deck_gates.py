"""Tests for the deck-name-gate detector in tools/check_abstraction.py.

Class D from docs/design/2026-05-04_modern_combo_audit_methodology.md.

These tests build crafted Python files in a temporary directory and invoke
the AST visitor / scanner directly, so they do not depend on the real
`engine/` or `ai/` trees beyond the fixed list of known deck names parsed
from `ai/strategy_profile.py`.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402

# The script lives at tools/check_abstraction.py — load it as a module so the
# tests can call its internal functions directly.
_SCRIPT = ROOT / "tools" / "check_abstraction.py"
_spec = importlib.util.spec_from_file_location("check_abstraction", _SCRIPT)
ca = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
assert _spec is not None and _spec.loader is not None
_spec.loader.exec_module(ca)


KNOWN_DECKS = {
    "Ruby Storm",
    "Affinity",
    "Goryo's Vengeance",
    "Boros Energy",
    "Eldrazi Tron",
}


def _visit(src: str) -> list[tuple[int, str]]:
    """Run the deck-gate visitor against a synthetic source string."""
    tree = ast.parse(src)
    visitor = ca.DeckGateVisitor(src.splitlines(), KNOWN_DECKS)
    visitor.visit(tree)
    return visitor.hits


# ─── Detector unit tests (synthetic source) ────────────────────────────────


def test_detects_deck_name_attr_equality():
    src = 'def f(p):\n    if p.deck_name == "Ruby Storm": pass\n'
    assert len(_visit(src)) == 1


def test_detects_active_deck_attr_equality():
    src = 'def f(p):\n    if p.active_deck == "Affinity": pass\n'
    assert len(_visit(src)) == 1


def test_detects_deck_dot_name_with_known_deck():
    """`deck.name == "X"` flagged when receiver is named like a deck."""
    src = 'def f(deck):\n    if deck.name == "Boros Energy": pass\n'
    assert len(_visit(src)) == 1


def test_detects_strategy_dot_name_with_known_deck():
    src = 'def f(strategy):\n    if strategy.name == "Eldrazi Tron": pass\n'
    assert len(_visit(src)) == 1


def test_detects_deck_name_in_tuple():
    src = 'def f(p):\n    if p.deck_name in ("Ruby Storm", "Affinity"): pass\n'
    assert len(_visit(src)) == 1


def test_detects_deck_name_in_set():
    src = 'def f(p):\n    if p.deck_name in {"Ruby Storm", "Affinity"}: pass\n'
    assert len(_visit(src)) == 1


def test_detects_quoted_deck_in_attr_substring():
    """`"Ruby Storm" in p.deck_name` — fuzzy substring check."""
    src = 'def f(p):\n    if "Ruby Storm" in p.deck_name: pass\n'
    assert len(_visit(src)) == 1


def test_does_not_flag_enum_name_compare():
    """`archetype.name == "AGGRO"` is an enum compare, not a deck gate."""
    src = 'def f(a):\n    if a.name == "AGGRO": pass\n'
    assert _visit(src) == []


def test_does_not_flag_card_name_compare():
    """`card.template.name == "Lightning Bolt"` is the *card-name* detector's
    job — the deck-gate detector must not double-count it."""
    src = 'def f(c):\n    if c.template.name == "Lightning Bolt": pass\n'
    assert _visit(src) == []


def test_does_not_flag_card_name_with_known_deck_string():
    """Even if the literal happens to match a deck, a `card.name` access
    doesn't have a deck-receiver — must not flag."""
    src = 'def f(card):\n    if card.name == "Ruby Storm": pass\n'
    # `card` is not in DECK_RECEIVER_NAMES → not a deck gate.
    assert _visit(src) == []


def test_does_not_flag_unknown_deck_string_on_deck_receiver():
    """`deck.name == "Pioneer Belcher"` is not in DECK_ARCHETYPES — must not flag."""
    src = 'def f(deck):\n    if deck.name == "Pioneer Belcher": pass\n'
    assert _visit(src) == []


# ─── Allow-marker exemption ────────────────────────────────────────────────


def test_abstraction_allow_comment_exempts_line():
    src = (
        'def f(p):\n'
        '    if p.deck_name == "Ruby Storm":  # abstraction-allow: test\n'
        '        pass\n'
    )
    assert _visit(src) == []


# ─── Repo-level scan tests (use temp repos via monkeypatch) ────────────────


def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Build a fake repo: tmp_path/ai/, tmp_path/engine/, plus
    a stub `ai/strategy_profile.py` providing DECK_ARCHETYPES."""
    ai = tmp_path / "ai"
    eng = tmp_path / "engine"
    ai.mkdir()
    eng.mkdir()
    # Stub strategy_profile.py — supplies known deck names.
    (ai / "strategy_profile.py").write_text(
        "DECK_ARCHETYPES = {\n"
        '    "Ruby Storm": "combo",\n'
        '    "Affinity": "aggro",\n'
        '    "Boros Energy": "aggro",\n'
        "}\n"
    )
    # Place the user-supplied files under ai/ and engine/ as requested.
    for relpath, src in files.items():
        target = tmp_path / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src)
    return tmp_path


def test_scan_finds_deck_gate_in_ai(tmp_path, monkeypatch):
    src = (
        "def is_storm(player):\n"
        '    return player.deck_name == "Ruby Storm"\n'
    )
    repo = _make_repo(tmp_path, {"ai/some_module.py": src})
    monkeypatch.setattr(ca, "ROOT", repo)
    monkeypatch.setattr(ca, "SCAN_DIRS", [repo / "engine", repo / "ai"])

    hits = ca.find_deck_gate_hits()
    # Exactly 1 hit, in the ai/ module (NOT in the strategy_profile.py stub).
    paths = [str(p) for p, _, _ in hits]
    assert len(hits) == 1, f"unexpected hits: {hits}"
    assert paths[0].endswith("ai/some_module.py")


def test_scan_finds_deck_gate_in_engine(tmp_path, monkeypatch):
    src = (
        "def f(player):\n"
        '    if player.active_deck == "Affinity":\n'
        "        return 1\n"
    )
    repo = _make_repo(tmp_path, {"engine/some_module.py": src})
    monkeypatch.setattr(ca, "ROOT", repo)
    monkeypatch.setattr(ca, "SCAN_DIRS", [repo / "engine", repo / "ai"])

    hits = ca.find_deck_gate_hits()
    assert len(hits) == 1
    assert str(hits[0][0]).endswith("engine/some_module.py")


def test_scan_ignores_decks_module(tmp_path, monkeypatch):
    """Files outside engine/ and ai/ (e.g. decks/*) are NOT scanned."""
    src = (
        "def f(p):\n"
        '    if p.deck_name == "Ruby Storm": pass\n'
    )
    repo = _make_repo(tmp_path, {"decks/modern_meta.py": src})
    monkeypatch.setattr(ca, "ROOT", repo)
    monkeypatch.setattr(ca, "SCAN_DIRS", [repo / "engine", repo / "ai"])

    hits = ca.find_deck_gate_hits()
    # Only the strategy_profile.py stub exists in ai/; that doesn't have a
    # gate (it's a dict literal). No hits expected.
    assert hits == []


def test_scan_ignores_test_files(tmp_path, monkeypatch):
    """Files under tests/ are NOT in SCAN_DIRS, so deck-name compares in
    test fixtures don't count against the ratchet."""
    src = (
        "def f(p):\n"
        '    if p.deck_name == "Ruby Storm": pass\n'
    )
    repo = _make_repo(tmp_path, {"tests/test_thing.py": src})
    monkeypatch.setattr(ca, "ROOT", repo)
    monkeypatch.setattr(ca, "SCAN_DIRS", [repo / "engine", repo / "ai"])

    assert ca.find_deck_gate_hits() == []


def test_existing_card_name_detector_unaffected(tmp_path, monkeypatch):
    """Regression: the original card-name detector must continue to fire."""
    src = 'def f(card):\n    if card.name == "Lightning Bolt":\n        return 1\n'
    repo = _make_repo(tmp_path, {"ai/scoring.py": src})
    monkeypatch.setattr(ca, "ROOT", repo)
    monkeypatch.setattr(ca, "SCAN_DIRS", [repo / "engine", repo / "ai"])

    hits = ca.find_hits()  # card-name detector
    assert len(hits) == 1
    assert "Lightning Bolt" in hits[0][2]


# ─── Baseline / ratchet integration ────────────────────────────────────────


def test_baseline_at_current_count():
    """The deck_gate_count in the real baseline matches the real scan."""
    real_hits = ca.find_deck_gate_hits()
    baseline = ca.load_deck_gate_baseline()
    assert len(real_hits) == baseline, (
        f"deck_gate_count baseline ({baseline}) does not match current "
        f"scan ({len(real_hits)} hits). Update tools/abstraction_baseline.json "
        f"or fix the regression. Hits: {real_hits}"
    )


def test_baseline_file_has_deck_gate_field():
    """The baseline JSON declares deck_gate_count explicitly."""
    raw = json.loads((ROOT / "tools" / "abstraction_baseline.json").read_text())
    assert "deck_gate_count" in raw
    assert isinstance(raw["deck_gate_count"], int)
    assert raw["deck_gate_count"] >= 0


def test_ratchet_passes_at_baseline():
    """Running the script as-is exits 0 against the real tree."""
    rc = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert rc.returncode == 0, (
        f"check_abstraction.py failed at baseline:\n"
        f"stdout: {rc.stdout}\nstderr: {rc.stderr}"
    )


def test_ratchet_fails_on_increase(tmp_path, monkeypatch):
    """Synthetic +1 in a temp `ai/` dir trips the ratchet."""
    src = (
        "def f(player):\n"
        '    return player.deck_name == "Ruby Storm"\n'
    )
    repo = _make_repo(tmp_path, {"ai/new_module.py": src})
    baseline_file = repo / "tools" / "abstraction_baseline.json"
    baseline_file.parent.mkdir(parents=True, exist_ok=True)
    baseline_file.write_text(json.dumps({
        "hardcoded_name_count": 0,
        "deck_gate_count": 0,
    }))

    monkeypatch.setattr(ca, "ROOT", repo)
    monkeypatch.setattr(ca, "SCAN_DIRS", [repo / "engine", repo / "ai"])
    monkeypatch.setattr(ca, "BASELINE_FILE", baseline_file)

    rc = ca.main([])
    assert rc == 1, "ratchet should fail when deck_gate_count exceeds baseline"


def test_list_mode_prints_both_sections(tmp_path, monkeypatch, capsys):
    """`--list` prints both card-name and deck-gate sections."""
    src_card = 'def f(c):\n    if c.name == "Lightning Bolt": pass\n'
    src_deck = 'def f(p):\n    if p.deck_name == "Ruby Storm": pass\n'
    repo = _make_repo(tmp_path, {
        "ai/cards.py": src_card,
        "ai/decks.py": src_deck,
    })
    baseline_file = repo / "tools" / "abstraction_baseline.json"
    baseline_file.parent.mkdir(parents=True, exist_ok=True)
    baseline_file.write_text(json.dumps({
        "hardcoded_name_count": 1,
        "deck_gate_count": 1,
    }))

    monkeypatch.setattr(ca, "ROOT", repo)
    monkeypatch.setattr(ca, "SCAN_DIRS", [repo / "engine", repo / "ai"])
    monkeypatch.setattr(ca, "BASELINE_FILE", baseline_file)

    rc = ca.main(["--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Card-name hits" in out
    assert "Deck-name-gate hits" in out
    assert "Lightning Bolt" in out
    assert "Ruby Storm" in out
