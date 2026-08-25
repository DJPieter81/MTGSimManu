"""Activated-ability legality and execution (CR 602). Rules only, zero scoring.

The engine decides what is LEGAL; the AI decides what is WORTH doing. Nothing
in this module scores, ranks, or prefers — that boundary is the project's
standing rule and it is what keeps `can_activate` reviewable as a rules list.

Safety, stated once because it is the whole risk of this subsystem: an ability
activated while a cost is being paid could recurse into payment and spin below
every existing action counter (`MAX_ACTIONS_COMBO` guards the main-phase loop,
not the payment path). Two independent guards close that:

  * `game._paying_mana` — raised for the whole of `tap_lands_for_mana`. CR
    605.3 permits only MANA abilities to be activated while paying, and this
    engine produces mana through the payment path itself, so every other
    activation is refused outright mid-payment. The recursive edge does not
    exist rather than being merely bounded.
  * `game._activation_depth` vs `ACTIVATION_MAX_DEPTH` — incremented on BOTH
    the push side (here) and the resolution side (`activated_effects`), because
    a push-side guard alone misses re-entry that begins during resolution.
"""
from __future__ import annotations

import functools
from typing import TYPE_CHECKING, List, Optional

from .cards import ActivationEffectKind, CardType
from .constants import ACTIVATION_MAX_DEPTH
from .stack import StackItem, StackItemType

if TYPE_CHECKING:  # pragma: no cover
    from .cards import ActivatedAbility, CardInstance
    from .game_state import GameState


class ActivationManager:
    """Stateless. Every method takes the GameState as its first argument,
    matching SBAManager / CombatManager / ManaPayment."""

    @staticmethod
    def can_activate(game: "GameState", player_idx: int,
                     perm: "CardInstance",
                     ability: "ActivatedAbility") -> bool:
        """Is this activation legal right now? Ordered cheapest-first."""
        import time as _time

        # 1. CR 605.3 — only mana abilities during cost payment. See module doc.
        if getattr(game, '_paying_mana', 0) > 0:
            return False

        # 2. Re-entry bound.
        if getattr(game, '_activation_depth', 0) >= ACTIVATION_MAX_DEPTH:
            return False

        # 3. Wall-clock deadline (same valve every engine loop head carries).
        deadline = getattr(game, '_game_deadline', None)
        if deadline is not None and _time.monotonic() > deadline:
            return False

        # 4. Mana abilities belong to ManaPayment, not to the play enumerator.
        if ability.is_mana_ability:
            return False

        # 5/6. Cost items this tranche cannot charge. A permanent with ANY
        # half-payable ability is refused entirely: a partially-usable engine
        # is worse than an unusable one, because the AI would pay for the
        # cheap half of a combo it can never finish.
        if ability.cost.unpayable:
            return False
        if any(sib.cost.unpayable
               for sib in (perm.template.activated_abilities or [])):
            return False

        # 7. CR 602.5b riders the schema cannot express.
        if ability.restrictions:
            return False

        # 8. Zone.
        if not ability.from_battlefield or perm.zone != "battlefield":
            return False

        # 9. A free, repeatable ability has no resource that depletes, so
        # nothing terminates the loop. Cost exhaustion is the real bound.
        if ability.cost.mana.cmc == 0 and not ability.cost.tap_self:
            return False

        player = game.players[player_idx]

        # 10. Capacity precondition. THIS is what makes payment atomic: both
        # mutating branches inside `tap_lands_for_mana` are gated on a
        # shortfall, so if capacity already covers the cost no mutation can
        # precede a failure return.
        capacity = (player.untapped_mana_capacity()
                    + player.mana_pool.total()
                    + player._tron_mana_bonus())
        if capacity < ability.cost.mana.cmc:
            return False

        # 11. Once-each-turn ledger (keyed on the stable ability index).
        if ability.once_each_turn and (
                perm.activations_this_turn.get(ability.index, 0) >= 1):
            return False

        # 12. Sorcery-speed rider.
        if ability.sorcery_speed_only:
            from .game_state import Phase
            own_main = (game.active_player == player_idx
                        and game.current_phase in (Phase.MAIN1, Phase.MAIN2))
            if not (own_main and game.stack.is_empty):
                return False

        # 13. CR 302.6 — a tap cost needs an untapped, non-sick permanent.
        if ability.cost.tap_self:
            if perm.tapped:
                return False
            if (CardType.CREATURE in perm.template.card_types
                    and perm.has_summoning_sickness):
                return False

        # 14. A pump on a non-creature would never be cleaned up: the cleanup
        # step iterates `player.creatures`, so the modifier would accumulate
        # across turns.
        if ability.effect_kind is ActivationEffectKind.PUMP_SELF_UEOT:
            if not (perm.effective_is_creature
                    or getattr(perm, 'is_animated', False)):
                return False

        # 15. CR 601.2c — a required target must have a legal choice.
        if ability.targets_required and ability.target_requirements:
            from .target_solver import has_legal_target_for_spell
            if not has_legal_target_for_spell(
                    game, player_idx, ability.target_requirements):
                return False

        return True

    @staticmethod
    def activate(game: "GameState", player_idx: int, perm: "CardInstance",
                 ability: "ActivatedAbility",
                 targets: Optional[List[int]] = None) -> bool:
        """Pay the cost and put the ability on the stack (CR 602.2)."""
        from .activated_effects import resolve_activated_ability

        game._activation_depth = getattr(game, '_activation_depth', 0) + 1
        try:
            # Mana FIRST, tap LAST: tapping cannot fail, so a refused payment
            # leaves nothing half-paid. `card_name=None` because CR 601.2f
            # cost reductions apply to SPELLS — passing the permanent's name
            # would let the solver apply a spurious discount.
            if ability.cost.mana.cmc > 0:
                paid = game.tap_lands_for_mana(
                    player_idx, ability.cost.mana, None,
                    exclude_instance_id=perm.instance_id)
                if not paid:
                    return False
            if ability.cost.tap_self:
                perm.tap()

            perm.activations_this_turn[ability.index] = (
                perm.activations_this_turn.get(ability.index, 0) + 1)
            game._activations_this_game = (
                getattr(game, '_activations_this_game', 0) + 1)

            # `target_zones` must be populated exactly as the cast path does,
            # or the CR 608.2b fizzle check reads a missing snapshot and is
            # silently inert.
            target_zones = {}
            for tid in (targets or []):
                found = game.find_card_by_id(tid) if hasattr(
                    game, 'find_card_by_id') else None
                if found is not None:
                    target_zones[tid] = found.zone

            # `ability=None` is MANDATORY: StackItem.ability is typed as the
            # legacy Ability dataclass and resolution tests `item.ability.effect`
            # BEFORE `elif item.effect`, so assigning the new dataclass there
            # raises before the partial is ever reached. The ActivatedAbility
            # travels only inside the partial.
            game.stack.items.append(StackItem(
                item_type=StackItemType.ACTIVATED_ABILITY,
                source=perm,
                controller=player_idx,
                targets=list(targets or []),
                effect=functools.partial(resolve_activated_ability,
                                         ability=ability),
                ability=None,
                target_zones=target_zones,
            ))
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: activate "
                f"{perm.name} — {ability.effect_text}")
            return True
        finally:
            game._activation_depth -= 1
