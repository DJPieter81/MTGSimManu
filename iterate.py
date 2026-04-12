#!/usr/bin/env python3
"""
iterate.py — Overnight micro-fix loop for MTGSimManu.

Usage (Claude Code):
    python iterate.py              # run full loop
    python iterate.py --check      # just check current WRs vs expected
    python iterate.py --matchup D1 D2  # deep-dive one matchup

Each iteration:
  1. Run quick field test (n=5 per pair, top 8 decks)
  2. Compare to EXPECTED_RANGES
  3. Flag outliers (>15pp off)
  4. Print diagnosis + suggested fix area
"""
import sys, json, time, os

# ── Expected win rates (from PROJECT_STATUS.md + real Modern data) ──
EXPECTED = {
    'Boros Energy':     (0.50, 0.65),
    'Affinity':         (0.45, 0.60),
    'Izzet Prowess':    (0.45, 0.58),
    'Domain Zoo':       (0.48, 0.60),
    'Jeskai Blink':     (0.45, 0.55),
    'Dimir Midrange':   (0.48, 0.58),
    'Eldrazi Tron':     (0.42, 0.55),
    'Ruby Storm':       (0.35, 0.50),
    "Goryo's Vengeance":(0.30, 0.45),
    'Living End':       (0.30, 0.45),
    'Amulet Titan':     (0.35, 0.50),
    '4c Omnath':        (0.35, 0.50),
}

# ── Expected head-to-head ranges (key matchups) ──
EXPECTED_H2H = {
    ('Boros Energy', 'Affinity'):     (40, 60),   # real ~50-50 to 55-45 Boros
    ('Boros Energy', 'Domain Zoo'):   (45, 60),
    ('Boros Energy', 'Izzet Prowess'):(50, 65),
    ('Affinity', 'Eldrazi Tron'):     (50, 70),
    ('Affinity', 'Dimir Midrange'):   (45, 60),
    ('Eldrazi Tron', 'Izzet Prowess'):(55, 75),   # Chalice locks Prowess
    ('Ruby Storm', 'Eldrazi Tron'):   (25, 45),
    ("Goryo's Vengeance", 'Boros Energy'): (25, 45),
}

TOP8 = ['Boros Energy', 'Affinity', 'Izzet Prowess', 'Domain Zoo',
        'Jeskai Blink', 'Dimir Midrange', 'Eldrazi Tron', 'Ruby Storm']


def run_field(n_games=5, decks=None):
    """Run all pairs, return {deck: field_wr, matchups: {(d1,d2): pct1}}."""
    from run_meta import run_matchup
    decks = decks or TOP8
    matchups = {}
    wins = {d: 0 for d in decks}
    total = {d: 0 for d in decks}

    for i, d1 in enumerate(decks):
        for j, d2 in enumerate(decks):
            if i >= j:
                continue
            r = run_matchup(d1, d2, n_games=n_games)
            matchups[(d1, d2)] = r['pct1']
            matchups[(d2, d1)] = r['pct2']
            wins[d1] += r['pct1'] * n_games // 100
            wins[d2] += r['pct2'] * n_games // 100
            total[d1] += n_games
            total[d2] += n_games

    field_wr = {}
    for d in decks:
        field_wr[d] = wins[d] / total[d] if total[d] > 0 else 0.5

    return {'field_wr': field_wr, 'matchups': matchups}


def check_outliers(results):
    """Compare results to expected ranges, return list of outliers."""
    outliers = []
    for deck, wr in results['field_wr'].items():
        exp = EXPECTED.get(deck)
        if not exp:
            continue
        lo, hi = exp
        if wr < lo - 0.10:
            outliers.append({
                'type': 'field_low', 'deck': deck,
                'actual': wr, 'expected': f'{lo:.0%}-{hi:.0%}',
                'gap': lo - wr,
                'severity': 'HIGH' if wr < lo - 0.20 else 'MED',
            })
        elif wr > hi + 0.10:
            outliers.append({
                'type': 'field_high', 'deck': deck,
                'actual': wr, 'expected': f'{lo:.0%}-{hi:.0%}',
                'gap': wr - hi,
                'severity': 'HIGH' if wr > hi + 0.20 else 'MED',
            })

    for (d1, d2), (lo, hi) in EXPECTED_H2H.items():
        key = (d1, d2)
        pct = results['matchups'].get(key)
        if pct is None:
            continue
        if pct < lo - 15:
            outliers.append({
                'type': 'h2h_low', 'deck': d1, 'vs': d2,
                'actual': f'{pct}%', 'expected': f'{lo}-{hi}%',
                'gap': lo - pct,
                'severity': 'HIGH' if pct < lo - 25 else 'MED',
            })
        elif pct > hi + 15:
            outliers.append({
                'type': 'h2h_high', 'deck': d1, 'vs': d2,
                'actual': f'{pct}%', 'expected': f'{lo}-{hi}%',
                'gap': pct - hi,
                'severity': 'HIGH' if pct > hi + 25 else 'MED',
            })

    outliers.sort(key=lambda x: -x['gap'])
    return outliers


