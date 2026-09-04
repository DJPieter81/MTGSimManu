"""Clock-Based Position Evaluation
===================================
Unified board evaluation based on game-theory principles.

Core idea: every game state is valued by the "clock differential" —
how many turns until I win minus how many turns until my opponent wins.
All creature values, keyword bonuses, and resource advantages are derived
from how they change this clock, not from arbitrary weights.

Units: turns of clock advantage. +1.0 means I'm one combat step ahead.
"""
from __future__ import annotations
import enum
import math
import re
from typing import TYPE_CHECKING, Optional, Set

from ai.scoring_constants import (
    PURE_BLOCKER_TOUGHNESS_VALUE,
    EVASION_VS_BLOCKERS_MULTIPLIER,
    FIRST_STRIKE_SURVIVAL_MULTIPLIER,
    REMOVAL_RESISTANT_MULTIPLIER,
    UNDYING_RECURSION_MULTIPLIER,
    KEYWORD_HALF_WEIGHT,
    KEYWORD_MINOR_WEIGHT,
    TOUGHNESS_DEFENSIVE_WEIGHT,
    ANNIHILATOR_CHIP_PER_OPP_CREATURE,
    ANNIHILATOR_BASE_SAC,
    PROWESS_TRIGGER_PER_TURN,
    ETB_VALUE_BONUS,
    TOKEN_MAKER_BONUS,
    AVG_CREATURE_POWER,
    CLOCK_IMPACT_LIFE_SCALING,
    CLOCK_LETHAL_ADVANTAGE_CAP,
    LIFELINK_LIFE_GAIN_WEIGHT,
    CLOCK_BLOCKER_ABSORPTION_TURN_CYCLE,
)

if TYPE_CHECKING:
    from ai.ev_evaluator import EVSnapshot
    from engine.cards import CardInstance
    from engine.game_state import PlayerState

# Sentinel: no clock (no creatures / no win condition)
NO_CLOCK = 99.0


# ─────────────────────────────────────────────────────────────
# Clock arithmetic — turns to kill, derived from board state
# ─────────────────────────────────────────────────────────────

def opp_one_turn_damage(game: "GameState", player_idx: int) -> int:
    """Damage the opponent can deal on their next turn — the M4-spec
    primitive.

    Derivation (state-only, no constants): their on-board creature
    power counts in full (tapped bodies untap at their untap step),
    plus ONE average-deployment increment (board_power / creature
    count) — aggro boards grow every turn, and a survival threshold
    built on the current board alone under-counts by exactly one
    deployment (probe s60110 T4: guard saw 4-5 power, took 8).
    Empty board → 0.
    """
    opp = game.players[1 - player_idx]
    creatures = [c for c in opp.creatures]
    if not creatures:
        return 0
    board_power = sum(c.power or 0 for c in creatures)
    development = board_power // len(creatures)
    return board_power + development


def combat_clock(power: int, opp_life: int,
                 evasion_power: int = 0,
                 opp_total_toughness: int = 0) -> float:
    """Turns to kill opponent via combat damage.

    Evasion power bypasses blockers entirely.  Non-evasive power must
    punch through blocker toughness (simplified: total toughness as a
    one-time wall, amortised per turn).

    Returns NO_CLOCK when there is no attack capability.
    """
    if power <= 0 or opp_life <= 0:
        return NO_CLOCK if power <= 0 else 1.0

    # Evasion damage lands every turn; ground damage is reduced by blockers.
    # Simplified model: blockers absorb (total_toughness / N) per turn on
    # average where N = CLOCK_BLOCKER_ABSORPTION_TURN_CYCLE (replacement
    # cadence on a typical Modern board).
    blocker_absorption = (opp_total_toughness / CLOCK_BLOCKER_ABSORPTION_TURN_CYCLE
                          if opp_total_toughness > 0 else 0)
    ground_power = max(0, power - evasion_power)
    effective_ground = max(0, ground_power - blocker_absorption)
    effective_power = evasion_power + effective_ground

    if effective_power <= 0:
        return NO_CLOCK
    return max(1.0, math.ceil(opp_life / effective_power))


def life_as_resource(life: int, incoming_power: int) -> float:
    """Value of life total expressed as turns of survival.

    Low life is disproportionately valuable — going from 3→2 is much
    worse than 20→19 because it brings lethal closer.

    Returns a value where higher = more comfortable:
    - Dead: -100
    - 1 life with 5 power incoming: 0.2 turns (critical)
    - 20 life with 5 power incoming: 4.0 turns (comfortable)
    - 20 life with 0 power incoming: 10.0 (safe, life is a luxury)
    """
    if life <= 0:
        return -100.0
    if incoming_power <= 0:
        # No clock — life is a luxury resource, diminishing returns
        return min(10.0, life * 0.5)
    # Cap at no-threat value so deploying small creatures never makes
    # opponent's survival score INCREASE (was: 1-power → 20.0 > no-threat 10.0)
    no_threat_cap = min(10.0, life * 0.5)
    return min(no_threat_cap, life / incoming_power)


# ─────────────────────────────────────────────────────────────
# Resource valuation — cards and mana as future clock changers
# ─────────────────────────────────────────────────────────────

