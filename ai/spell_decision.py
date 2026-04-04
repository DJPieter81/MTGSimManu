"""Reactive spell selection — concerns-based decision making.

Replaces the scoring-based _choose_spell with a priority queue of concerns.
Each turn, the AI asks:

  1. SURVIVE: Am I dying? → Find the play that keeps me alive
  2. ANSWER:  Is there a must-answer threat? → Remove it
  3. ADVANCE: Can I progress my game plan safely? → Do it
  4. EFFICIENT: Nothing urgent → make the best use of my mana

Within each concern, card selection is contextual:
  - "Which removal spell kills their best threat?"
  - "Which creature uses my mana best this turn?"
  - "Should I hold mana for instant-speed interaction?"

This is not a scoring function. It's a decision procedure.
No arbitrary constants. Cards are compared, not scored.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Tuple, Set
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState
    from ai.gameplan import Goal, GoalType, BoardAssessment, GoalEngine


@dataclass
class SpellDecision:
    """The result of the spell selection process."""
    card: Optional["CardInstance"]
    concern: str          # which concern drove the decision
    reasoning: str        # human-readable explanation
    alternatives: List[Tuple[str, str]]  # [(card_name, why_not), ...]


def choose_spell(engine: "GoalEngine", castable: List["CardInstance"],
                 game: "GameState", player_idx: int,
                 assessment: "BoardAssessment") -> SpellDecision:
    """Main entry point: decide which spell to cast this turn.

    Walks through concerns in priority order. Returns the first
    concern that produces a play, or a "pass" decision if nothing
    is worth doing.
    """
    # Build context once — all concerns share this
    ctx = _build_context(castable, game, player_idx, assessment, engine)

    # --- Pre-filter: remove cards that should never be cast proactively ---
    ctx.castable = _apply_pre_filters(ctx)

    # --- Emergency: re-include reactive_only cards when dying ---
    # Cards like Solitude (evoke) are normally held for instant-speed use,
    # but when we're about to die, they become survival plays.
    if ctx.am_dying:
        me = ctx.me
        for card in me.hand:
            if card not in ctx.castable and ctx.game.can_cast(ctx.player_idx, card):
                if card.name in ctx.engine.gameplan.reactive_only:
                    # Skip protection spells that need a creature target
                    if card.name in ('Undying Evil', 'Ephemerate') and not me.creatures:
                        continue
                    # Skip counterspells during main phase — they can't target
                    # anything proactively and waste mana
                    tags = getattr(card.template, 'tags', set())
                    if 'counterspell' in tags:
                        continue
                    ctx.castable.append(card)
                    # Also add to removal/threats lists as appropriate
                    if 'removal' in tags or 'board_wipe' in tags:
                        ctx.my_removal.append(card)
                    if card.template.is_creature:
                        ctx.my_threats.append(card)

    # --- Cycling priority for Living End style decks ---
    cycling_result = _check_cycling_priority(ctx)
    if cycling_result:
        return cycling_result

    # --- Concern 1: SURVIVE ---
    if ctx.am_dying:
        result = _concern_survive(ctx)
        if result:
            return result

    # --- Concern 2: ANSWER ---
    if ctx.must_answer_threats:
        result = _concern_answer(ctx)
        if result:
            return result

    # --- Concern 3: ADVANCE ---
    result = _concern_advance(ctx)
    if result:
        return result

    # --- Concern 4: EFFICIENT ---
    result = _concern_efficient(ctx)
    if result:
        return result

    # --- Nothing worth doing ---
    return SpellDecision(
        card=None,
        concern="pass",
        reasoning=_pass_reasoning(ctx),
        alternatives=[]
    )


# ---------------------------------------------------------------------------
# Context: gathered once, used by all concerns
# ---------------------------------------------------------------------------

@dataclass
class _DecisionContext:
    """All the information a concern needs to make a decision."""
    castable: List["CardInstance"]
    game: "GameState"
    player_idx: int
    assessment: "BoardAssessment"
    engine: "GoalEngine"
    goal: "Goal"
    me: object  # PlayerState
    opp: object  # PlayerState

    # Derived
    am_dying: bool
    must_answer_threats: List["CardInstance"]  # opponent creatures that are dangerous
    my_removal: List["CardInstance"]
    my_threats: List["CardInstance"]       # creatures + planeswalkers
    my_interaction: List["CardInstance"]   # instants/counterspells
    my_card_draw: List["CardInstance"]
    my_acceleration: List["CardInstance"]  # rituals, ramp
    my_other: List["CardInstance"]
    opp_mana_up: int
    opp_likely_has_interaction: bool
    holding_mana_is_valuable: bool
    
    # Combo state
    storm_hold_rituals: bool = False  # combo not ready, only cast cantrips
    
    # Role/strategy
    role_cache: object = None
    turning_corner: bool = False
    archetype: str = "midrange"


def _build_context(castable, game, player_idx, assessment, engine):
    """Build the decision context from game state."""
    from engine.cards import CardType
    me = game.players[player_idx]
    opp = game.players[1 - player_idx]
    goal = engine.current_goal

    # Categorize cards by what they DO (not by name)
    removal = []
    threats = []
    interaction = []
    card_draw = []
    acceleration = []
    other = []

    for card in castable:
        if not game.can_cast(player_idx, card):
            continue
        t = card.template
        tags = getattr(t, 'tags', set())
        categorized = False

        if 'removal' in tags or 'board_wipe' in tags:
            removal.append(card)
            categorized = True
        if 'counterspell' in tags:
            interaction.append(card)
            categorized = True
            continue  # counterspells are never proactive
        if t.is_creature or CardType.PLANESWALKER in getattr(t, 'card_types', []):
            threats.append(card)
            categorized = True
        if 'draw' in tags or 'cantrip' in tags:
            card_draw.append(card)
            categorized = True
        if 'ritual' in tags or 'mana_source' in tags:
            acceleration.append(card)
            categorized = True

        if not categorized:
            # Check burn spells (both removal and threat)
            oracle = (t.oracle_text or "").lower()
            if 'damage' in oracle and ('target' in oracle or 'any' in oracle):
                if card not in removal:
                    removal.append(card)
                if card not in threats:
                    threats.append(card)
            elif t.is_instant:
                interaction.append(card)
            else:
                other.append(card)

    # Am I dying? (opponent can kill me next turn)
    # Matches the old survival trigger: dead next turn OR under serious pressure
    # (fast clock with meaningful board power)
    am_dying = assessment.am_dead_next or (
        assessment.opp_clock <= 4 and assessment.opp_board_power >= 3
    )

    # Must-answer threats: opponent creatures that are winning the game
    # Threshold depends on pressure: when under pressure, more things need answering
    must_answer = []
    if opp.creatures:
        from ai.evaluator import _permanent_value
        # Under pressure: any creature with meaningful power is a must-answer
        # Not under pressure: only high-value engines/threats
        under_pressure = assessment.opp_clock <= 4 or assessment.am_dead_next
        val_threshold = 3.0 if under_pressure else 5.0
        for c in opp.creatures:
            val = _permanent_value(c, opp, game, 1 - player_idx)
            power = c.power if hasattr(c, 'power') and c.power else (c.template.power or 0)
            toughness = c.toughness if hasattr(c, 'toughness') and c.toughness else (c.template.toughness or 0)
            # Must answer if:
            # - High-value threat (engine, card advantage)
            # - Under pressure and creature has meaningful power (3+)
            # - Opponent has a very fast clock (2 turns or less)
            if val >= val_threshold or assessment.opp_clock <= 2:
                must_answer.append(c)
            elif under_pressure and power >= 3:
                must_answer.append(c)
            # Aggro: also answer blockers that prevent significant attack damage
            elif engine.gameplan.archetype == 'aggro' and me.creatures:
                my_blocked_power = sum(
                    (a.power or a.template.power or 0)
                    for a in me.creatures
                    if (a.power or a.template.power or 0) <= toughness
                )
                if my_blocked_power >= 3:
                    must_answer.append(c)  # Blocker prevents significant damage

    # Opponent interaction likelihood
    opp_mana = opp.available_mana_estimate
    opp_has_interaction = opp_mana >= 1 and len(opp.hand) >= 1

    # Is holding mana valuable? (do I have instants worth holding?)
    has_instants_in_hand = any(
        c.template.is_instant for c in me.hand
        if c not in castable  # instants not in the castable list
    )
    has_instant_castable = any(c.template.is_instant for c in castable)
    holding_valuable = has_instants_in_hand or has_instant_castable

    # During EXECUTE_PAYOFF: everything is chain fuel or payoff.
    # Clear fair-deck categorizations so only SURVIVE and ADVANCE fire.
    # This is simpler than adding exceptions — removing data removes bugs.
    from ai.gameplan import GoalType
    is_combo_turn = goal.goal_type == GoalType.EXECUTE_PAYOFF
    if is_combo_turn:
        removal = []
        interaction = []
        must_answer = []
        holding_valuable = False

    # Include cards that can be cycled (cycling is a legal action, not casting)
    from engine.game_state import CYCLING_COSTS
    castable_final = [c for c in castable if game.can_cast(player_idx, c)]
    for card in castable:
        if card not in castable_final and card.name in CYCLING_COSTS:
            if game.can_cycle(player_idx, card):
                castable_final.append(card)

    return _DecisionContext(
        castable=castable_final,
        game=game, player_idx=player_idx,
        assessment=assessment, engine=engine, goal=goal,
        me=me, opp=opp,
        am_dying=am_dying,
        must_answer_threats=must_answer,
        my_removal=removal,
        my_threats=threats,
        my_interaction=interaction,
        my_card_draw=card_draw,
        my_acceleration=acceleration,
        my_other=other,
        opp_mana_up=opp_mana,
        opp_likely_has_interaction=opp_has_interaction,
        holding_mana_is_valuable=holding_valuable,
        storm_hold_rituals=False,
        role_cache=engine._role_cache,
        turning_corner=engine.turning_the_corner,
        archetype=engine.gameplan.archetype,
    )


# ---------------------------------------------------------------------------
# Pre-filters: game-knowledge gates that prevent illegal/wasteful plays
# ---------------------------------------------------------------------------

def _apply_pre_filters(ctx: _DecisionContext) -> List["CardInstance"]:
    """Remove cards that should never be cast proactively in main phase.
    
    These are game-knowledge checks, not scoring decisions.
    """
    from ai.gameplan import GoalType
    from engine.cards import Keyword

    filtered = []
    engine = ctx.engine
    goal = ctx.goal
    me = ctx.me
    opp = ctx.opp

    for card in ctx.castable:
        tags = getattr(card.template, 'tags', set())

        # Skip reactive-only cards (Solitude evoke, etc.)
        # EXCEPTION 1: during EXECUTE_PAYOFF, reactive_only cards are combo fuel
        # EXCEPTION 2: protection spells allowed with EOT exile (Goryo's)
        if card.name in engine.gameplan.reactive_only:
            from ai.gameplan import GoalType
            if goal.goal_type == GoalType.EXECUTE_PAYOFF:
                pass  # Allow during combo execution
            else:
                has_eot_exile = bool(getattr(ctx.game, '_end_of_turn_exiles', []))
                is_protection = card.name in goal.card_roles.get('protection', set())
                if not (has_eot_exile and is_protection and me.creatures):
                    continue

        # Skip counterspells during main phase
        if 'counterspell' in tags:
            continue

        # Skip pump spells with no creatures to target
        if 'pump' in tags and not card.template.is_creature:
            if not me.creatures:
                continue

        # Skip reanimation with no valid target in graveyard
        if 'reanimate' in tags:
            gy_creatures = [c for c in me.graveyard if c.template.is_creature]
            if not gy_creatures:
                continue
            if 'targets_legendary' in tags:
                from engine.cards import Supertype
                if not any(Supertype.LEGENDARY in getattr(c.template, 'supertypes', [])
                           for c in gy_creatures):
                    continue
            # Smart reanimation gating: if the deck has high-CMC reanimation
            # targets (Griselbrand, Atraxa), don't waste reanimation spells
            # on low-value creatures (Solitude, etc.) unless we're desperate
            if goal.resource_min_cmc > 0:
                best_gy_cmc = max((c.template.cmc or 0) for c in gy_creatures)
                if best_gy_cmc < goal.resource_min_cmc and not ctx.am_dying:
                    continue  # hold reanimation for a worthy target

        # Storm gating: the chain simulator handles optimal sequencing,
        # but the pre-filter still prevents obviously bad plays.
        # During EXECUTE_PAYOFF, let the chain simulator decide ordering.
        # Outside EXECUTE_PAYOFF, block storm spells entirely (they're combo pieces).
        has_storm = Keyword.STORM in getattr(card.template, 'keywords', set())
        if has_storm:
            from ai.gameplan import GoalType as _GT
            storm = getattr(ctx.game, '_global_storm_count', 0)
            opp_life = opp.life
            
            # Lethal: always fire
            if storm + 1 >= opp_life:
                pass
            # During combo execution: let chain simulator decide
            elif ctx.goal.goal_type == _GT.EXECUTE_PAYOFF:
                pass  # chain simulator will sequence this correctly
            # Outside combo turn: don't waste storm finishers
            else:
                continue

        # Removal hold: don't fire removal at empty board (unless lethal burn)
        if 'removal' in tags and not card.template.is_creature:
            if not opp.creatures and not opp.battlefield:
                damage = engine._estimate_face_damage(card)
                if damage > 0 and ctx.assessment.my_clock <= 4:
                    pass  # aggro, allow face burn
                elif damage > 0 and opp.life <= damage + ctx.assessment.my_board_power:
                    pass  # close to lethal
                else:
                    continue  # hold removal

        # Silence spells: only cast to protect a key play
        if 'silence' in tags:
            total_mana = me.available_mana_estimate + me.mana_pool.total()
            chant_cost = card.template.cmc or 1
            mana_after = total_mana - chant_cost
            can_protect = any(
                ('threat' in getattr(c.template, 'tags', set()) or (c.template.cmc or 0) >= 3)
                and (c.template.cmc or 0) <= mana_after
                for c in me.hand if c != card and not c.template.is_land
            )
            has_key_on_board = any(
                'threat' in getattr(c.template, 'tags', set()) or (c.template.power or 0) >= 4
                for c in me.creatures
            )
            opp_can_respond = opp.available_mana_estimate >= 1
            if not ((can_protect or has_key_on_board) and opp_can_respond):
                continue  # don't waste silence

        # Spells that target a creature you control: skip if no creatures
        if 'targets_own_creature' in tags or card.name in ('Undying Evil', 'Ephemerate'):
            if not me.creatures:
                continue

        # Legend rule: don't play legends we already control
        if engine._would_violate_legend_rule(card, me):
            continue

        # Combo hold: if not ready to combo, only allow cantrips/draw
        if ctx.storm_hold_rituals:
            is_ritual = 'ritual' in tags or 'mana_source' in tags
            is_creature = card.template.is_creature
            if is_ritual or is_creature:
                continue  # hold for combo turn

        filtered.append(card)

    return filtered


# ---------------------------------------------------------------------------
# Cycling priority (Living End style)
# ---------------------------------------------------------------------------

def _check_cycling_priority(ctx: _DecisionContext) -> Optional[SpellDecision]:
    """For decks that prefer cycling, check if we should cycle or cast payoff."""
    goal = ctx.goal
    if not goal.prefer_cycling:
        return None

    from engine.game_state import CYCLING_COSTS
    from engine.cards import Keyword

    # Check if any payoff is castable right now
    payoff_cards = goal.card_roles.get("payoffs", set())
    for card in ctx.castable:
        if card.name not in payoff_cards:
            continue
        # For FILL_RESOURCE, check resource target
        from ai.gameplan import GoalType
        if goal.goal_type == GoalType.FILL_RESOURCE:
            if goal.resource_zone == "graveyard":
                gy_creatures = sum(1 for c in ctx.me.graveyard if c.template.is_creature)
                if gy_creatures < goal.resource_target:
                    continue
        # Storm gate for storm payoffs
        has_storm = Keyword.STORM in getattr(card.template, 'keywords', set())
        if has_storm:
            storm = getattr(ctx.game, '_global_storm_count', 0)
            if storm + 1 < 2:
                continue
        return SpellDecision(
            card=card, concern="advance",
            reasoning=f"Payoff ready — casting {card.name} (goal: {goal.description})",
            alternatives=[]
        )

    # No payoff ready — cycle creatures into graveyard
    cyclable = []
    for card in ctx.castable:
        if card.name in CYCLING_COSTS and ctx.game.can_cycle(ctx.player_idx, card):
            # Prefer cycling creatures (they go to GY for Living End)
            is_creature = card.template.is_creature
            # Prefer cards named in goal roles
            is_role_card = any(card.name in cards for cards in goal.card_roles.values())
            cyclable.append((card, is_creature, is_role_card))

    if cyclable:
        # Sort: creatures first, then role cards, then others
        cyclable.sort(key=lambda x: (x[1], x[2]), reverse=True)
        best = cyclable[0][0]
        reason = f"Cycling {best.name}"
        if cyclable[0][1]:
            reason += " (creature → graveyard for reanimation)"
        return SpellDecision(
            card=best, concern="advance",
            reasoning=reason,
            alternatives=[(c.name, "also cyclable") for c, _, _ in cyclable[1:3]]
        )

    return None


# ---------------------------------------------------------------------------
# Concern 1: SURVIVE — Am I dying?
# ---------------------------------------------------------------------------

def _concern_survive(ctx: _DecisionContext) -> Optional[SpellDecision]:
    """Find a play that prevents death.

    Priority: removal of biggest attacker > blocker > life gain > anything.
    Combo pieces are excluded — they're too valuable to waste as emergency plays.
    """
    # Identify combo pieces from goal card_roles (engines, payoffs, enablers)
    # For non-combo archetypes (midrange, aggro, control), enablers ARE the
    # survival plays — Bowmasters is both an enabler and a blocker/removal.
    # Only actual combo decks should protect their pieces from emergency use.
    combo_pieces = set()
    is_combo = ctx.archetype == 'combo'
    if is_combo:
        for goal in ctx.engine.gameplan.goals:
            for role_name in ('payoffs', 'engines', 'enablers'):
                combo_pieces.update(goal.card_roles.get(role_name, set()))

    # Filter out combo pieces from survival candidates
    safe_removal = [c for c in ctx.my_removal if c.name not in combo_pieces]
    safe_threats = [c for c in ctx.my_threats if c.name not in combo_pieces]
    safe_castable = [c for c in ctx.castable if c.name not in combo_pieces]

    # Best removal for their biggest creature
    removal = _best_removal_for_threats(safe_removal, ctx.opp.creatures, ctx)

    # Best blocker: creature with highest toughness
    blockers = [c for c in safe_threats
                if c.template.is_creature and (c.template.toughness or 0) >= 2]
    blockers.sort(key=lambda c: c.template.toughness or 0, reverse=True)

    if removal and blockers:
        r_card, r_target, r_reason = removal
        b_card = blockers[0]
        # Removal permanently removes the threat — usually better
        return SpellDecision(
            card=r_card, concern="survive",
            reasoning=f"Dying — removing {r_target} with {r_card.name} ({r_reason})",
            alternatives=[(b_card.name, "could block instead")]
        )
    elif removal:
        r_card, r_target, r_reason = removal
        return SpellDecision(
            card=r_card, concern="survive",
            reasoning=f"Dying — removing {r_target} with {r_card.name} ({r_reason})",
            alternatives=[]
        )
    elif blockers:
        return SpellDecision(
            card=blockers[0], concern="survive",
            reasoning=f"Dying — deploying {blockers[0].name} as blocker (toughness {blockers[0].template.toughness})",
            alternatives=[]
        )

    # Anything castable is better than nothing when dying
    if safe_castable:
        best = _most_mana_efficient(safe_castable, ctx)
        if best:
            return SpellDecision(
                card=best, concern="survive",
                reasoning=f"Dying — playing {best.name} as best available option",
                alternatives=[]
            )

    return None


# ---------------------------------------------------------------------------
# Concern 2: ANSWER — Must-answer threats
# ---------------------------------------------------------------------------

def _concern_answer(ctx: _DecisionContext) -> Optional[SpellDecision]:
    """Remove a threat that's winning the game for the opponent."""
    if not ctx.my_removal:
        return None

    removal = _best_removal_for_threats(ctx.my_removal, ctx.must_answer_threats, ctx)
    if removal:
        r_card, r_target, r_reason = removal
        return SpellDecision(
            card=r_card, concern="answer",
            reasoning=f"Must answer {r_target} — {r_card.name} ({r_reason})",
            alternatives=[(c.name, "also removal but less efficient")
                          for c in ctx.my_removal if c != r_card][:2]
        )

    return None


