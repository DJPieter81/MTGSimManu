"""Choose what each attacker attacks: the defending player or one of their
planeswalkers (CR 508.1b).

The engine enforces the rule (`engine/combat_manager.declare_attackers`
takes an ``attack_targets`` map); this module makes the choice. Both sides of
the comparison are position-value deltas in the opponent's frame, so they are
in the same units and no tuned constant is needed:

* killing a planeswalker is worth ``permanent_threat(pw)`` — the opponent's
  position value with it minus without it (``ai/permanent_threat.py``);
* the same damage to face is worth the opponent's position value at their
  current life minus at ``life − damage`` (``ai/damage_targets.
  face_damage_value``, shared with the aim of declared damage).

A group is sent at a planeswalker only when that power kills it (loyalty ≤
power) and killing it is worth more than the face damage it gives up. A
lethal attack always goes face. Mechanic-driven; no card names.
"""
from __future__ import annotations

from itertools import combinations
from typing import TYPE_CHECKING, Dict, List

from ai.damage_targets import face_damage_value

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState


def _smallest_killing_group(pool: List["CardInstance"], loyalty: int) -> List["CardInstance"]:
    """The fewest attackers, then least total power, whose power reaches
    ``loyalty`` — the least face damage given up for the kill."""
    for size in range(1, len(pool) + 1):
        groups = [g for g in combinations(pool, size)
                  if sum(max(c.power or 0, 0) for c in g) >= loyalty]
        if groups:
            return list(min(groups, key=lambda g: sum(c.power or 0 for c in g)))
    return []


def choose_attack_targets(game: "GameState", my_idx: int,
                          attackers: List["CardInstance"]) -> Dict[int, "CardInstance"]:
    """Map attacker instance_id → the opposing planeswalker it attacks.
    Attackers absent from the map attack the defending player."""
    from ai.permanent_threat import permanent_threat
    opp_idx = 1 - my_idx
    opp = game.players[opp_idx]
    walkers = [c for c in opp.battlefield
               if getattr(c, 'effective_is_planeswalker', False)
               and (c.loyalty_counters or 0) > 0]
    if not walkers or not attackers:
        return {}
    total_power = sum(max(a.power or 0, 0) for a in attackers)
    if total_power >= opp.life:
        return {}  # a lethal attack goes face

    targets: Dict[int, "CardInstance"] = {}
    pool = list(attackers)
    ranked = sorted(walkers, key=lambda pw: permanent_threat(pw, opp, game),
                    reverse=True)
    for pw in ranked:
        group = _smallest_killing_group(pool, pw.loyalty_counters)
        if not group:
            continue
        given_up = sum(max(c.power or 0, 0) for c in group)
        if permanent_threat(pw, opp, game) > face_damage_value(game, opp_idx,
                                                               given_up):
            for c in group:
                targets[c.instance_id] = pw
                pool.remove(c)
    return targets