def card_clock_impact(snap: "EVSnapshot") -> float:
    """How much one card in hand changes the clock (on average).

    Derived from game state: early game with mana to deploy = high impact.
    Late game with full board = diminishing impact.

    A card is worth roughly "average creature power / opponent life" turns,
    discounted by whether we have mana to cast it.
    """
    # AVG_CREATURE_POWER is the centralized "average Modern creature"
    # baseline (~2.5 power, ~2.5 CMC), shared with cascade/token bonuses.
    opp_life = max(1, snap.opp_life)
    base_impact = AVG_CREATURE_POWER / opp_life  # ~0.125 at 20 life, ~0.5 at 5 life

    # Mana gating: cards are worth less if we can't cast them
    castable_fraction = min(1.0, snap.my_mana / 3.0) if snap.my_mana > 0 else 0.2

    return base_impact * castable_fraction


def mana_clock_impact(snap: "EVSnapshot") -> float:
    """How much one point of mana advantage changes the clock.

    Mana enables casting spells. Its value depends on what spells
    could be cast — approximated by game phase.
    """
    opp_life = max(1, snap.opp_life)
    # Mana → enables casting → roughly 1 power per mana spent → clock change
    return 1.0 / opp_life


# ─────────────────────────────────────────────────────────────
# Game-phase predicate — derived from clock state, not turn count
# ─────────────────────────────────────────────────────────────

EARLY_GAME_CLOCK_THRESHOLD: float = 4.0
"""Derived: number of remaining turns of clock above which the game is
classified as "early."

Symmetric across both players via min(my_clock, opp_clock) — the game
is early iff *neither* side is within striking distance. Replaces the
hard-coded `turn_number <= 4` heuristic in bhi.py / evaluator.py /
gameplan.py — those checks ignored board state entirely, so a fast
aggro deck on T2 with lethal in 2 still triggered "early game" hold
rates and bonuses, mis-modelling the actual decision.

Threshold of 4 chosen to match the original turn-counter heuristic on
average game pacing (early-game discard / hold-rate decisions kicked
in through ~T4 of a typical Modern game). Past 4 turns of clock on
both sides, the average Modern board has resolved or is about to.
"""


def is_early_game(snap: "EVSnapshot") -> bool:
    """Early-game predicate, derived from board state instead of turn count.

    Returns True iff *both* sides' clocks exceed
    `EARLY_GAME_CLOCK_THRESHOLD` turns. A fast deck on a slow board
    is correctly classified as mid-game by T2 once the clock collapses;
    a Tron-style board with no creatures stays in "early game" past T6
    if neither side is pressuring.

    Uses the existing `EVSnapshot.my_clock` / `opp_clock` properties
    (continuous turns-to-lethal) — no separate clock primitive needed.
    """
    return min(snap.my_clock, snap.opp_clock) > EARLY_GAME_CLOCK_THRESHOLD


# ─────────────────────────────────────────────────────────────
# Combo clock — turns until combo fires
# ─────────────────────────────────────────────────────────────

# Per-archetype-subtype resource-assembly target.  The default (Storm /
# Amulet Titan / generic combo) needs ~8 "resource points" — 2-3 cards
# of fuel, 2-3 mana, and engine setup — before it can fire.  Cascade-
# reanimator decks (Living End et al.) have a cheaper win condition: a
# single 3-mana cascade spell + ~3 graveyard creatures resolves the
# combo in one shot.  That is ~6 resource points, not 8.
#
# Subtype strings are loaded from the gameplan JSON
# (`archetype_subtype` field) and plumbed through via
# `EVSnapshot.archetype_subtype`.  New subtypes register in this table;
# we do NOT branch on card or deck names.
_COMBO_ASSEMBLY_TARGET = {
    "storm": 8,                 # Ruby Storm — 8 resource points (default)
    "cascade_reanimator": 6,    # Living End style — cascade + GY fuel
}
_COMBO_ASSEMBLY_DEFAULT = 8     # fallback when archetype_subtype is missing
                                # (Amulet Titan, Goryo's Vengeance, etc.)


def combo_clock(snap: "EVSnapshot") -> float:
    """Turns until a combo deck can win.

    Based on storm count, hand size (fuel), mana, and graveyard
    (for reanimation combos).

    The resource-assembly target varies by archetype sub-type so that
    cheaper combos (cascade-reanimator: 3 mana + ~3 GY creatures +
    cascade spell) are not under-estimated as slow 8-resource Storm
    plans.  See `_COMBO_ASSEMBLY_TARGET`.
    """
    # Mid-chain: storm count directly measures proximity to kill
    if snap.storm_count >= 10:
        return 1.0  # likely lethal this turn
    if snap.storm_count >= 5:
        return 1.0  # close to lethal

    # Pre-chain: estimate turns to assemble
    # Need: fuel in hand (2-3 cards), mana (2-3), and a finisher
    fuel_ready = min(snap.my_hand_size, 5)  # cap at 5 useful cards
    mana_ready = min(snap.my_mana, 5)

    # Reanimation combos: creature in GY is a key resource
    reanimate_ready = min(snap.my_gy_creatures, 2)

    # Resource-assembly target, archetype-routed.  Unknown / missing
    # subtype → default 8-resource model (Storm-parity, regression-safe
    # for Amulet Titan / Goryo's Vengeance).
    subtype = getattr(snap, "archetype_subtype", None)
    needed = _COMBO_ASSEMBLY_TARGET.get(subtype, _COMBO_ASSEMBLY_DEFAULT)

    # Rough estimate: turns = (resources needed - resources available)
    resources = fuel_ready + mana_ready + reanimate_ready + snap.storm_count
    deficit = max(0, needed - resources)

    if deficit == 0:
        return 1.0  # ready to go off
    return min(NO_CLOCK, 1.0 + deficit)


