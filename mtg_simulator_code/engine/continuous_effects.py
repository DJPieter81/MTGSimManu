"""
Continuous Effects / Layer System
=================================
Implements the seven-layer system from CR 613 for applying continuous effects
in the correct order. This replaces the scattered temp_power_mod, instance_tags,
and temp_keywords approach with a centralized, re-calculable system.

MTG Layer System (CR 613.1):
  Layer 1: Copy effects
  Layer 2: Control-changing effects
  Layer 3: Text-changing effects
  Layer 4: Type-changing effects
  Layer 5: Color-changing effects
  Layer 6: Ability-adding/removing effects
  Layer 7: Power/toughness effects
    7a: Characteristic-defining abilities (e.g., Tarmogoyf)
    7b: Set P/T to specific values
    7c: Modifications from +1/+1 and -1/-1 counters
    7d: Static abilities that modify P/T (e.g., "other creatures you control get +1/+1")
    7e: Spells/abilities that modify P/T (e.g., Giant Growth)

For our simulation, we primarily need Layers 6 and 7, since copy effects,
control changes, text changes, type changes, and color changes are rare
in Modern competitive play.

Design:
  - ContinuousEffect is a data class describing one effect
  - ContinuousEffectsManager recalculates all effects each time it's called
  - Effects are registered by source (card on battlefield) and removed when source leaves
  - The manager is called at key points: after ETB, after spells resolve, before combat
"""
from __future__ import annotations
from typing import Dict, List, Set, Optional, Callable, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field
from enum import Enum, auto

if TYPE_CHECKING:
    from .game_state import GameState
    from .cards import CardInstance

from .cards import Keyword, CardType


class Layer(Enum):
    """CR 613.1: The seven layers."""
    COPY = 1
    CONTROL = 2
    TEXT = 3
    TYPE = 4
    COLOR = 5
    ABILITY = 6
    POWER_TOUGHNESS = 7


class PTSublayer(Enum):
    """CR 613.4: Power/toughness sublayers."""
    CDA = auto()          # 7a: Characteristic-defining abilities
    SET_PT = auto()       # 7b: Set P/T to specific values
    COUNTERS = auto()     # 7c: +1/+1 and -1/-1 counters
    STATIC_MOD = auto()   # 7d: Static abilities (e.g., lord effects)
    SPELL_MOD = auto()    # 7e: Spells/abilities (e.g., Giant Growth)


@dataclass
class ContinuousEffect:
    """A single continuous effect applied to the game.

    Attributes:
        source_id: instance_id of the card producing this effect
        source_name: name of the source card (for debugging)
        layer: which layer this effect applies in
        pt_sublayer: for Layer 7, which sublayer
        affected: function (game, card) -> bool to determine if a card is affected
        apply: function (game, card) -> None to apply the effect
        description: human-readable description
        timestamp: when this effect was created (for dependency ordering)
        duration: "permanent", "end_of_turn", or "end_of_combat"
    """
    source_id: int
    source_name: str
    layer: Layer
    pt_sublayer: Optional[PTSublayer] = None
    affected: Optional[Callable] = None
    apply: Optional[Callable] = None
    description: str = ""
    timestamp: int = 0
    duration: str = "permanent"  # "permanent", "end_of_turn", "end_of_combat"


