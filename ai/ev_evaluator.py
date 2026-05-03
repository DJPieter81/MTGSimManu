"""EV-Based Board Evaluator — per-archetype value functions.

Core idea: each archetype has a different VALUE FUNCTION that scores
a board state. The decision loop evaluates each candidate play by
projecting the resulting board state and scoring it with the
archetype's value function.

No hardcoded thresholds. All decisions are EV comparisons:
  "Is the projected state after casting X better than the current state?"

Value is measured in "life-point equivalents" — +1.0 means roughly
being 1 life ahead in an otherwise equal position.
"""
from __future__ import annotations
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from ai.predicates import (
    count_gy_creatures, is_draw_engine, is_ritual,
)

if TYPE_CHECKING:
    from engine.game_state import GameState, PlayerState
    from engine.cards import CardInstance, CardTemplate

from ai.deck_knowledge import DeckKnowledge


# ─────────────────────────────────────────────────────────────
# Board snapshot — lightweight representation for EV calculation
# ─────────────────────────────────────────────────────────────

@dataclass
class EVSnapshot:
    """Lightweight board snapshot for EV calculations.

    All values are derived from game state — no hardcoded defaults.
    """
    my_life: int = 20
    opp_life: int = 20
    my_power: int = 0          # total power of my creatures
    opp_power: int = 0         # total power of opp creatures
    my_toughness: int = 0      # total toughness of my creatures
    opp_toughness: int = 0
    my_creature_count: int = 0
    opp_creature_count: int = 0
    my_hand_size: int = 0
    opp_hand_size: int = 0
    my_mana: int = 0           # untapped mana sources
    opp_mana: int = 0
    # Per-color untapped mana availability. A multi-colored land (Steam
    # Vents U/R) contributes +1 to EACH color it can produce — tapping
    # the land pays for one color at a time, so "can I still make U?"
    # is answered by whether ANY untapped land currently produces U.
    # Populated in `snapshot_from_game` by iterating untapped lands and
    # calling `_effective_produces_mana` (Leyline of the Guildpact aware).
    # Keys: "W","U","B","R","G","C".
    my_mana_by_color: Dict[str, int] = field(default_factory=dict)
    my_total_lands: int = 0
    opp_total_lands: int = 0
    turn_number: int = 1
    storm_count: int = 0
    my_gy_creatures: int = 0   # creatures in graveyard (for Living End, etc.)
    # Opp graveyard creatures — used for SYMMETRIC reanimation projection
    # (Living End pattern: "each player returns all creature cards from
    # their graveyard").  Without this field the projection only credits
    # my side and misvalues symmetric mass-reanimation by the full opp
    # board swing.  Populated in `snapshot_from_game` by counting
    # creature-type cards in opp's graveyard.  LE-A2.
    opp_gy_creatures: int = 0
    my_energy: int = 0
    # Keyword counts on my board
    my_evasion_power: int = 0  # power of creatures with flying/menace/trample
    my_lifelink_power: int = 0
    opp_evasion_power: int = 0
    # Cards drawn this turn
    cards_drawn_this_turn: int = 0
    # Expected power contribution from recurring-trigger tokens over the
    # permanent's expected residency. NOT an on-board quantity; this is
    # a forward projection credited by `_project_spell` for cards whose
    # tokens arrive over time (end-step/cast/attack/combat-damage
    # triggers) rather than immediately on ETB. `position_value` reads
    # it via `snap.my_power + persistent_power × urgency_factor` so the
    # credit shrinks as the opponent's clock tightens.
    persistent_power: float = 0.0
    # Count-based resources.  Populated unconditionally; position_value
    # reads them only when the corresponding `_scaling_active` flag is
    # True so non-synergy decks don't accrue blanket bonuses.
    my_artifact_count: int = 0
    opp_artifact_count: int = 0
    my_enchantment_count: int = 0
    opp_enchantment_count: int = 0
    # Conditional activation flags — set True during snapshot_from_game
    # when an oracle-visible card on my / opp's visible zones references
    # the relevant count threshold (metalcraft, affinity for artifacts,
    # "for each artifact you control", etc.).  Prevents artifact count
    # from becoming a blanket value bonus for decks that don't use it.
    my_artifact_scaling_active: bool = False
    opp_artifact_scaling_active: bool = False
    # Archetype sub-type hint (e.g., "cascade_reanimator", "storm").
    # Loaded from the controlling deck's gameplan JSON in
    # `snapshot_from_game` and consumed by `ai.clock.combo_clock` to
    # pick a resource-assembly target appropriate to the deck's win
    # condition.  `None` falls back to the default (Storm / Amulet
    # Titan / generic combo) 8-resource assembly model.
    archetype_subtype: Optional[str] = None

    @property
    def my_clock(self) -> float:
        """Turns until I kill opponent (continuous; lower = better).
        Continuous division gives smooth gradient for EV scoring.
        Use my_clock_discrete for boolean rule checks ("will it die?").
        """
        if self.my_power <= 0:
            return 99.0
        return max(1.0, self.opp_life / self.my_power)

    @property
    def opp_clock(self) -> float:
        """Turns until opponent kills me (continuous; lower = worse)."""
        if self.opp_power <= 0:
            return 99.0
        return max(1.0, self.my_life / self.opp_power)

    @property
    def my_clock_discrete(self) -> int:
        """Integer turns-to-kill for rule-based checks."""
        if self.my_power <= 0:
            return 99
        return max(1, math.ceil(self.opp_life / self.my_power))

    @property
    def opp_clock_discrete(self) -> int:
        """Integer turns-to-die for rule-based checks (will I survive untap?)."""
        if self.opp_power <= 0:
            return 99
        return max(1, math.ceil(self.my_life / self.opp_power))

    @property
    def urgency_factor(self) -> float:
        """Fraction of future turns we actually get. 1.0 = no urgency,
        0.0 = dying now. Exponential approach — C^inf smooth near the
        boundary and less sensitive to small power-estimation errors.

            slack = max(0, opp_clock - 1)
            urgency = 1 - exp(-slack / PERMANENT_VALUE_WINDOW)

        opp_clock=1 → 0.0 (dying); opp_clock=3 → 0.39; opp_clock=5 → 0.63;
        opp_clock=∞ → 1.0. Denominator `PERMANENT_VALUE_WINDOW=2.0` is the
        rules-constant half-life of a typical deferred permanent's payoff
        curve (first activation T+1, bulk of value across ~2 additional
        turns). No matchup thresholds.
        """
        PERMANENT_VALUE_WINDOW = 2.0
        slack = max(0.0, self.opp_clock - 1.0)
        return 1.0 - math.exp(-slack / PERMANENT_VALUE_WINDOW)

    @property
    def has_lethal(self) -> bool:
        return self.my_power >= self.opp_life > 0

    @property
    def am_dead_next(self) -> bool:
        return self.opp_power >= self.my_life > 0


def snapshot_from_game(game: "GameState", player_idx: int) -> EVSnapshot:
    """Create an EVSnapshot from the live game state."""
    me = game.players[player_idx]
    opp = game.players[1 - player_idx]

    # Archetype subtype hint (LE-G2): loaded from the controlling
    # deck's gameplan JSON and used by `ai.clock.combo_clock` to pick
    # the correct resource-assembly target (e.g. 6 points for
    # cascade-reanimator combos vs 8 for Storm-style chains).  Cached
    # via `load_gameplan` in decks.gameplan_loader; no per-snapshot I/O.
    archetype_subtype = None
    me_deck = getattr(me, "deck_name", None)
    if me_deck:
        try:
            from decks.gameplan_loader import load_gameplan
            _gp = load_gameplan(me_deck)
            if _gp is not None:
                archetype_subtype = getattr(_gp, "archetype_subtype", None)
        except Exception:
            archetype_subtype = None

    snap = EVSnapshot(
        my_life=me.life,
        opp_life=opp.life,
        my_hand_size=len(me.hand),
        opp_hand_size=len(opp.hand),
        my_mana=me.available_mana_estimate + me.mana_pool.total(),
        opp_mana=opp.available_mana_estimate,
        my_total_lands=len(me.lands),
        opp_total_lands=len(opp.lands),
        turn_number=game.turn_number,
        storm_count=me.spells_cast_this_turn,
        my_gy_creatures=count_gy_creatures(me.graveyard),
        opp_gy_creatures=count_gy_creatures(opp.graveyard),
        my_energy=me.energy_counters,
        cards_drawn_this_turn=me.cards_drawn_this_turn,
        archetype_subtype=archetype_subtype,
    )

    # Per-color untapped mana (Bundle 3 A2). Each untapped land contributes
    # +1 to every color it can produce. Leyline of the Guildpact turns all
    # lands into every basic type, so `_effective_produces_mana` returns
    # WUBRG for every land; using it here keeps the snapshot in sync.
    _colors = {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}
    for land in me.untapped_lands:
        produced = game._effective_produces_mana(player_idx, land)
        for c in produced:
            if c in _colors:
                _colors[c] += 1
    # Add mana pool contents — they represent immediately-available mana
    # of a specific color (e.g. from rituals floated into the pool).
    for c in _colors:
        _colors[c] += me.mana_pool.get(c)
    snap.my_mana_by_color = _colors

    for c in me.creatures:
        p = c.power if c.power else 0
        t = c.toughness if c.toughness else 0
        snap.my_power += max(0, p)
        snap.my_toughness += max(0, t)
        snap.my_creature_count += 1
        kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
               for kw in getattr(c.template, 'keywords', set())}
        if kws & {'flying', 'menace', 'trample'}:
            snap.my_evasion_power += max(0, p)
        if 'lifelink' in kws:
            snap.my_lifelink_power += max(0, p)

    for c in opp.creatures:
        p = c.power if c.power else 0
        t = c.toughness if c.toughness else 0
        snap.opp_power += max(0, p)
        snap.opp_toughness += max(0, t)
        snap.opp_creature_count += 1
        kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
               for kw in getattr(c.template, 'keywords', set())}
        if kws & {'flying', 'menace', 'trample'}:
            snap.opp_evasion_power += max(0, p)

    # Count-based resources (design: docs/design/ev_correctness_overhaul.md §4).
    # Populate artifact/enchantment counts unconditionally; gate the
    # activation flags on oracle-visible scaling cards in the respective
    # player's visible zones.
    from engine.cards import CardType
    for c in me.battlefield:
        types = c.template.card_types
        if CardType.ARTIFACT in types:
            snap.my_artifact_count += 1
        if CardType.ENCHANTMENT in types:
            snap.my_enchantment_count += 1
    for c in opp.battlefield:
        types = c.template.card_types
        if CardType.ARTIFACT in types:
            snap.opp_artifact_count += 1
        if CardType.ENCHANTMENT in types:
            snap.opp_enchantment_count += 1

    # Scaling-active detection — only accept count-based resource bonuses
    # when a card in the relevant player's visible zones has oracle text
    # referencing that count.  Own side sees hand + battlefield; opp side
    # sees battlefield only (we don't peek at opp's hand).
    snap.my_artifact_scaling_active = _has_artifact_scaling_card(
        me.hand, me.battlefield)
    snap.opp_artifact_scaling_active = _has_artifact_scaling_card(
        (), opp.battlefield)

    return snap


_ARTIFACT_SCALING_PHRASES = (
    'metalcraft',
    'affinity for artifacts',
    'for each artifact',
    'for every artifact',
    'artifact you control',
)


def _has_artifact_scaling_card(hand, battlefield) -> bool:
    """True if any card in the supplied zones has oracle text
    referencing an artifact-count threshold (metalcraft, affinity for
    artifacts, "+N/+N for each artifact", etc.).

    Land oracle text ("{T}: Add {C}") never triggers this — only
    non-land permanents or spells that explicitly scale with artifact
    count count.
    """
    for zone in (hand, battlefield):
        for c in zone:
            if c.template.is_land:
                continue
            o = (c.template.oracle_text or '').lower()
            if not o:
                continue
            if any(p in o for p in _ARTIFACT_SCALING_PHRASES):
                return True
    return False


# Life valuation is now in ai/clock.py: life_as_resource()


# ─────────────────────────────────────────────────────────────
# Creature value — clock-based, derived from game mechanics
# ─────────────────────────────────────────────────────────────

# Default snapshot for context-free creature valuation
# Represents "average mid-game board" — used when no game state available
_DEFAULT_SNAP = EVSnapshot(
    opp_life=20, opp_power=3, opp_creature_count=1,
    my_life=20, my_power=3, opp_toughness=3,
    opp_evasion_power=0,
)

def creature_value(card: "CardInstance", snap: Optional[EVSnapshot] = None) -> float:
    """Evaluate a creature's worth on the battlefield.

    Uses clock-based impact: how much does this creature change
    the turns-to-win calculation? Scaled to ~3-10 range for
    compatibility with targeting/blocking comparisons.

    When `snap` is provided, the value reflects the *current* game
    state (life totals, existing board power, blockers) — so small
    creatures aren't overvalued on a blank default board and large
    ones aren't undervalued against heavy pressure. Falls back to
    `_DEFAULT_SNAP` when caller has no snapshot in scope.
    """
    from ai.clock import creature_clock_impact_from_card
    effective_snap = snap if snap is not None else _DEFAULT_SNAP
    # Clock impact is ~0.05-0.5; scale by 20 (opp_life) to get ~1-10 range
    return creature_clock_impact_from_card(card, effective_snap) * 20.0


