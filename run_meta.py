"""Metagame analysis tools.

Usage:
    python run_meta.py                          # full matrix, 20 games each
    python run_meta.py --games 50               # more games per matchup
    python run_meta.py --decks 8                # top 8 decks only
    python run_meta.py --matchup "Ruby Storm" "Dimir Midrange" --games 100
    python run_meta.py --field "Ruby Storm" --games 30
    python run_meta.py --verbose "Domain Zoo" "Dimir Midrange" --seed 42000
"""
import random
import sys
from typing import Dict, List, Optional, Tuple

from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from decks.modern_meta import MODERN_DECKS, get_all_deck_names, METAGAME_SHARES


def _get_runner():
    db = CardDatabase()
    return GameRunner(db)


def _run_game(runner, d1_name, d2_name, seed):
    d1 = MODERN_DECKS[d1_name]
    d2 = MODERN_DECKS[d2_name]
    random.seed(seed)
    return runner.run_game(
        d1_name, d1['mainboard'], d2_name, d2['mainboard'],
        deck1_sideboard=d1.get('sideboard', {}),
        deck2_sideboard=d2.get('sideboard', {}),
    )


# ─── Core functions ───────────────────────────────────────────


def run_matchup(deck1: str, deck2: str, n_games: int = 50,
                seed_start: int = 50000, verbose: bool = False) -> Dict:
    """Run N games between two decks. Returns stats dict."""
    runner = _get_runner()
    wins = {deck1: 0, deck2: 0, 'draw': 0}
    turn_wins = {deck1: [], deck2: []}

    for i in range(n_games):
        seed = seed_start + i * 500
        d1 = MODERN_DECKS[deck1]
        d2 = MODERN_DECKS[deck2]
        random.seed(seed)
        r = runner.run_game(
            deck1, d1['mainboard'], deck2, d2['mainboard'],
            deck1_sideboard=d1.get('sideboard', {}),
            deck2_sideboard=d2.get('sideboard', {}),
            verbose=verbose,
        )
        wins[r.winner_deck] = wins.get(r.winner_deck, 0) + 1
        if r.winner_deck in turn_wins:
            turn_wins[r.winner_deck].append(r.turns)

    pct1 = round(wins[deck1] / n_games * 100)
    pct2 = round(wins[deck2] / n_games * 100)
    avg_turn1 = (sum(turn_wins[deck1]) / len(turn_wins[deck1])) if turn_wins[deck1] else 0
    avg_turn2 = (sum(turn_wins[deck2]) / len(turn_wins[deck2])) if turn_wins[deck2] else 0

    return {
        'deck1': deck1, 'deck2': deck2, 'games': n_games,
        'wins': wins, 'pct1': pct1, 'pct2': pct2,
        'avg_turn1': round(avg_turn1, 1), 'avg_turn2': round(avg_turn2, 1),
        'turn_dist1': sorted(turn_wins[deck1]), 'turn_dist2': sorted(turn_wins[deck2]),
    }


def run_field(deck: str, n_games: int = 30, opponents: List[str] = None) -> Dict:
    """Run one deck against all others. Returns {opponent: win_pct}."""
    runner = _get_runner()
    if opponents is None:
        opponents = [n for n in get_all_deck_names() if n != deck]

    results = {}
    for opp in opponents:
        wins = {deck: 0, opp: 0}
        for i in range(n_games):
            seed = 50000 + i * 500
            r = _run_game(runner, deck, opp, seed)
            wins[r.winner_deck] = wins.get(r.winner_deck, 0) + 1
        results[opp] = round(wins[deck] / n_games * 100)

    avg = sum(results.values()) / len(results)
    return {'deck': deck, 'matchups': results, 'average': round(avg, 1)}


def run_meta_matrix(top_tier: int = None, n_games: int = 20,
                    seed_start: int = 40000) -> Dict:
    """Run full metagame matrix. Returns matrix dict + rankings.

    Args:
        top_tier: Only include top N decks by metagame share (None = all)
        n_games: Games per matchup pair
        seed_start: Starting seed

    Returns dict with:
        'matrix': {(deck1, deck2): win_pct}
        'rankings': [(avg_pct, deck_name), ...] sorted desc
        'names': list of deck names included
    """
    runner = _get_runner()
    names = get_all_deck_names()
    if top_tier and top_tier < len(names):
        # Sort by metagame share, take top N
        names = sorted(names, key=lambda n: METAGAME_SHARES.get(n, 0), reverse=True)[:top_tier]

    matrix = {}
    total = len(names) * (len(names) - 1) // 2
    done = 0

    for i, d1_name in enumerate(names):
        for j, d2_name in enumerate(names):
            if i >= j:
                continue
            wins = {d1_name: 0, d2_name: 0}
            for g in range(n_games):
                seed = seed_start + g * 500
                try:
                    r = _run_game(runner, d1_name, d2_name, seed)
                    wins[r.winner_deck] = wins.get(r.winner_deck, 0) + 1
                except Exception:
                    pass
            w1 = wins.get(d1_name, 0)
            pct = round(w1 / n_games * 100)
            matrix[(d1_name, d2_name)] = pct
            matrix[(d2_name, d1_name)] = 100 - pct
            done += 1
            print(f'  [{done}/{total}] {d1_name} vs {d2_name}: {pct}%-{100-pct}%', file=sys.stderr)

    rankings = []
    for d in names:
        rates = [matrix.get((d, opp), 50) for opp in names if opp != d]
        avg = sum(rates) / len(rates)
        rankings.append((round(avg, 1), d))
    rankings.sort(reverse=True)

    return {'matrix': matrix, 'rankings': rankings, 'names': names}