# ---------------------------------------------------------------------------
# Concern 3: ADVANCE — Progress the game plan
# ---------------------------------------------------------------------------

def _concern_advance(ctx: _DecisionContext) -> Optional[SpellDecision]:
    """Find a play that advances the current goal.

    This is where archetype strategy matters:
    - Aggro: deploy the best threat on curve
    - Control: hold up interaction, deploy threats only when safe
    - Combo: sequence enablers toward the combo turn
    - Midrange: play the most impactful card for the board state
    """
    from ai.gameplan import GoalType

    goal = ctx.goal
    gt = goal.goal_type

    # --- Always-early cards: deploy immediately in early turns ---
    # Use turn_number <= 5 to account for going second (player's T2 = game T4)
    if ctx.assessment.turn_number <= 5:
        for card in ctx.castable:
            if card.name in ctx.engine.gameplan.always_early:
                return SpellDecision(
                    card=card, concern="advance",
                    reasoning=f"Early deployment — {card.name} is an always-early card (turn {ctx.assessment.turn_number})",
                    alternatives=[]
                )

    # --- Cards named in the goal's card_roles are always good ---
    # BUT: for EXECUTE_PAYOFF (combo turn), skip this generic path
    # and let _advance_combo handle proper sequencing (cost reducers
    # before rituals before cantrips before finishers).
    if gt != GoalType.EXECUTE_PAYOFF:
        role_cards = []
        for role_name, card_names in goal.card_roles.items():
            for card in ctx.castable:
                if card.name in card_names:
                    role_cards.append((card, role_name))

        if role_cards:
            best_card, best_role = _best_role_card(role_cards, ctx)
            return SpellDecision(
                card=best_card, concern="advance",
                reasoning=f"Advancing {goal.description} — {best_card.name} is a {best_role} for this goal",
                alternatives=[(c.name, f"{r} for goal") for c, r in role_cards if c != best_card][:2]
            )

    # --- Goal-type specific logic ---
    if gt in (GoalType.CURVE_OUT, GoalType.PUSH_DAMAGE, GoalType.CLOSE_GAME):
        return _advance_proactive(ctx)
    elif gt in (GoalType.INTERACT, GoalType.DISRUPT):
        return _advance_reactive(ctx)
    elif gt in (GoalType.RAMP, GoalType.DEPLOY_ENGINE, GoalType.FILL_RESOURCE):
        return _advance_setup(ctx)
    elif gt == GoalType.EXECUTE_PAYOFF:
        return _advance_combo(ctx)
    elif gt == GoalType.GRIND_VALUE:
        return _advance_grind(ctx)
    else:
        return _advance_proactive(ctx)  # default: deploy something


