"""Where N damage goes: the opponent's face, or an opposing creature or
planeswalker the damage destroys (CR 115.4 "any target", CR 120.3); and
which opposing permanent a "destroy target <type>" instruction takes (CR
701.8a).

One owner of the choice for removal whose target the AI declares (CR
601.2c, 602.2b), and of what it is worth. Both sides of the comparison are
the opponent's position-value loss in the opponent's frame, so they are in
the same units and no tuned constant is needed -- the attack chooser's rule
(`ai/attack_targets.py`):

* N damage to the face is worth `face_damage_value`: the opponent's
  position value at their life minus at life - N (`ai/clock.position_value`);
* a permanent the damage or the destroy puts into the graveyard is worth
  `permanent_threat`: the opponent's position value with it minus without
  it (`ai/permanent_threat.py`).

Damage that reaches the opponent's life total goes to the face. A permanent
the instruction does not destroy is no candidate: damage marked on a
creature that survives wears off in the cleanup step (CR 514.2), no
primitive prices a planeswalker's lost loyalty short of its death, and
"destroy" does not destroy an indestructible permanent (CR 702.12b). Ties
go to the face. Only the opponent's permanents are candidates, so nothing
a cost sacrifices can be the target. Mechanic-driven; no card names, no
literals.
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
    """A declared removal target (of damage or of a destroy) and what it is
    worth."""
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


def destroy_destroys(card: "CardInstance") -> bool:
    """Would "destroy" put ``card`` into its owner's graveyard? Every
    permanent but an indestructible one (CR 701.8a, 702.12b)."""
    from engine.cards import Keyword
    return Keyword.INDESTRUCTIBLE not in card.keywords


def choose_destroy_recipient(game: "GameState", controller: int, source,
                             requirements, *,
                             mana_committed: int) -> Optional[DamageAim]:
    """Which opposing permanent a "destroy target <type>" instruction from
    ``source``, controlled by ``controller``, takes (CR 602.2b), or None
    when its ``requirements`` admit none worth declaring.

    Candidates are the opposing permanents the requirements admit (the
    engine's own admission, `target_solver.target_admitted`: the slot's
    types, qualifiers and controller; hexproof and protection) that the
    destroy destroys (`destroy_destroys`) and whose ward its controller
    would pay with the mana left after ``mana_committed``; the one whose
    loss costs the opponent most (`permanent_threat`) is chosen, as for
    damage that destroys."""
    from engine.target_solver import target_admitted
    from ai.permanent_threat import permanent_threat
    from ai.ward_targeting import ward_rules_out_target

    opp = game.players[1 - controller]
    best: Optional[DamageAim] = None
    for card in list(opp.battlefield):
        if not (destroy_destroys(card)
                and target_admitted(game, controller, list(requirements),
                                    card.instance_id, source=source)):
            continue
        if ward_rules_out_target(game, controller, source, card,
                                 mana_committed=mana_committed):
            continue
        value = permanent_threat(card, opp, game)
        if best is None or value > best.value:
            best = DamageAim(card.instance_id, card, value)
    return best


def _already_destroyed(card: "CardInstance") -> bool:
    """A creature whose marked damage is lethal is destroyed at the next
    state-based check (CR 704.5g); damage aimed at it is wasted."""
    toughness = card.toughness or 0
    return (getattr(card, 'effective_is_creature', False)
            and 0 < toughness <= (card.damage_marked or 0))


def copies_to_destroy(card: "CardInstance", amount: int, most: int,
                      source) -> Optional[int]:
    """The fewest copies of ``amount`` damage from ``source`` that together
    destroy ``card`` (`damage_destroys`, counting the damage already
    marked), at most ``most``; None when even ``most`` do not."""
    for k in range(1, most + 1):
        if damage_destroys(card, k * amount, source):
            return k
    return None


def plan_copy_damage(game: "GameState", controller: int, source,
                     amount: int, copies: int,
                     requirement: "TargetRequirement", *,
                     mana_committed: int = 0) -> list:
    """Where ``copies`` copies of a spell that deals ``amount`` damage to one
    target go (CR 707.10c: each copy's controller may choose its targets
    anew). One entry per copy: a target id, or None to keep the spell's
    own target.

    Copies lethal together go to the face. Otherwise the copies kill the
    set of opposing permanents worth most (`permanent_threat`, each
    needing `copies_to_destroy` copies: a 1/2 takes two 1-damage copies),
    and the rest go to the face, valued as the face value of what they deal
    together (`face_damage_value`). A small 0/1 knapsack over the copies,
    in the same currency as `choose_damage_recipient`; ties go to the
    face. The candidates are the ones `choose_damage_recipient` admits:
    what the slot admits and the source may target, with any ward its
    controller would pay."""
    from engine.constants import PLAYER_TARGET_OPPONENT
    from engine.target_solver import (can_be_targeted, slot_admits_permanent,
                                      slot_admits_player)
    from ai.permanent_threat import permanent_threat
    from ai.ward_targeting import ward_rules_out_target

    if copies <= 0:
        return []
    opp_idx = 1 - controller
    opp = game.players[opp_idx]
    face = slot_admits_player(requirement, opp_idx, controller)
    if face and amount * copies >= opp.life:
        return [PLAYER_TARGET_OPPONENT] * copies
    candidates = []
    for card in list(opp.battlefield):
        if not (slot_admits_permanent(requirement, card, controller)
                and can_be_targeted(card, source, controller)) \
                or _already_destroyed(card) \
                or ward_rules_out_target(game, controller, source, card,
                                         mana_committed=mana_committed):
            continue
        need = copies_to_destroy(card, amount, copies, source)
        if need is not None:
            candidates.append((card.instance_id, need,
                               permanent_threat(card, opp, game)))
    # best[w]: the most a set of kills spending exactly w copies is worth.
    best = {0: (0.0, ())}
    for tid, need, value in candidates:
        for spent, (worth, kills) in list(best.items()):
            total = spent + need
            if total <= copies and (total not in best
                                    or best[total][0] < worth + value):
                best[total] = (worth + value, kills + ((tid, need),))

    def plan_value(spent):
        left = copies - spent
        rest = (face_damage_value(game, opp_idx, left * amount)
                if face and left else 0.0)
        return best[spent][0] + rest
    spent = max(best, key=lambda k: (plan_value(k), -k))
    plan = [tid for tid, need in best[spent][1] for _ in range(need)]
    plan += [PLAYER_TARGET_OPPONENT if face else None] * (copies - len(plan))
    return plan


def copy_targets(game: "GameState", controller: int, item,
                 remaining: int) -> Optional[list]:
    """The AI's targets for a copy of the spell ``item`` (CR 707.10c), or
    None to keep the original's. A spell whose one instruction deals N
    damage to one target (its typed DAMAGE, one target requirement) is
    aimed by `plan_copy_damage` over the copies still to be made, this one
    first; any other spell keeps its targets."""
    from engine.cast_manager import CastManager
    from engine.effect_conditions import amount_value
    from engine.effect_spec import Verb, iter_specs
    card = item.source
    host = card.template.effects.spell(0)
    if host is None:
        return None
    specs = list(iter_specs(host.specs))
    reqs = CastManager.spell_requirements(card)
    if len(specs) != 1 or specs[0].verb is not Verb.DAMAGE \
            or len(reqs) != 1 or len(item.targets) != 1:
        return None
    amount = amount_value(game, controller, specs[0].amount, item.x_value)
    plan = plan_copy_damage(game, controller, card, amount, remaining,
                            reqs[0])
    if not plan or plan[0] is None:
        return None
    return [plan[0]]