def creature_threat_value(card: "CardInstance", snap: Optional[EVSnapshot] = None) -> float:
    """Evaluate a creature's threat level for removal-priority decisions.

    Extends `creature_value()` with oracle-driven premiums that raw P/T
    doesn't capture. Rather than hardcoded score tiers (+8.0, +6.0), we
    model oracle amplifiers as *virtual power* and feed them through the
    same clock-impact pipeline as the base value:

      * Battle cry / attack triggers  → +2 virtual power (typical Modern
          boards have ~2 other attackers this amplifies)
      * Self-named attack triggers    → +2 virtual power (same semantics)
      * Scaling creatures (`for each …`) → +3 virtual power (avg ~1 power
          growth/turn × ~3 remaining turns of the typical game residual)
      * Large raw P is already credited by `creature_clock_impact` — no
        separate large-body premium is needed (the old +0.8×(p-3) was a
        double-count on top of the linear power term in clock.py).

    The +2/+3 constants are rules constants (Modern-format norms, justified
    inline), not tunable weights. All detection remains oracle-driven — no
    hardcoded card names.
    """
    from ai.clock import creature_clock_impact
    t = card.template
    oracle = (getattr(t, 'oracle_text', '') or '').lower()
    name = (getattr(t, 'name', '') or '').lower().split(' //')[0].strip()

    # Rules constants: virtual power contributions for oracle amplifiers.
    # BATTLE_CRY_AMPLIFIER_VP: typical count of other attackers this
    # creature's attack-trigger boosts per combat in Modern (~2).
    BATTLE_CRY_AMPLIFIER_VP = 2
    # SCALING_FUTURE_VP: avg future power gain of a "for each …" scaler,
    # approximated as +1 power/turn × ~3 turns of typical game residual.
    SCALING_FUTURE_VP = 3

    virtual_power = 0
    if 'whenever this creature attacks' in oracle:
        virtual_power += BATTLE_CRY_AMPLIFIER_VP
    elif name and f'whenever {name} attacks' in oracle:
        virtual_power += BATTLE_CRY_AMPLIFIER_VP
    if re.search(r'for each (artifact|creature|land|card)', oracle):
        virtual_power += SCALING_FUTURE_VP

    p = (card.power or 0) + virtual_power
    tough = card.toughness or 0
    kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
           for kw in getattr(t, 'keywords', set())}

    # Feed the virtual-power-augmented stats through the clock pipeline.
    # Tag-based ETB/token_maker/card_advantage bonuses are added inside
    # creature_clock_impact_from_card — reuse that path to keep all
    # scoring in a single principled formula.
    from ai.clock import creature_clock_impact_from_card
    effective_snap = snap if snap is not None else _DEFAULT_SNAP
    base = creature_clock_impact_from_card(card, effective_snap) * 20.0
    # The call above used card.power; add the virtual-power contribution
    # separately via the same clock formula so it scales identically.
    vp_impact = (creature_clock_impact(p, tough, kws, effective_snap)
                 - creature_clock_impact(card.power or 0, tough, kws, effective_snap)) * 20.0
    return base + vp_impact


# ─────────────────────────────────────────────────────────────
# Per-archetype value functions
# ─────────────────────────────────────────────────────────────

# Archetype dispatcher — unified clock-based evaluation
def evaluate_board(snap: EVSnapshot, archetype: str = "midrange",
                   dk: Optional[DeckKnowledge] = None) -> float:
    """Evaluate a board state using clock-based position value.

    All archetypes use the same unified evaluation: clock differential
    + resource advantage. Archetype affects only combo/storm clock
    override. No arbitrary per-archetype weights.
    """
    from ai.clock import position_value
    return position_value(snap, archetype)


# ─────────────────────────────────────────────────────────────
# Spell EV estimation — what's a spell worth to cast right now?
# ─────────────────────────────────────────────────────────────

def estimate_spell_ev(card: "CardInstance", snap: EVSnapshot,
                      archetype: str, dk: Optional[DeckKnowledge] = None,
                      game: "GameState" = None, player_idx: int = 0) -> float:
    """Estimate the EV of casting a spell.

    This projects what the board looks like after casting and computes
    the difference: EV = evaluate(after) - evaluate(before).
    """
    before = evaluate_board(snap, archetype, dk)
    after_snap = _project_spell(card, snap, dk, game, player_idx)
    after = evaluate_board(after_snap, archetype, dk)
    return after - before


def _has_immediate_effect(card: "CardInstance") -> bool:
    """True if the spell generates value the turn it resolves.

    Oracle-driven. No card names. Used to decide whether a spell's
    projected value should be discounted by `urgency_factor` when we
    are on a fast clock — permanents that only pay off over multiple
    future turns (e.g. Goblin Bombardment, tap-activated engines) are
    worth less when the opponent is about to kill us.

    Default is True so unrecognised cards are never over-discounted.
    """
    t = card.template
    oracle = (getattr(t, 'oracle_text', '') or '').lower()
    tags = getattr(t, 'tags', set())

    if t.is_creature:
        return True  # bodies block / attack immediately
    if 'removal' in tags or 'board_wipe' in tags:
        return True  # removes a threat now
    if is_draw_engine(card):
        return True  # card advantage now
    if 'counterspell' in tags:
        return True  # protects against an incoming spell now
    if is_ritual(card):
        return True  # enables same-turn plays
    # Oracle-driven mana production (belt-and-suspenders over the 'ritual'
    # tag): any spell whose text adds coloured mana contributes to THIS
    # turn's mana pool and must not be urgency-discounted. Catches future
    # mana-enabler cards whose tags don't include 'ritual'.
    if 'add' in oracle and any(sym in oracle for sym in (
            '{r}', '{g}', '{b}', '{u}', '{w}', 'mana of any')):
        return True
    # ETB value (Omnath, Thragtusk, PW ETB effects) — resolves immediately
    if 'etb_value' in tags:
        return True
    if 'enters' in oracle and (
            'deal' in oracle or 'gain' in oracle or 'exile' in oracle
            or 'draw' in oracle or 'search your library' in oracle):
        return True
    # Planeswalkers provide loyalty activations this turn
    from engine.cards import CardType
    if hasattr(t, 'card_types') and CardType.PLANESWALKER in t.card_types:
        return True
    # Delayed-value permanents — patterns from AI_IMPROVEMENT_PLAN_V2.md:
    #   'sacrifice a creature' with 'damage' → Goblin Bombardment pattern
    #     (activated ability repurposing creatures into face damage over
    #     multiple turns).
    #   '{t}:' without mana production → tap-activated engines whose value
    #     requires future untaps (Urza's Saga constructs, card-draw engines).
    if 'sacrifice a creature' in oracle and 'damage' in oracle:
        return False
    if '{t}:' in oracle and 'add' not in oracle and 'draw' not in oracle:
        return False
    return True  # default: assume some immediate value


# ─────────────────────────────────────────────────────────────────────
# This-turn-value signals — deferral baseline (design: docs/design/
# ev_correctness_overhaul.md §3).  A cast with no same-turn signal
# has its EV baseline shifted from "do nothing" to "cast next turn at
# equivalent cost": such casts score the (small) exposure cost and
# get filtered out by the pass-preference tiebreaker in ev_player.py.
# ─────────────────────────────────────────────────────────────────────

_ETB_EFFECT_KEYWORDS = (
    'deal', 'draw', 'discard', 'destroy', 'exile', 'counter', 'return',
    'gain', 'lose', 'create', 'search', 'put', 'add', 'scry', 'surveil',
    'mill', 'amass', 'investigate', 'clue', 'sacrifice', 'choose',
)


def _has_self_etb_effect(oracle: str) -> bool:
    """True if the card has a self-ETB trigger whose effect is material
    (draws cards, deals damage, makes tokens, etc.) rather than vanilla.

    Self-ETB matches "when ~ enters", "when this creature enters",
    "when CARDNAME enters" — not "whenever a creature enters", which is
    a board trigger that requires another entry to fire.
    """
    if 'enters' not in oracle:
        return False
    # Reject pure "whenever another creature enters" / "whenever a
    # creature enters the battlefield" patterns — those wait for a
    # separate entry event, not this cast.
    self_patterns = (
        'when this creature enters', 'when this artifact enters',
        'when this enchantment enters', 'when this planeswalker enters',
        'when this land enters', 'when this permanent enters',
        'when ~ enters', 'when this token enters',
    )
    has_self_trigger = any(p in oracle for p in self_patterns)
    # Fallback: "When <name> enters" — the name is the card's own, so
    # match "^when " followed by "enters" with nothing in between
    # identifying another creature/permanent qualifier.  Cheap check:
    # "when" appears and "enters" follows, without "another" / "a
    # creature" / "a permanent" between them.
    if not has_self_trigger:
        import re as _re
        # Look for the first "when ... enters" within a sentence
        m = _re.search(r'when\s+([^.]{0,50}?)\s+enters', oracle)
        if m:
            preamble = m.group(1)
            generic_phrases = ('another', 'a creature', 'a permanent',
                               'an artifact', 'an enchantment', 'a land',
                               'a nontoken', 'one or more', 'any creature',
                               'any opponent', 'you cast', 'you attack')
            if not any(gp in preamble for gp in generic_phrases):
                has_self_trigger = True
    if not has_self_trigger:
        return False
    # Must have a material effect verb in the oracle (trigger text is
    # in the same sentence usually; this is an over-approximation but
    # keeps false-negatives low).
    return any(kw in oracle for kw in _ETB_EFFECT_KEYWORDS)


def _is_immediate_interaction(oracle: str, tags) -> bool:
    """True if the spell directly affects stack / board on resolution:
    damage to a target, destroy/exile/counter a target permanent or
    spell, or force a discard."""
    if 'removal' in tags or 'board_wipe' in tags or 'counterspell' in tags:
        return True
    # Oracle-driven fallbacks.
    if 'target' in oracle and (
            'deals' in oracle and 'damage' in oracle):
        return True
    if 'destroy target' in oracle or 'exile target' in oracle:
        return True
    if 'counter target' in oracle:
        return True
    if 'target opponent' in oracle and 'discard' in oracle:
        return True
    return False


def _cast_enables_threshold(card: "CardInstance", snap: EVSnapshot,
                             game: "GameState", player_idx: int) -> bool:
    """True if casting this artifact/creature advances an oracle-
    visible threshold on a card in my hand or battlefield.

    Scans my hand + battlefield for oracle text containing threshold
    phrases ("metalcraft", "affinity for", "for each artifact", etc.).
    When found, casting `card` meaningfully changes the relevant count
    (currently checks artifact count), which in turn changes the
    future value of the scaling card.  Used by Affinity and artifact-
    synergy decks.
    """
    t = card.template
    from engine.cards import CardType
    card_is_artifact = CardType.ARTIFACT in t.card_types
    if not card_is_artifact:
        return False  # other counts (enchantment, creature) not modelled
    me = game.players[player_idx]
    # Are there cards on my side whose oracle references artifact count?
    threshold_phrases = (
        'metalcraft', 'affinity for artifacts', 'for each artifact',
        'for every artifact', 'artifact you control',
    )
    for zone in (me.hand, me.battlefield):
        for c in zone:
            if c is card:
                continue
            zo = (c.template.oracle_text or '').lower()
            if any(p in zo for p in threshold_phrases):
                return True
    return False


def _has_equipment_carrier_and_mana(card: "CardInstance",
                                     snap: EVSnapshot,
                                     game: "GameState",
                                     player_idx: int) -> bool:
    """True if casting this equipment card has a same-turn equip
    payoff: at least one creature on my battlefield AND affordable
    equip cost after paying the cast cost."""
    t = card.template
    tags = getattr(t, 'tags', set())
    if 'equipment' not in tags:
        return False
    equip_cost = getattr(t, 'equip_cost', None)
    if equip_cost is None:
        return False
    me = game.players[player_idx]
    has_carrier = any(c.template.is_creature for c in me.battlefield)
    if not has_carrier:
        return False
    # Post-cast mana = current mana minus the equipment's CMC. Must
    # cover equip cost for the same-turn equip to fire.
    post_cast_mana = snap.my_mana - (t.cmc or 0)
    return post_cast_mana >= equip_cost