def _advance_proactive(ctx: _DecisionContext) -> Optional[SpellDecision]:
    """Aggro/midrange: deploy the best threat on curve.

    Considers prowess synergy: if we have prowess creatures on board,
    cheap noncreature spells may be better than deploying another creature.

    Mana reservation: if hand contains a high-priority card that's almost
    castable (within 1 mana), prefer to save mana or play a cheaper card
    instead of spending all mana on a lower-priority card.
    """
    # Should I hold mana for interaction instead?
    if _should_hold_for_interaction(ctx):
        return None  # let EFFICIENT concern handle or pass

    # Check prowess synergy: noncreature spells trigger prowess
    prowess_play = _check_prowess_play(ctx)
    if prowess_play:
        return prowess_play

    if ctx.my_threats:
        best = _best_on_curve(ctx.my_threats, ctx)
        best_cmc = best.template.cmc or 0
        available = ctx.assessment.my_mana

        reason = f"Deploying {best.name} on curve"
        if best_cmc == available:
            reason += " (uses all mana)"
        elif best_cmc < available and len(ctx.my_threats) > 1:
            reason += f" (leaves {available - best_cmc} mana open)"

        alternatives = [(c.name, f"cmc {c.template.cmc or 0}") for c in ctx.my_threats if c != best][:2]
        return SpellDecision(card=best, concern="advance", reasoning=reason,
                             alternatives=alternatives)

    # No threats — play whatever advances position
    if ctx.my_card_draw:
        best = ctx.my_card_draw[0]
        return SpellDecision(card=best, concern="advance",
                             reasoning=f"No threats available — drawing cards with {best.name}",
                             alternatives=[])

    return None


