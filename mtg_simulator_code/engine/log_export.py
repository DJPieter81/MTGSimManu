"""
Log export utilities for the MTG simulator.

Provides three export functions:
1. export_game_log() - Export a single game's turn-by-turn log to a text file
2. export_match_log() - Export a full bo3 match log with sideboard decisions
3. export_stress_test_results() - Export stress test results to CSV and JSON
"""

from __future__ import annotations
import csv
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any


def export_game_log(
    game_log: List[str],
    deck1_name: str,
    deck2_name: str,
    winner: int,
    turns: int,
    output_path: str,
    game_number: int = 1,
    metadata: Optional[Dict] = None,
) -> str:
    """
    Export a single game's turn-by-turn log to a readable text file.

    Returns the output file path.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    winner_name = deck1_name if winner == 0 else deck2_name if winner == 1 else "Draw"

    with open(output_path, "w") as f:
        f.write("=" * 72 + "\n")
        f.write(f"  MTG Game Log - Game {game_number}\n")
        f.write("=" * 72 + "\n")
        f.write(f"  Player 1: {deck1_name}\n")
        f.write(f"  Player 2: {deck2_name}\n")
        f.write(f"  Winner:   {winner_name}\n")
        f.write(f"  Turns:    {turns}\n")
        if metadata:
            for key, value in metadata.items():
                f.write(f"  {key}: {value}\n")
        f.write("=" * 72 + "\n\n")

        for line in game_log:
            f.write(line + "\n")

        f.write("\n" + "=" * 72 + "\n")
        f.write(f"  Game Over - {winner_name} wins in {turns} turns\n")
        f.write("=" * 72 + "\n")

    return output_path


def export_match_log(
    match_result: Any,  # Bo3MatchResult
    output_path: str,
) -> str:
    """
    Export a full bo3 match log with sideboard decisions to a text file.

    Returns the output file path.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    r = match_result
    match_winner = r.deck1_name if r.match_winner == 0 else r.deck2_name

    with open(output_path, "w") as f:
        f.write("=" * 72 + "\n")
        f.write(f"  Best-of-3 Match Log\n")
        f.write("=" * 72 + "\n")
        f.write(f"  {r.deck1_name} vs {r.deck2_name}\n")
        f.write(f"  Match Score: {r.match_score[0]}-{r.match_score[1]}\n")
        f.write(f"  Match Winner: {match_winner}\n")
        f.write(f"  Games Played: {len(r.game_results)}\n")
        f.write("=" * 72 + "\n\n")

        for game_idx, game_result in enumerate(r.game_results):
            game_num = game_idx + 1
            gw = r.deck1_name if game_result.winner == 0 else r.deck2_name
            f.write("-" * 72 + "\n")
            f.write(f"  Game {game_num} - Winner: {gw} (Turn {game_result.turns})\n")
            f.write("-" * 72 + "\n\n")

            # Write game log
            if game_idx < len(r.game_logs) and r.game_logs[game_idx]:
                for line in r.game_logs[game_idx]:
                    f.write(f"  {line}\n")
            else:
                f.write("  (No detailed log available)\n")

            f.write("\n")

            # Write sideboard decisions after games 1 and 2
            if game_idx < len(r.sideboard_decisions):
                sb = r.sideboard_decisions[game_idx]
                f.write("~" * 72 + "\n")
                f.write(f"  Sideboard Changes ({sb['between_games']})\n")
                f.write("~" * 72 + "\n")
                for pkey in ["player1", "player2"]:
                    p = sb[pkey]
                    f.write(f"\n  {p['deck']}:\n")
                    if p.get("note"):
                        f.write(f"    {p['note']}\n")
                    if p["in"]:
                        f.write("    IN:  ")
                        parts = [f"{count}x {name}" for name, count in p["in"].items()]
                        f.write(", ".join(parts) + "\n")
                    if p["out"]:
                        f.write("    OUT: ")
                        parts = [f"{count}x {name}" for name, count in p["out"].items()]
                        f.write(", ".join(parts) + "\n")
                    if not p["in"] and not p["out"] and not p.get("note"):
                        f.write("    (No changes)\n")
                f.write("\n")

        f.write("=" * 72 + "\n")
        f.write(f"  Match Complete: {match_winner} wins {r.match_score[0]}-{r.match_score[1]}\n")
        f.write("=" * 72 + "\n")

    return output_path


def export_stress_test_csv(
    results: List[Dict],
    output_path: str,
) -> str:
    """
    Export stress test results to CSV.

    Each row is one game/match with columns for decks, winner, turns,
    damage dealt, spells cast, etc.

    Returns the output file path.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if not results:
        return output_path

    # Determine fieldnames from first result
    fieldnames = list(results[0].keys())

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow(row)

    return output_path


def export_stress_test_json(
    results: List[Dict],
    matchup_summary: Dict,
    output_path: str,
    metadata: Optional[Dict] = None,
) -> str:
    """
    Export stress test results to JSON with matchup summary.

    Returns the output file path.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    export_data = {
        "generated_at": datetime.now().isoformat(),
        "metadata": metadata or {},
        "matchup_summary": matchup_summary,
        "game_results": results,
    }

    with open(output_path, "w") as f:
        json.dump(export_data, f, indent=2, default=str)

    return output_path