def run_verbose_game(deck1: str, deck2: str, seed: int = 42000) -> str:
    """Run a single verbose game, return the full log as string."""
    runner = _get_runner()
    d1 = MODERN_DECKS[deck1]
    d2 = MODERN_DECKS[deck2]
    random.seed(seed)
    r = runner.run_game(
        deck1, d1['mainboard'], deck2, d2['mainboard'],
        deck1_sideboard=d1.get('sideboard', {}),
        deck2_sideboard=d2.get('sideboard', {}),
        verbose=True,
    )
    lines = [f'Result: {r.winner_deck} wins T{r.turns} via {r.win_condition}',
             f'Life: P1={r.winner_life if r.winner==0 else r.loser_life} '
             f'P2={r.winner_life if r.winner==1 else r.loser_life}',
             '']
    lines.extend(r.game_log)
    return '\n'.join(lines)


# ─── Pretty printing ─────────────────────────────────────────


def print_matrix(result: Dict):
    """Pretty-print a metagame matrix result."""
    names = result['names']
    matrix = result['matrix']

    print('\n=== METAGAME POWER RANKINGS ===\n')
    for avg, deck in result['rankings']:
        bar = '#' * int(avg / 2)
        print(f'  {deck:25s}  {avg:4.0f}%  {bar}')

    print('\n=== MATCHUP MATRIX ===\n')
    short = {n: n[:12] for n in names}
    header = f'{"":>14s} | ' + ' | '.join(f'{short[n]:>12s}' for n in names)
    print(header)
    print('-' * len(header))
    for d1 in names:
        cells = []
        for d2 in names:
            if d1 == d2:
                cells.append(f'{"--":>12s}')
            else:
                pct = matrix.get((d1, d2), 50)
                cells.append(f'{pct:>11d}%')
        print(f'{short[d1]:>14s} | ' + ' | '.join(cells))


def print_matchup(result: Dict):
    """Pretty-print a matchup result."""
    print(f'\n{result["deck1"]} vs {result["deck2"]} ({result["games"]} games)')
    print(f'  {result["deck1"]:25s}: {result["pct1"]}% (avg T{result["avg_turn1"]})')
    print(f'  {result["deck2"]:25s}: {result["pct2"]}% (avg T{result["avg_turn2"]})')
    if result['turn_dist1']:
        print(f'  {result["deck1"]} wins on: {result["turn_dist1"]}')
    if result['turn_dist2']:
        print(f'  {result["deck2"]} wins on: {result["turn_dist2"]}')


def print_field(result: Dict):
    """Pretty-print a field result."""
    print(f'\n{result["deck"]} vs field (avg {result["average"]}%)\n')
    for opp, pct in sorted(result['matchups'].items(), key=lambda x: -x[1]):
        bar = '#' * (pct // 2)
        print(f'  vs {opp:25s}: {pct:3d}%  {bar}')


# ─── CLI ──────────────────────────────────────────────────────


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='MTG metagame analysis')
    parser.add_argument('--matrix', action='store_true', help='Run full metagame matrix')
    parser.add_argument('--matchup', nargs=2, metavar=('DECK1', 'DECK2'), help='Run matchup between two decks')
    parser.add_argument('--field', metavar='DECK', help='Run one deck vs all others')
    parser.add_argument('--verbose', nargs=2, metavar=('DECK1', 'DECK2'), help='Run single verbose game')
    parser.add_argument('--games', '-n', type=int, default=20, help='Games per matchup (default 20)')
    parser.add_argument('--decks', '-d', type=int, default=None, help='Top N decks for matrix')
    parser.add_argument('--seed', '-s', type=int, default=42000, help='Seed for verbose game')
    parser.add_argument('--list', action='store_true', help='List available decks')
    args = parser.parse_args()

    if args.list:
        for name in get_all_deck_names():
            share = METAGAME_SHARES.get(name, 0)
            print(f'  {name:25s} ({share:.1f}% meta share)')
        sys.exit(0)

    if args.verbose:
        print(run_verbose_game(args.verbose[0], args.verbose[1], seed=args.seed))
    elif args.matchup:
        print_matchup(run_matchup(args.matchup[0], args.matchup[1], n_games=args.games))
    elif args.field:
        print_field(run_field(args.field, n_games=args.games))
    else:
        # Default: run matrix
        result = run_meta_matrix(top_tier=args.decks, n_games=args.games)
        print_matrix(result)