def _advance_reactive(ctx: _DecisionContext) -> Optional[SpellDecision]:
    """Control: prioritize interaction, deploy threats only when safe."""
    # If opponent has threats, use removal
    if ctx.my_removal and ctx.opp.creatures:
        removal = _best_removal_for_threats(ctx.my_removal, ctx.opp.creatures, ctx)
        if removal:
            r_card, r_target, r_reason = removal
            return SpellDecision(
                card=r_card, concern="advance",
                reasoning=f"Interactive goal — removing {r_target} with {r_card.name}",
                alternatives=[])

    # Discard spells for disruption
    discard_spells = [c for c in ctx.castable if 'discard' in getattr(c.template, 'tags', set())]
    if discard_spells:
        return SpellDecision(
            card=discard_spells[0], concern="advance",
            reasoning=f"Disrupting opponent's hand with {discard_spells[0].name}",
            alternatives=[])

    # Deploy threats when:
    # 1. Board is clear (safe to deploy)
    # 2. Under pressure and need blockers (survival deployment)
    # 3. Can deploy AND hold up interaction
    if ctx.my_threats:
        best = _best_on_curve(ctx.my_threats, ctx)
        available_after = ctx.assessment.my_mana - (best.template.cmc or 0)
        
        if not ctx.opp.creatures:
            # Board clear — deploy freely
            if available_after >= 2 or not ctx.holding_mana_is_valuable:
                return SpellDecision(
                    card=best, concern="advance",
                    reasoning=f"Board clear — deploying {best.name} while holding up {available_after} mana",
                    alternatives=[])
        elif ctx.am_dying and not ctx.me.creatures:
            # Under pressure with no blockers — must deploy something
            return SpellDecision(
                card=best, concern="advance",
                reasoning=f"Under pressure with no blockers — deploying {best.name}",
                alternatives=[])

    # Turning the corner: control has stabilized, deploy threats
    if ctx.turning_corner and ctx.my_threats:
        best = _best_on_curve(ctx.my_threats, ctx)
        return SpellDecision(
            card=best, concern="advance",
            reasoning=f"Turning the corner — deploying {best.name} to close the game",
            alternatives=[])

    # Draw cards to find answers
    if ctx.my_card_draw:
        return SpellDecision(
            card=ctx.my_card_draw[0], concern="advance",
            reasoning=f"Looking for answers — {ctx.my_card_draw[0].name}",
            alternatives=[])

    return None


