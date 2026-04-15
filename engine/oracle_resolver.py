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


# ═══════════════════════════════════════════════════════════════════
# Oracle-driven target selection helpers
# ═══════════════════════════════════════════════════════════════════
# These replace hardcoded per-card targeting logic. No card names.
# Every scoring decision must derive from oracle text or template data.

def _permanent_threat_value(card: "CardInstance", owner_player) -> float:
    """Score a permanent by how much it threatens us. Higher = more valuable
    to remove. Oracle-driven only — no card names.

    Used by removal/exile/bounce patterns to pick the best target on
    the opponent's battlefield.
    """
    template = card.template
    oracle = (template.oracle_text or '').lower()
    val = 0.0

    # Creature body value (power heavier — pressure matters more than durability)
    if template.is_creature:
        p = card.power or template.power or 0
        t = card.toughness or template.toughness or 0
        val += p * 1.2 + t * 0.4

        # Combat-trigger creatures get a big bump — they generate value each turn
        if 'whenever this creature attacks' in oracle or 'when this creature attacks' in oracle:
            val += 5.0
        # ETB/dies triggers on a deployed creature are past us; only small residual
        if 'dies' in oracle:
            val += 1.0

    # Planeswalkers: loyalty is the resource; +0.5 per loyalty
    if 'planeswalker' in ' '.join(ct.value for ct in template.card_types).lower() \
            if hasattr(template, 'card_types') else False:
        val += (getattr(card, 'loyalty_counters', 0) or template.loyalty or 0) * 0.5 + 3.0

    # Per-permanent scaling triggers (Cranial Plating, Ravager, etc.)
    if re.search(r'for each (artifact|creature|land|enchantment|permanent)', oracle):
        val += 4.0

    # Equipment/Auras that pump attached creature
    if 'equipped creature gets' in oracle or 'enchanted creature gets' in oracle:
        val += 3.0

    # Mana-producing permanents that are not basic lands (rocks, dorks)
    if not template.is_land and 'add' in oracle and '{' in oracle:
        val += 2.0

    # Repeatable card advantage / engines
    if 'whenever' in oracle and ('draw' in oracle or 'create' in oracle):
        val += 3.5

    # CMC is a decent baseline for investment
    val += (template.cmc or 0) * 0.3

    return val


