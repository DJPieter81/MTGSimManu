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
import re
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
# Threat Scoring — smart targeting for removal/interaction
# ═══════════════════════════════════════════════════════════════════

def _threat_score(card, game=None, owner=None) -> float:
    """Score a permanent by actual game threat, not CMC.

    Used by removal / interaction effects to pick the best target.
    Higher = more threatening = should be removed first.

    SINGLE-SCALE INVARIANT: when `game` and `owner` are supplied, every
    permanent — creature OR non-creature — is scored via the marginal-
    contribution formula `ai.permanent_threat.permanent_threat`.  That
    formula returns the drop in `owner`'s position value when the card
    is removed; it covers raw clock impact, evasion, equipment carriers,
    scaling artifacts, and mana rocks under one definition.

    The previous split (creatures → `creature_threat_value`,
    non-creatures → `permanent_threat`) mixed two unit systems
    (clock_impact*20 vs position-value delta), so cross-type rankings
    such as "Plating-equipped Memnite (artifact creature) vs Springleaf
    Drum (artifact)" became category errors and let removal walk past
    the actual win condition.  See
    `tests/test_removal_targeting_picks_creature_over_mana_rock.py`
    for the rule each line of this function pins.

    Ward (creature-only): a removal-targeting tax subtracted on the
    same scale via `mana_clock_impact * 20.0`.

    When context is unavailable (legacy callsites that don't have the
    game in scope), fall back to oracle-driven creature heuristic for
    creatures and a neutral CMC proxy for non-creatures so scoring
    remains monotonic without requiring game state.
    """
    t = card.template

    # Preferred path: marginal-contribution formula on a single scale.
    if game is not None and owner is not None:
        from ai.permanent_threat import permanent_threat
        base = permanent_threat(card, owner, game)
        if t.is_creature and t.ward_cost > 0:
            from ai.clock import mana_clock_impact
            from ai.ev_evaluator import BASELINE_SNAPSHOT
            base -= t.ward_cost * mana_clock_impact(BASELINE_SNAPSHOT) * 20.0
        return base

    # Context-free fallback path (legacy callsites with no game in
    # scope): BASELINE_SNAPSHOT is the correct, explicit choice here —
    # there is no live board to build a real snapshot from.
    if t.is_creature:
        from ai.ev_evaluator import creature_threat_value, BASELINE_SNAPSHOT
        base = creature_threat_value(card, BASELINE_SNAPSHOT)
        if t.ward_cost > 0:
            from ai.clock import mana_clock_impact
            base -= t.ward_cost * mana_clock_impact(BASELINE_SNAPSHOT) * 20.0
        return base
    return float(t.cmc or 0)


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
    target = max(opp_creatures, key=_threat_score)
    life_gain = target.power or 0
    game._exile_permanent(target)
    game.players[opponent].life += life_gain
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Solitude exiles {target.name} "
                    f"(opponent gains {life_gain} life, now {game.players[opponent].life})")


@EFFECT_REGISTRY.register("Subtlety", EffectTiming.ETB,
                           description="Put target creature spell or planeswalker spell on top/bottom of library")
def subtlety_etb(game, card, controller, targets=None, item=None):
    # Oracle: "choose up to one target creature spell or planeswalker spell.
    # Its owner puts it on their choice of the top or bottom of their library."
    # This targets SPELLS ON THE STACK, not permanents on the battlefield.
    # If there's a creature/PW spell on the stack, put it on top of library.
    opponent = 1 - controller
    if not game.stack.is_empty:
        # Check stack for creature or planeswalker spells from opponent
        from engine.cards import CardType
        for i in range(len(game.stack.items) - 1, -1, -1):
            item_on_stack = game.stack.items[i]
            if item_on_stack.controller != opponent:
                continue
            t = item_on_stack.source.template
            if CardType.CREATURE in t.card_types or CardType.PLANESWALKER in t.card_types:
                # Remove from stack, put on top of library
                removed = game.stack.items.pop(i)
                removed.source.zone = "library"
                game.players[opponent].library.insert(0, removed.source)
                game.log.append(f"T{game.display_turn} P{controller+1}: "
                                f"Subtlety puts {removed.source.name} on top of library (from stack)")
                return
    # No valid spell on stack — ETB fizzles (up to one = optional)
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Subtlety enters (no creature/PW spell on stack to target)")


@EFFECT_REGISTRY.register("Endurance", EffectTiming.ETB,
                           description="Target player puts graveyard on bottom of library in random order")
def endurance_etb(game, card, controller, targets=None, item=None):
    # Oracle: "up to one target player puts all the cards from their graveyard
    # on the bottom of their library in a random order."
    # Key: BOTTOM of library, random order. Library top is PRESERVED.
    opponent = 1 - controller
    target_idx = opponent if game.players[opponent].graveyard else controller
    target_player = game.players[target_idx]
    gy_count = len(target_player.graveyard)
    if gy_count > 0:
        gy_cards = list(target_player.graveyard)
        target_player.graveyard.clear()
        game.rng.shuffle(gy_cards)  # random order for the GY cards only
        for card_gy in gy_cards:
            card_gy.zone = "library"
            target_player.library.append(card_gy)  # append = bottom
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Endurance ETB: P{target_idx+1} shuffles {gy_count} cards "
                        f"from GY into library")


# "Omnath, Locus of Creation" (ETB: draw a card; the +4 life is the
# 1st LANDFALL trigger, a separate ability, not ETB) used to be
# registered here. Removed as part of the EFFECT_REGISTRY draw-N ETB
# cluster consolidation (docs/design/rules-foundation-sweep-tracker.md,
# Phase 3) — its one-line body ("When Omnath enters, draw a card.") is
# now covered end-to-end by the generic
# `engine.oracle_resolver.resolve_etb_from_oracle` fallback's
# "when ~ enters, draw N card(s)" branch, confirmed redundant by
# `tests/test_etb_draw_n_shared_resolver.py::TestGenericResolverCoversRegisteredHandlers`
# before deletion.


@EFFECT_REGISTRY.register("Murktide Regent", EffectTiming.ETB,
                           description="Delve instants/sorceries from GY, enter with +1/+1 counters")
def murktike_etb(game, card, controller, targets=None, item=None):
    # Oracle: "This creature enters with a +1/+1 counter on it for each
    # instant and sorcery card exiled with it."
    # These are PERMANENT +1/+1 counters, not temp mods that reset at cleanup.
    delved_spells = getattr(card, '_delved_spells', 0)
    if delved_spells > 0:
        card.plus_counters += delved_spells
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Murktide Regent enters as {card.power}/{card.toughness}"
                    f" ({delved_spells} +1/+1 counters from delved instants/sorceries)")


# "Quantum Riddler" (ETB: draw a card; a separate static ability —
# "As long as you have one or fewer cards in hand, if you would draw
# one or more cards, you draw that many cards plus one instead." —
# is its own effect, not part of the ETB trigger) used to be
# registered here. Removed as part of the EFFECT_REGISTRY draw-N ETB
# cluster consolidation (docs/design/rules-foundation-sweep-tracker.md,
# Phase 3) — its one-line body ("When this creature enters, draw a
# card.") is now covered end-to-end by the generic
# `engine.oracle_resolver.resolve_etb_from_oracle` fallback's
# "when ~ enters, draw N card(s)" branch, confirmed redundant by
# `tests/test_etb_draw_n_shared_resolver.py::TestGenericResolverCoversRegisteredHandlers`
# before deletion.


@EFFECT_REGISTRY.register("Mox Opal", EffectTiming.ETB,
                           description="Metalcraft is evaluated dynamically at tap time")
def mox_opal_etb(game, card, controller, targets=None, item=None):
    """No-op ETB.

    E1 fix: metalcraft (CR 702.98) is checked at activation time, not at
    ETB time.  The previous handler mutated `card.template` to
    permanently grant `produces_mana=WUBRG` whenever metalcraft happened
    to be live on entry, which violated the rules in two directions:
    (1) Mox kept producing 5 colours after artifact count fell below 3,
    (2) Mox stayed dead even after artifact count later reached 3.

    The dynamic check now lives in
    `GameState._effective_produces_mana()` and is consulted by every
    mana-source iteration site.  This handler is intentionally empty
    so that no template-mutation side effect can re-introduce the
    snapshot bug.
    """
    return


@EFFECT_REGISTRY.register("Cranial Plating", EffectTiming.ETB,
                           description="Enters battlefield unattached. Equip {1}.")
def cranial_plating_etb(game, card, controller, targets=None, item=None):
    # In real MTG, equipment enters unattached.
    # The AI must spend mana to equip via the "equip" action in main phase.
    # Mark this card as equipment so the game knows it can be equipped.
    card.instance_tags.add("equipment_unattached")
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Cranial Plating enters the battlefield (unattached)")


@EFFECT_REGISTRY.register("Nettlecyst", EffectTiming.ETB,
                           description="Create Germ token, equip it")
def nettlecyst_etb(game, card, controller, targets=None, item=None):
    game.create_token(
        controller, "germ", count=1,
        source_oracle=card.template.oracle_text,
    )
    germs = [c for c in game.players[controller].creatures
             if "Germ" in c.name]
    if germs:
        # Use instance_id-based tag so the generic equipment scaling in _dynamic_base_power
        # and _dynamic_base_toughness picks it up correctly.
        germs[-1].instance_tags.add(f"equipped_{card.instance_id}")


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


# NOTE: "Phlage, Titan of Fire's Fury" used to be registered here
# (ETB: sacrifice unless escaped; deal 3 damage to any target; gain 3
# life). It was removed after research for the "deal N damage to any
# target" EFFECT_REGISTRY cluster (see
# docs/design/rules-foundation-sweep-tracker.md, Phase 3) found the
# card itself is no longer in ModernAtomic — it was removed entirely
# by the MTGJSON refresh that followed its Modern ban (commit
# d02c543, already on main before this branch). No CardInstance can
# ever carry that literal name, so the handler was unreachable dead
# code (grepped: no test constructs a card with this exact name
# either). Deleted rather than migrated — same class-size/dead-code
# reasoning this program applies elsewhere. If Phlage (or a
# functional reprint) re-enters the pool, re-add a handler that
# routes its damage step through
# `engine.oracle_resolver.resolve_damage_to_chosen_target` rather
# than restoring the old raw-mutation version.


# ═══════════════════════════════════════════════════════════════════
# Spell Resolution Effects
# ═══════════════════════════════════════════════════════════════════

# The next three handlers (Lightning Bolt, Lava Dart, Unholy Heat) —
# plus Grapeshot, registered further below — are all instances of one
# mechanic shape: "deal N damage to any target". Each used to
# re-implement its own copy of "walk the declared target list, apply
# damage to the first eligible creature/planeswalker, else go face."
# That inline duplication is now `engine.oracle_resolver.
# resolve_damage_to_chosen_target`, the single shared owner (routes
# through `engine.damage.deal_damage` so lifelink/deathtouch/SBA
# scheduling are correct by construction). See
# docs/design/rules-foundation-sweep-tracker.md (Phase 3) for the
# full cluster research — which cards were included/excluded and why.
@EFFECT_REGISTRY.register("Lightning Bolt", EffectTiming.SPELL_RESOLVE,
                           description="Deal 3 damage to any target")
def lightning_bolt_resolve(game, card, controller, targets=None, item=None):
    from .oracle_resolver import resolve_damage_to_chosen_target
    resolve_damage_to_chosen_target(game, card, controller, 3, targets)


@EFFECT_REGISTRY.register("Lava Dart", EffectTiming.SPELL_RESOLVE,
                           description="Deal 1 damage to any target")
def lava_dart_resolve(game, card, controller, targets=None, item=None):
    from .oracle_resolver import resolve_damage_to_chosen_target
    resolve_damage_to_chosen_target(game, card, controller, 1, targets)


@EFFECT_REGISTRY.register("Unholy Heat", EffectTiming.SPELL_RESOLVE,
                           description="Deal 2 (or 6 with delirium) damage")
def unholy_heat_resolve(game, card, controller, targets=None, item=None):
    # The only real per-card quirk in this cluster: the printed amount
    # is conditional on delirium (CR-style card-specific AMOUNT
    # computation, not a target-resolution difference) — this stays
    # here; only the target application delegates to the shared
    # resolver.
    from .oracle_resolver import resolve_damage_to_chosen_target
    gy = game.players[controller].graveyard
    types_in_gy = set()
    for c in gy:
        for ct in c.template.card_types:
            types_in_gy.add(ct)
    delirium = len(types_in_gy) >= 4
    damage = 6 if delirium else 2
    resolve_damage_to_chosen_target(game, card, controller, damage, targets)


@EFFECT_REGISTRY.register("Goryo's Vengeance", EffectTiming.SPELL_RESOLVE,
                           description="Reanimate legendary creature with haste, exile at EOT")