def _advance_setup(ctx: _DecisionContext) -> Optional[SpellDecision]:
    """Combo setup: deploy enablers, accelerate toward combo turn.
    
    During setup, cost reducers (Ruby Medallion, Birgi) are the priority
    because they make the entire combo turn cheaper. Rituals should be
    HELD for the combo turn, not spent during setup.
    """
    tags_for = lambda c: getattr(c.template, 'tags', set())

    # Separate cost reducers (deploy now) from rituals (hold for combo turn)
    cost_reducers = [c for c in ctx.my_acceleration
                     if 'cost_reducer' in tags_for(c)
                     or (not c.template.is_instant and not c.template.is_sorcery)]
    
    # Deploy cost reducers during setup
    for cr in cost_reducers:
        already_deployed = any(
            bf.template.name == cr.template.name
            for bf in ctx.me.battlefield
        )
        if not already_deployed:
            return SpellDecision(
                card=cr, concern="advance",
                reasoning=f"Setting up — deploying {cr.name} to reduce costs for combo turn",
                alternatives=[])

    # Card draw to find pieces
    if ctx.my_card_draw:
        return SpellDecision(
            card=ctx.my_card_draw[0], concern="advance",
            reasoning=f"Setting up — digging for pieces with {ctx.my_card_draw[0].name}",
            alternatives=[])

    # Deploy engines/enablers from other category
    if ctx.my_other:
        return SpellDecision(
            card=ctx.my_other[0], concern="advance",
            reasoning=f"Setting up — deploying {ctx.my_other[0].name}",
            alternatives=[])

    # Deploy threats if nothing else to do during setup
    if ctx.my_threats:
        best = _best_on_curve(ctx.my_threats, ctx)
        return SpellDecision(
            card=best, concern="advance",
            reasoning=f"Setup phase but no enablers — deploying {best.name}",
            alternatives=[])

    return None


def _advance_combo(ctx: _DecisionContext) -> Optional[SpellDecision]:
    """Combo execution via abstracted readiness evaluation.

    Three-layer architecture (no card names in decision logic):
      1. GATHER FACTS — chain simulator + readiness evaluator
      2. DECIDE — go/wait/dig via abstracted readiness assessment
      3. EXECUTE — map abstract action to concrete card selection

    Design principle #7: No patch-fixing — all logic is general.
    Design principle #8: Arithmetic is separate from decisions.
    """
    from ai.combo_chain import find_all_chains, what_is_missing
    from ai.combo_readiness import evaluate_readiness, decide_go_or_wait, ComboAction
    # ═══ Layer 1: GATHER FACTS ═══
    medallion_count = sum(
        1 for bf in ctx.me.battlefield
        if 'cost_reducer' in getattr(bf.template, 'tags', set())
    )
    all_hand_spells = [c for c in ctx.me.hand if not c.template.is_land]
    castable_spells = [c for c in ctx.castable if not c.template.is_land]

    if not all_hand_spells:
        return None

    available_mana = ctx.assessment.my_mana
    payoff_names = set(ctx.goal.card_roles.get('payoffs', set()))
    if not payoff_names:
        from engine.cards import Keyword
        payoff_names = {
            c.name for c in all_hand_spells
            if Keyword.STORM in getattr(c.template, 'keywords', set())
        }

    # Include spells already cast this turn in storm count
    base_storm = getattr(ctx.game, '_global_storm_count', 0)
    chains = find_all_chains(all_hand_spells, available_mana,
                             medallion_count, payoff_names, base_storm)
    status = what_is_missing(all_hand_spells, available_mana,
                             medallion_count, payoff_names)

    # Classify chains by outcome (pure facts)
    best_lethal = None   # best chain that kills with direct damage
    best_tokens = None   # best chain that kills with tokens
    best_chain = None    # best chain overall (by storm count)
    for chain in chains:
        if chain.payoff_has_storm and chain.payoff_deals_damage \
                and chain.storm_damage >= ctx.opp.life:
            if not best_lethal or chain.storm_count > best_lethal.storm_count:
                best_lethal = chain
        elif chain.payoff_has_storm and not chain.payoff_deals_damage \
                and chain.storm_tokens >= ctx.opp.life:
            if not best_tokens or chain.storm_tokens > best_tokens.storm_tokens:
                best_tokens = chain
        if not best_chain or chain.storm_count > best_chain.storm_count:
            best_chain = chain

    # Build abstracted readiness (no card names — just numbers)
    readiness = evaluate_readiness(
        me=ctx.me, opp=ctx.opp, chains=chains, status=status,
        available_mana=available_mana, medallion_count=medallion_count,
        opp_clock=ctx.assessment.opp_clock, am_dead_next=ctx.am_dying,
        payoff_names=payoff_names,
    )

    # ═══ Layer 2: DECIDE (abstracted — no card names) ═══
    action = decide_go_or_wait(readiness)


    # ═══ Layer 3: EXECUTE — unified role-based sequencing ═══
    # All actions use the same sequencer. The sequencer's role ordering
    # naturally handles GO (enablers first, finisher last) and DIG
    # (enablers only, finisher held). No separate code paths needed.
    return _execute_combo_sequenced(ctx, castable_spells, available_mana,
                                    action.value)


