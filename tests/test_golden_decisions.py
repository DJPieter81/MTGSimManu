"""Every golden decision at or above the gate confidence is made as labelled.

The golden decision set (`tests/eval/decisions/labels.json`, evaluated by
`tools/decision_eval.py`) pairs a replay position with the play a rule
says is right, and with the evidence for that label: rules-forced, a
rollout comparison, or adversarial review. Labels whose confidence reaches
the gate are ground truth the AI must match. Lower-confidence labels are
measured and reported, not enforced.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def rows(card_db):
    from tools.decision_eval import evaluate
    return evaluate(card_db)


def test_every_label_names_a_position_and_a_known_expectation():
    from tests.eval.decisions.positions import POSITIONS
    from tools.decision_eval import load_labels
    data = load_labels()
    ids = [lab["id"] for lab in data["labels"]]
    assert len(ids) == len(set(ids))
    assert set(ids) <= set(POSITIONS)
    assert 0 < data["unverified_confidence"] < data["gate_confidence"] <= 1


def test_the_gated_labels_are_all_made(rows):
    from tools.decision_eval import load_labels
    gate = load_labels()["gate_confidence"]
    failed = [r for r in rows if r["confidence"] >= gate and not r["passed"]]
    assert not failed, failed


def test_the_unverified_labels_are_reported_not_enforced(rows):
    from tools.decision_eval import load_labels
    gate = load_labels()["gate_confidence"]
    assert any(r["confidence"] < gate for r in rows)