# ─────────────────────────────────────────────────────────────
# Commit-vs-develop gate — spend a scarce one-shot payoff now, or
# hold to assemble a larger line? (decision-kernel primitive)
# ─────────────────────────────────────────────────────────────
# The single place that answers a question every deck faces the
# instant it is about to spend a resource it cannot get back: a
# one-shot burn finisher, a tutor that fetches one, any closer whose
# effect ends when it resolves. Before this primitive existed the
# answer lived only inside the storm-specific fuel-counting heuristic
# in `ai/combo_calc.py`, which fired the payoff whenever no fuel sat in
# hand *this turn* — with no notion of "is a decisive line executable
# now vs. developing toward a bigger one." That gap let the AI cash a
# scarce payoff for damage far below the clock-derived lethal
# threshold (a tutor -> burn finisher for 2 into 19) while a future
# turn could still assemble lethal. The gate is deck-agnostic: every
# input is a clock-derived fact, so it applies to any archetype facing
# a commit-vs-build choice, not a storm code path.


def scarce_payoff_commit_ev(
    fire_now_damage: float,
    opp_life: int,
    combo_value: float,
    develop_reach_probability: float,
) -> float:
    """Build-vs-combo EV: ``EV(commit the payoff now) − EV(develop a
    larger line)``, in the combo-value units the scorer adds to a play.
    Positive → committing now is worth more (fire); negative → the
    developed line is worth more (hold). This is the EV comparison the
    commit/hold decision reduces to — no literal threshold; the caller
    sources every term from clock / library resource-math / (later) BHI.

      * ``EV(now)`` = ``min(1, fire_now_damage / opp_life) × combo_value``
        — the fraction of a kill the scarce payoff secures immediately.
        A payoff whose damage reaches ``opp_life`` (CR 104.3a lethal)
        caps at a full ``combo_value``; it cannot be worth more than a
        kill, so overshoot damage adds nothing (this is why cashing a
        finisher for 2 into 19 scores a twentieth of a kill, not "some
        progress").
      * ``EV(develop)`` = ``develop_reach_probability × combo_value`` —
        the developed line IS the deck's assembled combo, worth a full
        kill, discounted by the probability we actually reach it.
        ``develop_reach_probability`` folds survival (we live to take
        the turn), growth headroom (a larger line is still assemblable),
        and — when a hand-tracker is threaded in — the chance the line
        is not disrupted. It is 0 when developing is impossible or
        pointless (already lethal now, dead next turn, this turn's
        per-turn resource already sunk, or no fuel left to grow), which
        makes ``EV(develop)`` collapse to 0 and firing dominate.

    Because both sides are expressed in the same kill-fraction ×
    combo_value units, the sign of the difference is the decision and
    its magnitude is the EV at stake — no arbitrary hold penalty.
    """
    ev_now = min(1.0, fire_now_damage / max(1, opp_life)) * combo_value
    ev_develop = develop_reach_probability * combo_value
    return ev_now - ev_develop


def should_commit_scarce_payoff(
    fire_now_damage: float,
    opp_life: int,
    *,
    line_can_grow: bool,
    chain_uncommitted: bool,
    survives_to_next_turn: bool,
) -> bool:
    """Spend a scarce, one-shot payoff NOW, or hold to develop a larger
    line? Returns True to commit (fire), False to hold. Thin boolean
    reduction of :func:`scarce_payoff_commit_ev` (``EV(commit) −
    EV(develop) >= 0``) for callers that only need the decision, not the
    magnitude — the decision itself is the EV comparison.

    A *scarce payoff* is consumed when cast and cannot be replayed — a
    one-shot burn finisher (Grapeshot class), a tutor that fetches one
    (Wish class), any closer whose effect ends on resolution. For such
    a resource "chip a little now and finish later" is a fiction:
    firing below lethal forfeits the larger line a future turn would
    assemble. Every argument is a clock-derived fact:

      * ``fire_now_damage`` — damage the payoff deals if cast now,
        measured against opponent life (the clock resource).
      * ``opp_life`` — the clock-derived lethal threshold (CR 104.3a:
        a player at 0 life loses).
      * ``line_can_grow`` — a future turn can make the line strictly
        larger (e.g. the library still holds chain fuel to draw).
      * ``chain_uncommitted`` — no part of THIS turn's line is sunk
        yet. A per-turn chain resource (storm count) empties at end of
        turn, so once spells are sunk into it, holding the payoff
        forfeits them; while nothing is sunk, holding costs nothing.
      * ``survives_to_next_turn`` — we live to take the turn that would
        grow the line (``not EVSnapshot.am_dead_next``).

    The developed line is reachable (``develop_reach = 1``) only when a
    strictly larger, still-assemblable line survives to a turn we take
    with nothing yet sunk — and is never reachable once the payoff is
    already lethal now. In every other case ``develop_reach = 0`` and
    ``EV(develop)`` is 0, so the payoff commits. Terminating by
    construction: the opponent's clock eventually removes survival, the
    library eventually empties, or the first sunk spell flips
    ``chain_uncommitted``, so the AI cannot stall forever.
    """
    lethal_now = fire_now_damage >= opp_life
    develop_reachable = (
        not lethal_now
        and survives_to_next_turn
        and line_can_grow
        and chain_uncommitted
    )
    develop_reach = 1.0 if develop_reachable else 0.0
    return scarce_payoff_commit_ev(
        fire_now_damage, opp_life, 1.0, develop_reach) >= 0.0


# ─────────────────────────────────────────────────────────────
# Creature clock impact — what one creature contributes
# ─────────────────────────────────────────────────────────────