# ─── Combo execution helpers (Layer 3) ───
# These map abstract actions to concrete card selections.
# Uses the spell_sequencer for role-based ordering.
# No card names — only roles derived from tags.


def _execute_combo_sequenced(
    ctx: _DecisionContext,
    castable_spells: list,
    available_mana: int,
    action_label: str,
) -> Optional[SpellDecision]:
    """Unified combo execution using role-based sequencing.

    The spell_sequencer assigns each card a role (REDUCER, FUEL, DRAW,
    TUTOR, REBUY, FINISHER) based on tags, then orders them so enablers
    come before finishers. This prevents firing Grapeshot at storm 1.

    Works for any combo deck — no card names in the logic.
    """
    from ai.spell_sequencer import next_spell_to_cast

    has_reducer = any(
        'cost_reducer' in getattr(bf.template, 'tags', set())
        for bf in ctx.me.battlefield
    )
    medallion_count = sum(
        1 for bf in ctx.me.battlefield
        if bf.template.name == "Ruby Medallion"
    )
    gy_spells = sum(1 for c in ctx.me.graveyard
                    if c.template.is_instant or c.template.is_sorcery)

    result = next_spell_to_cast(
        castable=castable_spells,
        available_mana=available_mana,
        has_reducer_on_board=has_reducer,
        graveyard_spell_count=gy_spells,
        opponent_life=ctx.opp.life,
        am_dead_next=ctx.am_dying,
        medallion_count=medallion_count,
    )

    if result:
        card, role, reason = result
        return SpellDecision(
            card=card, concern="advance",
            reasoning=f"{action_label}: {reason} [{role.name}]",
            alternatives=[])

    return None




def _advance_grind(ctx: _DecisionContext) -> Optional[SpellDecision]:
    """Grind/value: play the highest-impact card available."""
    # Card draw first (grind = card advantage)
    if ctx.my_card_draw:
        return SpellDecision(
            card=ctx.my_card_draw[0], concern="advance",
            reasoning=f"Grinding — drawing cards with {ctx.my_card_draw[0].name}",
            alternatives=[])

    # Then threats (value creatures)
    if ctx.my_threats:
        best = _best_on_curve(ctx.my_threats, ctx)
        return SpellDecision(card=best, concern="advance",
                             reasoning=f"Grinding — deploying {best.name} for value",
                             alternatives=[])

    # Other spells
    if ctx.my_other:
        return SpellDecision(
            card=ctx.my_other[0], concern="advance",
            reasoning=f"Grinding — playing {ctx.my_other[0].name}",
            alternatives=[])

    return None


# ---------------------------------------------------------------------------
# Concern 4: EFFICIENT — Best use of mana when nothing is urgent
# ---------------------------------------------------------------------------

def _concern_efficient(ctx: _DecisionContext) -> Optional[SpellDecision]:
    """Nothing urgent — play the card that uses mana most efficiently.

    This is the "pragmatism" layer. When no concern is pressing,
    just make a reasonable play rather than wasting mana.
    """
    # Should we hold everything?
    if _should_hold_for_interaction(ctx):
        return None  # pass is correct

    # During combo execution: if _advance_combo returned None (holding),
    # don't let EFFICIENT leak rituals/fuel that should be saved.
    from ai.gameplan import GoalType
    if ctx.goal.goal_type == GoalType.EXECUTE_PAYOFF and ctx.archetype == 'combo':
        # Only allow non-fuel spells through (e.g. removal if dying)
        fuel_tags = {'ritual', 'cantrip', 'mana_source', 'combo'}
        ctx_castable_filtered = [
            c for c in ctx.castable
            if not fuel_tags.intersection(getattr(c.template, 'tags', set()))
        ]
        if not ctx_castable_filtered:
            return None  # hold everything for combo turn
        # Replace castable with non-fuel only for this concern
        ctx = _DecisionContext(
            castable=ctx_castable_filtered,
            game=ctx.game, player_idx=ctx.player_idx,
            assessment=ctx.assessment, engine=ctx.engine, goal=ctx.goal,
            me=ctx.me, opp=ctx.opp,
            am_dying=ctx.am_dying,
            must_answer_threats=ctx.must_answer_threats,
            my_removal=ctx.my_removal,
            my_threats=ctx.my_threats,
            my_interaction=ctx.my_interaction,
            my_card_draw=ctx.my_card_draw,
            my_acceleration=ctx.my_acceleration,
            my_other=ctx.my_other,
            opp_mana_up=ctx.opp_mana_up,
            opp_likely_has_interaction=ctx.opp_likely_has_interaction,
            holding_mana_is_valuable=ctx.holding_mana_is_valuable,
            storm_hold_rituals=ctx.storm_hold_rituals,
            role_cache=ctx.role_cache,
            turning_corner=ctx.turning_corner,
            archetype=ctx.archetype,
        )

    # Mana reservation check: should we hold mana for instant-speed interaction?
    # This is a softer check than _should_hold — it considers specific instants in hand
    best_candidate = _most_mana_efficient(ctx.castable, ctx)
    if best_candidate and not best_candidate.template.is_instant:
        should_reserve, hold_for, mana_held = ctx.engine._should_reserve_mana(
            ctx.game, ctx.player_idx, best_candidate, ctx.assessment)
        if should_reserve:
            if ctx.engine.strategic_logger:
                ctx.engine.strategic_logger.log_mana_reservation(
                    ctx.player_idx, ctx.game, hold_for, mana_held,
                    f"Opponent has {ctx.assessment.opp_mana} mana up — holding {mana_held} for {hold_for} over casting {best_candidate.name}")
            return None  # hold mana

    # Play whatever uses mana best
    if best_candidate:
        cmc = best_candidate.template.cmc or 0
        available = ctx.assessment.my_mana
        return SpellDecision(
            card=best_candidate,
            concern="efficient",
            reasoning=f"No urgent concerns — playing {best_candidate.name} (cmc {cmc}, {available} mana available)",
            alternatives=[(c.name, f"cmc {c.template.cmc or 0}")
                          for c in ctx.castable if c != best_candidate][:2]
        )

    return None


