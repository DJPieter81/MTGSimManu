"""Rollout evidence for golden decisions is a deterministic paired bootstrap.

`tools.decision_rollout.p_better` scores how often, over seeded bootstrap
resamples of the paired per-seed differences, the labelled play wins
sooner on average than its alternative (a tie counts half). It must be
deterministic, near 1 when the labelled play is never later and sometimes
sooner, near 0 in the opposite case, and one half when the two never differ.
`tools.decision_eval.confidence` combines agreeing independent legs and
falls back to the weakest leg when one contradicts the label.
"""
from __future__ import annotations


def test_p_better_is_deterministic_and_ordered():
    from tools.decision_rollout import p_better
    # A resample may draw only the tied seed (a half), hence not 1 or 0.
    assert p_better([3, 3, 4], [4, 4, 4]) > 0.95
    assert p_better([5, 5, 5], [4, 4, 5]) < 0.05
    assert p_better([4, 4], [4, 4]) == 0.5
    assert p_better([3, 5, 4, 6], [4, 4, 5, 5]) == \
        p_better([3, 5, 4, 6], [4, 4, 5, 5])


def _label(rollout=None, review=None, forced=False):
    return {"evidence": {"rules_forced": forced, "rollout": rollout,
                         "review": review}}


def test_agreeing_legs_combine_and_a_contradicting_leg_wins():
    from tools.decision_eval import confidence
    review = {"reviewers": 2, "refuted": 0}
    agree = confidence(_label({"p_better": 0.9}, review), 0.5)
    assert agree > 0.9
    against = confidence(_label({"p_better": 0.2}, review), 0.5)
    assert against == 0.2
    assert confidence(_label(forced=True), 0.5) == 1.0
    assert confidence(_label(), 0.5) == 0.5