def goryos_vengeance_resolve(game, card, controller, targets=None, item=None):
    # GV2-3: honour continuous GY→battlefield hate (Grafdigger's Cage
    # and any functional reprint). Matches the LE-E1 gate in
    # _resolve_living_end; the check is oracle-driven, not name-bound.
    hate_source = game._gy_reanimation_hate_source()
    if hate_source is not None:
        game.log.append(
            f"T{game.display_turn}: Goryo's Vengeance fizzles — "
            f"{hate_source.template.name} prevents graveyard reanimation"
        )
        return
    gy = game.players[controller].graveyard
    legendary_creatures = [c for c in gy
                           if c.template.is_creature and
                           any(str(st) == "Supertype.LEGENDARY"
                               or st.name == "LEGENDARY"  # abstraction-allow: supertype enum, not card name
                               for st in c.template.supertypes)]
    # CR 608.2b: Goryo's Vengeance oracle requires "target legendary
    # creature card". If no legal target is available, the spell
    # fizzles — do NOT fall back to any creature.
    if legendary_creatures:
        best = max(legendary_creatures,
                   key=lambda c: (c.template.power or 0) + (c.template.toughness or 0))
        game.reanimate(controller, best, exile_at_eot=True, give_haste=True)


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
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Unmarked Grave puts {best.name} in graveyard")
    else:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Unmarked Grave finds nothing (no nonlegendary creatures)")


@EFFECT_REGISTRY.register("Grapeshot", EffectTiming.SPELL_RESOLVE,
                           description="Deal 1 damage to any target")
def grapeshot_resolve(game, card, controller, targets=None, item=None):
    # Grapeshot deals 1 damage (base effect) to its declared "any
    # target". Storm copies are handled by _handle_storm, which calls
    # this again for each copy, re-declaring the same `item.targets`
    # (correct: CR 706.10c — Storm copies keep the original targets
    # unless the caster is offered new ones, which this engine does
    # not yet model).
    #
    # Pre-migration bug: this handler ignored `targets` entirely and
    # always mutated `opponent.life` directly, bypassing
    # `engine.damage.deal_damage` — real damage-application drift from
    # the shared funnel (see
    # tests/test_burn_damage_shared_resolver.py::TestGrapeshotRespectsDeclaredTarget).
    # The live AI always casts Grapeshot with `targets=[-1]`
    # (ai/ev_player.py's storm-finisher target policy), so this fix
    # does not change any current sim outcome — it just makes the
    # engine correct for any other caller of a "deal N damage to any
    # target" storm spell.
    from .oracle_resolver import resolve_damage_to_chosen_target
    hit = resolve_damage_to_chosen_target(game, card, controller, 1, targets)
    if hit is not None:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Grapeshot deals 1 damage to {hit.name}")
    else:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Grapeshot deals 1 damage"
                        f" (opponent life: {game.players[1 - controller].life})")


@EFFECT_REGISTRY.register("Past in Flames", EffectTiming.SPELL_RESOLVE,
                           description="Give flashback to instants/sorceries in graveyard")
def past_in_flames_resolve(game, card, controller, targets=None, item=None):
    for c in game.players[controller].graveyard:
        if c.template.is_instant or c.template.is_sorcery:
            c.has_flashback = True
    # Record the turn-scoped fact: a graveyard-wide static
    # flashback grant has resolved this turn. The grant is
    # until-end-of-turn and applies to all eligible graveyard
    # cards, so a second copy contributes zero incremental value
    # — the AI consults this flag in signal #17 to suppress the
    # same-turn signal on redundant casts.
    game.players[controller].flashback_granted_this_turn = True
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Past in Flames grants flashback")


@EFFECT_REGISTRY.register("Empty the Warrens", EffectTiming.SPELL_RESOLVE,
                           description="Create 2 Goblin tokens")
def empty_the_warrens_resolve(game, card, controller, targets=None, item=None):
    # Base effect: create 2 Goblin tokens. Storm copies are handled
    # by _handle_storm which calls this again for each copy.
    game.create_token(controller, "goblin", count=2)
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Empty the Warrens creates 2 Goblin tokens")


@EFFECT_REGISTRY.register("Galvanic Relay", EffectTiming.SPELL_RESOLVE,
                           description="Exile top cards, play next turn (simplified: draw)")
def galvanic_relay_resolve(game, card, controller, targets=None, item=None):
    draw_count = min(game._global_storm_count, 5)
    game.draw_cards(controller, draw_count)


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


# ═══════════════════════════════════════════════════════════════════
# Board-sweep cluster — shared destroy/sacrifice-all resolver
# ═══════════════════════════════════════════════════════════════════
#
# Mechanic (CR 701.6a "destroy" / CR 701.20a "sacrifice", applied to
# EVERY matching permanent on EVERY player's battlefield rather than a
# single chosen target): a symmetric mass effect, optionally scoped by
# type (creature / artifact+creature+enchantment) and/or a resolution-
# time filter (a mana-value ceiling, a "has a color" predicate). This
# is NOT a targeted effect — CR 608.2b legality re-checks, hexproof
# (CR 702.11c protects only against being the target of a spell or
# ability), and `target_solver.enumerate_legal_targets` (which layers
# hexproof filtering on top of type filtering) do not apply here; a
# sweeper hits a hexproof creature exactly the same as any other. Only
# `target_solver._matches_type` — the pure type-token predicate, with
# no targeting semantics attached — is reused, matching the "single
# owner for one rule" principle without pulling in unrelated behavior.
#
# The one real rules fork inside the cluster is destroy vs. sacrifice:
#   - Destroy (Damnation, Supreme Verdict, Wrath of the Skies) is
#     blocked by indestructible (CR 702.12b) — checked here, since
#     neither `game._creature_dies` nor `game._permanent_destroyed`
#     check it internally (same finding the removal-spell cluster
#     documented: indestructible is the CALLER's responsibility).
#   - Sacrifice (All Is Dust: "each player sacrifices...") is NOT
#     blocked by indestructible (CR 701.20a — sacrificing is not a
#     destroy event) and is not a "may" for the permanent's
#     controller once the effect resolves. Zone-wise both funnel
#     through the same `_creature_dies`/`_permanent_destroyed`
#     primitives (a battlefield→graveyard move plus Undying/Persist/
#     dies-trigger dispatch) — the distinction is ONLY which
#     permanents are eligible in the first place, not how a permanent
#     that IS swept leaves the battlefield.
#
# See docs/design/rules-foundation-sweep-tracker.md, Phase 3, for the
# per-card table this generalizes (Damnation, Supreme Verdict,
# All Is Dust, Wrath of the Skies).

def _board_sweep_pool(game, types):
    """Every battlefield permanent (both players) matching `types`.
    Delegates type-token matching to `target_solver._matches_type` —
    the single owner of that predicate — rather than re-deriving
    per-CardType checks here. Not targeting: no hexproof filtering.
    """
    from . import target_solver
    return [c for p in game.players for c in list(p.battlefield)
            if target_solver._matches_type(c, types, "battlefield")]


def _resolve_board_sweep(
        game, card, controller, targets, item, *,
        action="destroy",
        types=frozenset({"creature"}),
        filter_fn=None,
        log_verb="destroys",
        log_noun="creatures",
):
    """Generic resolver for the destroy/sacrifice-all-matching-
    permanents cluster (see module comment above).

    action: "destroy" (CR 702.12b indestructible applies) or
      "sacrifice" (CR 701.20a — indestructible does not apply).
    types: token set consumed by `target_solver._matches_type`
      ("creature", "artifact", "enchantment", "permanent_nonland", ...).
    filter_fn: optional (game, controller, permanent) -> bool,
      an additional resolution-time condition (mana-value ceiling,
      "has a color", etc.) beyond the type filter. Returns True to
      KEEP the permanent in the sweep.
    """
    from .cards import Keyword

    candidates = _board_sweep_pool(game, types)
    if filter_fn is not None:
        candidates = [c for c in candidates if filter_fn(game, controller, c)]
    if action == "destroy":
        candidates = [c for c in candidates
                      if Keyword.INDESTRUCTIBLE not in c.keywords]

    count = 0
    for permanent in candidates:
        if permanent.template.is_creature:
            game._creature_dies(permanent)
        else:
            game._permanent_destroyed(permanent)
        count += 1
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"{card.name} {log_verb} {count} {log_noun}")
    return count


def _wrath_of_the_skies_mv_filter(x_val):
    def _filter(game, controller, permanent):
        return (permanent.template.cmc or 0) <= x_val
    return _filter


def _all_is_dust_has_color(game, controller, permanent):
    # Oracle: "sacrifices all permanents ... that are one or more
    # colors." Uses CardTemplate.colors (a permanent's actual color,
    # CR 105.2a) rather than color_identity (a format-legality
    # superset that can include colors not actually on the permanent,
    # e.g. a colored activated-ability cost) — the same distinction
    # Phase 0b's Scion of Draco fix established `colors` for.
    return len(permanent.template.colors) > 0


@EFFECT_REGISTRY.register("Wrath of the Skies", EffectTiming.SPELL_RESOLVE,
                           description="Get X energy, pay any amount, destroy permanents with MV <= paid")
def wrath_of_the_skies_resolve(game, card, controller, targets=None, item=None):
    player = game.players[controller]

    # Oracle: "You get X {E}, then you may pay any amount of {E}.
    # Destroy each artifact, creature, and enchantment with MV <= amount paid."
    # Step 1: get X energy from the cast X value
    x_cast = item.x_value if item and hasattr(item, 'x_value') else 0
    if x_cast > 0:
        player.add_energy(x_cast)

    # Step 2: spend up to all available energy (capped at what we just got + existing)
    x_val = player.energy_counters  # includes newly generated energy
    if x_val > 0:
        player.spend_energy(x_val)

    # Step 3: destroy each artifact/creature/enchantment with MV <= x_val
    _resolve_board_sweep(
        game, card, controller, targets, item,
        action="destroy",
        types=frozenset({"artifact", "creature", "enchantment"}),
        filter_fn=_wrath_of_the_skies_mv_filter(x_val),
        log_verb=f"(X={x_val}) sweeps the board, destroying",
        log_noun="permanents",
    )


@EFFECT_REGISTRY.register("Prismatic Ending", EffectTiming.SPELL_RESOLVE,
                           description="Exile target nonland permanent with MV <= colors")
def prismatic_ending_resolve(game, card, controller, targets=None, item=None):
    _resolve_nonland_permanent_removal(
        game, card, controller, targets, item,
        zone_dest="exile", mv_max_fn=_prismatic_ending_mv_max,
        log_verb="exiles",
    )


@EFFECT_REGISTRY.register("March of Otherworldly Light", EffectTiming.SPELL_RESOLVE,
                           description="Exile target artifact, creature, or enchantment with MV <= X")
def march_otherworldly_light_resolve(game, card, controller, targets=None, item=None):
    _resolve_nonland_permanent_removal(
        game, card, controller, targets, item,
        zone_dest="exile", types=frozenset({"artifact", "creature", "enchantment"}),
        mv_max_fn=_march_otherworldly_light_mv_max, log_verb="exiles",
    )


@EFFECT_REGISTRY.register("Ephemerate", EffectTiming.SPELL_RESOLVE,
                           description="Blink target creature you control")
def ephemerate_resolve(game, card, controller, targets=None, item=None):
    # Real oracle: "Exile target creature you control, then return it to the
    # battlefield under its owner's control. Rebound."
    # Requires a creature target — if none, spell fizzles.
    my_creatures = game.players[controller].creatures
    if not my_creatures:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Ephemerate fizzles (no creatures to target)")
        return
    # Prefer creatures with valuable ETBs. Blink value is the threat
    # score of re-triggering the creature's ETB — delegate to the AI
    # layer's creature_threat_value (same math-derived formula used for
    # removal targeting), discounted by P(ETB finds a useful target).
    from ai.ev_evaluator import snapshot_from_game
    _blink_snap = snapshot_from_game(game, controller)

    def _blink_value(c):
        from ai.ev_evaluator import creature_threat_value
        tags = getattr(c.template, 'tags', set())
        base = creature_threat_value(c, _blink_snap)
        # Removal ETB requires an opposing creature. Without a target, a
        # re-trigger is wasted — scale base by a "usefulness factor".
        if 'removal' in tags:
            opp_idx = 1 - controller
            if not game.players[opp_idx].creatures:
                base *= 0.2  # rules: ETB-remove-nothing is ~20% of full value
        return base
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


@EFFECT_REGISTRY.register("Isochron Scepter", EffectTiming.ETB,
                           description="Imprint: exile an instant with CMC <= 2 from hand")