def _pick_damage_target(game: "GameState", controller: int, amount: int):
    """Pick the best creature target for N damage, or None to go face.

    Oracle-driven threat scoring. No card names. Used by ETB damage,
    attack trigger damage, and direct damage spells to route burn
    into high-value creature kills when face damage isn't urgent.
    """
    opp = game.players[1 - controller]
    killable = []
    for c in opp.battlefield:
        if not c.template.is_creature:
            continue
        tough = c.toughness or c.template.toughness or 0
        if tough <= 0:
            continue
        already = getattr(c, 'damage_marked', 0)
        if tough - already <= amount:
            killable.append(c)

    if not killable:
        return None

    best = max(killable, key=lambda c: _permanent_threat_value(c, opp))
    # Only go creature-side if the kill is meaningfully valuable vs face damage.
    # Threshold scales with burn amount — a 1-dmg burn needs a bigger target
    # to skip face, a 3-dmg burn is happy to kill a modest creature.
    if _permanent_threat_value(best, opp) >= amount * 0.8:
        return best
    return None


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
            drawn = game.draw_cards(controller, amount)
            names = ", ".join(c.name for c in drawn) if drawn else ""
            if names:
                game.log.append(f"T{game.display_turn} P{controller+1}: "
                                f"{card.name} ETB: draw {amount} ({names})")

    # ── "When this creature enters, gain N life" ──
    # Only fire for unconditional gains — skip conditional ones like
    # "If you put a Cave onto the battlefield this way, gain N life"
    if ('enters' in oracle and 'gain' in oracle and 'life' in oracle
            and 'if you' not in oracle and 'if a' not in oracle):
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
                # Oracle-driven target pick: route to creature kill when valuable,
                # else face damage. No card-name branches.
                target = _pick_damage_target(game, controller, amount)
                if target is not None:
                    target.damage_marked = getattr(target, 'damage_marked', 0) + amount
                    game.log.append(
                        f"T{game.display_turn} P{controller+1}: "
                        f"{card.name} ETB: {amount} damage to {target.name}")
                    game.check_state_based_actions()
                else:
                    game.players[opponent].life -= amount
                    game.players[controller].damage_dealt_this_turn += amount
                    game.log.append(
                        f"T{game.display_turn} P{controller+1}: "
                        f"{card.name} ETB: {amount} damage to opponent "
                        f"(life: {game.players[opponent].life})")

    # ── Bounce land: "When this land enters, return a land you control
    #    to its owner's hand." (Gruul Turf, Simic Growth Chamber, etc.) ──
    if (card.template.is_land and 'when this land enters' in oracle
            and 'return a land you control' in oracle
            and 'hand' in oracle):
        player = game.players[controller]
        # Return cheapest non-bounce land (prefer basics to keep bounce land)
        candidates = [c for c in player.battlefield
                      if c.template.is_land and c.instance_id != card.instance_id]
        if candidates:
            # Prefer basics first; among bounce lands prefer not to return them
            def bounce_priority(c):
                is_bounce = ('return a land you control' in
                             (c.template.oracle_text or '').lower())
                return (1 if is_bounce else 0, c.template.cmc or 0)
            target = min(candidates, key=bounce_priority)
            player.battlefield.remove(target)
            target.zone = 'hand'
            target.tapped = False
            player.hand.append(target)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{card.name} returns {target.name} to hand")

    # ── Spelunking / "when this enters, draw a card, then you may put a
    #    land card from your hand onto the battlefield" ──
    if ('when this' in oracle and 'enters' in oracle
            and 'draw a card' in oracle
            and 'land card from your hand onto the battlefield' in oracle):
        player = game.players[controller]
        game.draw_cards(controller, 1)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"{card.name} ETB: draw a card")
        lands_in_hand = [c for c in player.hand if c.template.is_land]
        if lands_in_hand:
            # Prefer bounce lands (synergy with Amulet)
            bounce = [c for c in lands_in_hand
                      if 'return a land you control' in
                      (c.template.oracle_text or '').lower()]
            land = bounce[0] if bounce else lands_in_hand[0]
            player.hand.remove(land)
            land.zone = 'battlefield'
            land.controller = controller
            land.enter_battlefield()   # sets tapped if enters_tapped
            player.battlefield.append(land)
            game._apply_untap_on_enter_triggers(land, controller)
            # Also apply "Lands you control enter untapped" static (Spelunking etc.)
            game._apply_lands_enter_untapped(land, controller)
            # Fire land's own ETB (e.g. bounce land returns a land to hand)
            resolve_etb_from_oracle(game, land, controller)
            game._trigger_landfall(controller)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{card.name} puts {land.name} onto battlefield")
            # Cave bonus: gain 4 life only if land placed is a Cave
            if 'Cave' in (land.template.subtypes or []) and 'gain 4 life' in oracle:
                game.gain_life(controller, 4, card.name)
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"{card.name} Cave bonus: gain 4 life")


