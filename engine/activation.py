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
        # Sacrifice-self is inherently self-limiting (the source leaves), a
        # life cost depletes the life total, a sacrifice-another cost
        # depletes the board and a discard cost depletes the hand — each
        # terminates the loop.
        if (ability.cost.mana.cmc == 0 and not ability.cost.tap_self
                and not ability.cost.sacrifice_self
                and ability.cost.life == 0
                and ability.cost.sacrifice_type is None
                and ability.cost.discard_cards == 0):
            return False

        # 9b. An effect kind the resolver cannot execute must be refused
        # BEFORE any cost is charged — paying a cost for a recorded-unhandled
        # no-op is strictly worse than refusing. ANIMATE_SELF_UEOT is owned by
        # the land-animation path and must not be double-executed here.
        if ability.effect_kind not in (
                ActivationEffectKind.DAMAGE_ANY_TARGET,
                ActivationEffectKind.DRAW_N,
                ActivationEffectKind.PUMP_SELF_UEOT,
                ActivationEffectKind.GRANT_HASTE_TARGET,
                ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD,
                ActivationEffectKind.TUTOR_TO_HAND):
            return False

        # 9b-x. X-pip discipline. An {X} in the cost is chargeable exactly
        # when the classified effect BINDS X (a tutor's "mana value X or
        # less"): an unbound {X} cannot be charged honestly (the engine
        # does not know what X buys), and an X-bound search with no {X}
        # pip to bind is schema incoherence. Both refuse BEFORE any cost
        # is charged. Note a tutor whose search finds nothing is still a
        # LEGAL activation (CR 701.19b — searching may fail); refusing to
        # pay for a whiff is the AI's judgment, not a rule.
        is_tutor = ability.effect_kind in (
            ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD,
            ActivationEffectKind.TUTOR_TO_HAND)
        if is_tutor and ability.tutor_data is None:
            return False
        binds_x = bool(is_tutor
                       and ability.tutor_data.get('mv_bound_is_x'))
        if ability.cost.x_count > 0 and not binds_x:
            return False
        if binds_x and ability.cost.x_count == 0:
            return False

        player = game.players[player_idx]

        # 9c. CR 118.4 — life can be paid only while the life total covers
        # it. Paying down to exactly 0 is rules-legal (the SBA loss is a
        # separate event); suicide-avoidance is the AI's judgment, not a
        # legality question.
        if ability.cost.life > 0 and player.life < ability.cost.life:
            return False

        # 9d. CR 601.2h — a sacrifice cost needs a legal victim under the
        # controller's control RIGHT NOW: a permanent of the required type,
        # excluding the source when the cost says "another". WHICH victim is
        # sacrificed is strategy (the AI callback's choice at payment time);
        # WHETHER one exists is the legality question owned here.
        if ability.cost.sacrifice_type is not None:
            if not ActivationManager.legal_sacrifice_victims(
                    game, player_idx, perm, ability.cost):
                return False

        # 9e. CR 601.2h — a discard cost needs that many cards in hand.
        if (ability.cost.discard_cards > 0
                and len(player.hand) < ability.cost.discard_cards):
            return False

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
    def legal_sacrifice_victims(game: "GameState", player_idx: int,
                                perm: "CardInstance",
                                cost) -> List["CardInstance"]:
        """Battlefield permanents that can pay a sacrifice cost (CR 601.2h).

        Pure enumeration, zero preference — the CHOICE among these is the AI
        callback's. `sacrifice_another` excludes the source; a plain
        "sacrifice a creature" counts the source itself when it matches.
        The creature test uses effective types so animated permanents
        qualify, matching `PlayerState.creatures`.
        """
        type_word = cost.sacrifice_type
        if type_word is None:
            return []
        out: List["CardInstance"] = []
        for cand in game.players[player_idx].battlefield:
            if cost.sacrifice_another and cand is perm:
                continue
            if type_word == 'permanent':
                out.append(cand)
            elif type_word == 'creature':
                if (cand.effective_is_creature
                        or getattr(cand, 'is_animated', False)):
                    out.append(cand)
            elif CardType(type_word) in cand.template.card_types:
                out.append(cand)
        return out

    @staticmethod
    def activate(game: "GameState", player_idx: int, perm: "CardInstance",
                 ability: "ActivatedAbility",
                 targets: Optional[List[int]] = None) -> bool:
        """Pay the cost and put the ability on the stack (CR 602.2)."""
        from .activated_effects import resolve_activated_ability

        game._activation_depth = getattr(game, '_activation_depth', 0) + 1
        try:
            # Victim RESOLUTION before any payment: choosing is not a
            # mutation, and it is the only tranche-3 step that can refuse —
            # doing it first keeps the no-half-paid-cost invariant when
            # activate() is reached without a prior can_activate.
            victim = None
            if ability.cost.sacrifice_type is not None:
                legal = ActivationManager.legal_sacrifice_victims(
                    game, player_idx, perm, ability.cost)
                if legal:
                    victim = game.callbacks.choose_sacrifice(
                        game, player_idx, legal)
                if victim is None:
                    return False
            if (ability.cost.discard_cards > 0
                    and len(game.players[player_idx].hand)
                    < ability.cost.discard_cards):
                return False

            # X CHOICE (CR 601.2b) — made before payment, by the shared
            # engine-side picker the AI valuation layer also consults
            # (`pick_activated_tutor_x`), so projection and payment cannot
            # disagree. can_activate's 9b-x rule guarantees x_count > 0
            # only on X-binding tutors, so the chosen X both prices the
            # cost and bounds the search (bound into the resolver partial
            # below).
            chosen_x = 0
            pay_mana = ability.cost.mana
            if ability.cost.x_count > 0:
                from dataclasses import replace as _dc_replace
                from .activated_effects import (activated_tutor_x_budget,
                                                pick_activated_tutor_x)
                x_budget = activated_tutor_x_budget(game, player_idx, ability)
                chosen_x, _target, _top = pick_activated_tutor_x(
                    game, player_idx, x_budget, ability)
                if chosen_x > 0:
                    pay_mana = _dc_replace(
                        ability.cost.mana,
                        generic=(ability.cost.mana.generic
                                 + chosen_x * ability.cost.x_count))

            # Mana FIRST, tap LAST: tapping cannot fail, so a refused payment
            # leaves nothing half-paid. `card_name=None` because CR 601.2f
            # cost reductions apply to SPELLS — passing the permanent's name
            # would let the solver apply a spurious discount.
            if pay_mana.cmc > 0:
                paid = game.tap_lands_for_mana(
                    player_idx, pay_mana, None,
                    exclude_instance_id=perm.instance_id)
                if not paid:
                    return False
            if ability.cost.life > 0:
                game.players[player_idx].life -= ability.cost.life
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: pays "
                    f"{ability.cost.life} life ({perm.name} activation)")
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
            # Resource-consuming cost items are paid LAST, like
            # sacrifice_self below: every step that could refuse has already
            # run, so nothing follows the consumption that could leave the
            # cost half-paid.
            if ability.cost.discard_cards > 0:
                # Through the same funnel forced discards use
                # (callbacks.choose_discard + zone_mgr), so discard-linked
                # triggers keep firing. self_discard=True: activating the
                # ability was the player's own choice (CR 601.2h).
                game._force_discard(player_idx, ability.cost.discard_cards,
                                    self_discard=True)
            if victim is not None:
                # CR 602.2b: the victim is sacrificed as part of the COST —
                # it leaves at activation time, through the zone funnel.
                game.zone_mgr.move_card_to_graveyard(
                    game, victim, cause=f"activation cost ({perm.name})")

            if ability.cost.sacrifice_self:
                # CR 602.2b: the sacrifice is part of the COST — the source
                # leaves the battlefield at activation time; the ability on
                # the stack resolves independently of it. Routed through the
                # zone funnel. Placed LAST so nothing that could refuse runs
                # after the permanent is gone.
                game.zone_mgr.move_card_to_graveyard(
                    game, perm, cause=f"activation cost ({perm.name})")

            game.stack.items.append(StackItem(
                item_type=StackItemType.ACTIVATED_ABILITY,
                source=perm,
                controller=player_idx,
                targets=list(targets or []),
                effect=functools.partial(resolve_activated_ability,
                                         ability=ability,
                                         x_value=chosen_x),
                ability=None,
                target_zones=target_zones,
                x_value=chosen_x,
            ))
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: activate "
                f"{perm.name} — {ability.effect_text}")
            return True
        finally:
            game._activation_depth -= 1
