"""Metagame analysis tools.

Usage:
    python run_meta.py                          # full matrix, 20 games each
    python run_meta.py --games 50               # more games per matchup
    python run_meta.py --decks 8                # top 8 decks only
    python run_meta.py --matchup "Ruby Storm" "Dimir Midrange" --games 100
    python run_meta.py --field "Ruby Storm" --games 30
    python run_meta.py --verbose "Domain Zoo" "Dimir Midrange" --seed 42000
"""
import json
import multiprocessing as mp
import os
import random
import sys
from typing import Dict, List, Optional, Tuple

from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from decks.modern_meta import MODERN_DECKS, get_all_deck_names, METAGAME_SHARES

# Default worker count: use all cores but leave 1 free
_DEFAULT_WORKERS = max(1, (os.cpu_count() or 4) - 1)


DECK_ALIASES = {
    "zoo": "Domain Zoo",
    "storm": "Ruby Storm",
    "dimir": "Dimir Midrange",
    "omnath": "4c Omnath",
    "4c": "4c Omnath",
    "5c": "4/5c Control",
    "energy": "Boros Energy",
    "boros": "Boros Energy",
    "jeskai": "Jeskai Blink",
    "blink": "Jeskai Blink",
    "tron": "Eldrazi Tron",
    "eldrazi": "Eldrazi Tron",
    "amulet": "Amulet Titan",
    "titan": "Amulet Titan",
    "goryos": "Goryo's Vengeance",
    "goryo": "Goryo's Vengeance",
    "reanimator": "Goryo's Vengeance",
    "living end": "Living End",
    "cascade": "Living End",
    "prowess": "Izzet Prowess",
    "izzet": "Izzet Prowess",
    "affinity": "Affinity",
    "robots": "Affinity",
    "azorius": "Azorius Control",
    "uw": "Azorius Control",
    "wst": "Azorius Control",
}


def resolve_deck_name(name: str) -> str:
    """Resolve aliases and case-insensitive names to canonical deck name."""
    # Exact match first
    if name in MODERN_DECKS:
        return name
    # Case-insensitive alias
    lower = name.lower().strip()
    if lower in DECK_ALIASES:
        return DECK_ALIASES[lower]
    # Fuzzy: check if input is a substring of any deck name
    for deck in get_all_deck_names():
        if lower in deck.lower():
            return deck
    return name  # return as-is, will error later if invalid


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


_worker_runner = None  # Per-process cached runner


def _init_worker():
    """Initialize a GameRunner once per worker process."""
    global _worker_runner
    import logging
    logging.disable(logging.WARNING)
    db = CardDatabase()
    _worker_runner = GameRunner(db)


def _worker_matchup(args):
    """Worker function for parallel matchup execution.
    Uses the pre-initialized _worker_runner (one DB load per process).
    """
    d1_name, d2_name, n_games, seed_start = args
    runner = _worker_runner
    wins = {d1_name: 0, d2_name: 0}
    for i in range(n_games):
        seed = seed_start + i * 500
        d1 = MODERN_DECKS[d1_name]
        d2 = MODERN_DECKS[d2_name]
        random.seed(seed)
        try:
            r = runner.run_game(
                d1_name, d1['mainboard'], d2_name, d2['mainboard'],
                deck1_sideboard=d1.get('sideboard', {}),
                deck2_sideboard=d2.get('sideboard', {}),
            )
            wins[r.winner_deck] = wins.get(r.winner_deck, 0) + 1
        except Exception:
            pass
    pct = round(wins.get(d1_name, 0) / max(n_games, 1) * 100)
    return (d1_name, d2_name, pct)


def _run_game_no_runner(d1_name, d2_name, seed):
    """Standalone single game for parallel matchup execution."""
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


def run_field(deck: str, n_games: int = 30, opponents: List[str] = None,
              parallel: bool = True) -> Dict:
    """Run one deck against all others. Returns {opponent: win_pct}."""
    if opponents is None:
        opponents = [n for n in get_all_deck_names() if n != deck]

    if parallel and len(opponents) > 1:
        args = [(deck, opp, n_games, 50000) for opp in opponents]
        with mp.Pool(_DEFAULT_WORKERS, initializer=_init_worker) as pool:
            worker_results = pool.map(_worker_matchup, args)
        results = {d2: pct for d1, d2, pct in worker_results}
    else:
        runner = _get_runner()
        results = {}
        for opp in opponents:
            wins = {deck: 0, opp: 0}
            for i in range(n_games):
                seed = 50000 + i * 500
                r = _run_game(runner, deck, opp, seed)
                wins[r.winner_deck] = wins.get(r.winner_deck, 0) + 1
            results[opp] = round(wins[deck] / n_games * 100)

    avg = sum(results.values()) / len(results) if results else 0
    return {'deck': deck, 'matchups': results, 'average': round(avg, 1)}