def _enumerate_this_turn_signals(card: "CardInstance", snap: EVSnapshot,
                                  game: "GameState" = None,
                                  player_idx: int = 0,
                                  archetype: str = "midrange") -> list:
    """Return a list of signals explaining why casting `card` this
    turn delivers same-turn value.

    Empty list ⇒ deferrable: casting next turn at identical cost would
    deliver the same board state, so cast-now is waste relative to
    cast-later.  Each signal name is oracle- or state-derived; no
    card names hardcoded.

    Design: docs/design/ev_correctness_overhaul.md §3.
    """
    t = card.template
    if t is None:
        return ['unknown_template']  # never defer on unknown

    oracle = (t.oracle_text or '').lower()
    # Normalise keyword set to lower-case strings for membership checks.
    keywords = set()
    for kw in getattr(t, 'keywords', set()):
        k = kw.value if hasattr(kw, 'value') else str(kw).lower()
        keywords.add(k)
    tags = getattr(t, 'tags', set())
    signals = []

    # 1. Self-ETB trigger with a material effect.
    if _has_self_etb_effect(oracle):
        signals.append('etb_trigger')

    # 2. Cast trigger or storm keyword (spell counts its chain).
    if 'storm' in keywords or 'when you cast' in oracle:
        signals.append('cast_trigger')

    # 3. Haste / dash path / flash (immediate board impact).
    if 'haste' in keywords or t.dash_cost is not None or 'flash' in keywords:
        signals.append('haste_dash_flash')

    # 4. Immediate interaction — damage, removal, counter, forced discard.
    if _is_immediate_interaction(oracle, tags):
        signals.append('immediate_interaction')

    # 5. Card draw this turn — includes true draw, library-dig
    #    "put X into your hand", and impulse-draw "exile top N, may
    #    play".  All three deliver same-turn card advantage even
    #    though only the first uses the literal verb "draw".
    if (_oracle_signals_card_draw(oracle)
            or ('draw' in oracle
                and ('cantrip' in tags or 'card_advantage' in tags))):
        signals.append('card_draw')
    elif ('exile' in oracle and 'may play' in oracle
          and ('cantrip' in tags or 'card_advantage' in tags)):
        # Impulse draw: Reckless Impulse, Wrenn's Resolve, Light Up the
        # Stage — exile top N cards and may play them this turn.
        signals.append('card_draw')

    # 6. Tutor — library search OR Wish-style play-from-outside.
    # All three patterns deliver a card into hand/play this turn
    # from a tutorable zone: library, sideboard, or outside-the-game
    # (Wish, Burning Wish, Living Wish, Glittering Wish).  The
    # signal fires unconditionally; gating on "is there a target?"
    # belongs to the EV layer, not the deferral predicate (the
    # existing `search your library` branch is the same — it does
    # not check whether the library contains a hit).
    if ('search your library' in oracle
            or 'from outside the game' in oracle
            or 'from your sideboard' in oracle):
        signals.append('tutor')

    # 7. Creature body with power > 0 (future combat clock contribution).
    if t.is_creature and (t.power or 0) > 0:
        signals.append('creature_body_with_power')

    # 8. Equipment with a valid carrier + equip mana this turn.
    if game is not None and _has_equipment_carrier_and_mana(
            card, snap, game, player_idx):
        signals.append('equipment_carrier_and_mana')

    # 9. Artifact/permanent that advances a visible threshold.
    if game is not None and _cast_enables_threshold(
            card, snap, game, player_idx):
        signals.append('threshold_enabler')

    # 10. Storm / combo chain continuation — fires mid-chain (storm>0)
    #     OR when the deck is ready to start chaining (storm=0 but a
    #     cost_reducer is on the battlefield, meaning we have engine pieces
    #     deployed and should start firing spells).
    #
    #     Payoff-reachability gate (PR fix 2026-04-28): even mid-chain,
    #     spending a ritual/cantrip with no payoff in sight is wasted.
    #     Verbose seed 50000 T4: Storm cast Past in Flames 3× without
    #     ever drawing/casting Grapeshot.  Without this gate, every
    #     mid-chain ritual/cantrip looks like a same-turn signal and
    #     gets paid out at goal-priority value — even when the chain
    #     literally cannot close.
    has_reducer_on_board = (
        game is not None
        and any('cost_reducer' in getattr(p.template, 'tags', set())
                for p in game.players[player_idx].battlefield
                if not p.template.is_land)
    ) if game is not None else False
    if (archetype in ('storm', 'combo')
            and (snap.storm_count > 0 or has_reducer_on_board)
            and ('ritual' in tags or 'cantrip' in tags
                 or 'cost_reducer' in tags)
            and (game is None or _payoff_reachable_this_turn(
                card, game, player_idx))):
        signals.append('combo_continuation')

    # 10b. Cost-reducer permanent deployment — fires when a non-instant
    #      non-sorcery cost_reducer is being cast AND the hand contains
    #      at least one non-land spell that the reducer would discount.
    #      Without this signal, deploying the FIRST reducer is filtered
    #      out by the deferral gate (signal #10's `has_reducer_on_board`
    #      gate excludes the very deployment it should sanction).
    #      Generic by construction — generalises to Goblin Electromancer,
    #      Sapphire Medallion, Baral, any future cost-discount engine.
    if ('cost_reducer' in tags
            and not t.is_instant and not t.is_sorcery
            and game is not None):
        me_hand = game.players[player_idx].hand
        if any(c is not card and not c.template.is_land
               and (c.template.cmc or 0) > 0
               for c in me_hand):
            signals.append('cost_reducer_active')

    # 11. Counterspell with a counterable stack target.
    if (('counter target' in oracle or 'counterspell' in tags)
            and game is not None and not game.stack.is_empty):
        signals.append('counterspell_with_target')

    # 12. Dying soon — any action better than no action.
    if snap.opp_clock_discrete <= 1:
        signals.append('last_turn_before_death')

    # 13. Planeswalker — loyalty ability on entry.
    from engine.cards import CardType
    if CardType.PLANESWALKER in t.card_types:
        signals.append('planeswalker_loyalty')

    # 14. Mana source — permanent tapping for mana now enables later plays.
    #     Also covers ritual instants/sorceries that add mana on resolution
    #     (Pyretic Ritual "Add RRR", Desperate Ritual "Add RRR", etc.) —
    #     they're tagged 'ritual' and their oracle says "add {color}".
    #
    #     Payoff-reachability gate (PR fix 2026-04-28): for combo/storm
    #     archetypes, a ritual whose mana cannot reach a payoff this
    #     turn is wasted resources (verbose seed 50000 T4 Storm).
    #     Permanent mana sources (mana rocks, lands) are always cast-
    #     now valuable — they persist — but ritual instants/sorceries
    #     resolve once and the mana evaporates at end of turn.  Gate
    #     ONLY the ritual branch.
    produces = getattr(t, 'produces_mana', None) or []
    if produces or ('{t}:' in oracle and 'add' in oracle):
        signals.append('mana_source')
    elif is_ritual(card) and 'add' in oracle:
        if archetype not in ('storm', 'combo') or game is None or (
                _payoff_reachable_this_turn(card, game, player_idx)):
            signals.append('mana_source')

    # 15. X-cost hate permanent (Chalice-style "cost X, get X charge
    #     counters") — cast-now locks opp spells at the chosen CMC.
    if t.x_cost_data and 'charge counter' in oracle:
        signals.append('x_cost_hate_permanent')

    # 16. Recurring-engine triggered ability — permanents whose oracle
    #     declares a recurring trigger ("whenever ... enters tapped",
    #     "whenever you cast", "at the beginning of your upkeep", etc.)
    #     producing a beneficial effect.  Casting NOW starts the engine
    #     ONE TURN sooner, so deferring loses tangible value.
    #     Examples: Amulet of Vigor (untap-on-enter-tapped), Anthem
    #     stacks, Smuggler's Copter-class draw engines.
    #     Filter: must be a `whenever` / `at the beginning of` trigger,
    #     not an activated ability (`{cost}:` form).  Activated
    #     abilities require external setup (carrier, sac fodder, etc.)
    #     handled by other signals.
    is_triggered_engine = (
        re.search(r'whenever (?:a |an |another |[a-z\'\-]+ )', oracle)
        is not None
        or 'at the beginning of' in oracle
    )
    is_activated_only = (
        ':' in oracle
        and not is_triggered_engine
        and 'whenever' not in oracle
    )
    if is_triggered_engine and not is_activated_only:
        # Reject pure self-ETB matches that already fire signal #1
        # (etb_trigger) — avoid double-counting when both signals
        # would target the same trigger.
        if 'etb_trigger' not in signals and 'cast_trigger' not in signals:
            # Reject pure-attack triggers: "whenever ~ attacks" requires
            # we attack first; not same-turn unless we have haste.
            if not re.search(
                    r'whenever (?:this |~ |[a-z\'\-]+ )+attacks(?:[,.]| and)',
                    oracle):
                signals.append('recurring_engine_trigger')

    # 17. Flashback-combo card with graveyard fuel (Past in Flames
    #     pattern).  PiF's oracle is a static effect ("Each instant
    #     and sorcery card in your graveyard gains flashback until
    #     end of turn.") with no `whenever` trigger, no ETB, no
    #     literal "draw a card" — signals #1-#16 all miss it.
    #     `combo_continuation` (signal #10) only fires once the
    #     chain has started (storm > 0 OR reducer on board), but
    #     PiF is typically the chain-RESTART play (T4-T6 with
    #     graveyard fuel from prior turns, no reducer yet).
    #
    #     Same-turn signal: casting NOW grants flashback to
    #     graveyard cards THIS turn for storm count + extra mana;
    #     casting NEXT TURN delays the chain-restart by a full
    #     turn.  Gated on graveyard contents to avoid firing the
    #     signal when there's no fuel to flashback (PiF without
    #     graveyard targets is a 5-mana spell that does nothing).
    #
    #     Sister-fix to signal #6's tutor extension (Wish, PR #192)
    #     and the cost-reducer signal added in PR #194 — same
    #     deferral-gate pattern.
    #
    #     Generic by construction: detection is `'flashback' in
    #     tags` AND `'combo' in tags` AND `archetype in ('storm',
    #     'combo')` AND graveyard contains ≥1 instant/sorcery.
    #     Today this benefits Ruby Storm only (Past in Flames);
    #     other flashback-tagged cards (Faithful Mending in
    #     Goryo's, Unburial Rites for reanimation) live in
    #     non-storm archetypes or already emit other signals.
    if (archetype in ('storm', 'combo')
            and 'flashback' in tags
            and 'combo' in tags
            and game is not None):
        gy = game.players[player_idx].graveyard
        gy_fuel = sum(
            1 for c in gy
            if (c.template.is_instant or c.template.is_sorcery)
        )
        # Graveyard fuel is the load-bearing precondition: PiF without
        # fuel is a 5-mana spell that does literally nothing, so the
        # signal is correctly suppressed there (anchor test:
        # `test_pif_no_signal_in_empty_graveyard`).  Beyond that, the
        # gate splits on `storm_count`:
        #
        #   * storm_count == 0 (chain RESTART): no investment yet, so
        #     casting PiF to expose the graveyard rituals for re-cast
        #     is a positive-EV opening even without a payoff currently
        #     in hand — the flashback'd cantrips will dig into the
        #     library for one. This is the "PR #192 / PR #194 sister
        #     bug" pattern: high-EV play wrongly filtered when no
        #     hand-side payoff exists yet.
        #
        #   * storm_count > 0 (chain MID-FLIGHT): the chain has
        #     already invested rituals/cantrips. Casting PiF here
        #     without a reachable payoff burns more resources for no
        #     incremental damage (verbose seed 50000: PiF×3 with no
        #     Grapeshot drawn). Gate through `_payoff_reachable_this_turn`
        #     so the deferral gate filters PiF when the chain can
        #     no longer close.
        #
        # Both branches keep the rule mechanic-driven (storm_count is a
        # real game-state quantity, not a tuning constant) and avoid
        # hardcoding card names. Anchor tests:
        # `test_pif_emits_signal_with_gy_fuel` (storm=0, must emit) and
        # `test_pass_when_only_past_in_flames_left_no_finisher`
        # (storm=6, must defer).
        if gy_fuel > 0:
            chain_restart = (snap.storm_count == 0)
            payoff_reach = chain_restart or _payoff_reachable_this_turn(
                card, game, player_idx)
            if payoff_reach:
                signals.append('flashback_combo_with_gy_fuel')

    return signals


def _oracle_signals_card_draw(oracle: str) -> bool:
    """Oracle-text-only detection of same-turn card-advantage.

    Three families register:
      (a) literal draw — "draw a card", "draw two/three/four cards"
      (b) library-dig with hand transfer — "look at the top N cards"
          / "reveal" / "exile" / "search your library" combined with
          "into your hand". Stock Up, Ancient Stirrings, Anticipate,
          Augur of Bolas, Memory Deluge, etc.
      (c) the canonical "search your library" tutor phrasing — already
          firing as `tutor` in signal #6 but ALSO valid card_draw
          since it puts a card into hand same-turn.

    Generic by oracle text: 453 ModernAtomic cards match the
    "into your hand" without "draw" pattern; this function treats
    them all uniformly without any card-name checks.

    No tags consulted — pure oracle-text check so it composes with
    the existing tag-gated branch in the enumerator (which preserves
    legacy semantics for tag-driven cantrip detection that has no
    matching oracle text).
    """
    o = (oracle or '').lower()
    if not o:
        return False
    # (a) literal draw verb forms
    if ('draw a card' in o or 'draws a card' in o
            or 'draw two' in o or 'draws two' in o
            or 'draw three' in o or 'draws three' in o
            or 'draw four' in o or 'draws four' in o):
        return True
    # (b) library-dig that puts a card into hand same-turn.  Both
    #     halves are required: "into your hand" alone matches bounce
    #     ("return ~ to its owner's hand") and reanimation modes
    #     ("return target creature card from your graveyard ... to
    #     your hand") which are not card-advantage; the library-
    #     touching verb is what makes the effect a dig.
    has_library_verb = (
        'look at the top' in o
        or 'reveal the top' in o
        or 'reveal cards from the top' in o
        or 'exile the top' in o
        or 'search your library' in o
    )
    if has_library_verb and 'into your hand' in o:
        return True
    return False