# ---------------------------------------------------------------------------
# Prowess synergy check
# ---------------------------------------------------------------------------

def _check_prowess_play(ctx: _DecisionContext) -> Optional[SpellDecision]:
    """If we have prowess creatures on board, cheap noncreature spells
    may be better than deploying another creature."""
    from engine.cards import Keyword as Kw

    prowess_count = sum(
        1 for c in ctx.me.creatures
        if Kw.PROWESS in getattr(c.template, 'keywords', set())
    )
    if prowess_count == 0:
        return None

    # Find cheap noncreature spells that trigger prowess
    prowess_triggers = [
        c for c in ctx.castable
        if not c.template.is_creature and not c.template.is_land
        and (c.template.cmc or 0) <= 2
    ]
    if not prowess_triggers:
        return None

    # Prefer card draw (triggers prowess AND draws)
    draw_triggers = [c for c in prowess_triggers if c in ctx.my_card_draw]
    if draw_triggers:
        best = draw_triggers[0]
        return SpellDecision(
            card=best, concern="advance",
            reasoning=f"Prowess synergy — {best.name} triggers {prowess_count} prowess creature(s) and draws cards",
            alternatives=[]
        )

    # Burn to face while triggering prowess (aggro)
    if ctx.assessment.my_clock <= 4:
        burn = [c for c in prowess_triggers if c in ctx.my_removal]
        if burn and not ctx.opp.creatures:
            # Only burn face if no creatures to remove
            return SpellDecision(
                card=burn[0], concern="advance",
                reasoning=f"Prowess + burn — {burn[0].name} triggers prowess and deals damage",
                alternatives=[]
            )

    return None


# ---------------------------------------------------------------------------
# Pass reasoning
# ---------------------------------------------------------------------------

def _pass_reasoning(ctx: _DecisionContext) -> str:
    """Explain why we're passing with no play."""
    if not ctx.castable:
        return "No castable spells in hand"

    reasons = []
    if ctx.holding_mana_is_valuable:
        reasons.append(f"holding {ctx.assessment.my_mana} mana for instant-speed interaction")
    if ctx.opp_likely_has_interaction and ctx.my_threats:
        reasons.append("opponent likely has interaction, waiting for safer window")

    if reasons:
        return "Passing — " + "; ".join(reasons)
    return "Passing — no play worth making"


# ---------------------------------------------------------------------------
# Helper: find the best removal for a set of threats
# ---------------------------------------------------------------------------

def _best_removal_for_threats(
    removal_cards: List["CardInstance"],
    threats: List["CardInstance"],
    ctx: _DecisionContext
) -> Optional[Tuple["CardInstance", str, str]]:
    """Find the best removal spell for the most important threat.

    Returns (removal_card, target_name, reason) or None.
    Prefers mana-efficient removal that can actually kill the target.
    """
    if not removal_cards or not threats:
        return None

    from ai.evaluator import _permanent_value

    # Rank threats by importance
    threat_values = []
    for t in threats:
        val = _permanent_value(t, ctx.opp, ctx.game, 1 - ctx.player_idx)
        threat_values.append((t, val))
    threat_values.sort(key=lambda x: x[1], reverse=True)

    # For each threat (most important first), find removal that can kill it
    for threat, threat_val in threat_values:
        for rm in removal_cards:
            if _can_kill(rm, threat, ctx):
                reason = f"kills {threat.name} (value {threat_val:.1f})"
                rm_cmc = rm.template.cmc or 1
                threat_cmc = threat.template.cmc or 1
                if rm_cmc < threat_cmc:
                    reason += f", tempo advantage ({rm_cmc} mana vs {threat_cmc})"
                return (rm, threat.name, reason)

    return None


def _can_kill(removal: "CardInstance", target: "CardInstance",
              ctx: _DecisionContext) -> bool:
    """Can this removal spell kill this creature?
    
    Uses oracle text parsing — no hardcoded card names.
    """
    oracle = (removal.template.oracle_text or "").lower()

    # Destroy/exile effects — check for CMC/mana-value restrictions
    if 'destroy' in oracle or 'exile' in oracle:
        # Conditional destroy: "if it has mana value N or less"
        import re
        mv_match = re.search(r'mana value (\d+) or less', oracle)
        if mv_match:
            max_mv = int(mv_match.group(1))
            # Check revolt-like conditions that raise the limit
            revolt_match = re.search(r'mana value (\d+) or less instead', oracle)
            if revolt_match:
                # The higher limit applies with revolt; use the higher one
                # as a reasonable approximation (revolt is often active with fetches)
                max_mv = int(revolt_match.group(1))
            target_cmc = target.template.cmc or 0
            return target_cmc <= max_mv
        return True

    # Damage-based: check if damage >= toughness
    import re
    
    # Check for energy scaling (e.g., Galvanic Discharge)
    if 'energy_scaling' in getattr(removal.template, 'tags', set()):
        energy = getattr(ctx.engine, '_player_energy', 0)
        base = removal.template.cmc or 1
        dmg = base + min(energy, 5)
    else:
        m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
        if m:
            dmg = int(m.group(1))
        else:
            # Check card knowledge for burn damage (handles domain/variable)
            from decks.card_knowledge_loader import get_burn_damage
            known_dmg = get_burn_damage(removal.template.name)
            if known_dmg > 0:
                dmg = known_dmg
            else:
                # -X/-X effects
                m = re.search(r'gets?\s+(-\d+)/(-\d+)', oracle)
                if m:
                    toughness_reduction = abs(int(m.group(2)))
                    toughness = target.toughness or target.template.toughness or 0
                    return toughness_reduction >= toughness
                # Unknown removal type — assume it works
                return True

    toughness = target.toughness or target.template.toughness or 0
    damage_marked = getattr(target, 'damage_marked', 0) or 0
    return dmg >= (toughness - damage_marked)


