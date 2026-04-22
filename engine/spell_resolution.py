"""
Resolution manager — extracted from engine/game_state.py (Commit 5a).

Owns stack resolution and ETB/spell-effect execution:
- resolve_stack: pop the top StackItem, branch by type (spell vs
  activated/triggered ability), dispatch to spell effects or the
  ability effect closure. Handles storm / cascade / flashback /
  rebound / evoke-sac / escape-sac post-resolution transitions.
- _handle_permanent_etb: pre-ETB modal choice + generic ETB
  registry fan-out for permanents entering the battlefield.
- _resolve_living_end: mass-reanimate (exile battlefield creatures,
  return all creature cards from graveyards to battlefield under
  owner's control).
- _execute_spell_effects: dispatch instants/sorceries to the
  EFFECT_REGISTRY or to generic oracle-text-driven fallbacks.
- _blink_permanent: exile-then-return for Ephemerate / Whirlwind-of-
  Thought-style effects, preserving counters + re-triggering ETB.

Methods are static and take game: GameState as the first argument,
matching the SBAManager / CombatManager / CastManager pattern. The
old SpellResolutionMixin stub in this file has been deleted.
"""
from __future__ import annotations

import re
import random
from typing import TYPE_CHECKING, List

from .cards import CardType, Keyword, Supertype, Ability, AbilityType
from .card_effects import EFFECT_REGISTRY, EffectTiming
from .stack import StackItem, StackItemType

if TYPE_CHECKING:
    from .cards import CardInstance
    from .game_state import GameState


