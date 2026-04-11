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
                    f"T{game.display_turn} P{controller+1}: "
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
                f"T{game.display_turn} P{controller+1}: "
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
                f"T{game.display_turn} P{controller+1}: "
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
                    f"T{game.display_turn} P{controller+1}: "
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
                    f"T{game.display_turn} P{controller+1}: "
                    f"{card.name} discards {best.name}")
        # Life loss for Thoughtseize
        if 'you lose' in oracle and 'life' in oracle:
            m = re.search(r'lose\s+(\d+)\s+life', oracle)
            if m:
                game.players[controller].life -= int(m.group(1))


def resolve_attack_trigger(game: "GameState", attacker: "CardInstance",
                            controller: int):
    """Resolve attack triggers by parsing the attacker's oracle text.

    Called when a creature is declared as an attacker.
    """
    oracle = (attacker.template.oracle_text or '').lower()
    if not oracle:
        return

    opponent = 1 - controller

    # ── Battle cry: "each other attacking creature gets +1/+0" ──
    if 'battle cry' in oracle or ('attacks' in oracle and 'other attacking' in oracle
                                   and '+1/+0' in oracle):
        player = game.players[controller]
        for c in player.creatures:
            if c.instance_id != attacker.instance_id and c.attacking:
                c.temp_power_mod += 1
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"{attacker.name} battle cry — other attackers get +1/+0")

    # ── "Whenever this creature attacks, deal N damage" ──
    if 'attacks' in oracle and 'damage' in oracle:
        m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
        if m:
            amount = int(m.group(1))
            game.players[opponent].life -= amount
            game.players[controller].damage_dealt_this_turn += amount

    # ── "Whenever this creature attacks, gain N life" ──
    if 'attacks' in oracle and 'gain' in oracle and 'life' in oracle:
        m = re.search(r'gain\s+(\d+)\s+life', oracle)
        if m:
            game.gain_life(controller, int(m.group(1)), attacker.name)

    # ── Mobilize: "create N tapped and attacking tokens" ──
    if 'mobilize' in oracle:
        m = re.search(r'mobilize\s+(\d+)', oracle)
        if m:
            count = int(m.group(1))
            game.create_token(controller, "warrior", count=count,
                              power=1, toughness=1)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{attacker.name} mobilize {count} — create {count} 1/1 tokens")

    # ── "Whenever this creature attacks, create a token" ──
    if ('attacks' in oracle and 'create' in oracle and 'token' in oracle
            and 'mobilize' not in oracle):
        m = re.search(r'create\s+(?:a|(\d+))\s+(\d+)/(\d+)', oracle)
        if m:
            count = int(m.group(1) or 1)
            p, t = int(m.group(2)), int(m.group(3))
            game.create_token(controller, "creature", count=count,
                              power=p, toughness=t)


def resolve_dies_trigger(game: "GameState", card: "CardInstance",
                          controller: int):
    """Resolve dies/leaves-the-battlefield triggers from oracle text.

    Called when a creature dies or leaves the battlefield.
    """
    oracle = (card.template.oracle_text or '').lower()
    if not oracle:
        return

    # ── "When this creature dies, draw a card" ──
    if 'dies' in oracle and 'draw' in oracle:
        game.draw_cards(controller, 1)

    # ── "When this creature dies, create a token" ──
    if 'dies' in oracle and 'create' in oracle and 'token' in oracle:
        m = re.search(r'create\s+(?:a|(\d+))\s+(\d+)/(\d+)', oracle)
        if m:
            count = int(m.group(1) or 1)
            p, t = int(m.group(2)), int(m.group(3))
            game.create_token(controller, "creature", count=count,
                              power=p, toughness=t)

    # ── "When this creature leaves the battlefield, target opponent draws a card"
    #     (Thought-Knot Seer LTB) ──
    if 'leaves the battlefield' in oracle and 'draw' in oracle:
        opponent = 1 - controller
        if 'opponent' in oracle or 'that player' in oracle:
            game.draw_cards(opponent, 1)
        else:
            game.draw_cards(controller, 1)

    # ── "When this creature dies, return target card from graveyard to hand" ──
    if 'dies' in oracle and 'return' in oracle and 'graveyard' in oracle and 'hand' in oracle:
        player = game.players[controller]
        if player.graveyard:
            # Return the best non-land card
            nonlands = [c for c in player.graveyard if not c.template.is_land
                        and c.instance_id != card.instance_id]
            if nonlands:
                best = max(nonlands, key=lambda c: c.template.cmc or 0)
                player.graveyard.remove(best)
                best.zone = "hand"
                player.hand.append(best)