# ---------------------------------------------------------------------------
# Helper: pick the best card on curve
# ---------------------------------------------------------------------------

def _best_on_curve(cards: List["CardInstance"], ctx: _DecisionContext) -> "CardInstance":
    """Pick the card that uses mana most efficiently this turn.

    Prefers cards whose CMC is close to available mana (don't waste mana).
    Among equal CMC, prefers higher power/toughness.
    Considers role: beatdown prefers power, control prefers toughness.
    """
    available = ctx.assessment.my_mana

    def curve_fitness(card):
        cmc = card.template.cmc or 0
        # How well does this use our mana? (1.0 = perfect)
        if available > 0:
            usage = min(cmc / available, 1.0)
        else:
            usage = 0

        # Tiebreak: raw stats efficiency
        power = card.template.power or 0
        toughness = card.template.toughness or 0

        # Role-aware stat preference
        from ai.evaluator import Role
        if ctx.role_cache == Role.BEATDOWN:
            stats = power * 1.5 + toughness * 0.5
        elif ctx.role_cache == Role.CONTROL:
            stats = power * 0.5 + toughness * 1.5
        else:
            stats = power + toughness

        stats_per_mana = stats / max(cmc, 1)

        return (usage, stats_per_mana)

    return max(cards, key=curve_fitness)


def _most_mana_efficient(cards: List["CardInstance"],
                         ctx: _DecisionContext) -> Optional["CardInstance"]:
    """Pick the card that wastes the least mana."""
    if not cards:
        return None
    return _best_on_curve(cards, ctx)


def _best_role_card(role_cards: List[Tuple["CardInstance", str]],
                    ctx: _DecisionContext) -> Tuple["CardInstance", str]:
    """Pick the best card from those named in goal roles.
    
    During EXECUTE_PAYOFF (combo turn): enablers > engines > payoffs
    (because payoffs should be LAST in the chain).
    
    During other goals: engines > payoffs > enablers > fillers.
    Within same role, prefer cheaper (more mana-efficient).
    """
    if len(role_cards) == 1:
        return role_cards[0]
    
    from ai.gameplan import GoalType
    if ctx.goal.goal_type == GoalType.EXECUTE_PAYOFF:
        # During combo execution: cast enablers/engines first, payoffs LAST
        role_priority = {
            "enablers": 6, "engines": 5, "fillers": 4,
            "protection": 3, "interaction": 2, "payoffs": 1
        }
    else:
        role_priority = {
            "engines": 6, "payoffs": 5, "enablers": 4,
            "fillers": 3, "protection": 2, "interaction": 1
        }
    # Use card_priorities from gameplan as secondary tiebreaker
    priorities = ctx.goal.card_priorities if ctx.goal else {}
    return max(role_cards, key=lambda x: (
        role_priority.get(x[1], 0),
        priorities.get(x[0].template.name, 0),  # gameplan priority
        -(x[0].template.cmc or 0)  # cheaper is better within same role
    ))


# ---------------------------------------------------------------------------
# Helper: mana reservation for high-priority cards
# ---------------------------------------------------------------------------

def _check_mana_reservation(ctx: _DecisionContext, proposed_card) -> Optional["CardInstance"]:
    """Check if hand has a higher-priority castable card that should be preferred.

    Returns the better card if found, or None to stick with proposed.
    Only returns cards that are actually in ctx.my_threats (castable).
    """
    from ai.gameplan import GoalType

    # Aggro decks: just play the best on curve
    if ctx.goal.goal_type in (GoalType.CURVE_OUT, GoalType.PUSH_DAMAGE, GoalType.CLOSE_GAME):
        return None

    if ctx.am_dying:
        return None

    goal_roles = ctx.goal.card_roles if ctx.goal else {}
    payoffs = goal_roles.get("payoffs", set())

    from decks.card_knowledge_loader import get_threat_value
    proposed_threat = get_threat_value(proposed_card.template.name)
    proposed_is_payoff = proposed_card.template.name in payoffs

    # If the proposed card is already a payoff, no need to reserve
    if proposed_is_payoff:
        return None

    # Check if there's a higher-priority CASTABLE card we should play instead
    for card in ctx.my_threats:
        if card == proposed_card:
            continue
        card_threat = get_threat_value(card.template.name)
        card_is_payoff = card.template.name in payoffs

        # Prefer castable payoffs over non-payoffs
        if card_is_payoff and not proposed_is_payoff:
            return card
        # Prefer significantly higher-threat cards
        if card_threat > proposed_threat + 2.0:
            return card

    return None


# Helper: should we hold mana for interaction?
# ---------------------------------------------------------------------------

def _should_hold_for_interaction(ctx: _DecisionContext) -> bool:
    """Should we pass instead of deploying, to hold up instant-speed interaction?

    Only relevant for control/midrange strategies. Aggro should almost never hold.
    """
    from ai.gameplan import GoalType

    # Aggro goals: almost never hold
    if ctx.goal.goal_type in (GoalType.CURVE_OUT, GoalType.PUSH_DAMAGE,
                               GoalType.CLOSE_GAME, GoalType.EXECUTE_PAYOFF):
        return False

    # Turning the corner: stop holding, start deploying
    if ctx.turning_corner:
        return False

    # When dying, deploy threats instead of holding mana
    if ctx.am_dying:
        return False

    # Midrange with no board presence: deploy first, then hold
    # A midrange deck with 0 creatures should develop its board
    if ctx.archetype == 'midrange' and not ctx.me.creatures:
        return False

    # No instants to hold up? Don't hold.
    if not ctx.holding_mana_is_valuable:
        return False

    # Opponent tapped out? Safe to deploy.
    if ctx.opp_mana_up == 0:
        return False

    # If we have removal and opponent has creatures, hold for their turn
    instant_removal = [c for c in ctx.my_removal if c.template.is_instant]
    if instant_removal and ctx.opp.creatures:
        return True

    # If we have counterspells and opponent has mana for a big spell
    if ctx.my_interaction and ctx.opp_mana_up >= 3:
        return True

    return False
