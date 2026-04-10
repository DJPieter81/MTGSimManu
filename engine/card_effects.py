"""
Declarative Card Effects System
================================
Provides a registry-based approach for card-specific logic, replacing the
monolithic if/elif chains in game_state.py's _handle_permanent_etb and
_execute_spell_effects.

Design principles:
  1. Card effects are registered by name in a global registry
  2. The engine calls the registry at ETB / spell resolution / attack / dies
  3. New cards are added by writing a single function, not editing game_state.py
  4. The registry is a dict[str, list[EffectHandler]] — multiple effects per card
  5. Fallback: if no registered handler, the engine's generic logic still runs

This is an incremental migration: we move cards one at a time from game_state.py
into this registry, validating with the stress test after each batch.
"""
from __future__ import annotations
from typing import Callable, Dict, List, Optional, Any, TYPE_CHECKING
from dataclasses import dataclass, field
from enum import Enum

if TYPE_CHECKING:
    from .game_state import GameState
    from .cards import CardInstance
    from .stack import StackItem


class EffectTiming(Enum):
    """When the effect triggers."""
    ETB = "etb"                    # enters the battlefield
    SPELL_RESOLVE = "spell_resolve"  # instant/sorcery resolves
    ATTACK = "attack"              # when this creature attacks
    DIES = "dies"                   # when this creature dies
    UPKEEP = "upkeep"              # at beginning of upkeep
    END_STEP = "end_step"          # at beginning of end step
    LANDFALL = "landfall"          # when a land enters under your control
    CAST = "cast"                  # when this spell is cast (before resolution)
    DAMAGE_DEALT = "damage_dealt"  # when this creature deals combat damage


@dataclass
class EffectHandler:
    """A single registered effect for a card."""
    card_name: str
    timing: EffectTiming
    handler: Callable  # (game, card, controller, targets) -> None
    description: str = ""
    priority: int = 0  # higher = runs first (for ordering multiple effects)


class CardEffectRegistry:
    """Global registry of card-specific effects.

    Usage:
        registry = CardEffectRegistry()

        @registry.register("Solitude", EffectTiming.ETB)
        def solitude_etb(game, card, controller, targets=None):
            opponent = 1 - controller
            opp_creatures = game.players[opponent].creatures
            if opp_creatures:
                target = max(opp_creatures, key=lambda c: c.power or 0)
                game._exile_permanent(target)
                game.log.append(...)

        # In game_state.py:
        if not registry.execute(card.name, EffectTiming.ETB, game, card, controller):
            # fallback to generic logic
            ...
    """

    def __init__(self):
        self._handlers: Dict[str, List[EffectHandler]] = {}

    def register(self, card_name: str, timing: EffectTiming,
                 description: str = "", priority: int = 0):
        """Decorator to register an effect handler for a card."""
        def decorator(fn: Callable):
            handler = EffectHandler(
                card_name=card_name,
                timing=timing,
                handler=fn,
                description=description,
                priority=priority,
            )
            if card_name not in self._handlers:
                self._handlers[card_name] = []
            self._handlers[card_name].append(handler)
            # Sort by priority (descending) so highest runs first
            self._handlers[card_name].sort(key=lambda h: -h.priority)
            return fn
        return decorator

    def has_handler(self, card_name: str, timing: EffectTiming) -> bool:
        """Check if a card has registered handlers for a timing."""
        handlers = self._handlers.get(card_name, [])
        return any(h.timing == timing for h in handlers)

    def execute(self, card_name: str, timing: EffectTiming,
                game: "GameState", card: "CardInstance",
                controller: int, targets: Optional[List] = None,
                item: Optional["StackItem"] = None) -> bool:
        """Execute all registered handlers for a card at a given timing.

        Returns True if any handler was found and executed.
        Returns False if no handler exists (caller should use fallback).
        """
        handlers = self._handlers.get(card_name, [])
        matching = [h for h in handlers if h.timing == timing]

        if not matching:
            return False

        for handler in matching:
            handler.handler(game, card, controller, targets=targets, item=item)

        return True

    def get_registered_cards(self, timing: Optional[EffectTiming] = None) -> List[str]:
        """List all card names with registered handlers."""
        if timing is None:
            return list(self._handlers.keys())
        return [name for name, handlers in self._handlers.items()
                if any(h.timing == timing for h in handlers)]


# ═══════════════════════════════════════════════════════════════════
# Global registry instance
# ═══════════════════════════════════════════════════════════════════
EFFECT_REGISTRY = CardEffectRegistry()


# ═══════════════════════════════════════════════════════════════════
# ETB Effects
# ═══════════════════════════════════════════════════════════════════

@EFFECT_REGISTRY.register("Solitude", EffectTiming.ETB,
                           description="Exile target creature, its controller gains life equal to its power")
def solitude_etb(game, card, controller, targets=None, item=None):
    # Real oracle: "When Solitude enters the battlefield, exile up to one other
    # target creature. That creature's controller gains life equal to its power."
    # Key rules: targets opponent's creature, gives THEM life equal to power.
    opponent = 1 - controller
    opp_creatures = game.players[opponent].creatures
    if not opp_creatures:
        return  # No valid targets — ETB fizzles
    # Pick the most threatening creature (highest power, then CMC)
    target = max(opp_creatures, key=lambda c: (c.power or 0, c.template.cmc))
    life_gain = target.power or 0
    game._exile_permanent(target)
    game.players[opponent].life += life_gain
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Solitude exiles {target.name} "
                    f"(opponent gains {life_gain} life, now {game.players[opponent].life})")


@EFFECT_REGISTRY.register("Subtlety", EffectTiming.ETB,
                           description="Put target creature on top of its owner's library")
def subtlety_etb(game, card, controller, targets=None, item=None):
    # Subtlety's oracle: "choose up to one target creature spell or planeswalker spell.
    # Its owner puts it on their choice of the top or bottom of their library."
    # Simplified: bounce the best opponent creature to top of library.
    opponent = 1 - controller
    opp_creatures = game.players[opponent].creatures
    if not opp_creatures:
        return  # No valid targets — ETB fizzles
    # Pick the most threatening creature (highest power + CMC)
    target = max(opp_creatures, key=lambda c: (c.power or 0) + (c.template.cmc or 0))
    # Remove from battlefield and put on top of library
    if target in game.players[opponent].battlefield:
        game.players[opponent].battlefield.remove(target)
    target.zone = "library"
    target.tapped = False
    target.summoning_sick = False
    target.damage_taken = 0
    # Put on top of library
    game.players[opponent].library.append(target)  # append = top
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Subtlety puts {target.name} on top of opponent's library")


@EFFECT_REGISTRY.register("Endurance", EffectTiming.ETB,
                           description="Target player shuffles graveyard into library")
def endurance_etb(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    target_idx = opponent if game.players[opponent].graveyard else controller
    target_player = game.players[target_idx]
    gy_count = len(target_player.graveyard)
    if gy_count > 0:
        while target_player.graveyard:
            card_gy = target_player.graveyard.pop()
            card_gy.zone = "library"
            target_player.library.append(card_gy)
        game.rng.shuffle(target_player.library)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Endurance ETB: P{target_idx+1} shuffles {gy_count} cards "
                        f"from GY into library")


@EFFECT_REGISTRY.register("Omnath, Locus of Creation", EffectTiming.ETB,
                           description="Draw a card")
def omnath_etb(game, card, controller, targets=None, item=None):
    """Omnath ETB: draw a card. The +4 life is the 1st LANDFALL trigger, not ETB."""
    game.draw_cards(controller, 1)
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Omnath ETB: draw a card")


@EFFECT_REGISTRY.register("Murktide Regent", EffectTiming.ETB,
                           description="Delve instants/sorceries from GY, enter with +1/+1 counters")
def murktike_etb(game, card, controller, targets=None, item=None):
    # Murktide enters with +1/+1 counters for each instant/sorcery
    # exiled WITH IT during delve (not additional exiles).
    delved_spells = getattr(card, '_delved_spells', 0)
    if delved_spells > 0:
        card.temp_power_mod += delved_spells
        card.temp_toughness_mod += delved_spells
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Murktide Regent enters as {card.power}/{card.toughness}"
                    f" ({delved_spells} instants/sorceries delved)")


