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

# Every characteristic this layer writes on a permanent (`CardInstance.cem_*`)
# with the value that means "no effect applies". The layer writes them on
# battlefield objects only; an object off the battlefield is a new object
# (CR 400.7) and holds these values. Read by `_audit_off_battlefield`.
_EFFECT_CHARACTERISTIC_DEFAULTS = (
    ("cem_power_mod", 0), ("cem_toughness_mod", 0),
    ("cem_keywords", frozenset()), ("cem_colors_set", None),
    ("cem_land_type_set", None), ("cem_land_types_added", frozenset()),
    ("cem_types_added", frozenset()), ("cem_subtypes_added", frozenset()),
    ("cem_types_set", None), ("cem_subtypes_set", None),
    ("cem_types_removed", frozenset()), ("cem_base_pt", None),
)


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
    duration: str = "permanent"  # "permanent", "end_of_turn", "end_of_combat", "until_next_turn"
    # For "until_next_turn" (CR 611.2b): the player whose next turn ends it.
    controller: Optional[int] = None
    # A resolved effect on a chosen object (CR 611.2c): (instance_id,
    # battlefield_entry_seq). The object, not the card — CR 400.7.
    target_obj: Optional[tuple] = None


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
        # Resolved rule-modifying effects (engine/effect_model.Effect),
        # stored until their Duration is expired by a clock event.
        self._rule_effects: list = []

    # ── rule-modifying effects (CR 101.2 / 611) ─────────────────────

    def register_effect(self, effect) -> None:
        """Store a resolved rule effect (engine/effect_model.Effect)."""
        import dataclasses
        self._timestamp_counter += 1
        self._rule_effects.append(
            dataclasses.replace(effect, timestamp=self._timestamp_counter))

    def expire_rule_effects(self, event) -> None:
        """The one expiry path: drop every stored effect whose Duration
        this clock event ends."""
        self._rule_effects = [e for e in self._rule_effects
                              if not e.duration.expired_by(event)]

    def drop_rule_effects(self, predicate) -> None:
        self._rule_effects = [e for e in self._rule_effects if not predicate(e)]

    def forget_object(self, instance_id: int) -> None:
        """CR 400.7: an object a stored effect names (a permission to play
        exiled cards, `effect_model.permit_play`) left the zone the effect
        named, so the card is a new object the effect no longer names: it
        leaves every stored effect's objects, and an effect left naming
        none ends. Called where a card leaves exile -- the cast and the
        land play from exile, and the zone funnel (`ZoneManager.move_card`).
        Known gap, shared with `effect_resolver.Handle`: a card returned to
        exile by a direct zone write (no funnel) after leaving it another
        unfunnelled way is not seen leaving."""
        import dataclasses
        kept = []
        for e in self._rule_effects:
            objects = e.modification.get("objects")
            if objects and instance_id in objects:
                rest = tuple(o for o in objects if o != instance_id)
                if not rest:
                    continue
                data = tuple((k, rest if k == "objects" else v)
                             for k, v in e.modification.data)
                e = dataclasses.replace(e, modification=dataclasses.replace(
                    e.modification, data=data))
            kept.append(e)
        self._rule_effects = kept

    def permits_zone(self, zone: str) -> bool:
        """A stored permission names objects in `zone` (a resolved "you may
        play ..." -- permissions are resolved effects, never statics)."""
        from .effect_model import ModKind
        return any(e.modification.kind is ModKind.PERMIT
                   and e.modification.get("zone") == zone
                   for e in self._rule_effects)

    def spend_play(self, instance_id: int) -> None:
        """An object was played under a permission that names it: a
        permission bounded to `plays` plays ("you may play a card you own
        from outside the game this turn": one) spends one, and ends when
        none is left; then the object, now a new object (CR 400.7), leaves
        every permission (`forget_object`)."""
        import dataclasses
        kept = []
        for e in self._rule_effects:
            objects = e.modification.get("objects")
            plays = e.modification.get("plays")
            if plays is not None and objects and instance_id in objects:
                if plays <= 1:
                    continue
                data = tuple((k, plays - 1 if k == "plays" else v)
                             for k, v in e.modification.data)
                e = dataclasses.replace(e, modification=dataclasses.replace(
                    e.modification, data=data))
            kept.append(e)
        self._rule_effects = kept
        self.forget_object(instance_id)

    def rule_effects(self, game: "GameState") -> list:
        """Stored resolved effects plus the static ones permanents have
        right now (derived fresh, never stored — CR 611.3a)."""
        return list(self._rule_effects) + _derive_static_rule_effects(game)

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

    def cleanup_until_next_turn(self, player_idx: int) -> None:
        """CR 611.2b: effects that last "until your next turn" end as
        their controller's next turn begins (called from that untap step)."""
        self._effects = [e for e in self._effects
                         if not (e.duration == "until_next_turn"
                                 and e.controller == player_idx)]

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
                # Layer-4 land-type SET: None means "printed types"
                card.cem_land_type_set = None
                # Layer-4 land-type ADD: types on top of the printed ones
                card.cem_land_types_added = set()
                # Layer-4 type ADD ("in addition to its other types")
                card.cem_types_added = set()
                card.cem_subtypes_added = set()
                # Layer-4 type SET / removal, layer-7b base P/T SET
                card.cem_types_set = None
                card.cem_subtypes_set = None
                card.cem_types_removed = set()
                card.cem_base_pt = None

        # Sort effects by (layer, pt_sublayer, timestamp)
        sorted_effects = sorted(self._effects + derived, key=lambda e: (
            e.layer.value,
            e.pt_sublayer.value if e.pt_sublayer else 0,
            e.timestamp
        ))

        # Apply effects in order
        from .rules_audit import enabled as _audit_on
        _audit = _audit_on()
        for effect in sorted_effects:
            if effect.affected and effect.apply:
                for player in game.players:
                    for card in player.battlefield:
                        if effect.affected(game, card):
                            if _audit and effect.target_obj is not None:
                                # CR 400.7: an effect on a chosen object does
                                # not apply to the card's later object.
                                from .rules_audit import check as _audit_check
                                _audit_check(
                                    "400.7/effect_follows_old_object",
                                    (card.instance_id, card.battlefield_entry_seq)
                                    == tuple(effect.target_obj),
                                    f"{effect.description} applied to a new "
                                    f"object of {card.name}", game=game)
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
        if _audit:
            self._audit_off_battlefield(game)
            _audit_self_type_changes(game)

    @staticmethod
    def _audit_off_battlefield(game: "GameState") -> None:
        """CR 400.7, 611.3a, restated from raw fields: an object off the
        battlefield -- a card in a hand, graveyard or exile, a spell on the
        stack -- carries no characteristic a continuous effect gave it as a
        permanent: no power or toughness change, keyword, colour or land
        type set, land type or type added. The layer writes them on
        battlefield objects only, so one off it holds them only when they
        followed it out. Observation only."""
        from .rules_audit import check as _audit_check
        objects = [(zone, card) for player in game.players
                   for zone in ("hand", "graveyard", "exile")
                   for card in getattr(player, zone, ())]
        objects += [("stack", item.source) for item in game.stack.items
                    if getattr(item, "source", None) is not None
                    and getattr(item.source, "zone", None) == "stack"]
        for zone, card in objects:
            kept = [name for name, default in _EFFECT_CHARACTERISTIC_DEFAULTS
                    if getattr(card, name, default) != default]
            _audit_check("400.7/effect_characteristic_off_battlefield",
                         not kept,
                         f"{card.name} in {zone} keeps {', '.join(kept)} "
                         f"from its time as a permanent", game=game)

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
                forced = getattr(source.template, 'stax_forced_basic', None)
                if forced:
                    derived.extend(create_forced_land_type_effect(
                        source_id=source.instance_id,
                        source_name=source.template.name,
                        basic_type=forced,
                        timestamp=source.instance_id,
                    ))
                if getattr(source.template, 'has_all_basic_land_types', False):
                    derived.extend(create_all_basic_land_types_effect(
                        source_id=source.instance_id,
                        source_name=source.template.name,
                        controller=controller,
                        timestamp=source.instance_id,
                    ))
                derived.extend(_self_type_change_effects(game, controller,
                                                         source))
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