def resolve_spell_from_oracle(game: "GameState", card: "CardInstance",
                               controller: int, targets: list = None) -> bool:
    """Resolve instant/sorcery effects by parsing oracle text.

    Called when a spell resolves. Handles common spell patterns.
    This supplements the existing _execute_spell_effects fallback.

    Returns True if ANY pattern fired (so the caller can skip further
    generic handling). Returns False if oracle did not match any pattern.
    """
    oracle = (card.template.oracle_text or '').lower()
    if not oracle:
        return False

    opponent = 1 - controller
    fired = False

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
                fired = True
        # Life loss for Thoughtseize
        if 'you lose' in oracle and 'life' in oracle:
            m = re.search(r'lose\s+(\d+)\s+life', oracle)
            if m:
                game.players[controller].life -= int(m.group(1))

    # ── "Destroy target nonland permanent [with mana value X or less]"
    #     (Abrupt Decay: MV<=3, Assassin's Trophy: any, Prismatic Ending: X)
    #     "Exile target nonland permanent" (Celestial Purge, March of Otherworldly Light)
    if (('destroy target' in oracle or 'exile target' in oracle)
            and 'nonland permanent' in oracle):
        opp = game.players[opponent]
        nonland = [c for c in opp.battlefield if not c.template.is_land]
        # Apply mana-value restriction if the oracle has one
        m_mv = re.search(
            r'mana value (?:of\s+)?(\d+) or less', oracle)
        if m_mv:
            max_mv = int(m_mv.group(1))
            nonland = [c for c in nonland if (c.template.cmc or 0) <= max_mv]
        # Color restrictions (Celestial Purge: "black or red")
        if 'black or red' in oracle:
            nonland = [c for c in nonland
                       if any(col in getattr(c.template, 'color_identity', [])
                              for col in ['B', 'R']) or
                       any(str(col).upper() in ('BLACK', 'RED', 'B', 'R')
                           for col in getattr(c.template, 'color_identity', []))]
        if nonland:
            best = max(nonland, key=lambda c: _permanent_threat_value(c, opp))
            dest_zone = 'exile' if 'exile target' in oracle else 'graveyard'
            opp.battlefield.remove(best)
            best.zone = dest_zone
            (opp.exile if dest_zone == 'exile' else opp.graveyard).append(best)
            verb = 'exiles' if dest_zone == 'exile' else 'destroys'
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{card.name} {verb} {best.name}")
            fired = True

    # ── "Return target [adj] creature card from a graveyard to the battlefield"
    #     (Persist: nonlegendary creature card; Unburial Rites: creature card) ──
    if (re.search(r'return target\s+(\w+\s+)?creature card', oracle)
            and 'graveyard' in oracle
            and 'battlefield' in oracle):
        gy = game.players[controller].graveyard
        creatures = [c for c in gy if c.template.is_creature]
        # Honour nonlegendary restriction if present
        if 'nonlegendary' in oracle:
            from engine.cards import Supertype
            creatures = [c for c in creatures
                         if Supertype.LEGENDARY not in
                         getattr(c.template, 'supertypes', [])]
        if creatures:
            best = max(creatures,
                       key=lambda c: (c.template.power or 0)
                       + (c.template.toughness or 0))
            game.reanimate(controller, best)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{card.name} reanimates {best.name}")
            fired = True

    # ── "Return target nonland permanent to its owner's hand" (bounce) ──
    if ('return target' in oracle and "owner's hand" in oracle
            and ('nonland permanent' in oracle
                 or 'creature' in oracle and 'target creature' in oracle)):
        opp = game.players[opponent]
        candidates = [c for c in opp.battlefield if not c.template.is_land]
        if 'nonland permanent' not in oracle and 'creature' in oracle:
            candidates = [c for c in candidates if c.template.is_creature]
        if candidates:
            best = max(candidates, key=lambda c: _permanent_threat_value(c, opp))
            opp.battlefield.remove(best)
            best.zone = 'hand'
            best.tapped = False
            opp.hand.append(best)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{card.name} returns {best.name} to hand")
            fired = True

    # ── "Exile the top N cards... you may play them until end of turn"
    #     (Reckless Impulse, Wrenn's Resolve, Galvanic Relay,
    #      Glimpse the Impossible, March of Reckless Joy,
    #      Valakut Awakening). Simplified: treat exile+may-play as a draw.
    if ('exile' in oracle and 'may play' in oracle
            and ('this turn' in oracle or 'end of turn' in oracle
                 or 'end of your next turn' in oracle)):
        m = re.search(r'exile the top (\w+|\d+) cards?', oracle)
        word_to_num = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
                       'five': 5, 'six': 6, 'seven': 7}
        count = 2
        if m:
            val = m.group(1)
            try:
                count = int(val)
            except ValueError:
                count = word_to_num.get(val, 2)
        drawn = game.draw_cards(controller, count)
        names = ", ".join(c.name for c in drawn) if drawn else ""
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"{card.name}: exile {count} → may play ({names})")
        fired = True

    # ── "Draw N cards" (simple cantrips: Preordain, Sleight of Hand,
    #     Heroes' Hangout, Stock Up etc.) — only if no other more specific
    #     pattern has fired, to avoid double-draw.
    if not fired and 'draw' in oracle and 'card' in oracle:
        # Only fire for spells that are basically "draw N" — skip complex
        # draws guarded by conditions ("if... draw").
        if ('if' not in oracle or 'otherwise' in oracle
                or oracle.count('if') == 0):
            m = re.search(r'draw\s+(\w+)\s+card', oracle)
            if m:
                word_to_num = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
                               'five': 5, 'six': 6, 'seven': 7}
                try:
                    count = int(m.group(1))
                except ValueError:
                    count = word_to_num.get(m.group(1), 1)
                # Skip if oracle implies this is a trigger, not a spell effect
                if 'whenever' not in oracle and 'when' not in oracle:
                    drawn = game.draw_cards(controller, count)
                    names = ", ".join(c.name for c in drawn) if drawn else ""
                    game.log.append(
                        f"T{game.display_turn} P{controller+1}: "
                        f"{card.name}: draw {count} ({names})")
                    fired = True

    return fired


