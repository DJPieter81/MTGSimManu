"""Matrix symmetry invariant check.

For any pair (d1, d2), the sim should produce
`WR(d1, d2) + WR(d2, d1) ~= 1.0` -- otherwise the engine treats
P1 and P2 differently (a fairness bug).

Tolerance: 10% (i.e., flag pairs where the sum diverges from 1.0
by more than 0.10). 5% is statistical noise at N=50; 10% indicates
real bias.
"""
from __future__ import annotations
import json
import sys
from typing import Dict, List, Tuple


SYMMETRY_TOLERANCE = 0.10  # 10pp pair-sum deviation = engine bug


def check_symmetry(matchup_wrs: Dict[str, float]
                   ) -> List[Tuple[str, str, float, float, float]]:
    """Returns list of (d1, d2, wr1, wr2, sum) for pairs whose
    sum deviates from 1.0 by more than SYMMETRY_TOLERANCE.

    Input format: matchup_wrs is a dict keyed by "d1|d2" -> wr
    (fractional, e.g. 0.55 for 55%).
    """
    issues = []
    seen = set()
    for key, wr in matchup_wrs.items():
        if '|' not in key:
            continue
        d1, d2 = key.split('|', 1)
        pair = tuple(sorted((d1, d2)))
        if pair in seen:
            continue
        rev_key = f"{d2}|{d1}"
        if rev_key not in matchup_wrs:
            continue
        seen.add(pair)
        wr2 = matchup_wrs[rev_key]
        total = wr + wr2
        if abs(total - 1.0) > SYMMETRY_TOLERANCE:
            issues.append((d1, d2, wr, wr2, total))
    return sorted(issues, key=lambda x: -abs(x[4] - 1.0))


def _normalize_to_fractions(matrix: Dict[str, float]) -> Dict[str, float]:
    """Auto-detect percentage scale (0..100) and convert to 0..1.

    Heuristic: if any value > 1.5, assume the whole dict is in
    percentage form. The repository's `metagame_results.json` stores
    matrix values as integer percents (0..100); the spec for
    `check_symmetry` expects fractions summing near 1.0.
    """
    if not matrix:
        return matrix
    if any(v > 1.5 for v in matrix.values()):
        return {k: v / 100.0 for k, v in matrix.items()}
    return matrix


def main():
    """CLI: python tools/symmetry_check.py metagame_results.json"""
    if len(sys.argv) != 2:
        print("usage: symmetry_check.py <metagame_results.json>",
              file=sys.stderr)
        sys.exit(1)
    with open(sys.argv[1]) as f:
        data = json.load(f)
    # `matrix` field is the dict of {"d1|d2": wr} per metagame_results.json.
    # Auto-normalize percent (0..100) to fraction (0..1) for the invariant.
    raw = data.get('matrix', {})
    matrix = _normalize_to_fractions(raw)
    issues = check_symmetry(matrix)
    if not issues:
        print(f"OK: no symmetry violations > {SYMMETRY_TOLERANCE:.0%}")
        return 0
    print(f"FOUND {len(issues)} symmetry violations:")
    for d1, d2, wr1, wr2, total in issues[:10]:
        print(f"  {d1} vs {d2}: {wr1:.1%} + {wr2:.1%} = {total:.1%} "
              f"(off by {abs(total - 1.0):.1%})")
    sys.exit(2 if len(issues) > 0 else 0)


if __name__ == '__main__':
    sys.exit(main())