def creature_clock_impact(power: int, toughness: int,
                          keywords: Set[str],
                          snap: "EVSnapshot") -> float:
    """Clock impact of a single creature on the battlefield.

    Replaces the old creature_value() which used arbitrary keyword weights.
    All values derived from how the creature changes the combat clock.
    """
    opp_life = max(1, snap.opp_life)
    if power <= 0 and not keywords:
        return toughness * PURE_BLOCKER_TOUGHNESS_VALUE

    # Base clock impact: fraction of kill per turn.
    # A 3/3 vs 20 life = 0.15 turns per combat step.
    base = power / opp_life

    # Flying / menace / trample bypass blockers; multiplier from
    # EVASION_VS_BLOCKERS_MULTIPLIER (ground attackers lose ~30%).
    has_evasion = keywords & {"flying", "menace", "trample"}
    if has_evasion and snap.opp_creature_count > 0:
        base *= EVASION_VS_BLOCKERS_MULTIPLIER

    # Haste: immediate attack the turn it enters = one extra combat step.
    if "haste" in keywords:
        base += power / opp_life

    # Lifelink: each attack gains life = extends survival by
    # power/opp_power turns; weighted by KEYWORD_HALF_WEIGHT
    # (offensive + defensive contributions partially redundant).
    if "lifelink" in keywords and snap.opp_power > 0:
        life_extension = power / max(1, snap.opp_power)
        base += life_extension * KEYWORD_HALF_WEIGHT

    # Deathtouch: effectively removes a blocker = improves ground clock,
    # weighted by KEYWORD_HALF_WEIGHT (blocker can be re-deployed).
    if "deathtouch" in keywords and snap.opp_creature_count > 0:
        avg_opp_power = snap.opp_power / max(1, snap.opp_creature_count)
        base += avg_opp_power / opp_life * KEYWORD_HALF_WEIGHT

    # Double strike: effectively doubles power for clock.
    if "double_strike" in keywords:
        base += power / opp_life

    # First strike: survives combat more often, preserving clock.
    if "first_strike" in keywords and snap.opp_creature_count > 0:
        base *= FIRST_STRIKE_SURVIVAL_MULTIPLIER

    # Hexproof / indestructible: removal-proof clock is more reliable.
    if "hexproof" in keywords or "indestructible" in keywords:
        base *= REMOVAL_RESISTANT_MULTIPLIER

    # Vigilance: attacks without tapping = also blocks; defensive
    # contribution at KEYWORD_MINOR_WEIGHT (offensive clock dominates).
    if "vigilance" in keywords and snap.opp_power > 0:
        block_value = min(toughness, snap.opp_power) / max(1, snap.my_life)
        base += block_value * KEYWORD_MINOR_WEIGHT

    # Reach: blocks flyers; same minor defensive bracket as vigilance.
    if "reach" in keywords and snap.opp_evasion_power > 0:
        base += min(toughness, snap.opp_evasion_power) / max(1, snap.my_life) * KEYWORD_MINOR_WEIGHT

    # Undying: dies and comes back = ~1.5× clock contribution.
    if "undying" in keywords:
        base *= UNDYING_RECURSION_MULTIPLIER

    # Annihilator: forced sacrifice — board chip + per-trigger sac.
    if "annihilator" in keywords:
        base += snap.opp_creature_count * ANNIHILATOR_CHIP_PER_OPP_CREATURE / max(1, opp_life)
        base += ANNIHILATOR_BASE_SAC / opp_life

    # Prowess: ~1 noncreature spell per turn = ~1 trigger.
    if "prowess" in keywords:
        base += PROWESS_TRIGGER_PER_TURN / opp_life

    # Cascade: free spell of CMC < caster ≈ another small creature.
    # Phase 1 refactor: scaling factor sourced from the LLM helper,
    # cached per (archetype, context).  The keyword-tied "*" wildcard
    # row in DEFAULT_WEIGHTS preserves the historical 2.5 value when
    # the cache is cold.
    if "cascade" in keywords:
        from ai.llm_decision_scorer import (
            weight as _scorer_weight,
            CTX_CASCADE_FREE_SPELL_VALUE,
        )
        # The clock layer is archetype-agnostic at this level; use the
        # "*" wildcard so the LLM scoring layer can refine per-deck
        # values via the cache without forking this code path.
        base += _scorer_weight("*", CTX_CASCADE_FREE_SPELL_VALUE) / opp_life

    # Implicit toughness blocking value (no keyword required).
    if toughness > 0 and snap.opp_power > 0:
        block_value = min(toughness, snap.opp_power) / max(1, snap.my_life)
        base += block_value * TOUGHNESS_DEFENSIVE_WEIGHT

    return base


def forfeited_attack_clock_impact(power: int, keywords: Set[str],
                                  snap: "EVSnapshot") -> float:
    """Clock impact of the single combat step a creature would take
    THIS turn — the price of any pre-combat action that removes its
    attack capability (a blink resetting summoning sickness on a
    temporarily hasty body, a tap cost, an exile-and-return effect).

    Derivation mirrors the haste term in `creature_clock_impact`
    (haste = one extra combat step = power / opp_life):

      * damage step: ``min(1, damage / opp_life)`` — the kill fraction
        one attack secures. Capped at a full kill (CR 104.3a: damage
        past lethal adds nothing — the same cap
        ``scarce_payoff_commit_ev`` documents), so the charge grows as
        opp_life falls and saturates exactly when the attack is lethal.
      * double strike: both strike steps connect, doubling the damage
        of the forfeited step (same modeling as
        ``creature_clock_impact``).
      * lifelink: forfeiting the attack also forfeits the survival
        extension — ``power / opp_power`` turns × KEYWORD_HALF_WEIGHT,
        the SAME weighting ``creature_clock_impact`` gives lifelink.

    Returns 0.0 for a powerless creature: no combat step to lose.
    Units: turns of clock (kill fraction), like every primitive here;
    callers convert to EV via CLOCK_IMPACT_LIFE_SCALING.
    """
    if power <= 0:
        return 0.0
    opp_life = max(1, snap.opp_life)
    damage = power
    if "double_strike" in keywords:
        damage += power  # second strike step connects too
    base = min(1.0, damage / opp_life)
    if "lifelink" in keywords and snap.opp_power > 0:
        base += (power / max(1, snap.opp_power)) * KEYWORD_HALF_WEIGHT
    return base


