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


def _trace_one_deck(p1_name: str, p2_name: str, seed: int) -> None:
    """Run the diagnostic for one deck pair at one seed.

    Builds a real game state, then calls `card_combo_evaluation`
    on each card in P1's opening hand, dumping per-card scores
    + branch decisions to stderr.
    """
    from engine.card_database import CardDatabase
    from engine.game_runner import GameRunner
    from engine.game_state import GameState
    from engine.cards import CardInstance
    from decks.modern_meta import MODERN_DECKS
    from ai.ev_evaluator import snapshot_from_game
    from ai.combo_evaluator import card_combo_evaluation, _BASELINE_CACHE
    from ai.ev_player import _get_archetype

    _BASELINE_CACHE.clear()

    db = CardDatabase("ModernAtomic.json")
    p1_decklist = MODERN_DECKS[p1_name]
    p2_decklist = MODERN_DECKS[p2_name]

    rng = random.Random(seed)
    runner = GameRunner(card_db=db, rng=rng)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"=== {p1_name} vs {p2_name} (seed {seed}) ===",
          file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    deck1 = runner.build_deck(p1_decklist["mainboard"])
    deck2 = runner.build_deck(p2_decklist["mainboard"])
    game = GameState(rng=rng)
    game.setup_game(deck1, deck2)
    game.players[0].deck_name = p1_name
    game.players[1].deck_name = p2_name

    # Load P1 sideboard so simulator can see SB closers / extenders
    if p1_decklist.get("sideboard"):
        for template in runner.build_deck(p1_decklist["sideboard"]):
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
    archetype = _get_archetype(p1_name)

    print(f"\nHand:       {[c.name for c in me.hand]}", file=sys.stderr)
    print(f"SB top-5:   {[c.name for c in (me.sideboard or [])][:5]}",
          file=sys.stderr)
    print(f"Library:    {len(me.library)} cards",
          file=sys.stderr)
    print(f"Archetype:  {archetype}\n", file=sys.stderr)

    for card in list(me.hand):
        score = card_combo_evaluation(
            card=card, snap=snap, me=me, game=game,
            player_idx=0, archetype=archetype,
        )
        # Trace emitted to stderr by combo_evaluator; this is the summary
        print(f"  {card.name:40s} → {score:+8.2f}", file=sys.stderr)


def main() -> None:
    """Multi-deck diagnostic harness — Storm + Living End + Amulet
    Titan, each vs Affinity, at the same seed.

    Usage: MTGSIM_COMBO_TRACE=1 python tools/diag_combo_evaluator_trace.py

    The combo_evaluator's chain-progress credit (step 4) should
    produce non-zero scores for chain-fuel cards in build-up
    states across ALL combo patterns (storm, cascade,
    reanimation).  Diagnostic verifies this without a live wire-up.
    """
    if os.environ.get("MTGSIM_COMBO_TRACE", "") != "1":
        print("WARNING: MTGSIM_COMBO_TRACE is not set; trace will be silent.",
              file=sys.stderr)
        print("Set MTGSIM_COMBO_TRACE=1 in env to capture diagnostics.",
              file=sys.stderr)

    seed = 50000
    for p1_name in ("Ruby Storm", "Living End", "Amulet Titan"):
        _trace_one_deck(p1_name, "Affinity", seed)

    print(f"\n{'='*60}", file=sys.stderr)
    print("=== End of multi-deck trace ===", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)


if __name__ == "__main__":
    main()