class ContinuousEffectsManager:
    """Manages all continuous effects in the game.

    This is called to recalculate effects at key game points.
    It replaces the ad-hoc temp_power_mod / instance_tags approach.

    Usage:
        cem = ContinuousEffectsManager()

        # Register a static effect from a permanent
        cem.register(ContinuousEffect(
            source_id=lord.instance_id,
            source_name="Goblin King",
            layer=Layer.POWER_TOUGHNESS,
            pt_sublayer=PTSublayer.STATIC_MOD,
            affected=lambda g, c: "Goblin" in c.template.subtypes,
            apply=lambda g, c: setattr(c, 'temp_power_mod', c.temp_power_mod + 1),
            description="Other Goblins get +1/+1",
        ))

        # Recalculate all effects
        cem.recalculate(game)

        # Clean up at end of turn
        cem.cleanup_end_of_turn()
    """

    def __init__(self):
        self._effects: List[ContinuousEffect] = []
        self._timestamp_counter: int = 0

    def register(self, effect: ContinuousEffect) -> None:
        """Register a new continuous effect."""
        self._timestamp_counter += 1
        effect.timestamp = self._timestamp_counter
        self._effects.append(effect)

    def unregister_by_source(self, source_id: int) -> None:
        """Remove all effects from a specific source (e.g., when it leaves battlefield)."""
        self._effects = [e for e in self._effects if e.source_id != source_id]

    def cleanup_end_of_turn(self) -> None:
        """Remove all end-of-turn effects."""
        self._effects = [e for e in self._effects if e.duration != "end_of_turn"]

    def cleanup_end_of_combat(self) -> None:
        """Remove all end-of-combat effects."""
        self._effects = [e for e in self._effects if e.duration != "end_of_combat"]

    def recalculate(self, game: "GameState") -> None:
        """Recalculate all continuous effects in layer order.

        CR 613.1: Effects are applied in layer order (1-7).
        Within a layer, effects are applied in timestamp order (CR 613.7).

        This method:
        1. Clears all calculated modifications on all permanents
        2. Applies effects in layer order
        3. Each effect checks if it affects a card, then applies
        """
        # Remove effects whose source is no longer on the battlefield
        self._cleanup_stale_effects(game)

        # Sort effects by (layer, pt_sublayer, timestamp)
        sorted_effects = sorted(self._effects, key=lambda e: (
            e.layer.value,
            e.pt_sublayer.value if e.pt_sublayer else 0,
            e.timestamp
        ))

        # Apply effects in order
        for effect in sorted_effects:
            if effect.affected and effect.apply:
                for player in game.players:
                    for card in player.battlefield:
                        if effect.affected(game, card):
                            effect.apply(game, card)

    def _cleanup_stale_effects(self, game: "GameState") -> None:
        """Remove effects whose source is no longer on the battlefield."""
        battlefield_ids = set()
        for player in game.players:
            for card in player.battlefield:
                battlefield_ids.add(card.instance_id)

        # Keep effects whose source is on the battlefield OR are temporary
        # (end_of_turn effects from spells that have resolved)
        self._effects = [
            e for e in self._effects
            if e.source_id in battlefield_ids or e.duration != "permanent"
        ]

    def get_effects_count(self) -> int:
        """Get the number of active effects."""
        return len(self._effects)

    def get_effects_for_source(self, source_id: int) -> List[ContinuousEffect]:
        """Get all effects from a specific source."""
        return [e for e in self._effects if e.source_id == source_id]


# ═══════════════════════════════════════════════════════════════════
# Static Effect Factories
# ═══════════════════════════════════════════════════════════════════
# These create ContinuousEffect objects for common patterns.

def create_equipment_effect(source_id: int, source_name: str,
                             equipped_tag: str,
                             power_bonus_fn: Callable,
                             toughness_bonus_fn: Optional[Callable] = None,
                             description: str = "") -> List[ContinuousEffect]:
    """Create equipment P/T bonus effects.

    Args:
        source_id: instance_id of the equipment
        source_name: name of the equipment
        equipped_tag: instance_tag marking the equipped creature
        power_bonus_fn: (game, card) -> int for power bonus
        toughness_bonus_fn: (game, card) -> int for toughness bonus (None = same as power)
        description: human-readable description
    """
    effects = []

    def is_equipped(game, card):
        return equipped_tag in card.instance_tags

    def apply_power(game, card):
        bonus = power_bonus_fn(game, card)
        card.temp_power_mod += bonus

    effects.append(ContinuousEffect(
        source_id=source_id,
        source_name=source_name,
        layer=Layer.POWER_TOUGHNESS,
        pt_sublayer=PTSublayer.STATIC_MOD,
        affected=is_equipped,
        apply=apply_power,
        description=f"{source_name}: {description} (power)",
    ))

    if toughness_bonus_fn:
        def apply_toughness(game, card):
            bonus = toughness_bonus_fn(game, card)
            card.temp_toughness_mod += bonus

        effects.append(ContinuousEffect(
            source_id=source_id,
            source_name=source_name,
            layer=Layer.POWER_TOUGHNESS,
            pt_sublayer=PTSublayer.STATIC_MOD,
            affected=is_equipped,
            apply=apply_toughness,
            description=f"{source_name}: {description} (toughness)",
        ))

    return effects


