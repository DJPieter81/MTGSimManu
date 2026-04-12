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
EXPECTED_RANGES: Dict[str, Tuple[int, int]] = {
    # Tier 1
    "Boros Energy":       (55, 70),
    "Affinity":           (45, 65),
    "Eldrazi Tron":       (48, 62),
    "Izzet Prowess":      (45, 60),
    "Dimir Midrange":     (45, 58),
    "Ruby Storm":         (40, 58),
    "Domain Zoo":         (45, 60),
    # Tier 2
    "Jeskai Blink":       (35, 55),
    "4c Omnath":          (30, 52),
    "Amulet Titan":       (30, 50),
    "Goryo's Vengeance":  (25, 50),
    "Living End":         (20, 45),
    "Azorius Control":    (30, 50),
    "4/5c Control":       (35, 55),
    "Azorius Control (WST)": (35, 55),
    "Pinnacle Affinity":  (40, 58),
}


Outlier = Tuple[str, float, int, int, str]  # (deck, actual_wr, lo, hi, severity)


def _severity(wr: float, lo: int, hi: int) -> str:
    """Classify how far outside the band the WR falls."""
    if lo <= wr <= hi:
        return "ok"
    delta = (lo - wr) if wr < lo else (wr - hi)
    if delta >= 15:
        return "severe"
    if delta >= 7:
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
