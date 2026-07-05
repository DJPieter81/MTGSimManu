#!/usr/bin/env python3
"""Matchup-calibration check — pairwise sim results vs real-world bands.

Institutionalization item 1 (docs/diagnostics/
2026-07-05_calibration_probe_findings.md): deck-level EXPECTED bands
in CLAUDE.md hide pairwise distortion (a deck can sit mid-table while
individual matchups are 0/100 nonsense).  This tool reads the
committed pairwise matrix (`metagame_data.jsx` D object — the same
canonical source the dashboard renders) and compares each
ground-truth pair in `tools/calibration_matchups.json` against its
real-world prior band.

Run after every `run_meta.py --matrix --save` +
`build_dashboard.py --merge`:

    python tools/check_calibration.py            # report, exit 0
    python tools/check_calibration.py --strict   # exit 1 on any miss

Default is REPORT-ONLY: the table intentionally encodes known-red
pairs (that's the work queue), so gating CI on it would block every
merge until the sim is perfect.  --strict exists for the day the
grade depends on it.

Out-of-band pairs are the probe queue — for each miss, generate the
Bo3 replay per the calibration-probe method (one `--bo3 --dump-replay`
against the pair, read for generic mechanisms).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSX = ROOT / "metagame_data.jsx"
BANDS = ROOT / "tools" / "calibration_matchups.json"

JSX_PREFIX = "const D = "


def load_matrix(jsx_path: Path = JSX):
    """Parse the D object per the documented JSX pattern (never
    rewrite the file; read-only raw_decode of the data const)."""
    s = jsx_path.read_text()
    i = s.find(JSX_PREFIX)
    if i == -1:
        raise ValueError(f"no '{JSX_PREFIX}' in {jsx_path}")
    d, _ = json.JSONDecoder().raw_decode(s[i + len(JSX_PREFIX):])
    return d


def pair_win_pct(d: dict, deck1: str, deck2: str):
    """deck1's win% vs deck2 from the wins matrix, or None if either
    deck is absent from this matrix run.

    Formula per CLAUDE.md JSX pattern:
    round(wins[i][j] * 100 / matches_per_pair).
    """
    decks = d["decks"]
    if deck1 not in decks or deck2 not in decks:
        return None
    i, j = decks.index(deck1), decks.index(deck2)
    mpp = d["matches_per_pair"]
    return round(d["wins"][i][j] * 100 / mpp)


def check(d: dict, bands: dict):
    """Pure check → (in_band, misses, absent) lists of dicts."""
    in_band, misses, absent = [], [], []
    for p in bands["pairs"]:
        pct = pair_win_pct(d, p["deck1"], p["deck2"])
        row = {"deck1": p["deck1"], "deck2": p["deck2"],
               "lo": p["lo"], "hi": p["hi"], "pct": pct}
        if pct is None:
            absent.append(row)
        elif p["lo"] <= pct <= p["hi"]:
            in_band.append(row)
        else:
            misses.append(row)
    return in_band, misses, absent


def main(argv):
    strict = "--strict" in argv
    d = load_matrix()
    bands = json.loads(BANDS.read_text())
    in_band, misses, absent = check(d, bands)

    print(f"== calibration: {len(in_band)} in band, {len(misses)} out, "
          f"{len(absent)} absent (matrix n={d['matches_per_pair']}/pair) ==")
    for r in in_band:
        print(f"  OK   {r['deck1']} vs {r['deck2']}: {r['pct']}% "
              f"(band {r['lo']}-{r['hi']})")
    for r in misses:
        print(f"  MISS {r['deck1']} vs {r['deck2']}: {r['pct']}% "
              f"(band {r['lo']}-{r['hi']}) → probe: run_meta.py --bo3 "
              f"\"{r['deck1']}\" \"{r['deck2']}\" -s <seed> --dump-replay")
    for r in absent:
        print(f"  --   {r['deck1']} vs {r['deck2']}: not in this matrix")
    return 1 if (strict and misses) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
