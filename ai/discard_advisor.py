"""
Discard advisor — lifted from engine/game_state.py (Commit 6).

The legacy `_choose_self_discard` method embedded discard-scoring
heuristics directly in the engine layer, violating the CLAUDE.md
"engine never scores" architectural rule. This module implements
the same decisions using the oracle-text + tag signals that were
inside engine/game_state.py, but exposed via the
`callbacks.GameCallbacks.choose_discard` protocol method.

Installed by the AI callbacks wiring at game-runner setup time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState


def choose_discard(game: "GameState", player_idx: int,
                   hand: List["CardInstance"],
                   self_discard: bool) -> "CardInstance":
    """Pick the best card to discard.

    self_discard=True means the player chose to discard (Faithful
    Mending, Wrenn's Resolve random discard, etc.) — the player wants
    to maximise the value of the discard by binning cards that either
    belong in the graveyard (flashback, escape, reanimate targets) or
    are excess (flooded lands, redundant copies).

    self_discard=False means an opponent forced the discard
    (Thoughtseize, Grief) — the player wants to minimise the damage
    by binning the least-valuable card. In this direction we still
    use the same score (the scoring naturally surfaces the lowest-
    value cards at lowest scores), but a future AI implementation
    might invert the selection. Here we preserve the engine's legacy
    behaviour: highest-CMC card loses first.
    """
    if not hand:
        raise ValueError("choose_discard called with empty hand")
    if len(hand) == 1:
        return hand[0]

    if not self_discard:
        # Legacy non-AI behaviour: sort by CMC desc, take head.
        return max(hand, key=lambda c: c.template.cmc or 0)

    # Self-discard: score-based choice, highest score = discard first.
    player = game.players[player_idx]
    lands_on_field = len(player.lands)
    lands_in_hand = sum(1 for c in hand if c.template.is_land)

    def discard_score(card: "CardInstance") -> int:
        t = card.template
        score = 0

        # Cards with flashback/escape WANT to be in the graveyard.
        if t.escape_cost is not None:
            score += 100  # Escape (Phlage) — great to discard
        if 'flashback' in t.tags:
            score += 90

        # High-CMC creatures are reanimation targets.
        if t.is_creature and t.cmc >= 5:
            score += 80 + t.cmc

        # Excess lands (4+ in hand with 3+ already on battlefield).
        if t.is_land:
            if lands_in_hand > 1 and lands_on_field >= 3:
                score += 50
            elif lands_in_hand > 2:
                score += 40

        # Protection/reactive spells are lower priority to keep.
        if 'counterspell' in t.tags and not t.is_creature:
            score += 20

        # Combo pieces and key spells should be kept (lower score).
        # Exception: high-CMC creatures are reanimation targets.
        if any(tag in t.tags for tag in ('combo', 'tutor')):
            if not (t.is_creature and t.cmc >= 5):
                score -= 30

        # Removal is moderately important — slightly prefer to keep.
        if 'removal' in t.tags:
            score += 10

        return score

    return max(hand, key=discard_score)