def resolve_spell_cast_trigger(game: "GameState", caster_idx: int,
                                spell_cast: "CardInstance"):
    """Resolve "whenever you cast a spell" triggers for all permanents.

    Called after a spell is successfully cast (on the stack).
    Handles triggers beyond prowess (which is in game_state.py).
    """
    player = game.players[caster_idx]
    opponent = 1 - caster_idx

    for permanent in player.battlefield:
        oracle = (permanent.template.oracle_text or '').lower()
        if not oracle or 'whenever' not in oracle:
            continue

        # ── "Whenever you cast a noncreature spell, create a token" ──
        if ('noncreature spell' in oracle and 'create' in oracle
                and 'token' in oracle and not spell_cast.template.is_creature):
            m = re.search(r'create\s+(?:a|(\d+))\s+(\d+)/(\d+)', oracle)
            if m:
                count = int(m.group(1) or 1)
                p, t = int(m.group(2)), int(m.group(3))
                game.create_token(caster_idx, "creature", count=count,
                                  power=p, toughness=t)

        # ── "Whenever you cast a spell, [scry/surveil/draw]" ──
        if ('cast a spell' in oracle or 'cast an instant or sorcery' in oracle):
            if 'draw a card' in oracle and 'noncreature' not in oracle:
                game.draw_cards(caster_idx, 1)

        # ── "Whenever an opponent draws a card" (Orcish Bowmasters) ──
        # Already handled by EFFECT_REGISTRY — skip to avoid double-fire

    # Check OPPONENT's permanents for "whenever an opponent casts" triggers
    opp_player = game.players[opponent]
    for permanent in opp_player.battlefield:
        oracle = (permanent.template.oracle_text or '').lower()
        if not oracle or 'whenever' not in oracle:
            continue

        # ── "Whenever an opponent casts a spell, [effect]" ──
        if 'opponent casts' in oracle:
            if 'damage' in oracle:
                m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
                if m:
                    game.players[caster_idx].life -= int(m.group(1))


def check_static_ability(game: "GameState", card: "CardInstance",
                          controller: int, event_type: str, **kwargs):
    """Check if a permanent's static/triggered ability fires for an event.

    event_type: 'spell_cast', 'land_enter', etc.
    """
    oracle = (card.template.oracle_text or '').lower()
    if not oracle:
        return False
    return False


def count_cost_reducers(game, player_idx: int, card_template) -> int:
    """Count how many cost reducers on the battlefield apply to a given spell.

    Generic replacement for hardcoded Ruby Medallion / Ral checks.
    Parses each permanent's oracle text for "cost {N} less" patterns
    and checks if the spell being cast matches the reduction criteria.
    """
    from engine.oracle_parser import parse_cost_reduction
    from engine.cards import CardType, Color
    template = card_template
    player = game.players[player_idx]
    reduction = 0

    for perm in player.battlefield:
        oracle = (perm.template.oracle_text or '').lower()
        if 'cost' not in oracle or 'less' not in oracle:
            continue

        rule = parse_cost_reduction(oracle)
        if not rule:
            continue

        matches = False
        if rule['target'] == 'all':
            matches = True
        elif rule['target'] == 'instant_sorcery':
            matches = template.is_instant or template.is_sorcery
        elif rule['target'] == 'creature':
            matches = template.is_creature
        elif rule['target'] == 'noncreature':
            matches = not template.is_creature

        # Check color restriction
        if matches and rule.get('color'):
            color_map = {'R': Color.RED, 'U': Color.BLUE, 'B': Color.BLACK,
                         'W': Color.WHITE, 'G': Color.GREEN}
            required = color_map.get(rule['color'])
            if required and required not in template.color_identity:
                matches = False

        if matches:
            reduction += rule['amount']

    return reduction
