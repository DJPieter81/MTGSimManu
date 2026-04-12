#!/usr/bin/env python3
"""iterate_test.py — Quick field test for overnight iteration.

Runs each deck vs 4 key opponents at n=5 (takes ~2 min total).
Flags outliers outside EXPECTED range.

Usage: python3 iterate_test.py [--full]
  --full: test all 16 decks (slow, ~10 min)
  default: test only current outliers (fast, ~2 min)
"""
import sys
import time

EXPECTED = {
    'Boros Energy': (50, 70), 'Affinity': (45, 60), 'Eldrazi Tron': (50, 65),
    'Jeskai Blink': (45, 60), 'Ruby Storm': (35, 55), 'Domain Zoo': (50, 65),
    'Izzet Prowess': (45, 60), 'Dimir Midrange': (45, 60),
    'Living End': (20, 40), "Goryo's Vengeance": (20, 40),
    'Amulet Titan': (35, 55), '4c Omnath': (35, 55),
    '4/5c Control': (25, 45), 'Azorius Control (WST)': (25, 45),
    'Azorius Control': (10, 30), 'Pinnacle Affinity': (40, 60),
}

# Key opponents for quick field test (covers aggro/midrange/combo)
FIELD = ['Boros Energy', 'Affinity', 'Izzet Prowess', 'Dimir Midrange', 'Living End']

# Current outliers to prioritize
OUTLIERS = ['Affinity', 'Living End', 'Eldrazi Tron', 'Boros Energy']

N_GAMES = 5  # per matchup — fast but noisy


def run_field(deck, opponents, n=N_GAMES):
    """Run deck vs opponents, return average WR."""
    import os, sys, io
    # Suppress all noisy output (Loaded/Sideboard messages)
    devnull = open(os.devnull, 'w')
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stderr = devnull
    from run_meta import run_matchup
    wins = 0
    total = 0
    for opp in opponents:
        if opp == deck:
            continue
        sys.stdout = devnull
        r = run_matchup(deck, opp, n_games=n)
        sys.stdout = old_stdout
        wins += r['pct1'] * n // 100
        total += n
    sys.stderr = old_stderr
    devnull.close()
    return wins * 100 // max(total, 1)


def main():
    full = '--full' in sys.argv
    decks = list(EXPECTED.keys()) if full else OUTLIERS

    print(f"{'Deck':30s} {'WR':>5s} {'Expected':>10s} {'Status':>8s}")
    print("-" * 60)

    t0 = time.time()
    outlier_count = 0

    for deck in decks:
        wr = run_field(deck, FIELD)
        lo, hi = EXPECTED.get(deck, (30, 70))
        if wr < lo:
            status = f"LOW -{lo - wr}pp"
            outlier_count += 1
        elif wr > hi:
            status = f"HIGH +{wr - hi}pp"
            outlier_count += 1
        else:
            status = "OK"
        print(f"{deck:30s} {wr:4d}% {lo:3d}-{hi:3d}%  {status}")

    elapsed = time.time() - t0
    print(f"\n{len(decks)} decks tested in {elapsed:.0f}s. {outlier_count} outliers.")

    if outlier_count == 0:
        print("All decks within expected range!")
    else:
        print("\nNext: trace the worst outlier with --verbose and fix ONE thing.")


if __name__ == '__main__':
    main()