def run_meta_matrix(top_tier: int = None, n_games: int = 20,
                    seed_start: int = 40000, parallel: bool = True) -> Dict:
    """Run full metagame matrix. Returns matrix dict + rankings.

    Args:
        top_tier: Only include top N decks by metagame share (None = all)
        n_games: Games per matchup pair
        seed_start: Starting seed
        parallel: Use multiprocessing (default True)

    Returns dict with:
        'matrix': {(deck1, deck2): win_pct}
        'rankings': [(avg_pct, deck_name), ...] sorted desc
        'names': list of deck names included
    """
    names = get_all_deck_names()
    if top_tier and top_tier < len(names):
        names = sorted(names, key=lambda n: METAGAME_SHARES.get(n, 0), reverse=True)[:top_tier]

    # Build all matchup pairs
    pairs = []
    for i, d1_name in enumerate(names):
        for j, d2_name in enumerate(names):
            if i < j:
                pairs.append((d1_name, d2_name, n_games, seed_start))

    total = len(pairs)
    matrix = {}

    if parallel and total > 1:
        workers = min(_DEFAULT_WORKERS, total)
        print(f'Running {total} matchups × {n_games} games = {total * n_games} total '
              f'({workers} workers)', file=sys.stderr)
        with mp.Pool(workers, initializer=_init_worker) as pool:
            for i, (d1, d2, pct) in enumerate(pool.imap_unordered(_worker_matchup, pairs)):
                matrix[(d1, d2)] = pct
                matrix[(d2, d1)] = 100 - pct
                print(f'  [{i+1}/{total}] {d1} vs {d2}: {pct}%-{100-pct}%', file=sys.stderr)
    else:
        runner = _get_runner()
        for idx, (d1_name, d2_name, ng, ss) in enumerate(pairs):
            wins = {d1_name: 0, d2_name: 0}
            for g in range(ng):
                seed = ss + g * 500
                try:
                    r = _run_game(runner, d1_name, d2_name, seed)
                    wins[r.winner_deck] = wins.get(r.winner_deck, 0) + 1
                except Exception:
                    pass
            pct = round(wins.get(d1_name, 0) / ng * 100)
            matrix[(d1_name, d2_name)] = pct
            matrix[(d2_name, d1_name)] = 100 - pct
            print(f'  [{idx+1}/{total}] {d1_name} vs {d2_name}: {pct}%-{100-pct}%', file=sys.stderr)

    # Determine T1 (top 5) and T2 (next 6) by meta share
    all_by_share = sorted(METAGAME_SHARES.keys(),
                          key=lambda n: METAGAME_SHARES.get(n, 0), reverse=True)
    tier1 = set(all_by_share[:5])
    tier2 = set(all_by_share[5:11])
    tier_decks = tier1 | tier2

    rankings = []
    for d in names:
        # Flat average (all opponents in the matrix)
        rates = [matrix.get((d, opp), 50) for opp in names if opp != d]
        avg = sum(rates) / len(rates)

        # Meta-weighted WR against T1+T2 only
        weighted_sum = 0.0
        weight_total = 0.0
        for opp in names:
            if opp == d or opp not in tier_decks:
                continue
            share = METAGAME_SHARES.get(opp, 0)
            weighted_sum += matrix.get((d, opp), 50) * share
            weight_total += share
        meta_wr = round(weighted_sum / weight_total, 1) if weight_total > 0 else avg

        rankings.append((round(avg, 1), d, meta_wr))
    rankings.sort(key=lambda x: x[2], reverse=True)

    return {'matrix': matrix, 'rankings': rankings, 'names': names,
            'tier1': sorted(tier1), 'tier2': sorted(tier2)}


