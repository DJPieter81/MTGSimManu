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
from .mana import Color
from .oracle_parser import COLOR_SET_SELF, COLOR_SET_YOUR_NONLAND

# CR 105.1: the five colours. A "is all colors" static (CR 105.2b)
# sets a permanent's colour to exactly this set.
ALL_COLORS = frozenset({Color.WHITE, Color.BLUE, Color.BLACK,
                        Color.RED, Color.GREEN})


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
            apply=lambda g, c: setattr(c, 'cem_power_mod', c.cem_power_mod + 1),
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
           (cem_power_mod/cem_toughness_mod/cem_keywords — the
           dedicated accumulator fields this manager owns; see
           engine/cards.py for why these are kept separate from
           temp_power_mod/temp_toughness_mod/temp_keywords)
        2. Applies effects in layer order
        3. Each effect checks if it affects a card, then applies

        Idempotent: calling this any number of times in a row (the
        intended usage — after ETB, after spells resolve, before
        combat) produces the same result each time, since step 1
        always starts from zero rather than accumulating on top of
        the previous call's output.
        """
        # Remove effects whose source is no longer on the battlefield
        self._cleanup_stale_effects(game)

        # Statics that a permanent simply HAS while it is on the
        # battlefield are derived here rather than registered when it
        # arrives: there is no single choke point every permanent
        # passes through on its way to the battlefield (spell
        # resolution, the zone_transfer ETB fan-out, token creation,
        # reanimation, and the start-of-game leyline placement in
        # game_runner.py are all separate paths, and the last of those
        # never reaches EFFECT_REGISTRY at all). Deriving from the
        # current battlefield makes the effect present exactly while
        # its source is, whichever path put it there, and retracts it
        # the moment the source leaves — no registration bookkeeping
        # to get out of step.
        derived = self._derive_static_effects(game)

        # Step 1: clear all cem_* fields before reapplying — apply
        # functions use += (an accumulator, so multiple effects on the
        # same card compose), which requires starting from a known
        # zero baseline every call.
        for player in game.players:
            for card in player.battlefield:
                card.cem_power_mod = 0
                card.cem_toughness_mod = 0
                card.cem_keywords.clear()
                # Layer-5 colour SET: None means "printed colour"
                card.cem_colors_set = None

        # Sort effects by (layer, pt_sublayer, timestamp)
        sorted_effects = sorted(self._effects + derived, key=lambda e: (
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
            elif effect.affected is not None and effect.apply is None:
                # A continuous/static effect that SELECTS cards but carries no
                # ``apply`` callable silently modifies nothing — the static-
                # application path has exhausted its known handlers for this
                # source and would otherwise no-op invisibly. Make it observable
                # (parallel to the spell/etb/activated silent-miss sinks). Every
                # factory-built effect pairs affected+apply, so a real card never
                # reaches here; a future registration that forgets the executor
                # turns the guardrail red instead of mis-playing in silence.
                from .effect_diagnostics import record_unhandled_effect
                record_unhandled_effect(effect.source_name, "static")

    def _derive_static_effects(self, game: "GameState") -> List[ContinuousEffect]:
        """Build the continuous effects that permanents currently on the
        battlefield have simply by being there.

        Returned fresh every `recalculate()` and never stored, so a
        source leaving the battlefield retracts its effect with no
        cleanup step. Timestamps come from the source's instance_id,
        which increases with entry order — the CR 613.7 tiebreak
        within a layer.

        Covers layer 5 (colour-setting statics, CR 105.2b). Other
        always-on statics can be added here as they are parsed into
        typed CardTemplate fields.
        """
        derived: List[ContinuousEffect] = []
        for controller, player in enumerate(game.players):
            for source in player.battlefield:
                scope = getattr(source.template, 'color_setting_scope', "")
                if scope:
                    derived.extend(create_color_setting_effect(
                        source_id=source.instance_id,
                        source_name=source.template.name,
                        controller=controller,
                        scope=scope,
                        colors=ALL_COLORS,
                        timestamp=source.instance_id,
                    ))
        return derived

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

def create_color_setting_effect(source_id: int, source_name: str,
                                 controller: int,
                                 scope: str,
                                 colors: frozenset,
                                 timestamp: int = 0) -> List[ContinuousEffect]:
    """Layer-5 colour-SETTING static (CR 105.2b, CR 613.1e).

    `scope` is a parsed `CardTemplate.color_setting_scope` value:

      COLOR_SET_YOUR_NONLAND — "each nonland permanent you control is
        all colors": every nonland permanent under `controller`,
        including the source itself. Lands and the opponent's
        permanents are out of scope.
      COLOR_SET_SELF — "<this permanent> is all colors": the source
        only.

    The effect SETS colour (writes `cem_colors_set`) rather than
    adding to it, which is what CR 105.2b describes and what makes
    it a layer-5 effect rather than a layer-6 grant. Because layer 5
    is applied before layer 6 in `recalculate`, colour-CONDITIONAL
    ability grants ("has hexproof if it's blue") read the colour this
    effect produced. Nothing here touches `template.colors`: templates
    are shared database objects.

    Class size: 5 Modern-legal cards carry the clause today, but the
    mechanic — "a continuous effect sets a permanent's colour" — is
    the layer, not the card, and any future colour-setter reuses it.
    """
    def affects_your_nonland_permanents(game, card):
        return (card.controller == controller
                and CardType.LAND not in card.effective_card_types)

    def is_source(game, card):
        return card.instance_id == source_id

    if scope not in (COLOR_SET_YOUR_NONLAND, COLOR_SET_SELF):
        return []
    affected = (affects_your_nonland_permanents
                if scope == COLOR_SET_YOUR_NONLAND else is_source)

    def apply_colors(game, card):
        card.cem_colors_set = colors

    return [ContinuousEffect(
        source_id=source_id,
        source_name=source_name,
        layer=Layer.COLOR,
        affected=affected,
        apply=apply_colors,
        description=f"{source_name}: {scope} is all colors",
        timestamp=timestamp,
    )]


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
        card.cem_power_mod += bonus

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
            card.cem_toughness_mod += bonus

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
            card.cem_power_mod += power_bonus

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
            card.cem_toughness_mod += toughness_bonus

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
                card.cem_keywords.add(_kw)

            effects.append(ContinuousEffect(
                source_id=source_id,
                source_name=source_name,
                layer=Layer.ABILITY,
                affected=affected_fn,
                apply=apply_keyword,
                description=f"{source_name}: grants {kw.name}",
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
            card.cem_power_mod += power_bonus

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
            card.cem_toughness_mod += toughness_bonus

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
                card.cem_keywords.add(_kw)

            effects.append(ContinuousEffect(
                source_id=source_id,
                source_name=source_name,
                layer=Layer.ABILITY,
                affected=is_target,
                apply=apply_keyword,
                description=f"{source_name}: grants {kw.name}",
                duration=duration,
            ))

    return effects