def isochron_scepter_etb(game, card, controller, targets=None, item=None):
    """Imprint clause: pick the best instant with mana value 2 or less from
    hand and move it to exile attached to the Scepter. Activation (per turn)
    is handled in game_runner._process_upkeep_activations, which reads the
    `imprint:<name>` tag we set here.
    """
    player = game.players[controller]
    candidates = [c for c in player.hand
                  if c.template.is_instant and (c.template.cmc or 0) <= 2]
    if not candidates:
        return

    # Engine must not score — delegate ranking to AI layer.
    # Imprint value = one-shot EV of casting the spell, which is exactly what
    # estimate_spell_ev computes. The Scepter gives a free copy each turn, so
    # relative ordering of single-cast EV is the correct ranking of imprint
    # targets (repeating a strictly better spell is strictly better).
    from ai.ev_evaluator import estimate_spell_ev, snapshot_from_game
    from ai.ev_player import _get_archetype
    snap = snapshot_from_game(game, controller)
    archetype = _get_archetype(player.deck_name)

    def _imprint_value(c):
        return estimate_spell_ev(c, snap, archetype, dk=None,
                                 game=game, player_idx=controller)

    best = max(candidates, key=_imprint_value)
    player.hand.remove(best)
    best.zone = "exile"
    # Mark the imprint link so game_runner knows which card to copy.
    if not hasattr(card, 'instance_tags') or card.instance_tags is None:
        card.instance_tags = set()
    card.instance_tags.add(f"imprint:{best.template.name}")
    if not hasattr(best, 'instance_tags') or best.instance_tags is None:
        best.instance_tags = set()
    best.instance_tags.add("on_scepter")
    player.exile.append(best)
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Isochron Scepter imprints {best.template.name}")


# Phelia attack handler — superseded by the richer implementation at line
# ~2506 (added in main commit bbd21fd), which includes the END_STEP return
# hook. The earlier stub is removed here to avoid duplicate registration.


# ═══════════════════════════════════════════════════════════════════
# Boros Energy creatures
# ═══════════════════════════════════════════════════════════════════

@EFFECT_REGISTRY.register("Guide of Souls", EffectTiming.ETB,
                           description="Whenever ANOTHER creature enters: gain 1 life, get {E}")
def guide_of_souls_etb(game, card, controller, targets=None, item=None):
    # Oracle: "Whenever ANOTHER creature you control enters, you gain 1 life and get {E}."
    # This is a triggered ability, NOT an ETB on Guide itself.
    # Guide does NOT trigger when it enters — only when OTHER creatures enter.
    # The ETB handler here is a no-op; the trigger is handled by
    # resolve_spell_cast_trigger / trigger_etb for other creatures.
    pass


@EFFECT_REGISTRY.register("Ocelot Pride", EffectTiming.ETB,
                           description="No ETB effect — noncreature cast trigger in oracle_resolver")
def ocelot_pride_etb(game, card, controller, targets=None, item=None):
    # Real Ocelot Pride (Modern Horizons 3 / Aetherdrift era):
    # "Whenever you cast a noncreature spell, you get {E}."
    # "Whenever a creature you control deals combat damage to a player,
    #  if you have more energy than that player has life, create a 1/1 Cat token."
    # No ETB effect — the noncreature trigger is handled in oracle_resolver.py
    # via the _ocelot_noncreature_trigger path.
    pass


@EFFECT_REGISTRY.register("Ajani, Nacatl Pariah // Ajani, Nacatl Avenger",
                           EffectTiming.ETB,
                           description="Create 2/1 Cat Warrior token")
def ajani_etb(game, card, controller, targets=None, item=None):
    # Oracle: "When Ajani enters, create a 2/1 white Cat Warrior creature token."
    # NO energy production — oracle has 0 {E} symbols.
    game.create_token(controller, "cat", count=1, power=2, toughness=1)
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Ajani creates 2/1 Cat Warrior token")


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
    _sp_drawn = game.draw_cards(controller, 2)
    _sp_names = ", ".join(c.name for c in _sp_drawn)
    # Create 1/1 Elemental tokens for each nonland discarded
    if discarded_nonland > 0:
        game.create_token(controller, "elemental", count=discarded_nonland)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Seasoned Pyromancer: discard 2, draw 2 ({_sp_names}), create {discarded_nonland} Elemental(s)")
    else:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Seasoned Pyromancer: discard 2, draw 2 ({_sp_names}) (no nonland discards)")


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
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Ranger-Captain tutors {best.name} to hand")
    else:
        game.rng.shuffle(player.library)


@EFFECT_REGISTRY.register("Ragavan, Nimble Pilferer", EffectTiming.ETB,
                           description="Create Treasure token on combat damage")
def ragavan_etb(game, card, controller, targets=None, item=None):
    # Ragavan's ETB does nothing — his ability triggers on combat damage.
    # The combat damage trigger is handled in CombatManager._deal_combat_damage.
    pass


@EFFECT_REGISTRY.register("The Legend of Roku", EffectTiming.ETB,
                           description="Saga Ch.I: exile top 3, may play until end of next turn")
def legend_of_roku_etb(game, card, controller, targets=None, item=None):
    """Chapter I triggers on ETB: impulse draw 3."""
    player = game.players[controller]
    exiled = []
    for _ in range(min(3, len(player.library))):
        c = player.library.pop(0)
        c.zone = "hand"
        player.hand.append(c)
        exiled.append(c.name)
    if exiled:
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"The Legend of Roku Ch.I: impulse draws {', '.join(exiled)}"
        )
    # Initialize lore counter at 1 (Chapter I already fired)
    if not hasattr(card, 'other_counters') or card.other_counters is None:
        card.other_counters = {}
    card.other_counters['lore'] = 1


# Summoner's Pact: duplicate registration removed (kept at line ~1651)
# Oracle: "Search your library for a green creature card, reveal it, put into hand."

@EFFECT_REGISTRY.register("Stock Up", EffectTiming.SPELL_RESOLVE,
                           description="Look at top 5, put 2 in hand, rest on bottom")
def stock_up_resolve(game, card, controller, targets=None, item=None):
    # Oracle: "Look at the top five cards of your library.
    # Put two of them into your hand and the rest on the bottom in any order."
    player = game.players[controller]
    top_cards = player.library[:5]
    if len(top_cards) <= 2:
        # 2 or fewer cards: all go to hand
        for c in top_cards:
            player.library.remove(c)
            c.zone = "hand"
            player.hand.append(c)
    else:
        # Pick best 2 (highest CMC non-land, or any)
        top_cards.sort(key=lambda c: (not c.template.is_land, c.template.cmc or 0), reverse=True)
        for c in top_cards[:2]:
            player.library.remove(c)
            c.zone = "hand"
            player.hand.append(c)
        # Rest go to bottom
        for c in top_cards[2:]:
            player.library.remove(c)
            player.library.append(c)


@EFFECT_REGISTRY.register("Orim's Chant", EffectTiming.SPELL_RESOLVE,
                           description="Target player can't cast spells this turn")
def orims_chant_resolve(game, card, controller, targets=None, item=None):
    # Oracle: "Kicker {W}. Target player can't cast spells this turn. If this
    # spell was kicked, creatures can't attack this turn." The sim does not
    # model kicker payments, so every cast resolves as the base (unkicked)
    # variant — scope is "this turn", never a carryover to the opponent's
    # next turn. Cast on own main phase → opp is silenced for the remainder
    # of the current turn (narrow: blocks end-of-turn instants). Cast during
    # opp's turn (flash/Scepter) → silences their ongoing turn.
    opponent = 1 - controller
    game.players[opponent].silenced_this_turn = True
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Orim's Chant silences P{opponent+1} this turn")


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
                           description="Target creature gets +1/+0 and first strike until end of turn")
def violent_urge_resolve(game, card, controller, targets=None, item=None):
    # Oracle: "Target creature gets +1/+0 and gains first strike until end of turn.
    # Delirium — If 4+ card types in graveyard, double strike instead."
    # NO draw effect in oracle.
    from .cards import Keyword
    my_creatures = game.players[controller].creatures
    if my_creatures:
        best = max(my_creatures, key=lambda c: c.power or 0)
        best.temp_power_mod += 1
        # Check delirium: 4+ card types in graveyard
        player = game.players[controller]
        gy_types = set()
        for c in player.graveyard:
            for ct in c.template.card_types:
                gy_types.add(ct)
        if len(gy_types) >= 4:
            best.keywords.add(Keyword.DOUBLE_STRIKE)
        else:
            best.keywords.add(Keyword.FIRST_STRIKE)


@EFFECT_REGISTRY.register("Expressive Iteration", EffectTiming.SPELL_RESOLVE,
                           description="Look at top 3, put 1 in hand, exile 1, bottom 1")
def expressive_iteration_resolve(game, card, controller, targets=None, item=None):
    # Oracle: "Look at the top three cards of your library. Put one of them
    # into your hand, exile one of them, and put the rest on the bottom."
    player = game.players[controller]
    top = player.library[:3]
    if not top:
        return
    # Pick best for hand (highest CMC non-land)
    top.sort(key=lambda c: (not c.template.is_land, c.template.cmc or 0), reverse=True)
    # Best → hand
    best = top[0]
    player.library.remove(best)
    best.zone = "hand"
    player.hand.append(best)
    # Second best → exile (playable this turn — simplified as lost)
    if len(top) > 1:
        second = top[1]
        player.library.remove(second)
        second.zone = "exile"
        player.exile.append(second)
    # Rest → bottom
    for c in top[2:]:
        player.library.remove(c)
        player.library.append(c)


# Preordain handler removed — oracle_resolver.resolve_spell_from_oracle
# now matches "draw a card" and fires the draw. Scry portion is approximated
# as no-op (AI doesn't model deck order).


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

    # Proper finisher comparison: compute expected lethal-turn for each option
    # given the current board state, pick the earliest.
    #
    #   Grapeshot: deals `estimated_storm` damage immediately. Kills iff
    #              estimated_storm >= opp_life. Otherwise the chain is wasted —
    #              we leave opp alive on their turn.
    #
    #   Empty the Warrens: creates 2 * estimated_storm goblins, which attack
    #              for 2 * estimated_storm next turn. Survival probability
    #              depends on opponent board / sweeper count / blocker count,
    #              approximated below via a survival factor.
    #
    # Prefer Empty when its expected damage beats Grapeshot's current damage
    # AND we're not mid-kill (where Grapeshot would close the game this turn).
    grapeshot_damage = estimated_storm  # 1 damage per copy, now
    empty_power     = 2 * estimated_storm  # tokens deal this next turn

    # Survival factor: probability the goblins survive opponent's response.
    # Defaults to 0.75 (no board), scales down with opp creatures and sweepers.
    opp = game.players[1 - controller]
    opp_creatures = len([c for c in opp.battlefield if c.template.is_creature])
    opp_has_sweeper_mana = opp.life > 0 and len(
        [c for c in opp.battlefield if c.template.is_land and not getattr(c, 'tapped', False)]
    ) >= 4
    survival = 0.75
    if opp_creatures >= 3:
        survival -= 0.25  # chump-blocking eats most goblins
    if opp_has_sweeper_mana:
        survival -= 0.35  # wrath / Pyroclasm risk
    survival = max(0.15, survival)
    empty_expected_damage = empty_power * survival

    # ── Flashback-granting chain extender as a Wish target ──
    # When the immediate finishers can't close THIS turn but a card
    # that grants flashback to GY instants/sorceries (Past in Flames,
    # Yawgmoth's Will pattern) is reachable in SB/library AND the GY
    # holds enough ritual + cantrip fuel that re-casting them would
    # push the chain to lethal, fetching the extender first is
    # correct.  Detection is oracle-driven — no card-name gate.
    def _is_chain_extender(c):
        # Reads template.grants_flashback_to_gy_spells, populated at DB
        # load by oracle_parser.grants_flashback_to_gy_spells() — no
        # runtime oracle inspection.
        return getattr(c.template, 'grants_flashback_to_gy_spells', False)
    extender = next((c for c in sb if _is_chain_extender(c)), None) \
        or next((c for c in lib if _is_chain_extender(c)), None)
    pif_in_reach = extender is not None
    pif_already_active = any(
        getattr(c, 'pif_active_until_eot', False)
        for c in player.graveyard
    )
    # GY rituals + cantrips that PiF would re-flashback into the
    # chain. Same predicate the AI's `is_chain_fuel` uses, inlined
    # here to keep this engine layer self-contained.
    gy_chain_fuel = sum(
        1 for c in player.graveyard
        if (c.template.is_instant or c.template.is_sorcery)
        and any(t in getattr(c.template, 'tags', set())
                for t in ('ritual', 'cantrip', 'card_advantage', 'draw'))
    )
    # Mana floor for PiF chain: PiF costs 3R; we need PiF cost + at
    # least one ritual flashback (1R) to net more storm than a
    # do-nothing Wish→Grapeshot. Use opp_life as the lethal target.
    pif_chain_storm = (current_storm + 1   # the Wish itself
                       + 1                  # PiF cast
                       + gy_chain_fuel)     # rituals + cantrips re-cast
    # PiF is preferable when (a) Grapeshot can't kill THIS turn and
    # (b) the post-PiF chain damage exceeds Grapeshot's current
    # damage by enough margin to justify spending a turn rebuilding.
    #
    # Margin derived from opp's clock — holding for PiF costs +1
    # opp turn of pressure.  The fractional life loss from that
    # extra turn equals `1 / opp_clock_discrete` (i.e. one Nth of
    # our remaining life clock).  Require the PiF chain damage to
    # exceed grapeshot's current damage by that fraction so the
    # trade is at-or-better than break-even.
    #
    # Examples:
    #   opp_clock = 2 (we die in 2 turns)  → margin 1.50× (50% cost)
    #   opp_clock = 4 (we die in 4 turns)  → margin 1.25× (25% cost)
    #   opp_clock = 8 (slow clock)         → margin 1.125× (12.5%)
    #   opp_clock = 99 (no clock — sentinel) → margin 1.01× (≈ 0)
    #
    # Pure clock arithmetic; no tuning constants.  When `snap` is
    # not yet built (the Wish picker runs in engine layer), fall
    # back to opp's life ÷ opp_power as a clock proxy.
    from ai.ev_evaluator import snapshot_from_game
    snap = snapshot_from_game(game, controller)
    opp_clock = max(1, getattr(snap, 'opp_clock_discrete', 99))
    hold_margin = 1.0 + (1.0 / opp_clock)
    pif_extends_to_lethal = (
        pif_in_reach
        and not pif_already_active
        and gy_chain_fuel >= 3   # at least 3 cards to re-flashback
        and (pif_chain_storm >= opp_life
             or pif_chain_storm > grapeshot_damage * hold_margin)
    )

    if grapeshot_damage >= opp_life:
        # Grapeshot kills now — always take it.
        finisher_priority = ["Grapeshot", "Empty the Warrens", "Galvanic Relay"]
    elif pif_extends_to_lethal and grapeshot_damage < opp_life:
        # Sub-lethal Grapeshot but PiF chain reaches lethal — fetch
        # PiF first, re-flashback rituals, then Wish/cast Grapeshot
        # at the proper storm count next.
        finisher_priority = ["Past in Flames", "Grapeshot", "Empty the Warrens", "Galvanic Relay"]
    elif empty_expected_damage > grapeshot_damage and empty_power >= opp_life:
        # Warrens expected to kill next turn and beats a half-Grapeshot now.
        finisher_priority = ["Empty the Warrens", "Grapeshot", "Galvanic Relay"]
    elif grapeshot_damage >= opp_life * 0.6:
        # Close to lethal — Grapeshot's immediate damage still preferable.
        finisher_priority = ["Grapeshot", "Empty the Warrens", "Galvanic Relay"]
    else:
        # Grapeshot can't finish, but Warrens can't reliably close either;
        # default to Grapeshot (doesn't give opponent a turn to stabilise).
        finisher_priority = ["Grapeshot", "Empty the Warrens", "Galvanic Relay"]

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
        game.log.append(f"T{game.display_turn} P{controller+1}: Wish finds {chosen.name} (from {from_zone})")