def inspect_deck(deck_name: str) -> str:
    """Show full deck profile: decklist, gameplan, strategy profile, card tags.

    Usage:
        python run_meta.py --deck "Ruby Storm"
    """
    from ai.gameplan import create_goal_engine, get_gameplan
    from ai.strategy_profile import get_profile, DECK_ARCHETYPES, DECK_ARCHETYPE_OVERRIDES

    lines = []
    d = MODERN_DECKS.get(deck_name)
    if not d:
        return f'Deck "{deck_name}" not found. Use --list to see available decks.'

    # Header
    share = METAGAME_SHARES.get(deck_name, 0)
    lines.append(f'=== {deck_name} ({share:.1f}% meta share) ===\n')

    # Archetype + strategy profile
    arch_enum = DECK_ARCHETYPES.get(deck_name)
    arch_str = DECK_ARCHETYPE_OVERRIDES.get(deck_name) or (arch_enum.value if arch_enum else 'midrange')
    profile = get_profile(arch_str)
    lines.append(f'Archetype: {arch_str}')
    lines.append(f'Strategy profile: pass_threshold={profile.pass_threshold}, '
                 f'holdback={profile.holdback_applies}, '
                 f'storm_patience={profile.storm_patience}')
    lines.append(f'  burn_face_mult={profile.burn_face_mult}, '
                 f'attack_threshold={profile.attack_threshold}')
    lines.append('')

    # Decklist
    mainboard = d.get('mainboard', {})
    sideboard = d.get('sideboard', {})

    creatures = {}
    spells = {}
    lands = {}
    db = CardDatabase()
    for card_name, count in sorted(mainboard.items()):
        t = db.get_card(card_name)
        if t and t.is_land:
            lands[card_name] = count
        elif t and t.is_creature:
            creatures[card_name] = (count, t.power, t.toughness, t.cmc)
        else:
            cmc = t.cmc if t else '?'
            spells[card_name] = (count, cmc)

    lines.append(f'Mainboard ({sum(mainboard.values())} cards):')
    if creatures:
        lines.append(f'  Creatures ({sum(v[0] for v in creatures.values())}):')
        for name, (count, p, th, cmc) in sorted(creatures.items(), key=lambda x: x[1][3]):
            lines.append(f'    {count}x {name} ({p}/{th}, CMC {cmc})')
    if spells:
        lines.append(f'  Spells ({sum(v[0] for v in spells.values())}):')
        for name, (count, cmc) in sorted(spells.items(), key=lambda x: x[1][1]):
            t = db.get_card(name)
            tags = sorted(t.tags) if t else []
            lines.append(f'    {count}x {name} (CMC {cmc}) [{", ".join(tags)}]')
    if lands:
        lines.append(f'  Lands ({sum(lands.values())}):')
        for name, count in sorted(lands.items()):
            lines.append(f'    {count}x {name}')
    if sideboard:
        lines.append(f'\n  Sideboard ({sum(sideboard.values())}):')
        for name, count in sorted(sideboard.items()):
            lines.append(f'    {count}x {name}')

    # Gameplan
    gp = get_gameplan(deck_name)
    if gp:
        lines.append(f'\nGameplan:')
        lines.append(f'  Mulligan keys: {sorted(gp.mulligan_keys)}')
        lines.append(f'  Mulligan lands: {gp.mulligan_min_lands}-{gp.mulligan_max_lands}')
        if gp.reactive_only:
            lines.append(f'  Reactive only: {sorted(gp.reactive_only)}')
        if gp.always_early:
            lines.append(f'  Always early: {sorted(gp.always_early)}')
        if gp.critical_pieces:
            lines.append(f'  Critical pieces: {sorted(gp.critical_pieces)}')
        lines.append('')
        for i, goal in enumerate(gp.goals):
            lines.append(f'  Goal {i+1}: {goal.goal_type.value} — {goal.description}')
            for role, cards in sorted(goal.card_roles.items()):
                lines.append(f'    {role}: {sorted(cards)}')
            if goal.resource_target:
                lines.append(f'    resource: {goal.resource_zone} >= {goal.resource_target}')
    else:
        lines.append(f'\nNo gameplan found.')

    return '\n'.join(lines)


