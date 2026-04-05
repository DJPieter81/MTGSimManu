"""Generic Oracle Text Effect Resolver.

Parses oracle text into executable effects at card load time.
Replaces per-card hardcoded handlers with pattern-based resolution.

This module handles:
- ETB effects (enters the battlefield)
- Spell resolution effects (instants/sorceries)
- Triggered abilities (whenever, when, at the beginning of)
- Static abilities (cost reduction, etc.)

Design: each pattern is a (regex, handler_function) pair. When oracle text
matches a pattern, the handler is registered for that card. Multiple
patterns can match the same card (e.g., Omnath has ETB + landfall).
"""
from __future__ import annotations
import re
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.cards import CardInstance, CardTemplate


def resolve_etb_from_oracle(game: "GameState", card: "CardInstance",
                             controller: int):
    """Resolve ETB effects by parsing the card's oracle text.

    Called when a permanent enters the battlefield. Handles common
    ETB patterns generically instead of per-card.
    """
    oracle = (card.template.oracle_text or '').lower()
    if not oracle:
        return

    opponent = 1 - controller

    # ── "When this creature enters, target opponent reveals their hand.
    #     You choose a nonland card from it and exile that card." ──
    if ('enters' in oracle and 'reveals' in oracle and 'hand' in oracle
            and 'exile' in oracle and 'nonland' in oracle):
        opp = game.players[opponent]
        if opp.hand:
            # Choose the highest-CMC nonland card
            nonlands = [c for c in opp.hand if not c.template.is_land]
            if nonlands:
                best = max(nonlands, key=lambda c: (c.template.cmc or 0))
                opp.hand.remove(best)
                best.zone = "exile"
                game.players[opponent].exile.append(best)
                game.log.append(
                    f"T{game.turn_number} P{controller+1}: "
                    f"{card.name} exiles {best.name} from opponent's hand")

    # ── "When this creature enters, exile target creature/permanent
    #     an opponent controls" (Solitude-style) ──
    # This is already handled by EFFECT_REGISTRY for specific cards.
    # Generic version for any "enters...exile target" creature:
    elif ('enters' in oracle and 'exile target' in oracle
          and 'opponent controls' in oracle
          and card.template.is_creature):
        opp = game.players[opponent]
        if opp.creatures:
            # Exile the highest-value creature
            best = max(opp.creatures, key=lambda c: (c.power or 0) + (c.toughness or 0))
            opp.battlefield.remove(best)
            best.zone = "exile"
            game.players[opponent].exile.append(best)
            # Check for "its controller gains life equal to its power"
            if 'gains life equal' in oracle and 'power' in oracle:
                life_gain = best.power or 0
                opp.life += life_gain
            game.log.append(
                f"T{game.turn_number} P{controller+1}: "
                f"{card.name} exiles {best.name}")

    # ── "When this creature enters, draw a card" ──
    if 'enters' in oracle and 'draw' in oracle and 'card' in oracle:
        amount = 1
        m = re.search(r'draw\s+(\w+)\s+card', oracle)
        if m:
            word_to_num = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4}
            amount = word_to_num.get(m.group(1), 1)
            try:
                amount = int(m.group(1))
            except ValueError:
                pass
        # Avoid double-triggering if also handled by EFFECT_REGISTRY
        if 'draw' not in str(getattr(card, '_etb_effects_fired', [])):
            game.draw_cards(controller, amount)

    # ── "When this creature enters, gain N life" ──
    if 'enters' in oracle and 'gain' in oracle and 'life' in oracle:
        m = re.search(r'gain\s+(\d+)\s+life', oracle)
        if m:
            amount = int(m.group(1))
            game.gain_life(controller, amount, card.name)
            game.log.append(
                f"T{game.turn_number} P{controller+1}: "
                f"{card.name} ETB: gain {amount} life (now {game.players[controller].life})")

    # ── "When this creature enters, deal N damage to any target / opponent" ──
    if 'enters' in oracle and 'damage' in oracle:
        m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
        if m:
            amount = int(m.group(1))
            if 'any target' in oracle or 'target' in oracle:
                # Target opponent by default (AI always targets opp)
                game.players[opponent].life -= amount
                game.players[controller].damage_dealt_this_turn += amount
                game.log.append(
                    f"T{game.turn_number} P{controller+1}: "
                    f"{card.name} ETB: {amount} damage to opponent "
                    f"(life: {game.players[opponent].life})")


def resolve_spell_from_oracle(game: "GameState", card: "CardInstance",
                               controller: int, targets: list = None):
    """Resolve instant/sorcery effects by parsing oracle text.

    Called when a spell resolves. Handles common spell patterns.
    This supplements the existing _execute_spell_effects fallback.
    """
    oracle = (card.template.oracle_text or '').lower()
    if not oracle:
        return

    opponent = 1 - controller

    # ── "Target opponent reveals their hand. You choose a nonland card
    #     and that player discards it." (Thoughtseize, Inquisition) ──
    if 'reveals' in oracle and 'hand' in oracle and 'discard' in oracle:
        opp = game.players[opponent]
        if opp.hand:
            nonlands = [c for c in opp.hand if not c.template.is_land]
            if nonlands:
                # Choose highest-CMC nonland card
                best = max(nonlands, key=lambda c: (c.template.cmc or 0))
                opp.hand.remove(best)
                best.zone = "graveyard"
                game.players[opponent].graveyard.append(best)
                game.log.append(
                    f"T{game.turn_number} P{controller+1}: "
                    f"{card.name} discards {best.name}")
        # Life loss for Thoughtseize
        if 'you lose' in oracle and 'life' in oracle:
            m = re.search(r'lose\s+(\d+)\s+life', oracle)
            if m:
                game.players[controller].life -= int(m.group(1))


def check_static_ability(game: "GameState", card: "CardInstance",
                          controller: int, event_type: str, **kwargs):
    """Check if a permanent's static/triggered ability fires for an event.

    event_type: 'spell_cast', 'land_enter', etc.
    """
    oracle = (card.template.oracle_text or '').lower()
    if not oracle:
        return

    # ── Chalice of the Void: counter spells with CMC == charge counters ──
    if (event_type == 'spell_cast' and 'charge counter' in oracle
            and 'counter' in oracle and 'mana value' in oracle):
        spell = kwargs.get('spell')
        if spell and hasattr(card, 'counters'):
            charge = card.counters.get('charge', 0)
            spell_cmc = spell.template.cmc or 0
            if spell_cmc == charge and charge > 0:
                # Counter the spell
                game.log.append(
                    f"T{game.turn_number}: Chalice of the Void (X={charge}) "
                    f"counters {spell.name}")
                return True  # spell is countered
    return False
