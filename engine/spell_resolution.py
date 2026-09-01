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
    def _move_resolved_spell_off_stack(game: "GameState", card: "CardInstance"):
        """Move an instant/sorcery off the stack to its correct zone,
        applying the alternate-cast zone-replacement effects (CR
        702.33a flashback, CR 702.86 rebound, CR 707.10a spell copy).

        These replacements are tied to HOW the spell was cast, not to
        how it left the stack — they apply identically whether the
        spell resolves normally or is countered. Single owner of this
        logic so both call sites (normal resolution and the
        counterspell branch in `_execute_spell_effects`) stay in sync.

        All zone mutations go through ``game.zone_mgr.move_card_from_stack``
        so that CR 614 replacement effects (e.g. Rest in Peace) can
        intercept the destination, and CR 603 zone-change triggers fire.
        """
        if getattr(card, '_cast_with_flashback', False):
            # Flashback: exile instead of going to graveyard (CR 702.33a)
            game.zone_mgr.move_card_from_stack(
                game, card, "exile", cause="flashback resolution (CR 702.33a)"
            )
            card.has_flashback = False  # no longer has flashback
        elif (hasattr(card, '_rebound_controller')
              and getattr(card, '_cast_from_zone', 'hand') == 'hand'):
            # Rebound: exile instead of graveyard, cast for free next
            # upkeep. CR 702.88a scopes the replacement to a spell cast
            # FROM HAND — the free recast itself is cast from exile, so
            # it resolves into the graveyard and the loop terminates
            # after one repetition. Cards constructed directly on the
            # stack (fixtures) carry no origin and default to hand.
            game.zone_mgr.move_card_from_stack(
                game, card, "exile", cause="rebound (CR 702.86)"
            )
            if not hasattr(game, '_rebound_cards'):
                game._rebound_cards = []
            game._rebound_cards.append(card)
        elif getattr(card, '_is_spell_copy', False):
            # CR 707.10a — a resolved (or countered) spell COPY ceases
            # to exist; it never enters the graveyard (imprint-copy
            # artifacts, storm copies routed here, …).
            game.zone_mgr.move_card_from_stack(
                game, card, "expired_copy", cause="spell copy ceases (CR 707.10a)"
            )
        else:
            game.zone_mgr.move_card_from_stack(
                game, card, "graveyard", cause="resolution"
            )

    @staticmethod
    def _move_countered_stack_item(game: "GameState", stack_item: "StackItem",
                                    countered_card: "CardInstance"):
        """Move a countered stack item's source card to its correct
        zone. For a countered SPELL, this is the same alternate-cast
        zone-replacement logic as normal resolution (CR 702.33a/86,
        707.10a — see `_move_resolved_spell_off_stack`): countering a
        spell is one of the ways it "would be put into a graveyard
        from the stack", so the same replacement applies. For a
        countered ABILITY, the source is a permanent (already on the
        battlefield or wherever it was) — it does not move; only the
        ability itself fails to resolve. (Pre-existing behavior for
        ability items is unchanged here — out of scope for this fix.)
        """
        if stack_item.item_type == StackItemType.SPELL:
            ResolutionManager._move_resolved_spell_off_stack(game, countered_card)
        else:
            # Countered activated/triggered ability: the source is a permanent
            # still on the battlefield — it does not move (CR: abilities have
            # no physical card separate from their source permanent). The
            # ability item has already been removed from the stack by the
            # caller; no zone mutation is needed here.
            pass

    @staticmethod
    def resolve_stack(game: "GameState"):
        """Resolve the top item on the stack."""
        if game.stack.is_empty:
            return

        item = game.stack.pop()
        card = item.source
        template = card.template

        # CR 702.21a — Ward: "Whenever this permanent becomes the
        # target of a spell or ability an opponent controls, counter
        # that spell or ability unless its controller pays [cost]."
        # Checked here — for EVERY spell/ability item, right before
        # it would resolve — because ward is a property of the
        # TARGET, not of the spell/ability targeting it (unlike 1a's
        # counter-tax, which only applies to counterspells and is
        # dispatched from their own resolution branch below). Mirror
        # image of 1a's decision-maker relationship: the choice
        # belongs to THIS item's own controller (the caster who chose
        # a warded target), not to the warded permanent's controller.
        # Multiple simultaneously-targeted warded permanents each get
        # their own tax offer in target order; the first one left
        # unpaid counters the whole spell/ability immediately (CR
        # 702.21a counters the SPELL, not just that one target) and
        # stops further ward checks — there is nothing left to tax.
        # CR 603.3: a permanent spell (non-Aura creature/artifact/
        # enchantment/planeswalker) does NOT target — its ETB/attack
        # trigger does, on a separate stack object. Ward on such a
        # trigger-bound target may counter the trigger when it resolves,
        # but never the permanent spell, which still enters. Same
        # exemption the CR 608.2b fizzle branch below already applies;
        # abilities and genuinely-targeted instants/sorceries/Auras are
        # unaffected.
        _pt_ward = getattr(card.template, 'card_types', None) or []
        _is_permanent_spell_ward = any(
            t in _pt_ward for t in (CardType.CREATURE, CardType.ARTIFACT,
                                    CardType.ENCHANTMENT, CardType.PLANESWALKER))
        _is_aura_ward = getattr(card.template, 'aura_enchant_restriction', None) is not None
        _ward_can_counter = not (item.item_type == StackItemType.SPELL
                                 and _is_permanent_spell_ward and not _is_aura_ward)
        for _tid in (list(item.targets) if _ward_can_counter else []):
            if not isinstance(_tid, int) or _tid < 0:
                continue  # face/player target — permanents only have ward
            _target = game.get_card_by_id(_tid)
            if _target is None or _target.zone != "battlefield":
                continue
            _ward_amount = getattr(_target.template, 'ward_cost', 0) or 0
            if _ward_amount <= 0:
                continue
            if _target.controller == item.controller:
                continue  # CR 702.21a: only vs an OPPONENT's spell/ability
            from .optional_costs import offer_ward_tax
            _paid = offer_ward_tax(game, _target, card, item.controller)
            if _paid:
                game.log.append(
                    f"T{game.display_turn}: {card.name}'s controller "
                    f"pays {_ward_amount} — not countered by "
                    f"{_target.name}'s ward")
            else:
                ResolutionManager._move_countered_stack_item(game, item, card)
                game.log.append(
                    f"T{game.display_turn}: {card.name} is countered "
                    f"by {_target.name}'s ward")
                return

        # CR 608.2b: re-check target legality on resolution. A spell
        # whose targets are ALL illegal doesn't resolve — it fizzles
        # to the graveyard with no effect. Ported from the dead legacy
        # resolver (engine/stack.py, pre-unification); see
        # docs/proposals/resolver_sba_unification.md §5.1.
        #
        # Fizzling applies to the SPELL's own targets. A permanent spell
        # (creature/artifact/enchantment/planeswalker) that is NOT an
        # Aura enters the battlefield regardless of targets — a "when you
        # cast this spell" trigger is a separate object (CR 603.3), and
        # its target (recorded on this item and exiled by the trigger)
        # must not fizzle the permanent. Only instants, sorceries, and
        # Auras fizzle on all-illegal targets.
        _pt = getattr(card.template, 'card_types', None) or []
        _is_permanent_spell = any(
            t in _pt for t in (CardType.CREATURE, CardType.ARTIFACT,
                               CardType.ENCHANTMENT, CardType.PLANESWALKER))
        _is_aura = getattr(card.template, 'aura_enchant_restriction', None) is not None
        _fizzle_eligible = not (_is_permanent_spell and not _is_aura)
        if (item.item_type == StackItemType.SPELL and item.targets
                and _fizzle_eligible
                and ResolutionManager._spell_fizzles(game, item)):
            game.log.append(
                f"T{game.display_turn}: {card.name} fizzles "
                f"(all targets illegal, CR 608.2b)")
            # CR 702.33a / 702.86 / 707.10a: the same zone-replacement
            # effects that apply on normal resolution also apply when a
            # spell fizzles — "fizzle to the graveyard" is still a
            # "would be put into a graveyard from the stack" event.
            # Delegate to the single-owner helper so both code paths
            # stay in sync and route through the zone funnel.
            ResolutionManager._move_resolved_spell_off_stack(game, card)
            return

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
                ResolutionManager._move_resolved_spell_off_stack(game, card)
            else:
                # Permanent enters battlefield
                card.controller = item.controller
                # Cascade is a cast trigger (CR 702.85a): the trigger and
                # the free spell it casts resolve while the cascade SOURCE
                # is still on the stack — so a cascaded mass-effect (board
                # wipe, mass reanimation, mass bounce) must not see or
                # affect the source. Resolve cascade BEFORE the source
                # physically enters; the source enters last. Invisible for
                # instant/sorcery sources (they hit the graveyard), so only
                # a permanent source — entered first, then swept by its own
                # cascaded spell — exposed the bug.
                if Keyword.CASCADE in template.keywords:
                    game._handle_cascade(item)
                card.enter_battlefield()
                game.players[item.controller].battlefield.append(card)
                # Place counters for X-cost permanents — only if no dedicated
                # ETB handler exists (Engineered Explosives uses sunburst via its
                # own handler, so don't double-set charge counters here)
                if item.x_value > 0 and template.x_cost_data:
                    has_dedicated_etb = EFFECT_REGISTRY.has_handler(
                        template.name, EffectTiming.ETB)
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
                # Evoke: sacrifice after ETB triggers
                if getattr(card, '_evoked', False):
                    if card in game.players[item.controller].battlefield:
                        game.zone_mgr.move_card_to_graveyard(
                            game, card, cause="evoke sacrifice"
                        )
                        game.log.append(f"T{game.display_turn} P{item.controller+1}: "
                                       f"{card.name} sacrificed (evoke)")
                # Phlage sacrifice-unless-escaped: if cast normally (not escaped),
                # sacrifice after ETB trigger resolves
                if (template.escape_cost is not None
                        and not getattr(card, '_escaped', False)):
                    if card in game.players[item.controller].battlefield:
                        game.zone_mgr.move_card_to_graveyard(
                            game, card, cause="sacrifice (not escaped)"
                        )
                        game.log.append(f"T{game.display_turn} P{item.controller+1}: "
                                       f"{card.name} sacrificed (not escaped)")

        elif item.item_type in (StackItemType.ACTIVATED_ABILITY,
                                 StackItemType.TRIGGERED_ABILITY):
            if item.ability and item.ability.effect:
                item.ability.effect(game, item.source, item.controller, item.targets)
            elif item.effect:
                item.effect(game, item.source, item.controller, item.targets)


    @staticmethod
    def _spell_fizzles(game: "GameState", item: StackItem) -> bool:
        """CR 608.2b — true when EVERY target chosen at cast time is
        now illegal, in which case the spell doesn't resolve.

        Target-entry shapes handled (see ai/ev_player._choose_targets
        and ai/response.py for the producers):

        * negative int (``-1`` face marker): a player target. Players
          remain legal targets while the game is live — the game-over
          path never reaches resolution — so these always count as
          valid.
        * positive int: instance_id of a card. Legal iff the card
          still exists AND still occupies the zone it was in when
          targeted (cast-time snapshot in ``StackItem.target_zones``
          — battlefield for removal, stack for counterspells,
          graveyard for reanimation). Changing zone makes the target
          illegal (CR 608.2b).
        * anything else (direct object ref, player index without a
          snapshot, items built outside CastManager e.g. in tests):
          cannot be proven illegal — counts as valid, so the spell
          resolves. Fizzling is only ever asserted on positive
          evidence that a target left its zone.
        """
        for tid in item.targets:
            if not isinstance(tid, int) or tid < 0:
                return False  # player target / object ref — valid
            cast_zone = item.target_zones.get(tid)
            if cast_zone is None:
                return False  # no snapshot — can't prove illegal
            target = game.get_card_by_id(tid)
            if target is not None and target.zone == cast_zone:
                return False  # still where it was targeted — valid
        return True  # every target verifiably left its cast-time zone

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

        # Aura attachment (CR 303.4a): an Aura entering the battlefield
        # becomes attached to a legal object chosen by its "Enchant <quality>"
        # ability. Before this, Auras resolved as inert enchantments attached
        # to nothing — a mana Aura granted no mana at all.
        if getattr(template, 'aura_enchant_restriction', None):
            from .permanent_effects import PermanentEffects
            PermanentEffects.attach_aura(game, card, controller)

        # Planeswalker: set loyalty counters from template (oracle-derived)
        if CardType.PLANESWALKER in template.card_types:
            card.loyalty_counters = template.loyalty or 0

        # Modular (CR 702.43): enters with N +1/+1 counters.
        # Keyed on Keyword.MODULAR in template.keywords (populated at DB load via
        # KEYWORD_MAP / word-boundary scan) and template.modular_n > 0 (parsed
        # from "Modular N" in oracle text). Works for all modular cards — no card
        # names involved.  "Modular—Sunburst" has modular_n == 0 and is skipped.
        if Keyword.MODULAR in template.keywords and template.modular_n > 0:
            card.plus_counters += template.modular_n
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{template.name} enters with {template.modular_n} "
                f"+1/+1 counter(s) (modular)"
            )

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
        # effective_card_types (not template.card_types) so a
        # transformed creature-backed permanent is correctly seen as
        # a creature for this suppression check, not its front face's
        # type.
        is_creature = CardType.CREATURE in card.effective_card_types

        # Doorkeeper Thrull static: "Artifacts and creatures entering the
        # battlefield don't cause abilities to trigger." Generic oracle
        # check (no card name) — triggers when any permanent on the board
        # has that static clause.
        is_artifact = CardType.ARTIFACT in card.effective_card_types
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
            # Dispatch to card effect registry for card-specific ETB logic.
            #
            # Skipped entirely when `card.is_transformed`: EFFECT_REGISTRY
            # keys handlers on the literal card name, which is identical
            # for both faces of a DFC (e.g. "Fable of the Mirror-Breaker
            # // Reflection of Kiki-Jiki") — there is no way to register
            # a handler specific to one face. A transform-via-exile-
            # return (Fable Ch.III) genuinely re-enters the battlefield,
            # but what "enters" is the BACK face; firing the FRONT
            # face's own registered ETB handler (Fable's "create a
            # Goblin token") on that re-entry is a category error — the
            # handler describes the front face's printed text, not
            # what's now on the battlefield. Same reasoning for the
            # generic oracle-derived fallback below, which reads
            # `template.oracle_text` (also front-face). No card in the
            # current pool has a back-face-specific ETB effect that
            # would need to fire here; if one is added, it needs its
            # own dispatch keyed on back-face identity, not this path.
            #
            # Timing-scoped (not name-scoped) when it DOES run: a card
            # whose only registration is for an unrelated timing
            # (SPELL_RESOLVE, ATTACK, DIES, END_STEP) must still reach
            # the generic oracle-derived ETB resolver below. Mirrors
            # the correct pattern already used in
            # zone_transfer._fire_etb_triggers.
            has_specific_handler = False
            oracle_resolver_fired = False
            if not card.is_transformed:
                has_specific_handler = EFFECT_REGISTRY.has_handler(
                    template.name, EffectTiming.ETB)
                EFFECT_REGISTRY.execute(
                    template.name, EffectTiming.ETB, game, card, controller,
                    targets=(item.targets if item else None),
                    item=item,
                )

                # Generic oracle-text-based ETB resolution for cards WITHOUT specific handlers
                if not has_specific_handler:
                    from .oracle_resolver import resolve_etb_from_oracle
                    oracle_resolver_fired = resolve_etb_from_oracle(game, card, controller)

            # Generic ETB triggers
            game.trigger_etb(card, controller)

            # Silent-miss detection (parallel to the spell path): when no
            # layer claims the ETB AND the card has no parsed ETB ability
            # AND the oracle declares a SELF ETB trigger
            # ("when/whenever/as <card-name> enters"), the ETB resolved to
            # a no-op. The predicate intentionally requires the card's own
            # name right before "enters" so "whenever ANOTHER X enters"
            # static-watcher triggers (Amulet of Vigor, Eldrazi Mimic,
            # Risen Reef shape) don't false-positive.
            if not has_specific_handler and not oracle_resolver_fired:
                from .cards import AbilityType
                has_etb_ability = any(
                    a.ability_type == AbilityType.ETB
                    for a in template.abilities
                )
                if not has_etb_ability:
                    oracle_lc = (template.oracle_text or '').lower()
                    name_lc = template.name.lower()
                    if re.search(
                        rf'\b(?:when|whenever|as)\s+{re.escape(name_lc)}\s+enters\b',
                        oracle_lc,
                    ):
                        from .effect_diagnostics import record_unhandled_effect
                        record_unhandled_effect(template.name, "etb")

    # ─── STORM ───────────────────────────────────────────────────


    @staticmethod
    def _resolve_living_end(game: "GameState", controller: int):
        """Living End: exile all creatures from battlefield, return all from graveyard."""
        game.log.append(f"T{game.display_turn}: Living End resolves!")

        # Grafdigger's Cage and functional reprints: creatures can't
        # enter the battlefield from graveyards. We still perform the
        # battlefield exile (Cage gates entry-from-GY only, not
        # leaves-battlefield), but skip the graveyard-return portion
        # entirely. Oracle-driven detection so any hate card with the
        # same clause is handled without a name check.
        hate_card = game._gy_reanimation_hate_source()

        # For each player: exile battlefield creatures, return graveyard creatures
        for p_idx in range(2):
            player = game.players[p_idx]

            # Collect creatures on battlefield to exile
            bf_creatures = [c for c in player.battlefield if c.template.is_creature]
            # Collect creatures in graveyard to return
            gy_creatures = [c for c in player.graveyard if c.template.is_creature]

            # Exile battlefield creatures (zone funnel handles removal,
            # cleanup, and exile-list append).
            for creature in bf_creatures:
                game.zone_mgr.move_card(
                    game, creature, "battlefield", "exile",
                    cause="living end"
                )

            # Return graveyard creatures to battlefield (gated by Cage)
            if hate_card is not None:
                if gy_creatures:
                    game.log.append(
                        f"T{game.display_turn}: {hate_card.name} prevents "
                        f"{len(gy_creatures)} creature(s) from returning "
                        f"to the battlefield for P{p_idx+1} "
                        f"(cards stay in graveyard)."
                    )
                continue

            # CR 614 simultaneous return: bulk-remove from GY before
            # firing any ETB. An ETB that mutates this same GY (e.g.,
            # Endurance's clear) would otherwise desync the snapshot
            # from the live list and raise on .remove().
            to_return = set(map(id, gy_creatures))
            player.graveyard[:] = [c for c in player.graveyard
                                   if id(c) not in to_return]
            for creature in gy_creatures:
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

        # ── Modal "Choose one/two —" spells ──
        # Resolve exactly the chosen mode(s), not every mode. Scope:
        # multi-mode, non-counterspell instants/sorceries with no
        # counter mode (the counterspell path and single-parsed-mode
        # charms already resolve their one mode correctly and are left
        # untouched). Each chosen mode resolves off its REAL clause via
        # resolve_spell_from_oracle(oracle_override=...), so a mode's
        # type / mana-value restriction survives (the synthesized
        # per-mode ability description drops it).
        tmpl = card.template
        modes = getattr(tmpl, 'modes', None) or []
        # Gate on the number of PARSED mode-abilities, not the number of
        # printed modes: the bug is a modal card that synthesized MORE
        # THAN ONE ability and runs them all (Brotherhood's End: 2). A
        # modal card that parsed to a single ability (Kozilek's Command,
        # the charms) already resolves its one mode and must be left on
        # its existing path — intercepting it would route a mode clause
        # this generic resolver cannot fully execute.
        _n_abilities = len([a for a in tmpl.abilities if a.description])
        if (getattr(tmpl, 'is_modal', False)
                and _n_abilities > getattr(tmpl, 'modal_choose_count', 1)
                and not getattr(tmpl, 'is_counterspell', False)
                and (tmpl.is_instant or tmpl.is_sorcery)
                and not any('counter target' in m.get('text', '').lower()
                            for m in modes)):
            from ai.modal import select_modal_modes
            from .oracle_resolver import resolve_spell_from_oracle
            chosen = select_modal_modes(game, card, controller, item.targets)
            for idx in chosen:
                clause = modes[idx].get('text', '')
                resolve_spell_from_oracle(game, card, controller, item.targets,
                                          x_value=item.x_value,
                                          oracle_override=clause)
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
        #
        # Counterspells skip this resolver entirely and always fall
        # through to the generic per-ability loop below. Reason: this
        # resolver's pattern matches (e.g. "draw N cards") test the
        # WHOLE oracle string, not a single clause — on a multi-clause
        # counterspell (Cryptic Command's "Draw a card." mode; Censor/
        # Confirm Suspicions/Exclude's "Draw a card. Counter target
        # ... spell." template) it fires the draw and returns True,
        # never reaching the counter effect at all. The per-ability
        # loop below processes each synthesized ability independently
        # (draw AND counter both fire), so it is the only path that
        # gets a multi-ability counterspell's counter effect to run.
        from .oracle_resolver import resolve_spell_from_oracle
        if not getattr(card.template, 'is_counterspell', False):
            if resolve_spell_from_oracle(game, card, controller, item.targets,
                                         x_value=item.x_value):
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

        # Silent-miss detection: reaching here means neither the
        # EFFECT_REGISTRY handler nor resolve_spell_from_oracle claimed
        # this instant/sorcery. Track whether the legacy parser below
        # recognises a verb; if nothing does, the spell resolved to a
        # no-op and is recorded for the unhandled-effect diagnostic.
        matched_any = False

        for ability in effects:
            desc = ability.description.lower()

            if ("damage" in desc or "destroy" in desc or "exile" in desc
                    or "counter" in desc or "draw" in desc
                    or ("gain" in desc and "life" in desc)
                    or ("return" in desc and "hand" in desc)
                    or ("search" in desc and "library" in desc and "land" in desc)
                    or "discard" in desc
                    or ("create" in desc and "token" in desc)):
                matched_any = True

            if "damage" in desc:
                from .damage import deal_damage
                amount = 0
                for word in desc.split():
                    try:
                        amount = int(word)
                        break
                    except ValueError:
                        continue

                if item.targets:
                    for tid in item.targets:
                        if tid == -1:
                            # AI chose face — route to player damage.
                            deal_damage(item.source, game.players[opponent], amount)
                            continue
                        target = game.get_card_by_id(tid)
                        if (target and target.zone == "battlefield"
                                and (target.template.is_creature
                                     or CardType.PLANESWALKER in target.template.card_types)):
                            deal_damage(item.source, target, amount)
                elif ("all_creatures" in desc or "each creature" in desc
                      or "all creatures" in desc):
                    # Symmetric damage sweep (Pyroclasm / Anger of the Gods /
                    # Sweltering Suns / Kozilek's Return): N damage to every
                    # creature on both battlefields — and planeswalkers too
                    # when the clause names them. It deals nothing to players;
                    # without this case it fell through to the face fallback.
                    also_pw = "planeswalker" in desc
                    for _pl in game.players:
                        for _perm in list(_pl.battlefield):
                            if (_perm.template.is_creature
                                    or (also_pw and CardType.PLANESWALKER
                                        in _perm.template.card_types)):
                                deal_damage(item.source, _perm, amount)
                elif "each opponent" in desc or "player" in desc:
                    deal_damage(item.source, game.players[opponent], amount)
                elif amount > 0:
                    deal_damage(item.source, game.players[opponent], amount)

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

            elif (getattr(card.template, 'is_counterspell', False)
                  and desc.startswith('counter target')):
                # Gated on the structured `is_counterspell` flag
                # (populated at load time from the same parsed effect
                # the "Counter target ..." ability description was
                # built from) PLUS this specific ability's synthesized
                # description, not a raw oracle-text substring match —
                # a raw-text `"counter" in desc` check collides with
                # unrelated "counter" mentions ("+1/+1 counter",
                # "charge counter") elsewhere on the same card, and a
                # template-only flag (with no per-ability scoping)
                # would re-fire on every OTHER ability of a multi-
                # ability counterspell (58 Modern-legal counterspells —
                # Cryptic Command, Absorb, Censor, ... — carry a
                # second ability, e.g. "Draw 1 card(s)", alongside the
                # counter effect; only the counter ability's own
                # iteration should enter this branch).
                #
                # Validate counterspell targeting restrictions via the
                # structured `counter_target_kind` field, for the same
                # substring-collision reason as above.
                target_kind = getattr(card.template, 'counter_target_kind', '')
                target_template = None
                target_stack_index = None
                if item.targets:
                    for tid in item.targets:
                        for i, si in enumerate(game.stack.items):
                            if si.source.instance_id == tid:
                                target_template = si.source.template
                                target_stack_index = i
                                break
                        if target_template is not None:
                            break
                elif not game.stack.is_empty:
                    target_template = game.stack.top.source.template
                    target_stack_index = len(game.stack.items) - 1

                # Noncreature-only counters can't hit creatures
                if target_template and target_kind == 'noncreature_spell' and target_template.is_creature:
                    game.log.append(f"T{game.display_turn}: {card.name} fizzles (can't counter creature)")
                elif target_template and target_kind == 'instant_or_sorcery_spell' and not (target_template.is_instant or target_template.is_sorcery):
                    game.log.append(f"T{game.display_turn}: {card.name} fizzles (wrong target type)")
                elif target_stack_index is not None:
                    stack_item = game.stack.items[target_stack_index]
                    countered_card = stack_item.source
                    tax_amount = getattr(card.template, 'counter_tax_amount', 0) or 0
                    paid = False
                    if tax_amount > 0:
                        from .optional_costs import offer_counter_tax
                        paid = offer_counter_tax(game, card, countered_card)
                    if paid:
                        game.log.append(
                            f"T{game.display_turn}: {countered_card.name}'s "
                            f"controller pays {tax_amount} — not countered "
                            f"by {card.name}")
                    else:
                        game.stack.items.pop(target_stack_index)
                        ResolutionManager._move_countered_stack_item(
                            game, stack_item, countered_card)
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

        # An instant/sorcery that reached the legacy parser and matched
        # no verb resolved to a no-op — record it so the silent miss is
        # observable. Rituals (handled above) and registry/oracle hits
        # never reach here.
        if not matched_any and (card.template.oracle_text or '').strip():
            from .effect_diagnostics import record_unhandled_effect
            record_unhandled_effect(card.name, "spell")

    # ─── BLINK ───────────────────────────────────────────────────


    @staticmethod
    def _blink_permanent(game: "GameState", card: CardInstance, controller: int):
        """Exile a permanent and return it to the battlefield immediately.

        Zone bookkeeping (battlefield → exile → battlefield) is handled by
        ``zone_mgr._blink_zone_transition``, which lives inside the zone-
        transfer funnel (excluded from the zone-mutation ratchet). The caller
        is responsible for ETB effects via ``_handle_permanent_etb``.
        """
        game.zone_mgr._blink_zone_transition(game, card, controller)
        game._handle_permanent_etb(card, controller)
        game.log.append(f"T{game.display_turn}: Blink {card.name}")

    # ─── ZONE CHANGES ────────────────────────────────────────────


