"""Bo1 vs Bo3 framing experiment.

Loads the CardDatabase once in the parent process, then uses fork-based
multiprocessing to run a list of (deck1, deck2, n, bo1) tasks in parallel.
This avoids the sandbox issue where 4 workers concurrently parsing the
~150MB ModernAtomic.json corrupt the file on disk.

Usage:
    python tools/bo1_vs_bo3_experiment.py --decks 16 -n 30 --bo1
    python tools/bo1_vs_bo3_experiment.py --decks 16 -n 20 --bo3
    python tools/bo1_vs_bo3_experiment.py --field "Affinity" -n 30 --bo1
"""
from __future__ import annotations
import argparse
import json
import os
import random
import sys
import time
from typing import Dict, List, Tuple

# Pre-load DB at module import time, so fork-spawned children inherit it
# in memory without re-reading the JSON. This is the workaround for the
# sandbox bug where >1 concurrent reads of ModernAtomic.json corrupt it.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.card_database import CardDatabase  # noqa: E402
from engine.game_runner import GameRunner  # noqa: E402
from decks.modern_meta import MODERN_DECKS, METAGAME_SHARES  # noqa: E402
from run_meta import resolve_deck_name  # noqa: E402

_DB = None
_RUNNER = None


def _load_db_from_parts():
    """Sandbox workaround: ModernAtomic.json gets corrupted between
    process invocations in this environment. Load directly from the
    8 part files and inject into a CardDatabase. This bypasses the
    auto-discovery path entirely.
    """
    import json as _json
    import tempfile
    import os as _os
    project_root = _os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__)))
    merged = {}
    for i in range(1, 9):
        part = _os.path.join(project_root, f'ModernAtomic_part{i}.json')
        with open(part, encoding='utf-8') as f:
            d = _json.load(f)
        merged.update(d['data'])
    # Write to a private tempfile and pass that path to CardDatabase
    # so we don't trigger the corrupting auto-discovery.
    tf = tempfile.NamedTemporaryFile(
        mode='w', suffix='.json', delete=False, encoding='utf-8')
    _json.dump({'meta': {}, 'data': merged}, tf, ensure_ascii=False)
    tf.close()
    return tf.name


def _ensure_runner():
    """Lazy-init the runner per process. In the parent, this is called once
    before fork. In children, fork inherits the parent's already-loaded
    _DB and _RUNNER, so this becomes a no-op.
    """
    global _DB, _RUNNER
    if _RUNNER is None:
        path = _load_db_from_parts()
        _DB = CardDatabase(json_path=path)
        _RUNNER = GameRunner(_DB)
    return _RUNNER


def _run_one(args: Tuple[str, str, int, int, bool]) -> Tuple[str, str, int, str]:
    """Worker: run n games of (d1, d2) at seed_start, returning win counts.
    Args: (d1, d2, n, seed_start, bo1)
    """
    d1, d2, n, seed_start, bo1 = args
    runner = _ensure_runner()
    wins = {d1: 0, d2: 0}
    for i in range(n):
        seed = seed_start + i * 500
        try:
            random.seed(seed)
            runner.rng.seed(seed)
            d1_deck = MODERN_DECKS[d1]
            d2_deck = MODERN_DECKS[d2]
            if bo1:
                r = runner.run_game(
                    d1, d1_deck['mainboard'], d2, d2_deck['mainboard'],
                    deck1_sideboard=d1_deck.get('sideboard', {}),
                    deck2_sideboard=d2_deck.get('sideboard', {}),
                )
            else:
                r = runner.run_match(d1, d1_deck, d2, d2_deck, verbose=False)
            wins[r.winner_deck] = wins.get(r.winner_deck, 0) + 1
        except Exception as e:
            print(f'  [{d1} vs {d2} seed={seed}] error: {e}', file=sys.stderr)
    pct = round(wins.get(d1, 0) / max(n, 1) * 100)
    return (d1, d2, pct, 'bo1' if bo1 else 'bo3')