def creature_clock_impact_from_card(card: "CardInstance",
                                     snap: "EVSnapshot") -> float:
    """Convenience: compute clock impact from a CardInstance."""
    t = card.template
    p = card.power if card.power else 0
    tough = card.toughness if card.toughness else 0
    kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
           for kw in getattr(t, 'keywords', set())}

    base = creature_clock_impact(p, tough, kws, snap)

    # Tag-based abilities add value through game effects:
    tags = getattr(t, 'tags', set())
    opp_life = max(1, snap.opp_life)
    if "etb_value" in tags:
        base += ETB_VALUE_BONUS / opp_life
    if "card_advantage" in tags:
        base += card_clock_impact(snap)  # draws a card = future clock change
    if "token_maker" in tags:
        base += TOKEN_MAKER_BONUS / opp_life

    return base


# ─────────────────────────────────────────────────────────────
# Opportunity cost — "what do I lose by spending this?" (Phase 2a,
# docs/design/rules-foundation-sweep-tracker.md). Single owner for a
# question every chump-block / sacrifice / discard-target decision
# needs answered. Before this primitive existed, each call site
# answered it with its own POSITIVE-signal proxy wired as a VETO:
# raw power (ai.ev_player's 0-power chump-block gate — the audited
# specimen bug), an oracle substring ("whenever this creature
# attacks"), a card type (PLANESWALKER), or a name list. Every proxy
# correlates with "this permanent has value elsewhere", but none of
# them PRICE that value — so the AI refused its cheapest, most
# disposable resources at exactly the moment a real price comparison
# would say "spend it, it's worth ~0 anyway".
# ─────────────────────────────────────────────────────────────

# CR 602.1a: an activated ability is written "[Cost]: [Effect]." Costs
# are either mana symbols (`{T}`, `{2}{R}`, …) or a capitalized keyword
# cost phrase (`Sacrifice a Goblin`, `Discard a card`, `Remove a +1/+1
# counter from this creature`), optionally comma-separated, ending in a
# colon. This is the same "colon is the activation cost separator"
# convention `engine/oracle_parser.py`'s saga-chapter-grant parser
# already documents (`_SAGA_GAINS_RE`'s docstring) — reused here as a
# direct oracle-text scan because `CardTemplate.abilities` never
# populates `AbilityType.ACTIVATED` at DB-load time (only CAST / ETB /
# ATTACK / DIES are extracted there; verified empirically — 0 of 12972
# creatures in the live DB carry an ACTIVATED entry), so
# `ai.response_enumeration._battlefield_has_activatable`'s structural
# check is unreachable for real cards today. Deliberately NOT filtered
# to exclude mana abilities (unlike that helper) — a mana dork's
# activated ability is exactly the kind of recurring value this
# primitive must price; losing a mana source to a needless chump block
# is a real cost.
_ACTIVATED_ABILITY_RE = re.compile(
    r'(?:\{[^}]+\}|[A-Z][a-zA-Z \'\-]{2,40})'
    r'(?:,\s*(?:\{[^}]+\}|[A-Z][a-zA-Z \'\-]{2,40}))*'
    r'\s*:\s'
)


def _has_activated_ability(card: "CardInstance") -> bool:
    """True iff ``card``'s oracle text contains a CR 602.1a-shaped
    activated ability ("[Cost]: [Effect]")."""
    oracle = getattr(card.template, 'oracle_text', '') or ''
    return bool(_ACTIVATED_ABILITY_RE.search(oracle))


def opportunity_cost(card: "CardInstance", board: Optional["PlayerState"],
                      snap: "EVSnapshot") -> float:
    """What we lose by spending ``card`` right now (chump block,
    forced sacrifice, discard) — in the same "value" units as
    ``ai.ev_evaluator.creature_value`` (clock-impact ×
    ``CREATURE_VALUE_OUTER_SCALE``), so callers can weigh it directly
    against a benefit computed in those units.

    Three additive components, each reusing an existing primitive
    rather than reimplementing it — this function is the single
    place that prices "future value if kept", callable BEFORE a
    veto excludes the candidate rather than only after:

      1. Ongoing combat/keyword clock impact if the permanent stays
         on the battlefield — ``creature_clock_impact_from_card``.
         This already answers "can this thing ever attack/block
         productively again" generically: a vanilla 0/0 with no
         keywords prices at 0 (nothing left to block or attack
         with); a 0-power creature with real toughness prices its
         blocking value via ``PURE_BLOCKER_TOUGHNESS_VALUE`` inside
         that same function.
      2. Un-exhausted activated ability — one card's worth of future
         clock impact (``card_clock_impact``, the same conversion
         ``creature_clock_impact_from_card`` already uses for the
         "card_advantage" tag bonus) when the oracle text exposes a
         CR 602.1a-shaped activated ability (``_has_activated_ability``
         below — deliberately not
         ``ai.response_enumeration._battlefield_has_activatable``,
         whose structural ``AbilityType.ACTIVATED`` check is
         unreachable for every real card in the live DB; see that
         helper's comment for the empirical count). A repeatable
         engine recurs every turn cycle regardless of whether it
         already fired this turn, so this does not gate on
         ``card.tapped``.
      3. Equipment ceiling — ``_equipment_ceiling_for_creature``
         (``ai.permanent_threat``), already expressed in these same
         "value" units (it is summed directly into
         ``creature_threat_value`` today).

    Returns 0.0 for a non-creature permanent (this primitive prices
    what's lost by spending a creature/blocker specifically) and for
    a creature with genuinely no future value: no keywords, no
    toughness, no activated ability, no equipment ceiling on the
    board — a truly spent chump.

    ``board`` is the controller's ``PlayerState`` (needed to look up
    unattached/rebindable equipment); pass ``None`` when unavailable
    (e.g. a context-free fixture) — the equipment-ceiling term is
    simply omitted, matching ``creature_threat_value``'s existing
    "no game/controller in scope" fallback.
    """
    combat, extras = _opportunity_cost_terms(card, board, snap)
    return combat + extras


