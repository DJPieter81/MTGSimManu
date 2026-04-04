"""EV-Based Spell Decision — replaces the concern pipeline.

Core principle: every candidate play is scored by projected outcome.
No concern pipeline ordering. No hardcoded thresholds.

Decision flow:
  1. Snapshot current board state
  2. For each candidate play, project the resulting board state
  3. Score each projected state with the archetype's value function
  4. Pick the play with highest EV (or pass if pass-EV is best)

Special handling:
  - Combo decks use chain_simulator for lethal estimation
  - Removal spells are scored by the value of what they kill
  - Burn can target face OR creatures (picks best EV)
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Tuple, Set
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState
    from ai.gameplan import GoalEngine, Goal, BoardAssessment

from ai.ev_evaluator import (
    EVSnapshot, snapshot_from_game, evaluate_board,
    estimate_spell_ev, estimate_pass_ev, creature_value,
    _project_spell, _life_value,
)
from ai.deck_knowledge import DeckKnowledge


@dataclass
class EVSpellDecision:
    """Result of the EV-based spell selection."""
    card: Optional["CardInstance"]
    ev: float                    # expected value of this play
    reasoning: str               # human-readable explanation
    alternatives: List[Tuple[str, float]]  # [(card_name, ev), ...]


def choose_spell_ev(engine: "GoalEngine", castable: List["CardInstance"],
                    game: "GameState", player_idx: int,
                    assessment: "BoardAssessment") -> EVSpellDecision:
    """Main entry point: choose the best spell to cast using EV comparison.

    Replaces the concern pipeline (SURVIVE > ANSWER > ADVANCE > EFFICIENT)
    with a single EV-maximization loop.
    """
    if not castable:
        return EVSpellDecision(card=None, ev=0.0,
                               reasoning="No castable spells",
                               alternatives=[])

    archetype = engine.gameplan.archetype if engine else "midrange"
    snap = snapshot_from_game(game, player_idx)
    me = game.players[player_idx]
    opp = game.players[1 - player_idx]

    # Build deck knowledge if we have decklist info
    dk = _build_deck_knowledge(game, player_idx)

    # Pre-filter: remove cards that should never be cast proactively
    filtered = _apply_proactive_filter(castable, engine, game, player_idx, snap)

    # Emergency re-include: when dying, reactive cards become survival plays
    if snap.am_dead_next or (snap.opp_clock <= 3 and snap.opp_power >= 3):
        filtered = _emergency_reinclusion(filtered, me, engine, game, player_idx)

    # Score each candidate
    scored: List[Tuple["CardInstance", float, str]] = []

    for card in filtered:
        if not game.can_cast(player_idx, card):
            continue

        ev, reason = _score_candidate(card, snap, archetype, dk, game,
                                       player_idx, engine, assessment)
        scored.append((card, ev, reason))

    # Score passing
    pass_ev = estimate_pass_ev(snap, archetype, dk)

    # Apply goal-specific bonuses/penalties
    scored = _apply_goal_modifiers(scored, engine, game, player_idx, assessment)

    # Sort by EV descending
    scored.sort(key=lambda x: x[1], reverse=True)

    # Build alternatives list
    alternatives = [(c.name, ev) for c, ev, _ in scored[:5]]

    # Pick the best play (or pass if pass is better)
    if scored and scored[0][1] > pass_ev:
        best_card, best_ev, best_reason = scored[0]
        return EVSpellDecision(
            card=best_card, ev=best_ev,
            reasoning=best_reason,
            alternatives=alternatives,
        )

    return EVSpellDecision(
        card=None, ev=pass_ev,
        reasoning=f"Pass (EV={pass_ev:.1f}) beats best play "
                  f"({scored[0][0].name}={scored[0][1]:.1f})" if scored else "No plays available",
        alternatives=alternatives,
    )


def _build_deck_knowledge(game: "GameState", player_idx: int) -> Optional[DeckKnowledge]:
    """Build DeckKnowledge from the game state if decklist is available."""
    me = game.players[player_idx]
    # Reconstruct decklist from all zones
    decklist = {}
    for zone in [me.hand, me.library, me.graveyard, me.exile, me.battlefield]:
        for card in zone:
            decklist[card.name] = decklist.get(card.name, 0) + 1

    if not decklist:
        return None

    return DeckKnowledge.from_game_state(me, decklist)


def _apply_proactive_filter(castable: List["CardInstance"],
                             engine: "GoalEngine",
                             game: "GameState", player_idx: int,
                             snap: EVSnapshot) -> List["CardInstance"]:
    """Remove cards that should never be cast proactively in main phase."""
    if not engine:
        return list(castable)

    reactive_only = engine.gameplan.reactive_only
    filtered = []
    for card in castable:
        # Skip counterspells during main phase (can't target anything)
        tags = getattr(card.template, 'tags', set())
        if 'counterspell' in tags:
            continue

        # Skip reactive-only cards (Solitude evoke, etc.) unless we'll use them here
        if card.name in reactive_only:
            continue

        filtered.append(card)

    return filtered


def _emergency_reinclusion(filtered: List["CardInstance"],
                           me, engine: "GoalEngine",
                           game: "GameState", player_idx: int) -> List["CardInstance"]:
    """When dying, re-include reactive cards as survival plays."""
    if not engine:
        return filtered

    existing_ids = {c.instance_id for c in filtered}
    for card in me.hand:
        if card.instance_id in existing_ids:
            continue
        if not game.can_cast(player_idx, card):
            continue

        tags = getattr(card.template, 'tags', set())

        # Skip counterspells — still can't target in main phase
        if 'counterspell' in tags:
            continue

        # Skip protection spells without targets
        if card.name in ('Undying Evil', 'Ephemerate') and not me.creatures:
            continue

        if card.name in engine.gameplan.reactive_only:
            filtered.append(card)
            existing_ids.add(card.instance_id)

    return filtered


def _score_candidate(card: "CardInstance", snap: EVSnapshot,
                     archetype: str, dk: Optional[DeckKnowledge],
                     game: "GameState", player_idx: int,
                     engine: "GoalEngine",
                     assessment: "BoardAssessment") -> Tuple[float, str]:
    """Score a single candidate spell by projected board EV delta."""
    t = card.template
    tags = getattr(t, 'tags', set())

    # Base EV: project board state after casting
    ev = estimate_spell_ev(card, snap, archetype, dk, game, player_idx)

    # Combo chain bonus: if this is a combo piece, estimate chain value
    if archetype == "combo" and _is_combo_relevant(card, engine):
        chain_bonus = _estimate_chain_bonus(card, game, player_idx, engine, snap)
        ev += chain_bonus

    # Removal targeting bonus: score based on WHAT we kill
    if 'removal' in tags and not 'board_wipe' in tags:
        removal_bonus = _score_removal_target(card, game, player_idx, snap, archetype)
        ev += removal_bonus

    # Burn spell dual-use: face vs creature targeting
    if _is_burn_spell(card):
        face_ev = _score_burn_face(card, snap, archetype)
        creature_ev = _score_removal_target(card, game, player_idx, snap, archetype)
        # Pick the better use
        if face_ev > creature_ev and face_ev > 0:
            ev += face_ev
        elif creature_ev > 0:
            ev += creature_ev

    # Mana efficiency bonus — using all available mana is efficient
    cmc = t.cmc or 0
    if snap.my_mana > 0 and cmc > 0:
        efficiency = cmc / snap.my_mana
        ev += efficiency * 0.5  # small bonus for mana efficiency

    # Haste bonus — creature can attack this turn
    kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
           for kw in getattr(t, 'keywords', set())}
    if 'haste' in kws and t.is_creature:
        p = t.power if t.power else 0
        ev += p * 0.5  # immediate damage value

    # Card draw bonus with deck knowledge
    if dk and ('cantrip' in tags or 'draw' in tags):
        ev += _draw_ev_bonus(dk, game, player_idx, engine, archetype)

    # Build reason string
    reason = f"{card.name} (EV={ev:.1f})"

    return ev, reason


def _is_combo_relevant(card: "CardInstance", engine: "GoalEngine") -> bool:
    """Is this card relevant to the combo plan?"""
    if not engine:
        return False
    for goal in engine.gameplan.goals:
        for role_name, cards in goal.card_roles.items():
            if card.name in cards:
                return True
    return False


def _estimate_chain_bonus(card: "CardInstance", game: "GameState",
                          player_idx: int, engine: "GoalEngine",
                          snap: EVSnapshot) -> float:
    """Estimate the chain value for combo-relevant cards.

    Uses combo_chain.py to find viable spell sequences.
    """
    try:
        from ai.combo_chain import find_all_chains, what_is_missing

        me = game.players[player_idx]

        # Find payoff names from gameplan
        payoff_names = set()
        for goal in engine.gameplan.goals:
            payoff_names.update(goal.card_roles.get('finishers', set()))
            payoff_names.update(goal.card_roles.get('payoffs', set()))

        # Count medallions (cost reducers) on board
        from engine.cards import Keyword
        medallion_count = sum(
            1 for c in me.battlefield
            if 'cost_reducer' in getattr(c.template, 'tags', set())
        )

        hand_cards = list(me.hand)
        available_mana = me.available_mana_estimate + me.mana_pool.total()

        chains = find_all_chains(
            hand_cards, available_mana, medallion_count, payoff_names,
            base_storm=me.spells_cast_this_turn
        )

        if not chains:
            return 0.0

        # Best chain value
        best_chain = max(chains, key=lambda c: c.storm_damage + c.storm_tokens)
        if best_chain.storm_damage >= snap.opp_life:
            return 50.0  # lethal combo!
        if best_chain.storm_tokens >= 8:
            return 15.0  # massive token army
        if best_chain.storm_count >= 5:
            return 5.0  # building toward lethal

        # This card is part of a chain — bonus for being fuel
        tags = getattr(card.template, 'tags', set())
        if 'ritual' in tags:
            return 3.0
        if 'cost_reducer' in tags:
            return 5.0
        if 'cantrip' in tags:
            return 2.0

        return 1.0

    except Exception:
        return 0.0


def _score_removal_target(card: "CardInstance", game: "GameState",
                          player_idx: int, snap: EVSnapshot,
                          archetype: str) -> float:
    """Score removal by the value of the best target it can kill."""
    opp = game.players[1 - player_idx]
    if not opp.creatures:
        return -2.0  # no targets = removal is wasted

    # Find the best target
    best_target_value = 0.0
    t = card.template
    cmc = t.cmc or 0

    for c in opp.creatures:
        val = creature_value(c)

        # Check if this removal can actually kill this creature
        # (e.g., Fatal Push can only hit CMC <= 2 without revolt)
        if _can_removal_kill(card, c, game, player_idx):
            best_target_value = max(best_target_value, val)

    if best_target_value <= 0:
        return -1.0  # can't kill anything meaningful

    # Value of removing the target minus the card spent
    return best_target_value * 0.7  # discount: card-for-card trade isn't free


def _can_removal_kill(removal: "CardInstance", target: "CardInstance",
                      game: "GameState", player_idx: int) -> bool:
    """Check if a removal spell can kill a specific creature.

    Uses oracle text heuristics rather than hardcoded card names.
    """
    r = removal.template
    tags = getattr(r, 'tags', set())
    oracle = (r.oracle_text or '').lower()
    target_t = target.template

    # Board wipe kills everything
    if 'board_wipe' in tags:
        return True

    # Damage-based removal: check if damage >= toughness
    from decks.card_knowledge_loader import get_burn_damage
    dmg = get_burn_damage(r.name)
    if dmg > 0:
        return dmg >= (target.toughness or 0)

    # Destroy effects — check if target has indestructible
    kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
           for kw in getattr(target_t, 'keywords', set())}
    if 'indestructible' in kws:
        # Only exile-based removal works
        return 'exile' in oracle

    # CMC restrictions (Fatal Push style)
    if 'mana value' in oracle or 'converted mana cost' in oracle:
        # Extract CMC limit from oracle text
        import re
        match = re.search(r'mana value\s+(\d+)\s+or\s+less', oracle)
        if match:
            limit = int(match.group(1))
            return (target_t.cmc or 0) <= limit

    # Default: assume the removal can kill
    return True


def _is_burn_spell(card: "CardInstance") -> bool:
    """Check if a card is a burn spell (can target face)."""
    from decks.card_knowledge_loader import get_burn_damage
    return get_burn_damage(card.template.name) > 0


def _score_burn_face(card: "CardInstance", snap: EVSnapshot,
                     archetype: str) -> float:
    """Score a burn spell aimed at the opponent's face."""
    from decks.card_knowledge_loader import get_burn_damage
    dmg = get_burn_damage(card.template.name)
    if dmg <= 0:
        return 0.0

    # Lethal burn = massive EV
    if dmg >= snap.opp_life:
        return 50.0

    # Aggro values face damage more
    if archetype == "aggro":
        # Non-linear: damage is worth more when opponent is low
        life_before = _life_value(snap.opp_life)
        life_after = _life_value(max(0, snap.opp_life - dmg))
        return (life_before - life_after) * 0.8

    # Non-aggro: face damage is low priority unless lethal
    return dmg * 0.3