def create_forced_land_type_effect(source_id: int, source_name: str,
                                   basic_type: str,
                                   timestamp: int = 0) -> List[ContinuousEffect]:
    """Layer-4 land-type SET ("Nonbasic lands are Mountains", CR 613.1d,
    CR 305.7).

    Every nonbasic land on EVERY battlefield — the source's controller's
    included, and lands that enter while the source is out — loses its
    printed land types and abilities and is the named basic type: it
    produces that type's colour and nothing else, and has no other
    activated ability (a fetchland cannot be cracked).  The effect is
    re-derived each `recalculate()` from the source's presence, so it
    ends the moment the source leaves (CR 611.2a).

    `basic_type` is the parsed `CardTemplate.stax_forced_basic`
    (oracle_parser.parse_stax_forced_basic) — Blood Moon, Magus of the
    Moon, Harbinger of the Seas, … all reuse this one effect.
    """
    from .cards import Supertype

    def affects_nonbasic_lands(game, card):
        return (CardType.LAND in card.effective_card_types
                and Supertype.BASIC not in (card.template.supertypes or []))

    def apply_type(game, card):
        card.cem_land_type_set = basic_type
        # CR 613.7: a SET applied after an ADD leaves the land with the
        # one set type and nothing else — earlier additions are gone.
        # (An ADD with a later timestamp is applied after this and puts
        # its types back; the sort above orders them.)
        card.cem_land_types_added = set()

    return [ContinuousEffect(
        source_id=source_id,
        source_name=source_name,
        layer=Layer.TYPE,
        affected=affects_nonbasic_lands,
        apply=apply_type,
        description=f"{source_name}: nonbasic lands are {basic_type}s",
        timestamp=timestamp,
    )]