@EFFECT_REGISTRY.register("Eternal Witness", EffectTiming.ETB,
                           description="Return card from graveyard to hand")
def eternal_witness_etb(game, card, controller, targets=None, item=None):
    player = game.players[controller]
    if player.graveyard:
        nonlands = [c for c in player.graveyard if not c.template.is_land]
        if nonlands:
            best = max(nonlands, key=lambda c: c.template.cmc)
        else:
            best = player.graveyard[0]
        player.graveyard.remove(best)
        best.zone = "hand"
        player.hand.append(best)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Eternal Witness returns {best.name} from GY")


@EFFECT_REGISTRY.register("Quantum Riddler", EffectTiming.ETB,
                           description="Draw 2 cards")
def quantum_riddler_etb(game, card, controller, targets=None, item=None):
    game.draw_cards(controller, 2)
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Quantum Riddler ETB: draw 2 cards")


@EFFECT_REGISTRY.register("Mox Opal", EffectTiming.ETB,
                           description="Metalcraft: produces any color mana")
def mox_opal_etb(game, card, controller, targets=None, item=None):
    from .cards import CardType, CardTemplate
    template = card.template
    artifact_count = sum(1 for c in game.players[controller].battlefield
                         if CardType.ARTIFACT in c.template.card_types)
    if artifact_count >= 3:  # metalcraft
        card.template = CardTemplate(
            name=template.name,
            card_types=template.card_types,
            mana_cost=template.mana_cost,
            supertypes=template.supertypes,
            subtypes=template.subtypes,
            produces_mana=["W", "U", "B", "R", "G"],
            tags=template.tags | {"mana_source"},
        )


@EFFECT_REGISTRY.register("Cranial Plating", EffectTiming.ETB,
                           description="Enters battlefield unattached. Equip {1}.")
def cranial_plating_etb(game, card, controller, targets=None, item=None):
    # In real MTG, equipment enters unattached.
    # The AI must spend mana to equip via the "equip" action in main phase.
    # Mark this card as equipment so the game knows it can be equipped.
    card.instance_tags.add("equipment_unattached")
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Cranial Plating enters the battlefield (unattached)")


@EFFECT_REGISTRY.register("Nettlecyst", EffectTiming.ETB,
                           description="Create Germ token, equip it")
def nettlecyst_etb(game, card, controller, targets=None, item=None):
    game.create_token(controller, "germ", count=1)
    germs = [c for c in game.players[controller].creatures
             if "Germ" in c.name]
    if germs:
        germs[-1].instance_tags.add("nettlecyst_equipped")


@EFFECT_REGISTRY.register("Springleaf Drum", EffectTiming.ETB,
                           description="Tap creature to add mana of any color")
def springleaf_drum_etb(game, card, controller, targets=None, item=None):
    from .cards import CardTemplate
    template = card.template
    card.template = CardTemplate(
        name=template.name,
        card_types=template.card_types,
        mana_cost=template.mana_cost,
        produces_mana=["W", "U", "B", "R", "G"],
        tags=template.tags | {"mana_source"},
    )


@EFFECT_REGISTRY.register("Phlage, Titan of Fire's Fury", EffectTiming.ETB,
                           description="ETB: deal 3 damage to any target, gain 3 life")
def phlage_etb(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    # Deal 3 damage to opponent
    game.players[opponent].life -= 3
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Phlage ETB: 3 damage to opponent "
                    f"(opponent life: {game.players[opponent].life})")
    # Gain 3 life
    game.players[controller].life += 3
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Phlage ETB: gain 3 life "
                    f"(life: {game.players[controller].life})")
    # Check if opponent is dead
    if game.players[opponent].life <= 0:
        game.game_over = True
        game.winner = controller
        game.win_condition = "life_total"


# ═══════════════════════════════════════════════════════════════════
# Spell Resolution Effects
# ═══════════════════════════════════════════════════════════════════

@EFFECT_REGISTRY.register("Lightning Bolt", EffectTiming.SPELL_RESOLVE,
                           description="Deal 3 damage to any target")
