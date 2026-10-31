"""Where a beneficial characteristic change goes (AI).

"Target creature gets +N/+M until end of turn" or "... gains <keywords>"
helps whatever it lands on, and "target creature" admits the opponent's
creatures too, so the target's legality never protects this choice. One
owner, read by the cast-time target pick (`ai.ev_player`) and by a
trigger's target pick as it is put on the stack (`ai.resolution_choices.
pick_trigger_targets`, CR 603.3d): the player's own creature -- an
attacking one first, else the one worth most on the battlefield.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence


def beneficial_change(spec: Any) -> bool:
    """Does the spec's characteristic change only help its recipient: a
    P/T change with no negative part and a positive one, or granted
    keywords? A type change, or a P/T change with a negative part, is
    not one (the engine's default aim stands)."""
    from engine.effect_executors import _literal_n
    from engine.effect_model import Modification, ModKind
    m = getattr(spec, "payload", None)
    if not isinstance(m, Modification):
        return False
    if m.kind is ModKind.ADD_KEYWORDS:
        return True
    if m.kind is not ModKind.MODIFY_PT:
        return False
    vals = [_literal_n(v) for k, v in dict(m.data).items()
            if k in ("power", "toughness")]
    return (bool(vals) and all(v is not None and v >= 0 for v in vals)
            and any(v > 0 for v in vals))


def own_pump_target(game: Any, player_idx: int, candidates: Sequence[Any],
                    snap: Any = None) -> Optional[Any]:
    """The creature among `candidates` a beneficial change goes on: the
    player's own, an attacking one first, else the one worth most on the
    battlefield (`ai.ev_evaluator.creature_value` on `snap`, the live
    snapshot by default). None when no candidate is the player's."""
    from ai.ev_evaluator import creature_value, snapshot_from_game
    mine = [c for c in candidates
            if getattr(c, "controller", None) == player_idx]
    if not mine:
        return None
    if snap is None:
        snap = snapshot_from_game(game, player_idx)
    attacking = [c for c in mine if getattr(c, "attacking", False)]
    return max(attacking or mine, key=lambda c: creature_value(c, snap))