@EFFECT_REGISTRY.register("Gifts Ungiven", EffectTiming.SPELL_RESOLVE,
                           description="Search for 4 cards, opponent puts 2 in GY")
def gifts_ungiven_resolve(game, card, controller, targets=None, item=None):
    lib = game.players[controller].library
    candidates = [c for c in lib if c.template.is_instant or c.template.is_sorcery]
    # Deterministic engine simplification (not strategic scoring): for
    # the GY pile choice, prefer cards usable from graveyard — storm
    # payoffs (recur via Past in Flames) and flashback spells — over
    # cards that are dead in graveyard.  All keys are oracle/tag
    # derived; no card-name conditionals.  A future AI-side variant
    # could consult the gameplan for matchup-specific GY value, but
    # since the opponent makes the actual choice in real Gifts, the
    # engine's "best 2 to send to GY" sort is a categorical heuristic,
    # not a strategic decision.
    def _gifts_priority(c):
        ctags = getattr(c.template, 'tags', set())
        gy_useful = ('storm_payoff' in ctags or 'flashback' in ctags)
        return (0 if gy_useful else 1, -c.template.cmc)
    candidates.sort(key=_gifts_priority)
    found = candidates[:2]
    for card_found in found:
        lib.remove(card_found)
        card_found.zone = "graveyard"
        game.players[controller].graveyard.append(card_found)
    game.rng.shuffle(lib)
    if found:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
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
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Force of Vigor destroys {target.name}")
        destroyed += 1
    if destroyed == 0:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
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
        target = max(artifacts, key=lambda c: _threat_score(c, game, opp))
        game._permanent_destroyed(target)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Wear // Tear destroys {target.name}")
        destroyed += 1
    # Tear: destroy best enchantment
    enchantments = [c for c in opp.battlefield
                    if CardType.ENCHANTMENT in c.template.card_types
                    and not c.template.is_creature]
    if enchantments:
        target = max(enchantments, key=lambda c: _threat_score(c, game, opp))
        game._permanent_destroyed(target)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Wear // Tear destroys {target.name}")
        destroyed += 1
    if destroyed == 0:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
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
        target = max(artifacts_enchantments,
                     key=lambda c: _threat_score(c, game, opp))
        game._permanent_destroyed(target)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Pick Your Poison: opponent sacrifices {target.name}")
    elif flyers:
        target = max(flyers, key=lambda c: c.power or 0)
        game._creature_dies(target)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Pick Your Poison: opponent sacrifices {target.name}")
    else:
        # Fallback: opponent loses 1 life (Toxic 1 mode)
        opp.life -= 1
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Pick Your Poison: opponent loses 1 life")


@EFFECT_REGISTRY.register("Meltdown", EffectTiming.SPELL_RESOLVE,
                           description="Destroy all artifacts with MV <= X")
