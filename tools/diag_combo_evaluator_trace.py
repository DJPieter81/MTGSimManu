#!/usr/bin/env python3
"""Diagnostic harness for `ai/combo_evaluator.card_combo_evaluation`.

Per docs/PHASE_D_FOURTH_ATTEMPT.md, the live wire-up has collapsed
Storm five times.  This script runs `card_combo_evaluation` on a
representative Storm hand state with the env-gated trace ON, so the
per-card branch decisions are inspectable without doing another
risky wire-up attempt.

Usage:
    MTGSIM_COMBO_TRACE=1 python tools/diag_combo_evaluator_trace.py

The script bootstraps a real Ruby Storm game state from
`decks/modern_meta.py`, runs the AI's main-phase decision via the
combo_evaluator (NOT through ev_player), and dumps the trace to
stderr.  Inspect the output to find which branches fire for chain-
fuel cards in the failing collapse states.

This is a TEST BENCH — combo_evaluator's live wire-up is not
attempted.  The script just exercises the evaluator in isolation
to surface scoring decisions.
"""
from __future__ import annotations

import os
import random
import sys
from pathlib import Path

# Ensure project root is on sys.path so engine/ai/decks imports resolve
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    """Run a representative Storm scoring trace.

    Builds a real Ruby Storm vs Affinity game state via the
    standard GameRunner, then calls `card_combo_evaluation`
    directly on each card in the Storm player's hand at the
    start of each turn — capturing scoring decisions without
    actually wiring the evaluator into the live decision path.
    """
    if os.environ.get("MTGSIM_COMBO_TRACE", "") != "1":
        print("WARNING: MTGSIM_COMBO_TRACE is not set; trace will be silent.",
              file=sys.stderr)
        print("Set MTGSIM_COMBO_TRACE=1 in env to capture diagnostics.",
              file=sys.stderr)

    # Import here so MTGSIM_COMBO_TRACE is read after env is set
    from engine.card_database import CardDatabase
    from engine.game_runner import GameRunner
    from engine.game_state import GameState
    from engine.cards import CardInstance
    from decks.modern_meta import MODERN_DECKS
    from ai.ev_evaluator import snapshot_from_game
    from ai.combo_evaluator import card_combo_evaluation

    db = CardDatabase("ModernAtomic.json")
    storm_decklist = MODERN_DECKS["Ruby Storm"]
    affinity_decklist = MODERN_DECKS["Affinity"]

    seed = 50000
    rng = random.Random(seed)
    runner = GameRunner(card_db=db, rng=rng)

    print(f"=== Storm vs Affinity (seed {seed}) — diagnostic harness ===",
          file=sys.stderr)

    # Build decks + setup game manually so we can intercept the hand
    deck1 = runner.build_deck(storm_decklist["mainboard"])
    deck2 = runner.build_deck(affinity_decklist["mainboard"])
    game = GameState(rng=rng)
    game.setup_game(deck1, deck2)
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Affinity"

    # Load Storm's sideboard so the simulator can see Grapeshot/PiF
    if storm_decklist.get("sideboard"):
        for template in runner.build_deck(storm_decklist["sideboard"]):
            card = CardInstance(
                template=template, owner=0, controller=0,
                instance_id=game.next_instance_id(),
            )
            card.zone = "sideboard"
            if not hasattr(game.players[0], "sideboard"):
                game.players[0].sideboard = []
            game.players[0].sideboard.append(card)

    me = game.players[0]
    snap = snapshot_from_game(game, 0)

    print(f"\nStorm hand: {[c.name for c in me.hand]}",
          file=sys.stderr)
    print(f"Sideboard top-5: {[c.name for c in (me.sideboard or [])][:5]}",
          file=sys.stderr)
    print(f"Library size: {len(me.library)}\n",
          file=sys.stderr)

    for card in list(me.hand):
        score = card_combo_evaluation(
            card=card, snap=snap, me=me, game=game,
            player_idx=0, archetype="storm",
        )
        # Trace already emitted to stderr; print summary
        print(f"  {card.name:30s} → score {score:+8.2f}",
              file=sys.stderr)

    print("\n=== End of trace ===", file=sys.stderr)


if __name__ == "__main__":
    main()