def noncombat_opportunity_cost(card: "CardInstance",
                               board: Optional["PlayerState"],
                               snap: "EVSnapshot") -> float:
    """The part of `opportunity_cost` that is NOT the creature's own
    combat clock: activated abilities, equipment ceiling, mana production,
    unbounded-engine membership. The block scorer charges this as virtual
    life when a block kills the blocker — its combat contribution is
    already modelled there through `my_power_after`, so charging the
    whole opportunity cost would double-count power."""
    _combat, extras = _opportunity_cost_terms(card, board, snap)
    return extras


def _opportunity_cost_terms(card: "CardInstance",
                            board: Optional["PlayerState"],
                            snap: "EVSnapshot"):
    """(combat_term, non_combat_terms) behind `opportunity_cost` — one
    computation, two views."""
    if not getattr(card.template, 'is_creature', False):
        return 0.0, 0.0

    from ai.scoring_constants import CREATURE_VALUE_OUTER_SCALE

    combat = creature_clock_impact_from_card(card, snap) * CREATURE_VALUE_OUTER_SCALE
    base = 0.0

    if _has_activated_ability(card):
        base += card_clock_impact(snap) * CREATURE_VALUE_OUTER_SCALE

    ceiling_lift = 0.0
    game = getattr(card, '_game_state', None)
    if game is not None and board is not None:
        from ai.permanent_threat import _equipment_ceiling_for_creature
        ceiling_lift = _equipment_ceiling_for_creature(card, board, game)

    # 4. Mana production — a mana source's future is the mana it taps
    #    for, priced at `mana_clock_impact` per unit (the same per-mana
    #    rate `position_value` charges for spent mana), in these value
    #    units. Read from the parse-once mana fields via
    #    `ai.activation_ev.tap_mana_units`.
    from ai.activation_ev import tap_mana_units
    mana_units = tap_mana_units(card)
    if mana_units > 0:
        base += mana_units * mana_clock_impact(snap) * CREATURE_VALUE_OUTER_SCALE

    # 5. Unbounded-engine membership — a permanent that is half of a free
    #    self-untapping mana loop (CR 726.4 shortcut material; engine-side
    #    rules query, summoning sickness ignored because the loop is live
    #    from the next untap step) gives up the engine's whole shortcut
    #    allowance when spent. Membership is either side: the untapper
    #    itself, or the counter-placement replacement that frees it.
    if game is not None:
        from engine.activation import ActivationManager
        from engine.constants import LOOP_SHORTCUT_MANA
        lost = ActivationManager.engines_lost_if_removed(
            game, card.controller, card)
        if lost:
            base += (lost * LOOP_SHORTCUT_MANA * mana_clock_impact(snap)
                     * CREATURE_VALUE_OUTER_SCALE)

    return combat, base + ceiling_lift


# ─────────────────────────────────────────────────────────────
# Loyalty-pool value — composable primitive for permanents that
# accumulate "activation pools" (planeswalkers, saga chapters,
# class-card levels, charge-counter engines).
# ─────────────────────────────────────────────────────────────

def loyalty_pool_value(activations: float, snap: "EVSnapshot") -> float:
    """Power-equivalent value of an expected-activation pool.

    A planeswalker's loyalty pool resolves into a stream of single-card
    effects (a +1 draws a card, a -3 bounces a permanent, a -X deals X
    damage). Each useful activation is, on average, worth one card's
    worth of clock impact — the same primitive `card_clock_impact`
    already exports, scaled by `opp_life` to convert "fraction of
    kill per turn" back into "power-equivalent units" that compose
    cleanly with `EVSnapshot.persistent_power`.

    Composition (all from existing clock primitives, no new constants):

        per_tick_power = card_clock_impact(snap) × opp_life
                       = AVG_CREATURE_POWER × castable_fraction

    The total pool is `activations × per_tick_power`. Read by callers
    as `persistent_power` so the `urgency_factor` term in
    `position_value` decays the credit as opp's clock tightens — a
    loyalty pool we never get to activate is worth zero, the same
    way a Bombardment we never get to sac into is worth zero.

    Args:
        activations: Expected useful activations across the pool's
            residency (caller computes from loyalty / opp_threat).
        snap: Current EVSnapshot for clock context.

    Returns:
        Power-equivalent contribution suitable to add to
        `EVSnapshot.persistent_power`.
    """
    if activations <= 0:
        return 0.0
    opp_life = max(1, snap.opp_life)
    per_tick_power = card_clock_impact(snap) * opp_life
    return activations * per_tick_power


