"""Goldfish a deck: its AI against an opponent that does nothing.

The opponent's deck is basic lands only, so it never casts a spell, never
attacks and never blocks. The turn the deck kills it measures the deck's
own speed with every interaction removed -- the measurement a diagnostic
needs before it can say whether a deck loses to its opponents or to its
own play (docs/diagnostics/2026-10-15_ruby_storm_goldfish_speed.md).

    python tools/goldfish.py "Ruby Storm" -n 80 -s 70000 --side play
    python tools/goldfish.py "Ruby Storm" -n 10 -s 70000 --side draw \
        --out runs.jsonl --dump logs/ --trace

Each game is seeded like a matrix game (`random.seed` and the runner's rng
set to the seed), with the game deadline neutralised as the seeded
replays run it (`SEEDED_REPLAY_TIMEOUT_SECONDS`), and the LLM decision
scorer offline. `--side` fixes who plays first. `--trace` writes every
main-phase decision of the goldfished deck (its scored candidates) into
the dumped logs.

The summary prints, per side: kills, losses by how (an empty library is
"mill"), kills by each turn, and the median kill turn.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The opponent: a deck of one basic land, which only ever plays lands.
GOLDFISH_NAME = "Goldfish"
GOLDFISH_LAND = "Wastes"
DECK_SIZE = 60  # rules constant: a constructed deck's minimum size (CR 100.2a)


def _traced_main(deck_name):
    """Wrap the main-phase decision so the deck's scored candidates are
    written into its game log (the decision itself is unchanged)."""
    from ai.ev_player import EVPlayer
    orig = EVPlayer.decide_main_phase

    def traced(self, game, excluded_cards=None, excluded_activations=None):
        result = orig(self, game, excluded_cards,
                      excluded_activations=excluded_activations)
        if self.deck_name == deck_name:
            me = game.players[self.player_idx]
            game.log.append(
                f"  [TRACE] mana={me.available_mana_estimate + me.mana_pool.total()} "
                f"pool={me.mana_pool.total()} storm={game._global_storm_count} "
                f"life={me.life} opp={game.players[1 - self.player_idx].life} "
                f"lib={len(me.library)} hand={[c.name for c in me.hand]}")
            for p in getattr(self, '_last_candidates', []):
                game.log.append(f"      {p.ev:+8.2f} {p.action}: "
                                f"{p.card.name} [{p.card.zone}]")
            chosen = (f"{result[0]}: {result[1].name}" if result else "PASS")
            game.log.append(f"    >>> {chosen}")
        return result

    EVPlayer.decide_main_phase = traced


def run(deck_name, n, seed0, side, out=None, dump=None, trace=False):
    os.environ["MTG_LLM_DECISION_SCORER_OFFLINE"] = "1"
    import logging
    logging.disable(logging.CRITICAL)
    import ai.constants as ai_constants
    from ai.scoring_constants import SEEDED_REPLAY_TIMEOUT_SECONDS
    ai_constants.GAME_TIMEOUT_SECONDS = SEEDED_REPLAY_TIMEOUT_SECONDS
    from decks.modern_meta import MODERN_DECKS
    from engine.card_database import CardDatabase
    from engine.game_runner import GameRunner

    if trace:
        _traced_main(deck_name)
    deck = MODERN_DECKS[deck_name]
    runner = GameRunner(CardDatabase())
    first = 0 if side == "play" else 1
    if dump:
        os.makedirs(dump, exist_ok=True)
    rows = []
    sink = open(out, "w") if out else None
    try:
        for i in range(n):
            seed = seed0 + i
            random.seed(seed)
            runner.rng.seed(seed)
            g = runner.run_game(deck_name, deck["mainboard"], GOLDFISH_NAME,
                                {GOLDFISH_LAND: DECK_SIZE},
                                deck1_sideboard=deck.get("sideboard", {}),
                                forced_first_player=first,
                                verbose=bool(dump))
            row = {"seed": seed, "side": side, "won": g.winner == 0,
                   "turns": g.turns, "how": g.win_condition,
                   "mulligans": g.mulligan_count[0]}
            rows.append(row)
            if sink:
                sink.write(json.dumps(row) + "\n")
                sink.flush()
            if dump:
                with open(os.path.join(dump, f"{side}_{seed}.txt"), "w") as fh:
                    fh.write("\n".join(g.game_log) + "\n")
    finally:
        if sink:
            sink.close()
    return rows


def summarise(rows):
    """Kills, losses by how, kills by each turn and the median kill turn."""
    kills = sorted(r["turns"] for r in rows if r["won"])
    losses = Counter(r["how"] for r in rows if not r["won"])
    by_turn = {}
    if kills:
        for t in range(min(kills), max(kills) + 1):
            by_turn[t] = sum(1 for k in kills if k <= t)
    return {"games": len(rows), "kills": len(kills), "losses": dict(losses),
            "kills_by_turn": by_turn,
            "median_kill_turn": statistics.median(kills) if kills else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("deck")
    ap.add_argument("-n", type=int, default=40)
    ap.add_argument("-s", "--seed", type=int, default=70000)
    ap.add_argument("--side", choices=("play", "draw"), default="play")
    ap.add_argument("--out", help="write one JSON row per game")
    ap.add_argument("--dump", help="directory for each game's log")
    ap.add_argument("--trace", action="store_true",
                    help="write the deck's scored candidates into the logs")
    a = ap.parse_args(argv)
    rows = run(a.deck, a.n, a.seed, a.side, a.out, a.dump, a.trace)
    print(json.dumps(summarise(rows), indent=1))


if __name__ == "__main__":
    main()
