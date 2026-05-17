"""Tests for the RESPONSE_DECISION event kind (W0-H).

Covers the contract that counter / removal / discard *response*
decisions appear in the NDJSON replay stream as a sibling event to
the main-phase DECISION event, so reviewers can see *which* spell on
the stack the AI chose to counter (or chose to let resolve) without
having to re-run the simulation.

Rule-phrased tests:
  - RESPONSE_DECISION carries the stack_item (the spell being
    responded to) so the reviewer can verify the AI inspected the
    right threat.
  - A choice to pass (no response) still emits a RESPONSE_DECISION —
    its kind must distinguish it from a main-phase DECISION even
    when the action is "pass".
  - Both event kinds share the BaseDecision schema
    (decision_id, chosen, alternatives) so a single renderer can
    handle both.
  - Legacy replays (pre-W0-H, no RESPONSE_DECISION events) still
    parse without raising — the event is purely additive.
  - build_replay.py renders RESPONSE_DECISION with a distinct CSS
    class so reviewers can scan for them in the HTML.

These tests describe the *contract* the schema encodes, not the
exact wire format.  If the wire format changes, rename the test
to match the new rule, not the new bytes.
"""
from __future__ import annotations

import json

import pytest

from build_replay import parse_ndjson, render_html
from engine.replay_log import (
    KIND_DECISION,
    KIND_GAME_END,
    KIND_GAME_START,
    KIND_RESPONSE_DECISION,
    KIND_TURN_START,
    ReplayLog,
)


# ─── Helpers ────────────────────────────────────────────────────


def _build_log_with_response(
    *,
    chosen_action: str = "cast_spell",
    chosen_card: str = "Counterspell",
    stack_item_name: str = "Cranial Plating",
    stack_item_cost: int = 2,
) -> ReplayLog:
    """Synthesize an NDJSON log containing one RESPONSE_DECISION."""
    log = ReplayLog(seed=42, deck1="Azorius Control", deck2="Affinity")
    log.emit(KIND_GAME_START, game=1, on_play="Azorius Control",
             deck1="Azorius Control", deck2="Affinity")
    log.emit(KIND_TURN_START, turn=3, actor="Affinity", pidx=1,
             state={"life": [20, 20]})
    # First, a main-phase DECISION from the active player.
    log.emit_decision(
        actor="Affinity", pidx=1,
        chosen={"action": "cast_spell", "card": stack_item_name,
                "ev": 5.0, "reason": "deploy threat"},
        alternatives=[], candidates_n=1,
    )
    # Then, a RESPONSE_DECISION from the defender.
    log.emit_response_decision(
        actor="Azorius Control", pidx=0,
        chosen={"action": chosen_action, "card": chosen_card,
                "ev": 4.5, "reason": "counter the equipment"},
        alternatives=[
            {"action": "pass", "card": None, "ev": 0.0,
             "gap": 4.5, "rejected_because": "letting it resolve"},
        ],
        stack_item={"name": stack_item_name, "controller": 1,
                    "cost": stack_item_cost},
        held_counter_floor_ev=1.5,
        subsystems={"clock": 0.0, "bhi": 0.0, "combo": 0.0},
    )
    log.emit(KIND_GAME_END, game=1, winner="Azorius Control",
             winner_idx=0, turns=3, win_condition="combo", life=[20, 0])
    return log


# ─── Tests ──────────────────────────────────────────────────────


def test_response_decision_event_includes_stack_item():
    """The stack item being responded to is the reviewer's key info:
    'which spell on the stack did the AI choose to counter?'"""
    log = _build_log_with_response()
    response_events = [e for e in log.events
                       if e["kind"] == KIND_RESPONSE_DECISION]
    assert len(response_events) == 1
    evt = response_events[0]
    assert "stack_item" in evt
    assert evt["stack_item"]["name"] == "Cranial Plating"
    assert evt["stack_item"]["controller"] == 1
    assert evt["stack_item"]["cost"] == 2


def test_response_decision_pass_has_distinct_kind():
    """A choice not to respond still emits the event with kind
    RESPONSE_DECISION (not DECISION), so the reviewer can grep for
    'AI chose not to counter' moments without scanning every event."""
    log = _build_log_with_response(
        chosen_action="pass", chosen_card=None)
    response_events = [e for e in log.events
                       if e["kind"] == KIND_RESPONSE_DECISION]
    assert len(response_events) == 1
    evt = response_events[0]
    assert evt["kind"] == KIND_RESPONSE_DECISION
    assert evt["kind"] != KIND_DECISION
    assert evt["chosen"]["action"] == "pass"


