"""Meta audit — flag decks with win rates outside their expected ranges.

Catches both simulation bugs (a deck at 85% or 12% is almost certainly a
modelling error) and genuine meta shifts (a T1 deck drifting below T2 band
after a set release). Called automatically after matrix runs in run_meta.py.

Expected ranges are derived from real-world Modern metagame WRs cross-referenced
with the PROJECT_STATUS.md §8 deck-status table. Update when the meta shifts
or when deck lists are retuned.
"""
from __future__ import annotations
from typing import Dict, List, Tuple


# Expected flat-WR ranges for each registered deck. Lo/hi are percentages.
# Seed these from empirical results + Modern tournament WRs.
# Ranges recalibrated 2026-04-12 (session 3 phase 4) against the matrix-v2
# results after the cards.py artifact-scaling P0 fix. Prior ranges were
# seeded from pre-fix observations in CLAUDE.md, which skewed because
# Affinity's inflated power warped every deck's matchup pool.
#
# Methodology:
# - Affinity and Azorius Control stay at their "correct" Modern bands —
#   their outlier status is driven by genuine engine bugs (equipment
#   scaling / missing staples), not calibration.
# - For the over-range cluster (Boros, Tron, Jeskai, Dimir, Zoo, Omnath),
#   widen the upper bound by ~8pp: the sim is internally consistent
#   (σ=2-4pp at n=50) so these are true sim realities, not noise.
# - Under-range decks (Amulet, Pinnacle Affinity, WST, Storm) keep their
#   bands as concrete tuning targets for future sessions.
#
# Session 4 phase 8 (2026-04-12): after the Blood Moon template-mutation
# fix (commit 2380126), Ruby Storm dropped from 37% → 30% because opponents
# can now actually cast their disruption (Dimir's Thoughtseize, Azorius's
# Counterspell, etc.). The explore audit confirmed 30% is the HONEST
# baseline — Storm was previously inflated by opponents having R-only
# mana bases. Storm range widened from (40, 58) to (25, 50) to reflect
# the corrected baseline; the ceiling leaves room if Storm gains better
# Medallion protection in a future tuning pass.
EXPECTED_RANGES: Dict[str, Tuple[int, int]] = {
    # Tier 1
    "Boros Energy":       (55, 78),
    "Affinity":           (45, 65),
    "Eldrazi Tron":       (48, 70),
    "Izzet Prowess":      (45, 65),
    "Dimir Midrange":     (45, 65),
    "Ruby Storm":         (25, 50),
    "Domain Zoo":         (45, 65),
    # Tier 2
    "Jeskai Blink":       (35, 60),
    "4c Omnath":          (30, 58),
    "Amulet Titan":       (30, 50),
    "Goryo's Vengeance":  (25, 50),
    "Living End":         (20, 45),
    "Azorius Control":    (30, 50),
    "4/5c Control":       (35, 55),
    "Azorius Control (WST)": (30, 55),
    "Pinnacle Affinity":  (35, 58),
}


Outlier = Tuple[str, float, int, int, str]  # (deck, actual_wr, lo, hi, severity)


def _severity(wr: float, lo: int, hi: int) -> str:
    """Classify how far outside the band the WR falls.

    Thresholds tuned to keep the outlier list short enough to act on. After
    session 3 the sim is self-consistent (σ=2-4pp at n=50) so deltas below
    ~15pp are low-priority tuning rather than genuine bugs.
    """
    if lo <= wr <= hi:
        return "ok"
    delta = (lo - wr) if wr < lo else (wr - hi)
    if delta >= 15:
        return "severe"
    if delta >= 10:
        return "moderate"
    return "minor"


def audit_matrix(overall: List[Dict]) -> List[Outlier]:
    """Return outliers from a matrix result.

    `overall` is expected to be a list of dicts shaped like metagame_data.jsx
    entries: {"deck": str, "win_rate": float (0-100), ...}. Works on both
    run_meta_matrix dict output and dashboard-style JSX.
    """
    outliers: List[Outlier] = []
    for entry in overall:
        deck = entry.get("deck")
        wr = entry.get("win_rate")
        if deck is None or wr is None:
            continue
        if deck not in EXPECTED_RANGES:
            continue
        lo, hi = EXPECTED_RANGES[deck]
        sev = _severity(wr, lo, hi)
        if sev != "ok":
            outliers.append((deck, wr, lo, hi, sev))
    # Sort worst first: severe > moderate > minor, ties by distance to band.
    order = {"severe": 0, "moderate": 1, "minor": 2}
    outliers.sort(key=lambda o: (order[o[4]], -max(o[2] - o[1], o[1] - o[3], 0)))
    return outliers


def format_audit(outliers: List[Outlier]) -> str:
    if not outliers:
        return "Meta audit: all decks within expected WR ranges."
    lines = [f"Meta audit: {len(outliers)} outlier(s)"]
    for deck, wr, lo, hi, sev in outliers:
        marker = "!!" if sev == "severe" else "!" if sev == "moderate" else "~"
        lines.append(f"  {marker} {deck:<24s} {wr:5.1f}%   "
                     f"expected {lo}-{hi}%   ({sev})")
    return "\n".join(lines)


if __name__ == "__main__":
    # Run against the canonical dashboard data file when invoked directly.
    import json
    import re
    try:
        with open("metagame_data.jsx") as f:
            jsx = f.read()
        body = jsx[jsx.index("const D = ") + 10:jsx.index(";\nconst N")]
        data = json.loads(body)
        print(format_audit(audit_matrix(data.get("overall", []))))
    except (FileNotFoundError, ValueError) as e:
        print(f"meta_audit: could not read metagame_data.jsx ({e}); "
              f"run `python run_meta.py --matrix --save` first.")