def resolve_attack_trigger(game: "GameState", attacker: "CardInstance",
                            controller: int):
    """Resolve attack triggers by parsing the attacker's oracle text.

    Called when a creature is declared as an attacker.
    """
    oracle = (attacker.template.oracle_text or '').lower()
    if not oracle:
        return

    opponent = 1 - controller

    # Battle cry is handled by CombatManager._apply_battle_cry after all
    # attackers are declared — skipped here to avoid double-application.
    # (oracle_resolver fires per-attacker mid-loop; combat_manager fires once
    # over the complete attacker list, which is the correct timing.)

    # ── "Whenever this creature attacks, deal N damage" ──
    if 'attacks' in oracle and 'damage' in oracle:
        m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
        if m:
            amount = int(m.group(1))
            # Oracle-driven target pick: route to creature kill when valuable,
            # else face damage. Same logic as ETB damage.
            target = _pick_damage_target(game, controller, amount)
            if target is not None:
                target.damage_marked = getattr(target, 'damage_marked', 0) + amount
                game.check_state_based_actions()
            else:
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


def _handle_coin_flip_transform(game: "GameState", controller: int,
                                creature: "CardInstance"):
    """Handle Ral-style coin flip: flip a coin, on win → exile and return
    transformed as a planeswalker with loyalty = 3 + spells cast this turn.

    On loss: deals 1 damage to controller (from Ral's oracle text).
    """
    player = game.players[controller]
    result = game.rng.choice(["win", "lose"])

    if result == "lose":
        # Ral deals 1 damage to controller on losing the flip
        player.life -= 1
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                       f"{creature.name} — lost coin flip, takes 1 damage")
        return

    # Won the flip → transform creature into planeswalker
    spells_this_turn = player.spells_cast_this_turn
    base_loyalty = creature.template.back_face_loyalty or 2
    starting_loyalty = base_loyalty + spells_this_turn

    # Remove creature from battlefield
    if creature in player.battlefield:
        player.battlefield.remove(creature)

    # Transform: set as planeswalker with loyalty
    creature.is_transformed = True
    creature.loyalty_counters = starting_loyalty
    creature.damage_marked = 0

    # Return to battlefield as planeswalker
    creature.zone = "battlefield"
    player.battlefield.append(creature)

    game.log.append(f"T{game.display_turn} P{controller+1}: "
                   f"{creature.name} — won coin flip! Transforms with "
                   f"{starting_loyalty} loyalty ({spells_this_turn} spells cast)")


def resolve_spell_cast_trigger(game: "GameState", caster_idx: int,
                                spell_cast: "CardInstance"):
    """Resolve "whenever you cast a spell" triggers for all permanents.

    Called after a spell is successfully cast (on the stack).
    Handles triggers beyond prowess (which is in game_state.py).
    """
    player = game.players[caster_idx]
    opponent = 1 - caster_idx

    for permanent in list(player.battlefield):  # copy: battlefield may change (transform)
        oracle = (permanent.template.oracle_text or '').lower()
        if not oracle or 'whenever' not in oracle:
            continue

        # ── "Whenever you cast a noncreature spell, you get {E}" ──
        # Matches Ocelot Pride and any future card with this exact trigger.
        if ('noncreature spell' in oracle and 'you get' in oracle
                and '{e}' in oracle and not spell_cast.template.is_creature
                and permanent.controller == caster_idx
                and 'create' not in oracle):  # exclude token-creators to avoid double-fire
            import re as _re
            m = _re.search(r'you get\s+((?:\{e\})+)', oracle)
            energy_count = m.group(1).count('{e}') if m else 1
            game.produce_energy(caster_idx, energy_count, permanent.name)

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

        # ── Ral-style coin flip transform trigger ──
        # "Whenever you cast an instant or sorcery spell, flip a coin"
        # On winning flip: exile creature, return transformed as planeswalker
        # Only triggers while still a creature (not already transformed)
        if ('flip a coin' in oracle
                and ('instant or sorcery' in oracle or 'instant and sorcery' in oracle)
                and (spell_cast.template.is_instant or spell_cast.template.is_sorcery)
                and permanent.template.is_creature
                and not getattr(permanent, 'is_transformed', False)):
            _handle_coin_flip_transform(game, caster_idx, permanent)

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