def audit_deck(deck_name: str, n_games: int = 30, opponents: List[str] = None,
               seed_start: int = 60000) -> str:
    """Run N games across the field and report card-level gameplay statistics.

    Parses game logs to extract:
    - Win rate, avg win/loss turn, kill methods
    - Per-card stats: cast rate, avg turn cast, contribution to wins
    - Damage sources: which cards deal the most damage
    - Interaction: how often key pieces get removed
    - Mana curve: avg lands per turn, mana efficiency

    Usage:
        python run_meta.py --audit affinity -n 30
    """
    import re
    from collections import defaultdict

    runner = _get_runner()
    deck_data = MODERN_DECKS[deck_name]
    if opponents is None:
        opponents = [n for n in get_all_deck_names() if n != deck_name]

    # Collect stats across all games
    total_games = 0
    wins = 0
    win_turns = []
    loss_turns = []
    win_conditions = defaultdict(int)

    # Per-card tracking
    card_cast_count = defaultdict(int)       # card -> times cast
    card_cast_turns = defaultdict(list)      # card -> [turn numbers]
    card_cast_in_wins = defaultdict(int)     # card -> times cast in won games
    card_resolved = defaultdict(int)         # card -> times resolved
    card_countered = defaultdict(int)        # card -> times countered
    card_removed = defaultdict(int)          # card -> times removed/exiled from battlefield

    # Damage tracking
    attack_damage_total = 0
    attack_count = 0
    etb_damage_total = 0
    burn_damage_total = 0

    # Per-game creature stats
    creatures_deployed_total = 0
    equipment_equips_total = 0
    cards_drawn_total = 0

    # Mulligan stats
    mulligan_count = 0
    keep_7_count = 0

    for opp_name in opponents:
        opp_data = MODERN_DECKS[opp_name]
        games_vs = max(1, n_games // len(opponents))

        for i in range(games_vs):
            seed = seed_start + total_games * 500
            random.seed(seed)
            r = runner.run_game(
                deck_name, deck_data['mainboard'], opp_name, opp_data['mainboard'],
                deck1_sideboard=deck_data.get('sideboard', {}),
                deck2_sideboard=opp_data.get('sideboard', {}),
                verbose=True,
            )
            total_games += 1
            is_win = r.winner_deck == deck_name
            player_prefix = "P1"  # deck under audit is always P1

            if is_win:
                wins += 1
                win_turns.append(r.turns)
                win_conditions[r.win_condition] += 1
            else:
                loss_turns.append(r.turns)

            # Parse game log
            for line in r.game_log:
                # Cast detection: "T5 P1: Cast Thought Monitor (6U)"
                m = re.match(r'T(\d+) P1: Cast (.+?) \(', line)
                if m:
                    turn, card = int(m.group(1)), m.group(2)
                    card_cast_count[card] += 1
                    card_cast_turns[card].append(turn)
                    if is_win:
                        card_cast_in_wins[card] += 1

                # Resolve detection
                if line.startswith('T') and 'Resolve ' in line:
                    m2 = re.match(r'T\d+: Resolve (.+)', line)
                    if m2:
                        card_resolved[m2.group(1)] += 1

                # Counter detection: "CardName is countered"
                if 'is countered' in line:
                    m3 = re.match(r'T\d+: (.+?) is countered', line)
                    if m3:
                        card_countered[m3.group(1)] += 1

                # Removal detection: card moved from battlefield to exile/graveyard
                if 'moved battlefield ->' in line and 'P1' not in line.split('moved')[0]:
                    # This is an opponent's card being removed — skip
                    pass
                if 'moved battlefield ->' in line:
                    m4 = re.match(r'T\d+: (.+?) moved battlefield -> (exile|graveyard)', line)
                    if m4:
                        card_removed[m4.group(1)] += 1

                # Attack detection
                if f'{player_prefix}: Attack with' in line:
                    attack_count += 1
                    attackers = line.split('Attack with ')[1] if 'Attack with ' in line else ''
                    creatures_attacking = len(attackers.split(', ')) if attackers else 0

                # ETB damage: "ETB: deal N damage" or "ETB: draw N cards"
                if 'P1:' in line and 'ETB' in line and 'draw' in line:
                    m5 = re.search(r'draw (\d+) card', line)
                    if m5:
                        cards_drawn_total += int(m5.group(1))

                # Equip detection
                if f'{player_prefix}: Equip' in line:
                    equipment_equips_total += 1

                # Creature deployment
                if f'{player_prefix}: Cast' in line:
                    creatures_deployed_total += 1

                # Mulligan detection
                if 'P1 mulligans' in line:
                    mulligan_count += 1
                if 'P1 keeps 7' in line:
                    keep_7_count += 1

    # Build report
    lines = []
    win_pct = round(wins / max(1, total_games) * 100)
    avg_win_turn = round(sum(win_turns) / max(1, len(win_turns)), 1)
    avg_loss_turn = round(sum(loss_turns) / max(1, len(loss_turns)), 1)

    lines.append(f'=== AUDIT: {deck_name} ({total_games} games vs field) ===')
    lines.append(f'')
    lines.append(f'Win rate: {win_pct}% ({wins}/{total_games})')
    lines.append(f'Avg win turn: T{avg_win_turn}  |  Avg loss turn: T{avg_loss_turn}')
    if win_conditions:
        lines.append(f'Win conditions: {dict(win_conditions)}')
    mull_pct = round(mulligan_count / max(1, total_games) * 100)
    lines.append(f'Mulligan rate: {mull_pct}% ({mulligan_count}/{total_games})')
    lines.append(f'Equip actions: {equipment_equips_total} total ({equipment_equips_total/max(1,total_games):.1f}/game)')
    lines.append(f'ETB cards drawn: {cards_drawn_total} total ({cards_drawn_total/max(1,total_games):.1f}/game)')
    lines.append('')

    # Top cards by cast frequency
    lines.append('--- CARD CAST FREQUENCY (top 15) ---')
    lines.append(f'{"Card":<30s} {"Cast":>5s} {"Rate":>6s} {"AvgT":>5s} {"InWins":>7s} {"WinCR":>6s}')
    sorted_cards = sorted(card_cast_count.items(), key=lambda x: -x[1])
    for card, count in sorted_cards[:15]:
        rate = round(count / total_games, 1)
        avg_turn = round(sum(card_cast_turns[card]) / max(1, len(card_cast_turns[card])), 1)
        in_wins = card_cast_in_wins.get(card, 0)
        win_cast_rate = round(in_wins / max(1, count) * 100)
        lines.append(f'{card:<30s} {count:>5d} {rate:>5.1f}x {"T"+str(avg_turn):>5s} {in_wins:>5d}   {win_cast_rate:>4d}%')

    # Cards that get countered/removed
    lines.append('')
    lines.append('--- INTERACTION (countered / removed from battlefield) ---')
    lines.append(f'{"Card":<30s} {"Countered":>9s} {"Removed":>8s}')
    all_interacted = set(card_countered.keys()) | set(card_removed.keys())
    interacted_sorted = sorted(all_interacted, key=lambda c: -(card_countered.get(c, 0) + card_removed.get(c, 0)))
    for card in interacted_sorted[:15]:
        ct = card_countered.get(card, 0)
        rm = card_removed.get(card, 0)
        if ct + rm > 0:
            lines.append(f'{card:<30s} {ct:>9d} {rm:>8d}')

    # Win contribution: cards with highest win correlation
    lines.append('')
    lines.append('--- WIN CONTRIBUTION (cast rate in wins vs losses) ---')
    lines.append(f'{"Card":<30s} {"InWins":>7s} {"InLoss":>7s} {"Delta":>6s}')
    for card, count in sorted_cards[:20]:
        in_wins = card_cast_in_wins.get(card, 0)
        in_losses = count - in_wins
        if wins > 0 and total_games - wins > 0:
            rate_in_wins = in_wins / wins
            rate_in_losses = in_losses / max(1, total_games - wins)
            delta = rate_in_wins - rate_in_losses
            lines.append(f'{card:<30s} {rate_in_wins:>6.2f}x {rate_in_losses:>6.2f}x {delta:>+5.2f}')

    return '\n'.join(lines)


def run_verbose_game(deck1: str, deck2: str, seed: int = 42000) -> str:
    """Run a single verbose game, return the full log as string."""
    runner = _get_runner()
    runner.rng = random.Random(seed)
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


def run_bo3(deck1: str, deck2: str, seed: int = 42000) -> str:
    """Run a best-of-3 match with detailed text logs. Stops at 2 wins.

    Usage:
        python run_meta.py --bo3 affinity zoo -s 55555
    """
    runner = _get_runner()
    d1 = MODERN_DECKS[deck1]
    d2 = MODERN_DECKS[deck2]

    lines = []
    score = [0, 0]
    game_num = 0

    while score[0] < 2 and score[1] < 2:
        game_num += 1
        game_seed = seed + game_num - 1

        # Alternate who's on the play: G1 die roll, G2 loser of G1, G3 loser of G2
        if game_num == 1:
            p1_name, p1_data, p2_name, p2_data = deck1, d1, deck2, d2
        elif game_num == 2:
            # Loser of G1 goes first
            if last_winner == deck1:
                p1_name, p1_data, p2_name, p2_data = deck2, d2, deck1, d1
            else:
                p1_name, p1_data, p2_name, p2_data = deck1, d1, deck2, d2
        else:
            # Loser of G2 goes first
            if last_winner == deck1:
                p1_name, p1_data, p2_name, p2_data = deck2, d2, deck1, d1
            else:
                p1_name, p1_data, p2_name, p2_data = deck1, d1, deck2, d2

        lines.append('=' * 70)
        lines.append(f'  GAME {game_num}: {p1_name} (P1) vs {p2_name} (P2)  —  seed {game_seed}')
        lines.append(f'  Series: {deck1} {score[0]} - {score[1]} {deck2}')
        lines.append('=' * 70)
        lines.append('')

        runner.rng = random.Random(game_seed)
        random.seed(game_seed)
        r = runner.run_game(
            p1_name, p1_data['mainboard'], p2_name, p2_data['mainboard'],
            deck1_sideboard=p1_data.get('sideboard', {}),
            deck2_sideboard=p2_data.get('sideboard', {}),
            verbose=True,
        )

        lines.extend(r.game_log)
        lines.append('')

        p1_life = r.winner_life if r.winner == 0 else r.loser_life
        p2_life = r.winner_life if r.winner == 1 else r.loser_life
        lines.append(f'>>> {r.winner_deck} wins Game {game_num} on turn {r.turns} via {r.win_condition}')
        lines.append(f'    Final life: {p1_name}={p1_life}  {p2_name}={p2_life}')
        lines.append('')

        last_winner = r.winner_deck
        if r.winner_deck == deck1:
            score[0] += 1
        else:
            score[1] += 1

    lines.append('=' * 70)
    winner = deck1 if score[0] > score[1] else deck2
    lines.append(f'  MATCH RESULT: {winner} wins {max(score)}-{min(score)}')
    lines.append('=' * 70)

    return '\n'.join(lines)


def run_trace_game(deck1: str, deck2: str, seed: int = 42000) -> str:
    """Run a single game with full AI reasoning — shows hand, castable
    spells, EV scores, and chosen play each decision point.

    Usage:
        python run_meta.py --trace "Ruby Storm" "Dimir Midrange" --seed 42000
    """
    from ai.ev_player import EVPlayer
    from ai.ev_evaluator import snapshot_from_game

    runner = _get_runner()
    runner.rng = random.Random(seed)
    lines = []

    orig_main = EVPlayer.decide_main_phase
    orig_atk = EVPlayer.decide_attackers

    def traced_main(self, game, excluded_cards=None):
        # Run the real decision FIRST — no re-scoring, no RNG divergence
        result = orig_main(self, game, excluded_cards)

        # Read back the state and scored candidates from the decision
        me = game.players[self.player_idx]
        opp = game.players[1 - self.player_idx]
        hand_spells = [c.name for c in me.hand if not c.template.is_land]
        hand_lands = sum(1 for c in me.hand if c.template.is_land)
        mana = me.available_mana_estimate + me.mana_pool.total()
        bf_creatures = [f'{c.name} ({c.power}/{c.toughness})' for c in me.creatures]
        bf_other = [c.name for c in me.battlefield
                    if not c.template.is_creature and not c.template.is_land]
        opp_creatures = [f'{c.name} ({c.power}/{c.toughness})' for c in opp.creatures]
        gy_count = len(me.graveyard)

        lines.append(f'')
        lines.append(f'T{game.turn_number} {self.deck_name} | '
                     f'life={me.life} mana={mana} hand={len(hand_spells)}+{hand_lands}L gy={gy_count}')
        lines.append(f'  Hand: {hand_spells}')
        if bf_creatures:
            lines.append(f'  Board: {bf_creatures}')
        if bf_other:
            lines.append(f'  Permanents: {bf_other}')
        lines.append(f'  Opp board: {opp_creatures} (life={opp.life})')

        # Display candidates from the actual decision (no re-scoring)
        candidates = getattr(self, '_last_candidates', [])
        if candidates:
            lines.append(f'  EV scores:')
            for play in candidates[:6]:
                marker = ' <--' if play is candidates[0] else ''
                base = f'    {play.ev:+6.1f}  {play.action}: {play.card.name}{marker}'
                # Show lookahead breakdown for spells
                if play.action == "cast_spell" and play.lookahead_ev != 0:
                    h = play.heuristic_ev
                    la = play.lookahead_ev
                    parts = [f'h={h:+.1f} la={la:+.1f}']
                    if play.counter_pct > 0:
                        parts.append(f'ctr={play.counter_pct:.0%}')
                    if play.removal_pct > 0:
                        parts.append(f'rmv={play.removal_pct:.0%}')
                    base += f'  [{" ".join(parts)}]'
                lines.append(base)
            if len(candidates) > 6:
                lines.append(f'    ... +{len(candidates)-6} more')

        if result:
            lines.append(f'  >>> {result[0].upper()}: {result[1].name}')
        else:
            lines.append(f'  >>> PASS (threshold={self.profile.pass_threshold})')
        return result

    def traced_atk(self, game):
        result = orig_atk(self, game)
        if result:
            names = [c.name for c in result]
            lines.append(f'  >>> ATTACK: {names}')
        return result

    EVPlayer.decide_main_phase = traced_main
    EVPlayer.decide_attackers = traced_atk

    try:
        d1 = MODERN_DECKS[deck1]
        d2 = MODERN_DECKS[deck2]
        random.seed(seed)
        r = runner.run_game(
            deck1, d1['mainboard'], deck2, d2['mainboard'],
            deck1_sideboard=d1.get('sideboard', {}),
            deck2_sideboard=d2.get('sideboard', {}),
            verbose=True,
        )
        header = [f'=== {deck1} vs {deck2} (seed {seed}) ===',
                  f'Result: {r.winner_deck} wins T{r.turns} via {r.win_condition}',
                  f'Life: P1={r.winner_life if r.winner==0 else r.loser_life} '
                  f'P2={r.winner_life if r.winner==1 else r.loser_life}',
                  '']
        return '\n'.join(header + lines)
    finally:
        EVPlayer.decide_main_phase = orig_main
        EVPlayer.decide_attackers = orig_atk


RESULTS_FILE = os.path.join(os.path.dirname(__file__), 'metagame_results.json')


def save_results(result: Dict, path: str = RESULTS_FILE):
    """Save matrix/matchup/field results to JSON for later sessions."""
    import datetime
    # Convert tuple keys to strings for JSON
    data = {
        'timestamp': datetime.datetime.now().isoformat(),
        'type': 'matrix' if 'matrix' in result else 'field' if 'matchups' in result else 'matchup',
        'rankings': result.get('rankings', []),
        'names': result.get('names', []),
        'matrix': {f'{k[0]}|{k[1]}': v for k, v in result['matrix'].items()} if 'matrix' in result else {},
    }
    # Preserve any extra fields
    for key in result:
        if key not in ('matrix', 'rankings', 'names'):
            data[key] = result[key]

    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f'Results saved to {path}', file=sys.stderr)


