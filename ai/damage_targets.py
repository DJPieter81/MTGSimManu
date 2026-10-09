"""Where N damage goes: the opponent's face, or an opposing creature or
planeswalker the damage destroys (CR 115.4 "any target", CR 120.3).

One owner of the choice for damage whose target the AI declares (CR 601.2c,
602.2b), and of what that damage is worth. Both sides of the comparison are
the opponent's position-value loss in the opponent's frame, so they are in
the same units and no tuned constant is needed -- the attack chooser's rule
(`ai/attack_targets.py`):

* N damage to the face is worth `face_damage_value`: the opponent's
  position value at their life minus at life - N (`ai/clock.position_value`);
* N damage that destroys a creature or planeswalker is worth
  `permanent_threat`: the opponent's position value with it minus without
  it (`ai/permanent_threat.py`).

Damage that reaches the opponent's life total goes to the face. A permanent
the damage does not destroy is no candidate: damage marked on a creature
that survives wears off in the cleanup step (CR 514.2), and no primitive
prices a planeswalker's lost loyalty short of its death. Ties go to the
face. Only the opponent's permanents are candidates, so nothing a cost
sacrifices can be the target. Mechanic-driven; no card names, no literals.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Optional

if TYPE_CHECKING:  # pragma: no cover
    from engine.cards import CardInstance
    from engine.game_state import GameState
    from engine.target_solver import TargetRequirement


def face_damage_value(game: "GameState", opp_idx: int, damage: int) -> float:
    """The opponent's position-value loss from taking ``damage`` to the
    face."""
    from ai.clock import position_value
    from ai.ev_evaluator import snapshot_from_game
    snap = snapshot_from_game(game, opp_idx)
    hit = snap.model_copy(update={'my_life': snap.my_life - damage})
    return position_value(snap) - position_value(hit)


def damage_destroys(card: "CardInstance", amount: int, source) -> bool:
    """Would ``amount`` damage from ``source`` put ``card`` into its
    owner's graveyard? A planeswalker loses that many loyalty counters (CR
    120.3c) and with none left is put there (CR 704.5i). A creature is
    destroyed by lethal damage -- its toughness less the damage already
    marked (CR 120.6, 704.5g) -- or by any damage from a deathtouch source
    (CR 702.2b, 704.5h), unless it is indestructible (CR 702.12b)."""
    from engine.cards import Keyword
    if amount <= 0:                 # CR 120.8: no damage is dealt
        return False
    if getattr(card, 'effective_is_planeswalker', False) \
            and (card.loyalty_counters or 0) <= amount:
        return True
    if not getattr(card, 'effective_is_creature', False) \
            or Keyword.INDESTRUCTIBLE in card.keywords:
        return False
    if getattr(source, 'has_deathtouch', False):
        return True
    toughness = card.toughness or 0
    return 0 < toughness <= amount + (card.damage_marked or 0)


class DamageAim(NamedTuple):
    """A declared damage target and what it is worth."""
    target_id: int                          # instance id, or the face marker
    permanent: Optional["CardInstance"]     # None when it is the face
    value: float                            # the opponent's position lost


def choose_damage_recipient(game: "GameState", controller: int, source,
                            amount: int, requirement: "TargetRequirement", *,
                            mana_committed: int) -> Optional[DamageAim]:
    """Where ``amount`` damage from ``source``, controlled by
    ``controller``, goes for one target slot (``requirement``), or None when
    the slot admits nothing worth declaring.

    Candidates are the opposing player (when the slot admits a player) and
    each opposing permanent the slot admits that the damage destroys, that
    ``source`` may target (`target_solver.can_be_targeted`: hexproof,
    protection) and whose ward its controller would pay with the mana left
    after ``mana_committed`` (`ward_targeting.ward_rules_out_target`)."""
    from engine.constants import PLAYER_TARGET_OPPONENT
    from engine.target_solver import (can_be_targeted, slot_admits_permanent,
                                      slot_admits_player)
    from ai.permanent_threat import permanent_threat
    from ai.ward_targeting import ward_rules_out_target

    opp_idx = 1 - controller
    opp = game.players[opp_idx]
    best: Optional[DamageAim] = None
    if slot_admits_player(requirement, opp_idx, controller):
        best = DamageAim(PLAYER_TARGET_OPPONENT, None,
                         face_damage_value(game, opp_idx, amount))
        if amount >= opp.life:
            return best             # lethal damage goes to the face
    for card in list(opp.battlefield):
        if not (slot_admits_permanent(requirement, card, controller)
                and damage_destroys(card, amount, source)
                and can_be_targeted(card, source, controller)):
            continue
        if ward_rules_out_target(game, controller, source, card,
                                 mana_committed=mana_committed):
            continue
        value = permanent_threat(card, opp, game)
        if best is None or value > best.value:
            best = DamageAim(card.instance_id, card, value)
    return best