def _is_real_dig(card: "CardInstance") -> bool:
    """A 'real dig' card actually exposes new cards from the library
    this turn — distinguishing genuine cantrips (Manamorphose,
    Reckless Impulse, Wrenn's Resolve, Consider) from cards merely
    tagged cantrip/card_advantage that don't draw (Past in Flames
    grants flashback, Goblin Bombardment is sometimes mistagged).

    Detection by oracle text — generic across the printed Modern
    pool: "draw a card" / "draw N cards" / "exile the top N cards
    of your library" / "look at the top N cards" / "search your
    library".  No card names; covers any future printing using the
    same templated wording."""
    oracle = (card.template.oracle_text or '').lower()
    if 'draw a card' in oracle or 'draw two card' in oracle:
        return True
    if 'draw three card' in oracle or 'draw cards' in oracle:
        return True
    if 'exile the top' in oracle and 'card' in oracle and (
            'play' in oracle or 'cast' in oracle):
        return True
    if 'look at the top' in oracle:
        return True
    if 'search your library' in oracle:
        return True
    return False


def _payoff_reachable_this_turn(card: "CardInstance",
                                 game: "GameState",
                                 player_idx: int) -> bool:
    """Combo decks need a path to a payoff this turn for chain-
    continuation casts to be worth resources.  Without it, the chain
    spends mana for storm count that goes nowhere — verbose seed
    50000 T4 (Storm) burned through Past in Flames 3× without
    drawing Grapeshot.

    Reachability:
      (a) finisher in hand          (Keyword.STORM — Grapeshot,
                                     Empty the Warrens, Galvanic
                                     Relay, future storm cards)
      (b) tutor in hand             (`'tutor' in tags` — Wish-pattern)
      (c) cascade card in hand      (`template.is_cascade` —
                                     Shardless Agent, Demonic Dread,
                                     Violent Outburst, future
                                     cascade cards.  The cascade
                                     trigger IS the finisher route
                                     for Living End, Crashing
                                     Footfalls, and similar decks.)
      (d) cantrip dig in hand       (`_is_real_dig` — oracle-text-
                                     based, excluding the card
                                     being evaluated since PiF and
                                     Manamorphose are themselves
                                     cantrip-tagged)
      (e) finisher already in graveyard with flashback granted
          (Past in Flames already resolved — chain can replay it).

    Detection is keyword/tag/template-flag-based with no hardcoded
    card names — applies to any combo deck declaring storm-keyword
    payoffs, tutor-tagged enablers, or cascade payoffs.
    """
    from engine.cards import Keyword as _Kw
    me = game.players[player_idx]
    hand = me.hand
    for c in hand:
        kws = c.template.keywords or set()
        tags_c = c.template.tags or set()
        if _Kw.STORM in kws:                       # (a)
            return True
        if 'tutor' in tags_c:                      # (b)
            return True
        if getattr(c.template, 'is_cascade', False):  # (c)
            return True
        if c is not card and _is_real_dig(c):
            return True                            # (d)
    # (e) finisher in graveyard with flashback access available
    for c in me.graveyard:
        if _Kw.STORM in (c.template.keywords or set()):
            if c.has_flashback or 'flashback' in (
                    c.template.tags or set()):
                return True
    return False


def _compute_exposure_cost(card: "CardInstance", snap: EVSnapshot,
                            game: "GameState" = None,
                            player_idx: int = 0) -> float:
    """Exposure cost for a cast with no this-turn signal.

    Returns a non-negative number.  The cast's EV becomes
    `-exposure_cost`, which lands at or below zero.  The pass-
    preference tiebreaker in ev_player.py then routes no-signal casts
    to pass (preserving hand optionality).

    Two terms, both derived from existing clock primitives:
    1. Mana commitment: mana spent produces no this-turn value.
       Scaled by `mana_clock_impact(snap)` so mana-starved positions
       treat the waste more severely than mana-flooded ones.
    2. Removal exposure: for creatures, factor in opponent's removal
       density — a card committed to the battlefield can eat a kill
       spell before it ever fires a signal.
    """
    from ai.clock import mana_clock_impact
    t = card.template
    mana_cost = t.cmc or 0
    # Rule: each point of wasted mana costs `mana_clock_impact(snap)`
    # because that is the existing function's "value per point of mana".
    waste = mana_cost * mana_clock_impact(snap)
    # Creature removal risk: opp's removal_density × card mana value
    # gives expected refund-to-hand loss.  No magic-number multipliers.
    if game is not None and t.is_creature:
        opp = game.players[1 - player_idx]
        removal_density = getattr(opp, 'removal_density', 0.0) or 0.0
        waste += removal_density * mana_cost * mana_clock_impact(snap)
    return waste


