"""Marginal-contribution threat scoring.

    threat(P) = V_O(B) - V_O(B \\ {P})

where V_O is `ai.clock.position_value` evaluated from the owning
player's perspective.  The "threat" of a permanent is the drop in
its controller's position value that occurs when it is removed —
exactly the quantity that a targeted removal or burn spell is
trying to take off the board.

Scaling mechanics (equipment `for each artifact`, creature
`+N/+N for each ...`, domain, delirium, graveyard scalers) fall out
of this formula automatically.  They are all already reflected in
`CardInstance.power` / `.toughness`, which recompute dynamically
from the live battlefield.  Briefly removing the card from the
owner's battlefield (and restoring it via `try`/`finally`) is
enough to re-trigger every dependent computation — no per-pattern
bolt-on is required.

No anchor constants.  No archetype detection.  No premiums.  The
marginal formula *is* the definition of threat.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState, PlayerState


def permanent_threat(card: "CardInstance", owner: "PlayerState",
                     game: "GameState") -> float:
    """Marginal contribution of `card` to `owner`'s position value.

    Returns ``V_owner(battlefield) - V_owner(battlefield \\ {card})``.
    A higher value means removing `card` is a bigger swing against
    `owner`, so the caller (a removal / burn targeter) should
    prefer it.

    `owner` is the player whose board `card` is on — i.e. for an
    opponent's threat we pass `game.players[1 - my_idx]`.  The
    snapshot is built from that player's perspective, so `my_*`
    fields in the snapshot refer to `owner`'s side.

    Returns 0.0 when `card` is not currently on `owner`'s
    battlefield; callers should filter to on-battlefield targets.
    """
    from ai.ev_evaluator import snapshot_from_game
    from ai.clock import position_value

    bf = owner.battlefield
    idx = -1
    for i, c in enumerate(bf):
        if c is card:
            idx = i
            break
    if idx < 0:
        return 0.0

    owner_idx = owner.player_idx

    full_snap = snapshot_from_game(game, owner_idx)
    v_full = position_value(full_snap)

    removed = bf.pop(idx)
    try:
        partial_snap = snapshot_from_game(game, owner_idx)
        v_partial = position_value(partial_snap)
    finally:
        bf.insert(idx, removed)

    return v_full - v_partial