def create_type_adding_effect(source_id: int, source_name: str,
                              target_id: int, target_seq: Optional[int],
                              types=(), subtypes=(),
                              duration: str = "end_of_turn",
                              timestamp: int = 0) -> List[ContinuousEffect]:
    """Layer-4 type ADD on one chosen object: it "becomes a <type> /
    <subtype> in addition to its other types" while the effect lasts
    (CR 205.1b, 613.1d). The object, not the card: one that left and
    returned is a new object the effect does not name (CR 400.7, 611.2c).
    `types` are CardType members; `subtypes` subtype names."""
    types = frozenset(types)
    subtypes = frozenset(subtypes)
    target_obj = (target_id, target_seq) if target_seq is not None else None

    def is_target(game, card):
        return (card.instance_id == target_id
                and (target_seq is None
                     or card.battlefield_entry_seq == target_seq))

    def apply_types(game, card):
        card.cem_types_added |= types
        card.cem_subtypes_added |= subtypes

    names = sorted(t.value for t in types) + sorted(subtypes)
    return [ContinuousEffect(
        source_id=source_id,
        source_name=source_name,
        layer=Layer.TYPE,
        affected=is_target,
        apply=apply_types,
        description=f"{source_name}: becomes {', '.join(names)}",
        timestamp=timestamp,
        duration=duration,
        target_obj=target_obj,
    )]


def create_all_basic_land_types_effect(source_id: int, source_name: str,
                                       controller: int,
                                       timestamp: int) -> List[ContinuousEffect]:
    """Layer-4 land-type ADD (CR 613.2b): "Lands you control are every
    basic land type in addition to their other types" — Leyline of the
    Guildpact, Prismatic Omen, Dryad of the Ilysian Grove, … all reuse
    this one effect.  Affects the controller's lands only.  Ordered by
    timestamp against the SET family above: applied after a Blood Moon
    it adds the five types back to the Mountain; applied before, the
    Moon's SET removes them (CR 613.7).  Re-derived each recalculate()
    from the source's presence, so it ends when the source leaves."""
    from .constants import BASIC_LAND_TYPES

    def affects_controllers_lands(game, card):
        return (card.controller == controller
                and CardType.LAND in card.effective_card_types)

    def apply_add(game, card):
        card.cem_land_types_added = set(card.cem_land_types_added) | set(BASIC_LAND_TYPES)

    return [ContinuousEffect(
        source_id=source_id,
        source_name=source_name,
        layer=Layer.TYPE,
        affected=affects_controllers_lands,
        apply=apply_add,
        description=f"{source_name}: lands are every basic land type in addition",
        timestamp=timestamp,
    )]