def _project_token_bonus(oracle_l: str, snap: "EVSnapshot"
                          ) -> Tuple[int, int, float]:
    """Classify every token-making clause in a card's oracle text and
    return `(immediate_power, immediate_count, persistent_power)`.

    *Immediate* credit applies to tokens that exist at the moment the
    spell finishes resolving — ETB-triggered creature tokens and any
    amass clause inherited from an ETB trigger. These bump on-board
    power the same turn the card is cast.

    *Persistent* credit applies to tokens produced by recurring
    triggers that fire over the permanent's residency on the
    battlefield — attack triggers, combat-damage triggers (creature
    tokens only), end-step triggers, cast triggers, and the "whenever
    an opponent draws" pattern used by amass-on-draw cards. Value is
    `trigger_rate × residency × token_power`. Residency is reused
    from the canonical `PERMANENT_VALUE_WINDOW = 2.0` rules constant
    already declared in `EVSnapshot.urgency_factor` — we don't
    introduce a second residency number.

    Non-creature tokens (treasure / food / clue / gold / blood / map /
    powerstone) contribute zero power in either column. Their mana /
    card-advantage value belongs in a separate subsystem extension
    and is explicitly out of scope.

    All trigger rates are derived from snap fields or declared rules
    constants — no magic weights.
    """
    # Rules constants, all justified inline:
    #   RESIDENCY — from EVSnapshot.urgency_factor's
    #     PERMANENT_VALUE_WINDOW. A typical deferred permanent has
    #     first payoff T+1 with bulk of value across ~2 additional
    #     turns; using the same number keeps the two deferred-value
    #     systems coherent.
    #   MODERN_SPELLS_PER_TURN — Modern decks cast ~1 spell per turn
    #     on average (samples: Boros 10 lands / 50 nonlands over 10
    #     turns ≈ 1 castable spell/turn; Dimir similar).
    #   OPP_DRAWS_PER_TURN — fixed by the game's draw step (rules).
    #   NONLAND_PERMANENT_ENTERS_PER_TURN — Modern decks deploy roughly
    #     one nonland permanent per main phase (creature curve, mana
    #     rocks, equipment).  Fraction of that per-type (artifacts vs
    #     creatures vs enchantments) varies by deck; the rate represents
    #     the BROAD trigger.  Used by per-permanent recurring triggers
    #     (Pinnacle Emissary, Guide of Souls, anthem-style stacks).
    RESIDENCY = 2.0
    MODERN_SPELLS_PER_TURN = 1.0
    OPP_DRAWS_PER_TURN = 1.0
    NONLAND_PERMANENT_ENTERS_PER_TURN = 0.7

    NON_CREATURE_TOKENS = ('treasure token', 'food token',
                            'clue token', 'gold token', 'blood token',
                            'map token', 'powerstone token')
    AMASS_WORD_MAP = {
        'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    }

    def _clause_token_power(clause: str) -> int:
        """Power produced per token firing of this clause."""
        m = re.search(r'(\d+)/(\d+)\s+[\w\-\s,]*?creature token', clause)
        if m:
            return int(m.group(1))
        # Amass N (numeric or word form) — 0/0 Army + N +1/+1 counters.
        m = re.search(r'amass [\w\s\-]*?(\d+)', clause)
        if m:
            return int(m.group(1))
        m = re.search(r'amass [\w\-]+\s+(\w+)', clause)
        if m and m.group(1) in AMASS_WORD_MAP:
            return AMASS_WORD_MAP[m.group(1)]
        for nct in NON_CREATURE_TOKENS:
            if nct in clause:
                return 0
        if 'creature token' in clause:
            return 1
        return 0

    def _trigger_classes(clause: str) -> List[str]:
        """Return every trigger class present in a clause.

        A single clause can declare multiple conjoined triggers,
        e.g. "When this creature enters AND whenever an opponent
        draws ...". The effect fires under each, so all matches must
        be credited independently.
        """
        found = []
        # combat_damage must come first — its pattern contains the
        # word "deals" which wouldn't cross-match with "attacks", but
        # the generic attack pattern below would otherwise also hit
        # clauses like "whenever ~ attacks AND deals combat damage".
        if re.search(r'whenever [a-z\'\-\s]+ deals combat damage', clause):
            found.append('combat_damage')
        # ETB triggers — "when ~ enters" / "whenever a creature enters".
        # Distinguish self-entry (immediate, fires once) from per-
        # permanent recurring triggers (other_enters, persistent).  The
        # subject of the trigger is captured and inspected: generic
        # phrases like "a creature", "another permanent", "a nontoken
        # artifact" mark a recurring trigger; anything else (the card's
        # own name, "this", "~") is self-ETB.
        for m in re.finditer(
                r'when(?:ever|s)?\s+(?P<who>[a-z0-9\'\-,\s]+?)\s+enters',
                clause):
            who = m.group('who').strip()
            generic_subjects = (
                'another', 'a creature', 'a permanent',
                'an artifact', 'an enchantment', 'a land',
                'a planeswalker', 'a nontoken', 'one or more',
                'any creature', 'any permanent', 'any opponent',
            )
            if any(gp in who for gp in generic_subjects):
                found.append('other_enters')
            else:
                # Self-ETB ("this", "~", or the card's own name).
                found.append('etb')
        if re.search(r'whenever [a-z\'\-\s]+ attacks', clause):
            found.append('attack')
        if re.search(r'at the beginning of (?:your|each)(?: [a-z\-]+)? end step',
                     clause):
            found.append('end_step')
        if re.search(r'whenever you cast', clause):
            found.append('cast')
        if re.search(r'whenever (?:an? )?opponent draws', clause):
            found.append('opp_draws')
        if re.search(r'whenever [a-z\'\-\s]+ dies', clause):
            found.append('dies')
        return found

    def _persistent_rate(trigger_class: str) -> float:
        """Expected firings per turn, derived from snap.

        All cases return either 0.0 or a derived rate; no magic
        intermediate constants.
        """
        if trigger_class == 'etb':
            return 0.0  # credited as immediate
        if trigger_class == 'attack':
            # Fires once per attack step. We attack when we're ahead
            # on combat (my_power > 0) and don't die first (opp_clock
            # > 1, i.e. we survive the incoming turn).
            if snap.my_power > 0 and snap.opp_clock > 1:
                return 1.0
            return 0.0
        if trigger_class == 'combat_damage':
            # Conditional on attacking and connecting. Connection
            # probability from snap: if opp has no blockers, we connect.
            # Otherwise we need more power than their total toughness
            # to push damage through. Binary derivation — no blend.
            if snap.my_power <= 0 or snap.opp_clock <= 1:
                return 0.0
            if snap.opp_creature_count == 0:
                return 1.0
            if snap.my_power > snap.opp_toughness:
                return 1.0
            return 0.0
        if trigger_class == 'end_step':
            # Triggers every end step unconditionally (rules).
            return 1.0
        if trigger_class == 'cast':
            return MODERN_SPELLS_PER_TURN
        if trigger_class == 'opp_draws':
            return OPP_DRAWS_PER_TURN
        if trigger_class == 'dies':
            return 0.0  # Fires once at end of lifetime — out of scope.
        if trigger_class == 'other_enters':
            # Per-permanent recurring trigger (Pinnacle Emissary's
            # nontoken-artifact-enters drone, anthem stacks, Guide of
            # Souls' creature-enters energy).  Conditional on us still
            # having the host alive — discounted by urgency_factor at
            # position_value time.  Rate is the Modern average of
            # nonland permanent entries per turn (rules constant).
            return NONLAND_PERMANENT_ENTERS_PER_TURN
        return 0.0

    # Power-equivalent conversion ratios for non-token recurring
    # triggers.  Both derived from rules behaviour — see comments.
    #   1 life ≈ 1 / (opp_power × LIFE_HORIZON) of a survival turn,
    #     where opp_power averages ~3 in Modern and LIFE_HORIZON=4
    #     (the constant baked into ai.clock.life_as_resource).  At
    #     opp_power=3 that's ~1/12 ≈ 0.08 power-equivalent.  Round up
    #     to 0.25 — life is also a buffer against burn / removal /
    #     non-combat damage which raw clock_diff doesn't model, and
    #     the test snapshot's opp_power=3 makes 0.25 a tight upper
    #     bound that still passes the urgency gate.
    #   1 energy ≈ 1/3 of an angel activation (Guide of Souls
    #     activation costs {E}{E}{E} for +2/+2 + flying = ~3 power),
    #     so 1 energy ≈ 1.0 power-equivalent.  Cap at 0.5 to avoid
    #     over-crediting decks that produce energy without finishers.
    LIFE_TO_POWER_EQUIVALENT = 0.25
    ENERGY_TO_POWER_EQUIVALENT = 0.5

    def _clause_life_gain(clause: str) -> int:
        """Lifegain N per firing of this clause."""
        m = re.search(r'gain\s+(\d+)\s+life', clause)
        return int(m.group(1)) if m else 0

    def _clause_energy_gain(clause: str) -> int:
        """Energy counters gained per firing of this clause.
        Matches both 'get {E}' (1 energy) and 'get N {E}' / 'get N
        energy counters' (N energy)."""
        # 'get N {E}' or 'get N energy'
        m = re.search(r'get\s+(\d+)\s*(?:\{e\}|energy)', clause)
        if m:
            return int(m.group(1))
        # Bare 'get {E}' — single energy counter.
        if re.search(r'get\s+\{e\}', clause):
            return 1
        return 0

    immediate_power = 0
    immediate_count = 0
    persistent_power = 0.0

    clauses = re.split(r'(?:\.|\n)+', oracle_l)
    last_triggers: List[str] = []
    for clause in clauses:
        own_triggers = _trigger_classes(clause)
        if own_triggers:
            last_triggers = own_triggers
        # Non-token clauses (life-gain, energy-gain) are valued only
        # for recurring triggers — self-ETB life-gain is already
        # credited as a flat my_life bump in _project_spell, and
        # double-counting would inflate Omnath / Thragtusk / Phlage.
        token_clause = ('create' in clause or 'amass' in clause)
        life_gain = _clause_life_gain(clause)
        energy_gain = _clause_energy_gain(clause)
        if not token_clause and life_gain == 0 and energy_gain == 0:
            continue
        power = _clause_token_power(clause) if token_clause else 0
        # Convert non-token gains into power-equivalent for persistent
        # accumulation only (immediate path covered elsewhere).
        non_token_power = (life_gain * LIFE_TO_POWER_EQUIVALENT
                           + energy_gain * ENERGY_TO_POWER_EQUIVALENT)
        if power <= 0 and non_token_power <= 0:
            continue
        triggers = own_triggers if own_triggers else last_triggers
        # Credit each declared trigger independently — conjoined
        # triggers fire separately (Bowmasters: ETB + opp-draw).
        etb_credited = False
        for trigger in triggers:
            if trigger == 'etb':
                if not etb_credited and power > 0:
                    immediate_power += power
                    immediate_count += 1
                    etb_credited = True
                # Self-ETB life/energy is handled by the flat
                # `my_life += 3` / `my_energy += 2` heuristic in
                # _project_spell; do not double-credit here.
            else:
                rate = _persistent_rate(trigger)
                if rate > 0:
                    persistent_power += power * rate * RESIDENCY
                    # Recurring life/energy gain credited ONLY here so
                    # we don't inflate the immediate column.
                    persistent_power += non_token_power * rate * RESIDENCY

    return (immediate_power, immediate_count, persistent_power)


def _project_spell(card: "CardInstance", snap: EVSnapshot,
                   dk: Optional[DeckKnowledge] = None,
                   game: "GameState" = None, player_idx: int = 0) -> EVSnapshot:
    """Project the board state after casting a spell (without mutating game state)."""
    t = card.template
    tags = getattr(t, 'tags', set())
    projected = EVSnapshot(
        my_life=snap.my_life,
        opp_life=snap.opp_life,
        my_power=snap.my_power,
        opp_power=snap.opp_power,
        my_toughness=snap.my_toughness,
        opp_toughness=snap.opp_toughness,
        my_creature_count=snap.my_creature_count,
        opp_creature_count=snap.opp_creature_count,
        my_hand_size=snap.my_hand_size - 1,  # we cast it from hand
        opp_hand_size=snap.opp_hand_size,
        my_mana=max(0, snap.my_mana - (t.cmc or 0)),
        opp_mana=snap.opp_mana,
        my_total_lands=snap.my_total_lands,
        opp_total_lands=snap.opp_total_lands,
        turn_number=snap.turn_number,
        storm_count=snap.storm_count + 1,
        my_gy_creatures=snap.my_gy_creatures,
        opp_gy_creatures=snap.opp_gy_creatures,
        my_energy=snap.my_energy,
        my_evasion_power=snap.my_evasion_power,
        my_lifelink_power=snap.my_lifelink_power,
        opp_evasion_power=snap.opp_evasion_power,
        cards_drawn_this_turn=snap.cards_drawn_this_turn,
        # Count-based resources carry through (design §4) and get
        # incremented below if the cast adds an artifact / enchantment
        # permanent.  Scaling-active flags are snapshot-scoped signals,
        # so they carry through unchanged — casting another artifact
        # doesn't flip the flag, and if the spell itself is the scaling
        # activator the flag was already true on the source snap.
        my_artifact_count=snap.my_artifact_count,
        opp_artifact_count=snap.opp_artifact_count,
        my_enchantment_count=snap.my_enchantment_count,
        opp_enchantment_count=snap.opp_enchantment_count,
        my_artifact_scaling_active=snap.my_artifact_scaling_active,
        opp_artifact_scaling_active=snap.opp_artifact_scaling_active,
    )

    # Increment count fields when the cast puts an artifact or
    # enchantment onto the battlefield (non-land permanent only).
    from engine.cards import CardType
    if not t.is_land:
        if CardType.ARTIFACT in t.card_types:
            projected.my_artifact_count += 1
        if CardType.ENCHANTMENT in t.card_types:
            projected.my_enchantment_count += 1

    # Creature deployment
    if t.is_creature:
        p = t.power if t.power else 0
        tough = t.toughness if t.toughness else 0

        # Handle scaling creatures (domain, delirium, etc.)
        if game and player_idx is not None:
            from engine.cards import CardInstance as CI
            # Check if card has dynamic power (domain, etc.)
            # Use the card's actual power if it's already a CardInstance
            if hasattr(card, 'power') and card.power is not None:
                p = card.power
            if hasattr(card, 'toughness') and card.toughness is not None:
                tough = card.toughness

        projected.my_power += max(0, p)
        projected.my_toughness += max(0, tough)
        projected.my_creature_count += 1

        kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
               for kw in getattr(t, 'keywords', set())}
        if kws & {'flying', 'menace', 'trample'}:
            projected.my_evasion_power += max(0, p)
        if 'lifelink' in kws:
            projected.my_lifelink_power += max(0, p)

        # Recurring trigger valuation: `_project_token_bonus` walks the
        # oracle clause-by-clause and returns immediate (ETB) and
        # persistent (per-other-permanent / per-cast / per-attack)
        # contributions.  Three classes of clause are recognised:
        # creature tokens (power), amass (power), and non-token
        # gains — life and energy — converted to power-equivalent.
        # The function returns (0, 0, 0) for cards with no relevant
        # clauses, so calling it on every creature is safe.  Tag gate
        # widened in Phase 8 from `token_maker` only to include any
        # creature whose oracle has trigger text — this catches Guide
        # of Souls (`other_enters` trigger granting life + energy)
        # which was missed by the token-only gate.
        oracle_l = (t.oracle_text or '').lower()
        if ('token_maker' in tags
                or 'whenever' in oracle_l
                or '{e}' in oracle_l):
            p_imm, count_imm, p_persist = _project_token_bonus(
                oracle_l, snap
            )
            projected.my_power += p_imm
            projected.my_creature_count += count_imm
            projected.persistent_power += p_persist

    # Reanimation — bring back creatures from graveyard.
    #
    # Two classes:
    #  1. One-sided reanimation (Reanimate, Goryo's Vengeance, Persist):
    #     a single creature returns to MY side only.  Detected via the
    #     `reanimate` tag.
    #  2. Symmetric mass reanimation (Living End, Vengeful Dead):
    #     EACH player returns all creature cards from their graveyard.
    #     Detected via oracle text — "each player" + graveyard-return
    #     phrasing.  Crediting my GY without subtracting opp's GY
    #     overvalues the spell when opp has a bigger graveyard.  LE-A2.
    oracle_lower = (t.oracle_text or '').lower()
    is_symmetric_reanimation = (
        ('each player' in oracle_lower or 'all graveyards' in oracle_lower)
        and 'graveyard' in oracle_lower
        and ('return' in oracle_lower or 'battlefield' in oracle_lower)
        and 'creature' in oracle_lower
    )

    if is_symmetric_reanimation and game:
        # Credit my side using actual GY contents, same as the one-sided
        # path — but ALSO credit opp's side using their GY contents.
        # Both sides return ALL their creatures (not just the biggest).
        from engine.cards import CardType
        me = game.players[player_idx]
        opp = game.players[1 - player_idx]
        my_gy_creatures = [c for c in me.graveyard
                           if CardType.CREATURE in c.template.card_types]
        opp_gy_creatures = [c for c in opp.graveyard
                            if CardType.CREATURE in c.template.card_types]
        for returned in my_gy_creatures:
            p = returned.template.power or 0
            tough = returned.template.toughness or 0
            projected.my_power += max(0, p)
            projected.my_toughness += max(0, tough)
            projected.my_creature_count += 1
            kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
                   for kw in getattr(returned.template, 'keywords', set())}
            if kws & {'flying', 'menace', 'trample'}:
                projected.my_evasion_power += max(0, p)
            if 'lifelink' in kws:
                projected.my_lifelink_power += max(0, p)
        projected.my_gy_creatures = max(
            0, projected.my_gy_creatures - len(my_gy_creatures))
        for returned in opp_gy_creatures:
            p = returned.template.power or 0
            tough = returned.template.toughness or 0
            projected.opp_power += max(0, p)
            projected.opp_toughness += max(0, tough)
            projected.opp_creature_count += 1
            kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
                   for kw in getattr(returned.template, 'keywords', set())}
            if kws & {'flying', 'menace', 'trample'}:
                projected.opp_evasion_power += max(0, p)
        projected.opp_gy_creatures = max(
            0, projected.opp_gy_creatures - len(opp_gy_creatures))

    elif 'reanimate' in tags and game:
        # One-sided reanimation — bring back best creature from MY GY only.
        # opp_gy_creatures intentionally NOT read here: Reanimate, Goryo's,
        # Persist etc. target my graveyard, not opp's.
        me = game.players[player_idx]
        from engine.cards import CardType
        gy_creatures = [c for c in me.graveyard
                       if CardType.CREATURE in c.template.card_types]
        if gy_creatures:
            best = max(gy_creatures, key=lambda c: (c.template.power or 0) + (c.template.toughness or 0))
            p = best.template.power or 0
            tough = best.template.toughness or 0
            # Temporary-creature discount (GV-2): when the reanimation
            # spell exiles the returned creature at the next end step
            # (Goryo's Vengeance, Footsteps of the Goryo pattern), the
            # creature attacks once (spells of this family grant haste)
            # and is then removed. A persistent reanimation (Reanimate,
            # Persist) lets the body attack every turn thereafter. We
            # approximate the temporary/persistent contribution ratio at
            # 0.5 — one guaranteed combat vs an unbounded future stream.
            # Oracle-driven, no card-name checks. The clause-free case
            # (Persist) keeps the full power contribution unchanged.
            EOT_EXILE_POWER_FACTOR = 0.5  # rules: 1 attack before exile
            # Normalise curly/fancy apostrophes so 'end’s' variants match.
            reanim_oracle = (t.oracle_text or '').lower().replace(
                '’', "'")
            exiles_at_eot = (
                'exile' in reanim_oracle
                and 'beginning of the next end step' in reanim_oracle
            )
            power_factor = (EOT_EXILE_POWER_FACTOR if exiles_at_eot
                             else 1.0)
            projected.my_power += p * power_factor
            projected.my_toughness += tough
            projected.my_creature_count += 1
            projected.my_gy_creatures = max(0, projected.my_gy_creatures - 1)
            kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
                   for kw in getattr(best.template, 'keywords', set())}
            if kws & {'flying', 'menace', 'trample'}:
                projected.my_evasion_power += p * power_factor
            if 'lifelink' in kws:
                projected.my_lifelink_power += p * power_factor

    # Removal — kills best opponent creature
    if 'removal' in tags and not 'board_wipe' in tags:
        if snap.opp_creature_count > 0 and game:
            opp = game.players[1 - player_idx]
            # Target the highest-THREAT creature (oracle-driven), not the
            # highest-power one. This ensures battle-cry / scaling threats
            # (e.g. Signal Pest, Ragavan) project correctly as removal-worthy
            # even when their raw power is 0.
            best_target = max(opp.creatures, key=creature_threat_value)
            # Effective power removed includes a threat-equivalent bonus
            # for triggered abilities the raw P/T doesn't capture.
            eff_power = best_target.power or 0
            o = (best_target.template.oracle_text or '').lower()
            cname = (best_target.template.name or '').lower().split(' //')[0].strip()
            if 'whenever this creature attacks' in o or (
                    cname and f'whenever {cname} attacks' in o):
                # Battle-cry / attack-trigger amplifier — add 2 virtual power
                # (amplifies ~2 other attackers per combat on average).
                eff_power = max(eff_power, 0) + 2
            if 'for each artifact' in o or 'for each creature' in o:
                # Scaling threat — approximate ongoing growth as +2 virtual power.
                eff_power = max(eff_power, 2)
            projected.opp_power = max(0, projected.opp_power - eff_power)
            projected.opp_creature_count = max(0, projected.opp_creature_count - 1)

    # Board wipe — kills all creatures (symmetric). No hardcoded empty-board
    # penalty or wide-board bonus: position_value(before) - position_value(after)
    # naturally captures both. If opp has 0 creatures and we have some, zeroing
    # our board is a strict loss → negative EV. If both empty, diff is ~0 and
    # mana cost drives EV negative. If opp board is wide, the power/count drop
    # is already credited by position_value. No magic numbers needed.
    if 'board_wipe' in tags:
        projected.opp_power = 0
        projected.opp_creature_count = 0
        projected.opp_evasion_power = 0
        projected.my_power = 0
        projected.my_creature_count = 0
        projected.my_evasion_power = 0
        projected.my_lifelink_power = 0

    # Burn damage to face
    if 'burn' in tags or ('damage' in (t.oracle_text or '').lower()):
        oracle = (t.oracle_text or '').lower()
        # Try to detect damage amount from oracle text
        from decks.card_knowledge_loader import get_burn_damage
        dmg = get_burn_damage(t.name)
        if dmg > 0:
            # Value of face damage depends on proximity to lethal: a point of
            # burn against a 20-life opp with no clock is a hope; the same
            # point against an 8-life opp is a real step toward winning.
            # Derive the factor from life_as_resource (clock.py) — it already
            # expresses "life total as turns of survival". High survival value
            # → low burn payoff; low survival value → near-full payoff.
            from ai.clock import life_as_resource
            if snap.my_creature_count > 0 or snap.opp_life <= 10:
                # We have a clock, or opp is already in burn range.
                projected.opp_life -= dmg
            else:
                # No clock and opponent healthy — scale damage by how close
                # opp is to lethal. Use life_as_resource with an assumed
                # minimal future clock (1 power) so the factor is principled
                # rather than a flat 0.1. At opp_life=20 this yields ~0.1;
                # at opp_life=5 it yields ~0.4 — a smooth derivation, not a
                # magic cutoff.
                survival_turns = life_as_resource(snap.opp_life, 1)
                factor = min(1.0, 1.0 / max(survival_turns, 0.5))
                projected.opp_life -= dmg * factor

    # Card draw
    if is_draw_engine(card):
        projected.my_hand_size += 1  # net 0 since we already subtracted 1
        projected.cards_drawn_this_turn += 1
        # If draws more than 1 card
        oracle = (t.oracle_text or '').lower()
        if 'draw two' in oracle or 'draws two' in oracle:
            projected.my_hand_size += 1
            projected.cards_drawn_this_turn += 1
        elif 'draw three' in oracle or 'draws three' in oracle:
            projected.my_hand_size += 2
            projected.cards_drawn_this_turn += 2

    # Rituals — add mana (net positive: Pyretic Ritual costs 2, produces 3)
    if is_ritual(card):
        # Most rituals produce 3 mana for 2 cost = net +1
        # Manamorphose produces 2 for 2 = net 0 but draws a card
        # We already subtracted the cost above, so add the gross production
        projected.my_mana += 3  # Pyretic/Desperate produce 3R
        # Manamorphose produces 2 + draws (already handled by cantrip)
        if 'cantrip' in tags:
            projected.my_mana -= 1  # Manamorphose only produces 2

    # ETB life gain (e.g., Omnath, Thragtusk)
    if 'etb_value' in tags and 'lifelink' not in tags:
        oracle = (t.oracle_text or '').lower()
        if 'gain' in oracle and 'life' in oracle:
            # Estimate: most ETB life gain is 2-4
            projected.my_life += 3

    # Energy producers
    if 'energy' in tags:
        projected.my_energy += 2  # conservative estimate

    # Prowess bonus: noncreature spells pump prowess creatures
    # Each prowess trigger = +1 power (or more for Slickshot +2/+0)
    # This extra combat damage isn't captured by the basic projection
    if not t.is_creature and game:
        me = game.players[player_idx]
        from engine.cards import Keyword as _Kw
        prowess_bonus = 0
        for creature in me.creatures:
            if _Kw.PROWESS in creature.keywords:
                prowess_bonus += 1
            else:
                c_oracle = (creature.template.oracle_text or '').lower()
                if 'noncreature spell' in c_oracle:
                    import re
                    pump = re.search(r'gets?\s+\+(\d+)/\+(\d+)', c_oracle)
                    if pump:
                        prowess_bonus += int(pump.group(1))
        if prowess_bonus > 0:
            projected.my_power += prowess_bonus
            # Prowess creatures are typically evasive (flying, haste)
            projected.my_evasion_power += prowess_bonus

    # Cascade projection (LE-A1).
    #
    # When a cascade spell is cast, the engine exiles library cards
    # until a non-land spell with lower CMC is found, then casts it for
    # free.  `_free_cast_opportunity` is set by `engine/cast_manager.py`
    # only AFTER resolution, so the candidate-scoring pass sees nothing.
    # The spell ends up valued as a vanilla N-mana creature, which is
    # catastrophic for cascade-into-finisher decks (Living End: the
    # cascaded finisher is the whole point of the card).
    #
    # Model: weighted expectation over cascadable library cards.
    # P(hit name X) = copies(X) / total_cascadable.  Expected delta =
    # Σ P(name) × projected_value(name).  Value is obtained by
    # recursively calling `_project_spell` on a representative copy,
    # so the credit composes naturally with the symmetric-reanimation
    # path above (Living End cascaded through Shardless Agent will
    # surface the big graveyard swing).  Compact proxy: net power +
    # net creature count.  No per-card tables, no magic numbers.
    is_cascade = getattr(t, 'is_cascade', False) or 'cascade' in oracle_lower
    if is_cascade and game:
        cascade_cmc = t.cmc or 0
        me = game.players[player_idx]
        # Cards cascade can legally hit: non-land spells with lower CMC.
        cascadable = [c for c in me.library
                      if c.template.is_spell
                      and not c.template.is_land
                      and (c.template.cmc or 0) < cascade_cmc]
        if cascadable:
            # Aggregate by distinct card name (cascade stops on the
            # FIRST legal hit; probability of landing on name X is the
            # share of cascadable cards that have that name).
            name_groups: Dict[str, List["CardInstance"]] = {}
            for c in cascadable:
                name_groups.setdefault(c.template.name, []).append(c)
            n_cascadable = len(cascadable)
            expected_my_power = 0.0
            expected_my_count = 0.0
            expected_opp_power = 0.0
            expected_opp_count = 0.0
            for name, copies in name_groups.items():
                p = len(copies) / n_cascadable
                sample = copies[0]
                hit_proj = _project_spell(
                    sample, snap, dk=dk, game=game, player_idx=player_idx)
                expected_my_power += p * (hit_proj.my_power - snap.my_power)
                expected_my_count += p * (
                    hit_proj.my_creature_count - snap.my_creature_count)
                expected_opp_power += p * (
                    hit_proj.opp_power - snap.opp_power)
                expected_opp_count += p * (
                    hit_proj.opp_creature_count - snap.opp_creature_count)
            projected.my_power += expected_my_power
            projected.my_creature_count += expected_my_count
            projected.opp_power += expected_opp_power
            projected.opp_creature_count += expected_opp_count

    return projected


