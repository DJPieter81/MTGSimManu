"""
ManaPlanner — unified hand-aware mana planning for all land decisions.

Every land decision in the game (play from hand, fetch target, shock payment,
tapped/untapped) should flow through this module so the AI consistently
sequences its mana to maximize castable spells.

Design principles:
  1. Look at the hand and determine what colors/CMCs are needed in the next 1-2 turns
  2. Look at the battlefield to see what colors/mana are already available
  3. Score each candidate land by how much it closes the gap between "have" and "need"
  4. Shock payment is a tempo-vs-life tradeoff: aggressive early, conservative late
  5. Combo/ramp decks MUST shock aggressively on T1-T3 to hit critical mana thresholds
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from ai.scoring_constants import (
    HELD_COLOR_PRESERVATION_BONUS,
    LAND_SCORE_PER_MISSING_COLOR_DEMAND,
    LAND_SCORE_PAYOFF_MISSING_COLOR_BONUS,
    LAND_SCORE_REDUNDANT_COLOR_WEIGHT,
    LAND_SCORE_ENABLED_SPELL_URGENCY,
    LAND_SCORE_URGENCY_CMC_CEIL,
    LAND_SCORE_TAPPED_PENALTY_EARLY,
    LAND_SCORE_TAPPED_PENALTY_LATE,
    LAND_SCORE_DOMAIN_BASE,
    LAND_SCORE_DOMAIN_PER_CARD_BONUS,
    LAND_SCORE_DOMAIN_CARD_CAP,
    LAND_SCORE_TAPPED_BASE_PENALTY,
    LAND_SCORE_TAPPED_TURN_DECAY,
    LAND_SCORE_TAPPED_FLOOR_FRACTION,
    LAND_SCORE_UNTAPPED_BONUS,
    LAND_SCORE_FETCHLAND_FLEXIBILITY_BONUS,
    LAND_SCORE_VERSATILITY_PER_COLOR,
    LAND_SCORE_BEST_INIT_SENTINEL,
    LAND_SCORE_FETCH_LIFE_TEMPO_PENALTY,
    LAND_SHOCK_RACING_LIFE_THRESHOLD,
    MANA_NEEDS_NO_SPELL_SENTINEL,
    PAYOFF_HIGH_CMC_THRESHOLD,
)

if TYPE_CHECKING:
    from engine.game_state import GameState

# ── Color mapping ──
COLOR_MAP = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}
ALL_COLORS = {"W", "U", "B", "R", "G"}
BASIC_LAND_TYPES = {"Plains", "Island", "Swamp", "Mountain", "Forest"}

# NOTE: Shock decisions use analyze_mana_needs() directly in game_state.py
# which derives the decision from board state (life, mana needs, spell enablement).


@dataclass
class ManaNeeds:
    """Snapshot of what the hand needs and what the battlefield provides."""
    # Colors required by spells in hand (with counts for weighting)
    needed_colors: Dict[str, int] = field(default_factory=dict)
    # Colors already available on untapped lands
    existing_colors: Set[str] = field(default_factory=set)
    # Colors we need but don't yet have
    missing_colors: Set[str] = field(default_factory=set)
    # Basic land types already on the battlefield (for domain)
    existing_subtypes: Set[str] = field(default_factory=set)
    # The cheapest spell CMC in hand (for tempo priority)
    cheapest_spell_cmc: int = MANA_NEEDS_NO_SPELL_SENTINEL
    # Number of untapped lands on battlefield
    untapped_land_count: int = 0
    # Total available mana (untapped lands + mana pool)
    total_mana: int = 0
    # Spells that could be cast with exactly 1 more mana of the right color
    spells_enabled_by_one_more: List = field(default_factory=list)
    # Cheapest PROACTIVE spell CMC (creatures, sorceries, planeswalkers — not instants)
    cheapest_proactive_cmc: int = MANA_NEEDS_NO_SPELL_SENTINEL
    # Number of domain-scaling cards in hand (affects land subtype priority)
    domain_card_count: int = 0
    # Colors needed by high-CMC multi-color payoffs (e.g., Omnath WURG)
    # These get extra weight in fetch/shock decisions
    payoff_missing_colors: Set[str] = field(default_factory=set)
    # Colors of EVERY non-land spell currently in the player's hand
    # (Bundle 3 A5, locked-hand extension).  Fetch targeting and tap-
    # order decisions must prefer sources that PRESERVE these colors
    # so the held interaction remains castable.
    #
    # Originally restricted to instants / flash permanents (the only
    # cards castable on the opponent's turn), but the same principle
    # applies to held SORCERIES, creatures and planeswalkers — every
    # spell whose colored pips aren't yet covered by an untapped
    # source represents future castability at risk.  The redundant-
    # preservation guard in `score_land` (held_unmet =
    # held_color_pips - existing_colors) keeps the bonus from firing
    # for already-preserved colors, so widening the collection adds
    # signal without overweighting decks that hold a single instant.
    #
    # Set stays empty when no spells are held with colored pips — the
    # preference never biases a deck that has nothing to hold (e.g.,
    # Eldrazi Tron with only colorless / generic-cost spells).
    #
    # NOTE: the field name is preserved for backward compatibility
    # with the engine `tap_lands_for_mana` interface and the existing
    # held-instant preservation tests; the broader semantics described
    # above apply.
    held_instant_colors: Set[str] = field(default_factory=set)


def analyze_mana_needs(game: "GameState", player_idx: int,
                       effective_cmc_overrides: Optional[Dict[str, int]] = None) -> ManaNeeds:
    """Analyze the player's hand and battlefield to determine mana needs.
    
    This is the core analysis that all land decisions should use.
    effective_cmc_overrides: maps card name -> effective CMC (e.g. Scion of Draco -> 3 with domain)
    """
    player = game.players[player_idx]
    needs = ManaNeeds()

    # ── Held-spell colors (Bundle 3 A5, locked-hand extension) ──
    # Populated from EVERY non-land spell in hand: for each colored
    # pip in its mana cost, that color is "held" — we need to keep
    # the ability to produce it.  The original Bundle-3 logic only
    # tracked instants / flash permanents (opponent-turn castability);
    # the locked-hand seed-60100 diagnostic showed the same principle
    # applies to held sorceries / creatures / planeswalkers — anything
    # whose colored pips aren't covered yet should bias the fetch.
    # Oracle-driven (template.is_spell on every non-land card); no
    # hardcoded card names.  The redundant-preservation guard in
    # `score_land` ensures the bonus only fires for held colors NOT
    # already in `existing_colors`, so widening the collection adds
    # signal without overweighting already-preserved colors.
    for card in player.hand:
        tmpl = card.template
        if tmpl.is_land:
            continue
        if not tmpl.is_spell:
            continue
        mc = tmpl.mana_cost
        for code, attr in COLOR_MAP.items():
            if getattr(mc, attr, 0) > 0:
                needs.held_instant_colors.add(code)

    # ── What colors does the hand need? ──
    from engine.cards import DOMAIN_POWER_CREATURES
    # Domain cards detected from template.domain_reduction (oracle-derived)
    # and 'domain' tag for domain-scaled effects
    for card in player.hand:
        if card.template.is_land:
            continue
        mc = card.template.mana_cost
        for code, attr in COLOR_MAP.items():
            count = getattr(mc, attr, 0)
            if count > 0:
                needs.needed_colors[code] = needs.needed_colors.get(code, 0) + count
        raw_cmc = card.template.cmc
        # Apply effective CMC overrides for domain cards (Scion of Draco, Leyline Binding)
        cmc = raw_cmc
        if effective_cmc_overrides and card.name in effective_cmc_overrides:
            cmc = effective_cmc_overrides[card.name]
        if cmc is not None and cmc < needs.cheapest_spell_cmc and card.template.is_spell:
            needs.cheapest_spell_cmc = cmc
        # Count domain-scaling cards in hand
        if (card.name in DOMAIN_POWER_CREATURES or
            getattr(card.template, 'domain_reduction', 0) > 0 or
            'domain' in getattr(card.template, 'tags', set())):
            needs.domain_card_count += 1
    # Also count domain creatures already on battlefield
    for bf_card in player.battlefield:
        if (bf_card.name in DOMAIN_POWER_CREATURES or
            getattr(bf_card.template, 'domain_reduction', 0) > 0):
            needs.domain_card_count += 1

    # ── Cycling cost awareness: cards with cycling need specific colors ──
    for card in player.hand:
        cycle_cost = card.template.cycling_cost_data
        if cycle_cost:
            for color in cycle_cost.get("colors", set()):
                # Add cycling color needs (weighted lower than casting needs)
                needs.needed_colors[color] = needs.needed_colors.get(color, 0) + 1
            # Cycling is often cheaper than casting — update cheapest spell CMC
            cycle_mana = cycle_cost.get("mana", 0)
            if cycle_mana > 0 and cycle_mana < needs.cheapest_spell_cmc:
                needs.cheapest_spell_cmc = cycle_mana

    # ── What colors does the battlefield already provide? ──
    all_land_colors = set()  # colors from ALL lands (tapped or untapped)
    for bf_card in player.battlefield:
        if bf_card.template.is_land:
            for c in bf_card.template.produces_mana:
                all_land_colors.add(c)
            if not bf_card.tapped:
                for c in bf_card.template.produces_mana:
                    needs.existing_colors.add(c)
                needs.untapped_land_count += 1
            # Track subtypes for domain regardless of tapped state
            for st in getattr(bf_card.template, "subtypes", []):
                if st in BASIC_LAND_TYPES:
                    needs.existing_subtypes.add(st)

    needs.total_mana = needs.untapped_land_count + player.mana_pool.total()

    # ── What colors are missing? ──
    # Use ALL land colors (not just untapped) so tapped lands aren't treated as missing.
    # This is critical for fetch decisions: a tapped Steam Vents still provides U/R next turn.
    needs.missing_colors = set(needs.needed_colors.keys()) - all_land_colors

    # ── Track colors needed by high-CMC multi-color payoffs ──
    # These colors get extra priority in fetch/shock decisions (e.g., Omnath WURG)
    # all_land_colors already computed above (includes tapped lands)
    for card in player.hand:
        if card.template.is_land:
            continue
        cmc = card.template.cmc or 0
        if cmc >= PAYOFF_HIGH_CMC_THRESHOLD and len(card.template.color_identity) >= 2:
            card_colors = set()
            for c in card.template.color_identity:
                card_colors.add(c.value if hasattr(c, 'value') else str(c))
            missing = card_colors - all_land_colors
            needs.payoff_missing_colors |= missing

    # ── Which spells would become castable with 1 more mana of the right color? ──
    mana_if_one_more = needs.total_mana + 1
    for card in player.hand:
        if card.template.is_land or not card.template.is_spell:
            continue
        raw_cmc = card.template.cmc
        if raw_cmc is None:
            continue
        # Apply effective CMC overrides (e.g. domain cost reduction)
        cmc = raw_cmc
        if effective_cmc_overrides and card.name in effective_cmc_overrides:
            cmc = effective_cmc_overrides[card.name]
        # Track cheapest proactive spell (not instant)
        is_instant = card.template.is_instant if hasattr(card.template, 'is_instant') else False
        if not is_instant:
            # Check card types for instant
            from engine.card_database import CardType
            card_types = card.template.card_types
            is_instant = CardType.INSTANT in card_types if card_types else False
        if not is_instant and cmc < needs.cheapest_proactive_cmc:
            needs.cheapest_proactive_cmc = cmc
        if cmc <= mana_if_one_more:
            # Check if we'd have the colors with one more land
            mc = card.template.mana_cost
            spell_colors = set()
            for code, attr in COLOR_MAP.items():
                if getattr(mc, attr, 0) > 0:
                    spell_colors.add(code)
            # Store effective CMC (4th element) so should_pay_shock uses the
            # domain-reduced cost, not the raw template CMC
            needs.spells_enabled_by_one_more.append((card, spell_colors, is_instant, cmc))

    return needs


def score_land(land, needs: ManaNeeds, is_fetchable: bool = False,
               gameplan_priority: float = 0.0, turn: int = 1) -> float:
    """Score a candidate land using clock-derived values.

    All values derive from game mechanics:
    - Missing color value = clock impact of spells that color enables
    - Tapped penalty = delayed spell clock impact (1 turn discount)
    - Domain value = power boost per new land type × turns remaining
    """
    template = land.template if hasattr(land, "template") else land
    produces = template.produces_mana
    enters_tapped = getattr(template, "enters_tapped", False)
    # Lands with optional life payment can enter untapped (shock lands etc.)
    is_optional_untap = getattr(template, "untap_life_cost", 0) > 0
    score = 0.0
    player_turn = (turn + 1) // 2

    # ── (A) Missing color: value = sum of clock impact of spells it enables ──
    # A color needed by 3 spells is worth 3× a color needed by 1 spell.
    for c in produces:
        if c in needs.missing_colors:
            demand = needs.needed_colors.get(c, 1)
            score += demand * LAND_SCORE_PER_MISSING_COLOR_DEMAND
        if c in needs.payoff_missing_colors:
            score += LAND_SCORE_PAYOFF_MISSING_COLOR_BONUS

    # ── (B) Needed color: still valuable even if we have it (redundancy) ──
    for c in produces:
        if c in needs.needed_colors:
            score += needs.needed_colors[c] * LAND_SCORE_REDUNDANT_COLOR_WEIGHT

    # ── (C) Spell enablement: value = clock impact of enabled spell ──
    land_colors = set(produces)
    combined_colors = needs.existing_colors | land_colors
    for entry in needs.spells_enabled_by_one_more:
        spell, spell_colors = entry[0], entry[1]
        if spell_colors <= combined_colors:
            # Enabled spell's value: cheaper spells = more urgent (on-curve).
            # Clock impact: a 1-mana creature attacks for ~7 turns, 5-mana for ~3.
            cmc = spell.template.cmc or 0
            urgency = (max(1, LAND_SCORE_URGENCY_CMC_CEIL - cmc)
                       * LAND_SCORE_ENABLED_SPELL_URGENCY)
            if enters_tapped and not is_optional_untap:
                # Tapped = spell delayed 1 turn = lose 1 combat step.
                urgency *= (LAND_SCORE_TAPPED_PENALTY_EARLY
                            if player_turn <= 2
                            else LAND_SCORE_TAPPED_PENALTY_LATE)
            score += urgency

    # ── (D) Domain: each new land type = +1 power per domain creature ──
    new_subtypes = 0
    for st in getattr(template, "subtypes", []):
        if st in BASIC_LAND_TYPES and st not in needs.existing_subtypes:
            new_subtypes += 1
    # Domain value: each new type gives +1 power to domain creatures
    # = +1 damage per turn per domain creature = 1/(opp_life) clock per creature.
    domain_value = (LAND_SCORE_DOMAIN_BASE
                    + min(needs.domain_card_count, LAND_SCORE_DOMAIN_CARD_CAP)
                    * LAND_SCORE_DOMAIN_PER_CARD_BONUS)
    score += new_subtypes * domain_value

    # ── (E) Tempo: tapped land = lose 1 turn of mana ──
    # Derived: penalty = best spell we could cast this turn × delay.
    if enters_tapped and not is_optional_untap:
        # Tapped = can't use mana this turn. Penalty decays with player_turn,
        # capped below by LAND_SCORE_TAPPED_FLOOR_FRACTION.
        tempo_penalty = LAND_SCORE_TAPPED_BASE_PENALTY * max(
            LAND_SCORE_TAPPED_FLOOR_FRACTION,
            1.0 - player_turn * LAND_SCORE_TAPPED_TURN_DECAY,
        )
        score -= tempo_penalty
    elif not enters_tapped:
        score += LAND_SCORE_UNTAPPED_BONUS  # immediate mana availability
    if is_optional_untap:
        score += LAND_SCORE_UNTAPPED_BONUS  # shock lands have option to enter untapped

    # ── (F) Fetchlands: flexibility to find what you need ──
    from engine.card_database import FETCH_LAND_COLORS
    if template.name in FETCH_LAND_COLORS and not is_fetchable:
        score += LAND_SCORE_FETCHLAND_FLEXIBILITY_BONUS

    # ── (G) Versatility: more colors = more flexible ──
    score += len(produces) * LAND_SCORE_VERSATILITY_PER_COLOR

    # ── (H) Gameplan priority from deck config ──
    score += gameplan_priority

    # ── (I) Preserve held instant-speed interaction (Bundle 3 A5) ──
    # When the player holds instants / flash permanents, prefer lands
    # that PROVIDE one of the held colors over lands that don't.
    # HELD_COLOR_PRESERVATION_BONUS matches the per-demand weight in
    # block (A) above (8.0 per enabled spell) — held interaction is
    # worth the same as the spell it protects being castable.
    #
    # Locked-hand refinement (seed-60100 G2 T2 Living End): the bonus
    # has *marginal* value only for held colors that are not yet
    # produced by an existing untapped source.  When the held color
    # is already in `existing_colors`, the preservation goal is
    # already met — duplicating the source has zero EV.  Without this
    # guard a held flash spell (e.g. Subtlety, UU) whose color is
    # already on the battlefield can dominate the fetch decision,
    # crowding out lands that would unlock missing colors held
    # SORCERIES (cascade payoffs, board wipes, threats) need.
    # Generalises to every deck running fetchlands + flash + multi-
    # color spells (≥10 of the 16 registered Modern decks).
    if needs.held_instant_colors:
        # HELD_COLOR_PRESERVATION_BONUS sourced from
        # ai/scoring_constants.py — matches the per-demand weight in
        # block (A) above (8.0 per enabled spell).
        held_unmet = needs.held_instant_colors - needs.existing_colors
        if held_unmet and any(c in held_unmet for c in produces):
            score += HELD_COLOR_PRESERVATION_BONUS

    return score


# should_pay_shock removed — shock decisions use analyze_mana_needs() in game_state.py


def choose_best_land(lands: list, needs: ManaNeeds,
                     gameplan_priorities: Optional[Dict[str, float]] = None,
                     turn: int = 1,
                     library: Optional[list] = None) -> Optional:
    """Choose the best land to play from a list of candidates.
    
    Key insight: fetchlands are proxies for whatever they can fetch.
    When comparing a fetchland vs a shockland in hand, the fetchland's
    real value is the score of its best fetchable target (minus a small
    cost for the 1 life and the crack action).
    
    Args:
        lands: List of land cards in hand to choose from
        needs: ManaNeeds from analyze_mana_needs()
        gameplan_priorities: Optional deck-specific land priorities
        turn: Current turn number
        library: Player's library (needed for fetch-as-proxy scoring)
    """
    if not lands:
        return None

    from engine.card_database import FETCH_LAND_COLORS

    prios = gameplan_priorities or {}
    best = None
    best_score = LAND_SCORE_BEST_INIT_SENTINEL

    for land in lands:
        template = land.template if hasattr(land, "template") else land
        gp = prios.get(template.name, 0.0)

        # ── Fetch-as-proxy: score fetchlands by their best target ──
        if template.name in FETCH_LAND_COLORS and library:
            fetch_colors = FETCH_LAND_COLORS[template.name]
            # Find the best target this fetch could get
            proxy_target = choose_fetch_target(
                library, fetch_colors, needs,
                gameplan_priorities=prios, turn=turn
            )
            if proxy_target:
                # Score the fetch as if it were the target land
                proxy_score = score_land(
                    proxy_target, needs,
                    gameplan_priority=prios.get(proxy_target.template.name, 0.0),
                    turn=turn
                )
                # Small penalty: fetch costs 1 life and is slightly slower
                # But it also thins the deck, so net penalty is small.
                s = proxy_score - LAND_SCORE_FETCH_LIFE_TEMPO_PENALTY
            else:
                # No valid target in library — score the fetch itself
                s = score_land(land, needs, gameplan_priority=gp, turn=turn)
        else:
            s = score_land(land, needs, gameplan_priority=gp, turn=turn)

        if s > best_score:
            best_score = s
            best = land

    return best


def should_stagger_shock(game, player_idx: int, land, archetype: str) -> bool:
    """Should we defer paying life on an untapped shock to the next turn?

    Returns True (defer → pay nothing, shock enters tapped) when we've
    already paid life for another shock this turn AND we don't need the
    extra mana urgently. Pure AI-layer observation: no PlayerState
    mutation, no engine counters — detects "shock paid this turn" via
    summoning_sick + untapped + untap_life_cost>0 on other lands.

    Rules (all thresholds derived from game state):
      * Never stagger when the deck profile has a combo chain — every
        mana matters, life is noise.  Detection via
        `StrategyProfile.has_combo_chain` (a structural deck-property
        signal, not an archetype-name comparison) so storm / cascade /
        reanimator decks all qualify without hardcoding their names.
      * Never stagger if hand is empty (no need to keep life reserves).
      * Stagger when at least one shock already entered untapped this turn
        AND (a) incoming damage leaves us at <=12 life, OR
            (b) hand has cards, indicating more turns to play.
    """
    from ai.strategy_profile import get_profile
    if get_profile(archetype).has_combo_chain:
        return False
    me = game.players[player_idx]
    shocks_already = sum(
        1 for other in me.lands
        if other is not land
        and not other.tapped
        and getattr(other, 'summoning_sick', False)
        and getattr(other.template, 'untap_life_cost', 0) > 0
    )
    if shocks_already < 1:
        return False
    opp = game.players[1 - player_idx]
    incoming = sum((c.power or 0) for c in opp.creatures
                   if not getattr(c, 'tapped', False))
    # Racing: always defer second shock when life-after-incoming is below
    # LAND_SHOCK_RACING_LIFE_THRESHOLD.
    if me.life - incoming <= LAND_SHOCK_RACING_LIFE_THRESHOLD:
        return True
    # More plays to make: defer to preserve life
    if len(me.hand) > 0:
        return True
    return False


def choose_fetch_target(library: list, fetch_colors: list,
                        needs: ManaNeeds,
                        gameplan_priorities: Optional[Dict[str, float]] = None,
                        turn: int = 1) -> Optional:
    """Choose the best land to fetch from the library.
    
    Filters to only lands that match the fetch's color identity,
    then scores using the unified scoring system.
    """
    from engine.card_database import FETCH_LAND_COLORS

    prios = gameplan_priorities or {}
    best = None
    best_score = LAND_SCORE_BEST_INIT_SENTINEL

    # Map fetch colors to the basic land types they represent
    _COLOR_TO_BASIC_TYPE = {
        'W': 'Plains', 'U': 'Island', 'B': 'Swamp', 'R': 'Mountain', 'G': 'Forest',
    }
    fetchable_types = {_COLOR_TO_BASIC_TYPE[c] for c in fetch_colors
                       if c in _COLOR_TO_BASIC_TYPE}

    for lib_card in library:
        if not lib_card.template.is_land:
            continue
        # Fetch lands cannot find other fetch lands
        if lib_card.template.name in FETCH_LAND_COLORS:
            continue
        # Must have a matching basic land subtype (not just produce the color)
        # e.g., Sacred Foundry has subtypes ['Mountain', 'Plains'], Mountain has ['Mountain']
        # Gemstone Caverns has subtypes [] — NOT fetchable
        land_subtypes = set(getattr(lib_card.template, 'subtypes', []))
        if not fetchable_types & land_subtypes:
            continue

        gp = prios.get(lib_card.template.name, 0.0)
        s = score_land(lib_card, needs, is_fetchable=True, gameplan_priority=gp, turn=turn)
        if s > best_score:
            best_score = s
            best = lib_card

    return best