def create_lord_effect(source_id: int, source_name: str,
                        affected_fn: Callable,
                        power_bonus: int = 1,
                        toughness_bonus: int = 1,
                        keyword_grants: Optional[Set[Keyword]] = None,
                        description: str = "") -> List[ContinuousEffect]:
    """Create lord/anthem effects (e.g., 'other creatures you control get +1/+1').

    Args:
        source_id: instance_id of the lord
        source_name: name of the lord
        affected_fn: (game, card) -> bool to determine affected creatures
        power_bonus: power bonus to grant
        toughness_bonus: toughness bonus to grant
        keyword_grants: set of keywords to grant
        description: human-readable description
    """
    effects = []

    if power_bonus != 0:
        def apply_power(game, card):
            card.temp_power_mod += power_bonus

        effects.append(ContinuousEffect(
            source_id=source_id,
            source_name=source_name,
            layer=Layer.POWER_TOUGHNESS,
            pt_sublayer=PTSublayer.STATIC_MOD,
            affected=affected_fn,
            apply=apply_power,
            description=f"{source_name}: {description} (power +{power_bonus})",
        ))

    if toughness_bonus != 0:
        def apply_toughness(game, card):
            card.temp_toughness_mod += toughness_bonus

        effects.append(ContinuousEffect(
            source_id=source_id,
            source_name=source_name,
            layer=Layer.POWER_TOUGHNESS,
            pt_sublayer=PTSublayer.STATIC_MOD,
            affected=affected_fn,
            apply=apply_toughness,
            description=f"{source_name}: {description} (toughness +{toughness_bonus})",
        ))

    if keyword_grants:
        for kw in keyword_grants:
            def apply_keyword(game, card, _kw=kw):
                card.temp_keywords.add(_kw)

            effects.append(ContinuousEffect(
                source_id=source_id,
                source_name=source_name,
                layer=Layer.ABILITY,
                affected=affected_fn,
                apply=apply_keyword,
                description=f"{source_name}: grants {_kw.name}",
            ))

    return effects


def create_pump_spell_effect(source_id: int, source_name: str,
                              target_id: int,
                              power_bonus: int = 0,
                              toughness_bonus: int = 0,
                              keyword_grants: Optional[Set[Keyword]] = None,
                              duration: str = "end_of_turn") -> List[ContinuousEffect]:
    """Create a pump spell effect (e.g., Giant Growth: +3/+3 until end of turn).

    Args:
        source_id: instance_id of the spell (or 0 for abilities)
        source_name: name of the spell
        target_id: instance_id of the target creature
        power_bonus: power bonus
        toughness_bonus: toughness bonus
        keyword_grants: keywords to grant
        duration: "end_of_turn" or "end_of_combat"
    """
    effects = []

    def is_target(game, card):
        return card.instance_id == target_id

    if power_bonus != 0:
        def apply_power(game, card):
            card.temp_power_mod += power_bonus

        effects.append(ContinuousEffect(
            source_id=source_id,
            source_name=source_name,
            layer=Layer.POWER_TOUGHNESS,
            pt_sublayer=PTSublayer.SPELL_MOD,
            affected=is_target,
            apply=apply_power,
            description=f"{source_name}: +{power_bonus}/+0",
            duration=duration,
        ))

    if toughness_bonus != 0:
        def apply_toughness(game, card):
            card.temp_toughness_mod += toughness_bonus

        effects.append(ContinuousEffect(
            source_id=source_id,
            source_name=source_name,
            layer=Layer.POWER_TOUGHNESS,
            pt_sublayer=PTSublayer.SPELL_MOD,
            affected=is_target,
            apply=apply_toughness,
            description=f"{source_name}: +0/+{toughness_bonus}",
            duration=duration,
        ))

    if keyword_grants:
        for kw in keyword_grants:
            def apply_keyword(game, card, _kw=kw):
                card.temp_keywords.add(_kw)

            effects.append(ContinuousEffect(
                source_id=source_id,
                source_name=source_name,
                layer=Layer.ABILITY,
                affected=is_target,
                apply=apply_keyword,
                description=f"{source_name}: grants {_kw.name}",
                duration=duration,
            ))

    return effects