def forced_land_type_in_play(game: "GameState") -> Optional[str]:
    """The basic type every nonbasic land currently IS, or None.  Read
    by paths that run before the next `recalculate()` (a fetchland
    cracked as it enters) so a land never briefly keeps an ability the
    effect has already removed."""
    for player in game.players:
        for source in player.battlefield:
            forced = getattr(source.template, 'stax_forced_basic', None)
            if forced:
                return forced
    return None


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
                              duration: str = "end_of_turn",
                              controller: Optional[int] = None,
                              target_seq: Optional[int] = None) -> List[ContinuousEffect]:
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
    target_obj = (target_id, target_seq) if target_seq is not None else None

    def is_target(game, card):
        # CR 400.7 / 611.2c: the chosen object only — a card that left and
        # returned is a new object (its battlefield_entry_seq moved on).
        return (card.instance_id == target_id
                and (target_seq is None or card.battlefield_entry_seq == target_seq))

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
            controller=controller,
            target_obj=target_obj,
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
            controller=controller,
            target_obj=target_obj,
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
                controller=controller,
                target_obj=target_obj,
            ))

    return effects


# The printed static prohibitions the rule-effect read path enforces
# (`rules_query`): casting (by spell filter), casting outside sorcery timing
# or outside the caster's own turn (CR 101.2, 307.1), and activating the
# abilities of named permanent types (CR 602.5).
_RULE_ACTIONS = frozenset({"cast", "activate", "cast_outside_sorcery_timing",
                           "cast_outside_own_turn"})
# The grammar's printed cast objects, in the read path's filter vocabulary.
_CAST_FILTERS = {"spells": "all", "noncreature spells": "noncreature",
                 "creature spells": "creature"}
# id(CardEffects) -> (that CardEffects, its per-face records).
_PRINTED_RULES: dict = {}


def _printed_prohibitions(perm) -> tuple:
    """The rule prohibitions a permanent's printed static abilities state,
    on the face it shows, read from its parsed text (CR 113.1, 604.1): one
    ``(action, data, selector_kind, condition)`` record per prohibited act.
    A spec the read path cannot run whole -- an action, filter, subject,
    duration or condition outside its vocabulary, or residue -- yields
    nothing, never part of its rule. Memoised per parsed-effects object."""
    effects = perm.template.effects
    hit = _PRINTED_RULES.get(id(effects))
    if hit is None or hit[0] is not effects:
        hit = (effects, tuple(_face_prohibitions(face)
                              for face in effects.faces))
        _PRINTED_RULES[id(effects)] = hit
    faces = hit[1]
    face = 1 if getattr(perm, "is_transformed", False) and len(faces) > 1 \
        else 0
    return faces[face] if faces else ()


def _face_prohibitions(hosts) -> tuple:
    from .effect_conditions import rule_condition_supported
    from .effect_model import DurationKind, Modification, ModKind, SelectorKind
    from .effect_spec import HostKind, Verb, iter_specs
    out = []
    for host in hosts:
        if host.kind is not HostKind.STATIC:
            continue
        for s in iter_specs(host.specs):
            mod = s.payload
            if s.verb is not Verb.CONTINUOUS or not isinstance(mod, Modification) \
                    or mod.kind is not ModKind.PROHIBIT or s.residue:
                continue
            actions = tuple(mod.get("actions") or (mod.action,))
            who = getattr(s.subject, "kind", None)
            if not set(actions) <= _RULE_ACTIONS \
                    or who not in (SelectorKind.OPPONENTS, SelectorKind.ALL_PLAYERS) \
                    or not rule_condition_supported(s.condition) \
                    or (s.duration is not None and s.duration.kind
                        is not DurationKind.WHILE_SOURCE_ON_BATTLEFIELD):
                continue
            records = []
            for action in actions:
                if action == "cast":
                    spell_filter = _CAST_FILTERS.get(mod.get("filter"))
                    if spell_filter is None:
                        break
                    data = (("filter", spell_filter),)
                elif action == "activate":
                    if not mod.get("sources"):
                        break
                    data = (("sources", tuple(mod.get("sources"))),)
                else:
                    data = ()
                records.append((action, data, who, s.condition))
            else:
                out.extend(records)
    return tuple(out)