class ResolutionManager:
    """Stack resolution + permanent ETB + spell-effect dispatch."""

    @staticmethod
    def resolve_stack(game: "GameState"):
        """Resolve the top item on the stack."""
        if game.stack.is_empty:
            return

        item = game.stack.pop()
        card = item.source
        template = card.template

        # Only log "Resolve" for spells — not for triggered/activated abilities
        if item.item_type == StackItemType.SPELL:
            game.log.append(f"T{game.display_turn}: Resolve {card.name}")

        if item.item_type == StackItemType.SPELL:
            if CardType.INSTANT in template.card_types or CardType.SORCERY in template.card_types:
                game._execute_spell_effects(item)
                # Storm: copy the spell for each prior spell this turn
                if Keyword.STORM in template.keywords:
                    game._handle_storm(item)
                # Cascade: exile from top until lower CMC, cast free
                if Keyword.CASCADE in template.keywords:
                    game._handle_cascade(item)
                # Flashback: exile instead of going to graveyard (MTG CR 702.33a)
                if getattr(card, '_cast_with_flashback', False):
                    card.zone = "exile"
                    game.players[card.owner].exile.append(card)
                    card.has_flashback = False  # no longer has flashback
                elif hasattr(card, '_rebound_controller'):
                    # Rebound: exile instead of graveyard, cast for free next upkeep
                    card.zone = "exile"
                    game.players[card.owner].exile.append(card)
                    if not hasattr(game, '_rebound_cards'):
                        game._rebound_cards = []
                    game._rebound_cards.append(card)
                else:
                    card.zone = "graveyard"
                    game.players[card.owner].graveyard.append(card)
            else:
                # Permanent enters battlefield
                card.controller = item.controller
                card.enter_battlefield()
                game.players[item.controller].battlefield.append(card)
                # Place counters for X-cost permanents — only if no dedicated
                # ETB handler exists (Engineered Explosives uses sunburst via its
                # own handler, so don't double-set charge counters here)
                if item.x_value > 0 and template.x_cost_data:
                    has_dedicated_etb = template.name in EFFECT_REGISTRY._handlers
                    x_info = template.x_cost_data
                    effect = x_info.get("effect", "")
                    if effect == "charge_counters" and not has_dedicated_etb:
                        card.other_counters["charge"] = item.x_value
                        game.log.append(
                            f"T{game.display_turn} P{item.controller+1}: "
                            f"{card.name} enters with {item.x_value} charge counter(s)")
                    elif effect == "plus1_counters":
                        card.plus_counters += item.x_value
                        game.log.append(
                            f"T{game.display_turn} P{item.controller+1}: "
                            f"{card.name} enters with {item.x_value} +1/+1 counter(s)")
                game._handle_permanent_etb(card, item.controller, item=item)
                # Cascade on permanents too
                if Keyword.CASCADE in template.keywords:
                    game._handle_cascade(item)
                # Evoke: sacrifice after ETB triggers
                if getattr(card, '_evoked', False):
                    if card in game.players[item.controller].battlefield:
                        game.players[item.controller].battlefield.remove(card)
                        card.zone = "graveyard"
                        game.players[card.owner].graveyard.append(card)
                        game.log.append(f"T{game.display_turn} P{item.controller+1}: "
                                       f"{card.name} sacrificed (evoke)")
                # Phlage sacrifice-unless-escaped: if cast normally (not escaped),
                # sacrifice after ETB trigger resolves
                if (template.escape_cost is not None
                        and not getattr(card, '_escaped', False)):
                    if card in game.players[item.controller].battlefield:
                        game.players[item.controller].battlefield.remove(card)
                        card.zone = "graveyard"
                        game.players[card.owner].graveyard.append(card)
                        game.log.append(f"T{game.display_turn} P{item.controller+1}: "
                                       f"{card.name} sacrificed (not escaped)")

        elif item.item_type in (StackItemType.ACTIVATED_ABILITY,
                                 StackItemType.TRIGGERED_ABILITY):
            if item.ability and item.ability.effect:
                item.ability.effect(game, item.source, item.controller, item.targets)
            elif item.effect:
                item.effect(game, item.source, item.controller, item.targets)


    @staticmethod
    def _handle_permanent_etb(game: "GameState", card: CardInstance, controller: int,
                               item: "StackItem" = None):
        """Handle all enter-the-battlefield effects for a permanent.

        `item` — the resolving StackItem whose `targets` (list of
        instance_ids declared at cast time) must be threaded through to
        card-specific ETB handlers. Passing None (reanimation, blink,
        Living End) means no declared target; handlers fall back to
        oracle-driven pickers.
        """
        template = card.template

        # Planeswalker: set loyalty counters from template (oracle-derived)
        if CardType.PLANESWALKER in template.card_types:
            card.loyalty_counters = template.loyalty or 0

        # Energy production on ETB (from oracle-derived template property)
        if template.energy_production > 0:
            game.players[controller].add_energy(template.energy_production)
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                            f"{template.name} produces {template.energy_production} energy "
                            f"(total: {game.players[controller].energy_counters})")

        # Torpor Orb: suppress creature ETB abilities
        torpor_active = any(
            "torpor_orb_active" in c.instance_tags
            for p in game.players for c in p.battlefield
        )
        is_creature = CardType.CREATURE in template.card_types

        # Doorkeeper Thrull static: "Artifacts and creatures entering the
        # battlefield don't cause abilities to trigger." Generic oracle
        # check (no card name) — triggers when any permanent on the board
        # has that static clause.
        is_artifact = CardType.ARTIFACT in template.card_types
        doorkeeper_active = False
        if is_creature or is_artifact:
            for p in game.players:
                for perm in p.battlefield:
                    if perm.instance_id == card.instance_id:
                        continue
                    perm_oracle = (perm.template.oracle_text or '').lower()
                    if ("artifacts and creatures entering "
                            "don't cause abilities to trigger") in perm_oracle \
                            or "creatures entering the battlefield don't cause abilities to trigger" in perm_oracle:
                        doorkeeper_active = True
                        break
                if doorkeeper_active:
                    break

        if torpor_active and is_creature:
            game.log.append(f"T{game.display_turn}: {template.name} ETB suppressed by Torpor Orb")
        elif doorkeeper_active:
            game.log.append(f"T{game.display_turn}: {template.name} ETB suppressed by Doorkeeper Thrull")
        else:
            # Dispatch to card effect registry for card-specific ETB logic
            has_specific_handler = template.name in EFFECT_REGISTRY._handlers
            EFFECT_REGISTRY.execute(
                template.name, EffectTiming.ETB, game, card, controller,
                targets=(item.targets if item else None),
                item=item,
            )

            # Generic oracle-text-based ETB resolution for cards WITHOUT specific handlers
            if not has_specific_handler:
                from .oracle_resolver import resolve_etb_from_oracle
                resolve_etb_from_oracle(game, card, controller)

            # Generic ETB triggers
            game.trigger_etb(card, controller)

    # ─── STORM ───────────────────────────────────────────────────


    @staticmethod
    def _resolve_living_end(game: "GameState", controller: int):
        """Living End: exile all creatures from battlefield, return all from graveyard."""
        game.log.append(f"T{game.display_turn}: Living End resolves!")

        # For each player: exile battlefield creatures, return graveyard creatures
        for p_idx in range(2):
            player = game.players[p_idx]

            # Collect creatures on battlefield to exile
            bf_creatures = [c for c in player.battlefield if c.template.is_creature]
            # Collect creatures in graveyard to return
            gy_creatures = [c for c in player.graveyard if c.template.is_creature]

            # Exile battlefield creatures
            for creature in bf_creatures:
                player.battlefield.remove(creature)
                creature.zone = "exile"
                creature.reset_combat()
                creature.cleanup_damage()
                player.exile.append(creature)

            # Return graveyard creatures to battlefield
            for creature in gy_creatures:
                player.graveyard.remove(creature)
                creature.controller = p_idx
                creature.enter_battlefield()
                player.battlefield.append(creature)
                game._handle_permanent_etb(creature, p_idx)
                game.log.append(f"T{game.display_turn}: Living End returns "
                                f"{creature.name} for P{p_idx+1}")

        # Mark the controller's next combat as aggressive. Living End resets the
        # board in our favour; the AI should swing all-in even with blockers back
        # because the opponent has no creatures and any incremental damage is
        # close to lethal.
        #
        # Set to 2 (not 1): the first decrement happens in end_combat on the
        # turn Living End resolves, but the returned creatures have summoning
        # sickness on that turn and can't attack anyway. We need the flag to
        # SURVIVE that wasted decrement so the NEXT turn's combat sees it.
        game.players[controller].aggression_boost_turns = max(
            getattr(game.players[controller], 'aggression_boost_turns', 0), 2
        )

        # Sustained post-combo push: GoalEngine stays in PUSH_DAMAGE for
        # the next 3 turns. Opponent has no board; any incremental damage
        # is worth vastly more than the usual curve-out / deploy-engine
        # fill-in plays. Decremented each upkeep.
        game.players[controller].post_combo_push_turns = max(
            getattr(game.players[controller], 'post_combo_push_turns', 0), 3
        )

        # Signal the AI's GoalEngine to advance past CURVE_OUT / DEPLOY_ENGINE
        # into PUSH_DAMAGE on the next main-phase entry. Without this the
        # cascade deck keeps casting tutors / ritual fodder instead of
        # closing the game with the board it just produced. Consumed once
        # by ev_player._execute_main_phase.
        if not hasattr(game, '_pending_goal_advance'):
            game._pending_goal_advance = {}
        game._pending_goal_advance[controller] = 'post_combo_aggression'

    # ─── REANIMATION ─────────────────────────────────────────────


    @staticmethod
    def _execute_spell_effects(game: "GameState", item: StackItem):
        """Execute the effects of an instant/sorcery spell."""
        card = item.source
        controller = item.controller
        opponent = 1 - controller
        name = card.name

        # Rituals: add mana to pool (oracle-derived from template)
        ritual_data = card.template.ritual_mana
        if ritual_data:
            color, amount = ritual_data
            if color == "any":
                game.players[controller].mana_pool.add("R", 2)
                # Manamorphose draws a card
                if 'cantrip' in card.template.tags:
                    game.draw_cards(controller, 1)
            else:
                game.players[controller].mana_pool.add(color, amount)
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                            f"{name} adds {amount} {color} mana")

            # Splice: add mana from spliced card effects
            for spliced_tmpl in item.spliced:
                splice_ritual = spliced_tmpl.ritual_mana
                if splice_ritual:
                    sc, sa = splice_ritual
                    if sc == "any":
                        game.players[controller].mana_pool.add("R", 2)
                    else:
                        game.players[controller].mana_pool.add(sc, sa)
                    game.log.append(f"T{game.display_turn} P{controller+1}: "
                                    f"  Spliced {spliced_tmpl.name} adds {sa} {sc} mana")
            return

        # Dispatch to card effect registry
        # Snapshot opponent state before resolution to auto-generate target log
        _opp = game.players[1 - controller]
        _pre_life = _opp.life
        _pre_creatures = {c.instance_id: (c.name, c.toughness) for c in _opp.creatures}
        _pre_hand = len(_opp.hand)
        _pre_log_len = len(game.log)
        if EFFECT_REGISTRY.execute(
            name, EffectTiming.SPELL_RESOLVE, game, card, controller,
            targets=item.targets, item=item
        ):
            # Auto-generate target summary if no specific log was written
            # (check if last log entry already describes this spell's effect)
            # Check if handler wrote a meaningful log naming the spell
            _handler_logs = game.log[_pre_log_len:]
            _spell_logged = any(name in l for l in _handler_logs)
            _already_logged = _spell_logged
            if not _already_logged:
                effects = []
                # Creature deaths (prefer over face damage — spell targeted creature)
                killed = [cname for iid, (cname, _) in _pre_creatures.items()
                          if not any(c.instance_id == iid for c in _opp.creatures)]
                if killed:
                    effects.append(f"kills {', '.join(killed)}")
                elif _opp.life < _pre_life:
                    # Only log face damage if no creature died (not a creature spell)
                    effects.append(f"{_pre_life - _opp.life} damage → life {_opp.life}")
                # Discard
                if len(_opp.hand) < _pre_hand:
                    effects.append(f"opponent discards {_pre_hand - len(_opp.hand)}")
                if effects:
                    game.log.append(f"T{game.display_turn} P{controller+1}: "
                                    f"{name} → {', '.join(effects)}")
            return  # Registry handled it

        # ── Oracle-driven spell resolver (Phase I migration target) ──
        # When no EFFECT_REGISTRY handler claimed the spell, parse oracle
        # text for generic patterns (draw, discard, etc.). Returns True
        # when an effect fires, in which case the legacy ability-parser
        # below is skipped.
        from .oracle_resolver import resolve_spell_from_oracle
        if resolve_spell_from_oracle(game, card, controller, item.targets):
            return

        # ── Generic fallback: parse abilities from oracle text ──
        # All named card effects are now handled by EFFECT_REGISTRY (card_effects.py).
        # Legacy named-card blocks have been removed (Phase 2D migration).
        # Only the generic ability parser below remains as a last resort.
        # (Legacy named-card blocks deleted — all handled by EFFECT_REGISTRY)

        # ── Generic effect handling ──
        effects = []
        for ability in card.template.abilities:
            if ability.description:
                effects.append(ability)

        for ability in effects:
            desc = ability.description.lower()

            if "damage" in desc:
                amount = 0
                for word in desc.split():
                    try:
                        amount = int(word)
                        break
                    except ValueError:
                        continue

                if item.targets:
                    for tid in item.targets:
                        target = game.get_card_by_id(tid)
                        if target and target.zone == "battlefield" and target.template.is_creature:
                            target.damage_marked += amount
                            if target.is_dead:
                                game._creature_dies(target)
                elif "each opponent" in desc or "player" in desc:
                    game.players[opponent].life -= amount
                    game.players[controller].damage_dealt_this_turn += amount
                elif amount > 0:
                    game.players[opponent].life -= amount
                    game.players[controller].damage_dealt_this_turn += amount

            elif "destroy" in desc:
                if "all" in desc:
                    for p in game.players:
                        creatures_to_destroy = [c for c in p.creatures
                                                if Keyword.INDESTRUCTIBLE not in c.keywords]
                        for creature in creatures_to_destroy:
                            game._creature_dies(creature)
                elif item.targets:
                    for tid in item.targets:
                        target = game.get_card_by_id(tid)
                        if target and target.zone == "battlefield":
                            if Keyword.INDESTRUCTIBLE not in target.keywords:
                                game._permanent_destroyed(target)

            elif "exile" in desc:
                if "all" in desc:
                    for p in game.players:
                        to_exile = [c for c in p.battlefield
                                    if not c.template.is_land]
                        for c in to_exile:
                            game._exile_permanent(c)
                elif item.targets:
                    for tid in item.targets:
                        target = game.get_card_by_id(tid)
                        if target and target.zone == "battlefield":
                            game._exile_permanent(target)

            elif "counter" in desc:
                # Validate counterspell targeting restrictions
                counter_oracle = (card.template.oracle_text or '').lower()
                target_template = None
                if item.targets:
                    for tid in item.targets:
                        for si in game.stack.items:
                            if si.source.instance_id == tid:
                                target_template = si.source.template
                                break
                elif not game.stack.is_empty:
                    target_template = game.stack.top.source.template if game.stack.top else None

                # Noncreature-only counters can't hit creatures
                if target_template and 'noncreature' in counter_oracle and target_template.is_creature:
                    game.log.append(f"T{game.display_turn}: {card.name} fizzles (can't counter creature)")
                elif target_template and 'instant or sorcery' in counter_oracle and not (target_template.is_instant or target_template.is_sorcery):
                    game.log.append(f"T{game.display_turn}: {card.name} fizzles (wrong target type)")
                elif item.targets:
                    for tid in item.targets:
                        # Find the targeted spell on the stack
                        for i, stack_item in enumerate(game.stack.items):
                            if stack_item.source.instance_id == tid:
                                countered = game.stack.items.pop(i)
                                countered_card = countered.source
                                countered_card.zone = "graveyard"
                                game.players[countered_card.owner].graveyard.append(countered_card)
                                game.log.append(
                                    f"T{game.display_turn}: {countered_card.name} is countered")
                                break
                elif not game.stack.is_empty:
                    # No explicit target — counter the next spell on the stack
                    countered = game.stack.pop()
                    countered_card = countered.source
                    countered_card.zone = "graveyard"
                    game.players[countered_card.owner].graveyard.append(countered_card)
                    game.log.append(
                        f"T{game.display_turn}: {countered_card.name} is countered")

            elif "draw" in desc:
                amount = 1
                for word in desc.split():
                    try:
                        amount = int(word)
                        break
                    except ValueError:
                        continue
                game.draw_cards(controller, amount)

            elif "gain" in desc and "life" in desc:
                amount = 0
                for word in desc.split():
                    try:
                        amount = int(word)
                        break
                    except ValueError:
                        continue
                game.gain_life(controller, amount, "ability")

            elif "return" in desc and "hand" in desc:
                if item.targets:
                    for tid in item.targets:
                        target = game.get_card_by_id(tid)
                        if target and target.zone == "battlefield":
                            game._bounce_permanent(target)

            elif "search" in desc and "library" in desc and "land" in desc:
                player = game.players[controller]
                for i, card_in_lib in enumerate(player.library):
                    if card_in_lib.template.is_land:
                        land = player.library.pop(i)
                        land.controller = controller
                        land.enter_battlefield()
                        land.tapped = True
                        player.battlefield.append(land)
                        break

            elif "discard" in desc:
                amount = 1
                for word in desc.split():
                    try:
                        amount = int(word)
                        break
                    except ValueError:
                        continue
                target_player = opponent if "opponent" in desc else controller
                game._force_discard(target_player, amount)

            elif "create" in desc and "token" in desc:
                # Try to parse token from description
                import re
                token_match = re.search(r'create\s+(?:a|(\d+))\s+(\d+)/(\d+)', desc)
                if token_match:
                    count = int(token_match.group(1) or 1)
                    p = int(token_match.group(2))
                    t = int(token_match.group(3))
                    game.create_token(controller, "creature", count, p, t)

    # ─── BLINK ───────────────────────────────────────────────────


    @staticmethod
    def _blink_permanent(game: "GameState", card: CardInstance, controller: int):
        """Exile a permanent and return it to the battlefield immediately."""
        if card in game.players[card.controller].battlefield:
            game.players[card.controller].battlefield.remove(card)
        card.zone = "exile"
        # Return immediately
        card.controller = controller
        card.enter_battlefield()
        game.players[controller].battlefield.append(card)
        game._handle_permanent_etb(card, controller)
        game.log.append(f"T{game.display_turn}: Blink {card.name}")

    # ─── ZONE CHANGES ────────────────────────────────────────────


