"""What a removal spell can remove: the permanents its target may be.

A removal spell's target is chosen among the permanents its requirement
admits (CR 601.2c), within the mana value it can reach -- the X its caster
can pay (CR 601.2b: X is chosen before targets), a printed or resolution-
checked bound ("if it has mana value 2 or less"), the colours converge can
spend -- that the spell may target (hexproof, protection) past a ward its
caster would pay.

One candidate set, read by the target picker (`EVPlayer._choose_targets`),
the spell's board projection (`ev_evaluator._project_spell`) and its threat
premium (`EVPlayer._score_spell`): a removal spell is valued by a target it
can take, never by a creature it cannot touch.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional


def removal_ceiling(game: Any, player_idx: int, spell: Any) -> Optional[int]:
    """The highest mana value the spell's target may have, or None for no
    bound: the X the caster can pay for an X-bound target (the same
    formula cast-time legality reads), a numeric bound printed on the
    target or checked on resolution (raised when a permanent left the
    battlefield this turn), and converge's reachable colours."""
    t = spell.template
    ceiling = None
    x_bound_mode = any(((m.get('removal') or {}).get('mv') == 'x')
                       for m in (getattr(t, 'modes', None) or []))
    removal = (getattr(t, 'targeted_removal_data', None)
               or getattr(t, 'removal_mv_condition', None) or {})
    if removal.get('mv') == 'x' or x_bound_mode:
        # A spell on the stack has its X (CR 601.2b, chosen as it was
        # cast); one in hand reaches the X its caster can pay now.
        paid = next((item.x_value for item in game.stack.items
                     if item.source is spell), None)
        if paid is not None:
            ceiling = paid
        else:
            from engine.cast_manager import CastManager
            ceiling = CastManager.affordable_x(game, player_idx, t)
    elif isinstance(removal.get('mv'), int):
        ceiling = removal['mv']
        raised = removal.get('mv_if_permanent_left')
        if raised is not None and \
                game.players[player_idx].permanents_left_battlefield_this_turn:
            ceiling = raised
    if getattr(t, 'has_converge', False):
        from engine.card_effects import converge_reachable_max_mv
        reach = converge_reachable_max_mv(game, player_idx)
        ceiling = reach if ceiling is None else min(ceiling, reach)
    return ceiling


def can_remove(game: Any, player_idx: int, spell: Any, perm: Any,
               ceiling: Optional[int]) -> bool:
    """Within the ceiling, a permanent the spell may target (hexproof,
    protection, CR 702.11d / 702.16b), whose ward its caster would get
    past (CR 702.21a), and that one of the spell's requirements admits
    (the engine's own cast check, CR 601.2c)."""
    from engine.cast_manager import CastManager
    from engine.target_solver import can_be_targeted
    from ai.ward_targeting import ward_rules_out_target
    return ((ceiling is None or (perm.template.cmc or 0) <= ceiling)
            and can_be_targeted(perm, spell, player_idx)
            and not ward_rules_out_target(game, player_idx, spell, perm)
            and CastManager.targets_admitted(game, player_idx, spell,
                                             [perm.instance_id]))


def removal_candidates(game: Any, player_idx: int, spell: Any,
                       pool: Iterable[Any]) -> List[Any]:
    """Of `pool` (opposing permanents), the ones the spell can remove."""
    ceiling = removal_ceiling(game, player_idx, spell)
    return [p for p in pool if can_remove(game, player_idx, spell, p, ceiling)]