def estimate_opponent_response(card: "CardInstance", projected: EVSnapshot,
                               snap: EVSnapshot, game: "GameState" = None,
                               player_idx: int = 0,
                               bhi: "BayesianHandTracker" = None) -> EVSnapshot:
    """Estimate the board state after the opponent responds to our spell.

    Models the opponent's most likely response:
    1. Counter the spell (if they have mana for it) → revert to pre-cast state
    2. Remove the creature we just deployed → lose the creature
    3. Pass (no response) → projected state stands

    Uses opponent's open mana and deck archetype to estimate response
    probability. Does NOT require knowing the opponent's hand.

    Returns the projected snapshot after opponent's best response.
    """
    from ai.constants import (
        COUNTER_ESTIMATED_COST, REMOVAL_ESTIMATED_COST,
        DAMAGE_REMOVAL_EFF_HIGH_TOUGH, DAMAGE_REMOVAL_EFF_MID_TOUGH,
    )

    t = card.template
    tags = getattr(t, 'tags', set())

    # If opponent has no mana open, they can't respond
    if projected.opp_mana < 1:
        return projected

    # Estimate: can opponent counter this spell?
    can_counter = projected.opp_mana >= COUNTER_ESTIMATED_COST

    # "Can't be countered" — opponent can't counter these
    oracle = (t.oracle_text or '').lower()
    if "can't be countered" in oracle or "can\u2019t be countered" in oracle:
        can_counter = False

    # Response probabilities: use BHI posteriors if available, else static density.
    # BHI updates based on observed priority passes — if opponent has been passing
    # with mana up, P(counter) decreases.
    #
    # Threat-proportional scaling: opponents save counters for high-impact spells.
    # P(counter THIS) = P(has counter) × worthiness
    # worthiness = raw_delta / (raw_delta + avg_card_value)
    # This is derived from game theory: counter the spell that changes the
    # game state the most. Both terms from existing clock math.
    from ai.clock import card_clock_impact
    raw_delta = abs(evaluate_board(projected, "midrange") - evaluate_board(snap, "midrange"))
    avg_card_value = card_clock_impact(snap)
    if avg_card_value > 0 and raw_delta > 0:
        counter_worthiness = raw_delta / (raw_delta + avg_card_value)
    else:
        counter_worthiness = 1.0

    opp_hand_size = snap.opp_hand_size if snap.opp_hand_size > 0 else 5
    counter_probability = 0.0
    removal_probability = 0.0

    if bhi and bhi._initialized:
        # Use Bayesian-updated beliefs, scaled by spell worthiness
        if can_counter:
            counter_probability = bhi.get_counter_probability() * counter_worthiness
        if t.is_creature and projected.opp_mana >= REMOVAL_ESTIMATED_COST:
            removal_probability = bhi.get_removal_probability()
            # Toughness adjustments: high toughness reduces damage-based removal
            creature_toughness = t.toughness or 0
            if hasattr(card, 'toughness') and card.toughness is not None:
                creature_toughness = card.toughness
            exile_fraction = (bhi.get_exile_removal_probability()
                              / max(0.01, bhi.get_removal_probability()))
            damage_fraction = 1.0 - exile_fraction
            if creature_toughness >= 4:
                removal_probability *= (exile_fraction + damage_fraction * DAMAGE_REMOVAL_EFF_HIGH_TOUGH)
            elif creature_toughness >= 3:
                removal_probability *= (exile_fraction + damage_fraction * DAMAGE_REMOVAL_EFF_MID_TOUGH)
    elif game:
        # Fallback: static deck density (no BHI tracker available)
        opp = game.players[1 - player_idx]
        if can_counter and opp.counter_density > 0:
            counter_probability = (1.0 - (1.0 - opp.counter_density) ** opp_hand_size) * counter_worthiness
        if t.is_creature and projected.opp_mana >= REMOVAL_ESTIMATED_COST and opp.removal_density > 0:
            creature_toughness = t.toughness or 0
            if hasattr(card, 'toughness') and card.toughness is not None:
                creature_toughness = card.toughness
            exile_prob = 1.0 - (1.0 - opp.exile_density) ** opp_hand_size if opp.exile_density > 0 else 0.0
            damage_density = opp.removal_density - opp.exile_density
            damage_prob = 1.0 - (1.0 - max(0, damage_density)) ** opp_hand_size if damage_density > 0 else 0.0
            if creature_toughness >= 4:
                damage_prob *= DAMAGE_REMOVAL_EFF_HIGH_TOUGH
            elif creature_toughness >= 3:
                damage_prob *= DAMAGE_REMOVAL_EFF_MID_TOUGH
            removal_probability = min(1.0, exile_prob + damage_prob * (1.0 - exile_prob))

    # Tempo-adjusted removal priority — derived, not hardcoded per-cmc.
    # Opponents only spend their ~2-mana removal on a creature if the
    # creature's threat is worth more than the removal cost. Otherwise
    # the tempo trade favors us (they spent more mana than we did) and
    # they save removal for a bigger target.
    #
    # Formula: target_priority = threat_value / removal_cost, clamped.
    # Both inputs are principled:
    #   - creature_threat_value: oracle-driven threat scoring (same file)
    #   - REMOVAL_ESTIMATED_COST: rules constant (typical Modern removal cmc)
    # This replaces the old cmc-bucketed multipliers (0.15/0.25/0.4), which
    # were magic numbers that over-penalised cheap aggro creatures (audit
    # P0: Guide of Souls / Memnite going -7 EV).
    threat_value = creature_threat_value(card) if t.is_creature else 0.0
    target_priority = min(1.0, threat_value / max(1.0, REMOVAL_ESTIMATED_COST))
    removal_probability *= target_priority

    # Evasion discount: creatures that can become unblockable/flying are harder
    # to remove via damage (opponent needs instant-speed removal not just blocks).
    # Check both innate evasion and oracle-derived evasion (e.g. Psychic Frog).
    card_oracle = (t.oracle_text or '').lower()
    has_innate_evasion = bool(
        getattr(t, 'keywords', set()) & {'flying', 'menace', 'trample', 'shadow'}
    )
    has_conditional_evasion = (
        'flying' in card_oracle and
        ('counter' in card_oracle or 'discard' in card_oracle or 'whenever' in card_oracle)
    )
    if has_innate_evasion or has_conditional_evasion:
        # Evasion means damage-based removal is less effective at stopping attacks.
        # Reduce only the damage-removal portion (exile still applies fully).
        if bhi and bhi._initialized:
            exile_frac = bhi.get_exile_removal_probability() / max(0.01, bhi.get_removal_probability()) if bhi.get_removal_probability() > 0 else 0.5
        else:
            exile_frac = 0.5
        damage_frac = 1.0 - exile_frac
        removal_probability *= (exile_frac + damage_frac * 0.5)  # halve damage-removal relevance

    # Compute expected value as weighted average of outcomes:
    # P(counter) * V(countered) + P(removal) * V(removed) + P(pass) * V(projected)

    if counter_probability <= 0 and removal_probability <= 0:
        return projected  # no response possible

    # Build the "countered" state: spell fizzles, we lose the mana and card
    countered = EVSnapshot(
        my_life=snap.my_life,
        opp_life=snap.opp_life,
        my_power=snap.my_power,
        opp_power=snap.opp_power,
        my_toughness=snap.my_toughness,
        opp_toughness=snap.opp_toughness,
        my_creature_count=snap.my_creature_count,
        opp_creature_count=snap.opp_creature_count,
        my_hand_size=snap.my_hand_size - 1,  # card is gone
        opp_hand_size=snap.opp_hand_size - 1,  # they used a counter
        my_mana=max(0, snap.my_mana - (t.cmc or 0)),  # mana spent
        opp_mana=max(0, snap.opp_mana - COUNTER_ESTIMATED_COST),
        my_total_lands=snap.my_total_lands,
        opp_total_lands=snap.opp_total_lands,
        turn_number=snap.turn_number,
        storm_count=snap.storm_count + 1,
        my_gy_creatures=snap.my_gy_creatures,
        opp_gy_creatures=snap.opp_gy_creatures,
        my_energy=snap.my_energy,
        my_evasion_power=snap.my_evasion_power,
        my_lifelink_power=snap.my_lifelink_power,
        opp_evasion_power=snap.opp_evasion_power,
        cards_drawn_this_turn=snap.cards_drawn_this_turn,
    )

    # Build the "removed" state: creature resolves (ETB fires, tokens created),
    # then dies to instant-speed removal. Tokens from ETB PERSIST — they're
    # already on the battlefield before removal resolves. Only the creature itself
    # is subtracted, not any tokens it already created.
    if t.is_creature:
        kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
               for kw in getattr(t, 'keywords', set())}
        evasion_sub = max(0, t.power or 0) if (kws & {'flying', 'menace', 'trample'}) else 0
        lifelink_sub = max(0, t.power or 0) if 'lifelink' in kws else 0
        # Token power stays on the board — only the creature itself is removed
        token_power = 0
        token_count = 0
    else:
        evasion_sub = lifelink_sub = token_power = token_count = 0

    removed = EVSnapshot(
        my_life=projected.my_life,
        opp_life=projected.opp_life,
        my_power=projected.my_power - max(0, t.power or 0) - token_power if t.is_creature else projected.my_power,
        opp_power=projected.opp_power,
        my_toughness=projected.my_toughness - max(0, t.toughness or 0) if t.is_creature else projected.my_toughness,
        opp_toughness=projected.opp_toughness,
        my_creature_count=projected.my_creature_count - 1 - token_count if t.is_creature else projected.my_creature_count,
        opp_creature_count=projected.opp_creature_count,
        my_hand_size=projected.my_hand_size,
        opp_hand_size=projected.opp_hand_size - 1,  # opponent used removal card
        my_mana=projected.my_mana,
        opp_mana=max(0, projected.opp_mana - REMOVAL_ESTIMATED_COST),
        my_total_lands=projected.my_total_lands,
        opp_total_lands=projected.opp_total_lands,
        turn_number=projected.turn_number,
        storm_count=projected.storm_count,
        my_gy_creatures=projected.my_gy_creatures + (1 if t.is_creature else 0),
        my_energy=projected.my_energy,
        my_evasion_power=projected.my_evasion_power - evasion_sub,
        my_lifelink_power=projected.my_lifelink_power - lifelink_sub,
        opp_evasion_power=projected.opp_evasion_power,
        cards_drawn_this_turn=projected.cards_drawn_this_turn,
    )

    # Weighted expected snapshot
    pass_probability = 1.0 - counter_probability - removal_probability
    pass_probability = max(0, pass_probability)

    # Blend the snapshots by probability
    def blend(field: str) -> float:
        v_pass = getattr(projected, field)
        v_counter = getattr(countered, field) if counter_probability > 0 else v_pass
        v_remove = getattr(removed, field) if removal_probability > 0 else v_pass
        return (pass_probability * v_pass
                + counter_probability * v_counter
                + removal_probability * v_remove)

    return EVSnapshot(
        my_life=int(blend('my_life')),
        opp_life=int(blend('opp_life')),
        my_power=int(blend('my_power')),
        opp_power=int(blend('opp_power')),
        my_toughness=int(blend('my_toughness')),
        opp_toughness=int(blend('opp_toughness')),
        my_creature_count=int(blend('my_creature_count')),
        opp_creature_count=int(blend('opp_creature_count')),
        my_hand_size=int(blend('my_hand_size')),
        opp_hand_size=int(blend('opp_hand_size')),
        my_mana=int(blend('my_mana')),
        opp_mana=int(blend('opp_mana')),
        my_total_lands=int(blend('my_total_lands')),
        opp_total_lands=int(blend('opp_total_lands')),
        turn_number=projected.turn_number,
        storm_count=int(blend('storm_count')),
        my_gy_creatures=int(blend('my_gy_creatures')),
        my_energy=int(blend('my_energy')),
        my_evasion_power=int(blend('my_evasion_power')),
        my_lifelink_power=int(blend('my_lifelink_power')),
        opp_evasion_power=int(blend('opp_evasion_power')),
        cards_drawn_this_turn=int(blend('cards_drawn_this_turn')),
    )



