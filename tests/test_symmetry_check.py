"""Phase 2.5 — symmetry invariant check (Option C).

`tools/symmetry_check.check_symmetry` flags any pair of decks (A, B)
whose `WR(A, B) + WR(B, A)` deviates from 1.0 by more than
`SYMMETRY_TOLERANCE` (10pp). This is the engine-fairness invariant:
if it ever fails on real matrix data, the engine treats P1 and P2
asymmetrically — a bug, not statistical noise.

The tests below are pure unit tests over synthetic dicts; they do
not run any games.
"""
from __future__ import annotations

from tools.symmetry_check import SYMMETRY_TOLERANCE, check_symmetry


def test_no_violations_when_symmetric():
    """Pairs whose WRs sum to exactly 1.0 produce no violations."""
    matrix = {
        "A|B": 0.55, "B|A": 0.45,
        "A|C": 0.60, "C|A": 0.40,
        "B|C": 0.50, "C|B": 0.50,
    }
    assert check_symmetry(matrix) == []


def test_finds_symmetric_pair_with_30pct_asymmetry():
    """A deliberately asymmetric pair (sum=1.30) must be flagged."""
    matrix = {
        "A|B": 0.80, "B|A": 0.50,   # sum 1.30 — clearly broken
        "A|C": 0.55, "C|A": 0.45,   # sum 1.00 — clean
    }
    issues = check_symmetry(matrix)
    assert len(issues) == 1
    d1, d2, wr1, wr2, total = issues[0]
    # The flagged pair is (A,B); ordering follows whichever key was
    # iterated first, but both decks must appear and the numbers add up.
    assert {d1, d2} == {"A", "B"}
    assert abs(total - 1.30) < 1e-9
    # And the clean pair must NOT appear.
    for issue in issues:
        assert {issue[0], issue[1]} != {"A", "C"}


def test_tolerance_threshold():
    """Sum within 10pp tolerance is OK; outside it is a violation."""
    inside = {"A|B": 0.55, "B|A": 0.50}    # sum 1.05 — within tolerance
    assert check_symmetry(inside) == []

    outside = {"A|B": 0.60, "B|A": 0.55}   # sum 1.15 — outside tolerance
    issues = check_symmetry(outside)
    assert len(issues) == 1
    assert abs(issues[0][4] - 1.15) < 1e-9


def test_tolerance_constant_is_10_percent():
    """Lock the tolerance value: 10pp pair-sum deviation = engine bug.

    If this constant ever changes, callers (CI, dashboards, audit
    scripts) need to know — bumping it should be a deliberate,
    reviewed change, not an accidental drift.
    """
    assert SYMMETRY_TOLERANCE == 0.10


def test_orphan_pair_without_reverse_is_skipped():
    """If only one direction exists (no `B|A`), the pair is skipped
    rather than treated as a 1.0-vs-0.0 violation. This matches the
    spec: the invariant only applies to pairs where both orderings
    were measured."""
    matrix = {"A|B": 0.99}  # no "B|A"
    assert check_symmetry(matrix) == []