def _draw_ev_bonus(dk: DeckKnowledge, game: "GameState",
                   player_idx: int, engine: "GoalEngine",
                   archetype: str) -> float:
    """EV bonus for draw spells based on what we might find."""
    if dk.deck_size == 0:
        return 0.0

    bonus = 0.0

    # Combo: draw spells are valuable if we're missing combo pieces
    if archetype == "combo" and engine:
        payoff_names = set()
        fuel_names = set()
        for goal in engine.gameplan.goals:
            payoff_names.update(goal.card_roles.get('finishers', set()))
            payoff_names.update(goal.card_roles.get('payoffs', set()))
            fuel_names.update(goal.card_roles.get('fuel', set()))

        # P(drawing a payoff) increases draw spell value
        p_payoff = dk.probability_of_drawing_any(list(payoff_names), 1)
        bonus += p_payoff * 8.0  # finding a payoff is very valuable

        p_fuel = dk.probability_of_drawing_any(list(fuel_names), 1)
        bonus += p_fuel * 3.0

    # Control: value drawing into answers
    if archetype == "control":
        bonus += 1.5  # control always wants more cards

    # General: avoid drawing pure lands when flooded
    me = game.players[player_idx]
    land_density = dk.category_density(dk._land_names)
    if len(me.lands) >= 5:
        # Flooded — draw spells might hit lands
        bonus -= land_density * 2.0
    else:
        # Need lands — draw spells might hit them
        bonus += land_density * 1.0

    return bonus


