"""
Stack response logic — extracted from AIPlayer (Phase 4B).

Handles instant-speed responses: counterspells, blink saves,
instant removal, and threat evaluation for stack items.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, List, Optional, Tuple

from ai.scoring_constants import (
    CHEAP_COUNTER_PAID_THRESHOLD,
    CHEAP_THREAT_PAID_THRESHOLD,
    CLOCK_IMPACT_LIFE_SCALING,
    COUNTER_GATE_HIGH_MULTIPLIER,
    COUNTER_GATE_LOW_MULTIPLIER,
    EQUIPMENT_DEFAULT_POWER_BONUS,
    EQUIPMENT_RESIDENCY_TURNS,
    HELD_COUNTER_FLOOR_MIN_EV,
    LETHAL_THREAT,
    NEAR_LETHAL_CUTOFF,
    PITCH_COUNTER_FREE_COST,
    PROACTIVE_REMOVAL_MIN_VALUE,
)

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.cards import CardInstance
    from engine.stack import StackItem
    from ai.turn_planner import TurnPlanner
    from ai.strategic_logger import StrategicLogger


class ResponseDecider:
    """Decides instant-speed responses to opponent's spells."""

    def __init__(self, player_idx: int, turn_planner: Optional["TurnPlanner"] = None, strategic_logger: Optional["StrategicLogger"] = None) -> None:
        self.player_idx = player_idx
        self.turn_planner = turn_planner
        self.strategic_logger = strategic_logger

    def decide_response(self, game: "GameState", stack_item: "StackItem",
                        pick_removal_target_fn: Optional[Callable] = None) -> Optional[Tuple["CardInstance", List[int]]]:
        """Decide whether and how to respond to a stack item.

        Returns (response_card, targets) or None.
        """
        from ai.evaluator import estimate_spell_value, estimate_permanent_value
        from engine.cards import CardType, Keyword

        player = game.players[self.player_idx]
        instants = [c for c in player.hand
                    if (c.template.is_instant or c.template.has_flash)
                    and game.can_cast(self.player_idx, c)]

        # "Can't be countered" — don't try counterspells against these
        threat_oracle = (stack_item.source.template.oracle_text or '').lower()
        if "can't be countered" in threat_oracle or "can\u2019t be countered" in threat_oracle:
            instants = [c for c in instants if "counterspell" not in c.template.tags]

        if not instants:
            if self.strategic_logger:
                self.strategic_logger.log_no_response(
                    self.player_idx, stack_item.source.name, game,
                    "No castable instants in hand")
            return None

        threat = self.evaluate_stack_threat(game, stack_item)

        # Triage rule (counter vs. flash creature-exile):
        # If the stack threat is a creature AND a flash/instant
        # creature-removal in hand can answer it post-resolution, the
        # counter is REDUNDANT against this threat and should be
        # reserved for non-creature threats (artifacts, planeswalkers,
        # sorceries) that creature-removal cannot touch.  Skipping the
        # counter for this creature lets the flash-removal handle it
        # and preserves the counter as the deck's only answer to
        # uncounterable-by-creature-removal threats.
        # Class size: applies to any (counter, flash creature-removal)
        # pairing — Solitude / Subtlety / Endurance / Path to Exile /
        # Fatal Push / Lightning Bolt / Swords to Plowshares / etc.
        #
        # Override: if the threat is so large that the post-resolution
        # path can't save us (lethal on resolution — burn at our face
        # at low life, or a creature swing already lethal), triage is
        # suspended and the counter fires regardless.
        #
        # Threshold uses `card_clock_impact` to derive the floor value
        # of holding a counter.  A held counter is worth at least
        # `card_clock_impact(snap) × CLOCK_IMPACT_LIFE_SCALING` in future
        # EV (same scaling the threat evaluator uses for equipment /
        # cascade).
        # Override fires when the current threat exceeds the LETHAL
        # sentinel scale (`evaluate_stack_threat`'s 100.0) — at half-
        # sentinel we already accept that no future use of the counter
        # is more important than this one.
        from ai.clock import card_clock_impact
        from ai.ev_evaluator import snapshot_from_game
        snap_self = snapshot_from_game(game, self.player_idx)
        # Floor on held-counter EV — a held counter is worth at least
        # this much when reserved for a future non-creature threat.
        # Used as the lower bound for the triage decision.
        # CLOCK_IMPACT_LIFE_SCALING converts clock-impact units into
        # life-point units (sourced from ai/scoring_constants.py).
        held_counter_floor_ev = card_clock_impact(snap_self) * CLOCK_IMPACT_LIFE_SCALING
        # Near-lethal cutoff: ½ × LETHAL_THREAT (sentinel inside
        # `evaluate_stack_threat`).  Above this, the counter must fire
        # because no held-counter future EV can outweigh "we lose now".
        # Sourced from ai/scoring_constants.py (NEAR_LETHAL_CUTOFF).
        # Triage suspends only when current threat dominates BOTH the
        # near-lethal cutoff AND any plausible held-counter future EV.
        triage_overridden = (
            threat >= NEAR_LETHAL_CUTOFF
            and threat > held_counter_floor_ev
        )
        skip_counter_for_this_creature = (
            not triage_overridden
            and self._has_post_resolution_creature_answer(game, stack_item)
        )

        # Try TurnPlanner's evaluate_response first
        try:
            from ai.turn_planner import extract_virtual_board, VirtualSpell
            vboard = extract_virtual_board(game, self.player_idx)

            src = stack_item.source
            threat_vspell = VirtualSpell(
                instance_id=src.instance_id,
                name=src.name,
                cmc=src.template.cmc or 0,
                tags=set(src.template.tags),
                is_instant=src.template.is_instant or src.template.has_flash,
                is_creature=src.template.is_creature,
                power=src.template.power or 0,
                toughness=src.template.toughness or 0,
                keywords=set(),
                spell_value=threat,
                damage=0,
                has_etb="etb_value" in src.template.tags,
            )

            v_responses = []
            for inst in instants:
                # Triage: drop redundant counters when a post-resolution
                # creature-exile is available.
                if (skip_counter_for_this_creature
                        and "counterspell" in inst.template.tags):
                    # Allow nearly-free pitch counters through (their
                    # opportunity cost is low — exiled card vs. UU).
                    effective_cost = self._effective_counter_cost(game, inst)
                    # Rules constant: a "free" pitch counter on opp's
                    # turn costs 1 (the exiled card).  Counters costing
                    # 2+ effective should be reserved.  Sourced from
                    # ai/scoring_constants.py (PITCH_COUNTER_FREE_COST).
                    if effective_cost > PITCH_COUNTER_FREE_COST:
                        continue
                # Use *effective* cost: pitch counters on opp turn cost 1 card,
                # not their printed CMC. Without this, TurnPlanner would
                # prefer the printed-cheaper counter (Counterspell at 2)
                # over a free pitch counter (Force of Negation at printed 3).
                effective_cmc = (
                    self._effective_counter_cost(game, inst)
                    if "counterspell" in inst.template.tags
                    else (inst.template.cmc or 0)
                )
                v_resp = VirtualSpell(
                    instance_id=inst.instance_id,
                    name=inst.name,
                    cmc=effective_cmc,
                    tags=set(inst.template.tags),
                    is_instant=True,
                    is_creature=False,
                    power=0, toughness=0,
                    keywords=set(),
                    spell_value=estimate_spell_value(inst, game, self.player_idx),
                    damage=0,
                    has_etb=False,
                )
                v_responses.append(v_resp)

            result = self.turn_planner.evaluate_response(
                vboard, threat, threat_vspell, v_responses)

            if result:
                v_spell, reasoning = result
                for inst in instants:
                    if inst.instance_id == v_spell.instance_id:
                        targets = self._choose_response_targets(
                            game, inst, stack_item, pick_removal_target_fn)
                        if self.strategic_logger:
                            self.strategic_logger.log_response(
                                self.player_idx, inst.name,
                                stack_item.source.name, game,
                                f"TurnPlanner: threat value {threat:.1f}, responding with {inst.name}")
                        return (inst, targets)

        except Exception as e:
            import logging
            logging.debug(f"TurnPlanner response failed: {e}")
            # Fall through to legacy path

        # Legacy fallback.
        # Counterspells: collect ALL eligible candidates first, then pick the
        # one with the lowest *effective* cost. Without this, a hand-order
        # iteration would fire the first castable counter even when a strictly
        # cheaper alternative (e.g. a pitch counter on opp's turn) is available
        # — burning real mana / a real card when a free counter exists.
        # Bug R2 (docs/diagnostics consolidated affinity findings).
        response_value = threat
        counter_candidates: List[Tuple[int, "CardInstance"]] = []
        # Rules constant: a "free" pitch counter on opp's turn costs 1
        # (the exiled card).  Counters with effective cost > 1 are
        # reserved when a post-resolution creature-exile is available.
        # Sourced from ai/scoring_constants.py (PITCH_COUNTER_FREE_COST).
        for instant in instants:
            if "counterspell" not in instant.template.tags:
                continue
            if not stack_item.source.template.is_spell:
                continue
            # Triage: skip redundant counters when a post-resolution
            # creature-exile in hand can answer the same threat.  Free
            # pitch counters (effective cost ≤ 1) are still considered
            # — their opportunity cost is too low to reserve.
            cost = self._effective_counter_cost(game, instant)
            if (skip_counter_for_this_creature
                    and cost > PITCH_COUNTER_FREE_COST):
                continue
            # Targeting restrictions from oracle text
            oracle = (instant.template.oracle_text or '').lower()
            target_spell = stack_item.source.template
            if 'noncreature' in oracle and target_spell.is_creature:
                continue
            if ('instant or sorcery' in oracle
                and not (target_spell.is_instant or target_spell.is_sorcery)):
                continue
            counter_candidates.append((cost, instant))

        if counter_candidates:
            counter_candidates.sort(key=lambda pair: pair[0])
            cost, chosen = counter_candidates[0]
            # Gate thresholds derived from held_counter_floor_ev: the
            # EV of holding the counter for a future spell. A counter
            # in hand is roughly "one card of impact" — quantified by
            # `card_clock_impact` × the same scaling factor (×20) used
            # everywhere else in evaluate_stack_threat to convert
            # clock-impact units into life-point units. Replaces the
            # hardcoded 1.5 / 3.0 thresholds (P0-A) which ignored that
            # a cheap counter is essentially free to spend on any
            # threat that exceeds the held-card floor.
            held_counter_floor_ev = self._held_counter_floor_ev(game)
            # COUNTER_GATE_HIGH: threat must clear the floor EV by a
            # decisive margin to justify firing an expensive counter.
            # Multiplier sourced from ai/scoring_constants.py
            # (COUNTER_GATE_HIGH_MULTIPLIER).
            COUNTER_GATE_HIGH = held_counter_floor_ev * COUNTER_GATE_HIGH_MULTIPLIER
            # COUNTER_GATE_LOW: when the trade is favourable (cheap
            # held counter OR cheaply-paid threat), the gate drops to
            # below-replacement so we still fire on tempo-positive
            # trades.  Multiplier sourced from ai/scoring_constants.py
            # (COUNTER_GATE_LOW_MULTIPLIER).
            COUNTER_GATE_LOW = held_counter_floor_ev * COUNTER_GATE_LOW_MULTIPLIER
            # `cost` is the counter's effective cost (already routed
            # through _effective_counter_cost which handles pitch-
            # counter alternatives). `threat_paid_cost` is what the
            # opponent actually paid to put this spell on the stack
            # — discounted by affinity, delve, domain, and generic
            # cost-reducers (extends the X-cost-only handling that
            # used to live in evaluate_stack_threat). A cheap held
            # counter OR a cheaply-paid threat both qualify the LOW
            # gate; either represents a strong tempo trade.
            # Thresholds sourced from ai/scoring_constants.py
            # (CHEAP_COUNTER_PAID_THRESHOLD / CHEAP_THREAT_PAID_THRESHOLD).
            threat_paid_cost = self._effective_paid_cost(game, stack_item)
            cheap_trade = (cost <= CHEAP_COUNTER_PAID_THRESHOLD
                           or threat_paid_cost <= CHEAP_THREAT_PAID_THRESHOLD)
            if (response_value >= COUNTER_GATE_HIGH
                    or (response_value >= COUNTER_GATE_LOW and cheap_trade)):
                if self.strategic_logger:
                    self.strategic_logger.log_response(
                        self.player_idx, chosen.name,
                        stack_item.source.name, game,
                        f"Counter: threat value {response_value:.1f} vs counter cost {cost} "
                        f"(opp paid {threat_paid_cost}). Floor EV {held_counter_floor_ev:.2f}, "
                        f"gate HIGH={COUNTER_GATE_HIGH:.2f}/LOW={COUNTER_GATE_LOW:.2f}. "
                        f"Worth countering (chose cheapest of {len(counter_candidates)} candidates).")
                return (chosen, [stack_item.source.instance_id])

        for instant in instants:
            tags = instant.template.tags

            # Blink response
            if "blink" in tags:
                if hasattr(stack_item, 'targets') and stack_item.targets:
                    me = game.players[self.player_idx]
                    my_creature_ids = {c.instance_id for c in me.creatures}
                    targeted_own = [tid for tid in stack_item.targets if tid in my_creature_ids]
                    if targeted_own:
                        return (instant, targeted_own[:1])
                if "removal" in stack_item.source.template.tags:
                    me = game.players[self.player_idx]
                    etb_creatures = [c for c in me.creatures
                                     if "etb_value" in c.template.tags]
                    if etb_creatures:
                        best = max(etb_creatures,
                                   key=lambda c: estimate_permanent_value(
                                       c, me, game, self.player_idx))
                        return (instant, [best.instance_id])

            # Instant-speed removal — only use proactively if the target is
            # genuinely threatening, not just because opponent cast a spell.
            # Don't waste removal as a "response" to an unrelated creature spell
            # when the real target (the stack spell) can't be hit by removal.
            if "removal" in tags and pick_removal_target_fn:
                opponent = game.players[1 - self.player_idx]
                # Only fire removal reactively if the stack spell is NOT a creature
                # (if it IS a creature, save removal for after it resolves)
                stack_is_creature = stack_item.source.template.is_creature
                if opponent.creatures and not stack_is_creature:
                    target = pick_removal_target_fn(
                        instant, opponent.creatures, opponent,
                        game, 1 - self.player_idx)
                    if target:
                        from ai.evaluator import estimate_removal_value
                        val = estimate_removal_value(
                            target, instant.template.cmc,
                            opponent, game, 1 - self.player_idx)
                        # Threshold sourced from ai/scoring_constants.py
                        # (PROACTIVE_REMOVAL_MIN_VALUE) — the "worth a
                        # card" floor on estimate_removal_value below
                        # which we hold removal for a higher-EV target.
                        if val >= PROACTIVE_REMOVAL_MIN_VALUE:
                            return (instant, [target.instance_id])

        if self.strategic_logger:
            self.strategic_logger.log_no_response(
                self.player_idx, stack_item.source.name, game,
                f"Threat value {threat:.1f} not worth responding to, or no suitable response")
        return None

    def _choose_response_targets(self, game: "GameState", instant: "CardInstance", stack_item: "StackItem",
                                  pick_removal_target_fn: Optional[Callable] = None) -> List[int]:
        """Choose targets for a response spell."""
        tags = instant.template.tags
        if "counterspell" in tags:
            return [stack_item.source.instance_id]
        if "blink" in tags:
            me = game.players[self.player_idx]
            if hasattr(stack_item, 'targets') and stack_item.targets:
                my_creature_ids = {c.instance_id for c in me.creatures}
                targeted_own = [tid for tid in stack_item.targets if tid in my_creature_ids]
                if targeted_own:
                    return targeted_own[:1]
            from ai.evaluator import estimate_permanent_value
            etb_creatures = [c for c in me.creatures if "etb_value" in c.template.tags]
            if etb_creatures:
                best = max(etb_creatures,
                           key=lambda c: estimate_permanent_value(c, me, game, self.player_idx))
                return [best.instance_id]
            if me.creatures:
                best = max(me.creatures,
                           key=lambda c: estimate_permanent_value(c, me, game, self.player_idx))
                return [best.instance_id]
            return []
        if "removal" in tags and pick_removal_target_fn:
            opponent = game.players[1 - self.player_idx]
            if opponent.creatures:
                target = pick_removal_target_fn(
                    instant, opponent.creatures, opponent,
                    game, 1 - self.player_idx)
                if target:
                    return [target.instance_id]
            return []
        return []

    def _held_counter_floor_ev(self, game: "GameState") -> float:
        """EV of holding a counterspell (the floor below which firing is wasteful).

        A counter in hand is worth roughly "one card of impact" — exactly
        what `card_clock_impact` already quantifies (avg power / opp life,
        gated by castable mana). Multiply by CLOCK_IMPACT_LIFE_SCALING
        (the same factor used throughout `evaluate_stack_threat`) to
        convert clock-impact units into life-point units, so the floor
        is directly comparable to the threat values produced there.

        Floored at HELD_COUNTER_FLOOR_MIN_EV so an empty snapshot (no
        mana, no opp life pressure) doesn't collapse the gate to zero
        — the counter still costs at least a card.

        Used to derive the gate thresholds in `decide_response` instead
        of the legacy hardcoded 1.5 / 3.0 magic numbers (P0-A).
        """
        from ai.clock import card_clock_impact
        from ai.ev_evaluator import snapshot_from_game
        snap = snapshot_from_game(game, self.player_idx)
        # Floor and scaling sourced from ai/scoring_constants.py
        # (HELD_COUNTER_FLOOR_MIN_EV / CLOCK_IMPACT_LIFE_SCALING).
        return max(HELD_COUNTER_FLOOR_MIN_EV,
                   card_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING)

    def _effective_paid_cost(self, game: "GameState",
                             stack_item: "StackItem") -> int:
        """Mana the controller actually paid for a stack spell, after
        cost-reducer mechanics (affinity, delve, domain, generic
        cost-reducers) and X-cost surplus.

        Mirrors the cost computation in `engine.cast_manager.can_cast`
        without re-running it: subtracts cost-reducer effects from the
        printed CMC and adds the X paid for X-cost spells. Used to
        decide whether a counter qualifies as "cheap" in the LOW gate
        — the original code only handled the X-cost case (line ~573)
        but missed the cost-reducer case which is the larger source
        of paid-vs-printed divergence (Affinity, Tron, etc.).

        Returns the printed CMC + X if no cost-reducer applies.
        """
        from engine.cards import CardType, Keyword
        source = stack_item.source
        template = source.template
        controller = stack_item.controller
        player = game.players[controller]

        printed = template.cmc or 0
        # X surplus: the X actually paid is tracked on the stack item.
        x_paid = getattr(stack_item, 'x_value', 0) or 0
        effective = printed + x_paid

        # Affinity for artifacts: -1 per artifact controller has.
        if Keyword.AFFINITY in template.keywords:
            artifact_count = sum(
                1 for c in player.battlefield
                if CardType.ARTIFACT in c.template.card_types
            )
            effective = max(0, effective - artifact_count)

        # Delve: -1 per card exiled from graveyard, capped by generic
        # portion of the cost (mirrors cast_manager.can_cast).
        if getattr(template, 'has_delve', False):
            gy_count = len(player.graveyard)
            colored_cost = (template.mana_cost.white
                            + template.mana_cost.blue
                            + template.mana_cost.black
                            + template.mana_cost.red
                            + template.mana_cost.green)
            generic_portion = max(0, effective - colored_cost)
            delve_reduction = min(gy_count, generic_portion)
            effective = max(colored_cost, effective - delve_reduction)

        # Domain: -N per basic land type controlled.
        if getattr(template, 'domain_reduction', 0) > 0:
            domain = game._count_domain(controller)
            effective = max(
                0, effective - template.domain_reduction * domain)

        # Generic cost reducers (Medallions, Goblin Electromancer, etc.)
        from engine.oracle_resolver import count_cost_reducers
        generic_reduction = count_cost_reducers(game, controller, template)
        if generic_reduction > 0:
            effective = max(0, effective - generic_reduction)

        return effective

    def _effective_counter_cost(self, game: "GameState", instant: "CardInstance") -> int:
        """Cost paid to actually fire this counter, after alternative-cost paths.

        Mirrors the engine's `can_cast` alternative-cost path for "exile a
        {color} card from your hand rather than pay this spell's mana cost"
        (game_state.py:880-903): on the opponent's turn, the counter is free
        in mana — its cost is a single exiled card, which we represent as 1
        for ranking purposes. Otherwise the cost is the printed CMC.

        Used to pick the cheapest castable counter when several are available;
        without this the legacy hand-order iteration would burn the wrong one.
        """
        oracle = (instant.template.oracle_text or '').lower()
        is_pitch_counter = (
            'exile a' in oracle and 'rather than pay' in oracle
            and getattr(game, 'active_player', None) != self.player_idx
        )
        if is_pitch_counter:
            return 1  # one exiled card, no mana
        return instant.template.cmc

    def _has_post_resolution_creature_answer(
        self, game: "GameState", stack_item: "StackItem",
    ) -> bool:
        """True iff a flash/instant creature-removal in our hand could
        kill the stack creature once it resolves onto the battlefield.

        Triage rule: when a counterspell candidate and a flash/instant
        creature-removal both target the same creature spell, the counter
        is the strictly more flexible card (counter answers any spell;
        creature-removal only answers creatures).  Reserve the counter
        for non-creature threats — let the creature spell resolve and
        die to the post-resolution answer.

        Class size (>10 cards): Solitude, Subtlety-style flashable
        creature-exile (any creature with `removal` tag and Flash),
        Path to Exile, Swords to Plowshares, Lightning Bolt-style burn
        (when target's toughness ≤ damage), March of Otherworldly Light,
        Generous Visitor, Otawara channel, Skyclave Apparition (flash
        equipped), Fatal Push, Cut Down — every flash creature-removal.

        Looks at full hand (not just `can_cast`-filtered instants):
        evoke-pitch creatures gate `can_cast` on `should_evoke` callback
        decisions, which would mask their availability here.  The triage
        check is about CAPACITY — does the hand contain a removal card
        that could answer this creature post-resolution if the AI chose
        to fire it.  Whether to actually fire it is decided downstream.

        Filter by what can ACTUALLY kill the threat:
        - Burn removal: damage must reach toughness.
        - Exile/destroy creature removal: any creature target works,
          modulo standard targeting restrictions.
        """
        src = stack_item.source
        tmpl = src.template
        if not tmpl.is_creature:
            return False

        from decks.card_knowledge_loader import get_burn_damage

        target_toughness = tmpl.toughness or 0
        # We use printed toughness as the resolved value — affinity /
        # cost-reduction effects don't change body stats, only mana cost.

        player = game.players[self.player_idx]
        for inst in player.hand:
            i_tmpl = inst.template
            i_tags = i_tmpl.tags
            if "removal" not in i_tags:
                continue
            if "counterspell" in i_tags:
                continue  # not a post-resolution answer
            # Must be flash-speed answer (instant, has-flash, or evoke-
            # pitch flash creature — Solitude / Subtlety-style).
            is_flash_speed = (
                i_tmpl.is_instant or i_tmpl.has_flash
                or "instant_speed" in i_tags
            )
            if not is_flash_speed:
                continue

            # Burn removal: filter by lethality on the target's toughness.
            burn = get_burn_damage(i_tmpl.name)
            if burn > 0:
                if burn < target_toughness:
                    continue
                return True

            # Exile / destroy / bounce target creature: standard creature
            # removal — assume it can answer any creature target.  Tags
            # `destroy_target_creature` and `removal` (without `burn`) cover
            # Path to Exile / Solitude / Fatal Push / Swords to Plowshares.
            o = (i_tmpl.oracle_text or '').lower()
            # Skip removal that targets only our own creatures.
            if 'target creature you control' in o and 'opponent' not in o:
                continue
            return True

        return False

    def evaluate_stack_threat(self, game: "GameState", stack_item: "StackItem") -> float:
        """Evaluate how threatening a stack item is using clock impact.

        Threat = how much this spell worsens our position if it resolves.
        Derived from game mechanics, not arbitrary weights.
        """
        from ai.ev_evaluator import EVSnapshot, snapshot_from_game, evaluate_board
        from ai.ev_evaluator import _project_spell
        from decks.card_knowledge_loader import get_threat_value, get_burn_damage

        source = stack_item.source
        template = source.template
        opp_idx = 1 - self.player_idx

        # Use projection: what does the board look like if this spell resolves?
        # Score from OPPONENT's perspective (their spell improving their position)
        snap = snapshot_from_game(game, opp_idx)
        archetype = getattr(self, 'opp_archetype', 'midrange')
        current = evaluate_board(snap, archetype)
        projected = _project_spell(source, snap, None, game, opp_idx)
        after = evaluate_board(projected, archetype)
        threat = after - current  # positive = opponent's position improved

        # Card knowledge can override if it's higher
        known_threat = get_threat_value(source.name)
        if known_threat > 0:
            threat = max(threat, known_threat)

        # Incoming creature spell: route through creature_threat_value()
        # so the resolved body is scored by oracle + clock semantics
        # (battle cry, "for each X" scalers, ETB tags) rather than by
        # the printed mana value scaled through mana_clock_impact. Fixes
        # mis-evaluation of cost-reduced threats — Sojourner's Companion
        # at printed CMC 7 paid 0 via affinity reads as a 4/4 body, not
        # as "expensive low value-density spell". P0-A.
        # Mirrors the board-wipe summing pattern just above; both lift
        # the threat by the actual creature_threat_value rather than
        # leaving the projection alone.
        if template.is_creature:
            from ai.ev_evaluator import creature_threat_value
            threat = max(threat, creature_threat_value(source, snap))

        # Lethal burn: sentinel-level threat. LETHAL_THREAT is a rules
        # constant — any spell that kills us is worth countering above all
        # else, so we pin it at the top of the threat scale.  Sourced
        # from ai/scoring_constants.py (LETHAL_THREAT).
        known_burn = get_burn_damage(source.name)
        if known_burn > 0:
            my_life = game.players[self.player_idx].life
            if my_life <= known_burn:
                return LETHAL_THREAT

        # Board wipes: scale with the sum of our creatures' threat values
        # (oracle-driven, via creature_threat_value). Replaces `count * 2`
        # with the actual value we lose — e.g. a wrath that kills a
        # battle-cry amplifier is worth more to counter than one killing
        # vanilla bodies.
        me = game.players[self.player_idx]
        if "board_wipe" in template.tags and len(me.creatures) >= 2:
            from ai.ev_evaluator import creature_threat_value
            threat += sum(creature_threat_value(c, snap) for c in me.creatures)

        # Cascade / reanimate: the actual threat is the creature or spell
        # they cheat into play. Approximate as card_clock_impact × a few
        # turns — both mechanics replace a card with a bigger one, so
        # value ≈ mana we'd save × mana_clock_impact.
        from ai.clock import card_clock_impact, mana_clock_impact
        snap_for_clock = snap  # already built above at opp_idx
        oracle = (template.oracle_text or '').lower()
        subtypes = getattr(template, 'subtypes', [])
        opp_player = game.players[opp_idx]

        if 'cascade' in getattr(template, 'tags', set()):
            # Cascade ~= 1 free spell at cmc - 1 mana. Value = saved mana
            # + expected threat value of what they cast.
            saved_mana = max(1, (template.cmc or 2) - 1)
            threat += saved_mana * mana_clock_impact(snap_for_clock) * CLOCK_IMPACT_LIFE_SCALING
        if 'reanimate' in getattr(template, 'tags', set()):
            # Reanimate value = threat value of the biggest creature in
            # opp graveyard (oracle-driven, not hardcoded).
            from engine.cards import CardType
            gy_creatures = [c for c in opp_player.graveyard
                             if CardType.CREATURE in c.template.card_types]
            if gy_creatures:
                from ai.ev_evaluator import creature_threat_value
                threat += max(creature_threat_value(c, snap) for c in gy_creatures)

        # Equipment: ongoing damage amplifier. Value = damage added to
        # the creature it equips × expected turns the equipment sticks.
        # Derive from oracle: flat +P/+T bonuses and "for each" scalers.
        # Residency window + default pump sourced from
        # ai/scoring_constants.py (EQUIPMENT_RESIDENCY_TURNS /
        # EQUIPMENT_DEFAULT_POWER_BONUS).
        import re
        if ('Equipment' in subtypes
                or 'equipment' in getattr(template, 'tags', set())):
            # Base equipment: approximate default power bonus on a
            # creature over the residency window.  Uses mana_clock_impact
            # × effective power.
            power_bonus = EQUIPMENT_DEFAULT_POWER_BONUS
            m = re.search(r'\+(\d+)/\+\d+', oracle)
            if m:
                power_bonus = int(m.group(1))
            # Scaling equipment (Cranial Plating / Nettlecyst): count
            # matching permanents for a truer virtual-power estimate.
            if 'for each artifact' in oracle:
                # Scaler grows with opponent's (caster's) artifact board.
                from engine.cards import CardType as _CT
                power_bonus += sum(1 for c in opp_player.battlefield
                                    if _CT.ARTIFACT in c.template.card_types)
            threat += (power_bonus * EQUIPMENT_RESIDENCY_TURNS
                       * mana_clock_impact(snap_for_clock) * CLOCK_IMPACT_LIFE_SCALING)

        # Carrier-pool synergy (R1). When the incoming spell is a creature,
        # check opp's battlefield for equipment whose pump rebinds onto
        # any creature each turn. Adding a NEW carrier to the pool means
        # the pump can land on a fresh attacker — the equipment's damage
        # over EQUIPMENT_RESIDENCY_TURNS multiplies across more bodies before
        # any single blocker can trade them all away.
        #
        # Marginal value per equipment = pump / (current_carriers + 1):
        # going from 2 carriers to 3 means the equipment now has a 1/3
        # chance of swinging on this new body each turn. Sum over all
        # eligible equipment on opp's board. Oracle-driven — detects any
        # 'equipped creature gets +X' clause via the existing regex.
        if template.is_creature:
            from engine.cards import CardType as _CT
            opp_creatures = [
                c for c in opp_player.battlefield
                if _CT.CREATURE in c.template.card_types
            ]
            current_carriers = max(1, len(opp_creatures))
            for perm in opp_player.battlefield:
                p_oracle = (perm.template.oracle_text or '').lower()
                if 'equip' not in p_oracle:
                    continue
                m = re.search(
                    r'equipped creature gets \+(\d+)/\+\d+', p_oracle
                )
                if not m:
                    continue
                pump = int(m.group(1))
                # 'for each artifact' scaler — read opp's artifact board.
                if 'for each artifact' in p_oracle:
                    pump += sum(
                        1 for c in opp_player.battlefield
                        if _CT.ARTIFACT in c.template.card_types
                    )
                marginal = pump / (current_carriers + 1)
                threat += (marginal * EQUIPMENT_RESIDENCY_TURNS
                           * mana_clock_impact(snap_for_clock) * CLOCK_IMPACT_LIFE_SCALING)

        # X-cost / 'for each' creatures (R1). Walking Ballista enters with
        # X +1/+1 counters where X is the mana paid; its printed P/T is
        # 0/0. Project an expected X from opp's available mana so the
        # threat reflects what the spell will actually be. Detect either
        # the explicit `{X}` mana symbol or the 'X +1/+1 counter' / 'for
        # each' patterns embedded in oracle text.
        x_scaler = ('{x}' in oracle
                    or 'x +1/+1 counter' in oracle
                    or 'x +1/+1 counters' in oracle
                    or 'for each' in oracle)
        if template.is_creature and x_scaler:
            # Expected X = opp's available mana minus the fixed portion of
            # the cost. cmc==0 for {X}-only cards (Ballista); for cards
            # like Hangarback Walker (cmc=2 + {X}) this still leaves a
            # reasonable surplus estimate. Note: `snap` was taken from
            # opp_idx's perspective, so `snap.my_mana` == opp's mana.
            fixed_cost = template.cmc or 0
            expected_x = max(0, snap_for_clock.my_mana - fixed_cost)
            if 'x +1/+1 counter' in oracle or 'x +1/+1 counters' in oracle:
                # Each counter = +1 power on the body. Treat the projected
                # body as a creature attacking over EQUIPMENT_RESIDENCY_TURNS
                # (same residency primitive used elsewhere in this fn).
                threat += (expected_x * EQUIPMENT_RESIDENCY_TURNS
                           * mana_clock_impact(snap_for_clock) * CLOCK_IMPACT_LIFE_SCALING)
            elif 'for each' in oracle:
                # Generic 'for each X' creature scaler — count opp's
                # matching permanents and credit one power per match.
                fe = re.search(
                    r'for each (artifact|creature|land|card)', oracle
                )
                if fe:
                    kind = fe.group(1)
                    from engine.cards import CardType as _CT2
                    if kind == 'artifact':
                        n = sum(
                            1 for c in opp_player.battlefield
                            if _CT2.ARTIFACT in c.template.card_types
                        )
                    elif kind == 'creature':
                        n = len(opp_player.creatures)
                    elif kind == 'land':
                        n = sum(
                            1 for c in opp_player.battlefield
                            if c.template.is_land
                        )
                    else:
                        n = len(opp_player.battlefield)
                    threat += (n * EQUIPMENT_RESIDENCY_TURNS
                               * mana_clock_impact(snap_for_clock) * CLOCK_IMPACT_LIFE_SCALING)

        # Cost reducers: enable combos. Value = mana saved per spell ×
        # spells_per_turn × turns_remaining. Use card_clock_impact as a
        # proxy for "card advantage via mana savings".
        if getattr(template, 'is_cost_reducer', False):
            threat += card_clock_impact(snap_for_clock) * CLOCK_IMPACT_LIFE_SCALING

        # Token generators / engines: value = ongoing bodies over time.
        # card_clock_impact already expresses "future card as clock change",
        # so one trigger per turn over a few turns.
        if 'whenever' in oracle and ('create' in oracle or 'token' in oracle):
            threat += card_clock_impact(snap_for_clock) * CLOCK_IMPACT_LIFE_SCALING

        # Card advantage engines (Thought Monitor draws 2): value = one
        # extra card — already what card_clock_impact computes.
        if 'card_advantage' in getattr(template, 'tags', set()):
            threat += card_clock_impact(snap_for_clock) * CLOCK_IMPACT_LIFE_SCALING

        return max(0, threat)
