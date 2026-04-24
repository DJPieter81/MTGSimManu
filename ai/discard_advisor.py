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

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState


def choose_discard(game: "GameState", player_idx: int,
                   hand: List["CardInstance"],
                   self_discard: bool) -> Optional["CardInstance"]:
    """Pick the best card to discard.

    self_discard=True means the player chose to discard (Faithful
    Mending, Wrenn's Resolve random discard, etc.) — the player wants
    to maximise the value of the discard by binning cards that either
    belong in the graveyard (flashback, escape, reanimate targets) or
    are excess (flooded lands, redundant copies).

    self_discard=False means an opponent forced the discard
    (Thoughtseize, Grief, Inquisition, Duress). The caster wants to
    strip the most threatening card from the victim's hand. Bug E2:
    we route this through `ai.ev_evaluator.choose_card_to_strip`,
    which uses `creature_threat_value()` for creatures and the
    victim's declared gameplan keystones (critical_pieces /
    always_early / mulligan_keys) plus tag weights for non-creatures.
    That picker filters out lands (Thoughtseize text: "nonland card")
    and returns None for an all-lands hand — the engine loop stops in
    that case.
    """
    if not hand:
        raise ValueError("choose_discard called with empty hand")
    if len(hand) == 1:
        # Single-card hand: if the lone card is a land and this is an
        # opponent-forced (non-self) discard, honour the "nonland" clause
        # by returning None. Otherwise return the only card available.
        only = hand[0]
        if (not self_discard
                and getattr(only.template, 'is_land', False)):
            return None
        return only

    if not self_discard:
        # Bug E2 fix — opponent-forced discard (Thoughtseize / Duress /
        # Inquisition / Grief). Delegate to the AI threat-scoring helper,
        # passing the victim's gameplan so its declared keystones can be
        # consulted. No hardcoded card names here.
        from ai.ev_evaluator import choose_card_to_strip
        opp_gameplan = None
        player = game.players[player_idx]
        deck_name = getattr(player, 'deck_name', '') or ''
        if deck_name:
            try:
                from ai.gameplan import get_gameplan
                opp_gameplan = get_gameplan(deck_name)
            except Exception:
                opp_gameplan = None
        return choose_card_to_strip(hand, opp_gameplan)

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