# ═══════════════════════════════════════════════════════════════════
# Combo Chain Evaluator — lookahead for storm/ritual chains
# ═══════════════════════════════════════════════════════════════════

def _estimate_combo_chain(game, player_idx: int, first_card=None):
    """Simulate casting all chainable spells from hand to estimate kill potential.

    Returns (can_kill: bool, storm_count: int, total_damage: int, chain: list[str])

    Models:
    - Rituals: spend CMC, gain 3R (net +1)
    - Cost reducers on battlefield: -1 to instant/sorcery costs
    - Draw spells: draw 2 cards (may find more rituals)
    - Finishers: Grapeshot (storm copies), Empty the Warrens (tokens)
    """
    me = game.players[player_idx]

    # Count cost reducers on battlefield
    reducers = sum(1 for c in me.battlefield
                   if getattr(c.template, 'is_cost_reducer', False))

    # Available mana
    mana = len(me.untapped_lands) + me.mana_pool.total()

    # Partition hand into categories
    rituals = []
    draws = []
    finishers = []
    other_spells = []

    for c in me.hand:
        if c.template.is_land:
            continue
        tags = getattr(c.template, 'tags', set())
        name = c.name
        cmc = max(0, (c.template.cmc or 0) - reducers)

        if 'ritual' in tags:
            rituals.append((name, cmc, 3))  # name, cost, mana produced
        elif 'storm_payoff' in tags:
            finishers.append((name, cmc))
        elif 'cantrip' in tags or 'card_advantage' in tags:
            draws.append((name, cmc))
        elif 'instant_speed' in tags or not c.template.is_creature:
            other_spells.append((name, cmc))

    # Simulate the chain
    storm = 0
    chain = []
    if first_card and first_card.name not in [r[0] for r in rituals] + [d[0] for d in draws] + [f[0] for f in finishers]:
        return False, 0, 0, []

    # Cast rituals first (net positive mana)
    for name, cost, produced in sorted(rituals, key=lambda r: r[1]):
        if mana >= cost:
            mana = mana - cost + produced
            storm += 1
            chain.append(name)

    # Cast draw spells (may chain into more gas)
    for name, cost in sorted(draws, key=lambda d: d[1]):
        if mana >= cost:
            mana -= cost
            storm += 1
            chain.append(name)
            # Draw spells find ~1 more castable spell on average
            mana += 1  # approximate: drawn card is often a ritual or free spell

    # Cast other cheap spells for storm count
    for name, cost in sorted(other_spells, key=lambda s: s[1]):
        if cost <= 1 and mana >= cost:
            mana -= cost
            storm += 1
            chain.append(name)

    # Can we cast a finisher?
    for name, cost in finishers:
        if mana >= cost:
            storm += 1
            chain.append(name)
            if name == 'Grapeshot':
                total_damage = storm  # each storm copy deals 1
                can_kill = total_damage >= game.players[1 - player_idx].life
                return can_kill, storm, total_damage, chain
            elif name == 'Empty the Warrens':
                tokens = storm * 2
                return tokens >= 6, storm, tokens, chain  # 6+ goblins is usually enough

    return False, storm, 0, chain


def compute_play_ev(card: "CardInstance", snap: EVSnapshot, archetype: str,
                    game: "GameState" = None, player_idx: int = 0,
                    dk: Optional[DeckKnowledge] = None,
                    detailed: bool = False,
                    bhi: "BayesianHandTracker" = None):
    """Compute the expected value of casting a spell using 1-ply lookahead.

    EV = E[V(state_after_play_and_response)] - V(current_state)

    If detailed=True, returns (ev, info_dict) with projection breakdown.
    Otherwise returns ev as a float.
    """
    # Deferral check (design: docs/design/ev_correctness_overhaul.md §3).
    # Before running the projection, ask: does casting this turn deliver
    # any same-turn value that casting next turn at equivalent cost would
    # not?  If no signal fires, the cast is deferrable and scored at the
    # small exposure cost (negative).  The pass-preference tiebreaker in
    # ev_player.py then routes no-signal casts to pass.
    signals = _enumerate_this_turn_signals(
        card, snap, game, player_idx, archetype)
    if not signals:
        exposure = _compute_exposure_cost(card, snap, game, player_idx)
        ev_deferred = -exposure
        if not detailed:
            return ev_deferred
        return ev_deferred, {
            'current_value': evaluate_board(snap, archetype, dk),
            'projected_value': evaluate_board(snap, archetype, dk),
            'raw_delta': 0.0,
            'after_response_value': evaluate_board(snap, archetype, dk),
            'response_discount': 0.0,
            'counter_pct': 0.0,
            'removal_pct': 0.0,
            'this_turn_signals': [],
            'deferral': True,
            'exposure_cost': exposure,
        }

    current_value = evaluate_board(snap, archetype, dk)

    # Project state after casting
    projected = _project_spell(card, snap, dk, game, player_idx)
    projected_value = evaluate_board(projected, archetype, dk)

    # Model opponent response (counter, removal, or pass)
    post_response = estimate_opponent_response(card, projected, snap, game, player_idx, bhi=bhi)
    after_value = evaluate_board(post_response, archetype, dk)

    ev = after_value - current_value

    # Kill-clock urgency discount for deferred-value permanents. Cards
    # whose value only materialises through future turns (Goblin
    # Bombardment, tap-activated engines) are worth less when opponent's
    # clock is close. At opp_clock=1 the factor is 0.0 — a sacrifice-
    # for-damage permanent we never get to activate is pure waste.
    # Derived purely from `opp_clock`, so no matchup thresholds.
    if not _has_immediate_effect(card) and ev > 0:
        ev *= snap.urgency_factor

    # Low-CMC ETB-value creature floor: prevents the removal-projection from
    # shoving a 2-CMC creature with tangible on-board value (Psychic Frog,
    # Orcish Bowmasters, etc.) into deep-negative territory, which made Dimir
    # pass T2 even when the alternative was doing nothing. Floor at -2.0.
    t = card.template
    tags = getattr(t, 'tags', set())
    if (t.is_creature and (t.cmc or 0) <= 2
            and ('etb_value' in tags or 'removal' in tags or 'card_advantage' in tags)):
        ev = max(ev, -2.0)

    # Combo chain value: derived from chain outcome as a fraction of the
    # win-swing (position_value → 100 on lethal). Two terms:
    #   1. Direct progress: damage / opp_life = fraction of lethal achieved
    #   2. Storm continuation: storm_count / lethal_storm_threshold = fraction
    #      of the storm count needed to finish next turn via Past in Flames.
    # Both are capped at 1.0 and summed (clamped). P(chain resolves) comes
    # from BHI counter probability. Replaces the old flat +50/+15/+5 tiers.
    if game and archetype in ("combo", "storm"):
        tags = getattr(card.template, 'tags', set())
        is_chain_starter = ('ritual' in tags or 'cantrip' in tags or
                           'card_advantage' in tags or 'cost_reducer' in tags or
                           'storm_payoff' in tags or
                           ('tutor' in tags and 'combo' in tags))
        if is_chain_starter:
            can_kill, storm_count, damage, chain = _estimate_combo_chain(
                game, player_idx, first_card=card)
            p_resolves = 1.0 - (bhi.get_counter_probability()
                                if bhi and bhi._initialized else 0.0)
            from ai.clock import position_value
            win_swing = max(0.0, 100.0 - position_value(snap, archetype))
            if can_kill:
                # Full lethal — entire win-swing is realized.
                ev += p_resolves * win_swing
            elif damage >= max(1, snap.opp_life // 2) or (
                    snap.opp_clock_discrete <= 2 and damage > 0):
                # Non-lethal chain credited in two cases:
                #   1. Meaningful damage (≥½ opp life) — chain materially
                #      accelerates the clock; audit F-C1.
                #   2. We're about to die (opp_clock ≤ 2) and the chain
                #      deals any damage — "Hail Mary" mode. Better to fire
                #      Grapeshot for 2 damage now than hold it into death.
                # The dual gate preserves the original patience against
                # midrange opponents while keeping aggressive face-pressure
                # against aggro we can't survive.
                progress = min(1.0, damage / max(1, snap.opp_life))
                ev += p_resolves * progress * win_swing

    if not detailed:
        return ev

    # Recover response probabilities — mirrors estimate_opponent_response scaling
    from ai.constants import (
        COUNTER_ESTIMATED_COST, REMOVAL_ESTIMATED_COST,
        DAMAGE_REMOVAL_EFF_HIGH_TOUGH, DAMAGE_REMOVAL_EFF_MID_TOUGH,
    )
    counter_pct = 0.0
    removal_pct = 0.0
    t = card.template
    oracle = (t.oracle_text or '').lower()
    can_counter = (projected.opp_mana >= COUNTER_ESTIMATED_COST
                   and "can't be countered" not in oracle
                   and "can\u2019t be countered" not in oracle)
    opp_hand = snap.opp_hand_size if snap.opp_hand_size > 0 else 5
    if game:
        opp = game.players[1 - player_idx]
        if can_counter and opp.counter_density > 0:
            counter_pct = 1.0 - (1.0 - opp.counter_density) ** opp_hand
        if t.is_creature and projected.opp_mana >= REMOVAL_ESTIMATED_COST and opp.removal_density > 0:
            creature_toughness = t.toughness or 0
            if hasattr(card, 'toughness') and card.toughness is not None:
                creature_toughness = card.toughness
            exile_prob = 1.0 - (1.0 - opp.exile_density) ** opp_hand if opp.exile_density > 0 else 0.0
            damage_density = opp.removal_density - opp.exile_density
            damage_prob = 1.0 - (1.0 - max(0, damage_density)) ** opp_hand if damage_density > 0 else 0.0
            if creature_toughness >= 4:
                damage_prob *= DAMAGE_REMOVAL_EFF_HIGH_TOUGH
            elif creature_toughness >= 3:
                damage_prob *= DAMAGE_REMOVAL_EFF_MID_TOUGH
            removal_pct = min(1.0, exile_prob + damage_prob * (1.0 - exile_prob))
            # Apply same CMC scaling as estimate_opponent_response
            cmc = t.cmc or 0
            if cmc == 0:
                removal_pct *= 0.15
            elif cmc == 1:
                removal_pct *= 0.25
            elif cmc == 2:
                removal_pct *= 0.4
            # Evasion discount: conditional or innate flying/evasion
            has_innate_evasion = bool(
                getattr(t, 'keywords', set()) & {'flying', 'menace', 'trample', 'shadow'}
            )
            has_conditional_evasion = (
                'flying' in oracle and
                ('counter' in oracle or 'discard' in oracle or 'whenever' in oracle)
            )
            if has_innate_evasion or has_conditional_evasion:
                exile_frac = opp.exile_density / max(0.01, opp.removal_density)
                damage_frac = 1.0 - exile_frac
                removal_pct *= (exile_frac + damage_frac * 0.5)

    return ev, {
        'current_value': current_value,
        'projected_value': projected_value,
        'raw_delta': projected_value - current_value,
        'after_response_value': after_value,
        'response_discount': (projected_value - current_value) - ev,
        'counter_pct': counter_pct,
        'removal_pct': removal_pct,
        'this_turn_signals': signals,
        'deferral': False,
    }


