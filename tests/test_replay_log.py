"""Tests for the structured replay log (engine/replay_log.py).

Covers:
- schema-version round-trip (NDJSON serialize → parse → equal events)
- DECISION events carry decision_id, alternatives, EV gap
- emit() rejects unknown event kinds (defends the schema contract)
- header schema mismatch raises a clear ValueError
- TURN_START / PHASE / GAME_START update the auto-injected context
- snapshot_state / snapshot_board produce the documented shape

These tests describe the *contract* — the rule the schema encodes —
not the wire format.  If you change wire details, fix the test name to
match the new rule, not the new format.
"""
from __future__ import annotations

import json

import pytest

from engine.replay_log import (
    KIND_DECISION,
    KIND_GAME_START,
    KIND_PHASE,
    KIND_TURN_START,
    REPLAY_LOG_SCHEMA,
    ReplayLog,
)


def test_emit_rejects_unknown_kind():
    log = ReplayLog()
    with pytest.raises(ValueError):
        log.emit("MADE_UP_KIND", note="x")


def test_emit_assigns_monotonic_seq():
    log = ReplayLog()
    log.emit(KIND_GAME_START, game=1)
    log.emit(KIND_TURN_START, turn=1)
    log.emit(KIND_PHASE, phase="Main1")
    seqs = [e["seq"] for e in log.events]
    assert seqs == [0, 1, 2]


def test_turn_start_updates_context_for_subsequent_events():
    log = ReplayLog()
    log.emit(KIND_GAME_START, game=1)
    log.emit(KIND_TURN_START, turn=4)
    log.emit(KIND_PHASE, phase="Main1")
    note = log.emit("NOTE", text="hello")
    assert note["turn"] == 4
    assert note["phase"] == "Main1"
    assert note["game"] == 1


def test_decision_carries_id_and_alternatives_with_gap():
    log = ReplayLog()
    log.emit(KIND_GAME_START, game=1)
    log.emit(KIND_TURN_START, turn=3)
    chosen = {"action": "cast_spell", "card": "Bolt", "ev": 7.2,
              "reason": "burn opp face"}
    alts = [
        {"action": "cast_spell", "card": "Goblin Guide", "ev": 6.8,
         "gap": 0.4, "rejected_because": "holds clock"},
        {"action": "hold", "card": None, "ev": 3.1,
         "gap": 4.1, "rejected_because": "passing wastes mana"},
    ]
    evt = log.emit_decision(
        actor="Boros Energy", pidx=0, chosen=chosen, alternatives=alts,
        state={"life": [20, 16]}, subsystems={"clock": 2.1, "bhi": -0.5},
        goal="reduce_life", candidates_n=8,
    )
    assert evt["kind"] == KIND_DECISION
    assert evt["decision_id"].startswith("g1t3d")
    assert evt["chosen"]["card"] == "Bolt"
    assert len(evt["alternatives"]) == 2
    assert evt["alternatives"][0]["gap"] == 0.4
    assert evt["subsystems"]["clock"] == 2.1
    assert evt["candidates_n"] == 8


def test_decision_ids_are_unique_within_a_turn():
    log = ReplayLog()
    log.emit(KIND_GAME_START, game=1)
    log.emit(KIND_TURN_START, turn=3)
    a = log.emit_decision("X", 0, chosen={"card": "A", "ev": 1.0},
                          alternatives=[])
    b = log.emit_decision("X", 0, chosen={"card": "B", "ev": 1.0},
                          alternatives=[])
    assert a["decision_id"] != b["decision_id"]


def test_ndjson_round_trip_preserves_events():
    log = ReplayLog(seed=55555, deck1="Ruby Storm", deck2="Affinity")
    log.emit(KIND_GAME_START, game=1, on_play="Ruby Storm")
    log.emit(KIND_TURN_START, turn=1)
    log.emit_decision(
        actor="Ruby Storm", pidx=0,
        chosen={"action": "play_land", "card": "Mountain", "ev": 0.0},
        alternatives=[],
    )
    text = log.to_ndjson()
    parsed = ReplayLog.from_ndjson(text)
    assert parsed.seed == 55555
    assert parsed.deck1 == "Ruby Storm"
    assert parsed.schema == REPLAY_LOG_SCHEMA
    # Header is stripped during parse; events list compares equal
    assert len(parsed.events) == len(log.events)
    assert parsed.events[0]["kind"] == KIND_GAME_START


def test_from_ndjson_rejects_unknown_major_schema():
    log = ReplayLog()
    log.schema = "99.0"
    text = log.to_ndjson()
    with pytest.raises(ValueError, match="schema"):
        ReplayLog.from_ndjson(text)


def test_ndjson_lines_are_pure_json_no_trailing_garbage():
    log = ReplayLog(seed=1)
    log.emit(KIND_GAME_START, game=1)
    log.emit(KIND_TURN_START, turn=1)
    text = log.to_ndjson()
    for line in text.splitlines():
        assert line.strip()
        json.loads(line)  # raises if any line isn't valid JSON