def _derive_static_rule_effects(game: "GameState") -> list:
    """Rule-modifying effects that permanents have by being on the
    battlefield (CR 611.3a): cost reducers, draw limits and the printed
    static prohibitions (`_printed_prohibitions`) apply while their source
    is there."""
    from .effect_model import (Effect, Modification, OriginKind, Selector,
                               WHILE_SOURCE)
    from .effect_model import ModKind, cost_delta_effect, draw_limit_effect
    from .oracle_resolver import reduction_rules_of
    out = []
    for controller, player in enumerate(game.players):
        for perm in player.battlefield:
            for rule in reduction_rules_of(perm):
                # CR 601.2f: a reducer static applies to the spells it names
                # while the permanent shows the face that prints it.
                out.append(cost_delta_effect(controller, rule, WHILE_SOURCE,
                                             source_id=perm.instance_id,
                                             origin=OriginKind.STATIC))
            obs = getattr(perm.template, 'attack_observer', None)
            if obs and obs['duration'] == 'static':
                # CR 611.3a / 603.2: a permanent's printed attack observer.
                from .effect_model import observe_attacks
                out.append(observe_attacks(controller, obs, WHILE_SOURCE,
                                           source_id=perm.instance_id,
                                           origin=OriginKind.STATIC))
            rep = getattr(perm.template, 'draw_replacement', None)
            if rep:
                # CR 614.1a: a printed draw replacement covers its
                # controller's draws while the permanent is there.
                from .effect_model import draw_replacement_effect
                out.append(draw_replacement_effect(
                    controller, rep, WHILE_SOURCE,
                    source_id=perm.instance_id, origin=OriginKind.STATIC))
            lim = getattr(perm.template, 'draw_limit', None)
            if lim:
                # CR 101.2: "<players> can't draw more than N cards each
                # turn" limits draws while the permanent is there.
                out.append(draw_limit_effect(controller, lim['who'], lim['max'],
                                             WHILE_SOURCE, source_id=perm.instance_id,
                                             origin=OriginKind.STATIC))
            for action, data, who, cond in _printed_prohibitions(perm):
                # CR 101.2 / 604.1: a printed prohibition binds the players
                # it names, relative to this permanent's controller.
                out.append(Effect(
                    Selector(who, player=controller),
                    Modification(ModKind.PROHIBIT, action=action, data=data),
                    WHILE_SOURCE, OriginKind.STATIC, source_id=perm.instance_id,
                    controller=controller, timestamp=perm.instance_id,
                    condition=cond))
    return out


# ── A permanent's own type-changing static (CR 613.1d, 611.3a) ────────

@dataclass(frozen=True)
class _SelfTypeChange:
    """One printed static ability that changes its own permanent's card
    types while its condition holds: the types (and subtypes) it sets, the
    ones it adds, the ones it says the permanent isn't, the base power and
    toughness it sets and the keywords it lists."""
    condition: object
    set_types: Optional[Tuple[CardType, ...]] = None
    set_subtypes: Optional[Tuple[str, ...]] = None
    add_types: Tuple[CardType, ...] = ()
    add_subtypes: Tuple[str, ...] = ()
    remove_types: Tuple[CardType, ...] = ()
    base_pt: Optional[Tuple[int, int]] = None
    keywords: Tuple[Keyword, ...] = ()


# id(CardEffects) -> (that CardEffects, its per-face records).
_SELF_TYPE_CHANGES: dict = {}
_CARD_TYPE_BY_NAME = {t.value: t for t in CardType}
_KEYWORD_BY_NAME = {k.value: k for k in Keyword}
_SET_KEYS = frozenset({"types", "subtypes", "power", "toughness",
                       "keywords", "still"})


def _printed_self_type_changes(perm) -> tuple:
    """The static abilities a permanent prints that change its own card
    types while a condition holds, on the face it shows, read once from its
    parsed text. An ability the layer cannot apply whole -- a payload,
    condition or duration outside this vocabulary, or residue -- yields
    nothing, never part of its effect. Memoised per parsed-effects object."""
    effects = getattr(perm.template, "effects", None)
    if effects is None:
        return ()
    hit = _SELF_TYPE_CHANGES.get(id(effects))
    if hit is None or hit[0] is not effects:
        hit = (effects, tuple(_face_self_type_changes(face)
                              for face in effects.faces))
        _SELF_TYPE_CHANGES[id(effects)] = hit
    faces = hit[1]
    face = 1 if getattr(perm, "is_transformed", False) and len(faces) > 1 \
        else 0
    return faces[face] if faces else ()


def _literal_pt(amount) -> Optional[int]:
    from .effect_spec import AmountKind
    if amount is None or amount.kind is not AmountKind.LITERAL \
            or amount.quantity is not None or amount.ref is not None \
            or amount.inner is not None:
        return None
    return amount.n