def run_pairs(pairs: List[Tuple[str, str, int, int, bool]], workers: int = 2):
    """Run a list of pair tasks. Each task is (d1, d2, n, seed_start, bo1)."""
    if workers <= 1:
        return [_run_one(p) for p in pairs]

    # Force fork start method so children inherit the pre-loaded DB.
    import multiprocessing as mp
    ctx = mp.get_context('fork')
    # Pre-load DB in parent before forking
    _ensure_runner()

    # Disable verbose logging in workers
    import logging
    logging.disable(logging.WARNING)

    with ctx.Pool(workers) as pool:
        results = []
        for i, r in enumerate(pool.imap_unordered(_run_one, pairs)):
            results.append(r)
            d1, d2, pct, fmt = r
            print(f'  [{i+1}/{len(pairs)}] {d1} vs {d2}: {pct}% ({fmt})',
                  file=sys.stderr)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--matrix', action='store_true',
                    help='Run full deck matrix (top --decks N)')
    ap.add_argument('--field', metavar='DECK',
                    help='Run one deck vs all opponents')
    ap.add_argument('--decks', '-d', type=int, default=16,
                    help='Top N decks for matrix (default 16)')
    ap.add_argument('--games', '-n', type=int, default=20,
                    help='Games per pair (default 20)')
    ap.add_argument('--seed', '-s', type=int, default=40000,
                    help='Seed start (default 40000)')
    ap.add_argument('--bo1', action='store_true',
                    help='Bo1 single games (default Bo3 matches)')
    ap.add_argument('--workers', '-w', type=int, default=2,
                    help='Parallel workers (default 2)')
    ap.add_argument('--out', default=None,
                    help='Output JSON path')
    args = ap.parse_args()

    bo1 = args.bo1
    fmt = 'bo1' if bo1 else 'bo3'

    if args.matrix:
        names = list(MODERN_DECKS.keys())
        if args.decks and args.decks < len(names):
            names = sorted(names,
                           key=lambda n: METAGAME_SHARES.get(n, 0),
                           reverse=True)[:args.decks]
        # Off-diagonal pairs only, both directions for explicit per-deck WR
        pairs = [(d1, d2, args.games, args.seed, bo1)
                 for d1 in names for d2 in names if d1 != d2]
    elif args.field:
        deck = resolve_deck_name(args.field)
        if deck not in MODERN_DECKS:
            ap.error(f'Unknown deck: {args.field}')
        opponents = [n for n in MODERN_DECKS.keys() if n != deck]
        pairs = [(deck, opp, args.games, args.seed, bo1)
                 for opp in opponents]
        names = [deck] + opponents
    else:
        ap.error('Pass --matrix or --field DECK')

    print(f'Running {len(pairs)} pair tasks ({args.games} games each, '
          f'{fmt}, {args.workers} workers)...', file=sys.stderr)
    t0 = time.time()
    results = run_pairs(pairs, workers=args.workers)
    elapsed = time.time() - t0
    print(f'Done in {elapsed:.1f}s', file=sys.stderr)

    # Build per-deck rankings
    matrix = {}
    for d1, d2, pct, _f in results:
        matrix[(d1, d2)] = pct

    rankings = []
    if args.matrix:
        for d in names:
            rates = [matrix.get((d, opp), 50)
                     for opp in names if opp != d and (d, opp) in matrix]
            avg = sum(rates) / max(len(rates), 1)
            rankings.append({'deck': d, 'flat_wr': round(avg, 1),
                             'opponents_played': len(rates)})
        rankings.sort(key=lambda r: -r['flat_wr'])
    elif args.field:
        deck = args.field
        for d, opp, pct, _f in results:
            rankings.append({'opponent': opp, 'wr': pct})
        rankings.sort(key=lambda r: -r['wr'])

    out = {
        'mode': 'matrix' if args.matrix else 'field',
        'format': fmt,
        'n_games': args.games,
        'seed_start': args.seed,
        'workers': args.workers,
        'elapsed_sec': round(elapsed, 1),
        'matrix': {f'{d1}|{d2}': pct for (d1, d2), pct in matrix.items()},
        'rankings': rankings,
    }
    if args.field:
        out['deck'] = args.field

    if args.out:
        with open(args.out, 'w') as f:
            json.dump(out, f, indent=2)
        print(f'Wrote {args.out}', file=sys.stderr)
    else:
        print(json.dumps(out, indent=2))

    # Print rankings to stderr for visibility
    print('\n=== Rankings ===', file=sys.stderr)
    if args.matrix:
        for r in rankings:
            print(f'  {r["deck"]:30s}  {r["flat_wr"]:5.1f}%  '
                  f'(n={r["opponents_played"]})', file=sys.stderr)
    elif args.field:
        for r in rankings:
            print(f'  vs {r["opponent"]:25s}  {r["wr"]}%', file=sys.stderr)

    return out


if __name__ == '__main__':
    main()
