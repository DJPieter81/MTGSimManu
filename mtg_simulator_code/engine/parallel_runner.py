"""
MTG Parallel Simulation Runner - v2
Executes bulk game or match (best-of-3) simulations in parallel using multiprocessing.
Designed for background execution with no visual output - pure data collection.

v2 additions:
- Best-of-3 match mode with sideboarding
- Improved progress tracking
- Both game-level and match-level result output
"""
from __future__ import annotations
import os
import sys
import json
import time
import random
import multiprocessing as mp
from multiprocessing import Pool, Manager
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path
from functools import partial
import traceback

sys.path.insert(0, '/home/ubuntu/mtg_simulator')


@dataclass
class SimulationConfig:
    """Configuration for a batch of simulations."""
    matchups: List[Tuple[str, str]]
    games_per_matchup: int = 100
    num_workers: int = 0  # 0 = auto
    seed: int = 0
    output_dir: str = "/home/ubuntu/mtg_simulator/output"
    card_db_path: str = "/home/ubuntu/mtg_simulator/ModernAtomic.json"
    include_logs: bool = False
    max_turns: int = 50
    metagame_weighted: bool = False
    test_deck_name: str = ""
    test_deck_list: Dict[str, int] = field(default_factory=dict)
    # v2: match mode (best-of-3 with sideboarding)
    match_mode: bool = False