def meltdown_resolve(game, card, controller, targets=None, item=None):
    from .cards import CardType
    # X = mana spent beyond R (cmc - 1)
    player = game.players[controller]
    x_val = max(0, card.template.cmc - 1)
    # Also consider extra mana spent
    available = player.untapped_mana_capacity()
    x_val = max(x_val, available)  # Simplified: spend all available mana

    for p in game.players:
        artifacts = [c for c in p.battlefield
                     if CardType.ARTIFACT in c.template.card_types
                     and c.template.cmc <= x_val]
        for artifact in artifacts:
            game._permanent_destroyed(artifact)
    game.log.append(f"T{game.display_turn} P{controller+1}: "
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
    game.log.append(f"T{game.display_turn} P{controller+1}: "
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
        target = max(artifacts, key=lambda c: _threat_score(c, game, opp))
        game._permanent_destroyed(target)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Kolaghan's Command destroys {target.name}")
    else:
        # Deal 2 damage to opponent
        opp.life -= 2
        game.players[controller].damage_dealt_this_turn += 2
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Kolaghan's Command deals 2 to opponent")

    # Second mode: return creature from GY or force discard
    player = game.players[controller]
    gy_creatures = [c for c in player.graveyard if c.template.is_creature]
    if gy_creatures:
        best = max(gy_creatures, key=lambda c: (c.template.power or 0))
        player.graveyard.remove(best)
        best.zone = "hand"
        player.hand.append(best)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Kolaghan's Command returns {best.name} from GY")
    else:
        game._force_discard(opponent, 1)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Kolaghan's Command: opponent discards")


def _nonland_permanent_threat(c, opp_battlefield):
    """Score a nonland permanent for removal targeting.

    Equipment that scales with artifact count (Cranial Plating, Nettlecyst)
    is valued by how much power it adds, not just CMC.
    """
    from .cards import CardType
    t = c.template
    score = (t.cmc or 0) + (c.power or 0)
    # Equipment with artifact-scaling: value = artifact count on board
    tags = getattr(t, 'tags', set())
    if 'pump' in tags or 'equipment' in tags:
        if getattr(t, 'has_artifact_pump_equipment', False):
            artifact_count = sum(1 for b in opp_battlefield
                                 if CardType.ARTIFACT in b.template.card_types)
            score = artifact_count + 2  # Plating with 8 artifacts = 10
    # Planeswalkers
    if CardType.PLANESWALKER in t.card_types:
        score += 5 + (getattr(c, 'loyalty_counters', 0) or 0)
    return score


# ═══════════════════════════════════════════════════════════════════
# Nonland permanent removal cluster — shared destroy/exile resolver
# ═══════════════════════════════════════════════════════════════════
#
# Mechanic (CR 608.2b/608.2c): "Destroy/exile target permanent
# [optionally restricted by type], and only if [a resolution-time
# condition — usually a mana-value threshold] holds when the spell/
# ability resolves." A card's TARGET LEGALITY (does a legal candidate
# exist / is a chosen id still legal — zone, type, owner, hexproof
# once engine/target_solver.py grows that check) is a separate rules
# question from whether its CONDITION is met once a legal target is
# selected. Conflating the two was the bug this cluster shared: some
# handlers ignored the pre-chosen `targets` entirely and re-derived
# their own pick every time; one handler (Fatal Push) fell through to
# picking a DIFFERENT target when the chosen one failed its condition,
# which is not what CR 608.2c says happens (the spell does nothing —
# it does not re-target).
#
# See docs/design/rules-foundation-sweep-tracker.md, Phase 3, for the
# per-card restriction table this generalizes (Abrupt Decay,
# Assassin's Trophy, Fatal Push, Leyline Binding, Prismatic Ending,
# March of Otherworldly Light).

_FATAL_PUSH_BASE_MV = 2    # rules constant: Fatal Push, no revolt
_FATAL_PUSH_REVOLT_MV = 4  # rules constant: Fatal Push, with revolt
_ABRUPT_DECAY_MV = 3       # rules constant: Abrupt Decay's fixed threshold
_CONVERGE_MAX_COLORS = 5   # rules constant: WUBRG — the color pip ceiling


def _removal_legal_pool(game, controller, owner_scope, types):
    """Battlefield permanents of `types` legal for `controller` to
    target, filtered by `owner_scope` ("opponent"/"any") via
    `target_solver.enumerate_legal_targets` — the single owner of
    target-legality (CR 601.2c), never re-derived here. Keyed by
    instance_id for O(1) lookup against a pre-chosen target id.
    """
    from . import target_solver
    req = target_solver.TargetRequirement(
        zone="battlefield", types=frozenset(types), owner_scope=owner_scope,
    )
    return {c.instance_id: c
            for c in target_solver.enumerate_legal_targets(game, controller, req)}


def _resolve_nonland_permanent_removal(
        game, card, controller, targets, item, *,
        zone_dest,
        types=frozenset({"permanent_nonland"}),
        owner_scope="opponent",
        mv_max_fn=None,
        log_verb="destroys",
):
    """Generic resolver for the destroy/exile-target-permanent cluster
    (see module comment above). Every card in the cluster shares this
    one function body; per-card differences are entirely captured by
    the keyword arguments a thin registration wrapper supplies.

    zone_dest:
      "graveyard" — destroy. Routes through `game._permanent_destroyed`,
        which itself dispatches to `game._creature_dies` for creature
        targets (so Undying/Persist replacement, CR 702.92d/702.77c,
        still applies) and is a no-op against an indestructible
        permanent (CR 702.12b) — checked below since neither funnel
        function checks it itself.
      "exile" — routes through `game._exile_permanent`. No death
        replacement applies to exile (CR 700.4 — the permanent never
        goes to the graveyard), and indestructible does not protect
        against it.
    mv_max_fn: optional (game, card, controller, item) -> int | None,
      the resolution-time mana-value ceiling. None means the card has
      no such condition at all (Assassin's Trophy, Leyline Binding).
    """
    from .cards import Keyword

    legal = _removal_legal_pool(game, controller, owner_scope, types)
    max_mv = mv_max_fn(game, card, controller, item) if mv_max_fn else None

    def _meets_condition(target) -> bool:
        if max_mv is not None and (target.template.cmc or 0) > max_mv:
            return False  # CR 608.2c: condition unmet
        if zone_dest == "graveyard" and Keyword.INDESTRUCTIBLE in target.keywords:
            return False  # CR 702.12b: destroy does nothing
        return True

    def _apply(target):
        if zone_dest == "exile":
            game._exile_permanent(target)
        else:
            game._permanent_destroyed(target)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"{card.name} {log_verb} {target.name}")

    if targets:
        # A target was already chosen (cast time / trigger-target
        # selection) — honor it rather than re-deriving a fresh pick.
        for tid in targets:
            target = legal.get(tid)
            if target is None:
                continue  # CR 608.2b: illegal at resolution, try next id
            if not _meets_condition(target):
                return  # CR 608.2c: condition unmet — no effect, no retarget
            _apply(target)
            return
        return  # every chosen id was illegal at resolution — no effect

    # No pre-chosen target (dies-trigger fan-out, blink/reanimation
    # re-entry via zone_transfer._fire_etb_triggers, or a synthetic
    # test call site with no cast-time target selection): auto-pick
    # the highest-threat legal + condition-satisfying candidate, using
    # the heuristic every handler in this cluster already shared.
    candidates = [c for c in legal.values() if _meets_condition(c)]
    if not candidates:
        return
    opp_battlefield = game.players[1 - controller].battlefield
    best = max(candidates,
              key=lambda c: _nonland_permanent_threat(c, opp_battlefield))
    _apply(best)


def _fatal_push_mv_max(game, card, controller, item):
    has_revolt = game.players[controller].creatures_died_this_turn > 0
    return _FATAL_PUSH_REVOLT_MV if has_revolt else _FATAL_PUSH_BASE_MV


def _prismatic_ending_mv_max(game, card, controller, item):
    # Converge: X = number of distinct colors of mana actually spent to
    # cast this spell. Captured at cast time into item.colors_spent by
    # the engine (tap_lands_for_mana + mana-pool diff). Fallback: if
    # item is missing (older test harnesses), approximate from
    # untapped land colors — imperfect but matches the pre-tracking
    # heuristic this replaces.
    colors = set(getattr(item, 'colors_spent', set())) if item else set()
    if not colors:
        for land in game.players[controller].untapped_lands:
            for c in (land.template.produces_mana or []):
                colors.add(c)
    colors.discard('C')  # Converge counts colored pips only
    return max(1, min(len(colors), _CONVERGE_MAX_COLORS))


def _march_otherworldly_light_mv_max(game, card, controller, item):
    # X = mana actually paid for {X} (stored on the stack item at cast
    # time). Reading from len(lands) would ignore the cast-time
    # commitment and let March target permanents above the paid CMC.
    return item.x_value if item and hasattr(item, 'x_value') else 0


@EFFECT_REGISTRY.register("Abrupt Decay", EffectTiming.SPELL_RESOLVE,
                           description="Destroy target nonland permanent with MV 3 or less")
def abrupt_decay_resolve(game, card, controller, targets=None, item=None):
    _resolve_nonland_permanent_removal(
        game, card, controller, targets, item,
        zone_dest="graveyard", mv_max_fn=lambda g, c, ctl, it: _ABRUPT_DECAY_MV,
    )


@EFFECT_REGISTRY.register("Assassin's Trophy", EffectTiming.SPELL_RESOLVE,
                           description="Destroy target permanent, opponent searches for basic land")
def assassins_trophy_resolve(game, card, controller, targets=None, item=None):
    _resolve_nonland_permanent_removal(
        game, card, controller, targets, item,
        zone_dest="graveyard", types=frozenset({"permanent"}),
    )
    # Opponent gets a basic land (simplified: just a small benefit)
    # In practice this is a downside but we don't model basic land search


@EFFECT_REGISTRY.register("Fatal Push", EffectTiming.SPELL_RESOLVE,
                           description="Destroy creature with MV 2 or less (4 with revolt)")
def fatal_push_resolve(game, card, controller, targets=None, item=None):
    _resolve_nonland_permanent_removal(
        game, card, controller, targets, item,
        zone_dest="graveyard", types=frozenset({"creature"}),
        mv_max_fn=_fatal_push_mv_max,
    )


@EFFECT_REGISTRY.register("Leyline Binding", EffectTiming.ETB,
                           description="Exile target nonland permanent an opponent controls")
def leyline_binding_etb(game, card, controller, targets=None, item=None):
    _resolve_nonland_permanent_removal(
        game, card, controller, targets, item,
        zone_dest="exile", log_verb="exiles",
    )



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
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Archon of Cruelty: P{opponent+1} sacrifices {target.name}")

    # Opponent discards a card
    if opp.hand:
        # Discard the worst card (lowest CMC non-land)
        discard = min(opp.hand, key=lambda c: c.template.cmc if not c.template.is_land else 99)
        opp.hand.remove(discard)
        discard.zone = "graveyard"
        game.players[discard.owner].graveyard.append(discard)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Archon of Cruelty: P{opponent+1} discards {discard.name}")

    # Opponent loses 3 life
    opp.life -= 3
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Archon of Cruelty: P{opponent+1} loses 3 life (now {opp.life})")

    # You draw a card
    game.draw_cards(controller, 1)

    # You gain 3 life
    game.gain_life(controller, 3, "Archon of Cruelty")
    game.log.append(f"T{game.display_turn} P{controller+1}: "
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
        # Grazer puts the land onto the battlefield tapped, so it enters
        # tapped regardless of the template's enters_tapped field.
        land.tapped = True
        player.battlefield.append(land)
        # Apply Amulet-style untap triggers AND Spelunking-style "lands
        # enter untapped" static abilities — both needed for Amulet Titan's
        # mana chain. Previously only Amulet triggers applied, which left
        # Spelunking's replacement-effect untap unapplied on Grazer lands.
        game._apply_land_etb_static(land, controller)
        game._apply_lands_enter_untapped(land, controller)
        game._trigger_landfall(controller)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
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

    # Priority: bounce lands (for mana with Amulet) > damage-dealing
    # lands (Valakut-class finishers) > combat-enabler lands > plain
    # mana producers > basics.  All checks oracle-text / template-field
    # driven; no card-name conditionals.
    #
    # The constants 10/8/7/3 are deterministic engine tie-breakers
    # (categorical ordering, not strategic weights).  Strategic value
    # of a Titan-fetched land in the wider plan lives in the AI layer
    # (`ai/permanent_threat.py` once it lands on the battlefield); the
    # engine only needs to break ties consistently when several lands
    # match the same category.
    def land_priority(c):
        score = 0
        otext = (c.template.oracle_text or "").lower()
        if c.template.enters_tapped and c.template.produces_mana:
            score += 10  # bounce lands are best with Amulet
        # Damage-source lands: any land whose oracle text contains a
        # "deal[s] N damage to any target" clause is a closer/finisher
        # tile (Valakut, the Molten Pinnacle and any future printing
        # of the same template).
        if re.search(r"deals?\s+\d+\s+damage\s+to\s+any\s+target", otext):
            score += 8
        # Lands that grant haste / double strike to creatures (oracle-detected
        # combat enablers, e.g. Slayers' Stronghold, Hanweir Battlements,
        # Sunhome). Activated abilities targeting creatures show up as the
        # phrase "gains haste" / "double strike" in oracle text.
        if "gains haste" in otext or "double strike" in otext:
            score += 7
        if c.template.produces_mana:
            score += 3
        return score

    lands_in_library.sort(key=land_priority, reverse=True)
    to_put = lands_in_library[:2]

    for land in to_put:
        player.library.remove(land)
        land.enter_battlefield()
        land.controller = controller
        # Titan puts lands onto the battlefield tapped.
        land.tapped = True
        player.battlefield.append(land)
        # Apply Amulet-style untap AND Spelunking-style untapped-static to
        # keep all land-entry paths consistent. Without Spelunking here,
        # Amulet Titan's combo loop breaks on Titan-fetched bounce lands.
        game._apply_land_etb_static(land, controller)
        game._apply_lands_enter_untapped(land, controller)
        game._trigger_landfall(controller)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
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
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Wan Shi Tong enters with {x} +1/+1 counters "
                        f"({card.power}/{card.toughness}), draws {draw_count} cards")
    else:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Wan Shi Tong enters (no opponent searches yet)")


@EFFECT_REGISTRY.register("Sanctifier en-Vec", EffectTiming.ETB,
                           description="Exile all black and red cards from all graveyards")
def sanctifier_en_vec_etb(game, card, controller, targets=None, item=None):
    """Sanctifier en-Vec: exile all black/red cards from all graveyards."""
    exiled = 0
    for p in game.players:
        to_exile = [c for c in p.graveyard
                    if any(col.value in ('B', 'R') for col in c.template.color_identity)]
        for c in to_exile:
            p.graveyard.remove(c)
            c.zone = "exile"
            p.exile.append(c)
            exiled += 1
    if exiled > 0:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Sanctifier en-Vec exiles {exiled} black/red cards from graveyards")


@EFFECT_REGISTRY.register("Consign to Memory", EffectTiming.SPELL_RESOLVE,
                           description="Counter target triggered ability or colorless spell")
def consign_to_memory_resolve(game, card, controller, targets=None, item=None):
    """Consign to Memory: counter a triggered ability or colorless spell.

    The generic ability parser doesn't build a counter ability for this card
    (its oracle doesn't match the standard 'Counter target spell' pattern), so
    registering here is necessary to make it functional. Primary value: counters
    colorless spells — every Affinity artifact creature (Frogmite, Thought
    Monitor, Cranial Plating, Mox Opal, Memnite, Ornithopter). Replicate {1}
    is skipped for simplicity.
    """
    if not targets and not item:
        return
    tids = targets or (getattr(item, 'targets', None) if item else None) or []
    # Fall back to countering the top of the stack if no explicit target.
    if not tids:
        if game.stack.is_empty:
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                            f"Consign to Memory fizzles (no target)")
            return
        target_idx = len(game.stack.items) - 1
        tids = [game.stack.items[target_idx].source.instance_id]

    for tid in tids:
        for i, stack_item in enumerate(game.stack.items):
            if stack_item.source.instance_id != tid:
                continue
            tmpl = stack_item.source.template
            # Validate: only colorless spells (no color identity).
            # Triggered ability counters are not modelled separately.
            if tmpl.color_identity:
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"Consign to Memory fizzles (target has color identity)")
                continue
            countered = game.stack.items.pop(i)
            countered_card = countered.source
            countered_card.zone = "graveyard"
            game.players[countered_card.owner].graveyard.append(countered_card)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"Consign to Memory counters {countered_card.name}")
            break


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
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                            f"Bowmasters deals 1 damage to {target.name}")
        else:
            opp.life -= 1
            game.players[controller].damage_dealt_this_turn += 1
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                            f"Bowmasters deals 1 damage to opponent (life: {opp.life})")
    else:
        opp.life -= 1
        game.players[controller].damage_dealt_this_turn += 1
        game.log.append(f"T{game.display_turn} P{controller+1}: "
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
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Bowmasters creates 1/1 Orc Army token")


@EFFECT_REGISTRY.register("Psychic Frog", EffectTiming.ETB,
                           description="Psychic Frog enters (discard-to-pump tracked via tags)")
def psychic_frog_etb(game, card, controller, targets=None, item=None):
    """Psychic Frog: mark it so combat can pump it by discarding."""
    card.instance_tags.add("psychic_frog")


@EFFECT_REGISTRY.register("Damnation", EffectTiming.SPELL_RESOLVE,
                           description="Destroy all creatures")
def damnation_resolve(game, card, controller, targets=None, item=None):
    """Damnation: destroy all creatures. They can't be regenerated
    (no regeneration mechanic modeled in this engine, so no-op) —
    still subject to CR 702.12b indestructible like any destroy
    effect (bug fix: the pre-migration handler destroyed unconditionally,
    with no indestructible check at all)."""
    _resolve_board_sweep(
        game, card, controller, targets, item,
        action="destroy", types=frozenset({"creature"}),
    )


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
            f"T{game.display_turn} P{controller+1}: "
            f"Walking Ballista deals {damage_to_creature} to {target.name}")
        
        # Send remaining damage to opponent's face
        if remaining > 0:
            card.plus_counters -= remaining
            opp.life -= remaining
            game.players[controller].damage_dealt_this_turn += remaining
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"Walking Ballista deals {remaining} to opponent "
                f"(life: {opp.life})")
    else:
        # No killable creature — all damage to face
        card.plus_counters = 0
        opp.life -= counters
        game.players[controller].damage_dealt_this_turn += counters
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
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


# Sleight of Hand handler removed — oracle_resolver detects
# "put one of them into your hand" and draws 1 as approximation.


# Reckless Impulse / Wrenn's Resolve / Glimpse the Impossible handlers
# removed — oracle_resolver.resolve_spell_from_oracle covers the
# "exile the top N cards … you may play those cards" pattern as draw N.
# Heroes' Hangout removed — oracle says "Scry 2. Draw a card." which
# matches the existing draw-a-card pattern.


@EFFECT_REGISTRY.register("Valakut Awakening // Valakut Stoneforge", EffectTiming.SPELL_RESOLVE,
                           description="Put cards from hand on bottom, draw that many + 1")
def valakut_awakening_resolve(game, card, controller, targets=None, item=None):
    # Simplified: draw 3 (redraw hand minus best cards)
    player = game.players[controller]
    hand_size = len(player.hand)
    # Put worst cards on bottom, keep best, draw replacements
    # Simplified: draw 2 (net card selection)
    game.draw_cards(controller, 2)


# March of Reckless Joy handler removed — its "exile the top X …
# you may play up to two" shape routes through the tagged
# impulse-reveal branch in oracle_resolver (play-cap sub-shape), so
# the impulse family stays draw-trigger-free (CR 121.1c). The old
# handler drew 2 through draw_cards and fired Bowmasters — the exact
# M1/R1 bug reintroduced for one card.


# ═══════════════════════════════════════════════════════════════════
# Amulet Titan effects
# ═══════════════════════════════════════════════════════════════════

@EFFECT_REGISTRY.register("Explore", EffectTiming.SPELL_RESOLVE,
                           description="Draw a card, may play an additional land this turn")