def lightning_bolt_resolve(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    if targets:
        for tid in targets:
            target = game.get_card_by_id(tid)
            if target and target.zone == "battlefield" and target.template.is_creature:
                target.damage_marked += 3
                if target.is_dead:
                    game._creature_dies(target)
                return
    game.players[opponent].life -= 3
    game.players[controller].damage_dealt_this_turn += 3


@EFFECT_REGISTRY.register("Lava Dart", EffectTiming.SPELL_RESOLVE,
                           description="Deal 1 damage to any target")
def lava_dart_resolve(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    if targets:
        for tid in targets:
            target = game.get_card_by_id(tid)
            if target and target.zone == "battlefield" and target.template.is_creature:
                target.damage_marked += 1
                if target.is_dead:
                    game._creature_dies(target)
                return
    game.players[opponent].life -= 1
    game.players[controller].damage_dealt_this_turn += 1


@EFFECT_REGISTRY.register("Unholy Heat", EffectTiming.SPELL_RESOLVE,
                           description="Deal 2 (or 6 with delirium) damage")
def unholy_heat_resolve(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    gy = game.players[controller].graveyard
    types_in_gy = set()
    for c in gy:
        for ct in c.template.card_types:
            types_in_gy.add(ct)
    damage = 6 if len(types_in_gy) >= 4 else 2
    if targets:
        for tid in targets:
            target = game.get_card_by_id(tid)
            if target and target.zone == "battlefield" and target.template.is_creature:
                target.damage_marked += damage
                if target.is_dead:
                    game._creature_dies(target)
                return
    game.players[opponent].life -= damage
    game.players[controller].damage_dealt_this_turn += damage


@EFFECT_REGISTRY.register("Goryo's Vengeance", EffectTiming.SPELL_RESOLVE,
                           description="Reanimate legendary creature with haste, exile at EOT")
def goryos_vengeance_resolve(game, card, controller, targets=None, item=None):
    gy = game.players[controller].graveyard
    legendary_creatures = [c for c in gy
                           if c.template.is_creature and
                           any(str(st) == "Supertype.LEGENDARY"
                               or st.name == "LEGENDARY"
                               for st in c.template.supertypes)]
    if not legendary_creatures:
        legendary_creatures = [c for c in gy if c.template.is_creature]
    if legendary_creatures:
        best = max(legendary_creatures,
                   key=lambda c: (c.template.power or 0) + (c.template.toughness or 0))
        game.reanimate(controller, best, exile_at_eot=True, give_haste=True)


@EFFECT_REGISTRY.register("Persist", EffectTiming.SPELL_RESOLVE,
                           description="Reanimate creature from graveyard")
def persist_resolve(game, card, controller, targets=None, item=None):
    gy = game.players[controller].graveyard
    creatures = [c for c in gy if c.template.is_creature]
    if creatures:
        best = max(creatures,
                   key=lambda c: (c.template.power or 0) + (c.template.toughness or 0))
        game.reanimate(controller, best)


@EFFECT_REGISTRY.register("Unmarked Grave", EffectTiming.SPELL_RESOLVE,
                           description="Tutor nonlegendary card to graveyard")
def unmarked_grave_resolve(game, card, controller, targets=None, item=None):
    from .cards import Supertype
    lib = game.players[controller].library
    # Unmarked Grave can only find NONLEGENDARY cards
    nonlegendary_creatures = [
        c for c in lib if c.template.is_creature
        and Supertype.LEGENDARY not in getattr(c.template, 'supertypes', [])
    ]
    # Fallback: if no nonlegendary creatures, try any nonlegendary card
    if not nonlegendary_creatures:
        nonlegendary_creatures = [
            c for c in lib
            if Supertype.LEGENDARY not in getattr(c.template, 'supertypes', [])
            and c.template.is_creature
        ]
    if nonlegendary_creatures:
        best = max(nonlegendary_creatures,
                   key=lambda c: (c.template.power or 0) + (c.template.toughness or 0))
        lib.remove(best)
        best.zone = "graveyard"
        game.players[controller].graveyard.append(best)
        game.rng.shuffle(lib)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Unmarked Grave puts {best.name} in graveyard")
    else:
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Unmarked Grave finds nothing (no nonlegendary creatures)")


@EFFECT_REGISTRY.register("Grapeshot", EffectTiming.SPELL_RESOLVE,
                           description="Deal 1 damage to any target")
def grapeshot_resolve(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    # Grapeshot deals 1 damage (base effect). Storm copies are handled
    # by _handle_storm which calls this again for each copy.
    game.players[opponent].life -= 1
    game.players[controller].damage_dealt_this_turn += 1
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Grapeshot deals 1 damage"
                    f" (opponent life: {game.players[opponent].life})")


@EFFECT_REGISTRY.register("Past in Flames", EffectTiming.SPELL_RESOLVE,
                           description="Give flashback to instants/sorceries in graveyard")
def past_in_flames_resolve(game, card, controller, targets=None, item=None):
    for c in game.players[controller].graveyard:
        if c.template.is_instant or c.template.is_sorcery:
            c.has_flashback = True
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Past in Flames grants flashback")


@EFFECT_REGISTRY.register("Empty the Warrens", EffectTiming.SPELL_RESOLVE,
                           description="Create 2 Goblin tokens")
def empty_the_warrens_resolve(game, card, controller, targets=None, item=None):
    # Base effect: create 2 Goblin tokens. Storm copies are handled
    # by _handle_storm which calls this again for each copy.
    game.create_token(controller, "goblin", count=2)
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Empty the Warrens creates 2 Goblin tokens")


@EFFECT_REGISTRY.register("Galvanic Relay", EffectTiming.SPELL_RESOLVE,
                           description="Exile top cards, play next turn (simplified: draw)")
def galvanic_relay_resolve(game, card, controller, targets=None, item=None):
    draw_count = min(game._global_storm_count, 5)
    game.draw_cards(controller, draw_count)


@EFFECT_REGISTRY.register("Galvanic Discharge", EffectTiming.SPELL_RESOLVE,
                           description="Deal 2 + energy spent damage")
def galvanic_discharge_resolve(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    player = game.players[controller]
    # Real oracle: "deals 2 damage ... You get {E}{E}, then you may pay any
    # amount of {E}. Galvanic Discharge deals that much additional damage."
    # Step 1: gain 2 energy
    player.energy_counters += 2
    # Step 2: spend as much energy as useful (up to 5)
    energy_to_spend = min(player.energy_counters, 5)
    if energy_to_spend > 0:
        player.spend_energy(energy_to_spend)
    damage = 2 + energy_to_spend
    opp = game.players[opponent]
    # Use AI-chosen targets if available
    target_creature = None
    if targets:
        for tid in targets:
            if tid == -1:
                break  # AI chose to go face
            candidate = game.get_card_by_id(tid)
            if candidate and candidate.zone == "battlefield" and candidate.template.is_creature:
                target_creature = candidate
                break
    # Fallback: pick best killable creature (not just highest power)
    if target_creature is None and (not targets or (targets and targets[0] != -1)):
        killable = [
            c for c in opp.creatures
            if damage >= (c.toughness or 0) - (getattr(c, 'damage_marked', 0) or 0)
        ]
        if killable:
            target_creature = max(killable, key=lambda c: c.power or 0)
    if target_creature:
        target_creature.damage_marked += damage
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Galvanic Discharge deals {damage} to {target_creature.name}")
        if target_creature.is_dead:
            game._creature_dies(target_creature)
    else:
        opp.life -= damage
        game.players[controller].damage_dealt_this_turn += damage
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Galvanic Discharge deals {damage} to opponent")


@EFFECT_REGISTRY.register("Thoughtseize", EffectTiming.SPELL_RESOLVE,
                           description="Opponent discards nonland, you lose 2 life")
def thoughtseize_resolve(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    game.players[controller].life -= 2
    game._force_discard(opponent, 1)


@EFFECT_REGISTRY.register("Faithful Mending", EffectTiming.SPELL_RESOLVE,
                           description="Draw 2, discard 2, gain 2 life")
def faithful_mending_resolve(game, card, controller, targets=None, item=None):
    game.draw_cards(controller, 2)
    game._force_discard(controller, 2, self_discard=True)
    game.players[controller].life += 2


@EFFECT_REGISTRY.register("Wrath of the Skies", EffectTiming.SPELL_RESOLVE,
                           description="Destroy nonland permanents with MV <= X (energy)")
def wrath_of_the_skies_resolve(game, card, controller, targets=None, item=None):
    from .cards import Keyword
    player = game.players[controller]
    x_val = min(player.energy_counters, 10)
    if x_val > 0:
        player.spend_energy(x_val)
    for p in game.players:
        to_destroy = [c for c in p.battlefield
                      if not c.template.is_land
                      and c.template.cmc <= (x_val + 2)
                      and Keyword.INDESTRUCTIBLE not in c.keywords]
        for creature in to_destroy:
            if creature.template.is_creature:
                game._creature_dies(creature)
            else:
                game._permanent_destroyed(creature)
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Wrath of the Skies (X={x_val}) sweeps the board")


@EFFECT_REGISTRY.register("Prismatic Ending", EffectTiming.SPELL_RESOLVE,
                           description="Exile target nonland permanent with MV <= colors")
def prismatic_ending_resolve(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    opp = game.players[opponent]
    player_lands = game.players[controller].lands
    colors = set()
    for land in player_lands:
        for c in (land.template.produces_mana or []):
            colors.add(c)
    max_cmc = min(len(colors), 5)
    if max_cmc < 1:
        max_cmc = 1
    exile_targets = [c for c in opp.battlefield
                     if not c.template.is_land and c.template.cmc <= max_cmc]
    if exile_targets:
        target = max(exile_targets, key=lambda c: c.template.cmc)
        game._exile_permanent(target)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Prismatic Ending exiles {target.name}")


@EFFECT_REGISTRY.register("March of Otherworldly Light", EffectTiming.SPELL_RESOLVE,
                           description="Exile target artifact, creature, or enchantment with MV <= X")
def march_otherworldly_light_resolve(game, card, controller, targets=None, item=None):
    from .cards import CardType
    opponent = 1 - controller
    opp = game.players[opponent]
    # X = mana spent beyond the base W cost (total lands available as proxy)
    x_val = len(game.players[controller].lands)
    exile_targets = [c for c in opp.battlefield
                     if not c.template.is_land
                     and (CardType.ARTIFACT in c.template.card_types
                          or CardType.CREATURE in c.template.card_types
                          or CardType.ENCHANTMENT in c.template.card_types)
                     and c.template.cmc <= x_val]
    if exile_targets:
        target = max(exile_targets, key=lambda c: c.template.cmc)
        game._exile_permanent(target)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"March of Otherworldly Light exiles {target.name}")
    else:
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"March of Otherworldly Light: no valid targets")


@EFFECT_REGISTRY.register("Ephemerate", EffectTiming.SPELL_RESOLVE,
                           description="Blink target creature you control")
def ephemerate_resolve(game, card, controller, targets=None, item=None):
    # Real oracle: "Exile target creature you control, then return it to the
    # battlefield under its owner's control. Rebound."
    # Requires a creature target — if none, spell fizzles.
    my_creatures = game.players[controller].creatures
    if not my_creatures:
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Ephemerate fizzles (no creatures to target)")
        return
    # Prefer creatures with valuable ETBs.
    # Use tags to identify ETB value, then score by card impact.
    def _blink_value(c):
        tags = getattr(c.template, 'tags', set())
        score = 0
        if 'etb_value' in tags:
            score += 5
        # Life gain on ETB is especially valuable (e.g., Omnath +4 life)
        oracle = (c.template.oracle_text or "").lower()
        if 'gain' in oracle and 'life' in oracle:
            score += 3
        # Card draw on ETB
        if 'cantrip' in tags or ('draw' in oracle and 'enter' in oracle):
            score += 2
        # Removal on ETB (Solitude, Fury): value depends on opponent board
        if 'removal' in tags:
            opp_idx = 1 - controller
            if game.players[opp_idx].creatures:
                score += 4
            else:
                score += 1  # No targets: removal ETB is wasted
        # Higher CMC creatures are generally more impactful to blink
        score += (c.template.cmc or 0) * 0.5
        return score
    best = max(my_creatures, key=_blink_value)
    game._blink_permanent(best, controller)
    # Mark for rebound — the actual zone move happens after resolution
    # (in resolve_stack, which puts the card in GY, then we intercept)
    card._rebound_controller = controller


@EFFECT_REGISTRY.register("Undying Evil", EffectTiming.SPELL_RESOLVE,
                           description="Target creature gains undying until EOT")
def undying_evil_resolve(game, card, controller, targets=None, item=None):
    from .cards import Keyword
    my_creatures = game.players[controller].creatures
    if my_creatures:
        best = max(my_creatures, key=lambda c: c.template.cmc)
        best.temp_keywords.add(Keyword.UNDYING)


# ═══════════════════════════════════════════════════════════════════
# Boros Energy creatures
# ═══════════════════════════════════════════════════════════════════

@EFFECT_REGISTRY.register("Guide of Souls", EffectTiming.ETB,
                           description="Get 1 energy")
def guide_of_souls_etb(game, card, controller, targets=None, item=None):
    game.produce_energy(controller, 1, "Guide of Souls")


@EFFECT_REGISTRY.register("Ocelot Pride", EffectTiming.ETB,
                           description="Get 1 energy")
def ocelot_pride_etb(game, card, controller, targets=None, item=None):
    game.produce_energy(controller, 1, "Ocelot Pride")


@EFFECT_REGISTRY.register("Ajani, Nacatl Pariah // Ajani, Nacatl Avenger",
                           EffectTiming.ETB,
                           description="Create 2/1 Cat Warrior token and get 2 energy")
def ajani_etb(game, card, controller, targets=None, item=None):
    game.create_token(controller, "cat", count=1, power=2, toughness=1)
    game.produce_energy(controller, 2, "Ajani")
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Ajani creates 2/1 Cat Warrior token and gains 2 energy")


@EFFECT_REGISTRY.register("Seasoned Pyromancer", EffectTiming.ETB,
                           description="Discard 2, draw 2, create tokens for nonland discards")
def seasoned_pyromancer_etb(game, card, controller, targets=None, item=None):
    player = game.players[controller]
    # Discard 2, draw 2 — count nonland discards for token creation
    discarded_nonland = 0
    for _ in range(min(2, len(player.hand))):
        if player.hand:
            # Discard worst card (lowest CMC land, or lowest priority spell)
            worst = min(player.hand, key=lambda c: (
                0 if c.template.is_land else 1,  # prefer discarding lands
                c.template.cmc or 0
            ))
            if not worst.template.is_land:
                discarded_nonland += 1
            player.hand.remove(worst)
            worst.zone = "graveyard"
            player.graveyard.append(worst)
    game.draw_cards(controller, 2)
    # Create 1/1 Elemental tokens for each nonland discarded
    if discarded_nonland > 0:
        game.create_token(controller, "elemental", count=discarded_nonland)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Seasoned Pyromancer: discard 2, draw 2, create {discarded_nonland} Elemental(s)")
    else:
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Seasoned Pyromancer: discard 2, draw 2 (no nonland discards)")


@EFFECT_REGISTRY.register("Ranger-Captain of Eos", EffectTiming.ETB,
                           description="Search library for creature with MV 1 or less")
def ranger_captain_etb(game, card, controller, targets=None, item=None):
    player = game.players[controller]
    # Find best 1-drop creature in library
    targets_in_lib = [
        c for c in player.library
        if c.template.is_creature and (c.template.cmc or 0) <= 1
    ]
    if targets_in_lib:
        # Prefer energy/value creatures
        best = max(targets_in_lib, key=lambda c: (
            1 if 'energy' in getattr(c.template, 'tags', set()) else 0,
            c.template.power or 0,
        ))
        player.library.remove(best)
        best.zone = "hand"
        player.hand.append(best)
        game.rng.shuffle(player.library)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Ranger-Captain tutors {best.name} to hand")
    else:
        game.rng.shuffle(player.library)


@EFFECT_REGISTRY.register("Ragavan, Nimble Pilferer", EffectTiming.ETB,
                           description="Create Treasure token on combat damage")
def ragavan_etb(game, card, controller, targets=None, item=None):
    # Ragavan's ETB does nothing — his ability triggers on combat damage.
    # The combat damage trigger is handled in trigger_combat_damage.
    pass


@EFFECT_REGISTRY.register("Summoner's Pact", EffectTiming.SPELL_RESOLVE,
                           description="Search for a green creature")
def summoners_pact_resolve(game, card, controller, targets=None, item=None):
    lib = game.players[controller].library
    green_creatures = [c for c in lib if c.template.is_creature and
                       any(ci.value == "G" for ci in c.template.color_identity)]
    if not green_creatures:
        green_creatures = [c for c in lib if c.template.is_creature]
    if green_creatures:
        best = max(green_creatures, key=lambda c: (c.template.power or 0))
        lib.remove(best)
        best.zone = "hand"
        game.players[controller].hand.append(best)
        game.rng.shuffle(lib)


@EFFECT_REGISTRY.register("Stock Up", EffectTiming.SPELL_RESOLVE,
                           description="Draw 2 cards")
def stock_up_resolve(game, card, controller, targets=None, item=None):
    game.draw_cards(controller, 2)


@EFFECT_REGISTRY.register("Orim's Chant", EffectTiming.SPELL_RESOLVE,
                           description="Opponent can't cast spells this turn")
def orims_chant_resolve(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    game.players[opponent].silenced_this_turn = True
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Orim's Chant silences opponent")


@EFFECT_REGISTRY.register("Mutagenic Growth", EffectTiming.SPELL_RESOLVE,
                           description="Target creature gets +2/+2, pay 2 life")
def mutagenic_growth_resolve(game, card, controller, targets=None, item=None):
    my_creatures = game.players[controller].creatures
    if my_creatures:
        best = max(my_creatures, key=lambda c: c.power or 0)
        best.temp_power_mod += 2
        best.temp_toughness_mod += 2
    game.players[controller].life -= 2


@EFFECT_REGISTRY.register("Violent Urge", EffectTiming.SPELL_RESOLVE,
                           description="Target creature gets +1/+0 and first strike, draw a card")
def violent_urge_resolve(game, card, controller, targets=None, item=None):
    from .cards import Keyword
    my_creatures = game.players[controller].creatures
    if my_creatures:
        best = max(my_creatures, key=lambda c: c.power or 0)
        best.temp_power_mod += 1
        best.keywords.add(Keyword.FIRST_STRIKE)
    game.draw_cards(controller, 1)


@EFFECT_REGISTRY.register("Expressive Iteration", EffectTiming.SPELL_RESOLVE,
                           description="Look at top 3, exile 1, put 1 in hand (simplified: draw 1)")
def expressive_iteration_resolve(game, card, controller, targets=None, item=None):
    game.draw_cards(controller, 1)


@EFFECT_REGISTRY.register("Preordain", EffectTiming.SPELL_RESOLVE,
                           description="Scry 2, draw 1 (simplified: draw 1)")
def preordain_resolve(game, card, controller, targets=None, item=None):
    game.draw_cards(controller, 1)


@EFFECT_REGISTRY.register("Tribal Flames", EffectTiming.SPELL_RESOLVE,
                           description="Deal damage equal to domain (basic land types)")
def tribal_flames_resolve(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    player = game.players[controller]
    land_types = set()
    for c in player.battlefield:
        if c.template.is_land:
            for st in c.template.subtypes:
                if st in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
                    land_types.add(st)
    damage = min(len(land_types), 5)
    if damage < 2:
        damage = 2
    game.players[opponent].life -= damage
    game.players[controller].damage_dealt_this_turn += damage


@EFFECT_REGISTRY.register("Wish", EffectTiming.SPELL_RESOLVE,
                           description="Get a card from sideboard (simplified: tutor)")
def wish_resolve(game, card, controller, targets=None, item=None):
    player = game.players[controller]
    sb = player.sideboard
    lib = player.library
    chosen = None
    from_zone = None

    # Smart priority: at low storm counts, Empty the Warrens is better
    # (creates 2*storm tokens that attack for lethal next turn)
    # Grapeshot needs storm >= opponent_life for instant kill
    opp_life = game.players[1 - controller].life
    current_storm = game._global_storm_count
    # Count ALL available fuel: hand + GY flashback
    fuel_in_hand = sum(1 for c in player.hand if not c.template.is_land
                       and c.name != 'Wish' and c.name != 'Past in Flames')
    fuel_in_gy = sum(1 for c in player.graveyard
                     if getattr(c, 'has_flashback', False)
                     and (c.template.is_instant or c.template.is_sorcery))
    total_fuel = fuel_in_hand + fuel_in_gy
    # After Wish resolves: Grapeshot is the next spell, then remaining fuel
    estimated_storm = current_storm + 1 + min(total_fuel, 8)

    # Grapeshot is the PRIMARY plan — it's an instant kill, no waiting a turn.
    # Only fall back to Empty the Warrens when Grapeshot can't get close.
    # "Close" means storm covers 60%+ of opponent's life.
    grapeshot_damage = estimated_storm  # 1 damage per copy
    if grapeshot_damage >= opp_life:
        # Lethal! Always Grapeshot.
        finisher_priority = ["Grapeshot", "Empty the Warrens", "Galvanic Relay"]
    elif grapeshot_damage >= opp_life * 0.6:
        # Close to lethal — Grapeshot still best (leaves opp at low life,
        # Ral/tokens finish next turn). Better than tokens that might get blocked.
        finisher_priority = ["Grapeshot", "Empty the Warrens", "Galvanic Relay"]
    else:
        # Not close — Empty creates a board that threatens lethal next turn
        finisher_priority = ["Empty the Warrens", "Grapeshot", "Galvanic Relay"]

    # Search sideboard first (real Wish behavior)
    if sb:
        for fname in finisher_priority:
            match = [c for c in sb if c.name == fname]
            if match:
                chosen = match[0]
                from_zone = "sideboard"
                break
        if not chosen:
            instants_sorceries = [c for c in sb if c.template.is_instant or c.template.is_sorcery]
            if instants_sorceries:
                chosen = max(instants_sorceries, key=lambda c: c.template.cmc)
                from_zone = "sideboard"

    # Fallback: search library
    if not chosen:
        for fname in finisher_priority:
            match = [c for c in lib if c.name == fname]
            if match:
                chosen = match[0]
                from_zone = "library"
                break
        if not chosen:
            instants_sorceries = [c for c in lib if c.template.is_instant or c.template.is_sorcery]
            if instants_sorceries:
                chosen = max(instants_sorceries, key=lambda c: c.template.cmc)
                from_zone = "library"

    if chosen:
        if from_zone == "sideboard":
            sb.remove(chosen)
        else:
            lib.remove(chosen)
            game.rng.shuffle(lib)
        chosen.zone = "hand"
        player.hand.append(chosen)
        game.log.append(f"T{game.turn_number} P{controller+1}: Wish finds {chosen.name} (from {from_zone})")


@EFFECT_REGISTRY.register("Gifts Ungiven", EffectTiming.SPELL_RESOLVE,
                           description="Search for 4 cards, opponent puts 2 in GY")
def gifts_ungiven_resolve(game, card, controller, targets=None, item=None):
    lib = game.players[controller].library
    candidates = [c for c in lib if c.template.is_instant or c.template.is_sorcery]
    candidates.sort(key=lambda c: (
        0 if c.name in ("Grapeshot", "Past in Flames") else 1,
        -c.template.cmc
    ))
    found = candidates[:2]
    for card_found in found:
        lib.remove(card_found)
        card_found.zone = "graveyard"
        game.players[controller].graveyard.append(card_found)
    game.rng.shuffle(lib)
    if found:
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Gifts Ungiven finds {', '.join(c.name for c in found)} to GY")


# ═══════════════════════════════════════════════════════════════════
# Missing Card Effects — Artifact/Enchantment Interaction
# ═══════════════════════════════════════════════════════════════════

@EFFECT_REGISTRY.register("Force of Vigor", EffectTiming.SPELL_RESOLVE,
                           description="Destroy up to 2 artifacts/enchantments")
def force_of_vigor_resolve(game, card, controller, targets=None, item=None):
    from .cards import CardType
    opponent = 1 - controller
    opp = game.players[opponent]
    # Find artifacts and enchantments on opponent's board
    valid_targets = [c for c in opp.battlefield
                     if not c.template.is_land and
                     (CardType.ARTIFACT in c.template.card_types or
                      CardType.ENCHANTMENT in c.template.card_types)]
    # Destroy up to 2, prioritizing highest value
    valid_targets.sort(key=lambda c: c.template.cmc, reverse=True)
    destroyed = 0
    for target in valid_targets[:2]:
        game._permanent_destroyed(target)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Force of Vigor destroys {target.name}")
        destroyed += 1
    if destroyed == 0:
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Force of Vigor: no valid targets")


@EFFECT_REGISTRY.register("Wear // Tear", EffectTiming.SPELL_RESOLVE,
                           description="Destroy target artifact and/or enchantment")
def wear_tear_resolve(game, card, controller, targets=None, item=None):
    from .cards import CardType
    opponent = 1 - controller
    opp = game.players[opponent]
    destroyed = 0
    # Wear: destroy best artifact
    artifacts = [c for c in opp.battlefield
                 if CardType.ARTIFACT in c.template.card_types]
    if artifacts:
        target = max(artifacts, key=lambda c: c.template.cmc)
        game._permanent_destroyed(target)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Wear // Tear destroys {target.name}")
        destroyed += 1
    # Tear: destroy best enchantment
    enchantments = [c for c in opp.battlefield
                    if CardType.ENCHANTMENT in c.template.card_types
                    and not c.template.is_creature]
    if enchantments:
        target = max(enchantments, key=lambda c: c.template.cmc)
        game._permanent_destroyed(target)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Wear // Tear destroys {target.name}")
        destroyed += 1
    if destroyed == 0:
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Wear // Tear: no valid targets")


@EFFECT_REGISTRY.register("Pick Your Poison", EffectTiming.SPELL_RESOLVE,
                           description="Opponent sacrifices artifact/enchantment or creature with flying/reach")
def pick_your_poison_resolve(game, card, controller, targets=None, item=None):
    from .cards import CardType, Keyword
    opponent = 1 - controller
    opp = game.players[opponent]
    # Choose mode: destroy artifact/enchantment if they have one, else creature with flying
    artifacts_enchantments = [c for c in opp.battlefield
                              if not c.template.is_land and
                              (CardType.ARTIFACT in c.template.card_types or
                               CardType.ENCHANTMENT in c.template.card_types)]
    flyers = [c for c in opp.creatures
              if Keyword.FLYING in c.keywords]

    if artifacts_enchantments:
        # Sacrifice highest value artifact/enchantment
        target = max(artifacts_enchantments, key=lambda c: c.template.cmc)
        game._permanent_destroyed(target)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Pick Your Poison: opponent sacrifices {target.name}")
    elif flyers:
        target = max(flyers, key=lambda c: c.power or 0)
        game._creature_dies(target)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Pick Your Poison: opponent sacrifices {target.name}")
    else:
        # Fallback: opponent loses 1 life (Toxic 1 mode)
        opp.life -= 1
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Pick Your Poison: opponent loses 1 life")


@EFFECT_REGISTRY.register("Meltdown", EffectTiming.SPELL_RESOLVE,
                           description="Destroy all artifacts with MV <= X")
def meltdown_resolve(game, card, controller, targets=None, item=None):
    from .cards import CardType
    # X = mana spent beyond R (cmc - 1)
    player = game.players[controller]
    x_val = max(0, card.template.cmc - 1)
    # Also consider extra mana spent
    available = len(player.untapped_lands)
    x_val = max(x_val, available)  # Simplified: spend all available mana

    for p in game.players:
        artifacts = [c for c in p.battlefield
                     if CardType.ARTIFACT in c.template.card_types
                     and c.template.cmc <= x_val]
        for artifact in artifacts:
            game._permanent_destroyed(artifact)
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Meltdown (X={x_val}) destroys artifacts with MV <= {x_val}")


@EFFECT_REGISTRY.register("Engineered Explosives", EffectTiming.ETB,
                           description="Enters with sunburst counters")
def engineered_explosives_etb(game, card, controller, targets=None, item=None):
    # Count colors of mana spent (simplified: count unique colors in lands)
    player = game.players[controller]
    colors = set()
    for land in player.lands:
        for c in (land.template.produces_mana or []):
            colors.add(c)
    # Set charge counters = number of colors (max 5)
    card.other_counters["charge"] = min(len(colors), 5)
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Engineered Explosives enters with {card.other_counters['charge']} charge counters")


@EFFECT_REGISTRY.register("Kolaghan's Command", EffectTiming.SPELL_RESOLVE,
                           description="Choose 2: deal 2 damage, destroy artifact, discard, return creature from GY")
def kolaghans_command_resolve(game, card, controller, targets=None, item=None):
    from .cards import CardType
    opponent = 1 - controller
    opp = game.players[opponent]

    # Mode selection: destroy artifact if available, else deal 2 damage
    artifacts = [c for c in opp.battlefield
                 if CardType.ARTIFACT in c.template.card_types]
    if artifacts:
        target = max(artifacts, key=lambda c: c.template.cmc)
        game._permanent_destroyed(target)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Kolaghan's Command destroys {target.name}")
    else:
        # Deal 2 damage to opponent
        opp.life -= 2
        game.players[controller].damage_dealt_this_turn += 2
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Kolaghan's Command deals 2 to opponent")

    # Second mode: return creature from GY or force discard
    player = game.players[controller]
    gy_creatures = [c for c in player.graveyard if c.template.is_creature]
    if gy_creatures:
        best = max(gy_creatures, key=lambda c: (c.template.power or 0))
        player.graveyard.remove(best)
        best.zone = "hand"
        player.hand.append(best)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Kolaghan's Command returns {best.name} from GY")
    else:
        game._force_discard(opponent, 1)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Kolaghan's Command: opponent discards")


@EFFECT_REGISTRY.register("Abrupt Decay", EffectTiming.SPELL_RESOLVE,
                           description="Destroy target nonland permanent with MV 3 or less")
def abrupt_decay_resolve(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    opp = game.players[opponent]
    valid = [c for c in opp.battlefield
             if not c.template.is_land and c.template.cmc <= 3]
    if valid:
        # Pick highest value target
        target = max(valid, key=lambda c: c.template.cmc + (c.power or 0))
        game._permanent_destroyed(target)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Abrupt Decay destroys {target.name}")


@EFFECT_REGISTRY.register("Assassin's Trophy", EffectTiming.SPELL_RESOLVE,
                           description="Destroy target permanent, opponent searches for basic land")
def assassins_trophy_resolve(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    opp = game.players[opponent]
    if targets:
        for tid in targets:
            target = game.get_card_by_id(tid)
            if target and target.zone == "battlefield":
                game._permanent_destroyed(target)
                game.log.append(f"T{game.turn_number} P{controller+1}: "
                                f"Assassin's Trophy destroys {target.name}")
    else:
        # Auto-target: highest value nonland permanent
        nonlands = [c for c in opp.battlefield if not c.template.is_land]
        if nonlands:
            target = max(nonlands, key=lambda c: c.template.cmc + (c.power or 0))
            game._permanent_destroyed(target)
            game.log.append(f"T{game.turn_number} P{controller+1}: "
                            f"Assassin's Trophy destroys {target.name}")
    # Opponent gets a basic land (simplified: just a small benefit)
    # In practice this is a downside but we don't model basic land search


@EFFECT_REGISTRY.register("Fatal Push", EffectTiming.SPELL_RESOLVE,
                           description="Destroy creature with MV 2 or less (4 with revolt)")
def fatal_push_resolve(game, card, controller, targets=None, item=None):
    from .cards import Keyword
    opponent = 1 - controller
    opp = game.players[opponent]
    # Check revolt (simplified: did a permanent leave this turn?)
    has_revolt = game.players[controller].creatures_died_this_turn > 0
    max_cmc = 4 if has_revolt else 2

    if targets:
        for tid in targets:
            target = game.get_card_by_id(tid)
            if (target and target.zone == "battlefield" and
                target.template.is_creature and target.template.cmc <= max_cmc and
                Keyword.INDESTRUCTIBLE not in target.keywords):
                game._creature_dies(target)
                game.log.append(f"T{game.turn_number} P{controller+1}: "
                                f"Fatal Push destroys {target.name}")
                return
    # Auto-target
    valid = [c for c in opp.creatures
             if c.template.cmc <= max_cmc and
             Keyword.INDESTRUCTIBLE not in c.keywords]
    if valid:
        target = max(valid, key=lambda c: c.power or 0)
        game._creature_dies(target)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Fatal Push destroys {target.name}")


@EFFECT_REGISTRY.register("Leyline Binding", EffectTiming.ETB,
                           description="Exile target nonland permanent")
def leyline_binding_etb(game, card, controller, targets=None, item=None):
    opponent = 1 - controller
    opp = game.players[opponent]
    nonlands = [c for c in opp.battlefield if not c.template.is_land]
    if nonlands:
        target = max(nonlands, key=lambda c: c.template.cmc + (c.power or 0))
        game._exile_permanent(target)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Leyline Binding exiles {target.name}")



# ═══════════════════════════════════════════════════════════════════
# Goryo's Vengeance package
# ═══════════════════════════════════════════════════════════════════

@EFFECT_REGISTRY.register("Archon of Cruelty", EffectTiming.ETB,
                           description="Opponent discards, sacrifices, loses 3 life; you draw, gain 3 life")
def archon_of_cruelty_etb(game, card, controller, targets=None, item=None):
    """Archon of Cruelty ETB: opponent sacrifices a creature or planeswalker,
    discards a card, and loses 3 life. You draw a card and gain 3 life."""
    opponent = 1 - controller
    opp = game.players[opponent]

    # Opponent sacrifices a creature or planeswalker
    from .cards import CardType
    sac_targets = [c for c in opp.battlefield
                   if CardType.CREATURE in c.template.card_types
                   or CardType.PLANESWALKER in c.template.card_types]
    if sac_targets:
        # Sacrifice the least valuable
        target = min(sac_targets, key=lambda c: (c.template.cmc, c.power or 0))
        opp.battlefield.remove(target)
        target.zone = "graveyard"
        game.players[target.owner].graveyard.append(target)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Archon of Cruelty: P{opponent+1} sacrifices {target.name}")

    # Opponent discards a card
    if opp.hand:
        # Discard the worst card (lowest CMC non-land)
        discard = min(opp.hand, key=lambda c: c.template.cmc if not c.template.is_land else 99)
        opp.hand.remove(discard)
        discard.zone = "graveyard"
        game.players[discard.owner].graveyard.append(discard)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Archon of Cruelty: P{opponent+1} discards {discard.name}")

    # Opponent loses 3 life
    opp.life -= 3
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Archon of Cruelty: P{opponent+1} loses 3 life (now {opp.life})")

    # You draw a card
    game.draw_cards(controller, 1)

    # You gain 3 life
    game.gain_life(controller, 3, "Archon of Cruelty")
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Archon of Cruelty: draw 1, gain 3 life (now {game.players[controller].life})")

    # Check if opponent is dead
    if opp.life <= 0:
        game.game_over = True
        game.winner = controller


# ═══════════════════════════════════════════════════════════════════
# Amulet Titan package
# ═══════════════════════════════════════════════════════════════════

@EFFECT_REGISTRY.register("Arboreal Grazer", EffectTiming.ETB,
                           description="Put a land from hand onto the battlefield tapped")
def arboreal_grazer_etb(game, card, controller, targets=None, item=None):
    """Arboreal Grazer: ETB put a land from hand onto battlefield tapped."""
    player = game.players[controller]
    lands_in_hand = [c for c in player.hand if c.template.is_land]
    if lands_in_hand:
        # Pick the best land to put in (bounce lands > basics)
        bounce_lands = [c for c in lands_in_hand
                        if c.template.enters_tapped and c.template.produces_mana]
        land = bounce_lands[0] if bounce_lands else lands_in_hand[0]
        player.hand.remove(land)
        land.enter_battlefield()
        land.controller = controller
        player.battlefield.append(land)
        # Amulet of Vigor can untap it
        if land.tapped:
            amulet_count = sum(1 for c in player.battlefield
                               if c.template.name == "Amulet of Vigor")
            if amulet_count > 0:
                land.tapped = False
                game.log.append(f"T{game.turn_number} P{controller+1}: "
                                f"Amulet of Vigor untaps {land.name}")
        game._trigger_landfall(controller)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Arboreal Grazer puts {land.name} onto battlefield")


@EFFECT_REGISTRY.register("Primeval Titan", EffectTiming.ETB,
                           description="Search library for 2 lands and put them onto battlefield tapped")
def primeval_titan_etb(game, card, controller, targets=None, item=None):
    """Primeval Titan: ETB search for 2 lands, put them onto battlefield tapped."""
    _primeval_titan_search(game, controller)


def _primeval_titan_search(game, controller):
    """Shared logic for Primeval Titan ETB and attack trigger."""
    player = game.players[controller]
    lands_in_library = [c for c in player.library if c.template.is_land]
    if not lands_in_library:
        return

    # Priority: bounce lands (for mana with Amulet), utility lands, basics
    def land_priority(c):
        score = 0
        if c.template.enters_tapped and c.template.produces_mana:
            score += 10  # bounce lands are best with Amulet
        if "Valakut" in c.name:
            score += 8  # Valakut for damage
        if c.name in ("Slayers' Stronghold", "Sunhome, Fortress of the Legion",
                       "Boros Garrison", "Hanweir Battlements // Hanweir, the Writhing Township"):
            score += 7  # haste/double strike enablers
        if c.template.produces_mana:
            score += 3
        return score

    lands_in_library.sort(key=land_priority, reverse=True)
    to_put = lands_in_library[:2]

    for land in to_put:
        player.library.remove(land)
        land.enter_battlefield()
        land.controller = controller
        player.battlefield.append(land)
        # Amulet of Vigor untaps
        if land.tapped:
            amulet_count = sum(1 for c in player.battlefield
                               if c.template.name == "Amulet of Vigor")
            if amulet_count > 0:
                land.tapped = False
                game.log.append(f"T{game.turn_number} P{controller+1}: "
                                f"Amulet of Vigor untaps {land.name}")
        game._trigger_landfall(controller)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Primeval Titan searches for {land.name}")

    # Shuffle library
    game.rng.shuffle(player.library)


# ═══════════════════════════════════════════════════════════════════
# Dimir Midrange package
# ═══════════════════════════════════════════════════════════════════

@EFFECT_REGISTRY.register("Wan Shi Tong, Librarian", EffectTiming.ETB,
                           description="Put X +1/+1 counters, draw half X cards (X = opponent's library searches)")
def wan_shi_tong_etb(game, card, controller, targets=None, item=None):
    """Wan Shi Tong enters with X +1/+1 counters where X = opponent searches."""
    opponent = 1 - controller
    x = game.players[opponent].library_searches_this_game
    if x > 0:
        card.plus_counters += x
        draw_count = x // 2
        if draw_count > 0:
            game.draw_cards(controller, draw_count)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Wan Shi Tong enters with {x} +1/+1 counters "
                        f"({card.power}/{card.toughness}), draws {draw_count} cards")
    else:
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Wan Shi Tong enters (no opponent searches yet)")


@EFFECT_REGISTRY.register("Sanctifier en-Vec", EffectTiming.ETB,
                           description="Exile all black and red cards from all graveyards")
def sanctifier_en_vec_etb(game, card, controller, targets=None, item=None):
    """Sanctifier en-Vec: exile all black/red cards from all graveyards."""
    exiled = 0
    for p in game.players:
        to_exile = [c for c in p.graveyard
                    if c.template.color_identity & {"B", "R"}]
        for c in to_exile:
            p.graveyard.remove(c)
            c.zone = "exile"
            p.exile.append(c)
            exiled += 1
    if exiled > 0:
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Sanctifier en-Vec exiles {exiled} black/red cards from graveyards")


@EFFECT_REGISTRY.register("Orcish Bowmasters", EffectTiming.ETB,
                           description="Deal 1 damage to any target, create Orc Army token")
def orcish_bowmasters_etb(game, card, controller, targets=None, item=None):
    """Orcish Bowmasters: ETB deal 1 damage + create 1/1 Orc Army token."""
    opponent = 1 - controller
    opp = game.players[opponent]

    # Deal 1 damage to best target (creature or player)
    if opp.creatures:
        # Target the weakest creature we can kill, or the strongest threat
        one_toughness = [c for c in opp.creatures if c.toughness <= 1]
        if one_toughness:
            target = max(one_toughness, key=lambda c: c.power)
            target.damage_marked += 1
            game.log.append(f"T{game.turn_number} P{controller+1}: "
                            f"Bowmasters deals 1 damage to {target.name}")
        else:
            opp.life -= 1
            game.players[controller].damage_dealt_this_turn += 1
            game.log.append(f"T{game.turn_number} P{controller+1}: "
                            f"Bowmasters deals 1 damage to opponent (life: {opp.life})")
    else:
        opp.life -= 1
        game.players[controller].damage_dealt_this_turn += 1
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Bowmasters deals 1 damage to opponent (life: {opp.life})")

    # Create a 1/1 Orc Army token (simplified — in real MTG it grows)
    from .cards import CardTemplate, CardType, ManaCost
    token_template = CardTemplate(
        name="Orc Army",
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(0, 0, 0, 0, 0, 0),
        power=1,
        toughness=1,
        tags={"creature", "token"},
    )
    from .cards import CardInstance
    token = CardInstance(
        template=token_template, owner=controller,
        controller=controller, instance_id=game.next_instance_id(),
    )
    token._game_state = game
    token.enter_battlefield()
    game.players[controller].battlefield.append(token)
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Bowmasters creates 1/1 Orc Army token")


@EFFECT_REGISTRY.register("Psychic Frog", EffectTiming.ETB,
                           description="Psychic Frog enters (discard-to-pump tracked via tags)")
def psychic_frog_etb(game, card, controller, targets=None, item=None):
    """Psychic Frog: mark it so combat can pump it by discarding."""
    card.instance_tags.add("psychic_frog")


@EFFECT_REGISTRY.register("Damnation", EffectTiming.SPELL_RESOLVE,
                           description="Destroy all creatures")
def damnation_resolve(game, card, controller, targets=None, item=None):
    """Damnation: destroy all creatures."""
    all_creatures = []
    for p in game.players:
        all_creatures.extend(p.creatures)
    for creature in all_creatures:
        game._permanent_destroyed(creature)
    game.log.append(f"T{game.turn_number} P{controller+1}: "
                    f"Damnation destroys {len(all_creatures)} creatures")


@EFFECT_REGISTRY.register("Sheoldred, the Apocalypse", EffectTiming.ETB,
                           description="Sheoldred enters — life drain on card draw tracked via static")
def sheoldred_etb(game, card, controller, targets=None, item=None):
    """Sheoldred: mark presence for draw triggers (gain 2 on own draw, opp loses 2)."""
    card.instance_tags.add("sheoldred")


@EFFECT_REGISTRY.register("Walking Ballista", EffectTiming.ETB,
                           description="Walking Ballista enters with X +1/+1 counters, can ping creatures/players")
def walking_ballista_etb(game, card, controller, targets=None, item=None):
    """Walking Ballista: on ETB, use all counters to deal damage to best target.
    
    In real MTG this is an activated ability, but for simulation we fire all
    counters at the best target when it enters (since the AI would do this anyway
    in most cases — Ballista is primarily used as removal/reach).
    """
    counters = card.plus_counters
    if counters <= 0:
        return  # X=0, no damage to deal
    
    opponent = 1 - controller
    opp = game.players[opponent]
    
    # Find best creature target that can be killed
    killable = [c for c in opp.creatures 
                if (c.toughness or 0) <= counters and c.toughness and c.toughness > 0]
    
    if killable:
        # Kill the highest-value creature we can
        target = max(killable, key=lambda c: (c.template.cmc, c.power or 0))
        damage_to_creature = target.toughness  # exact lethal
        remaining = counters - damage_to_creature
        
        # Remove counters used and deal damage
        card.plus_counters -= damage_to_creature
        target.damage_marked += damage_to_creature
        if target.damage_marked >= (target.toughness or 0):
            game._permanent_destroyed(target)
        game.log.append(
            f"T{game.turn_number} P{controller+1}: "
            f"Walking Ballista deals {damage_to_creature} to {target.name}")
        
        # Send remaining damage to opponent's face
        if remaining > 0:
            card.plus_counters -= remaining
            opp.life -= remaining
            game.players[controller].damage_dealt_this_turn += remaining
            game.log.append(
                f"T{game.turn_number} P{controller+1}: "
                f"Walking Ballista deals {remaining} to opponent "
                f"(life: {opp.life})")
    else:
        # No killable creature — all damage to face
        card.plus_counters = 0
        opp.life -= counters
        game.players[controller].damage_dealt_this_turn += counters
        game.log.append(
            f"T{game.turn_number} P{controller+1}: "
            f"Walking Ballista deals {counters} to opponent "
            f"(life: {opp.life})")


# ═══════════════════════════════════════════════════════════════════
# Phase 2D: Migrated from _execute_spell_effects legacy code
# ═══════════════════════════════════════════════════════════════════

@EFFECT_REGISTRY.register("Griselbrand", EffectTiming.SPELL_RESOLVE,
                           description="No-op on cast (activated ability handled separately)")
def griselbrand_resolve(game, card, controller, targets=None, item=None):
    # Griselbrand's draw-7 is an activated ability, not a cast trigger
    pass


@EFFECT_REGISTRY.register("Sleight of Hand", EffectTiming.SPELL_RESOLVE,
                           description="Look at top 2, keep 1")
def sleight_of_hand_resolve(game, card, controller, targets=None, item=None):
    game.draw_cards(controller, 1)


@EFFECT_REGISTRY.register("Reckless Impulse", EffectTiming.SPELL_RESOLVE,
                           description="Exile top 2, may play until end of next turn")
def reckless_impulse_resolve(game, card, controller, targets=None, item=None):
    # Simplified: draw 2 cards (exile-draw approximation)
    game.draw_cards(controller, 2)


@EFFECT_REGISTRY.register("Wrenn's Resolve", EffectTiming.SPELL_RESOLVE,
                           description="Exile top 2, may play until end of next turn")
def wrenns_resolve_resolve(game, card, controller, targets=None, item=None):
    # Simplified: draw 2 cards (exile-draw approximation)
    game.draw_cards(controller, 2)


@EFFECT_REGISTRY.register("Heroes' Hangout", EffectTiming.SPELL_RESOLVE,
                           description="Scry 2, draw 1")
def heroes_hangout_resolve(game, card, controller, targets=None, item=None):
    game.draw_cards(controller, 1)


@EFFECT_REGISTRY.register("Glimpse the Impossible", EffectTiming.SPELL_RESOLVE,
                           description="Exile top 3, may play this turn")
def glimpse_the_impossible_resolve(game, card, controller, targets=None, item=None):
    # Simplified: draw 2 cards (exile-play approximation)
    game.draw_cards(controller, 2)


@EFFECT_REGISTRY.register("Valakut Awakening // Valakut Stoneforge", EffectTiming.SPELL_RESOLVE,
                           description="Put cards from hand on bottom, draw that many + 1")
def valakut_awakening_resolve(game, card, controller, targets=None, item=None):
    # Simplified: draw 3 (redraw hand minus best cards)
    player = game.players[controller]
    hand_size = len(player.hand)
    # Put worst cards on bottom, keep best, draw replacements
    # Simplified: draw 2 (net card selection)
    game.draw_cards(controller, 2)


@EFFECT_REGISTRY.register("March of Reckless Joy", EffectTiming.SPELL_RESOLVE,
                           description="Exile top cards equal to cards exiled + 2")
def march_of_reckless_joy_resolve(game, card, controller, targets=None, item=None):
    # Simplified: draw 2 (exile-play)
    game.draw_cards(controller, 2)


# ═══════════════════════════════════════════════════════════════════
# Amulet Titan effects
# ═══════════════════════════════════════════════════════════════════

@EFFECT_REGISTRY.register("Explore", EffectTiming.SPELL_RESOLVE,
                           description="Draw a card, may play an additional land this turn")
def explore_resolve(game, card, controller, targets=None, item=None):
    game.draw_cards(controller, 1)
    game.players[controller].extra_land_drops += 1
    game.log.append(f"T{game.turn_number} P{controller+1}: Explore — draw 1, extra land drop")


@EFFECT_REGISTRY.register("Green Sun's Zenith", EffectTiming.SPELL_RESOLVE,
                           description="Search library for green creature CMC <= X, put onto battlefield")
def green_suns_zenith_resolve(game, card, controller, targets=None, item=None):
    from .cards import Color
    player = game.players[controller]
    x_value = getattr(card, '_x_value', 0) or 0
    # Find best green creature with CMC <= X
    candidates = [
        c for c in player.library
        if c.template.is_creature
        and Color.GREEN in c.template.color_identity
        and (c.template.cmc or 0) <= x_value
    ]
    if candidates:
        best = max(candidates, key=lambda c: (c.template.power or 0) + (c.template.toughness or 0))
        player.library.remove(best)
        game.rng.shuffle(player.library)
        best.controller = controller
        best.enter_battlefield()
        player.battlefield.append(best)
        game._handle_permanent_etb(best, controller)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Green Sun's Zenith finds {best.name}")
    # Shuffle GSZ back into library (unique to this card)
    # card is already in graveyard at this point; move it to library
    if card in player.graveyard:
        player.graveyard.remove(card)
        card.zone = "library"
        player.library.append(card)
        game.rng.shuffle(player.library)


@EFFECT_REGISTRY.register("Summoner's Pact", EffectTiming.SPELL_RESOLVE,
                           description="Search library for a green creature, put into hand")
def summoners_pact_resolve(game, card, controller, targets=None, item=None):
    from .cards import Color
    player = game.players[controller]
    candidates = [
        c for c in player.library
        if c.template.is_creature
        and Color.GREEN in c.template.color_identity
    ]
    if candidates:
        best = max(candidates, key=lambda c: (c.template.power or 0) + (c.template.toughness or 0))
        player.library.remove(best)
        game.rng.shuffle(player.library)
        best.zone = "hand"
        player.hand.append(best)
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Summoner's Pact finds {best.name}")


# ═══════════════════════════════════════════════════════════════════
# Jeskai Blink / Control effects
# ═══════════════════════════════════════════════════════════════════

# Teferi, Time Raveler: NO ETB handler needed.
# His -3 (bounce + draw) is a loyalty ability handled by PLANESWALKER_ABILITIES
# in game_state.py. Adding a fake ETB here was a bug — it caused double-bounce.


@EFFECT_REGISTRY.register("Snapcaster Mage", EffectTiming.ETB,
                           description="Give flashback to an instant or sorcery in graveyard")
def snapcaster_mage_etb(game, card, controller, targets=None, item=None):
    player = game.players[controller]
    candidates = [c for c in player.graveyard
                  if (c.template.is_instant or c.template.is_sorcery)
                  and not getattr(c, 'has_flashback', False)]
    if candidates:
        # Pick best: removal > counterspell > draw > other, then by CMC
        def snap_priority(c):
            tags = getattr(c.template, 'tags', set())
            if 'removal' in tags: return (3, c.template.cmc or 0)
            if 'counterspell' in tags: return (2, c.template.cmc or 0)
            if 'cantrip' in tags: return (1, c.template.cmc or 0)
            return (0, c.template.cmc or 0)
        best = max(candidates, key=snap_priority)
        best.has_flashback = True
        game.log.append(f"T{game.turn_number} P{controller+1}: "
                        f"Snapcaster gives flashback to {best.name}")


@EFFECT_REGISTRY.register("Wall of Omens", EffectTiming.ETB,
                           description="Draw a card")
def wall_of_omens_etb(game, card, controller, targets=None, item=None):
    game.draw_cards(controller, 1)


@EFFECT_REGISTRY.register("Spell Queller", EffectTiming.ETB,
                           description="Exile target spell with CMC 4 or less from stack")
def spell_queller_etb(game, card, controller, targets=None, item=None):
    # Simplified: exile top spell from stack if CMC <= 4
    # Only works when there's actually a spell on the stack (not from blink ETB)
    if not game.stack.is_empty:
        top = game.stack.top
        if top and top.source and (top.source.template.cmc or 0) <= 4:
            spell_card = top.source
            game.stack.pop()
            spell_card.zone = "exile"
            game.players[spell_card.owner].exile.append(spell_card)
            game.log.append(f"T{game.turn_number} P{controller+1}: "
                            f"Spell Queller exiles {spell_card.name}")