def _run_single_game(args: Tuple) -> dict:
    """Worker function for a single game simulation."""
    try:
        # Support both old (9-tuple) and new (11-tuple with sideboards) format
        if len(args) >= 11:
            (deck1_name, deck1_list, deck2_name, deck2_list,
             card_db_path, seed, game_idx, max_turns, include_logs,
             deck1_sb, deck2_sb) = args[:11]
        else:
            (deck1_name, deck1_list, deck2_name, deck2_list,
             card_db_path, seed, game_idx, max_turns, include_logs) = args[:9]
            deck1_sb, deck2_sb = {}, {}

        from engine.card_database import CardDatabase
        from engine.game_runner import GameRunner

        db = _get_cached_db(card_db_path)
        rng = random.Random(seed + game_idx)
        runner = GameRunner(db, rng)

        result = runner.run_game(
            deck1_name, deck1_list,
            deck2_name, deck2_list,
            verbose=include_logs,
            deck1_sideboard=deck1_sb or None,
            deck2_sideboard=deck2_sb or None,
        )

        return {
            "game_idx": game_idx,
            "winner": result.winner,
            "winner_deck": result.winner_deck,
            "loser_deck": result.loser_deck,
            "turns": result.turns,
            "winner_life": result.winner_life,
            "loser_life": result.loser_life,
            "win_condition": result.win_condition,
            "deck1_name": result.deck1_name,
            "deck2_name": result.deck2_name,
            "deck1_lands_played": result.deck1_lands_played,
            "deck2_lands_played": result.deck2_lands_played,
            "deck1_spells_cast": result.deck1_spells_cast,
            "deck2_spells_cast": result.deck2_spells_cast,
            "deck1_damage_dealt": result.deck1_damage_dealt,
            "deck2_damage_dealt": result.deck2_damage_dealt,
            "on_play_won": result.on_play_won,
            "mulligan_p1": result.mulligan_count[0],
            "mulligan_p2": result.mulligan_count[1],
        }
    except Exception as e:
        return {
            "game_idx": args[6] if len(args) > 6 else -1,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


def _run_single_match(args: Tuple) -> dict:
    """Worker function for a single best-of-3 match simulation."""
    try:
        (deck1_name, deck1_data, deck2_name, deck2_data,
         card_db_path, seed, match_idx, max_turns, include_logs) = args

        from engine.card_database import CardDatabase
        from engine.game_runner import GameRunner

        db = _get_cached_db(card_db_path)
        rng = random.Random(seed + match_idx)
        runner = GameRunner(db, rng)

        result = runner.run_match(
            deck1_name, deck1_data,
            deck2_name, deck2_data,
            verbose=include_logs,
        )

        # Flatten match result
        game_results = []
        for g in result.games:
            game_results.append({
                "game_number": g.game_number,
                "winner": g.winner,
                "winner_deck": g.winner_deck,
                "turns": g.turns,
                "win_condition": g.win_condition,
            })

        return {
            "match_idx": match_idx,
            "winner": result.winner,
            "winner_deck": result.winner_deck,
            "loser_deck": result.loser_deck,
            "match_score": list(result.match_score),
            "deck1_name": result.deck1_name,
            "deck2_name": result.deck2_name,
            "games_played": len(result.games),
            "game_results": game_results,
            # Aggregate stats from individual games
            "total_turns": sum(g.turns for g in result.games),
            "deck1_total_damage": sum(g.deck1_damage_dealt for g in result.games),
            "deck2_total_damage": sum(g.deck2_damage_dealt for g in result.games),
            "deck1_total_spells": sum(g.deck1_spells_cast for g in result.games),
            "deck2_total_spells": sum(g.deck2_spells_cast for g in result.games),
        }
    except Exception as e:
        return {
            "match_idx": args[6] if len(args) > 6 else -1,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


# Module-level cache for card database per worker process
_db_cache: Dict[str, 'CardDatabase'] = {}


def _get_cached_db(path: str):
    """Cache the card database per worker process."""
    global _db_cache
    if path not in _db_cache:
        from engine.card_database import CardDatabase
        _db_cache[path] = CardDatabase(path)
    return _db_cache[path]


class ParallelSimulator:
    """Runs bulk MTG simulations in parallel."""

    def __init__(self, config: SimulationConfig):
        self.config = config
        self.results: List[dict] = []
        self._start_time: float = 0
        self._total_games: int = 0

        if config.num_workers <= 0:
            self.num_workers = max(1, mp.cpu_count() - 1)
        else:
            self.num_workers = config.num_workers

        os.makedirs(config.output_dir, exist_ok=True)

    def prepare_game_args(self) -> List[Tuple]:
        """Prepare argument tuples for all games to simulate."""
        from decks.modern_meta import MODERN_DECKS

        args_list = []
        base_seed = self.config.seed if self.config.seed else random.randint(1, 999999)
        game_idx = 0

        for deck1_name, deck2_name in self.config.matchups:
            if deck1_name == self.config.test_deck_name and self.config.test_deck_list:
                deck1_list = self.config.test_deck_list
                deck1_sb = {}
            else:
                deck1_data = MODERN_DECKS.get(deck1_name, {})
                deck1_list = deck1_data.get("mainboard", {})
                deck1_sb = deck1_data.get("sideboard", {})

            if deck2_name == self.config.test_deck_name and self.config.test_deck_list:
                deck2_list = self.config.test_deck_list
                deck2_sb = {}
            else:
                deck2_data = MODERN_DECKS.get(deck2_name, {})
                deck2_list = deck2_data.get("mainboard", {})
                deck2_sb = deck2_data.get("sideboard", {})

            if not deck1_list or not deck2_list:
                continue

            for i in range(self.config.games_per_matchup):
                args_list.append((
                    deck1_name, deck1_list,
                    deck2_name, deck2_list,
                    self.config.card_db_path,
                    base_seed, game_idx,
                    self.config.max_turns,
                    self.config.include_logs,
                    deck1_sb, deck2_sb,
                ))
                game_idx += 1

        self._total_games = len(args_list)
        return args_list

    def prepare_match_args(self) -> List[Tuple]:
        """Prepare argument tuples for all matches (best-of-3) to simulate."""
        from decks.modern_meta import MODERN_DECKS

        args_list = []
        base_seed = self.config.seed if self.config.seed else random.randint(1, 999999)
        match_idx = 0

        for deck1_name, deck2_name in self.config.matchups:
            if deck1_name == self.config.test_deck_name and self.config.test_deck_list:
                deck1_data = {"mainboard": self.config.test_deck_list, "sideboard": {}}
            else:
                deck1_data = MODERN_DECKS.get(deck1_name, {})

            if deck2_name == self.config.test_deck_name and self.config.test_deck_list:
                deck2_data = {"mainboard": self.config.test_deck_list, "sideboard": {}}
            else:
                deck2_data = MODERN_DECKS.get(deck2_name, {})

            if not deck1_data.get("mainboard") or not deck2_data.get("mainboard"):
                continue

            for i in range(self.config.games_per_matchup):
                args_list.append((
                    deck1_name, deck1_data,
                    deck2_name, deck2_data,
                    self.config.card_db_path,
                    base_seed, match_idx,
                    self.config.max_turns,
                    self.config.include_logs,
                ))
                match_idx += 1

        self._total_games = len(args_list)
        return args_list

    def run(self, progress_callback=None) -> List[dict]:
        """Run all simulations in parallel."""
        if self.config.match_mode:
            return self._run_matches(progress_callback)
        return self._run_games(progress_callback)

    def _run_games(self, progress_callback=None) -> List[dict]:
        """Run game-level simulations in parallel."""
        args_list = self.prepare_game_args()
        if not args_list:
            print("No games to simulate!")
            return []

        print(f"Starting {self._total_games} games across {self.num_workers} workers...")
        self._start_time = time.time()

        results = []
        completed = 0

        with Pool(processes=self.num_workers) as pool:
            for result in pool.imap_unordered(_run_single_game, args_list,
                                               chunksize=max(1, len(args_list) // (self.num_workers * 4))):
                results.append(result)
                completed += 1

                if progress_callback and completed % 50 == 0:
                    progress_callback(completed, self._total_games)

                if completed % 100 == 0:
                    elapsed = time.time() - self._start_time
                    rate = completed / elapsed if elapsed > 0 else 0
                    eta = (self._total_games - completed) / rate if rate > 0 else 0
                    print(f"  Progress: {completed}/{self._total_games} "
                          f"({rate:.1f} games/sec, ETA: {eta:.0f}s)")

        elapsed = time.time() - self._start_time
        errors = sum(1 for r in results if "error" in r)
        print(f"Completed {len(results)} games in {elapsed:.1f}s "
              f"({len(results)/elapsed:.1f} games/sec, {errors} errors)")

        self.results = results
        return results

    def _run_matches(self, progress_callback=None) -> List[dict]:
        """Run match-level (best-of-3) simulations in parallel."""
        args_list = self.prepare_match_args()
        if not args_list:
            print("No matches to simulate!")
            return []

        print(f"Starting {self._total_games} matches (bo3) across {self.num_workers} workers...")
        self._start_time = time.time()

        results = []
        completed = 0

        with Pool(processes=self.num_workers) as pool:
            for result in pool.imap_unordered(_run_single_match, args_list,
                                               chunksize=max(1, len(args_list) // (self.num_workers * 4))):
                results.append(result)
                completed += 1

                if progress_callback and completed % 50 == 0:
                    progress_callback(completed, self._total_games)

                if completed % 50 == 0:
                    elapsed = time.time() - self._start_time
                    rate = completed / elapsed if elapsed > 0 else 0
                    eta = (self._total_games - completed) / rate if rate > 0 else 0
                    print(f"  Progress: {completed}/{self._total_games} "
                          f"({rate:.1f} matches/sec, ETA: {eta:.0f}s)")

        elapsed = time.time() - self._start_time
        errors = sum(1 for r in results if "error" in r)
        print(f"Completed {len(results)} matches in {elapsed:.1f}s "
              f"({len(results)/elapsed:.1f} matches/sec, {errors} errors)")

        self.results = results
        return results

    def run_sequential(self, progress_callback=None) -> List[dict]:
        """Run simulations sequentially (for debugging)."""
        if self.config.match_mode:
            args_list = self.prepare_match_args()
            worker_fn = _run_single_match
        else:
            args_list = self.prepare_game_args()
            worker_fn = _run_single_game

        if not args_list:
            print("No games to simulate!")
            return []

        label = "matches" if self.config.match_mode else "games"
        print(f"Starting {self._total_games} {label} sequentially...")
        self._start_time = time.time()

        results = []
        for i, args in enumerate(args_list):
            result = worker_fn(args)
            results.append(result)

            if (i + 1) % 10 == 0:
                elapsed = time.time() - self._start_time
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                print(f"  Progress: {i+1}/{self._total_games} ({rate:.1f} {label}/sec)")

        elapsed = time.time() - self._start_time
        print(f"Completed {len(results)} {label} in {elapsed:.1f}s")

        self.results = results
        return results

    def save_results(self, filename: str = None) -> str:
        """Save results to JSON file."""
        if not filename:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            mode_tag = "match" if self.config.match_mode else "game"
            filename = f"sim_results_{mode_tag}_{timestamp}.json"

        filepath = os.path.join(self.config.output_dir, filename)

        output = {
            "metadata": {
                "total_simulations": len(self.results),
                "games_per_matchup": self.config.games_per_matchup,
                "num_workers": self.num_workers,
                "seed": self.config.seed,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "matchups": self.config.matchups,
                "match_mode": self.config.match_mode,
            },
            "results": self.results,
        }

        with open(filepath, 'w') as f:
            json.dump(output, f, indent=2)

        print(f"Results saved to {filepath}")
        return filepath

    @staticmethod
    def generate_all_matchups(deck_names: List[str]) -> List[Tuple[str, str]]:
        """Generate all unique matchup pairs."""
        matchups = []
        for i, d1 in enumerate(deck_names):
            for d2 in deck_names[i+1:]:
                matchups.append((d1, d2))
        return matchups

    @staticmethod
    def generate_test_matchups(test_deck: str,
                                field_decks: List[str]) -> List[Tuple[str, str]]:
        """Generate matchups for testing one deck against the field."""
        return [(test_deck, d) for d in field_decks if d != test_deck]