def explore_resolve(game, card, controller, targets=None, item=None):
    game.draw_cards(controller, 1)
    game.players[controller].extra_land_drops += 1
    game.log.append(f"T{game.display_turn} P{controller+1}: Explore — draw 1, extra land drop")


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
        game.log.append(f"T{game.display_turn} P{controller+1}: "
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
        game.log.append(f"T{game.display_turn} P{controller+1}: "
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
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Snapcaster gives flashback to {best.name}")


# Wall of Omens ETB handler removed — oracle_resolver ETB-draw pattern
# already fires "When this creature enters, draw a card".


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
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                            f"Spell Queller exiles {spell_card.name}")


# ─── Affinity cards ─────────────────────────────────────────────────────

# "Thought Monitor" (ETB: draw two cards) used to be registered here.
# Removed as part of the EFFECT_REGISTRY draw-N ETB cluster
# consolidation (docs/design/rules-foundation-sweep-tracker.md, Phase
# 3) — its one-line body ("When this creature enters, draw two
# cards.") is now covered end-to-end by the generic
# `engine.oracle_resolver.resolve_etb_from_oracle` fallback's
# "when ~ enters, draw N card(s)" branch, confirmed redundant by
# `tests/test_etb_draw_n_shared_resolver.py::TestGenericResolverCoversRegisteredHandlers`
# before deletion.


@EFFECT_REGISTRY.register("Dispatch", EffectTiming.SPELL_RESOLVE,
                           description="Tap creature; with metalcraft, exile it instead")
def dispatch_resolve(game, card, controller, targets=None, item=None):
    from .cards import CardType
    opponent = 1 - controller
    opp = game.players[opponent]
    artifact_count = sum(1 for c in game.players[controller].battlefield
                         if CardType.ARTIFACT in c.template.card_types)
    has_metalcraft = artifact_count >= 3

    if targets:
        for tid in targets:
            target = game.get_card_by_id(tid)
            if target and target.zone == "battlefield" and target.template.is_creature:
                if has_metalcraft:
                    game._exile_permanent(target)
                    game.log.append(f"T{game.display_turn} P{controller+1}: "
                                    f"Dispatch exiles {target.name} (metalcraft)")
                else:
                    target.tapped = True
                    game.log.append(f"T{game.display_turn} P{controller+1}: "
                                    f"Dispatch taps {target.name}")
                return
    # Auto-target: pick best opponent creature
    valid = [c for c in opp.creatures]
    if valid:
        target = max(valid, key=lambda c: (c.power or 0) + (c.toughness or 0))
        if has_metalcraft:
            game._exile_permanent(target)
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                            f"Dispatch exiles {target.name} (metalcraft)")
        else:
            target.tapped = True
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                            f"Dispatch taps {target.name}")


@EFFECT_REGISTRY.register("Haywire Mite", EffectTiming.DIES,
                           description="Gain 2 life on death")
def haywire_mite_dies(game, card, controller, targets=None, item=None):
    game.players[controller].life += 2
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Haywire Mite dies: gain 2 life (now {game.players[controller].life})")


@EFFECT_REGISTRY.register("Hurkyl's Recall", EffectTiming.SPELL_RESOLVE,
                           description="Return all artifacts target player owns to hand")
def hurkyls_recall_resolve(game, card, controller, targets=None, item=None):
    from .cards import CardType
    opponent = 1 - controller
    opp = game.players[opponent]
    artifacts = [c for c in opp.battlefield
                 if CardType.ARTIFACT in c.template.card_types]
    count = len(artifacts)
    for a in artifacts:
        game._bounce_permanent(a)
    if count:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Hurkyl's Recall bounces {count} artifacts from P{opponent+1}")


@EFFECT_REGISTRY.register("Relic of Progenitus", EffectTiming.ETB,
                           description="Graveyard hate artifact")
def relic_of_progenitus_etb(game, card, controller, targets=None, item=None):
    # Simplified: on ETB, exile all cards from opponent's graveyard
    # (models the activated "exile all graveyards" ability as immediate value)
    opponent = 1 - controller
    opp = game.players[opponent]
    count = len(opp.graveyard)
    if count:
        for c in opp.graveyard:
            c.zone = "exile"
            opp.exile.append(c)
        opp.graveyard.clear()
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Relic of Progenitus exiles {count} cards from P{opponent+1}'s graveyard")
    game.draw_cards(controller, 1)
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Relic of Progenitus: draw 1 card")


@EFFECT_REGISTRY.register("Ethersworn Canonist", EffectTiming.ETB,
                           description="Each player can only cast one nonartifact spell per turn")
def ethersworn_canonist_etb(game, card, controller, targets=None, item=None):
    # Tag presence on battlefield restricts spellcasting (checked in can_cast)
    card.instance_tags.add("canonist_active")
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Ethersworn Canonist enters — nonartifact spell restriction active")


@EFFECT_REGISTRY.register("Torpor Orb", EffectTiming.ETB,
                           description="Creatures entering don't cause abilities to trigger")
def torpor_orb_etb(game, card, controller, targets=None, item=None):
    # Tag presence on battlefield suppresses creature ETBs (checked in _handle_etb)
    card.instance_tags.add("torpor_orb_active")
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Torpor Orb enters — creature ETB abilities suppressed")


# ═══════════════════════════════════════════════════════════════════
# Ral, Monsoon Mage — cost reduction handled by oracle parser;
# this adds the storm-count transform trigger
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Ral, Monsoon Mage // Ral, Leyline Prodigy",
                           EffectTiming.ETB,
                           description="Ral ETB: cost reducer on battlefield")
def ral_monsoon_etb(game, card, controller, targets=None, item=None):
    """Ral's cost reduction is handled by oracle text parsing.
    ETB just logs deployment."""
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"Ral, Monsoon Mage enters — instants/sorceries cost {{1}} less")


# ═══════════════════════════════════════════════════════════════════
# Fable of the Mirror-Breaker — Saga
# Ch I: Create 2/2 Goblin with haste
# Ch II: Discard up to 2, draw that many
# Ch III: Transform (create 2/2 creature copy-maker)
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Fable of the Mirror-Breaker // Reflection of Kiki-Jiki",
                           EffectTiming.ETB,
                           description="Saga Ch.I: Create 2/2 Goblin Shaman with haste")
def fable_etb(game, card, controller, targets=None, item=None):
    """Chapter I: Create a 2/2 Goblin token with haste."""
    from engine.cards import Keyword
    tokens = game.create_token(controller, "goblin", power=2, toughness=2,
                               extra_keywords={Keyword.HASTE})
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"Fable Ch.I: Create 2/2 Goblin Shaman with haste")
    if not hasattr(card, 'other_counters') or card.other_counters is None:
        card.other_counters = {}
    card.other_counters['lore'] = 1


# ═══════════════════════════════════════════════════════════════════
# Kappa Cannoneer & Pinnacle Emissary — recurring CR 603 triggers, NOT
# one-time ETB self-effects. Their dedicated EffectTiming.ETB handlers
# were removed (docs/design/2026-08-12_recurring_trigger_dispatch_gap.md):
#   * Kappa ("whenever this creature or another artifact you control
#     enters, put a +1/+1 counter on it; it can't be blocked this turn")
#     is now dispatched per qualifying ETB by
#     engine.triggers.TriggerManager.trigger_etb via the typed field
#     template.enters_type_counter — one counter per artifact ETB, the
#     self-ETB counted exactly once (no more approximate "half the
#     artifacts on board" one-shot).
#   * Pinnacle Emissary ("whenever you cast an artifact spell, create a
#     1/1 flying Drone") is now dispatched per artifact cast by
#     engine.oracle_resolver.resolve_spell_cast_trigger via the typed
#     field template.cast_trigger_token — one Drone per artifact spell,
#     not a one-shot projection of free artifacts in hand.
# The same two dispatchers serve every card of each shape (Young
# Pyromancer, Monastery Mentor, Talrand, …), artifact or not.


# ═══════════════════════════════════════════════════════════════════
# Arcbound Ravager — modular: sac artifacts for counters
# Sim approximation: grows by sacrificing artifacts
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Arcbound Ravager", EffectTiming.ETB,
                           description="Arcbound Ravager: 0/0 + modular 1, grows by sacrificing artifacts")
def arcbound_ravager_etb(game, card, controller, targets=None, item=None):
    """Arcbound Ravager ETB: starts as 1/1 (modular 1), grows later.

    Uses plus_counters on the instance instead of writing to the shared
    template (prior impl mutated template.power/toughness, leaking across
    games the same way Blood Moon did).
    """
    card.plus_counters += 1
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"Arcbound Ravager enters as {card.power}/{card.toughness} (modular 1)")


# ═══════════════════════════════════════════════════════════════════
# Emry, Lurker of the Loch — ETB: mill 4
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Emry, Lurker of the Loch", EffectTiming.ETB,
                           description="Emry ETB: mill 4 cards")
def emry_etb(game, card, controller, targets=None, item=None):
    """Emry ETB: mill 4 cards into graveyard."""
    player = game.players[controller]
    milled = []
    for _ in range(min(4, len(player.library))):
        c = player.library.pop(0)
        c.zone = "graveyard"
        player.graveyard.append(c)
        milled.append(c.name)
    if milled:
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Emry mills {len(milled)}: {', '.join(milled)}")


# ═══════════════════════════════════════════════════════════════════
# Mana rituals — add mana on resolve (critical for Ruby Storm)
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Pyretic Ritual", EffectTiming.SPELL_RESOLVE,
                           description="Add RRR (net +1R)")
def pyretic_ritual_resolve(game, card, controller, targets=None, item=None):
    """Pyretic Ritual: Add {R}{R}{R}."""
    player = game.players[controller]
    player.mana_pool.add("R", 3)
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"Pyretic Ritual adds RRR (pool: {player.mana_pool})")


@EFFECT_REGISTRY.register("Desperate Ritual", EffectTiming.SPELL_RESOLVE,
                           description="Add RRR (net +1R)")
def desperate_ritual_resolve(game, card, controller, targets=None, item=None):
    """Desperate Ritual: Add {R}{R}{R}."""
    player = game.players[controller]
    player.mana_pool.add("R", 3)
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"Desperate Ritual adds RRR (pool: {player.mana_pool})")


@EFFECT_REGISTRY.register("Manamorphose", EffectTiming.SPELL_RESOLVE,
                           description="Add 2 mana of any color, draw a card")
def manamorphose_resolve(game, card, controller, targets=None, item=None):
    """Manamorphose: Add two mana in any combination + draw a card."""
    player = game.players[controller]
    # Add RR by default (Storm deck context)
    player.mana_pool.add("R", 2)
    # Draw a card
    if player.library:
        drawn = player.library.pop(0)
        drawn.zone = "hand"
        player.hand.append(drawn)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Manamorphose adds RR, draws {drawn.name}")
    else:
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Manamorphose adds RR (no cards to draw)")


# ═══════════════════════════════════════════════════════════════════
# Teferi, Time Raveler — bounce + shut off instant speed
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Teferi, Time Raveler", EffectTiming.ETB,
                           description="Bounce target opponent permanent, draw a card")
def teferi_t3_etb(game, card, controller, targets=None, item=None):
    """T3feri ETB: bounce opponent's best nonland permanent, draw a card."""
    opp_idx = 1 - controller
    opp = game.players[opp_idx]
    player = game.players[controller]

    # Bounce best opponent nonland permanent (by threat)
    nonlands = [c for c in opp.battlefield if not c.template.is_land]
    if nonlands:
        target = max(nonlands, key=lambda c: _threat_score(c, game, opp))
        opp.battlefield.remove(target)
        if target in opp.creatures:
            opp.creatures.remove(target)
        target.zone = "hand"
        opp.hand.append(target)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Teferi bounces {target.name}")

    # Draw a card
    if player.library:
        drawn = player.library.pop(0)
        drawn.zone = "hand"
        player.hand.append(drawn)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Teferi draws {drawn.name}")

    # Static: opponents can only cast at sorcery speed.
    # Reducing counter_density is an AI hint; the hard engine restriction
    # lives in GameState via the `_teferi_shutdown_active` helper that
    # checks controller-side "cast at sorcery speed" permanents whenever
    # a response window would open. Setting counter_density to 0 keeps
    # the BHI and EV heuristics aligned with the hard restriction.
    opp.counter_density = 0.0


# ═══════════════════════════════════════════════════════════════════
# Supreme Verdict — uncounterable board wipe
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Supreme Verdict", EffectTiming.SPELL_RESOLVE,
                           description="Destroy all creatures (can't be countered)")
def supreme_verdict_resolve(game, card, controller, targets=None, item=None):
    """Supreme Verdict: destroy all creatures. "Can't be countered" is
    a casting-time property, not a resolution-time board-sweep effect
    — out of this cluster's scope. (Verified: no `is_uncounterable`/
    "can't be countered" enforcement exists anywhere in engine/ today;
    Supreme Verdict is castable-and-counterable like any other spell
    in the current implementation. A real, separate gap — belongs
    with 1a's counter-tax framework, not this sweep-resolver slice.)"""
    _resolve_board_sweep(
        game, card, controller, targets, item,
        action="destroy", types=frozenset({"creature"}),
    )


# ═══════════════════════════════════════════════════════════════════
# Goblin Bombardment — sacrifice creature, deal 1 damage
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Goblin Bombardment", EffectTiming.ETB,
                           description="Enchantment: sacrifice a creature, deal 1 damage to any target")
def goblin_bombardment_etb(game, card, controller, targets=None, item=None):
    """Goblin Bombardment enters — mark it for activated ability processing."""
    # The actual sacrifice-for-damage is handled in game_runner's
    # _activate_goblin_bombardment method. ETB just logs.
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"Goblin Bombardment enters — sacrifice creatures for 1 damage each")


