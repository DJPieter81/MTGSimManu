"""Tests for build_replay.py — NDJSON parser + HTML renderer.

Covers:
- format sniffing distinguishes NDJSON header vs legacy text
- parse_ndjson groups events by game and attaches results
- render_html produces a non-empty HTML string with the seed,
  decision_id anchors, and EV numbers visible in the output

The HTML render is checked by string-search rather than DOM parsing —
the goal is "the visible artifact contains the data," not pixel
fidelity.  If the renderer changes, fix the test for the rule, not
the surface form.
"""
from __future__ import annotations

import json

from build_replay import parse_ndjson, render_html, sniff_format
from engine.replay_log import (
    KIND_DECISION,
    KIND_GAME_END,
    KIND_GAME_START,
    KIND_TURN_START,
    ReplayLog,
)


def _build_minimal_log() -> str:
    log = ReplayLog(seed=42, deck1="Storm", deck2="Affinity")
    log.emit(KIND_GAME_START, game=1, on_play="Storm",
             deck1="Storm", deck2="Affinity")
    log.emit(KIND_TURN_START, turn=1, actor="Storm", pidx=0,
             state={"life": [20, 20]},
             board=[
                 {"creatures": [], "lands": [], "other": [],
                  "hand_size": 7, "library": 53, "graveyard": 0, "life": 20},
                 {"creatures": [], "lands": [], "other": [],
                  "hand_size": 7, "library": 53, "graveyard": 0, "life": 20},
             ])
    log.emit_decision(
        actor="Storm", pidx=0,
        chosen={"action": "cast_spell", "card": "Pyretic Ritual",
                "ev": 5.0, "reason": "ramp into chain",
                "targets": []},
        alternatives=[
            {"action": "play_land", "card": "Mountain", "ev": 0.5,
             "gap": 4.5, "rejected_because": "low EV"},
        ],
        state={"life": [20, 20]}, candidates_n=2,
    )
    log.emit(KIND_GAME_END, game=1, winner="Storm", winner_idx=0,
             turns=1, win_condition="combo", life=[20, 0])
    return log.to_ndjson()


def test_sniff_format_detects_ndjson_header():
    text = _build_minimal_log()
    assert sniff_format(text) == "ndjson"


def test_sniff_format_falls_back_to_text_for_legacy_log():
    text = "╔══ TURN 1 ══╗\nT1 P1: Cast Lightning Bolt\n"
    assert sniff_format(text) == "text"


def test_sniff_format_text_for_empty_input():
    assert sniff_format("") == "text"
    assert sniff_format("   \n\n") == "text"


def test_parse_ndjson_groups_events_into_games():
    text = _build_minimal_log()
    model = parse_ndjson(text)
    assert model["header"]["seed"] == 42
    assert len(model["games"]) == 1
    g = model["games"][0]
    assert g["number"] == 1
    assert g["on_play"] == "Storm"
    assert g["result"]["winner"] == "Storm"
    kinds = [e["kind"] for e in g["events"]]
    assert "TURN_START" in kinds
    assert "DECISION" in kinds


def test_render_html_includes_decision_anchor_and_ev():
    text = _build_minimal_log()
    model = parse_ndjson(text)
    html = render_html(model, seed=42)
    assert "<!doctype html>" in html
    assert "Storm" in html and "Affinity" in html
    assert "seed 42" in html
    # The decision_id from emit_decision is g1t1d0 — should appear as
    # both an id= and a #-anchor.
    assert "g1t1d0" in html
    # EV numbers should render as fixed-decimal strings.
    assert "5.00" in html
    # Feedback form must be present.
    assert "Export feedback" in html
    assert "thumbs up" in html or "👍" in html


def test_render_html_handles_empty_alternatives():
    log = ReplayLog(seed=1, deck1="A", deck2="B")
    log.emit(KIND_GAME_START, game=1, on_play="A", deck1="A", deck2="B")
    log.emit(KIND_TURN_START, turn=1, actor="A", pidx=0,
             state={"life": [20, 20]})
    log.emit_decision(actor="A", pidx=0,
                      chosen={"action": "cast_spell", "card": "Bolt",
                              "ev": 1.0, "reason": ""},
                      alternatives=[], candidates_n=1)
    log.emit(KIND_GAME_END, game=1, winner="A", winner_idx=0,
             turns=1, win_condition="damage", life=[20, 0])
    text = log.to_ndjson()
    model = parse_ndjson(text)
    html = render_html(model, seed=1)
    # No alternatives → no "runner-ups" section.
    assert "runner-ups" not in html


def test_parse_ndjson_skips_blank_lines():
    text = _build_minimal_log()
    text = text.replace("\n", "\n\n")
    model = parse_ndjson(text)
    assert len(model["games"]) == 1


def test_render_html_does_not_double_escape_card_names():
    log = ReplayLog(seed=1, deck1="A", deck2="B")
    log.emit(KIND_GAME_START, game=1, on_play="A", deck1="A", deck2="B")
    log.emit(KIND_TURN_START, turn=1, actor="A", pidx=0,
             state={"life": [20, 20]})
    log.emit_decision(actor="A", pidx=0,
                      chosen={"action": "cast_spell",
                              "card": "Urza's Saga",
                              "ev": 1.0, "reason": ""},
                      alternatives=[], candidates_n=1)
    log.emit(KIND_GAME_END, game=1, winner="A", winner_idx=0,
             turns=1, win_condition="damage", life=[20, 0])
    text = log.to_ndjson()
    model = parse_ndjson(text)
    html = render_html(model, seed=1)
    # The apostrophe should appear once (escaped), not double-escaped
    # to &amp;#39; — the exact form is "Urza's Saga" or "Urza&#39;s
    # Saga"; both are acceptable.  We just check we don't see &amp;.
    assert "&amp;#" not in html
    # data-card attribute should contain the card name (apostrophe ok)
    assert "data-card=\"Urza" in html
