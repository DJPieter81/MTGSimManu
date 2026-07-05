"""Archetype Strategy Profiles — streamlined AI decision parameters.

Most AI scoring is now clock-based (ai/clock.py + projection in ev_evaluator.py).
StrategyProfile retains only the parameters that can't be derived from
game mechanics: combo sequencing flags, burn targeting preferences,
land scoring weights, cycling/pump config, and pass thresholds.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Dict, Iterable, Tuple

if TYPE_CHECKING:
    from ai.clock import LifePhase


class ArchetypeStrategy(Enum):
    AGGRO = "aggro"
    MIDRANGE = "midrange"
    CONTROL = "control"
    COMBO = "combo"
    TEMPO = "tempo"
    RAMP = "ramp"


DECK_ARCHETYPES = {
    "Boros Energy":       ArchetypeStrategy.AGGRO,
    "Jeskai Blink":       ArchetypeStrategy.TEMPO,
    "Ruby Storm":         ArchetypeStrategy.COMBO,
    "Affinity":           ArchetypeStrategy.AGGRO,
    "Eldrazi Tron":       ArchetypeStrategy.RAMP,
    "Amulet Titan":       ArchetypeStrategy.COMBO,
    "Goryo's Vengeance":  ArchetypeStrategy.COMBO,
    "Domain Zoo":         ArchetypeStrategy.AGGRO,
    "Living End":         ArchetypeStrategy.COMBO,
    "Dimir Midrange":     ArchetypeStrategy.MIDRANGE,
    "Izzet Prowess":      ArchetypeStrategy.AGGRO,
    "4c Omnath":          ArchetypeStrategy.MIDRANGE,
    "4/5c Control":       ArchetypeStrategy.CONTROL,
    "Azorius Control":    ArchetypeStrategy.CONTROL,
    "Azorius Control (WST)": ArchetypeStrategy.CONTROL,
    "Azorius Control (WST v2)": ArchetypeStrategy.CONTROL,
    "Pinnacle Affinity":  ArchetypeStrategy.AGGRO,
}


@dataclass
class StrategyProfile:
    """AI decision parameters that can't be derived from clock mechanics.

    Clock-based projection handles creature/removal/burn/draw scoring.
    What remains: combo sequencing, burn mode, land/cycling config,
    combat thresholds, and mulligan rules.
    """

    # ── Burn targeting ──
    burn_face_mult: float = 0.5           # face burn value multiplier (0 for control)
    burn_face_low_life_mult: float = 1.0  # multiplier when opp is low
    burn_kill_min_power: int = 4          # min power to prefer killing over face
    burn_kill_life_ratio: float = 2.0     # kill if life > damage × this
    burn_low_life_threshold: int = 10     # opp life ≤ this for burn bonuses

    # ── Mana holdback ──
    holdback_applies: bool = True         # whether to hold mana for instants

    # ── Combat ──
    attack_threshold: float = -0.5        # slightly negative: attack when trades are close
    # Aggressive-closing flag in numeric form: how much to lower the EV
    # threshold for "go for it" attacks when opp is at lethal range.
    # Default 0.0 = the profile does not race; subtracting 0 is a no-op.
    # AGGRO and TEMPO opt in by overriding to a positive value. This
    # replaces the prior ``self.archetype in ('aggro', 'tempo')`` gate
    # at the call site, so the predicate lives on the profile data and
    # the ev_player code is archetype-name-agnostic.
    aggro_closing_threshold_reduction: float = 0.0
    # Opp won't block with creatures whose power exceeds attacker.power * this ratio
    # (trading up forfeits offensive value the bigger creature would generate).
    block_threat_power_ratio: float = 2.0

    # ── Mulligan ──
    mulligan_always_keep: int = 5
    mulligan_bad_land_count: int = 6

    # ── Board assessment thresholds ──
    big_creature_power: int = 4           # "big creature" for removal priority
    dying_opp_power: int = 3              # opp_power >= this + clock ≤ 3 = dying
    dying_opp_clock: int = 3

    # ── Removal ──
    removal_overkill_cmc_diff: int = 2
    removal_overkill_mult: float = 0.6

    # ── Combo chain ──
    has_combo_chain: bool = False
    storm_patience: bool = False
    storm_min_fuel_to_go: int = 2
    # Expected probability that a "fuel" spell (ritual / cantrip / draw)
    # in hand will successfully contribute +1 storm copy to a chain this turn,
    # accounting for counters, mana constraints, and draw variance. Used
    # in ev_player.py to derive the hold-vs-fire penalty for storm finishers:
    #   hold_benefit = (fuel_available / opp_life) × LETHAL_VALUE × chain_p
    # where LETHAL_VALUE=100.0 (winning a game) is the rules-derived scaling
    # already used at the lethal-reward line in the same function.
    # Default 0.0 = profile doesn't chain combos (STORM overrides to empirical ~0.4).
    storm_chain_continuation_p: float = 0.0

    # ── Control patience ──
    # When True, reactive-only spells are suppressed in main phase unless
    # the AI is actually dying (snap.am_dead_next or opp_clock <= dying
    # thresholds). Mirrors `storm_patience` — same gate-suppression idea
    # but for control archetypes that should hold up mana for the
    # opponent's turn instead of casting Verdict/Chant on an empty board.
    control_patience: bool = False

    # ── Burn target comparison ──
    # Multiplier applied to a creature target's clock-derived value when
    # comparing against face-burn EV. >1.0 means burn-removal is preferred
    # over face damage at parity, reflecting that a removed creature
    # avoids future damage while face damage is one-shot.
    creature_value_mult: float = 1.5

    # ── Pump (Psychic Frog etc.) ──
    pump_uncastable_cmc_buffer: int = 2
    pump_extra_lands_threshold: int = 5
    pump_max_discards: int = 2

    # ── (Deleted in M3) Pass threshold ──
    # The binary `pass_threshold` gate has been removed.  The AI now
    # compares signed `play_value = ev - holdback_cost(snap)` against
    # zero, where `holdback_cost` is positive when held interaction +
    # active opp threats argue for keeping mana open and NEGATIVE
    # (=> bonus) when no defensive use exists.  See
    # `ai/ev_player.py::_holdback_penalty` for the signed model and
    # `docs/history/audits/2026-05-16_5panel_bo3_audit.md` (control
    # panel) for the binary-gate trap that motivated removal.


# ═══════════════════════════════════════════════════════════════════
# Archetype profiles — only override what differs from defaults
# ═══════════════════════════════════════════════════════════════════

AGGRO = StrategyProfile(
    burn_face_mult=1.5,
    burn_face_low_life_mult=2.5,
    holdback_applies=False,
    attack_threshold=-1.0,
    aggro_closing_threshold_reduction=2.0,
)

MIDRANGE = StrategyProfile()

CONTROL = StrategyProfile(
    burn_face_mult=0.0,
    control_patience=True,
)

COMBO = StrategyProfile(
    has_combo_chain=True,
    holdback_applies=False,
)

STORM = StrategyProfile(
    has_combo_chain=True,
    storm_patience=True,
    storm_min_fuel_to_go=2,
    # 0.4 = preserves existing 40.0 effective coefficient (old magic number)
    # when multiplied by LETHAL_VALUE=100.0 in ev_player.py storm finisher gate.
    # Back-of-envelope: chain disruption from counters + top-deck variance in
    # 60-card deck averages ~40-50% success per incremental fuel card.
    storm_chain_continuation_p=0.4,
    holdback_applies=False,
)

RAMP = StrategyProfile(
    holdback_applies=False,
)

TEMPO = StrategyProfile(
    aggro_closing_threshold_reduction=2.0,
)


PROFILES = {
    "aggro": AGGRO,
    "midrange": MIDRANGE,
    "control": CONTROL,
    "combo": COMBO,
    "storm": STORM,
    "ramp": RAMP,
    "tempo": TEMPO,
}

# Per-deck archetype overrides (deck_name -> archetype key in PROFILES)
DECK_ARCHETYPE_OVERRIDES = {
    "Ruby Storm": "storm",
}


def get_profile(archetype: str) -> StrategyProfile:
    """Get the strategy profile for an archetype."""
    return PROFILES.get(archetype, MIDRANGE)


# ═══════════════════════════════════════════════════════════════════
# Phase-weights table — life-phase-aware EV re-weighting (M4)
# ═══════════════════════════════════════════════════════════════════
#
# Per `docs/history/audits/2026-05-16_5panel_bo3_audit.md` §M4
# (3/5 panel consensus, P0):
#
#   `goal=close_game` was inert.  The gameplan flipped the label
#   (`grind_value → close_game`) but `compute_play_ev` returned the
#   same EV regardless of life phase, so Dimir at 9 life tapped out
#   for Psychic Frog into open Thraben Charm mana → died, and
#   Azorius at 3 life cast 5-CMC Teferi (no body) → died.
#
# Mechanism (purely declarative, no code branches):
#
#   `compute_play_ev` consults `ai.clock.life_phase(snap)` then
#   looks up `phase_weights[archetype][phase]` — a dict of card-tag
#   → multiplier.  For each tag on the candidate card, the
#   corresponding multiplier is composed into the running product.
#   Unmatched tags / phases / archetypes default to the identity
#   weight `IDENTITY_PHASE_WEIGHTS` (1.0), so the lookup is a total
#   function that NEVER raises and NEVER nullifies an EV signal
#   (every leaf is strictly positive).
#
# Knowledge location: card-specific knowledge stays in oracle text +
# tag classification.  This table re-weights ALREADY-EXISTING tag
# buckets — it does not introduce new card-level data.  The tags
# we reference here (`removal`, `board_wipe`, `counterspell`,
# `lifegain`, `lifelink`, `cantrip`, `card_advantage`, `finisher`)
# are all populated by `ai/predicates.py` / `engine/oracle_parser.py`
# from oracle text.
#
# Direction (not magnitude) is the contract; the magnitudes here are
# starting points the matrix can tune.  Tests in
# tests/test_panic_gear_at_lethal_minus_one.py pin only the
# directional rules (defensive > non-defensive at PANIC; identity at
# DEVELOP; identity for unknown archetypes), so changing 1.5 → 1.8
# does not require a test edit.

# magic-allow: identity multiplier is a rules constant (multiplying
# by 1.0 is a no-op).  Exposed at module scope so callers can spell
# the no-op explicitly.
IDENTITY_PHASE_WEIGHTS: float = 1.0


def _build_phase_weights():
    """Construct the phase_weights table.

    Wrapped in a function so the `ai.clock.LifePhase` import is
    lazy — `strategy_profile.py` is imported by many call sites and
    we don't want a circular-import risk with `ai.clock`.
    """
    from ai.clock import LifePhase

    # Defensive tag bucket — cards whose role is to slow the
    # opponent down or refill life.  These get up-weighted at PANIC
    # because they directly address the failing race.
    defensive_panic = {
        "removal": 1.5,
        "board_wipe": 1.5,
        "counterspell": 1.4,
        "lifegain": 1.5,
        "lifelink": 1.4,
    }

    # Proactive / non-defensive bucket — cards that develop our
    # board or refill our hand.  These get down-weighted at PANIC
    # because they don't move the failing-race needle this turn.
    proactive_panic_down = {
        "cantrip": 0.7,
        "card_advantage": 0.8,
    }

    # CONTROL — most extreme gear-shift; control's gameplan is
    # explicit "stay alive, win late".  At PANIC, lean hard into
    # defensive cards and away from card-draw value engines.
    control_weights = {
        LifePhase.PANIC: {**defensive_panic, **proactive_panic_down},
    }

    # MIDRANGE — gear-shift to defense at PANIC but less extreme;
    # midrange's plan retains some proactive value at low life.
    midrange_weights = {
        LifePhase.PANIC: {
            "removal": 1.4,
            "board_wipe": 1.4,
            "counterspell": 1.3,
            "lifegain": 1.4,
            "lifelink": 1.3,
            "cantrip": 0.8,
        },
    }

    # AGGRO — at PANIC the aggro deck is losing the race; up-weight
    # any reach (finisher / burn) and lifegain (sustains the clock).
    # Down-weight cantrips that don't deal damage.
    aggro_weights = {
        LifePhase.PANIC: {
            "finisher": 1.3,
            "lifegain": 1.3,
            "lifelink": 1.3,
            "cantrip": 0.8,
        },
    }

    return {
        "control": control_weights,
        "midrange": midrange_weights,
        "aggro": aggro_weights,
    }


phase_weights: Dict[str, Dict["LifePhase", Dict[str, float]]] = \
    _build_phase_weights()


def phase_weight_multiplier(
    archetype: str,
    phase: "LifePhase",
    tags: Iterable[str],
) -> float:
    """Return the EV multiplier for a card with `tags` at `phase`.

    Pure lookup over the `phase_weights` table.  The result is the
    product of every weight at `phase_weights[archetype][phase][tag]`
    for each tag in `tags`; tags not in the table contribute the
    identity multiplier (1.0).

    Total function: misses on `archetype`, `phase`, or any individual
    `tag` all fall through to `IDENTITY_PHASE_WEIGHTS`.  Never raises;
    never returns 0.0 or a negative number (the table enforces
    strictly positive weights).

    Composition is multiplicative (not max/sum) because a card with
    BOTH a defensive tag and a cantrip tag should net out — the
    panic bonus and the cantrip penalty multiply to a near-identity.
    """
    per_phase = phase_weights.get(archetype, {})
    by_tag = per_phase.get(phase, {})
    if not by_tag:
        return IDENTITY_PHASE_WEIGHTS
    multiplier = IDENTITY_PHASE_WEIGHTS
    for tag in tags:
        multiplier *= by_tag.get(tag, IDENTITY_PHASE_WEIGHTS)
    return multiplier


# ═══════════════════════════════════════════════════════════════════
# Sign-aware preference application (A2 fix, 2026-07-05 probe)
# ═══════════════════════════════════════════════════════════════════
#
# The M4 gear-shift originally applied its weights as `ev *= w`.
# That is correct only on the positive half-line: for a play whose
# projection EV is NEGATIVE (the typical spot-removal "trade a card
# for a small creature" shape), an up-weight w > 1 makes the EV MORE
# negative — the defensive play the PANIC row exists to promote gets
# DEMOTED, falls below `PLAY_VALUE_FLOOR`, and the AI passes the turn.
# That is the exact s60104 "pass Main 1 at 2 life / landcycle-only
# turns" pattern from finding A2 (re-confirming audit M4/M3).
#
# `apply_preference_weight` restores the intended semantics: a weight
# is a PREFERENCE re-ranking, monotone-increasing in w for every
# fixed EV — gains are amplified (ev × w), costs are discounted
# (ev ÷ w).  Identity at w = 1; never flips the sign of the EV.
# Pinned by tests/test_panic_gearshift_reaches_play_selection.py.


def apply_preference_weight(ev: float, weight: float) -> float:
    """Apply a preference weight to a signed EV.

    Monotone-increasing in `weight` for every fixed `ev`:

      - ev >= 0 → ev × weight  (a bonus amplifies the gain,
                                a penalty shrinks it)
      - ev <  0 → ev ÷ weight  (a bonus discounts the cost,
                                a penalty inflates it)

    d/dw (ev·w) = ev ≥ 0 and d/dw (ev/w) = −ev/w² > 0 for ev < 0,
    so "up-weight" always means "strictly more attractive" and
    "down-weight" always means "strictly less attractive", on both
    sides of zero.  The sign of the EV is never flipped — a weight
    re-ranks plays, it does not turn a cost into a gain.

    `weight` must be strictly positive (the weight tables enforce
    positive finite leaves; see the table shape tests).
    """
    if ev >= 0.0:
        return ev * weight
    return ev / weight


# ═══════════════════════════════════════════════════════════════════
# Goal-weights table — goal-aware EV re-weighting (M4 part 3 / A2)
# ═══════════════════════════════════════════════════════════════════
#
# Per `docs/history/audits/2026-05-16_5panel_bo3_audit.md` §M4, third
# mechanism: per-(archetype, goal) signal weights so that the
# GoalEngine's `close_game` gear literally re-weights finisher-role
# plays up and cantrip/value plays down.  Without this table the goal
# flip (`grind_value → close_game`) changed a label and nothing else —
# finding A2 in the 2026-07-05 calibration probe.
#
# Mechanism (purely declarative, mirrors `phase_weights`):
#
#   `compute_play_ev` receives the current goal's `GoalType.value`
#   from `ev_player._score_spell` and looks up
#   `goal_weights[archetype][goal_value]` — a dict of card-tag →
#   weight, composed into the same sign-aware preference application
#   as the life-phase weights.  Misses on archetype / goal / tag (and
#   a `None` goal for players without a goal engine) all fall through
#   to the identity weight, so the lookup is a total function.
#
# Knowledge location: the FINISHER signal comes from gameplan card
# roles (`decks/gameplans/*.json` `card_roles.finishers`/`payoffs`),
# surfaced by `ev_player` as the synthetic tag `ROLE_FINISHER_TAG` —
# card-specific knowledge stays in gameplan data, never in code.
# The remaining tags (`cantrip`, `card_advantage`) are the same
# oracle-derived buckets `phase_weights` uses.
#
# Direction (not magnitude) is the contract; tests in
# tests/test_close_game_upweights_finisher_over_cantrip.py pin only
# the directional rules (finisher > 1 > cantrip at close_game for
# proactive-closing archetypes; identity everywhere else; identity
# for storm/combo whose cantrips ARE the finisher-access path).

# Synthetic role tag injected by ev_player for cards declared in the
# gameplan's `finishers` / `payoffs` role buckets.  Namespaced with
# `role:` so it can never collide with an oracle-derived tag.
ROLE_FINISHER_TAG: str = "role:finisher"


def _build_goal_weights():
    """Construct the goal_weights table (pure data).

    Only `close_game` rows are declared: the other goals keep identity
    weights — their behaviour is already goal-appropriate via the
    projection (INTERACT holds mana, GRIND_VALUE values card
    advantage) and adding rows without audit evidence would be
    speculative tuning.

    Magnitudes mirror the PANIC row of `phase_weights` (1.5 bonus /
    0.7 penalty families) — same re-ranking scale, same direction-only
    test contract.
    """
    # CONTROL — the A2 exemplar (s60104): once the plan says "convert
    # advantage into a win", deploying the declared win condition must
    # outrank another cycle of card selection.
    close_game_control = {
        ROLE_FINISHER_TAG: 1.5,
        "cantrip": 0.7,
        "card_advantage": 0.85,
    }

    # MIDRANGE — the audit's other exemplar (Dimir grinding past its
    # window): same direction, milder — midrange finishers double as
    # value pieces so the gear-shift needs less correction.
    close_game_midrange = {
        ROLE_FINISHER_TAG: 1.4,
        "cantrip": 0.8,
    }

    # AGGRO — close_game means push the finisher/reach play; cantrips
    # that don't affect the board are a tempo loss in the closing turn.
    close_game_aggro = {
        ROLE_FINISHER_TAG: 1.4,
        "cantrip": 0.8,
    }

    # NOTE: no rows for "storm" / "combo" — their cantrips and rituals
    # are chain fuel (finisher ACCESS), and down-weighting them at
    # close_game would re-create the "rituals penalised mid-chain" P1.
    # Pinned by test_storm_chain_fuel_unaffected_by_close_game.
    from ai.gameplan import GoalType

    close = GoalType.CLOSE_GAME.value
    return {
        "control": {close: close_game_control},
        "midrange": {close: close_game_midrange},
        "aggro": {close: close_game_aggro},
    }


goal_weights: Dict[str, Dict[str, Dict[str, float]]] = _build_goal_weights()


def goal_weight_multiplier(
    archetype: str,
    goal_type: "str | None",
    tags: Iterable[str],
) -> float:
    """Return the EV multiplier for a card with `tags` under `goal_type`.

    Pure lookup over the `goal_weights` table — the goal-axis twin of
    `phase_weight_multiplier`.  `goal_type` is the `GoalType.value`
    string of the caller's current goal (or None when the player has
    no goal engine).

    Total function: misses on `archetype`, `goal_type` (including
    None), or any individual tag fall through to the identity weight.
    Composition is multiplicative across tags, same rationale as the
    phase lookup.
    """
    if goal_type is None:
        return IDENTITY_PHASE_WEIGHTS
    by_tag = goal_weights.get(archetype, {}).get(goal_type, {})
    if not by_tag:
        return IDENTITY_PHASE_WEIGHTS
    multiplier = IDENTITY_PHASE_WEIGHTS
    for tag in tags:
        multiplier *= by_tag.get(tag, IDENTITY_PHASE_WEIGHTS)
    return multiplier