# ═══════════════════════════════════════════════════════════════════
# Blood Moon — nonbasic lands are Mountains
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Blood Moon", EffectTiming.ETB,
                           description="Nonbasic lands are Mountains")
def blood_moon_etb(game, card, controller, targets=None, item=None):
    """Blood Moon enters: opponent's nonbasic lands become Mountains.

    CRITICAL: Do NOT mutate `land.template.produces_mana` — templates are
    shared across every CardInstance of that land in every game in the
    matrix worker. The old implementation permanently corrupted the
    CardDatabase for all subsequent games, which is the primary cause of
    Boros's 94% WR (Blood Moon SB → opponent lands become Mountains in
    game 1 → stay broken for games 2..N because mana-tap logic reads the
    shared template).

    Fix: give each affected land instance its own shallow-copied template
    with produces_mana=['R']. Only the instance sees the change; the
    shared CardDatabase template is untouched.
    """
    import copy
    opp_idx = 1 - controller
    opp = game.players[opp_idx]
    affected = 0
    for land in opp.lands:
        supertypes = getattr(land.template, 'supertypes', [])
        if 'Basic' in supertypes:
            continue
        old_colors = set(land.template.produces_mana)
        if old_colors == {'R'} or old_colors == set():
            continue
        # Shallow-copy the template, then replace produces_mana in the copy.
        # Assigning land.template rebinds only this instance's reference.
        per_instance_tmpl = copy.copy(land.template)
        per_instance_tmpl.produces_mana = ['R']
        land.template = per_instance_tmpl
        affected += 1
    if affected > 0:
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Blood Moon: {affected} opponent nonbasic lands become Mountains")


# ═══════════════════════════════════════════════════════════════════
# Celestial Purge — exile target red or black permanent
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Celestial Purge", EffectTiming.SPELL_RESOLVE,
                           description="Exile target red or black permanent")
def celestial_purge_resolve(game, card, controller, targets=None, item=None):
    """Exile best red or black permanent opponent controls."""
    opp_idx = 1 - controller
    opp = game.players[opp_idx]

    red_black = [c for c in opp.battlefield
                 if not c.template.is_land
                 and any(col.value in ('R', 'B') for col in c.template.color_identity)]
    if red_black:
        target = max(red_black, key=lambda c: _threat_score(c, game, opp))
        game._exile_permanent(target)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Celestial Purge exiles {target.name}")
    else:
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Celestial Purge: no valid red/black target")


# ═══════════════════════════════════════════════════════════════════
# Thraben Charm — modal: damage / destroy enchantment / exile graveyard
# ═══════════════════════════════════════════════════════════════════
# Oracle text (https://scryfall.com/card/dft/207/thraben-charm):
#
#   Choose one —
#   • Thraben Charm deals damage equal to twice the number of creatures
#     you control to target creature.
#   • Destroy target enchantment.
#   • Exile any number of target players' graveyards.
#
# Mode selection at resolve time:
#   - If `targets` is supplied, dispatch by target type so the cast-time
#     pick (damage on a creature / destroy an enchantment) is respected.
#   - Otherwise pick the mode with the highest expected value killed:
#       damage:      best opp creature threat_value where 2N is lethal
#       enchantment: best opp enchantment threat_score
#       graveyard:   opp_gy_value when >= GRAVEYARD_HATE_FLOOR
#     Damage > enchantment > graveyard on ties.
@EFFECT_REGISTRY.register("Thraben Charm", EffectTiming.SPELL_RESOLVE,
                           description="Modal: 2N damage to creature / destroy enchantment / exile graveyard")
def thraben_charm_resolve(game, card, controller, targets=None, item=None):
    from .cards import CardType
    opp_idx = 1 - controller
    opp = game.players[opp_idx]
    me = game.players[controller]
    n = len(me.creatures)
    damage = 2 * n

    # Threshold below which graveyard exile is dead value (rules constant
    # mirroring the previous implementation; protects against weak picks).
    GRAVEYARD_HATE_FLOOR = 10

    def _deal_damage(target_creature):
        target_creature.damage_marked += damage
        if target_creature.is_dead:
            game._creature_dies(target_creature)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Thraben Charm deals {damage} to {target_creature.name}")

    def _destroy_enchantment(target_enchantment):
        game._permanent_destroyed(target_enchantment)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Thraben Charm destroys enchantment {target_enchantment.name}")

    def _exile_graveyard():
        gy_total = sum((c.template.cmc or 0) for c in opp.graveyard)
        for c in list(opp.graveyard):
            opp.graveyard.remove(c)
            c.zone = "exile"
            opp.exile.append(c)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Thraben Charm exiles opponent's graveyard ({gy_total} CMC)")

    # Caller-supplied target — dispatch by type.
    if targets:
        for tid in targets:
            target = game.get_card_by_id(tid)
            if not target or target.zone != "battlefield":
                continue
            if target.template.is_creature:
                _deal_damage(target)
                return
            if CardType.ENCHANTMENT in target.template.card_types:
                _destroy_enchantment(target)
                return
        # Fall through to auto-pick if no usable supplied target.

    # Auto-pick: score each mode by its principled value and choose the
    # highest. Damage mode value uses creature_threat_value (the same
    # primitive the AI scoring layer uses) so a Plating-equipped 9/1
    # outranks an unequipped 0/2.
    from ai.ev_evaluator import creature_threat_value, snapshot_from_game

    best_creature = None
    best_creature_value = 0.0
    if damage > 0:
        _tc_snap = snapshot_from_game(game, controller)
        for c in opp.creatures:
            tough = c.toughness if c.toughness is not None else (c.template.toughness or 0)
            if damage >= tough > 0:  # 2N kills it
                v = creature_threat_value(c, _tc_snap)
                if v > best_creature_value:
                    best_creature_value = v
                    best_creature = c

    enchantments = [c for c in opp.battlefield
                    if CardType.ENCHANTMENT in c.template.card_types]
    best_enchantment = None
    best_enchantment_value = 0.0
    if enchantments:
        best_enchantment = max(enchantments,
                               key=lambda c: _threat_score(c, game, opp))
        best_enchantment_value = _threat_score(best_enchantment, game, opp)

    opp_gy_value = sum((c.template.cmc or 0) for c in opp.graveyard)
    graveyard_value = opp_gy_value if opp_gy_value >= GRAVEYARD_HATE_FLOOR else 0.0

    # Choose the highest-EV mode. Ties prefer damage > enchantment > GY
    # because removing a battlefield threat is generally tempo-positive
    # while graveyard hate is situational.
    options = [
        ("damage", best_creature_value, lambda: _deal_damage(best_creature)),
        ("enchantment", best_enchantment_value,
         lambda: _destroy_enchantment(best_enchantment)),
        ("graveyard", graveyard_value, _exile_graveyard),
    ]
    options.sort(key=lambda x: -x[1])  # stable: preserves declaration order on ties
    mode_name, mode_value, fire = options[0]
    if mode_value > 0:
        fire()
    else:
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Thraben Charm: no high-value mode available")


# ═══════════════════════════════════════════════════════════════════
# Phelia, Exuberant Shepherd — blink on attack
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Phelia, Exuberant Shepherd", EffectTiming.ATTACK,
                           description="When attacks, exile another nonland permanent; return at end step")
def phelia_attack(game, card, controller, targets=None, item=None):
    """Phelia attacks: exile a nonland permanent. Returns at end step.

    Smart targeting:
    - Own ETB creature (Solitude, Phlage, Omnath) = repeating value engine
    - Opponent's best nonland permanent = tempo removal (returns end step)
    """
    player = game.players[controller]
    opp_idx = 1 - controller
    opp = game.players[opp_idx]

    # Find own ETB creatures (excluding Phelia herself)
    own_etb = [c for c in player.battlefield
               if c.instance_id != card.instance_id
               and not c.template.is_land
               and 'etb_value' in getattr(c.template, 'tags', set())]

    # Find opponent nonlands
    opp_nonlands = [c for c in opp.battlefield if not c.template.is_land]

    target = None
    target_owner = None

    if own_etb:
        # Blink own best ETB creature — repeating value engine.
        # Ranking is delegated to the AI primitive `_threat_score`
        # (which routes creatures through `creature_threat_value`):
        # exile-ETB > burn-ETB > cantrip-ETB falls out of the
        # principled formula without any per-card tuning.
        target = max(own_etb, key=lambda c: _threat_score(c))
        target_owner = controller
    elif opp_nonlands:
        # Tempo: exile opponent's best nonland (it returns at end step)
        target = max(opp_nonlands,
                     key=lambda c: _threat_score(c, game, opp))
        target_owner = opp_idx

    if target is None:
        return

    # Exile the target
    owner_player = game.players[target_owner]
    if target in owner_player.battlefield:
        owner_player.battlefield.remove(target)
        if target.template.is_creature and target in owner_player.creatures:
            owner_player.creatures.remove(target)
        target.zone = "exile"
        owner_player.exile.append(target)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Phelia exiles {target.name} (P{target_owner+1}'s)")

        # Schedule return at end step
        if not hasattr(game, '_phelia_returns'):
            game._phelia_returns = []
        game._phelia_returns.append((target, target_owner, controller))

        # If it returns under controller's control, Phelia gets +1/+1
        if target_owner == controller:
            card.plus_counters += 1
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"Phelia gets +1/+1 counter ({card.power}/{card.toughness})")


@EFFECT_REGISTRY.register("Phelia, Exuberant Shepherd", EffectTiming.END_STEP,
                           description="Return Phelia-exiled cards to battlefield")
def phelia_end_step(game, card, controller, targets=None, item=None):
    """Return all Phelia-exiled permanents to the battlefield."""
    if not hasattr(game, '_phelia_returns') or not game._phelia_returns:
        return

    returns = list(game._phelia_returns)
    game._phelia_returns.clear()

    for exiled_card, owner_idx, phelia_controller in returns:
        owner = game.players[owner_idx]
        if exiled_card in owner.exile:
            owner.exile.remove(exiled_card)
            exiled_card.zone = "battlefield"
            owner.battlefield.append(exiled_card)
            if exiled_card.template.is_creature:
                exiled_card.enter_battlefield()
                owner.creatures.append(exiled_card)
            game.log.append(
                f"T{game.display_turn}: "
                f"{exiled_card.name} returns to battlefield (P{owner_idx+1})")
            # Trigger ETB on return
            game.trigger_etb(exiled_card, owner_idx)


# ═══════════════════════════════════════════════════════════════════
# All Is Dust — sacrifice all colored permanents
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("All Is Dust", EffectTiming.SPELL_RESOLVE,
                           description="Each player sacrifices all colored permanents")
def all_is_dust_resolve(game, card, controller, targets=None, item=None):
    """All Is Dust: each player SACRIFICES (not destroys — CR 701.20a,
    indestructible does not apply) all colored nonland permanents they
    control (leaves colorless permanents, e.g. Eldrazi, alive). Bug
    fix migrating this handler: the pre-migration check used
    `color_identity` (a format-legality superset — can include colors
    not actually on the permanent, e.g. from a colored activated-
    ability cost) instead of `colors` (the permanent's real color,
    CR 105.2a) — see `_all_is_dust_has_color` above."""
    _resolve_board_sweep(
        game, card, controller, targets, item,
        action="sacrifice", types=frozenset({"permanent_nonland"}),
        filter_fn=_all_is_dust_has_color,
        log_verb="sacrifices", log_noun="colored permanents",
    )


# ═══════════════════════════════════════════════════════════════════
# Expedition Map — search for a land (Tron assembly)
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Expedition Map", EffectTiming.ETB,
                           description="Artifact: sacrifice to search for a land card")
def expedition_map_etb(game, card, controller, targets=None, item=None):
    """Expedition Map ETB: auto-activate to find missing Tron piece.

    In real MTG this is an activated ability ({2}, T, sacrifice).
    Simplified: activates automatically when controller has 3+ mana
    and is missing a Tron piece.
    """
    # Don't auto-activate on ETB — handled in game_runner end step
    pass


# ═══════════════════════════════════════════════════════════════════
# Ratchet Bomb — destroy permanents with specific CMC
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Ratchet Bomb", EffectTiming.ETB,
                           description="Artifact: tick up counters, sacrifice to destroy CMC=X")
def ratchet_bomb_etb(game, card, controller, targets=None, item=None):
    """Ratchet Bomb: enters with 0 charge counters. Auto-ticks each turn."""
    card.other_counters["charge"] = 0
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"Ratchet Bomb enters with 0 charge counters")


# ═══════════════════════════════════════════════════════════════════
# Leyline of the Guildpact — free T0 enchantment, domain enabler
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Leyline of the Guildpact", EffectTiming.ETB,
                           description="All lands are every basic type, all permanents are all colors")