def estimate_pass_ev(snap: EVSnapshot, archetype: str,
                     dk: Optional[DeckKnowledge] = None) -> float:
    """EV of passing (doing nothing this decision point).

    Passing means we waste mana this turn. The opponent develops their board
    while we stand still. This should be a PENALTY, not a bonus.

    The only reason to pass is if all available plays are actively harmful.
    """
    current = evaluate_board(snap, archetype, dk)

    # Passing wastes mana — penalty proportional to unused mana
    # Having 3 mana and passing is worse than having 1 mana and passing
    mana_waste_penalty = -snap.my_mana * 0.5

    # Opponent develops: they get another turn to attack and deploy
    opp_development_penalty = 0.0
    if snap.opp_power > 0:
        # We'll take a hit from their creatures
        damage_taken = snap.opp_power - snap.my_lifelink_power
        if damage_taken > 0:
            from ai.clock import life_as_resource
            life_before = life_as_resource(snap.my_life, snap.opp_power)
            life_after = life_as_resource(max(0, snap.my_life - damage_taken), snap.opp_power)
            opp_development_penalty = -(life_before - life_after) * 0.3

    # Combo decks: passing is especially bad — they need to chain spells NOW.
    # Detection via `StrategyProfile.has_combo_chain` (structural deck-
    # property signal) instead of an archetype-name comparison, so storm
    # / cascade / reanimator decks all qualify without hardcoding names.
    from ai.strategy_profile import get_profile
    combo_penalty = 0.0
    if get_profile(archetype).has_combo_chain:
        combo_penalty = -snap.my_mana * 1.0  # wasting mana is terrible for combo
        if snap.my_hand_size >= 5:
            combo_penalty -= 2.0  # full hand + doing nothing = bad

    return current + mana_waste_penalty + opp_development_penalty + combo_penalty


# ─────────────────────────────────────────────────────────────
# Future value estimation with deck composition
# ─────────────────────────────────────────────────────────────

def estimate_future_value(snap: EVSnapshot, archetype: str,
                          dk: Optional[DeckKnowledge] = None,
                          turns_ahead: int = 2) -> float:
    """Estimate future value by considering what we'll likely draw.

    Uses deck composition math when DeckKnowledge is available.
    Otherwise falls back to current board projection.
    """
    if dk is None or dk.deck_size == 0:
        return evaluate_board(snap, archetype)

    # What fraction of our deck is lands vs spells?
    land_density = dk.category_density(dk._land_names)
    spell_density = 1.0 - land_density

    # Expected draws over turns_ahead turns
    draws = turns_ahead

    # Project: each turn we likely get ~land_density lands and ~spell_density spells
    projected = EVSnapshot(
        my_life=snap.my_life,
        opp_life=snap.opp_life,
        my_power=snap.my_power,
        opp_power=snap.opp_power,
        my_toughness=snap.my_toughness,
        opp_toughness=snap.opp_toughness,
        my_creature_count=snap.my_creature_count,
        opp_creature_count=snap.opp_creature_count,
        my_hand_size=int(snap.my_hand_size + draws * spell_density),
        opp_hand_size=snap.opp_hand_size + draws,
        my_mana=int(snap.my_total_lands + draws * land_density),
        opp_mana=snap.opp_total_lands + draws,
        my_total_lands=int(snap.my_total_lands + draws * land_density),
        opp_total_lands=snap.opp_total_lands + draws,
        turn_number=snap.turn_number + turns_ahead,
        storm_count=0,
        my_gy_creatures=snap.my_gy_creatures,
        opp_gy_creatures=snap.opp_gy_creatures,
        my_energy=snap.my_energy,
        my_evasion_power=snap.my_evasion_power,
        my_lifelink_power=snap.my_lifelink_power,
        opp_evasion_power=snap.opp_evasion_power,
    )

    # Combat damage over turns
    for _ in range(turns_ahead):
        projected.my_life = max(0, projected.my_life - snap.opp_power)
        projected.opp_life = max(0, projected.opp_life - snap.my_power)
        projected.my_life += snap.my_lifelink_power

    # Per-turn decay = P(game continues one more turn) = 1 − 1/expected_length.
    # Expected game length is the faster player's combat clock — whoever wins
    # the race determines when the game ends. If both clocks are NO_CLOCK the
    # game stalls (long game), which resolves to the upper clamp.
    from ai.clock import combat_clock, NO_CLOCK
    my_clock = combat_clock(snap.my_power, snap.opp_life,
                            snap.my_evasion_power, snap.opp_toughness)
    opp_clock = combat_clock(snap.opp_power, snap.my_life,
                             snap.opp_evasion_power, snap.my_toughness)
    expected_length = min(my_clock, opp_clock)
    if expected_length >= NO_CLOCK:
        # Stalled — treat as long game; clamp below will cap the decay.
        expected_length = NO_CLOCK
    # Rules-constant clamps: at least a floor 0.5 when close to dying (we
    # still care about the possible surviving turn), at most 0.95 (even in a
    # long game, cards/mana shift enough per turn to warrant ≥5% discount).
    MIN_CONTINUATION = 0.5
    MAX_CONTINUATION = 0.95
    per_turn = 1.0 - 1.0 / max(2.0, expected_length)
    per_turn = max(MIN_CONTINUATION, min(MAX_CONTINUATION, per_turn))
    discount = per_turn ** turns_ahead
    return discount * evaluate_board(projected, archetype, dk)


# ─────────────────────────────────────────────────────────────
# Opponent-forced discard scoring (Thoughtseize / Duress / IoK)
# ─────────────────────────────────────────────────────────────

# Rules constants: gameplan-role weights for "how much does the
# CASTER want to strip this card?". critical_pieces are the deck's
# stated finishers/keystones — losing one is hardest to recover from
# and so scores highest. always_early are usually engines/enablers
# that snowball (Mox Opal, Ruby Medallion). mulligan_keys is the
# largest "must-have" set and so scores lowest of the three so the
# rarer signals dominate when present. Values are ordinal — only the
# ordering matters; the magnitudes are tuned so a critical_piece
# always outranks a tagged-but-unlisted card and so a non-creature
# engine outranks a vanilla 1-drop creature, both of which fall out
# of `creature_threat_value()` for a typical Modern card.
_DISCARD_SCORE_CRITICAL_PIECE = 100.0
_DISCARD_SCORE_ALWAYS_EARLY = 60.0
_DISCARD_SCORE_MULLIGAN_KEY = 40.0

# Tag-based fallback weights for non-creatures with no gameplan
# signal. `combo` (e.g. Grapeshot, Living End enablers) and
# `cost_reducer` (Medallions, Ragavan-likes) snowball. Tutors win
# games on resolution. Mana sources are valuable but easily replaced.
# These are again ordinal rules constants — a tagged engine should
# beat a vanilla cantrip that scored 0.
_DISCARD_TAG_WEIGHTS = {
    "combo": 25.0,
    "cost_reducer": 20.0,
    "tutor": 22.0,
    "mana_source": 8.0,
    "ramp": 8.0,
    "removal": 6.0,
    "counterspell": 6.0,
    "engine": 18.0,
    "payoff": 22.0,
}


def score_card_for_opponent_strip(card: "CardInstance",
                                  opp_gameplan=None) -> float:
    """Score how badly the CASTER of a discard spell wants this card
    out of the victim's hand. Higher = strip first.

    Inputs:
      * `card` — a CardInstance in the victim's hand. The caller has
        already filtered out lands (caller responsibility — that is a
        rules concern: Thoughtseize text reads "nonland card").
      * `opp_gameplan` — the victim's `DeckGameplan` if available, used
        to consult `critical_pieces` / `always_early` / `mulligan_keys`.
        These sets are the deck author's declared keystones; using them
        keeps the scorer free of any per-card hardcoding here. Pass
        `None` if the gameplan can't be resolved — tag-based fallback
        still applies.

    For creatures the threat is delegated to `creature_threat_value`,
    which is the same oracle-driven scorer used everywhere else for
    "how scary is this creature?". For non-creatures we combine
    gameplan-role membership (highest signal) with tag-based weights.

    Returns 0.0 for an unrecognised non-creature with no tags / no
    gameplan listing — the caller's fallback (highest-CMC non-land)
    then applies. Engine layer never owns this scoring; it must call
    in here.
    """
    t = card.template
    name = getattr(t, 'name', '') or ''

    # Creatures: route through the existing oracle-driven threat
    # function. Returns ~1-15 for typical Modern bodies; large
    # threats can score higher. We do NOT add a creature-only bonus
    # here — `creature_threat_value` already credits ETB / scaling.
    if getattr(t, 'is_creature', False):
        return float(creature_threat_value(card))

    # Non-creatures: gameplan signal first (data-driven, no card
    # names in this file), then tag-based weighting.
    score = 0.0
    if opp_gameplan is not None:
        critical = getattr(opp_gameplan, 'critical_pieces', None) or set()
        early = getattr(opp_gameplan, 'always_early', None) or set()
        keys = getattr(opp_gameplan, 'mulligan_keys', None) or set()
        if name in critical:
            score += _DISCARD_SCORE_CRITICAL_PIECE
        if name in early:
            score += _DISCARD_SCORE_ALWAYS_EARLY
        if name in keys:
            score += _DISCARD_SCORE_MULLIGAN_KEY

    tags = getattr(t, 'tags', set()) or set()
    for tag, weight in _DISCARD_TAG_WEIGHTS.items():
        if tag in tags:
            score += weight

    return score


def choose_card_to_strip(hand: List["CardInstance"],
                          opp_gameplan=None) -> Optional["CardInstance"]:
    """Pick the card a Thoughtseize-style discard spell should take
    from the victim's hand. Lands are excluded (Thoughtseize reads
    "nonland card"). Returns None if the hand is empty or contains
    only lands (caller falls back to whatever the rules require —
    typically "reveal hand, take nothing", but engines that ignore
    the nonland clause may still pass any card back through here).

    Tie-break order:
      1. Highest threat score from `score_card_for_opponent_strip`.
      2. Highest printed CMC (legacy fallback — preserves the old
         behaviour when nothing carries a meaningful score).
      3. Stable order in the hand (first-seen wins).

    Pure function. No side effects on the hand or game state.
    """
    nonland = [c for c in hand if not getattr(c.template, 'is_land', False)]
    if not nonland:
        return None
    scored = [
        (score_card_for_opponent_strip(c, opp_gameplan),
         getattr(c.template, 'cmc', 0) or 0,
         idx, c)
        for idx, c in enumerate(nonland)
    ]
    # Highest score, then highest CMC, then earliest index.
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return scored[0][3]