def _self_change_of(mod, condition) -> Optional[_SelfTypeChange]:
    """The record of one type-changing payload, or None when any part of it
    is outside the vocabulary."""
    from .effect_model import ModKind
    d = dict(mod.data)
    types = d.get("types") or ()
    if not all(t in _CARD_TYPE_BY_NAME for t in types):
        return None
    ctypes = tuple(_CARD_TYPE_BY_NAME[t] for t in types)
    subtypes = tuple(st[:1].upper() + st[1:] for st in d.get("subtypes") or ())
    if mod.kind is ModKind.REMOVE_TYPES:
        if set(d) != {"types"} or not ctypes:
            return None
        return _SelfTypeChange(condition, remove_types=ctypes)
    if mod.kind is ModKind.ADD_TYPES:
        if set(d) - {"types", "subtypes"} or not (ctypes or subtypes):
            return None
        return _SelfTypeChange(condition, add_types=ctypes,
                               add_subtypes=subtypes)
    if set(d) - _SET_KEYS or not ctypes:
        return None
    still = d.get("still") or ()
    if not all(t in _CARD_TYPE_BY_NAME for t in still):
        return None
    kws = d.get("keywords") or ()
    if not all(param is None and kw in _KEYWORD_BY_NAME for kw, param in kws):
        return None
    pt = None
    if "power" in d or "toughness" in d:
        p, t = _literal_pt(d.get("power")), _literal_pt(d.get("toughness"))
        if p is None or t is None:
            return None
        pt = (p, t)
    if {CardType.ARTIFACT, CardType.CREATURE} <= set(ctypes):
        # "an artifact creature" keeps every prior type and subtype
        # (CR 205.1b): the change adds its types.
        return _SelfTypeChange(
            condition, add_types=ctypes, add_subtypes=subtypes, base_pt=pt,
            keywords=tuple(_KEYWORD_BY_NAME[kw] for kw, _ in kws))
    return _SelfTypeChange(
        condition,
        set_types=ctypes + tuple(_CARD_TYPE_BY_NAME[t] for t in still
                                 if _CARD_TYPE_BY_NAME[t] not in ctypes),
        set_subtypes=subtypes or None, base_pt=pt,
        keywords=tuple(_KEYWORD_BY_NAME[kw] for kw, _ in kws))


def _face_self_type_changes(hosts) -> tuple:
    import dataclasses
    from .effect_conditions import self_static_condition_supported
    from .effect_model import DurationKind, Modification, ModKind
    from .effect_spec import HostKind, RefKind, Verb, iter_specs
    type_kinds = (ModKind.SET_TYPES, ModKind.ADD_TYPES, ModKind.REMOVE_TYPES)
    out = []
    for host in hosts:
        if host.kind is not HostKind.STATIC:
            continue
        specs = list(iter_specs(host.specs))
        changes, keywords, refused = [], {}, False
        for s in specs:
            mod = s.payload
            if s.verb is not Verb.CONTINUOUS or not isinstance(mod, Modification) \
                    or mod.kind not in type_kinds + (ModKind.ADD_KEYWORDS,):
                continue
            if s.residue or getattr(s.ref, "kind", None) is not RefKind.SELF \
                    or (s.duration is not None and s.duration.kind
                        is not DurationKind.WHILE_SOURCE_ON_BATTLEFIELD) \
                    or not self_static_condition_supported(s.condition):
                refused = True
                continue
            if mod.kind is ModKind.ADD_KEYWORDS:
                kws = mod.get("keywords") or ()
                if not kws or not all(p is None and k in _KEYWORD_BY_NAME
                                      for k, p in kws):
                    refused = True
                    continue
                keywords.setdefault(id(s.condition), (s.condition, []))[1] \
                    .extend(_KEYWORD_BY_NAME[k] for k, _ in kws)
                continue
            change = _self_change_of(mod, s.condition)
            if change is None:
                refused = True
                continue
            changes.append(change)
        if refused or not changes:
            continue
        # A keyword the same ability lists under the same condition ("he's
        # a 3/4 Ninja creature and has hexproof") rides on its change.
        for i, change in enumerate(changes):
            extra = [kw for cond, kws in keywords.values()
                     if cond == change.condition for kw in kws]
            if extra:
                changes[i] = dataclasses.replace(
                    change, keywords=change.keywords + tuple(extra))
        out.extend(changes)
    return tuple(out)