# ─────────────────────────────────────────────────────────────
# Position value — the unified board evaluation
# ─────────────────────────────────────────────────────────────

def position_value(snap: "EVSnapshot") -> float:
    """Unified board evaluation. Replaces 4 archetype-specific evaluators.

    Returns clock differential + resource advantage.
    Higher = better position for the player.

    Phase 2 refactor: the prior `archetype` parameter and the
    `min(my_clock, combo_clock(snap))` override are removed.  Combo
    decks express their proximity to a win through per-deck gameplan
    data and LLM-scored weights at the call-site layer (e.g.
    ``ai.ev_evaluator.compute_play_ev``'s combo-chain branch and
    ``ai.combo_calc.assess_combo``).  Removing the override here
    keeps ``position_value`` mechanic-driven and archetype-agnostic —
    every Modern card hits the same code path regardless of which
    deck is currently controlling it.
    """
    # Dead check
    if snap.my_life <= 0:
        return -100.0
    if snap.opp_life <= 0:
        return 100.0

    # Combat clocks — use on-board power only. Persistent (recurring-
    # trigger) tokens are credited further below as an additive
    # position-value term, NOT through combat_clock. Clock is
    # nonlinear in power (opp_life / power), so stuffing expected-
    # future tokens into it compounds the bonus — see
    # docs/proposals/recurring_token_ev.md §5 risk note.
    my_clock = combat_clock(
        snap.my_power, snap.opp_life,
        snap.my_evasion_power, snap.opp_toughness
    )
    opp_clock = combat_clock(
        snap.opp_power, snap.my_life,
        snap.opp_evasion_power, snap.my_toughness
    )

    # Clock differential: positive = I'm winning the race
    clock_diff = opp_clock - my_clock

    # Normalize clock diff to prevent extreme values when one clock is NO_CLOCK
    if my_clock >= NO_CLOCK and opp_clock >= NO_CLOCK:
        clock_diff = 0.0  # neither player has a clock — stalled
    elif my_clock >= NO_CLOCK:
        # I have no clock, opponent does → I'm losing; worse as opp gets
        # faster. Mirror of the winning branch below: saturating in the
        # opponent's turns-to-lethal, so a SLOWER opposing clock (a blocker
        # deployed, a threat removed) is never a worse position than a
        # faster one. The prior `-opp_clock` was the inverse of this
        # comment (a 50-turn clock scored -50, a 1-turn clock -1); it
        # priced every defensive play on a creatureless board as a
        # downgrade and projected a zero-power mana creature at -34.
        # docs/diagnostics/2026-08-30_clock_sign_inversion_fix_falsified.md
        # confirms the defect and falsifies only the prediction that
        # repairing it (together with the sentinel cliff) lifts
        # creature-light control; this is the sign half alone.
        clock_diff = -CLOCK_LETHAL_ADVANTAGE_CAP / opp_clock
    elif opp_clock >= NO_CLOCK:
        # Opponent has no clock, I do → I'm winning; better as I get faster.
        # Invert: lower my_clock = bigger advantage. CLOCK_LETHAL_ADVANTAGE_CAP
        # caps the differential near Modern starting life when I have lethal.
        clock_diff = CLOCK_LETHAL_ADVANTAGE_CAP / my_clock

    # Resource advantage: cards and mana as future clock changes
    card_diff = snap.my_hand_size - snap.opp_hand_size
    card_value = card_clock_impact(snap) * card_diff

    mana_diff = snap.my_mana - snap.opp_mana
    mana_value = mana_clock_impact(snap) * max(0, mana_diff)

    # Survival margin: how comfortable is my life total?
    survival = life_as_resource(snap.my_life, snap.opp_power)
    opp_survival = life_as_resource(snap.opp_life, snap.my_power)
    life_advantage = survival - opp_survival

    # Lifelink: extends survival
    if snap.my_lifelink_power > 0 and snap.opp_power > 0:
        lifelink_turns = snap.my_lifelink_power / max(1, snap.opp_power)
        life_advantage += lifelink_turns * LIFELINK_LIFE_GAIN_WEIGHT

    # Persistent (recurring-trigger token) power contribution.
    # Expected damage = persistent_power × urgency_factor (fraction of
    # residency we actually survive). Converted to life-point units via
    # mana_clock_impact × CLOCK_IMPACT_LIFE_SCALING — same scale the
    # clock_diff / card_value / mana_value terms use. No clock non-
    # linearity; linear additive.
    persistent_value = (snap.persistent_power * snap.urgency_factor
                        * mana_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING)

    # Artifact-count resource (design: docs/design/ev_correctness_overhaul.md §4).
    # Each artifact is worth roughly +1 virtual power to decks that
    # actually scale with artifact count: "+1/+0 per artifact" equipment,
    # affinity cost reduction, metalcraft activation.  Gated on the
    # scaling_active flag so Zoo / Burn / generic decks never accrue
    # this bonus.  Differential formulation (my − opp) mirrors clock_diff.
    artifact_diff = 0
    if snap.my_artifact_scaling_active:
        artifact_diff += snap.my_artifact_count
    if snap.opp_artifact_scaling_active:
        artifact_diff -= snap.opp_artifact_count
    # Convert each marginal artifact into life-point units via the same
    # mana_clock_impact × CLOCK_IMPACT_LIFE_SCALING used by card_value /
    # mana_value — a rules-derived "value per power point" rather than a
    # tuning constant.
    artifact_value = (artifact_diff * mana_clock_impact(snap)
                      * CLOCK_IMPACT_LIFE_SCALING)

    return (clock_diff + card_value + mana_value + life_advantage
            + persistent_value + artifact_value)


