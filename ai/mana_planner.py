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
    cheapest_spell_cmc: int = 99
    # Number of untapped lands on battlefield
    untapped_land_count: int = 0
    # Total available mana (untapped lands + mana pool)
    total_mana: int = 0
    # Spells that could be cast with exactly 1 more mana of the right color
    spells_enabled_by_one_more: List = field(default_factory=list)
    # Cheapest PROACTIVE spell CMC (creatures, sorceries, planeswalkers — not instants)
    cheapest_proactive_cmc: int = 99
    # Number of domain-scaling cards in hand (affects land subtype priority)
    domain_card_count: int = 0
    # Colors needed by high-CMC multi-color payoffs (e.g., Omnath WURG)
    # These get extra weight in fetch/shock decisions
    payoff_missing_colors: Set[str] = field(default_factory=set)


def analyze_mana_needs(game: "GameState", player_idx: int,
                       effective_cmc_overrides: Optional[Dict[str, int]] = None) -> ManaNeeds:
    """Analyze the player's hand and battlefield to determine mana needs.
    
    This is the core analysis that all land decisions should use.
    effective_cmc_overrides: maps card name -> effective CMC (e.g. Scion of Draco -> 3 with domain)
    """
    player = game.players[player_idx]
    needs = ManaNeeds()

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
    from engine.game_state import CYCLING_COSTS
    for card in player.hand:
        if card.name in CYCLING_COSTS:
            cycle_cost = CYCLING_COSTS[card.name]
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
        if cmc >= 3 and len(card.template.color_identity) >= 2:
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
    """Score a candidate land based on how well it serves the hand's needs.
    
    Used for:
      - Choosing which land to play from hand
      - Choosing which land to fetch from library
      - Comparing multiple land options
    
    Args:
        land: CardInstance or CardTemplate of the land
        needs: ManaNeeds from analyze_mana_needs()
        is_fetchable: True if this is a fetch target (library search)
        gameplan_priority: Additional priority from the GoalEngine's deck config
        turn: Current turn number (affects tempo weighting)
    """
    template = land.template if hasattr(land, "template") else land
    produces = template.produces_mana
    enters_tapped = getattr(template, "enters_tapped", False)
    from engine.card_database import SHOCK_LANDS
    is_shock = template.name in SHOCK_LANDS
    score = 0.0
    # Convert game turn to player turn (players alternate turns)
    player_turn = (turn + 1) // 2

    # ── (A) Missing color match: highest priority ──
    missing_match = sum(1 for c in produces if c in needs.missing_colors)
    score += missing_match * 20.0

    # ── (B) Needed color match: still valuable even if we have it ──
    for c in produces:
        if c in needs.needed_colors:
            score += needs.needed_colors[c] * 3.0
        # Extra boost for colors needed by high-CMC multi-color payoffs
        # (e.g., Omnath WURG — fetching a Green source when we have WUR is critical)
        if c in needs.payoff_missing_colors:
            score += 15.0

    # ── (C) Enables a specific spell this turn ──
    # Huge bonus if this land lets us cast something we couldn't before
    # BUT: tapped lands can't enable spells THIS turn — discount heavily
    land_colors = set(produces)
    combined_colors = needs.existing_colors | land_colors
    for entry in needs.spells_enabled_by_one_more:
        spell, spell_colors = entry[0], entry[1]
        if spell_colors <= combined_colors:
            cmc = spell.template.cmc or 0
            urgency = max(0, 8 - cmc) * 3.0
            if enters_tapped and not is_shock:
                # Tapped land: spell enablement is delayed by 1 turn
                # On player T1-T2 this is devastating; on T5+ it matters less
                urgency *= 0.15 if player_turn <= 2 else 0.4
            score += urgency

    # ── (D) Domain bonus: new basic land types ──
    new_subtypes = 0
    for st in getattr(template, "subtypes", []):
        if st in BASIC_LAND_TYPES and st not in needs.existing_subtypes:
            new_subtypes += 1
    # Domain weight scales with how many domain cards are in hand/on board
    # Base: 2-4 per new subtype. With domain cards: up to 8-12 per subtype.
    # Each domain card adds ~2 points of value per new subtype:
    #   - Kavu/Brawler: +1/+1 per domain = ~2 damage per turn
    #   - Scion: -2 cost per domain = huge mana savings
    #   - Binding: -1 cost per domain
    #   - Tribal Flames: +1 damage per domain
    base_domain_weight = 2.0 if player_turn <= 2 else 4.0
    domain_scaling = min(needs.domain_card_count, 5) * 2.0  # cap at 5 cards
    domain_weight = base_domain_weight + domain_scaling
    score += new_subtypes * domain_weight

    # ── (E) Tempo: untapped is MUCH better on early turns ──
    if enters_tapped and not is_shock:
        # Heavy penalty for tapped lands, especially on player T1-T3
        if player_turn <= 1:
            score -= 30.0  # T1 tapped land is almost always wrong for aggro
        elif player_turn <= 2:
            score -= 20.0
        elif player_turn <= 3:
            score -= 12.0
        else:
            score -= 5.0
    elif not enters_tapped:
        score += 8.0  # Bonus for untapped
    # Shocklands CAN enter untapped (with life payment) so treat as untapped
    if is_shock:
        score += 7.0  # strong untapped potential

    # ── (F) Fetchlands: bonus for flexibility (can find the right land) ──
    from engine.card_database import FETCH_LAND_COLORS
    if template.name in FETCH_LAND_COLORS and not is_fetchable:
        # Fetchlands are flexible — they find what you need
        # But they cost 1 life and require cracking, so slightly less than a direct shockland
        score += 4.0

    # ── (G) Versatility: more colors = more flexible ──
    score += len(produces) * 1.0

    # ── (H) Gameplan priority from deck config ──
    score += gameplan_priority

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
    best_score = -999.0

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
                # But it also thins the deck, so net penalty is small
                s = proxy_score - 1.0
            else:
                # No valid target in library — score the fetch itself
                s = score_land(land, needs, gameplan_priority=gp, turn=turn)
        else:
            s = score_land(land, needs, gameplan_priority=gp, turn=turn)

        if s > best_score:
            best_score = s
            best = land

    return best


def choose_fetch_target(library: list, fetch_colors: list,
                        needs: ManaNeeds,
                        gameplan_priorities: Optional[Dict[str, float]] = None,
                        turn: int = 1) -> Optional:
    """Choose the best land to fetch from the library.
    
    Filters to only lands that match the fetch's color identity,
    then scores using the unified scoring system.
    """
    from engine.card_database import SHOCK_LANDS, FETCH_LAND_COLORS

    prios = gameplan_priorities or {}
    best = None
    best_score = -999.0

    for lib_card in library:
        if not lib_card.template.is_land:
            continue
        # Fetch lands cannot find other fetch lands
        if lib_card.template.name in FETCH_LAND_COLORS:
            continue
        produces = lib_card.template.produces_mana
        # Must produce at least one of the fetch's colors
        if not any(c in fetch_colors for c in produces):
            continue

        gp = prios.get(lib_card.template.name, 0.0)
        s = score_land(lib_card, needs, is_fetchable=True, gameplan_priority=gp, turn=turn)
        if s > best_score:
            best_score = s
            best = lib_card

    return best