def _self_type_change_effects(game: "GameState", controller: int,
                              source) -> list:
    """The layer 4, 6 and 7b effects of `source`'s own type-changing
    statics whose condition holds now (CR 611.3a): derived every
    recalculation, so the change starts and ends with its condition."""
    from .effect_conditions import self_static_condition_holds
    out = []
    for change in _printed_self_type_changes(source):
        if not self_static_condition_holds(game, controller, change.condition,
                                           source):
            continue
        sid, name = source.instance_id, source.template.name

        def is_source(g, c, _sid=sid):
            return c.instance_id == _sid

        def apply_types(g, c, ch=change):
            if ch.set_types is not None:
                c.cem_types_set = list(ch.set_types)
                if ch.set_subtypes is not None:
                    c.cem_subtypes_set = list(ch.set_subtypes)
            c.cem_types_added |= set(ch.add_types)
            c.cem_subtypes_added |= set(ch.add_subtypes)
            c.cem_types_removed |= set(ch.remove_types)

        out.append(ContinuousEffect(
            source_id=sid, source_name=name, layer=Layer.TYPE,
            affected=is_source, apply=apply_types, timestamp=sid,
            description=f"{name}'s own type change"))
        if change.keywords:
            def apply_keywords(g, c, kws=change.keywords):
                c.cem_keywords |= set(kws)
            out.append(ContinuousEffect(
                source_id=sid, source_name=name, layer=Layer.ABILITY,
                affected=is_source, apply=apply_keywords, timestamp=sid,
                description=f"{name}'s own keywords"))
        if change.base_pt is not None:
            def apply_pt(g, c, pt=change.base_pt):
                c.cem_base_pt = pt
            out.append(ContinuousEffect(
                source_id=sid, source_name=name, layer=Layer.POWER_TOUGHNESS,
                pt_sublayer=PTSublayer.SET_PT, affected=is_source,
                apply=apply_pt, timestamp=sid,
                description=f"{name}'s own base power and toughness"))
    return out


def _audit_self_type_changes(game: "GameState") -> None:
    """CR 613.1d, 611.3a, restated from each permanent's parsed text: while
    the condition of a permanent's own static that sets, adds or removes a
    card type holds, the permanent has the types it names and lacks the
    types it removes. Read from the template's typed hosts directly, never
    through the layer's own reader. Observation only."""
    from .effect_conditions import (self_static_condition_holds,
                                    self_static_condition_supported)
    from .effect_model import Modification, ModKind
    from .effect_spec import HostKind, RefKind, Verb, iter_specs
    from .rules_audit import check as _audit_check
    for controller, player in enumerate(game.players):
        for perm in player.battlefield:
            effects = getattr(perm.template, "effects", None)
            if effects is None or not effects.faces:
                continue
            face = effects.faces[1 if perm.is_transformed
                                 and len(effects.faces) > 1 else 0]
            for host in face:
                if host.kind is not HostKind.STATIC:
                    continue
                for s in iter_specs(host.specs):
                    mod = s.payload
                    if s.verb is not Verb.CONTINUOUS \
                            or not isinstance(mod, Modification) \
                            or mod.kind is not ModKind.SET_TYPES \
                            and mod.kind is not ModKind.REMOVE_TYPES \
                            or getattr(s.ref, "kind", None) is not RefKind.SELF \
                            or s.residue \
                            or not self_static_condition_supported(s.condition):
                        continue
                    keys = {k for k, _ in mod.data}
                    if keys - {"types", "subtypes", "power", "toughness",
                               "keywords", "still"}:
                        continue          # a variant the model refuses
                    if not self_static_condition_holds(game, controller,
                                                       s.condition, perm):
                        continue
                    named = {t for t in (mod.get("types") or ())}
                    have = {t.value for t in perm.effective_card_types}
                    ok = (not named & have if mod.kind is ModKind.REMOVE_TYPES
                          else named <= have)
                    _audit_check("613.1d/self_type_change", ok,
                                 f"{perm.name}: its condition holds but its "
                                 f"types are {sorted(have)} "
                                 f"({mod.kind.value} {sorted(named)})",
                                 game=game)
