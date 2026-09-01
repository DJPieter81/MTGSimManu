"""
MTG Card Model
Defines card types, subtypes, abilities, and the core Card class.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Callable, Any, TYPE_CHECKING
from enum import Enum
from .mana import ManaCost, Color
from .delayed_triggers import DelayedTriggerTiming

if TYPE_CHECKING:
    from .game_state import GameState
    from .target_solver import TargetRequirement


class CardType(Enum):
    CREATURE = "creature"
    INSTANT = "instant"
    SORCERY = "sorcery"
    ENCHANTMENT = "enchantment"
    ARTIFACT = "artifact"
    PLANESWALKER = "planeswalker"
    LAND = "land"


class Supertype(Enum):
    LEGENDARY = "legendary"
    BASIC = "basic"
    SNOW = "snow"


class Keyword(Enum):
    FLYING = "flying"
    FIRST_STRIKE = "first_strike"
    DOUBLE_STRIKE = "double_strike"
    DEATHTOUCH = "deathtouch"
    LIFELINK = "lifelink"
    TRAMPLE = "trample"
    HASTE = "haste"
    VIGILANCE = "vigilance"
    REACH = "reach"
    MENACE = "menace"
    FLASH = "flash"
    HEXPROOF = "hexproof"
    INDESTRUCTIBLE = "indestructible"
    PROTECTION = "protection"
    DEFENDER = "defender"
    CASCADE = "cascade"
    CONVOKE = "convoke"
    AFFINITY = "affinity"
    PROWESS = "prowess"
    UNDYING = "undying"
    PERSIST = "persist"
    UNEARTH = "unearth"
    EVOKE = "evoke"
    SUSPEND = "suspend"
    STORM = "storm"
    ANNIHILATOR = "annihilator"
    IMPROVISE = "improvise"
    MODULAR = "modular"


class AbilityType(Enum):
    ACTIVATED = "activated"
    TRIGGERED = "triggered"
    STATIC = "static"
    MANA_ABILITY = "mana_ability"
    ETB = "etb"  # enters the battlefield
    LTB = "ltb"  # leaves the battlefield
    DIES = "dies"
    ATTACK = "attack"
    CAST = "cast"
    UPKEEP = "upkeep"
    REPLACEMENT = "replacement"


@dataclass
class Ability:
    """Represents a card ability."""
    ability_type: AbilityType
    description: str
    effect: Optional[Callable] = None  # function(game_state, source, controller) -> None
    cost: Optional[ManaCost] = None
    tap_cost: bool = False  # requires tapping
    condition: Optional[Callable] = None  # function(game_state, source) -> bool
    targets_required: int = 0
    target_filter: Optional[Callable] = None  # function(game_state, potential_target) -> bool
    keyword: Optional[Keyword] = None
    trigger_condition: Optional[str] = None  # for triggered abilities
    priority: int = 0  # for ordering simultaneous triggers

    def can_activate(self, game_state: "GameState", source: "CardInstance", controller_idx: int) -> bool:
        if self.condition and not self.condition(game_state, source):
            return False
        if self.tap_cost and source.tapped:
            return False
        if self.cost:
            pool = game_state.players[controller_idx].mana_pool
            if not pool.can_pay(self.cost):
                return False
        return True


# Canonical counter-kind strings (CR 122). The two P/T kinds name the
# instance fields that carry them (`plus_counters` / `minus_counters`) and
# so move `power`/`toughness`; every other kind is a named resource counter
# in `other_counters`. Parsed once, in oracle_parser.
COUNTER_KIND_PLUS = "+1/+1"
COUNTER_KIND_MINUS = "-1/-1"
COUNTER_KIND_LOYALTY = "loyalty"


class ActivationEffectKind(Enum):
    """What an activated ability actually does (CR 602).

    Deliberately a CLOSED set with an explicit escape hatch: `UNCLASSIFIED`
    means the line parsed as an ability but its effect is not one this tranche
    executes. Visible-but-refused beats silently-dropped — a dropped line would
    have to be re-parsed by every later tranche.

    `ANIMATE_SELF_UEOT` is present so the enumerator can explicitly SKIP lines
    that `parse_land_animation` / `animate_land` already own, rather than
    double-executing them.
    """
    DAMAGE_ANY_TARGET = "damage_any_target"
    DRAW_N = "draw_n"
    PUMP_SELF_UEOT = "pump_self_ueot"
    ANIMATE_SELF_UEOT = "animate_self_ueot"
    # "[Cost]: Target creature gains haste until end of turn" — the
    # combat-enabler activation carried by utility lands and creatures
    # (Hanweir Battlements-shape). Grants Keyword.HASTE via temp_keywords,
    # the same until-EOT channel Dash/Goryo's reanimation use, so
    # `has_summoning_sickness` clears for the target this turn and
    # cleanup_damage() expires the grant at end of turn.
    GRANT_HASTE_TARGET = "grant_haste_target"
    # Activated tutors — "[Cost]: Search your library for a ... card, put
    # it onto the battlefield / into your hand, then shuffle." The parsed
    # search constraint rides on `ActivatedAbility.tutor_data`; the resolver
    # routes through the shared library-search machinery (search triggers,
    # zone funnel, shuffle). The battlefield kind is restricted to CREATURE
    # searches — a land put onto the battlefield carries land-ETB statics
    # this tranche does not wire, so that shape stays UNCLASSIFIED.
    TUTOR_CREATURE_TO_BATTLEFIELD = "tutor_creature_to_battlefield"
    TUTOR_TO_HAND = "tutor_to_hand"
    # "[Cost]: Untap target <permanent>" / "[Cost]: Untap this <permanent>"
    # — the mana-untapper class (Arbor Elf, Voyaging Satyr, Blossom Dryad)
    # and the self-untappers (Devoted Druid, Barrenton Medic). The TARGETED
    # shape carries its restriction in `target_requirements` like any other
    # targeted ability; the SELF shape declares no target (CR 115.1) and
    # untaps its own source, which is why `targets_required == 0` is the
    # discriminator the resolver and the no-free-repeatable rule both read.
    UNTAP_TARGET_PERMANENT = "untap_target_permanent"
    # "[Cost]: Exile target player's graveyard / all graveyards / each
    # opponent's graveyard / N target cards from a graveyard" — the
    # graveyard-hate class (Tormod's Crypt, Nihil Spellbomb, Relic of
    # Progenitus, Soul-Guide Lantern, Withered Wretch, Unlicensed Hearse,
    # …; 33 Modern cards). The parsed shape rides on
    # `ActivatedAbility.graveyard_exile_data`; `scope` — not the effect
    # kind — is what tells the resolver whose graveyards it clears, and
    # only the 'cards' scope declares card targets. Every exile is a zone
    # change and routes through the zone funnel.
    EXILE_FROM_GRAVEYARD = "exile_from_graveyard"
    # "[Cost]: Put N <kind> counter(s) on this/target <permanent>" — the
    # put-counter class (157 Modern activated abilities across 156 cards).
    # Counter COSTS became payable in tranche 4; this is the EFFECT half,
    # which until now landed in UNCLASSIFIED and so was refused by rule 9b
    # before any cost was charged. The parsed shape rides on
    # `ActivatedAbility.put_counter_data`; SCOPE is split across two kinds
    # rather than carried as a flag because the SELF form is not targeted
    # at all (CR 115.1) and `targets_required == 0` is the discriminator
    # the resolver reads. Counters go on the permanent through the
    # instance's existing counter fields, so a +1/+1 counter moves power
    # and toughness and, unlike PUMP_SELF_UEOT, does not expire.
    PUT_COUNTER_SELF = "put_counter_self"
    PUT_COUNTER_TARGET = "put_counter_target"
    UNCLASSIFIED = "unclassified"


@dataclass
class ActivationCost:
    """The cost half of "[Cost]: [Effect]".

    `unpayable` carries cost items this tranche cannot charge (sacrifice, pay
    life, discard, remove/put counter, exile, ...). A NON-EMPTY tuple means the
    ability is fully parsed and visible but must be refused. That is what makes
    each later tranche a *payer addition* rather than a re-parse of the pool.
    """
    mana: "ManaCost" = field(default_factory=lambda: ManaCost())
    tap_self: bool = False
    untap_self: bool = False
    # Tranche 2 payable costs. `life` is the amount of life paid (CR 118.4:
    # payable only while the life total covers it). `sacrifice_self` means the
    # source itself is sacrificed as part of the cost (CR 602.2b) — inherently
    # self-limiting, so it satisfies the no-free-repeatable rule the way a tap
    # cost does.
    life: int = 0
    sacrifice_self: bool = False
    # Sibling of `sacrifice_self` with a different destination zone: the
    # source is EXILED as part of the cost (CR 602.2b). Equally
    # self-limiting -- the source leaves the battlefield, so a repeatable
    # ability charging it terminates -- but the permanent does not land in
    # the graveyard, so graveyard recursion cannot bring it back.
    # 24 Modern activated abilities charge it.
    exile_self: bool = False
    # Tranche 3 payable costs. `sacrifice_type` is the required permanent
    # type of a single-victim sacrifice cost ("creature", "artifact",
    # "enchantment", "land", or the wildcard "permanent"); None means no
    # such cost item. `sacrifice_another` records the word "another" —
    # the source is then excluded from the legal victims (plain
    # "sacrifice a creature" permits the source itself). `discard_cards`
    # is the number of cards the untyped, non-random discard cost pays.
    # Both deplete a real resource (board, hand), so they satisfy the
    # no-free-repeatable rule the way sacrifice_self/life do.
    sacrifice_type: Optional[str] = None
    sacrifice_another: bool = False
    discard_cards: int = 0
    # Tranche 4 payable costs — counters put on / removed from the SOURCE.
    # `*_kind` is the canonical counter kind ('+1/+1', '-1/-1', or a named
    # kind like 'charge'/'oil'/'page' that lives in
    # `CardInstance.other_counters`); `*_amount` is how many. A REMOVE cost
    # is payable only while that many counters are there (CR 118.x — you
    # cannot pay what you do not have); a PUT cost is always payable.
    # Depletion (the no-free-repeatable rule) differs between them: REMOVE
    # always drains a finite supply, while PUT drains only when the counter
    # itself is a resource — a -1/-1 counter on a creature shrinks toughness
    # toward the zero-toughness SBA. See `ActivationManager.can_activate`.
    put_counter_kind: Optional[str] = None
    put_counter_amount: int = 0
    remove_counter_kind: Optional[str] = None
    remove_counter_amount: int = 0
    # Tranche 5 payable cost: the number of cards exiled from the
    # ACTIVATOR'S OWN graveyard as part of the cost (CR 602.2b). Untyped
    # and fixed-count only ("Exile three cards from your graveyard"); a
    # type-restricted, "all"/"any number", {X}-counted, or other-zone
    # exile stays in `unpayable` rather than being approximated by a
    # count. Payable only while the graveyard actually holds that many
    # cards (CR 601.2h), and depleting for the no-free-repeatable rule:
    # the graveyard is finite and each payment strictly shrinks it.
    # 11 Modern activated abilities charge it.
    exile_from_graveyard_cards: int = 0
    # Number of {X} pips in the mana cost. X is chosen at activation time
    # (CR 601.2b) and is chargeable exactly when the classified effect
    # BINDS X (a tutor's "mana value X or less"); an {X} pip on any other
    # effect kind is refused by `can_activate` because the engine cannot
    # know what X buys. Hybrid pips are folded into `mana` as one generic
    # each (caster picks the colour — the `_parse_mana_symbols_to_cost`
    # convention), so they are NOT tracked here.
    x_count: int = 0
    unpayable: Tuple[str, ...] = ()


@dataclass
class ActivatedAbility:
    """One parsed "[Cost]: [Effect]" line on a permanent (CR 602).

    `index` is a stable ordinal within the template. It is both the ledger key
    for per-turn activation limits and the value that crosses the AI/engine
    seam, so it must not be derived from list position at call time.

    `restrictions` holds "Activate only ..." clauses the schema cannot express
    as booleans. Non-empty means refuse — capturing them verbatim is what stops
    an unrepresentable restriction from being silently ignored.
    """
    index: int
    cost: ActivationCost
    effect_text: str
    effect_kind: ActivationEffectKind
    amount: int = 0
    power_mod: int = 0
    toughness_mod: int = 0
    targets_required: int = 0
    target_requirements: List = field(default_factory=list)
    sorcery_speed_only: bool = False
    once_each_turn: bool = False
    restrictions: Tuple[str, ...] = ()
    from_battlefield: bool = True
    is_mana_ability: bool = False
    # TUTOR_* kinds only: the structured search constraint from
    # `oracle_parser.parse_activation_tutor` — dest ('battlefield'/'hand'),
    # types / not_types / supertypes / subtypes / colors, max_mv (fixed
    # bound), mv_bound_is_x ("mana value X or less"), tapped (battlefield
    # entry rider). None for every other effect kind.
    tutor_data: Optional[Dict] = None
    # EXILE_FROM_GRAVEYARD kind only: the structured shape from
    # `oracle_parser.parse_activation_graveyard_exile` -- scope
    # ('cards'/'target_player'/'all'/'each_opponent'), count, up_to,
    # types, owner, single_graveyard. None for every other effect kind.
    graveyard_exile_data: Optional[Dict] = None
    # PUT_COUNTER_* kinds only: the structured shape from
    # `oracle_parser.parse_activation_put_counter` -- kind (canonical
    # counter kind), amount, self (bool), types. None for every other
    # effect kind.
    put_counter_data: Optional[Dict] = None
    # Delayed timing (CR 603.7): non-None when the effect happens at a
    # LATER step ("Draw a card at the beginning of the next turn's
    # upkeep"). `effect_kind` is the INNER effect's kind — the delay is
    # orthogonal, so every executable kind gets its delayed form for free
    # instead of each spawning a delayed twin. Parsed once at DB load by
    # `oracle_parser.parse_activation_delay`; `resolve_activated_ability`
    # dispatches off it into `GameState.register_delayed_trigger`.
    delayed_timing: Optional["DelayedTriggerTiming"] = None


@dataclass(frozen=True)
class TapForManaTrigger:
    """A "whenever you tap a <filter> for mana, add …" trigger (CR 605.1b).

    12 Modern cards carry this shape — Leyline of Abundance, Nissa Who Shakes
    the World, Crypt Ghast, Nirkana Revenant, Badgermole Cub, Groundchuck &
    Dirtbag (fixed-symbol riders) and Zendikar Resurgent, Mirari's Wake,
    Nikya of the Old Ways, Vorinclex, Kinnan, Roxanne (source-mirroring
    riders). Parsed once by `oracle_parser.parse_tap_for_mana_trigger`; the
    engine dispatches off these fields, never off a card name.

    `watch` is the normalised noun phrase the trigger looks for — a card type
    ('creature', 'land', 'nonland permanent', 'artifact token') or a land
    subtype ('forest', 'swamp'). `PermanentEffects.tap_trigger_matches`
    turns it into a predicate over a permanent.

    `units` are the extra mana units granted, in the same shape as
    `CardTemplate.mana_units`, so a source's unit list can simply be extended.
    `mirror_source` instead means "one mana of any type that permanent
    produced": the granted unit is a copy of what the tapped source itself
    offers, so it is resolved against the source rather than stored here.
    """
    watch: str
    units: Tuple[Tuple[str, ...], ...] = ()
    mirror_source: bool = False


class LoyaltyEffectKind(Enum):
    """What a planeswalker loyalty ability actually does (CR 606).

    Same shape and same discipline as `ActivationEffectKind` above: a
    CLOSED set with an explicit `UNCLASSIFIED` escape hatch.  A loyalty
    ability whose printed effect this engine cannot execute is refused
    BEFORE its loyalty is paid (`PlaneswalkerManager.activate_planeswalker`,
    mirroring `ActivationManager.can_activate` rule 9b) — visible-but-
    refused beats silently-dropped.

    The kinds below the RETURN_TO_HAND line are the pre-existing dispatch
    branches, moved from runtime substring tests to load-time
    classification without changing which abilities they take or what they
    do.  Their names describe the shape they execute, not a card.
    """
    # "Return [up to N] target <filter> to its owner's hand" (battlefield)
    # and "Return target <filter> card from your graveyard to your hand".
    # Resolved through `engine.target_solver` + the zone funnel — the same
    # seam ETB / spell resolution / triggers use.
    RETURN_TO_HAND = "return_to_hand"
    # "<walker> deals N damage to …" — targeted burn, kill-or-face.
    DAMAGE = "damage"
    # "You gain N life, draw N cards, …" — the Ugin-shape value ultimate.
    GAIN_LIFE_AND_DRAW = "gain_life_and_draw"
    # "Draw a card. Untap up to two lands."
    DRAW_AND_UNTAP_LANDS = "draw_and_untap_lands"
    # "Put target <permanent> into its owner's library Nth from the top."
    TUCK_TARGET_INTO_LIBRARY = "tuck_target_into_library"
    # An emblem line whose executed part exiles an opposing permanent.
    EMBLEM_EXILE_PERMANENT = "emblem_exile_permanent"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class LoyaltyAbility:
    """One printed `[±N]: effect` line, classified once at DB load.

    `text` is the PRINTED oracle text of the ability — it is what the log
    shows and what the AI's description-driven chooser reads, so it must
    never be paraphrased.  `effect_kind` is what the resolver dispatches
    on; `target` (a `target_solver.TargetRequirement`) and `draws` carry
    the parsed payload for the kinds that need one.
    """
    slot: str          # "plus" | "zero" | "minus" | "ult"
    cost: int          # signed loyalty change (+1, 0, -3, …)
    text: str
    effect_kind: "LoyaltyEffectKind" = LoyaltyEffectKind.UNCLASSIFIED
    target: Optional["TargetRequirement"] = None
    # Printed "Draw a card" / "Draw N cards" rider on the same ability.
    draws: int = 0


@dataclass(frozen=True)
class FetchLandProfile:
    """The printed shape of a fetchland's self-sacrifice search ability.

    The mechanic: a land whose activated ability sacrifices ITSELF to put a
    land card from its controller's library onto the battlefield, where the
    search is constrained by BASIC LAND TYPES ("a Mountain or Plains card",
    "a basic Forest, Plains, or Island card", "a basic land card").  ~43
    Modern lands print it — the Onslaught/Zendikar cycles, the Panorama and
    Landscape cycles, Evolving Wilds, Terramorphic Expanse, Fabled Passage,
    Prismatic Vista, Escape Tunnel, Hobbit Hole and friends.

    Everything the engine needs to execute one is printed on the card, so
    everything here is parsed once by
    `oracle_parser.parse_fetchland_profile` at DB load and read off the
    typed field afterwards.  In particular the life payment is part of the
    printed activation cost ("{T}, Pay 1 life, Sacrifice this land: …") and
    the fetched land's entry state is part of the printed effect ("put it
    onto the battlefield" vs "… onto the battlefield tapped", plus Fabled
    Passage's "Then if you control four or more lands, untap that land").

    `colors` are the mana colours of the basic land types the search may
    name, in canonical WUBRG order — the colour-fixing view the mana
    planner scores fetches by.  A search that names NO land type (Urza's
    Cave: "search your library for a land card") is refused rather than
    approximated as five colours: it can find any land at all, which this
    colour-set model cannot express.
    """
    colors: Tuple[str, ...]
    # Life paid as part of the activation cost ("Pay 1 life"). 0 for the
    # Panorama/Landscape/Evolving Wilds families, which pay none.
    life_cost: int = 0
    # How many land cards the one activation finds ("up to two basic land
    # cards" — Blighted Woodland).
    count: int = 1
    # "put it onto the battlefield TAPPED" — a property of the FETCH, not
    # of the land it finds (which carries its own enters-tapped rules).
    target_enters_tapped: bool = False
    # Fabled Passage's "Then if you control N or more lands, untap that
    # land." 0 means the fetch prints no such rider.
    untap_target_min_lands: int = 0


@dataclass
class CardTemplate:
    """Template for a card (shared data, not instance-specific)."""
    name: str
    card_types: List[CardType]
    mana_cost: ManaCost
    supertypes: List[Supertype] = field(default_factory=list)
    subtypes: List[str] = field(default_factory=list)
    power: Optional[int] = None
    toughness: Optional[int] = None
    loyalty: Optional[int] = None
    keywords: Set[Keyword] = field(default_factory=set)
    abilities: List[Ability] = field(default_factory=list)
    color_identity: Set[Color] = field(default_factory=set)
    # The permanent's own printed color (MTGJSON `colors`) — NOT the
    # same as color_identity (MTGJSON `colorIdentity`, which can
    # include colors referenced by the card's text/cost beyond its own
    # printed color; used for format-legality checks, not "is this
    # permanent white/blue/etc." characteristic checks). Needed for
    # any color-conditional static ability ("has vigilance if it's
    # white") — using color_identity for that check is a common latent
    # bug in exactly this shape of effect.
    colors: Set[Color] = field(default_factory=set)
    # For lands
    produces_mana: List[str] = field(default_factory=list)  # e.g., ["W", "R"]
    # Mana UNITS from the land's plain {T} ability: one inner list of
    # color options per unit of mana produced (E1 — multi-mana lands).
    # 'Add {G}{U}' → [["G"], ["U"]] (two fixed units);
    # 'Add {G} or {U}' → [["G", "U"]] (one unit, color choice).
    # Empty ⇒ legacy single unit whose options are `produces_mana`.
    mana_units: List[List[str]] = field(default_factory=list)
    # ONE-SHOT mana from "Sacrifice this <thing>: Add <mana>" (CR 605) —
    # Eldrazi Spawn/Scion, Treasure, Lotus-style artifacts (207 Modern cards).
    # Same unit shape as `mana_units`, but spending it CONSUMES the permanent,
    # so it is deliberately a separate field: it must never be counted as
    # repeatable per-turn mana. Populated at load time (and for tokens at
    # creation time) by oracle_parser.parse_sacrifice_mana_units.
    sacrifice_mana_units: List[List[str]] = field(default_factory=list)
    # Aura attachment (CR 303.4). `aura_enchant_restriction` is the quality
    # from the printed "Enchant <quality>" ability ('land', 'forest',
    # 'creature', ...) and gates which objects are legal hosts; 786 Modern
    # cards are Auras, so this is the shared primitive rather than a
    # mana-specific field. `aura_mana_units` is its first consumer: the units
    # a mana Aura GRANTS to the land it enchants.
    # Parsed "[Cost]: [Effect]" lines (CR 602). A NEW field: deliberately NOT
    # merged into `abilities`, because emitting AbilityType.ACTIVATED there
    # would light up ABILITY_TYPE_ACTIVATED in ai/evaluator for thousands of
    # templates, feeding estimate_permanent_value — the removal-target sort key
    # in ai/response.py. See tests/test_activation_schema_is_behaviour_neutral.
    activated_abilities: List["ActivatedAbility"] = field(default_factory=list)
    # Parse-once typed field derived from `activated_abilities`: True when
    # any parsed line is a GRANT_HASTE_TARGET activation. Consumers (e.g.
    # Primeval Titan's fetch priority) read this instead of re-scanning
    # oracle text at runtime (oracle-runtime-parse ratchet).
    grants_haste_activation: bool = False
    aura_enchant_restriction: Optional[str] = None
    aura_mana_units: List[List[str]] = field(default_factory=list)
    # True when the Aura's granted mana is "of the chosen color" — a colour
    # picked as the Aura enters (Utopia Sprawl, Shimmerwilds Growth). The
    # COLOUR is a decision, so it is routed through the `choose_mana_color`
    # callback and recorded per-instance on `CardInstance.chosen_color`;
    # `aura_mana_units` holds the full option set that choice ranges over.
    aura_mana_color_chosen: bool = False
    # "Whenever you tap a <filter> for mana, add …" (12 Modern cards).
    # See TapForManaTrigger. Read by `PermanentEffects.tap_trigger_bonus_units`
    # from inside `ManaPayment.land_mana_units` — the single per-source unit
    # resolver — so mana CAPACITY and actual PRODUCTION cannot disagree.
    tap_for_mana_trigger: Optional["TapForManaTrigger"] = None
    # The printed self-sacrifice land search (fetchland mechanic).  See
    # FetchLandProfile; populated by oracle_parser.parse_fetchland_profile.
    # `None` means the card is not a fetchland — that predicate replaces the
    # old FETCH_LAND_COLORS card-name table.
    fetchland: Optional["FetchLandProfile"] = None
    # 'When this land enters, return a land you control to its owner's
    # hand' — structural ETB clause of the karoo family (E1b), a
    # sibling of `enters_tapped`.
    etb_return_land: bool = False
    enters_tapped: bool = False
    # Life payment to enter untapped (shock lands = 2, derived from oracle text)
    untap_life_cost: int = 0
    # Conditional untap: max other lands to enter untapped (fast lands = 2)
    # -1 means no conditional check (always tapped or always untapped)
    untap_max_other_lands: int = -1
    # Self-damage when tapping for colored mana (pain lands = 1)
    tap_damage: int = 0
    # For split/modal cards
    is_modal: bool = False
    modes: List[Dict] = field(default_factory=list)
    # How many modes a "Choose N —" modal spell picks (CR 700.2d).
    modal_choose_count: int = 1
    # Oracle text (raw rules text from card database)
    oracle_text: str = ""
    # Tags for AI strategy
    tags: Set[str] = field(default_factory=set)  # e.g., {"removal", "threat", "ramp"}
    # Evoke cost
    evoke_cost: Optional[ManaCost] = None
    # Dash cost (alternative cast: gains haste, returns to hand at end of turn)
    dash_cost: Optional[ManaCost] = None  # Full ManaCost preserving colour pips
    # Warp cost (alternative cast from hand for less mana; creature exiles at end of turn)
    warp_cost: Optional[ManaCost] = None
    # Escape cost (alternative cast from graveyard)
    escape_cost: Optional[ManaCost] = None  # Full ManaCost preserving colour pips
    escape_exile_count: int = 0  # Number of other cards to exile from graveyard
    # Equipment
    equip_cost: Optional[int] = None  # CMC to equip, e.g. 1 for Cranial Plating
    # Delve: exile cards from graveyard to reduce generic mana cost
    has_delve: bool = False
    # Extra land drops per turn (Azusa, Dryad, etc.)
    extra_land_drops: int = 0
    # Conditional mana bonus: extra mana produced when a condition is met
    # Format: {"condition": "tron", "bonus": 2} means +2C when Tron assembled
    # Parsed from oracle text patterns like "If you control an Urza's..."
    conditional_mana: Optional[Dict] = None
    # Oracle-derived properties (populated by oracle_parser at load time)
    # These replace hardcoded data tables in game_state.py
    ritual_mana: Optional[tuple] = None       # (color, amount) e.g. ("R", 3)
    cycling_cost_data: Optional[Dict] = None  # {mana, life, colors}
    # None = plain cycling / no cycling; dict = landcycling/typecycling
    # tutor predicate (see engine/oracle_parser.py:parse_cycling_variant)
    cycling_variant_data: Optional[Dict] = None
    energy_production: int = 0                # number of {E} symbols
    is_cascade: bool = False                  # has cascade keyword
    # True for cards that grant flashback to instant/sorcery cards in the
    # graveyard (Past in Flames, Snapcaster Mage pattern).  Populated at
    # DB load by grants_flashback_to_gy_spells() in oracle_parser.py.
    grants_flashback_to_gy_spells: bool = False
    x_cost_data: Optional[Dict] = None        # {multiplier, min_x}
    # X-bound creature tutor on a SPELL's own resolution (Green Sun's
    # Zenith shape). The structured search constraint plus its executable
    # riders ({'dest', 'types', 'colors', 'mv_bound_is_x', 'also_graveyard',
    # 'self_shuffle_into_library', 'enters_with_x_counters', 'haste_at_x',
    # 'team_pump_haste_at_x', ...}), parsed once at DB load by
    # parse_x_creature_tutor(). None = not this shape, or a shape whose
    # riders the engine refuses rather than half-executes — the AI's
    # valuation gate keys off this field, so it must never be set for a
    # card `oracle_resolver._resolve_x_creature_tutor` cannot deliver.
    x_creature_tutor_data: Optional[Dict] = None
    is_cost_reducer: bool = False             # reduces spell costs (from tags)
    domain_reduction: int = 0                 # cost reduction per basic land type
    back_face_oracle: str = ""                # oracle text for back face (transform cards)
    back_face_loyalty: int = 0                # starting loyalty for back face planeswalker
    # Full back-face characteristics for ANY multi-face card (not just
    # planeswalker-backed). Populated at DB load alongside
    # back_face_oracle/back_face_loyalty above. Empty/None = single-faced
    # card, or back-face data wasn't captured (should not happen for any
    # card with >=2 MTGJSON face entries after the DFC-capture fix).
    back_face_types: List[CardType] = field(default_factory=list)
    back_face_subtypes: List[str] = field(default_factory=list)
    back_face_power: Optional[int] = None
    back_face_toughness: Optional[int] = None
    back_face_keywords: Set[Keyword] = field(default_factory=set)
    power_scales_with: str = ""               # "domain", "tarmogoyf", "delirium", "graveyard",
                                                # "permanent_count:<word>",
                                                # "graveyard_count:<formula>:<type>:<scope>"
    # Splice onto Arcane: oracle-derived from "Splice onto Arcane {cost}"
    splice_cost: Optional["ManaCost"] = None   # full mana cost to splice (None = no splice)
    is_arcane: bool = False                    # True if subtype includes Arcane
    # Counter/tax framework — structured replacement for the old
    # "'counter' in ability.description" substring dispatch. Populated
    # at load time from the same OracleEffect the ability description
    # is synthesized from, so it can never drift from what actually
    # got matched.
    is_counterspell: bool = False              # has a "counter target ..." effect
    counter_target_kind: str = ""              # "spell"/"creature_spell"/"noncreature_spell"/"instant_or_sorcery_spell"
    counter_tax_amount: int = 0                # {N} from "unless its controller pays {N}"; 0 = hard counter
    is_land_sacrifice_tutor: bool = False      # Scapeshift shape: sac any number of lands + search (typed, parse-once)
    # Combat/targeting legality (CR 702.16d/e) — colors this permanent
    # has protection from. Empty = no protection. Populated at load
    # time from oracle text (engine.oracle_parser.parse_protection_from),
    # consumed by CombatManager.declare_blockers (blocking legality)
    # and target_solver.py (illegal-target filtering).
    protection_from_colors: frozenset = field(default_factory=frozenset)
    # Ward (CR 702.21a) — mirror image of the counter/tax framework
    # above: there, the SOURCE card taxes a TARGETED spell's
    # controller; here, the permanent (this template) taxes the
    # controller of whatever spell/ability TARGETS it. Populated at
    # load time from engine.oracle_parser.parse_ward_cost. 0 = no
    # (mana-shaped) ward to enforce — either genuinely no ward, or a
    # ward of an excluded cost shape (life/discard/sacrifice/...; see
    # parse_ward_cost's docstring). Consumed by
    # engine.optional_costs.offer_ward_tax via the resolve_stack hook.
    ward_cost: int = 0                         # {N} from "Ward {N}"; 0 = no mana-shaped ward
    # Spell targeting capability (CR 601.2c) — derived at load time by
    # engine.oracle_parser.parse_can_target_player/planeswalker.
    # Replace runtime `'any target' in oracle_text` inline checks in ai/.
    can_target_player: bool = False            # "any target"/"target player"/"target opponent"
    can_target_planeswalker: bool = False      # "any target" or planeswalker oracle patterns
    # On-attack triggered ability (CR 603.2) — populated at load time by
    # oracle_parser.parse_has_attack_trigger(oracle, name). True for cards
    # whose oracle text contains "Whenever this creature attacks" or
    # "Whenever [Card Name] attacks". Replaces runtime 'whenever...attacks'
    # in-oracle substring checks in ai/ and engine/.
    has_attack_trigger: bool = False
    # On-combat-damage-to-a-player triggered ability (CR 603.2) — populated at
    # load time by oracle_parser.parse_has_combat_damage_trigger(oracle, name).
    # A DISTINCT shape from has_attack_trigger: fires on connecting, not on
    # declaring. Covers the "connects → draw / Treasure / steal" value engines
    # (331 Modern creatures), which were previously valued as vanilla bodies.
    has_combat_damage_trigger: bool = False
    # Lifegain-token trigger (CR 603.2) — True when oracle creates a token
    # whenever the controller gains life ("whenever you gain life … create …
    # token").  Replaces the runtime oracle substring check in
    # permanent_effects.gain_life().  Populated at load time by
    # oracle_parser.parse_has_lifegain_token_trigger.
    has_lifegain_token_trigger: bool = False
    lifegain_token_type: str = "creature"  # subtype passed to create_token()
    # Stack-spell targeting (e.g. ETBs that target a spell on the stack) —
    # populated at load time by oracle_parser.parse_targets_creature_spell /
    # parse_targets_planeswalker_spell. Replaces runtime oracle substring
    # checks in ai/board_eval.py (_eval_evoke stack-check gate).
    targets_creature_spell: bool = False      # oracle contains "target creature spell"
    targets_planeswalker_spell: bool = False  # oracle contains "target planeswalker spell"
                                              # or "or planeswalker spell" (chained form)
    # Landfall trigger (CR 603.2) — populated at load time by
    # oracle_parser.parse_has_landfall(). True for cards matching
    # "landfall" / "land enters" / "whenever a land" oracle patterns.
    # Replaces runtime 'landfall' in oracle checks in engine/land_manager.py.
    has_landfall: bool = False
    # Library-search opponent trigger — "whenever an opponent searches …
    # library" (Wan Shi Tong pattern). Populated by
    # oracle_parser.parse_has_library_search_opponent_trigger().
    has_library_search_opponent_trigger: bool = False
    # Whether the library-search opponent trigger also draws a card.
    # Populated by oracle_parser.parse_library_search_trigger_draws_card().
    library_search_trigger_draws_card: bool = False
    # Life gained on the FIRST landfall trigger (0 = no first-landfall life
    # gain). Omnath, Locus of Creation pattern. Populated at load time.
    landfall_first_life_gain: int = 0
    # Damage dealt on the THIRD landfall trigger (0 = none). Omnath pattern.
    landfall_third_damage: int = 0
    # Mana colors added on the SECOND landfall trigger (empty = none).
    # Omnath pattern: ('R', 'G') means add {R} and {G}. Populated at load time.
    landfall_second_mana_colors: tuple = ()
    # On-hit trigger — True when oracle contains "combat damage to a player"
    # (Ragavan, Thieving Skydiver pattern).  Populated at load time by
    # oracle_parser.parse_has_combat_damage_player_trigger.
    has_combat_damage_player_trigger: bool = False
    # Destroy-target capability flags — True when the card's oracle text can
    # destroy the named permanent type.  Populated by
    # oracle_parser.parse_can_destroy_{artifact,enchantment,nonland_permanent}.
    can_destroy_artifact: bool = False
    can_destroy_enchantment: bool = False
    can_destroy_nonland_permanent: bool = False
    # Tutor / Wish flag — True when oracle searches the library or fetches
    # from outside the game.  Populated by oracle_parser.parse_is_tutor.
    is_tutor: bool = False
    # Noncreature-spell-cast trigger — True when oracle contains "whenever …
    # cast … noncreature spell" (Young Pyromancer, Monastery Swiftspear, etc.).
    # Populated by oracle_parser.parse_has_noncreature_spell_cast_trigger.
    has_noncreature_spell_cast_trigger: bool = False
    # Artifact synergy — True when oracle has 'for each artifact', 'metalcraft',
    # or 'affinity for artifacts'.  Populated by
    # oracle_parser.parse_has_artifact_synergy.
    has_artifact_synergy: bool = False
    # Draw effect — True when oracle draws or impulse-draws cards (draw a card,
    # look at the top, exile top N and play/cast).
    # Populated by oracle_parser.parse_has_draw_effect.
    has_draw_effect: bool = False
    # Deals damage to a target/each/any — direct-damage finisher shape
    # (Grapeshot). Populated by oracle_parser.parse_deals_targeted_damage.
    deals_targeted_damage: bool = False
    # Creates a storm-scaled token count ("create … tokens for each …").
    # Populated by oracle_parser.parse_has_scaling_token_finisher.
    has_scaling_token_finisher: bool = False
    # Exile permanent — True when oracle has 'exile target <permanent-type>'.
    # Covers instant/sorcery removal that exiles rather than destroys.
    # Populated by oracle_parser.parse_can_exile_permanent.
    can_exile_permanent: bool = False
    # Symmetric reanimation — True for Living End-class mass reanimation from
    # all graveyards simultaneously.
    # Populated by oracle_parser.parse_has_symmetric_reanimation.
    has_symmetric_reanimation: bool = False
    # Phyrexian pip count (CR 107.4f) — TOTAL number of {C/P} pips in the
    # printed MANA COST; each may be paid with 2 life instead of one mana
    # of that pip's colour.  The per-colour breakdown lives on the cost
    # itself (`mana_cost.phyrexian`) because which colour a pip waives is
    # load-bearing; this field is the sum, kept for consumers that only
    # need the life price.
    # Populated at DB load from `mana_cost.phyrexian` — NOT from oracle
    # text, whose reminder clause names the symbol once however many pips
    # the cost carries.
    phyrexian_pip_count: int = 0
    # -- Batch 7 typed fields -----------------------------------------------
    # Token-creation effect -- True when oracle contains "create ... token" /
    # "put a ... token".
    # Populated by oracle_parser.parse_has_token_effect.
    has_token_effect: bool = False
    # Graveyard recursion -- True when oracle returns cards from a graveyard
    # to hand or battlefield.
    # Populated by oracle_parser.parse_has_graveyard_recursion.
    has_graveyard_recursion: bool = False
    # Graveyard hate -- True when oracle exiles graveyards or prevents GY casting.
    # Populated by oracle_parser.parse_has_graveyard_hate.
    has_graveyard_hate: bool = False
    # Spell-chain hate -- True when oracle limits spells per turn or taxes each spell.
    # Populated by oracle_parser.parse_has_spell_chain_hate.
    has_spell_chain_hate: bool = False
    # Stax classification -- which locking/taxing family this card belongs to:
    # 'chalice', 'blood_moon', 'canonist', 'torpor_orb', or None.
    # Populated by oracle_parser.parse_stax_class.
    stax_class: Optional[str] = None
    # Blood Moon forced basic land type ('mountain', 'island', etc.), or None.
    # Populated by oracle_parser.parse_stax_forced_basic.
    stax_forced_basic: Optional[str] = None
    # Cast trigger -- True when oracle has a 'when you cast' triggered ability.
    # Populated by oracle_parser.parse_has_cast_trigger.
    has_cast_trigger: bool = False
    # Recurring trigger -- True when oracle has a non-ETB periodic trigger
    # ('whenever ...' or 'at the beginning of ...').
    # Populated by oracle_parser.parse_has_recurring_trigger.
    has_recurring_trigger: bool = False
    # Limits opponent spell timing -- True for Teferi-style 'cast only as sorcery' statics.
    # Populated by oracle_parser.parse_limits_opponent_spell_timing.
    limits_opponent_spell_timing: bool = False
    # Charge-counter board wipe -- True for Ratchet Bomb / EE pattern.
    # Populated by oracle_parser.parse_has_charge_counter_wipe.
    has_charge_counter_wipe: bool = False
    # Mana-value wipe -- True for X-cost wipes that destroy by mana value.
    # Populated by oracle_parser.parse_has_mana_value_wipe.
    has_mana_value_wipe: bool = False
    # Sacrifice-for-damage -- True for Goblin Bombardment / Blasting Station pattern.
    # Populated by oracle_parser.parse_has_sacrifice_for_damage.
    has_sacrifice_for_damage: bool = False
    # Prevents graveyard ETB -- True for Grafdigger's Cage pattern.
    # Populated by oracle_parser.parse_prevents_graveyard_etb.
    prevents_graveyard_etb: bool = False
    # Prevents graveyard CASTING -- the other half of the Grafdigger's Cage
    # clause ("Players can't cast spells from graveyards or libraries").
    # The rules gate in CastManager.can_cast reads THIS, not the broad
    # has_graveyard_hate sideboard-advice predicate: a permanent that exiles
    # a graveyard removes fuel when activated and bans no cast at all.
    # Populated by oracle_parser.parse_prevents_graveyard_casting.
    prevents_graveyard_casting: bool = False
    # Continuous replacement -- 'if a card would be put into a
    # graveyard, exile it instead' (Leyline of the Void / Rest in
    # Peace family). The third slice of what has_graveyard_hate lumps
    # together; this one answers 'can this permanent stop a card
    # reaching a graveyard'.
    # Populated by oracle_parser.parse_exiles_cards_bound_for_graveyard.
    exiles_cards_bound_for_graveyard: bool = False
    # Reanimation -- True when the card puts a card from a GRAVEYARD onto
    # the BATTLEFIELD. Narrower than has_graveyard_recursion (which also
    # matches flashback's own reminder text); the ordering constraint
    # graveyard-before-battlefield is the discriminator.
    # Populated by oracle_parser.parse_reanimates_from_graveyard.
    reanimates_from_graveyard: bool = False
    # Requires creature target -- True when oracle needs a creature or creature-spell target.
    # Populated by oracle_parser.parse_requires_creature_target.
    requires_creature_target: bool = False
    # Alternate exile cost -- True for Grief/Solitude 'exile a ... rather than pay' pattern.
    # Populated by oracle_parser.parse_has_alternate_exile_cost.
    has_alternate_exile_cost: bool = False
    # Spectacle alternate cost (CR 702.131): cast for this cost instead of mana cost if
    # an opponent lost life this turn. None when the card has no spectacle.
    # Populated by oracle_parser.parse_spectacle_cost.
    spectacle_cost: Optional[ManaCost] = None
    # Flashback cost (CR 702.33): cast from graveyard for this cost (card exiles after).
    # None when the card has no printed Flashback. Cards granted flashback by Past in
    # Flames use template.mana_cost instead (this field stays None for them).
    # Populated by oracle_parser.parse_flashback_mana_cost.
    flashback_cost: Optional[ManaCost] = None
    # Discard effect -- True when oracle causes the target to discard cards.
    # Populated by oracle_parser.parse_has_discard_effect.
    has_discard_effect: bool = False
    # Scaling effect -- True when oracle has 'for each'/'for every' clause.
    # Populated by oracle_parser.parse_has_scaling_effect.
    has_scaling_effect: bool = False
    # Self trigger -- True when oracle has a 'when this' self-referential trigger.
    # Populated by oracle_parser.parse_has_self_trigger.
    has_self_trigger: bool = False
    # Recurring draw trigger -- True when oracle has 'whenever' + 'draw' pattern.
    # Populated by oracle_parser.parse_has_recurring_draw_trigger.
    has_recurring_draw_trigger: bool = False
    # Each-opponent effect -- True when oracle targets 'each opponent'/'each player'.
    # Populated by oracle_parser.parse_has_each_opponent_effect.
    has_each_opponent_effect: bool = False
    # Pump grant -- True when oracle grants +X/+Y bonus ('gets +'/'additional +').
    # Populated by oracle_parser.parse_has_pump_grant.
    has_pump_grant: bool = False
    # X-counter scaling -- True when oracle grants 'X +1/+1 counter(s)' (Ballista pattern).
    # Populated by oracle_parser.parse_has_x_counter_scaling.
    has_x_counter_scaling: bool = False
    # Lifegain equal to power -- True when oracle grants life equal to a creature's power.
    # Populated by oracle_parser.parse_has_lifegain_equal_power.
    has_lifegain_equal_power: bool = False
    # Lifegain effect -- True when oracle causes a player or creature to gain life.
    # Populated by oracle_parser.parse_has_lifegain_effect.
    has_lifegain_effect: bool = False
    # Exile own creature -- True when oracle exiles a creature the controller controls.
    # Populated by oracle_parser.parse_has_exile_own_creature.
    has_exile_own_creature: bool = False
    # Converge keyword -- True when oracle has 'converge'/'colors of mana spent'.
    # Populated by oracle_parser.parse_has_converge.
    has_converge: bool = False
    # Delirium keyword -- True when oracle has 'delirium' condition.
    # Populated by oracle_parser.parse_has_delirium.
    has_delirium: bool = False
    # All basic land types -- True when oracle grants all basic land types to lands.
    # Populated by oracle_parser.parse_has_all_basic_land_types.
    has_all_basic_land_types: bool = False
    # Colour-setting static (CR 105.2b, continuous-effects layer 5):
    # "" | "self" | "your_nonland_permanents".  Populated by
    # oracle_parser.parse_color_setting_scope; consumed by
    # ContinuousEffectsManager.recalculate, which derives the layer-5
    # effect from every permanent on the battlefield carrying it.
    color_setting_scope: str = ""
    # Destroy or exile -- True when oracle destroys or exiles a permanent.
    # Populated by oracle_parser.parse_has_destroy_or_exile.
    has_destroy_or_exile: bool = False
    # Artifact-count P/T scaling -- "gets +N/+N for each artifact you control".
    # Populated by oracle_parser.parse_has_artifact_count_scaling.
    has_artifact_count_scaling: bool = False
    # Surveil keyword -- True when oracle contains the surveil keyword.
    # Populated by oracle_parser.parse_has_surveil.
    has_surveil: bool = False
    # Scry keyword (CR 701.18) -- True when oracle contains the scry keyword.
    # Populated by oracle_parser.parse_has_scry.
    has_scry: bool = False
    # Coin-flip effect -- True when oracle involves flipping a coin.
    # Populated by oracle_parser.parse_has_coin_flip.
    has_coin_flip: bool = False
    # Mobilize keyword -- True when oracle contains the mobilize keyword.
    # Populated by oracle_parser.parse_has_mobilize.
    has_mobilize: bool = False
    # Land-type conditional bonuses: "gets +N/+N as long as you control a [Type]".
    # Maps lowercase land type (e.g. "mountain") to integer P/T bonus.
    # Populated by oracle_parser.parse_land_type_bonuses.
    land_type_bonuses: dict = None  # type: ignore[assignment]
    # Transform effect -- True when oracle references transforming.
    # Populated by oracle_parser.parse_has_transform_effect.
    has_transform_effect: bool = False
    # Instant/sorcery reference -- True when oracle counts instants or sorceries.
    # Populated by oracle_parser.parse_has_instant_or_sorcery_reference.
    has_instant_or_sorcery_reference: bool = False
    # Graveyard targeting -- True when oracle targets from a graveyard.
    # Populated by oracle_parser.parse_has_graveyard_target.
    has_graveyard_target: bool = False
    # Dual land search (Primeval Titan pattern) -- True when oracle searches for two lands.
    # Populated by oracle_parser.parse_has_dual_land_search.
    has_dual_land_search: bool = False
    # Energy-damage target -- True when oracle deals energy-scaled damage to a creature/pw.
    # Populated by oracle_parser.parse_has_energy_damage_target.
    has_energy_damage_target: bool = False
    # Energy production -- True when oracle produces energy (you get {E}).
    # Populated by oracle_parser.parse_has_energy_production.
    has_energy_production: bool = False
    # Look-at-top + hand selection -- True when oracle puts selected card(s) into hand.
    # Populated by oracle_parser.parse_has_look_hand_selection.
    has_look_hand_selection: bool = False
    # Cast-spell draw -- True when oracle draws on casting any spell (not noncreature-only).
    # Populated by oracle_parser.parse_has_cast_spell_draw.
    has_cast_spell_draw: bool = False
    # Opponent-cast damage -- True when oracle damages on opponent casting a spell.
    # Populated by oracle_parser.parse_has_opponent_cast_damage.
    has_opponent_cast_damage: bool = False
    # Mana add text -- True when oracle adds mana (mana rocks, rituals, etc.).
    # Populated by oracle_parser.parse_has_mana_add_text.
    has_mana_add_text: bool = False
    # Bounce land -- True when oracle contains 'return a land you control'.
    # Populated by oracle_parser.parse_has_bounce_land_oracle.
    has_bounce_land_oracle: bool = False
    # Sacrifice-search-land -- True for Expedition Map / Wayfarer's Bauble pattern.
    # Populated by oracle_parser.parse_has_sacrifice_search_land.
    has_sacrifice_search_land: bool = False
    # Emry graveyard cast -- True for 'choose target artifact card in your graveyard'.
    # Populated by oracle_parser.parse_has_emry_graveyard_cast.
    has_emry_graveyard_cast: bool = False
    # {C}{C},{T}: draw a card -- True for Endbringer pattern.
    # Populated by oracle_parser.parse_has_cc_tap_draw.
    has_cc_tap_draw: bool = False
    # Stax ability -- True for Stony Silence / Damping Sphere pattern.
    # Populated by oracle_parser.parse_has_stax_ability.
    has_stax_ability: bool = False
    # Pithing Needle lock -- True for Pithing Needle / Revoker pattern.
    # Populated by oracle_parser.parse_has_pithing_needle_lock.
    has_pithing_needle_lock: bool = False
    # Another-creature-enters trigger -- outer gate for ETB fan-out.
    # Populated by oracle_parser.parse_has_another_creature_enters_trigger.
    has_another_creature_enters_trigger: bool = False
    # Another-creature-enters lifegain -- conjunction: another creature+enters+gain+life.
    # Populated by oracle_parser.parse_has_another_creature_enters_lifegain.
    has_another_creature_enters_lifegain: bool = False
    # Cycling-watch trigger -- True for 'whenever you cycle (another card)'
    # battlefield permanents.  Populated by
    # oracle_parser.parse_has_cycling_watch_trigger.
    has_cycling_watch_trigger: bool = False
    # Damage dealt to each opponent when cycling-watch trigger fires.
    # Populated by oracle_parser.parse_cycling_watch_trigger_damage.
    cycling_watch_trigger_damage: int = 0
    # Life gained by controller when cycling-watch trigger fires.
    # Populated by oracle_parser.parse_cycling_watch_trigger_life_gain.
    cycling_watch_trigger_life_gain: int = 0
    # May-play-or-cast -- True for exile-and-play effects.
    # Populated by oracle_parser.parse_has_may_play_or_cast.
    has_may_play_or_cast: bool = False
    # Damage-equal scaling -- True for domain-scaling damage (Tribal Flames pattern).
    # Populated by oracle_parser.parse_has_damage_equal_scaling.
    has_damage_equal_scaling: bool = False
    # X-damage spell -- True for 'deals X damage' spells.
    # Populated by oracle_parser.parse_has_x_damage.
    has_x_damage: bool = False
    # Artifact pump equipment -- True for +1/+0 per-artifact equipment (Cranial Plating).
    # Populated by oracle_parser.parse_has_artifact_pump_equipment.
    has_artifact_pump_equipment: bool = False
    # Artifact-or-enchantment scaling -- True for Nettlecyst pattern.
    # Populated by oracle_parser.parse_has_artifact_or_enchantment_scaling.
    has_artifact_or_enchantment_scaling: bool = False
    # Channel ability clause -- substring from 'channel —'/'channel -' to end.
    # Empty string when card has no channel ability.
    # Populated by oracle_parser.parse_channel_clause.
    channel_clause: str = ""
    # Storm keyword (CR 702.39) -- True when oracle contains standalone "storm".
    # Populated by oracle_parser.parse_is_storm_spell.
    is_storm_spell: bool = False
    # Charge-counter ability -- True when oracle mentions "charge counter".
    # Populated by oracle_parser.parse_has_charge_counter_ability.
    has_charge_counter_ability: bool = False
    # Cast-triggered token by spell TYPE (CR 603) -- dict
    # {"spell_types": frozenset[str], "count": int} for
    # "whenever you cast a[n] <type> spell, create a token" (Pinnacle
    # Emissary/artifact, Monastery Mentor/noncreature, Young Pyromancer &
    # Talrand/instant-or-sorcery). None when absent.
    # Populated by oracle_parser.parse_cast_trigger_token.
    cast_trigger_token: Optional[dict] = None
    # ORDINAL cast trigger (CR 603.2) -- dict
    # {"ordinal": int, "caster_scope": "you"|"any"|"opponent",
    #  "reset": "turn", "clause": str} for
    # "whenever you cast your Nth spell each turn, <effect>".
    # `ordinal` is which spell of the turn meets the condition; `reset`
    # records that the counter's scope is the TURN, not the game (CR 500.8);
    # `caster_scope` says whose spells are counted. None when absent.
    # 45 cards in the pool carry this trigger shape -- it is the condition
    # gate for every effect on such a card, not just token creation.
    # Populated by oracle_parser.parse_ordinal_cast_trigger.
    ordinal_cast_trigger: Optional[dict] = None
    # Permanent-enters counter by card TYPE (CR 603) -- dict
    # {"permanent_type": str, "counter_power": int,
    #  "counter_toughness": int, "unblockable_this_turn": bool} for
    # "whenever this creature or another <type> you control enters, put a
    # +N/+N counter on this creature[. It can't be blocked this turn.]"
    # (Kappa Cannoneer/artifact). None when absent.
    # Populated by oracle_parser.parse_enters_type_counter.
    enters_type_counter: Optional[dict] = None
    # Modular keyword (CR 702.43) -- N from "Modular N" in oracle text.
    # 0 when absent or when N cannot be parsed (e.g. Modular—Sunburst).
    # ETB: card enters with this many +1/+1 counters.
    # DIES: its +1/+1 counters may be placed on a target artifact creature.
    # Populated by card_database.py oracle-derived properties section.
    modular_n: int = 0
    # -- Land destruction (spell tranche) -----------------------------------
    # True when the card is a spell-shaped "Destroy target land" effect with
    # only supported riders (see oracle_parser.parse_land_destruction).
    # Activated/triggered LD (Ghost Quarter, Fulminator classes) is a later
    # tranche and stays False here.
    destroys_target_land: bool = False
    # Structured rider data for the destroy-target-land clause: compound
    # artifact-or-land mode, nonbasic-only restriction, replacement-basic
    # search rider, damage-to-controller rider, caster-draw rider.  None
    # when the card is not in the class (unsupported riders refuse the
    # whole card — never half-executed).
    # Populated by oracle_parser.parse_land_destruction.
    land_destruction_data: Optional[dict] = None
    # Printed `[±N]: effect` loyalty abilities (CR 606), classified once at
    # DB load by oracle_parser.parse_loyalty_abilities into
    # {slot: LoyaltyAbility}.  `PlaneswalkerManager` dispatches off
    # `effect_kind` and refuses UNCLASSIFIED lines before charging loyalty.
    # None means "not parsed yet"; {} means "parsed, no loyalty abilities".
    loyalty_abilities: Optional[Dict[str, "LoyaltyAbility"]] = None
    # Same, for the back face of a transforming planeswalker DFC.  Set by
    # CardDatabase after `back_face_oracle` / `back_face_loyalty` land.
    back_face_loyalty_abilities: Optional[Dict[str, "LoyaltyAbility"]] = None

    def __post_init__(self) -> None:
        # Derive fields from oracle text for templates not loaded through
        # CardDatabase (e.g. synthetic templates in tests). CardDatabase sets
        # these explicitly; this fires only for empty/None fields.
        if self.oracle_text:
            from .oracle_parser import parse_is_land_sacrifice_tutor as _plst
            if not self.is_land_sacrifice_tutor:
                self.is_land_sacrifice_tutor = _plst(self.oracle_text)
            if self.loyalty_abilities is None:
                from .oracle_parser import parse_loyalty_abilities as _pl
                self.loyalty_abilities = _pl(self.oracle_text, self.loyalty)
            from .oracle_parser import (parse_warp_cost as _pwc,
                                        parse_dash_cost as _pdc,
                                        parse_escape_cost as _pec,
                                        parse_splice_cost as _psc,
                                        parse_spectacle_cost as _pspc,
                                        parse_can_target_player as _pctp,
                                        parse_can_target_planeswalker as _pctpw,
                                        parse_has_attack_trigger as _phat,
                                        parse_has_combat_damage_trigger as _phcdt,
                                        parse_has_lifegain_token_trigger as _phltt,
                                        parse_lifegain_token_type as _pltt,
                                        parse_targets_creature_spell as _ptcs,
                                        parse_targets_planeswalker_spell as _ptpws,
                                        parse_has_landfall as _phl,
                                        parse_has_library_search_opponent_trigger as _phlsot,
                                        parse_library_search_trigger_draws_card as _plstdc,
                                        parse_landfall_first_life_gain as _plflg,
                                        parse_landfall_third_damage as _pltd,
                                        parse_landfall_second_mana_colors as _plsmc,
                                        parse_has_combat_damage_player_trigger as _phcdpt,
                                        parse_can_destroy_artifact as _pcda,
                                        parse_can_destroy_enchantment as _pcde,
                                        parse_can_destroy_nonland_permanent as _pcdnp,
                                        parse_is_tutor as _pit,
                                        parse_has_noncreature_spell_cast_trigger as _phnsct,
                                        parse_has_artifact_synergy as _phas,
                                        parse_has_draw_effect as _phde,
                                        parse_can_exile_permanent as _pcep,
                                        parse_has_symmetric_reanimation as _phsr,
                                        parse_has_token_effect as _phte,
                                        parse_has_graveyard_recursion as _phgr,
                                        parse_has_discard_effect as _phde2,
                                        parse_is_storm_spell as _piss,
                                        parse_has_charge_counter_ability as _phcca,
                                        parse_cast_trigger_token as _pctt,
                                        parse_ordinal_cast_trigger as _poct,
                                        parse_enters_type_counter as _petc)
            from .card_database import KEYWORD_MAP as _KM
            import re as _re
            if self.warp_cost is None:
                self.warp_cost = _pwc(self.oracle_text)
            if self.dash_cost is None:
                self.dash_cost = _pdc(self.oracle_text)
            if self.escape_cost is None:
                _esc = _pec(self.oracle_text)
                if _esc:
                    self.escape_cost = _esc['cost']
                    if self.escape_exile_count == 0:
                        self.escape_exile_count = _esc['exile']
            if self.splice_cost is None:
                self.splice_cost = _psc(self.oracle_text)
            if self.spectacle_cost is None:
                self.spectacle_cost = _pspc(self.oracle_text)
            # Targeting capability flags — always derived (not gated on
            # a sentinel) since they default False and any oracle text
            # can contain the relevant phrases.
            if not self.can_target_player:
                self.can_target_player = _pctp(self.oracle_text)
            if not self.can_target_planeswalker:
                self.can_target_planeswalker = _pctpw(self.oracle_text)
            if not self.has_attack_trigger:
                self.has_attack_trigger = _phat(self.oracle_text, self.name)
            if not self.has_combat_damage_trigger:
                self.has_combat_damage_trigger = _phcdt(
                    self.oracle_text, self.name)
            if not self.has_lifegain_token_trigger:
                self.has_lifegain_token_trigger = _phltt(self.oracle_text)
            if self.has_lifegain_token_trigger and self.lifegain_token_type == 'creature':
                self.lifegain_token_type = _pltt(self.oracle_text)
            if not self.targets_creature_spell:
                self.targets_creature_spell = _ptcs(self.oracle_text)
            if not self.targets_planeswalker_spell:
                self.targets_planeswalker_spell = _ptpws(self.oracle_text)
            if not self.has_landfall:
                self.has_landfall = _phl(self.oracle_text)
            if not self.has_library_search_opponent_trigger:
                self.has_library_search_opponent_trigger = _phlsot(self.oracle_text)
            if not self.library_search_trigger_draws_card:
                self.library_search_trigger_draws_card = _plstdc(self.oracle_text)
            if not self.landfall_first_life_gain:
                self.landfall_first_life_gain = _plflg(self.oracle_text)
            if not self.landfall_third_damage:
                self.landfall_third_damage = _pltd(self.oracle_text)
            if not self.landfall_second_mana_colors:
                self.landfall_second_mana_colors = _plsmc(self.oracle_text)
            if not self.has_combat_damage_player_trigger:
                self.has_combat_damage_player_trigger = _phcdpt(self.oracle_text)
            if not self.can_destroy_artifact:
                self.can_destroy_artifact = _pcda(self.oracle_text)
            if not self.can_destroy_enchantment:
                self.can_destroy_enchantment = _pcde(self.oracle_text)
            if not self.can_destroy_nonland_permanent:
                self.can_destroy_nonland_permanent = _pcdnp(self.oracle_text)
            if not self.is_tutor:
                self.is_tutor = _pit(self.oracle_text)
            if not self.has_noncreature_spell_cast_trigger:
                self.has_noncreature_spell_cast_trigger = _phnsct(self.oracle_text)
            if not self.has_artifact_synergy:
                self.has_artifact_synergy = _phas(self.oracle_text)
            if not self.has_draw_effect:
                self.has_draw_effect = _phde(self.oracle_text)
            if not self.can_exile_permanent:
                self.can_exile_permanent = _pcep(self.oracle_text)
            if not self.has_symmetric_reanimation:
                self.has_symmetric_reanimation = _phsr(self.oracle_text)
            if self.phyrexian_pip_count == 0:
                # `mana_cost` is Optional: a template can be constructed
                # without one (lands, and test fixtures that only exercise
                # oracle-derived fields). Deriving the pip count from the
                # COST rather than from oracle reminder text is what makes
                # it correct for multi-pip cards, but it must not turn a
                # cost-less template into an AttributeError at construction.
                _mc = self.mana_cost
                self.phyrexian_pip_count = (
                    sum(_mc.phyrexian.values())
                    if _mc is not None and getattr(_mc, 'phyrexian', None)
                    else 0)
            if not self.has_token_effect:
                self.has_token_effect = _phte(self.oracle_text)
            if not self.has_graveyard_recursion:
                self.has_graveyard_recursion = _phgr(self.oracle_text)
            if not self.has_discard_effect:
                self.has_discard_effect = _phde2(self.oracle_text)
            if not self.is_storm_spell:
                self.is_storm_spell = _piss(self.oracle_text)
            if not self.has_charge_counter_ability:
                self.has_charge_counter_ability = _phcca(self.oracle_text)
            if self.cast_trigger_token is None:
                self.cast_trigger_token = _pctt(self.oracle_text)
            if self.ordinal_cast_trigger is None:
                self.ordinal_cast_trigger = _poct(self.oracle_text)
            if self.enters_type_counter is None:
                self.enters_type_counter = _petc(self.oracle_text)
            if self.land_type_bonuses is None:
                from .oracle_parser import parse_land_type_bonuses as _pltb
                self.land_type_bonuses = _pltb(self.oracle_text)
            # Derive keywords from oracle text for synthetic templates that
            # were constructed with keywords=set(). DB-loaded templates
            # already have complete keyword sets from KEYWORD_MAP scanning;
            # this catches only templates whose keywords field is still empty.
            if not self.keywords:
                _tl = self.oracle_text.lower()
                for _ks, _ke in _KM.items():
                    if _ks.lower() in _tl:
                        _pat = r'(?:^|\n)' + _re.escape(_ks.lower()) + r'(?:\s|$|,|\n)'
                        if _re.search(_pat, _tl):
                            self.keywords.add(_ke)
            # Derive modular_n for synthetic templates constructed with oracle_text.
            # DB-loaded templates have modular_n set explicitly by card_database.py;
            # this fires only when the field is still at its zero default.
            if self.modular_n == 0 and Keyword.MODULAR in self.keywords:
                _m = _re.search(r'(?:^|\n)modular\s+(\d+)',
                                self.oracle_text.lower())
                if _m:
                    self.modular_n = int(_m.group(1))
            # Land destruction (spell tranche) — warp_cost pattern: derive
            # for synthetic templates; CardDatabase sets both explicitly.
            if self.land_destruction_data is None and not self.destroys_target_land:
                from .oracle_parser import parse_land_destruction as _pld
                self.land_destruction_data = _pld(self.oracle_text)
                self.destroys_target_land = self.land_destruction_data is not None

    @property
    def is_creature(self) -> bool:
        return CardType.CREATURE in self.card_types

    @property
    def mana_count(self) -> int:
        """Units of mana one tap of this land produces (≥1 for any
        mana-producing land; E1 multi-mana schema)."""
        if self.mana_units:
            return len(self.mana_units)
        return 1 if self.produces_mana else 0

    @property
    def is_land(self) -> bool:
        return CardType.LAND in self.card_types

    @property
    def is_instant(self) -> bool:
        return CardType.INSTANT in self.card_types

    @property
    def is_sorcery(self) -> bool:
        return CardType.SORCERY in self.card_types

    @property
    def is_spell(self) -> bool:
        return not self.is_land

    @property
    def cmc(self) -> int:
        return self.mana_cost.cmc

    @property
    def has_flash(self) -> bool:
        return Keyword.FLASH in self.keywords

    @property
    def has_haste(self) -> bool:
        return Keyword.HASTE in self.keywords

    def __hash__(self):
        return hash(self.name)


# These sets are no longer used — scaling is detected from oracle text via
# CardTemplate.power_scales_with ("domain", "tarmogoyf", "delirium", "graveyard").
# Kept as empty dicts for backwards compatibility only; will be removed next refactor.
DOMAIN_POWER_CREATURES: set = set()
TARMOGOYF_CREATURES: set = set()
DELIRIUM_CREATURES: set = set()
GRAVEYARD_SCALING_CREATURES: set = set()

BASIC_LAND_TYPES = {"Plains", "Island", "Swamp", "Mountain", "Forest"}


@dataclass
class CardInstance:
    """A specific instance of a card in a game (tracks state)."""
    template: CardTemplate
    owner: int  # player index
    controller: int  # current controller
    instance_id: int  # unique per game
    zone: str = "library"  # library, hand, battlefield, graveyard, exile, stack
    tapped: bool = False
    summoning_sick: bool = False  # True when first enters battlefield
    # Counters
    plus_counters: int = 0
    minus_counters: int = 0
    loyalty_counters: int = 0
    other_counters: Dict[str, int] = field(default_factory=dict)
    # Combat state
    attacking: bool = False
    blocking: Optional[int] = None  # instance_id of creature being blocked
    blocked_by: List[int] = field(default_factory=list)
    # Damage
    damage_marked: int = 0
    # Deathtouch marker (CR 702.2 / SBA 704.5i): total damage dealt to
    # this creature by deathtouch sources since the last damage
    # cleanup. Written by engine/damage.py:deal_damage; consumed by
    # SBAManager.perform_deathtouch_check (destroy on any amount > 0).
    # Wears off with marked damage in cleanup_damage().
    _deathtouch_damage: int = 0
    # Temporary effects
    temp_power_mod: int = 0
    temp_toughness_mod: int = 0
    temp_keywords: Set[Keyword] = field(default_factory=set)
    # Continuous-effects-manager-derived P/T/keywords (engine/
    # continuous_effects.py). Kept SEPARATE from temp_power_mod/
    # temp_toughness_mod/temp_keywords above — those are a shared
    # dumping ground for one-shot pump spells, Dash, and other ad-hoc
    # mutations throughout engine/card_effects.py, cleared
    # unconditionally by cleanup_damage() every end of turn. The
    # cem_* fields represent RE-DERIVED continuous/static effects
    # (lords, anthems, equipment, static keyword grants) that persist
    # exactly as long as their source is on the battlefield; they are
    # cleared and rebuilt by ContinuousEffectsManager.recalculate()
    # on every call, not by turn-based cleanup, so mixing them into
    # temp_power_mod would either wipe a continuous effect early
    # (at end of turn) or double-count it (recalculate() re-applying
    # on top of a value cleanup_damage() never reset).
    cem_power_mod: int = 0
    cem_toughness_mod: int = 0
    cem_keywords: Set[Keyword] = field(default_factory=set)
    # Layer-5 colour SET (CR 105.2b / CR 613.1e).  None = no
    # colour-setting effect applies and the permanent is its printed
    # colour; a frozenset REPLACES the printed colour (a set, not an
    # accumulator — unlike the cem_* fields above — because that is
    # what "is all colors" does).  Written only by
    # ContinuousEffectsManager.recalculate, read through the `colors`
    # property below.  Never write the template: templates are shared
    # database objects and a mutation there leaks into every other
    # game in the process.
    cem_colors_set: Optional[frozenset] = None
    # Land animation ("this land becomes an N/M creature until end of
    # turn") — Track H. While True the instance belongs to the combat
    # class (creatures property, can_attack/can_block, SBA death
    # check); the printed P/T ride on temp_power_mod/temp_toughness_mod
    # and every part wears off together in cleanup_damage().
    is_animated: bool = False
    # True once `_transform_permanent` (engine/oracle_resolver.py) has
    # flipped this instance to its back face. Consulted by
    # `effective_card_types`/`effective_power`/`effective_toughness`
    # below to select front- vs back-face characteristics — the single
    # owner of "what type/P/T is this permanent right now" for
    # transformed DFCs. Previously an undeclared setattr-only
    # attribute read via getattr(..., False) at 9 call sites.
    is_transformed: bool = False
    # Tracking
    turned_face_up: bool = True
    entered_battlefield_this_turn: bool = False
    attacked_this_turn: bool = False
    # CR 509.1b turn-scoped evasion: set True by a "it can't be blocked
    # this turn" trigger (Kappa Cannoneer's enters-counter). Read by
    # CombatManager._can_block; reset at the controller's next turn start.
    cannot_be_blocked_this_turn: bool = False
    # CR 400.7 object identity: a card that changes zones becomes a new
    # object. The engine reuses one CardInstance across zones, so each
    # battlefield entry bumps this sequence; delayed one-shot riders
    # (e.g. "exile it at the beginning of the next end step") capture it
    # at registration and go stale if the object re-enters.
    battlefield_entry_seq: int = 0
    # Energy counters (for energy decks)
    energy_produced: int = 0
    # Flashback (granted by Past in Flames)
    has_flashback: bool = False
    # Targets (when on stack)
    targets: List[int] = field(default_factory=list)  # instance_ids
    # Instance-level tags (for equipment effects etc.)
    instance_tags: Set[str] = field(default_factory=set)
    # CR 111: token-ness is a property of the OBJECT, not the template.
    # Set by the token-creation funnel (PermanentEffects.create_token);
    # read by SBA 704.5f (tokens off the battlefield cease to exist)
    # and the cast gate (CR 111.2 — tokens aren't cards, can't be cast).
    is_token: bool = False

    # Activated abilities granted to THIS instance by resolved effects
    # (saga "gains '<cost>: <effect>'" chapters, etc.). Oracle-text
    # fragments of the form "<cost>: <effect>"; cleared when the
    # permanent leaves the battlefield.
    granted_abilities: List[str] = field(default_factory=list)
    # Back-reference to game state (set when entering battlefield)
    # Aura attachment back-reference (CR 303.4): instance_id of the object
    # this Aura enchants, or None when unattached. The HOST additionally
    # carries an `attached_{aura_id}` instance tag, mirroring Equipment.
    # Per-turn activation ledger, keyed by ActivatedAbility.index. Cleared in
    # new_turn() and enter_battlefield() (CR 400.7 — battlefield re-entry is a
    # NEW object, so its budget resets). Deliberately NOT cleared in untap():
    # turn_manager untaps some opponent permanents without calling new_turn(),
    # and clearing there would refresh a once-each-turn budget twice per turn
    # cycle.
    activations_this_turn: Dict[int, int] = field(default_factory=dict)
    attached_to_id: Optional[int] = None
    # "As this permanent enters, choose a color" (CR 614.12-style entry
    # choice). Set once at ETB from the `choose_mana_color` callback and read
    # by the mana resolver for "of the chosen color" riders. None when the
    # permanent makes no such choice.
    chosen_color: Optional[str] = None
    _game_state: Any = field(default=None, repr=False)
    # Evoke tracking
    _evoked: bool = False
    _dashed: bool = False  # Cast via Dash: has haste, returns to hand at end of turn
    _escaped: bool = False  # Cast via Escape from graveyard
    # Suspend tracking (LE-E2): when a suspend card is paid-and-exiled,
    # suspended=True and suspend_counters holds the number of time counters
    # remaining. Each of the controller's upkeeps removes one; when the last
    # is removed the card is cast for free. See
    # docs/diagnostics/2026-04-24_living_end_consolidated_findings.md (LE-E2).
    suspend_counters: int = 0
    suspended: bool = False

    @property
    def name(self) -> str:
        return self.template.name

    # ── Effective characteristics (CR 613-adjacent) ─────────────────
    # Single owner of "what is true about this permanent's printed
    # characteristics RIGHT NOW" for a transformed DFC. Front vs back
    # face is selected once, here, instead of every reader guessing
    # independently (the bug class this fixes: player_state.creatures/
    # planeswalkers previously hardcoded "transformed ⇒ became a
    # planeswalker" — true only for the one DFC shape the original
    # transform code was built against).

    @property
    def effective_card_types(self) -> List[CardType]:
        """Card types of whichever face is currently active."""
        if self.is_transformed and self.template.back_face_types:
            return self.template.back_face_types
        return self.template.card_types

    @property
    def effective_subtypes(self) -> List[str]:
        """Subtypes of whichever face is currently active."""
        if self.is_transformed and self.template.back_face_types:
            return self.template.back_face_subtypes
        return self.template.subtypes

    @property
    def effective_is_creature(self) -> bool:
        return CardType.CREATURE in self.effective_card_types

    @property
    def effective_is_planeswalker(self) -> bool:
        return CardType.PLANESWALKER in self.effective_card_types

    def _effective_printed_power(self) -> int:
        """Printed power of whichever face is currently active."""
        if (self.is_transformed and self.template.back_face_types
                and self.template.back_face_power is not None):
            return self.template.back_face_power
        return self.template.power or 0

    def _effective_printed_toughness(self) -> int:
        """Printed toughness of whichever face is currently active."""
        if (self.is_transformed and self.template.back_face_types
                and self.template.back_face_toughness is not None):
            return self.template.back_face_toughness
        return self.template.toughness or 0

    def _effective_oracle_text(self) -> str:
        """Oracle text of whichever face is currently active — drives
        the regex-based scaling detection in _dynamic_base_power/
        _dynamic_base_toughness below."""
        if self.is_transformed and self.template.back_face_oracle:
            return self.template.back_face_oracle
        return self.template.oracle_text or ''

    @property
    def power(self) -> int:
        base = self._dynamic_base_power()
        return (base + self.plus_counters - self.minus_counters
                + self.temp_power_mod + self.cem_power_mod)

    @property
    def toughness(self) -> int:
        base = self._dynamic_base_toughness()
        return (base + self.plus_counters - self.minus_counters
                + self.temp_toughness_mod + self.cem_toughness_mod)

    # ── Counters by kind (CR 122) ────────────────────────────────────
    # Thin accessors over the SAME four fields declared above — NOT a
    # second counter mechanism. They exist so a caller that only knows a
    # counter kind as a parsed string ("+1/+1", "charge") does not have to
    # re-derive which field holds it, and so P/T-bearing counters keep
    # moving `power`/`toughness` (which is what makes the zero-toughness
    # SBA terminate a -1/-1-paid activation loop).

    def counter_count(self, kind: str) -> int:
        """How many counters of `kind` are on this permanent."""
        if kind == COUNTER_KIND_PLUS:
            return self.plus_counters
        if kind == COUNTER_KIND_MINUS:
            return self.minus_counters
        if kind == COUNTER_KIND_LOYALTY:
            return self.loyalty_counters
        return self.other_counters.get(kind, 0)

    def adjust_counters(self, kind: str, delta: int) -> None:
        """Add (or, with a negative delta, remove) counters of `kind`.

        Counts never go below zero — CR 122.3: removing more counters than
        are present simply removes what is there.
        """
        if kind == COUNTER_KIND_PLUS:
            self.plus_counters = max(0, self.plus_counters + delta)
        elif kind == COUNTER_KIND_MINUS:
            self.minus_counters = max(0, self.minus_counters + delta)
        elif kind == COUNTER_KIND_LOYALTY:
            self.loyalty_counters = max(0, self.loyalty_counters + delta)
        else:
            self.other_counters[kind] = max(
                0, self.other_counters.get(kind, 0) + delta)

    def _get_domain_count(self) -> int:
        """Count basic land types among lands controlled by this card's controller."""
        if self._game_state is None:
            return 0
        player = self._game_state.players[self.controller]
        # Oracle-driven detection of "lands you control are every basic
        # land type" (Leyline of the Guildpact pattern). Same predicate
        # as engine/mana_payment.py::ManaPayment.has_leyline_of_guildpact.
        for c in player.battlefield:
            if getattr(c.template, 'has_all_basic_land_types', False):
                if any(l.template.is_land for l in player.battlefield):
                    return 5
        found_types: set = set()
        for land in player.battlefield:
            if land.template.is_land:
                for st in land.template.subtypes:
                    if st in BASIC_LAND_TYPES:
                        found_types.add(st)
        return len(found_types)

    def _get_tarmogoyf_count(self) -> int:
        """Count card types among cards in ALL graveyards."""
        if self._game_state is None:
            return 0
        type_set: set = set()
        for player in self._game_state.players:
            for card in player.graveyard:
                for ct in card.template.card_types:
                    type_set.add(ct)
        return len(type_set)

    def _get_artifact_count(self) -> int:
        """Count artifacts controlled by this card's controller."""
        if self._game_state is None:
            return 0
        player = self._game_state.players[self.controller]
        return sum(1 for c in player.battlefield if CardType.ARTIFACT in c.template.card_types)

    _PERMANENT_TYPE_WORDS = {
        'land': CardType.LAND, 'lands': CardType.LAND,
        'creature': CardType.CREATURE, 'creatures': CardType.CREATURE,
        'artifact': CardType.ARTIFACT, 'artifacts': CardType.ARTIFACT,
        'enchantment': CardType.ENCHANTMENT, 'enchantments': CardType.ENCHANTMENT,
        'planeswalker': CardType.PLANESWALKER, 'planeswalkers': CardType.PLANESWALKER,
    }

    def _get_permanent_type_count(self, type_word: str) -> int:
        """Count permanents controlled by this card's controller that
        match `type_word` — a card type ("land", "creature",
        "artifact", "enchantment", "planeswalker"), the generic
        "permanent"/"permanents", or a subtype (a land type like
        "Island", or a creature type like "Soldier").

        Generalizes `_get_domain_count` (land subtypes) /
        `_get_artifact_count` (artifact card type) into one
        oracle-driven dispatch for "the number of X you control" CDAs
        (Cultivator Colossus-class effects — 47 cards in the DB share
        this exact oracle-text shape).
        """
        if self._game_state is None:
            return 0
        player = self._game_state.players[self.controller]
        word = type_word.lower()
        if word in ('permanent', 'permanents'):
            return len(player.battlefield)
        card_type = self._PERMANENT_TYPE_WORDS.get(word)
        if card_type is not None:
            return sum(1 for c in player.battlefield
                      if card_type in c.template.card_types)
        # Subtype match (tribal / land-type CDAs) — naive regular-
        # plural strip ("Islands" -> "Island", "Soldiers" ->
        # "Soldier"). Irregular plurals (Elves, Wolves) are a
        # documented gap: 0 such cards in the registered 16-deck pool
        # today; extend if one enters.
        singular = type_word[:-1] if type_word.endswith('s') else type_word
        subtype = singular[:1].upper() + singular[1:]
        return sum(1 for c in player.battlefield
                  if subtype in c.template.subtypes)

    def _get_artifact_or_enchantment_count(self) -> int:
        """Count permanents that are artifacts OR enchantments,
        controlled by this card's controller (no double-count of
        cards that are both, e.g. Urza's Saga or theros enchantment-
        creatures).  Mirrors the set-union semantics of equipment
        oracles like Nettlecyst's "+1/+1 for each artifact and/or
        enchantment you control."
        """
        if self._game_state is None:
            return 0
        player = self._game_state.players[self.controller]
        count = 0
        for c in player.battlefield:
            types = c.template.card_types
            if (CardType.ARTIFACT in types
                    or CardType.ENCHANTMENT in types):
                count += 1
        return count

    def _get_controller_battlefield(self):
        """Get the controller's battlefield."""
        if self._game_state is None:
            return []
        return self._game_state.players[self.controller].battlefield

    def _has_delirium(self) -> bool:
        """Check if controller has 4+ card types in graveyard (delirium)."""
        if self._game_state is None:
            return False
        player = self._game_state.players[self.controller]
        type_set: set = set()
        for card in player.graveyard:
            for ct in card.template.card_types:
                type_set.add(ct)
        return len(type_set) >= 4

    def _get_gy_instants_sorceries(self) -> int:
        """Count instants and sorceries in controller's graveyard."""
        if self._game_state is None:
            return 0
        player = self._game_state.players[self.controller]
        return sum(1 for c in player.graveyard
                   if c.template.is_instant or c.template.is_sorcery)

    _GY_NONPERMANENT_TYPES = {CardType.INSTANT, CardType.SORCERY}

    def _get_graveyard_type_count(self, type_word: str, scope: str) -> int:
        """Count graveyard cards matching `type_word` across `scope`
        — resolution side of the `graveyard_count:<formula>:<type>:
        <scope>` CDA bucket (Mortivore/Bonehoard-class: "power and
        toughness are each equal to the number of creature cards in
        all graveyards"). Generalizes the narrow, hardcoded
        `_get_gy_instants_sorceries` (instant/sorcery, controller-
        only) the same way `_get_permanent_type_count` generalized
        `_get_artifact_count` — parse generically, resolve
        specifically, fall back to counting every card for an
        unrecognized type word rather than returning 0.
        """
        if self._game_state is None:
            return 0
        if scope == 'your':
            graveyards = [self._game_state.players[self.controller].graveyard]
        elif scope == 'opponents':
            graveyards = [p.graveyard for i, p in enumerate(self._game_state.players)
                          if i != self.controller]
        else:  # 'all'
            graveyards = [p.graveyard for p in self._game_state.players]
        cards = [c for gy in graveyards for c in gy]

        word = type_word.lower()
        if word in ('any', 'card'):
            return len(cards)
        if word == 'creature':
            return sum(1 for c in cards if CardType.CREATURE in c.template.card_types)
        if word == 'artifact':
            return sum(1 for c in cards if CardType.ARTIFACT in c.template.card_types)
        if word == 'land':
            return sum(1 for c in cards if CardType.LAND in c.template.card_types)
        if word == 'nonbasic_land':
            return sum(1 for c in cards if CardType.LAND in c.template.card_types
                       and Supertype.BASIC not in c.template.supertypes)
        if word == 'sorcery':
            return sum(1 for c in cards if CardType.SORCERY in c.template.card_types)
        if word == 'instant':
            return sum(1 for c in cards if CardType.INSTANT in c.template.card_types)
        if word == 'enchantment':
            return sum(1 for c in cards if CardType.ENCHANTMENT in c.template.card_types)
        if word == 'planeswalker':
            return sum(1 for c in cards if CardType.PLANESWALKER in c.template.card_types)
        if word in ('instant_and_sorcery', 'instant_sorcery'):
            return sum(1 for c in cards
                       if CardType.INSTANT in c.template.card_types
                       or CardType.SORCERY in c.template.card_types)
        if word == 'permanent':
            return sum(1 for c in cards
                       if not (set(c.template.card_types) & self._GY_NONPERMANENT_TYPES))
        if word == 'noncreature_nonland':
            return sum(1 for c in cards
                       if CardType.CREATURE not in c.template.card_types
                       and CardType.LAND not in c.template.card_types)
        # Unrecognized type word: count every card, matching
        # _get_permanent_type_count's "don't return a silent 0"
        # fallback discipline for words this dispatch doesn't know.
        return len(cards)

    def _dynamic_base_power(self) -> int:
        """Calculate base power, accounting for domain and similar effects.
        Scaling type is detected at template load time from oracle text
        (CardTemplate.power_scales_with) — no card names hardcoded here.

        Reads the currently-active face's printed power/oracle text
        (_effective_printed_power/_effective_oracle_text) so a
        transformed permanent's power reflects its back face, not the
        front face it flipped from.
        """
        if self.zone != "battlefield":
            return self._effective_printed_power()
        scaling = self.template.power_scales_with

        if scaling == "domain":
            return self._get_domain_count()
        if scaling.startswith("permanent_count:"):
            return self._get_permanent_type_count(scaling.split(":", 1)[1])
        if scaling == "tarmogoyf":
            return self._get_tarmogoyf_count()
        if scaling == "delirium":
            if self._has_delirium():
                # Parse bonus from oracle: "gets +N/+M" → use N for power
                import re as _re
                oracle = self._effective_oracle_text().lower()
                m = _re.search(r'gets?\s+\+(\d+)/\+(\d+)', oracle)
                bonus = int(m.group(1)) if m else 2
                return self._effective_printed_power() + bonus
            return self._effective_printed_power()
        if scaling == "graveyard":
            return self._effective_printed_power() + self._get_gy_instants_sorceries()
        if scaling.startswith("graveyard_count:"):
            formula, type_word, scope = scaling.split(":", 1)[1].split(":")
            # "sym" and "goyf" both define power as the raw count —
            # they differ only in toughness (see _dynamic_base_toughness).
            return self._get_graveyard_type_count(type_word, scope)

        base = self._effective_printed_power()
        # Construct Token and similar: "gets +N/+N for each artifact you control"
        # NB: must match the bonus-per-artifact pattern specifically. The naive
        # `'artifact you control' in oracle` match triggered on Affinity reminder
        # text ("costs {1} less to cast for each artifact you control") and
        # inflated every Affinity creature's power to the controller's artifact
        # count. Scope the match to the actual Construct/Plating pattern.
        if getattr(self.template, 'has_artifact_count_scaling', False):
            base = self._effective_printed_power() + self._get_artifact_count()
        # Equipment scaling (Cranial Plating, Nettlecyst, etc.)
        # Tags are equipped_{instance_id} — unique per equipment, supports stacking.
        for tag in self.instance_tags:
            if tag.startswith("equipped_"):
                try:
                    equip_iid = int(tag[len("equipped_"):])
                    if self._game_state is None:
                        continue
                    equip_perm = self._game_state.get_card_by_id(equip_iid)
                    if equip_perm is None:
                        continue
                    eq_oracle = (equip_perm.template.oracle_text or '').lower()
                    # "X and/or Y" clauses (Nettlecyst: artifact and/or
                    # enchantment) count the union, not just X. Detect
                    # the union form before the artifact-only form.
                    if ('artifact and/or enchantment' in eq_oracle
                            or 'enchantment and/or artifact' in eq_oracle):
                        base += self._get_artifact_or_enchantment_count()
                    elif 'for each artifact' in eq_oracle or 'artifact you control' in eq_oracle:
                        base += self._get_artifact_count()
                except (ValueError, AttributeError):
                    pass
        # Land-type conditional bonus: "gets +N/+N as long as you control
        # a [LandType]" (Wild Nacatl / similar creatures). Each bonus fires
        # when the controller has at least one land of the matching subtype.
        lt_bonuses = getattr(self.template, 'land_type_bonuses', None) or {}
        if lt_bonuses and self._game_state is not None:
            controller = self.controller
            for land_type, bonus in lt_bonuses.items():
                if any(
                    land_type.lower() in (s.lower() for s in c.template.subtypes)
                    for c in self._game_state.players[controller].battlefield
                    if CardType.LAND in c.template.card_types
                ):
                    base += bonus
        return base

    def _dynamic_base_toughness(self) -> int:
        """Calculate base toughness — mirrors _dynamic_base_power scaling logic."""
        if self.zone != "battlefield":
            return self._effective_printed_toughness()
        scaling = self.template.power_scales_with

        if scaling == "domain":
            return self._get_domain_count()
        if scaling.startswith("permanent_count:"):
            return self._get_permanent_type_count(scaling.split(":", 1)[1])
        if scaling == "tarmogoyf":
            return self._get_tarmogoyf_count() + 1
        if scaling == "delirium":
            if self._has_delirium():
                import re as _re
                oracle = self._effective_oracle_text().lower()
                m = _re.search(r'gets?\s+\+(\d+)/\+(\d+)', oracle)
                bonus = int(m.group(2)) if m else 2
                return self._effective_printed_toughness() + bonus
            return self._effective_printed_toughness()
        if scaling == "graveyard":
            return self._effective_printed_toughness() + self._get_gy_instants_sorceries()
        if scaling.startswith("graveyard_count:"):
            formula, type_word, scope = scaling.split(":", 1)[1].split(":")
            count = self._get_graveyard_type_count(type_word, scope)
            if formula == "sym":
                return count
            if formula == "goyf":
                return count + 1
            # "power_only": toughness is NOT derived from the count at
            # all (Enigma Drake-class — only power is a CDA; toughness
            # stays at whatever the card is actually printed with).
            return self._effective_printed_toughness()

        base = self._effective_printed_toughness()
        # Same tightening as _dynamic_base_power — see note above.
        if getattr(self.template, 'has_artifact_count_scaling', False):
            base = self._effective_printed_toughness() + self._get_artifact_count()
        # Equipment toughness scaling — only applies when toughness component is non-zero.
        # e.g. Nettlecyst: +1/+1 for each artifact → toughness bonus applies
        # e.g. Cranial Plating: +1/+0 for each artifact → NO toughness bonus
        import re as _re2
        for tag in self.instance_tags:
            if tag.startswith("equipped_"):
                try:
                    equip_iid = int(tag[len("equipped_"):])
                    if self._game_state is None:
                        continue
                    equip_perm = self._game_state.get_card_by_id(equip_iid)
                    if equip_perm is None:
                        continue
                    eq_oracle = (equip_perm.template.oracle_text or '').lower()
                    # Parse +A/+B from the oracle — only add to toughness if B != 0
                    m = _re2.search(r'gets \+(\w+)/\+(\w+)\s+for each', eq_oracle)
                    if m:
                        tou_component = m.group(2)
                        if tou_component != '0':
                            # "X and/or Y" set-union form (Nettlecyst).
                            if ('artifact and/or enchantment' in eq_oracle
                                    or 'enchantment and/or artifact' in eq_oracle):
                                base += self._get_artifact_or_enchantment_count()
                            else:
                                base += self._get_artifact_count()
                except (ValueError, AttributeError):
                    pass
        # Land-type conditional bonus (mirrors _dynamic_base_power logic).
        lt_bonuses = getattr(self.template, 'land_type_bonuses', None) or {}
        if lt_bonuses and self._game_state is not None:
            controller = self.controller
            for land_type, bonus in lt_bonuses.items():
                if any(
                    land_type.lower() in (s.lower() for s in c.template.subtypes)
                    for c in self._game_state.players[controller].battlefield
                    if CardType.LAND in c.template.card_types
                ):
                    base += bonus
        return base

    @property
    def current_loyalty(self) -> int:
        return (self.template.loyalty or 0) + self.loyalty_counters

    @property
    def keywords(self) -> Set[Keyword]:
        return self.template.keywords | self.temp_keywords | self.cem_keywords

    @property
    def colors(self) -> Set[Color]:
        """This permanent's CURRENT colour (CR 105.2, CR 613 layer 5).

        The printed colour unless a colour-setting continuous effect
        applies, in which case that effect's colour replaces it
        (CR 105.2b "is all colors" SETS colour, it does not add).
        Every runtime colour read on a permanent goes through here so
        that layer-5 effects are visible to colour-conditional rules
        (protection, colour-conditional keyword grants, "permanents
        that are one or more colors" sweeps)."""
        if self.cem_colors_set is not None:
            return set(self.cem_colors_set)
        return set(self.template.colors)

    @property
    def has_deathtouch(self) -> bool:
        """DamageSource protocol hook (engine/damage.py:deal_damage
        reads `source.has_deathtouch` to write the CR 704.5i marker).
        Keyword-derived — includes temp-granted deathtouch."""
        return Keyword.DEATHTOUCH in self.keywords

    @property
    def has_lifelink(self) -> bool:
        """DamageSource protocol hook (engine/damage.py:deal_damage
        reads `source.has_lifelink` to apply CR 702.15 life gain).
        Keyword-derived — includes temp-granted lifelink."""
        return Keyword.LIFELINK in self.keywords

    @property
    def has_summoning_sickness(self) -> bool:
        """A creature has summoning sickness if it entered this turn and doesn't have haste."""
        if not (self.template.is_creature or self.is_animated):
            return False
        if Keyword.HASTE in self.keywords:
            return False
        if self._dashed:  # Dash grants haste
            return False
        return self.summoning_sick

    @property
    def can_attack(self) -> bool:
        if not (self.template.is_creature or self.is_animated):
            return False
        if self.tapped:
            return False
        if self.has_summoning_sickness:
            return False
        if Keyword.DEFENDER in self.keywords:
            return False
        return True

    @property
    def can_block(self) -> bool:
        if not (self.template.is_creature or self.is_animated):
            return False
        if self.tapped:
            return False
        return True

    @property
    def is_dead(self) -> bool:
        if not (self.template.is_creature or self.is_animated):
            return False
        if self.toughness <= 0:
            # CR 704.5g: toughness 0 or less puts the creature into the
            # graveyard — not a destroy effect, indestructible can't save it.
            return True
        if Keyword.INDESTRUCTIBLE in self.keywords:
            # CR 704.5h exemption: lethal marked damage destroys, and
            # indestructible permanents can't be destroyed.
            return False
        return self.damage_marked >= self.toughness

    def tap(self):
        self.tapped = True

    def untap(self):
        self.tapped = False

    def reset_combat(self):
        self.attacking = False
        self.blocking = None
        self.blocked_by = []

    def cleanup_damage(self):
        self.damage_marked = 0
        # CR 704.5i marker wears off with the marked damage it rode on.
        self._deathtouch_damage = 0
        self.temp_power_mod = 0
        self.temp_toughness_mod = 0
        self.temp_keywords.clear()
        # "Until end of turn" animation wears off with the temp mods it
        # rides on — and ends immediately when the permanent leaves the
        # battlefield (every cleanup_damage call site is one of those
        # two events).
        self.is_animated = False

    def take_damage(self, amount: int, source) -> None:
        """Receive `amount` damage as a permanent (CR 119.3).

        Implements the `DamageTarget` protocol consumed by
        `engine/damage.py:deal_damage`. Routing is by card type,
        not by card name:

        - Creature: accrue `damage_marked`. SBA 704.5h destroys the
          creature on the next pass if `damage_marked >= toughness`.
        - Planeswalker: decrement `loyalty_counters`. SBA 704.5p
          destroys it when `loyalty_counters <= 0`.
        - Battles (future): decrement defense counters. Not yet
          implemented; falls through to no-op until W1 adds the
          battle target type.

        A single CardInstance can be both creature and PW
        (Tibalt, Cosmic Impostor-style cards): the engine treats
        the *current* face / current types as authoritative; we
        check creature first because the most common multi-type
        case is creature-with-PW-back-face and we are damaging the
        face that is currently on the battlefield.

        `source` is accepted to satisfy the protocol; today it
        feeds into the deathtouch marker that `deal_damage` writes
        directly onto `_deathtouch_damage` before reaching here.
        Triggers that care about "dealt damage by X" read it from
        the event log, not from this parameter.
        """
        if CardType.CREATURE in self.template.card_types:
            self.damage_marked += amount
            return
        if CardType.PLANESWALKER in self.template.card_types:
            self.loyalty_counters -= amount
            return
        # Unknown permanent type (battle, etc.) — no-op until the
        # rules layer for that type lands. Returning silently is
        # safe; the SBA pass will not destroy anything that wasn't
        # marked.
        return

    def new_turn(self):
        """Called at the start of controller's turn."""
        self.summoning_sick = False
        self.entered_battlefield_this_turn = False
        self.attacked_this_turn = False
        self.cannot_be_blocked_this_turn = False
        self.activations_this_turn.clear()

    def enter_battlefield(self):
        self.zone = "battlefield"
        self.summoning_sick = True
        self.entered_battlefield_this_turn = True
        # CR 400.7: each battlefield entry is a new object; delayed
        # one-shot riders bound to a previous entry must lose track of it.
        self.battlefield_entry_seq += 1
        if self.template.is_land and self.template.enters_tapped:
            self.tapped = True
        # CR 400.7: a new object gets a fresh activation budget.
        self.activations_this_turn.clear()

    def __hash__(self):
        return self.instance_id

    def __eq__(self, other):
        if isinstance(other, CardInstance):
            return self.instance_id == other.instance_id
        return False