def load_results(path: str = RESULTS_FILE) -> Optional[Dict]:
    """Load saved results from JSON."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    # Reconstruct tuple keys
    if data.get('matrix'):
        matrix = {}
        for key, val in data['matrix'].items():
            d1, d2 = key.split('|')
            matrix[(d1, d2)] = val
        data['matrix'] = matrix
    return data


def print_saved_results(path: str = RESULTS_FILE):
    """Load and print the last saved metagame results."""
    data = load_results(path)
    if not data:
        print(f'No saved results found at {path}')
        print(f'Run: python run_meta.py --matrix -n 50 --save')
        return

    print(f'Last run: {data.get("timestamp", "unknown")}')

    if data.get('matrix') and data.get('names'):
        print_matrix(data)
    elif data.get('matchups'):
        print_field(data)
    else:
        print(json.dumps(data, indent=2))


# ─── Pretty printing ─────────────────────────────────────────


def print_matrix(result: Dict):
    """Pretty-print a metagame matrix result."""
    names = result['names']
    matrix = result['matrix']
    tier1 = set(result.get('tier1', []))
    tier2 = set(result.get('tier2', []))

    print('\n=== METAGAME POWER RANKINGS ===')
    print(f'  (Meta-weighted WR uses T1+T2 opponents only)\n')
    print(f'  {"Deck":25s}  {"Flat":>5s}  {"Meta":>5s}')
    print(f'  {"":25s}  {"Avg":>5s}  {"WR":>5s}')
    print(f'  {"-"*25}  {"-"*5}  {"-"*5}')
    for avg, deck, meta_wr in result['rankings']:
        tier_tag = '[T1]' if deck in tier1 else '[T2]' if deck in tier2 else '    '
        bar = '#' * int(meta_wr / 2)
        print(f'  {deck:25s}  {avg:4.0f}%  {meta_wr:4.0f}%  {tier_tag} {bar}')

    print(f'\n  T1: {", ".join(sorted(tier1))}')
    print(f'  T2: {", ".join(sorted(tier2))}')

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
    parser.add_argument('--verbose', nargs=2, metavar=('DECK1', 'DECK2'), help='Run single game log (actions only)')
    parser.add_argument('--trace', nargs=2, metavar=('DECK1', 'DECK2'), help='Run single game with full AI reasoning')
    parser.add_argument('--bo3', nargs=2, metavar=('DECK1', 'DECK2'),
                        help='Best-of-3 match with detailed play-by-play log (board states, life, phases)')
    # Synonyms for --bo3 (so Claude and users can always find it)
    parser.add_argument('--match', nargs=2, metavar=('DECK1', 'DECK2'), help=argparse.SUPPRESS)
    parser.add_argument('--play-by-play', nargs=2, metavar=('DECK1', 'DECK2'), help=argparse.SUPPRESS)
    parser.add_argument('--pbp', nargs=2, metavar=('DECK1', 'DECK2'), help=argparse.SUPPRESS)
    parser.add_argument('--detailed', nargs=2, metavar=('DECK1', 'DECK2'), help=argparse.SUPPRESS)
    parser.add_argument('--game-log', nargs=2, metavar=('DECK1', 'DECK2'), help=argparse.SUPPRESS)
    parser.add_argument('--simulate', nargs=2, metavar=('DECK1', 'DECK2'), help=argparse.SUPPRESS)
    parser.add_argument('--games', '-n', type=int, default=20, help='Games per matchup (default 20)')
    parser.add_argument('--decks', '-d', type=int, default=None, help='Top N decks for matrix')
    parser.add_argument('--seed', '-s', type=int, default=42000, help='Seed for verbose/trace game')
    parser.add_argument('--deck', metavar='DECK', help='Show deck profile: list, gameplan, strategy')
    parser.add_argument('--audit', metavar='DECK', help='Run N games vs field, report card-level stats')
    parser.add_argument('--list', action='store_true', help='List available decks')
    parser.add_argument('--save', action='store_true', help='Save results to metagame_results.json')
    parser.add_argument('--results', action='store_true', help='Print last saved results (no sim)')
    args = parser.parse_args()

    if args.results:
        print_saved_results()
        sys.exit(0)

    if args.list:
        for name in get_all_deck_names():
            share = METAGAME_SHARES.get(name, 0)
            print(f'  {name:25s} ({share:.1f}% meta share)')
        sys.exit(0)

    # Resolve all deck name aliases
    if args.deck:
        print(inspect_deck(resolve_deck_name(args.deck)))
        sys.exit(0)

    if args.audit:
        print(audit_deck(resolve_deck_name(args.audit), n_games=args.games))
        sys.exit(0)

    # Bo3 / detailed match (many synonyms)
    bo3_args = (args.bo3 or args.match or getattr(args, 'play_by_play', None)
                or args.pbp or args.detailed or getattr(args, 'game_log', None)
                or args.simulate)
    if bo3_args:
        d1, d2 = resolve_deck_name(bo3_args[0]), resolve_deck_name(bo3_args[1])
        print(run_bo3(d1, d2, seed=args.seed))
    elif args.trace:
        d1, d2 = resolve_deck_name(args.trace[0]), resolve_deck_name(args.trace[1])
        print(run_trace_game(d1, d2, seed=args.seed))
    elif args.verbose:
        d1, d2 = resolve_deck_name(args.verbose[0]), resolve_deck_name(args.verbose[1])
        print(run_verbose_game(d1, d2, seed=args.seed))
    elif args.matchup:
        d1, d2 = resolve_deck_name(args.matchup[0]), resolve_deck_name(args.matchup[1])
        result = run_matchup(d1, d2, n_games=args.games)
        print_matchup(result)
        if args.save:
            save_results(result)
    elif args.field:
        result = run_field(resolve_deck_name(args.field), n_games=args.games)
        print_field(result)
        if args.save:
            save_results(result)
    else:
        # Default: run matrix
        result = run_meta_matrix(top_tier=args.decks, n_games=args.games)
        print_matrix(result)
        if args.save:
            save_results(result)