def _apply_goal_modifiers(scored: List[Tuple["CardInstance", float, str]],
                          engine: "GoalEngine", game: "GameState",
                          player_idx: int,
                          assessment: "BoardAssessment") -> List[Tuple["CardInstance", float, str]]:
    """Apply goal-specific EV modifiers to scored candidates.

    These are multiplicative adjustments, not overrides. The EV comparison
    still drives the final decision.
    """
    if not engine or not scored:
        return scored

    goal = engine.current_goal
    modified = []

    for card, ev, reason in scored:
        modifier = 0.0

        # Card role bonuses from current goal
        for role_name, card_names in goal.card_roles.items():
            if card.name in card_names:
                if role_name in ('payoffs', 'finishers'):
                    modifier += 5.0  # strongly prefer goal payoffs
                elif role_name in ('enablers', 'engines'):
                    modifier += 3.0  # prefer engines
                elif role_name in ('fuel',):
                    modifier += 1.5
                elif role_name in ('interaction',):
                    # Interaction is situational — only bonus when needed
                    if assessment.opp_board_power > 0:
                        modifier += 2.0
                    else:
                        modifier -= 1.0  # no threats to interact with

        # always_early cards get a bonus
        if card.name in engine.gameplan.always_early:
            modifier += 4.0

        # Cycling preference for Living End-style decks
        if goal.prefer_cycling:
            tags = getattr(card.template, 'tags', set())
            if 'cycling' in tags:
                modifier += 3.0

        # Hold mana for interaction (goal.hold_mana)
        if goal.hold_mana and not (card.template.is_instant or
                                     getattr(card.template, 'has_flash', False)):
            cmc = card.template.cmc or 0
            remaining_mana = (assessment.my_mana or 0) - cmc
            if remaining_mana < 2:
                modifier -= 3.0  # penalty for tapping out when holding

        # Storm hold: during combo setup, only cast draw spells
        from ai.gameplan import GoalType
        if goal.goal_type in (GoalType.DEPLOY_ENGINE, GoalType.FILL_RESOURCE):
            tags = getattr(card.template, 'tags', set())
            # In setup phase, prefer engines/draw over raw aggression
            if 'cost_reducer' in tags:
                modifier += 4.0
            elif 'cantrip' in tags or 'draw' in tags:
                modifier += 2.0

        # Mana holdback for interaction (control/midrange)
        if engine.gameplan.archetype in ("control", "midrange"):
            me = game.players[player_idx]
            has_instant_interaction = any(
                c.template.is_instant and (
                    'removal' in getattr(c.template, 'tags', set()) or
                    'counterspell' in getattr(c.template, 'tags', set())
                )
                for c in me.hand if c.instance_id != card.instance_id
            )
            if has_instant_interaction:
                cmc = card.template.cmc or 0
                remaining = (assessment.my_mana or 0) - cmc
                if remaining < 2 and not card.template.is_instant:
                    modifier -= 2.0  # penalty for tapping out with answers in hand

        modified.append((card, ev + modifier, reason))

    return modified