# ─────────────────────────────────────────────────────────────
# Life-phase classifier — pure composition primitive (W0-B)
# ─────────────────────────────────────────────────────────────


class LifePhase(enum.Enum):
    """Coarse game-state phase, derived from clock + life primitives.

    Replaces scattered magic-life-threshold conditionals (control
    side "below 5 = panic", combo "life ≤ Bolt-zone", aggro "race
    math") with a single composer that callers in `ev_player.py`,
    `ev_evaluator.py`, `response.py` consult to gear-shift behaviour.

    Phases in increasing order of urgency:

    - DEVELOP — early/comfortable. Either we are still in the early-
      game window (per `is_early_game`) or my life buffer is at
      least as long as the opponent's. Safe to deploy proactive
      resources (lands, mana rocks, value engines).
    - GRIND  — both sides have committed clocks, neither racing
      decisively. Trade resources, hold removal, value over tempo.
    - PANIC  — I am losing the race in absolute terms: my
      `life_as_resource` buffer is strictly less than the opponent's.
      Gear-shift: tighten chump rules, deploy reactive cards now,
      stop holding counterspells for hypothetical threats.
    - LETHAL — opp has on-board lethal at next combat
      (`am_dead_next`). Every Wave-1 caller that consults this
      enum is expected to fold to this phase first.

    The four phases form a total ordering on
    `(am_dead_next, buffer_deficit, is_early_game)` — see `life_phase`.
    """

    DEVELOP = "develop"
    GRIND = "grind"
    PANIC = "panic"
    LETHAL = "lethal"


def life_phase(snap: "EVSnapshot") -> LifePhase:
    """Classify the snapshot into one of four life phases.

    Pure composition — every comparison routes through an existing
    primitive (`EVSnapshot.am_dead_next`, `is_early_game`,
    `life_as_resource`). No numeric thresholds are introduced here.

    Ordering rule (most urgent wins):

    1. LETHAL  — `snap.am_dead_next` (opp_power >= my_life > 0).
       Single-predicate gate; nothing else can override it.
    2. PANIC   — past the development window AND my life buffer
       (`life_as_resource(my_life, opp_power)`) is strictly less
       than the opponent's (`life_as_resource(opp_life, my_power)`).
       The buffer comparison is symmetric and free of literals: both
       sides use the same primitive applied to mirrored arguments.
    3. GRIND   — past the development window, neither LETHAL nor
       PANIC. Both sides have committed clocks (`is_early_game` is
       False) and my buffer is not strictly less than the opponent's.
    4. DEVELOP — fallback. Either `is_early_game` (both sides'
       clocks still exceed the early-game threshold, which is itself
       derived in `EARLY_GAME_CLOCK_THRESHOLD`) or my buffer is at
       least as long as the opponent's.

    All four arms reduce to the same shape: a comparison between two
    already-derived values, or a call to an already-derived predicate.
    """
    if snap.am_dead_next:
        return LifePhase.LETHAL

    if is_early_game(snap):
        return LifePhase.DEVELOP

    my_buffer = life_as_resource(snap.my_life, snap.opp_power)
    opp_buffer = life_as_resource(snap.opp_life, snap.my_power)

    if my_buffer < opp_buffer:
        return LifePhase.PANIC

    return LifePhase.GRIND


# ─────────────────────────────────────────────────────────────
# Block-assignment scorer — single-formula post-state evaluation
# ─────────────────────────────────────────────────────────────


def score_block_assignment(
    snap: "EVSnapshot",
    *,
    my_life_after: int,
    opp_power_after: int,
    my_power_after: int,
) -> float:
    """Score a hypothetical post-combat state by life-as-resource
    *buffer differential* — my survival turns minus opp's survival
    turns.

    Replaces the chump / trade / favorable-trade enum-of-reasons
    that previously gated block selection.  The choice between
    "chump", "even trade", and "favorable trade" is **derived**
    from the same single formula:

        score = life_as_resource(my_life_after, opp_power_after)
              - life_as_resource(opp_life, my_power_after)

    All three previously-named cases reduce to comparing scores
    across hypothetical post-states:

      - Chump (blocker dies, attacker stays):
            my_life_after = my_life - 0
            opp_power_after = opp_power (attacker survives)
            my_power_after = my_power - blocker_power (blocker died)
      - Trade (both die):
            my_life_after = my_life - 0
            opp_power_after = opp_power - attacker_power
            my_power_after = my_power - blocker_power
      - Favorable trade (only attacker dies):
            my_life_after = my_life - 0
            opp_power_after = opp_power - attacker_power
            my_power_after = my_power
      - No block:
            my_life_after = my_life - attacker_power
            opp_power_after = opp_power
            my_power_after = my_power

    Each option is one input to this function; the caller picks the
    option with the maximum score.  No new numeric literals are
    introduced — both terms compose ``life_as_resource``.  ``opp_life``
    is the only field of ``snap`` we read directly because opp's
    life is not affected by the block decision (they're attacking,
    not blocking) — every other coordinate comes from the caller's
    post-state.
    """
    my_buffer = life_as_resource(my_life_after, opp_power_after)
    opp_buffer = life_as_resource(snap.opp_life, my_power_after)
    return my_buffer - opp_buffer