def diagnose(outlier):
    """Suggest investigation area for an outlier."""
    d = outlier['deck']
    vs = outlier.get('vs', 'field')
    actual = outlier['actual']
    expected = outlier['expected']
    tips = []

    if outlier['type'] == 'h2h_high':
        tips.append(f"{d} vs {vs}: too strong ({actual} actual vs {expected} expected)")
    elif outlier['type'] == 'h2h_low':
        tips.append(f"{d} vs {vs}: too weak ({actual} actual vs {expected} expected)")
    elif outlier['type'] == 'field_high':
        tips.append(f"{d} field WR too high ({actual:.0%} actual vs {expected} expected)")
    elif outlier['type'] == 'field_low':
        tips.append(f"{d} field WR too low ({actual:.0%} actual vs {expected} expected)")

    if 'Affinity' in (d, vs) and 'high' in outlier['type']:
        tips.append("  → Check: Construct Token P/T, opponent artifact removal, Wrath effectiveness")
        tips.append("  → Files: engine/cards.py, engine/sideboard_manager.py, ai/board_eval.py")
    elif 'Storm' in d and 'low' in outlier['type']:
        tips.append("  → Check: storm count at kill turn, combo patience, Medallion/PiF chain")
        tips.append("  → Trace: python run_meta.py --verbose storm boros -s 94800")
    elif 'Goryo' in d and 'low' in outlier['type']:
        tips.append("  → Check: reanimate speed, evoke exile protection, SB GY hate")
    elif 'Tron' in d:
        tips.append("  → Check: Tron assembly, Chalice, creature sizing vs removal")
    else:
        tips.append(f"  → Trace: python run_meta.py --verbose \"{d}\" \"{vs}\" -s 90000")

    return '\n'.join(tips)


def print_matrix(results):
    """Print compact matchup matrix."""
    decks = TOP8
    # Header
    short = {d: d[:8] for d in decks}
    header = f"{'':20s}" + ''.join(f'{short[d]:>9s}' for d in decks)
    print(header)
    print('-' * len(header))
    for d1 in decks:
        row = f'{d1:20s}'
        for d2 in decks:
            if d1 == d2:
                row += f'{"--":>9s}'
            else:
                pct = results['matchups'].get((d1, d2), '?')
                if isinstance(pct, (int, float)):
                    row += f'{pct:>8.0f}%'
                else:
                    row += f'{"?":>9s}'
        # Field WR
        wr = results['field_wr'].get(d1, 0)
        exp = EXPECTED.get(d1, (0, 0))
        flag = '✅' if exp[0] <= wr <= exp[1] else '⚠️' if exp[0]-0.1 <= wr <= exp[1]+0.1 else '❌'
        row += f'  {wr:5.1%} {flag}'
        print(row)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='MTGSimManu iterate loop')
    parser.add_argument('--check', action='store_true', help='Just check current WRs')
    parser.add_argument('--matchup', nargs=2, help='Deep-dive one matchup')
    parser.add_argument('--n', type=int, default=5, help='Games per pair')
    args = parser.parse_args()

    if args.matchup:
        from run_meta import run_matchup
        d1, d2 = args.matchup
        print(f'\n=== {d1} vs {d2} (n={args.n}) ===')
        r = run_matchup(d1, d2, n_games=args.n)
        print(f'  {d1}: {r["pct1"]}%  |  {d2}: {r["pct2"]}%')
        print(f'  Avg turns: {r["avg_turn1"]}/{r["avg_turn2"]}')
        exp = EXPECTED_H2H.get((d1, d2))
        if exp:
            print(f'  Expected: {exp[0]}-{exp[1]}%')
        return

    print(f'\n=== Field check (n={args.n}, {len(TOP8)} decks) ===\n')
    t0 = time.time()
    results = run_field(n_games=args.n)
    elapsed = time.time() - t0
    print(f'\nCompleted in {elapsed:.0f}s\n')

    print_matrix(results)

    print('\n=== Outlier analysis ===\n')
    outliers = check_outliers(results)
    if not outliers:
        print('No outliers! All decks within expected ranges.')
    else:
        for o in outliers:
            print(f'[{o["severity"]}] {diagnose(o)}')
            print()

    # Save results
    save = {
        'timestamp': time.strftime('%Y-%m-%d %H:%M'),
        'n_games': args.n,
        'field_wr': results['field_wr'],
        'matchups': {f'{k[0]} vs {k[1]}': v for k, v in results['matchups'].items()},
        'outliers': len(outliers),
    }
    with open('iterate_log.json', 'a') as f:
        f.write(json.dumps(save) + '\n')
    print(f'\nResults appended to iterate_log.json')


if __name__ == '__main__':
    main()