def leyline_guildpact_etb(game, card, controller, targets=None, item=None):
    """Leyline of the Guildpact: enables full domain (5 basic land types).

    Static: all your lands are every basic land type → domain = 5.
    Static: all nonland permanents you control are all colors.
    """
    player = game.players[controller]
    # Set a flag for domain calculation
    player._leyline_guildpact = True
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"Leyline of the Guildpact: all lands are every basic type (domain=5)")


# ═══════════════════════════════════════════════════════════════════
# Territorial Kavu — domain P/T + modal attack trigger
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Territorial Kavu", EffectTiming.ETB,
                           description="P/T = domain count")
def territorial_kavu_etb(game, card, controller, targets=None, item=None):
    """Territorial Kavu: 0/0 base + domain. With Leyline = 5/5."""
    # Power/toughness handled by CardInstance._dynamic_base_power
    # which checks domain. Just log.
    domain = game._count_domain(controller)
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"Territorial Kavu enters as {domain}/{domain} (domain={domain})")


@EFFECT_REGISTRY.register("Territorial Kavu", EffectTiming.ATTACK,
                           description="Modal: loot (discard, draw) / exile up to 1 from a graveyard")
def territorial_kavu_attack(game, card, controller, targets=None, item=None):
    """Territorial Kavu attack trigger — modal:

        • Discard a card. If you do, draw a card.
        • Exile up to one target card from a graveyard.

    Mode selection rules (oracle-grounded, no card-name hardcoding):

      - Loot value = the marginal benefit of a card swap. We model
        the discard target as the worst card in hand: the lowest-CMC
        non-creature, non-removal card. If hand has only lands, the
        loot is hollow (we still discard the worst land if no
        graveyard target exists, since "if you do" requires the
        discard to fire and net zero is acceptable).

      - Graveyard-exile value = the highest CMC of any non-token
        card in opponent's graveyard. CMC is the principled proxy
        already used by Thraben Charm and Relic of Progenitus for
        graveyard-hate value (delve / reanimation / flashback fuel
        scales with CMC).

      - Tie-break: if opp graveyard has a target with CMC ≥ 1 AND
        the controller has nothing better than a land to discard,
        prefer the graveyard-exile mode. Otherwise prefer loot.

      - Both modes dead → no-op (logs only).
    """
    me = game.players[controller]
    opp = game.players[1 - controller]

    # ── Score graveyard mode ──
    gy_targets = [c for c in opp.graveyard if not c.template.is_land]
    best_gy_target = None
    if gy_targets:
        best_gy_target = max(gy_targets, key=lambda c: (c.template.cmc or 0))
    gy_value = (best_gy_target.template.cmc or 0) if best_gy_target else 0

    # ── Score loot mode ──
    # Hand non-land count is the principled proxy: a non-land discard is
    # cheaper to give up than a land in midgame, so loot is "good" when
    # we have at least one non-land we'd happily discard. But even a
    # land-only loot is fine (we lose a land, gain an unknown card =
    # cycling value).
    hand = me.hand
    has_hand = len(hand) > 0
    loot_value = 1 if has_hand else 0  # any non-empty hand → loot can fire

    # ── Tie-break: graveyard hate beats loot when GY target is
    #     non-trivial (CMC ≥ 1) and loot would discard a land or empty
    #     hand. Otherwise loot wins.
    pick_gy = False
    if gy_value > 0:
        # Prefer GY hate when loot is hollow OR when GY target is
        # high-impact (CMC ≥ 3 — same threshold as Thraben Charm
        # graveyard floor scaled per-card instead of per-pile).
        nonland_in_hand = any(not c.template.is_land for c in hand)
        if not nonland_in_hand:
            pick_gy = True
        elif gy_value >= 3:
            pick_gy = True

    if pick_gy and best_gy_target is not None:
        opp.graveyard.remove(best_gy_target)
        best_gy_target.zone = "exile"
        opp.exile.append(best_gy_target)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Territorial Kavu exiles {best_gy_target.name} from opponent GY"
        )
        return

    if loot_value > 0:
        # Discard the worst card: prefer extra land if we already have
        # plenty, else prefer the lowest-CMC non-essential spell.
        # Simple rule: lowest CMC overall, with lands preferred when we
        # have surplus lands (≥ 4 lands in play). Otherwise discard the
        # lowest-CMC non-land (keeps the mana base flowing).
        lands_in_play = sum(1 for c in me.battlefield if c.template.is_land)
        if lands_in_play >= 4:
            worst = min(hand, key=lambda c: (
                0 if c.template.is_land else 1,  # prefer lands
                c.template.cmc or 0,
            ))
        else:
            nonlands = [c for c in hand if not c.template.is_land]
            if nonlands:
                worst = min(nonlands, key=lambda c: c.template.cmc or 0)
            else:
                # Land-only hand and we don't have surplus lands —
                # still must discard if loot mode chosen.  Discard the
                # extra land; cantrip value still net-zero.
                worst = min(hand, key=lambda c: c.template.cmc or 0)
        hand.remove(worst)
        worst.zone = "graveyard"
        me.graveyard.append(worst)
        game.draw_cards(controller, 1)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"Territorial Kavu loots — discard {worst.name}, draw 1"
        )
        return

    # Both modes dead: log no-op.
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"Territorial Kavu attack trigger: no legal mode (empty hand, "
        f"empty opposing graveyard)"
    )


# ═══════════════════════════════════════════════════════════════════
# Scion of Draco — domain cost reduction + keyword soup
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Scion of Draco", EffectTiming.ETB,
                           description="Flying 4/4; creatures you control gain a "
                                       "color-conditional keyword")
def scion_of_draco_etb(game, card, controller, targets=None, item=None):
    """Scion of Draco: 4/4 flying. Static ability (continuous, not a
    one-time ETB event): 'Each creature you control has vigilance if
    it's white, hexproof if it's blue, lifelink if it's black, first
    strike if it's red, and trample if it's green.' Registered as
    ContinuousEffects (engine/continuous_effects.py) so the grant is
    re-derived every recalculate() — per-creature by that creature's
    OWN printed color, not the controller's overall color range — and
    automatically retracts if Scion leaves the battlefield (the
    manager's stale-source cleanup). Mechanic-class: "creatures you
    control have keyword K if they're color C" — this registration
    shape generalizes to any future card with the same clause."""
    from .cards import Keyword
    from .mana import Color
    from .continuous_effects import ContinuousEffect, Layer, create_lord_effect

    color_keyword_map = {
        Color.WHITE: Keyword.VIGILANCE,
        Color.BLUE: Keyword.HEXPROOF,
        Color.BLACK: Keyword.LIFELINK,
        Color.RED: Keyword.FIRST_STRIKE,
        Color.GREEN: Keyword.TRAMPLE,
    }
    for color, keyword in color_keyword_map.items():
        def affected(g, c, _color=color, _controller=controller):
            return (c.controller == _controller
                    and c.template.is_creature
                    and _color in c.template.colors)
        for effect in create_lord_effect(
                source_id=card.instance_id, source_name=card.template.name,
                affected_fn=affected, power_bonus=0, toughness_bonus=0,
                keyword_grants={keyword},
                description=f"grants {keyword.name} to your {color.name} creatures"):
            game.continuous_effects.register(effect)
    game.continuous_effects.recalculate(game)
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"Scion of Draco enters — creatures gain vigilance/hexproof/"
        f"lifelink/first strike/trample if white/blue/black/red/green")


# ═══════════════════════════════════════════════════════════════════
# Doorkeeper Thrull — flash flyer, shuts off ETB triggers
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Doorkeeper Thrull", EffectTiming.ETB,
                           description="Flash, flying, artifacts/creatures entering don't trigger abilities")
def doorkeeper_thrull_etb(game, card, controller, targets=None, item=None):
    """Doorkeeper Thrull: Torpor Orb on a 1/2 flash flyer."""
    # Set a flag to suppress future ETB triggers
    if not hasattr(game, '_etb_suppressed'):
        game._etb_suppressed = set()
    game._etb_suppressed.add(controller)
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"Doorkeeper Thrull: opponent ETB triggers suppressed")


# ═══════════════════════════════════════════════════════════════════
# Scapeshift — sacrifice lands, search for same number
# ═══════════════════════════════════════════════════════════════════
@EFFECT_REGISTRY.register("Scapeshift", EffectTiming.SPELL_RESOLVE,
                           description="Sacrifice any number of lands, search for that many")
def scapeshift_resolve(game, card, controller, targets=None, item=None):
    """Scapeshift: sacrifice N lands → search library for N lands → battlefield tapped.

    With Amulet of Vigor: all enter untapped → massive mana.
    Key targets: bounce lands (for Amulet untap triggers), utility lands.

    ETB ordering rule (simultaneous entry):
      MTG rules treat all fetched lands as entering simultaneously.  Each
      land's ETB trigger sees the *full* set of newly-entered lands, not
      only those whose ETBs were processed before it.  Concretely:

        Phase 1 — enter all lands (append to battlefield, no ETBs yet).
        Phase 2 — fire each land's ETBs in sequence.

      Without this two-phase split, a bounce land entering before other
      fetched lands fires its ETB against an empty battlefield and returns
      itself — producing zero net lands even when non-bounce lands were
      also fetched.  The simultaneous-entry model lets each bounce land's
      ETB see its co-entrants and return a *different* land, and lets any
      ETB whose land has already been bounced skip gracefully (the
      `apply_land_etb_static` guard `if land not in player.battlefield`
      handles that case).
    """
    player = game.players[controller]
    my_lands = [c for c in player.battlefield if c.template.is_land]

    # Only sacrifice if we have enough lands for a meaningful payoff
    if len(my_lands) < 4:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Scapeshift: only {len(my_lands)} lands, not enough to sacrifice")
        return

    # Sacrifice all lands
    sac_count = len(my_lands)
    for land in list(my_lands):
        player.battlefield.remove(land)
        land.zone = "graveyard"
        player.graveyard.append(land)

    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Scapeshift sacrifices {sac_count} lands")

    # ── Search priority ───────────────────────────────────────────────
    # Check whether the player has any "untap on enter" watcher —
    # an Amulet-of-Vigor-style trigger or a Spelunking-style static.
    # This is done via LandManager (which already holds both oracle
    # patterns, using w_oracle to avoid the oracle-runtime-parse
    # ratchet; the ratchet exempts engine/land_manager.py's existing
    # runtime checks via the w_oracle variable naming convention).
    from .land_manager import LandManager as _LM
    has_untap_watcher = _LM.player_has_untap_on_enter_watcher(game, controller)

    library_lands = [c for c in player.library if c.template.is_land]

    def land_priority(c):
        # Use the load-time template flag (etb_return_land) to identify
        # bounce lands — no oracle string parsing at runtime here.
        is_bounce = getattr(c.template, 'etb_return_land', False)
        # Bounce lands are highest priority when an untap-on-enter watcher
        # (Amulet of Vigor, Spelunking) is in play; they generate extra
        # mana via the untap trigger before bouncing.  Without a watcher
        # they enter tapped, produce nothing before bouncing, and are no
        # better than a basic (the simultaneous-entry ETB fix still keeps
        # them viable, but they're not preferred over non-bounce options).
        if is_bounce and has_untap_watcher:
            return 3  # bounce land WITH untap watcher — prioritise
        if len(c.template.produces_mana) >= 2:
            return 2  # non-bounce dual / utility land
        if not is_bounce:
            return 1  # basic or single-colour land
        return 0  # bounce land WITHOUT untap watcher — lowest priority

    library_lands.sort(key=land_priority, reverse=True)

    # ── Phase 1: enter all lands (no ETBs yet) ───────────────────────
    # All fetched lands enter simultaneously per MTG rules.  We stage
    # them all onto the battlefield before firing any triggers so that
    # each land's ETB (Phase 2) sees the complete set of co-entrants.
    lands_entered: list = []
    for land in library_lands[:sac_count]:
        if land not in player.library:
            continue  # safety guard (shouldn't happen in Phase 1)
        player.library.remove(land)
        land.zone = "battlefield"
        land.tapped = True  # enters tapped (Amulet/Spelunking untaps in Phase 2)
        player.battlefield.append(land)
        lands_entered.append(land)

    # ── Phase 2: fire ETBs in LIFO order (MTG stack semantics) ──────
    # When multiple triggers go on the stack simultaneously the
    # controller chooses the order; LIFO (last-entered fires first)
    # maximises retention — e.g. with 4 bounce lands the first two
    # triggers each bounce one of the later ones, the later ones' own
    # triggers are then skipped because those lands are no longer on
    # the battlefield, leaving N/2 bounce lands on the field.
    # FIFO would instead produce a cascade that leaves only 1.
    # A land bounced to hand by an earlier trigger is handled by the
    # `if land not in player.battlefield: return` guard in
    # `apply_land_etb_static`, so it is skipped cleanly.
    from .oracle_resolver import resolve_etb_from_oracle
    for land in reversed(lands_entered):
        game._apply_land_etb_static(land, controller)
        game._apply_lands_enter_untapped(land, controller)
        resolve_etb_from_oracle(game, land, controller)
        game._trigger_landfall(controller)

    game.rng.shuffle(player.library)
    fetched = len(lands_entered)
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Scapeshift fetches {fetched} lands onto battlefield")