def test_base_decision_fields_present_on_both_kinds():
    """Both DECISION and RESPONSE_DECISION share the BaseDecision schema
    (decision_id, chosen, alternatives) so a single renderer covers
    both. Diverging fields belong on the response kind only."""
    log = _build_log_with_response()
    main = [e for e in log.events if e["kind"] == KIND_DECISION]
    resp = [e for e in log.events if e["kind"] == KIND_RESPONSE_DECISION]
    assert main and resp
    for evt in (main[0], resp[0]):
        assert "decision_id" in evt
        assert "chosen" in evt
        assert "alternatives" in evt
        assert isinstance(evt["alternatives"], list)
    # The response-only fields must NOT leak onto a main-phase event.
    assert "stack_item" not in main[0]
    assert "held_counter_floor_ev" not in main[0]
    # And must be present on the response event.
    assert "stack_item" in resp[0]
    assert "held_counter_floor_ev" in resp[0]


def test_response_decision_id_is_unique_and_distinguishable():
    """RESPONSE_DECISION events get their own decision_id from the same
    monotonic counter as DECISION — reviewer can anchor to either."""
    log = _build_log_with_response()
    ids = [e["decision_id"] for e in log.events
           if e["kind"] in (KIND_DECISION, KIND_RESPONSE_DECISION)]
    assert len(ids) == 2
    assert ids[0] != ids[1]


def test_legacy_replay_without_response_decisions_still_parses():
    """The new event is purely additive: a replay written before W0-H
    (containing only DECISION events) must still parse and render
    without raising."""
    log = ReplayLog(seed=1, deck1="A", deck2="B")
    log.emit(KIND_GAME_START, game=1, on_play="A", deck1="A", deck2="B")
    log.emit(KIND_TURN_START, turn=1, actor="A", pidx=0,
             state={"life": [20, 20]})
    log.emit_decision(
        actor="A", pidx=0,
        chosen={"action": "cast_spell", "card": "Bolt",
                "ev": 3.0, "reason": "burn"},
        alternatives=[], candidates_n=1,
    )
    log.emit(KIND_GAME_END, game=1, winner="A", winner_idx=0,
             turns=1, win_condition="damage", life=[20, 0])
    text = log.to_ndjson()
    # Round-trip parse: no errors.
    model = parse_ndjson(text)
    # Render: also no errors, and no RESPONSE_DECISION elements appear
    # in the body (the CSS class itself shows up in the stylesheet —
    # what we care about is that no rendered response-decision card is
    # emitted when the input contains zero RESPONSE_DECISION events).
    html = render_html(model, seed=1)
    assert 'class="decision response-decision' not in html


def test_build_replay_renders_response_decision_card():
    """A NDJSON containing a RESPONSE_DECISION must render a sibling
    decision card with the stack_item shown prominently and a CSS
    class the reviewer can spot in the output HTML."""
    log = _build_log_with_response()
    model = parse_ndjson(log.to_ndjson())
    html = render_html(model, seed=42)
    # The response-decision section is distinct CSS so reviewers can
    # scan for "where did the AI counter / pass on a counter?"
    assert "response-decision" in html
    # The stack item being responded to must surface in the output
    # — that is the whole point of the new event.
    assert "Cranial Plating" in html


def test_build_replay_renders_pass_response_with_muted_style():
    """When the AI chose not to counter (action == 'pass') the renderer
    marks it visually distinct (a muted/passed style) so reviewers
    can scan for 'AI declined to counter' moments."""
    log = _build_log_with_response(
        chosen_action="pass", chosen_card=None)
    model = parse_ndjson(log.to_ndjson())
    html = render_html(model, seed=42)
    # Two related CSS classes are acceptable; the test names the rule
    # (visually distinct passed-response style), not the exact name.
    assert "response-decision" in html
    assert ("response-pass" in html or "response-muted" in html)


def test_response_decision_carries_held_counter_floor_ev():
    """The diagnostic field `held_counter_floor_ev` (from
    ai/response.py decide_response) is what made the audit
    'why did the AI not counter?' question unanswerable.  Surfacing
    it on the event makes M2 reviewable in the replayer."""
    log = _build_log_with_response()
    evt = next(e for e in log.events
               if e["kind"] == KIND_RESPONSE_DECISION)
    assert "held_counter_floor_ev" in evt
    assert isinstance(evt["held_counter_floor_ev"], (int, float))


def test_response_decision_ndjson_round_trip():
    """The new event survives serialize → parse without loss."""
    log = _build_log_with_response()
    text = log.to_ndjson()
    parsed = ReplayLog.from_ndjson(text)
    response_events = [e for e in parsed.events
                       if e["kind"] == KIND_RESPONSE_DECISION]
    assert len(response_events) == 1
    evt = response_events[0]
    assert evt["stack_item"]["name"] == "Cranial Plating"
    assert evt["held_counter_floor_ev"] == 1.5
