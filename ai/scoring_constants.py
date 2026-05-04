"""Centralized scoring constants for the AI layer.

Each constant is justified by a docstring explaining its derivation.
Sister constants (LETHAL_THREAT / NEAR_LETHAL_CUTOFF, the held-* family)
live in the same section so re-tuning one prompts review of the other.

Convention: rules constants only.  Per-card or per-deck values belong
in `decks/gameplans/*.json`, not here.  Per-archetype values belong in
`ai/strategy_profile.py`, not here.

Audit hook: this file is the single point of review for any future
coefficient re-tune.
"""
from __future__ import annotations

from typing import Dict

# ─── Threat-evaluation sentinels ─────────────────────────────────────
# Used by ai/response.py to score stack threats and gate counter triage.

LETHAL_THREAT: float = 100.0
"""Sentinel value for a stack item that, if it resolves, kills us this turn.

Pinned at the top of the threat scale so any spell with a credible
lethal projection outranks any non-lethal threat in counter selection.
Used by `evaluate_stack_threat` in `ai/response.py` for known burn that
exceeds our remaining life.
"""

NEAR_LETHAL_CUTOFF: float = 50.0
"""Half of LETHAL_THREAT — the threshold above which counter-triage
suspends and the counter must fire regardless of whether a flash creature-
removal could answer the same threat post-resolution.

Above this threshold no held-counter future EV can outweigh "we lose now".
Derivation: ½ × LETHAL_THREAT.  Re-tune in lockstep with LETHAL_THREAT.
"""

# ─── Held-interaction preservation values ────────────────────────────
# Used by ai/ev_player.py (holdback) and ai/mana_planner.py (fetch).
# These two constants describe the same underlying quantity from
# different angles: the value of holding interaction in hand.

HELD_RESPONSE_VALUE_PER_CMC: float = 4.0
"""Per-CMC value of held interaction (counterspells / removal) that may
be lost when a main-phase tap-out forfeits response capacity.

Scale used by `_held_response_penalty` in `ai/ev_player.py` as
    counter_count × counter_cmc × opp_threat_prob × HELD_RESPONSE_VALUE_PER_CMC

Iteration-2 B3-Tune: coefficient lowered 7.0 → 4.0.  The Bundle-3 value
of 7.0 was calibrated against 2× Counterspell held (2×2×1×7 = 28, gates
a +20 EV play), but the single-counter case (1×2×1×7 = 14) floored
ordinary main-phase plays, triggering a measurable defender-collapse
in N=50 matrix (Jeskai -5pp, Dimir -6pp, AzCon WST -8pp after the
surrounding Affinity session fixes shipped).  4.0 is derived from
CONTROL's pass_threshold = -5.0: with 1 counter × 2 CMC × threat_prob
1.0 × 4.0 = -8 the gate still blocks a +5 main-phase play, but a
+10 draw engine (EV 10 − 8 = +2 > -5.0) remains castable.
2× Counterspell still scales to 2×2×1×4 = -16 which keeps the
Bundle-3 intent intact.

Now exposed as the BASE / floor of `held_response_value_per_cmc(p)` —
the function-form below scales this up against artifact-heavy
opponents (Affinity-class) where the held counter is the only stack-
side answer.  Existing call sites that read the flat constant still
behave as if facing the average opponent (p_artifact_threat = 0.0).

Sister constant: HELD_COLOR_PRESERVATION_BONUS — same "held interaction
is worth keeping castable" intent, applied at fetchland decision time.
"""


HELD_RESPONSE_VALUE_PER_CMC_ARTIFACT_RAMP: float = 4.0
"""Additive ramp for `held_response_value_per_cmc(p)`: the per-CMC
value increases from a base of 2.0 toward 2.0 + RAMP = 6.0 as
`bhi.beliefs.p_artifact_threat` saturates.  Floored at the Iter-2
base (4.0), so the function reduces to identity for low-artifact
opponents.

Derivation: AzCon vs Affinity (`docs/diagnostics/2026-05-01_azcon_followup.md`)
showed a single-counter holdback at 1×2×1.0×4.0 = -8 was insufficient
to gate a +7.5 Teferi tap-out (net -0.5, above CONTROL's pass_threshold
of -5.0).  At p_artifact_threat ≈ 1.0 the function returns 6.0,
yielding 1×2×1.0×6.0 = -12 (net -4.5) — still inside the gate band
but bringing the play within reach of the threshold.  Affinity-class
matchups depend on this ramp; non-artifact matchups stay at the floor.
"""


def held_response_value_per_cmc(p_artifact_threat: float = 0.0) -> float:
    """Per-CMC value of held interaction, scaled by the artifact-threat
    density of the opponent.

    Formula:
        max(HELD_RESPONSE_VALUE_PER_CMC,
            2.0 + p_artifact_threat * HELD_RESPONSE_VALUE_PER_CMC_ARTIFACT_RAMP)

    For non-artifact opponents (p ≈ 0) the floor binds and the
    function returns the Iter-2 base (4.0).  For Affinity-class
    opponents (p ≈ 1) it ramps to 6.0.  At the midpoint (p = 0.5) the
    linear term equals the floor, so mixed opponents see no change.
    """
    return max(HELD_RESPONSE_VALUE_PER_CMC,
               2.0 + p_artifact_threat * HELD_RESPONSE_VALUE_PER_CMC_ARTIFACT_RAMP)

HELD_COLOR_PRESERVATION_BONUS: float = 8.0
"""Bonus applied to a fetchland candidate that *provides* a color the
player currently holds an instant / flash spell of, when no existing
untapped source covers that color.

Used by `score_land` in `ai/mana_planner.py`.

Derivation: matches the per-demand weight in block (A) of the same
scoring function (8.0 per enabled spell) — held interaction is worth
the same as the spell it protects being castable.

Sister constant: HELD_RESPONSE_VALUE_PER_CMC — same "held interaction
is worth keeping castable" intent, applied at tap-out decision time.
"""

# ─── Spot-removal timing constants ───────────────────────────────────
# Used by ai/ev_player.py to defer cheap removal when BHI predicts a
# higher-EV target arriving within the next few turns.

REMOVAL_DEFERRAL_TARGET_GAP: float = 4.0
"""Expected ``creature_threat_value`` gap between a low-tier current
target and the higher-tier future target the deferral term anticipates.

Derivation (matches creature_threat_value's mid-game scale, no magic
number):

    A 1-power vanilla body on a typical Modern board scores ~1 in
    ``creature_threat_value`` (Memnite ≈ 1.15 with the snap defaults).
    A "premium threat" — a battle-cry / equipped / scaling creature
    with effective power ~3-4 plus virtual-power amplifiers — scores
    ~5-6 (Signal Pest under Plating, equipped Ornithopter, Construct
    token).  The gap is therefore ~4 in the same units used by every
    other removal-side scoring overlay (`_score_spell` adds
    `premium * 0.5` for the threat-premium term, where `premium` is
    in this exact `creature_threat_value` unit space).

The deferral penalty is computed as ``p_better * GAP`` so that a 1.0
probability of a higher-threat arrival reduces the cheap-removal score
by exactly one threat-tier — enough to let a non-removal alternative
outrank a 1-mana burn against a vanilla body, but not enough to gate
removal when the current target is already a premium threat
(`p_better → 0` against the top of the deck profile).

Sister constant: BATTLE_CRY_AMPLIFIER_VP (in ai/ev_evaluator.py) — the
same +2 virtual-power rule that produces the threat-value gap this
constant anticipates.
"""

# ─── Opp-threat-probability primitives ──────────────────────────────
# Used by ai/ev_player.py::_estimate_opp_threat_prob to derive
# P(opp deploys a follow-up threat next turn) from the opp's pool
# composition rather than a flat coefficient on raw hand size.

STARTING_HAND_SIZE: int = 7
"""Modern starting hand size — Magic rules constant.

Used as the denominator in the legacy `min(1.0, hand_size / 7)` hand-
saturation factor in `_estimate_opp_threat_prob`. Pinned as a named
constant so the heuristic-fallback branch (un-initialised BHI) shares
the same rules anchor as the BHI-driven branch and re-tuning is a
single-point change.
"""


def opp_threat_prob_from_density(p_threat_density: float,
                                  opp_hand_size: int) -> float:
    """P(at least one threat in the opp's unknown hand), derived from
    the per-card threat density and the live hand size.

    Formula (no magic numbers):

        P(at least one threat in N draws) = 1 - (1 - density) ** N

    This is the standard Bernoulli-trials form already used by every
    other density-based prior in `ai/bhi.py` (counter / removal /
    artifact-threat). It replaces the previous flat `0.5 * hand_factor`
    weighting in `_estimate_opp_threat_prob`'s BHI branch — that
    coefficient ignored the threat composition of the opp's pool and
    inflated the threat probability identically against a counter-
    heavy control deck and a creature-heavy aggro deck at equal hand
    size.

    Sister primitive: `HandBeliefs.p_higher_threat_in_n_turns` —
    same Bernoulli formula applied to the spot-removal-deferral
    decision. Both consume density priors maintained by
    `BayesianHandTracker._recalculate_priors`.
    """
    if p_threat_density <= 0.0 or opp_hand_size <= 0:
        return 0.0
    p = 1.0 - (1.0 - p_threat_density) ** max(0, opp_hand_size)
    return max(0.0, min(1.0, p))


# ─── Evoke-budget constants ──────────────────────────────────────────
# Used by ai/board_eval.py::_eval_evoke to gate the Nth removal-evoke
# in the same turn. The flat per-evoke value of +1.0 (the default-
# evoke branch) has no context for "we already burned two cards on a
# removal-evoke this turn", which produced the AzCon vs Affinity T3
# double-evoke described in
# `docs/diagnostics/2026-05-01_azcon_followup.md`.

EVOKE_BUDGET_PENALTY_PER_PRIOR: float = 4.0
"""Additive penalty applied to each subsequent removal-evoke after the
first this turn.

Derivation: a removal-evoke spends two cards (the elemental itself
plus the pitched support card). The default-evoke branch in
`_eval_evoke` returns +1.0 for the ETB value alone — that score makes
sense for the FIRST trade but ignores the second's marginal cost.
4.0 matches the per-CMC value of held interaction
(`HELD_RESPONSE_VALUE_PER_CMC`) — both encode "a card we expected to
keep is now committed", so the units agree. With counter = 1 the
penalty is -4.0, which dominates the +1.0 default and gates the
second evoke. With counter = 2 the penalty ramps to -8.0 (no chained
third evoke survives without a sentinel target).

Sister constant: EVOKE_BUDGET_SENTINEL_THREAT — the threshold above
which the penalty is waived because the trade is forced.
"""


EVOKE_BUDGET_SENTINEL_THREAT: float = 8.0
"""Target `creature_threat_value` at or above which the evoke-budget
penalty is waived. 8.0 = 2 × REMOVAL_DEFERRAL_TARGET_GAP — twice the
premium-threat tier, the level at which "we lose if this resolves"
outweighs the marginal-card argument the budget guard encodes.

Derivation: a Cranial-Plating-equipped carrier with ~5 artifacts in
play scores ~13 in `creature_threat_value`; a vanilla 4/4 (Sojourner's
Companion) scores ~4.45; a Memnite scores ~1.15. The threshold sits
between vanilla-4/4 and Plating-buffed carriers so that the second
evoke fires on the latter (a forced trade) but not on the former.

Sister constant: REMOVAL_DEFERRAL_TARGET_GAP — same scale, same
intent ("premium threat" tier in `creature_threat_value` units).
"""


def evoke_budget_penalty(prior_evokes: int, target_threat: float) -> float:
    """Return the additive penalty for the next removal-evoke given
    the count of prior removal-evokes this turn and the candidate
    target's threat value.

    Returns 0 (no penalty) when no prior evokes have fired this turn,
    or when the target clears the sentinel-class threshold. Otherwise
    returns ``-prior_evokes × EVOKE_BUDGET_PENALTY_PER_PRIOR``.
    """
    if prior_evokes <= 0:
        return 0.0
    if target_threat >= EVOKE_BUDGET_SENTINEL_THREAT:
        return 0.0
    return -float(prior_evokes) * EVOKE_BUDGET_PENALTY_PER_PRIOR


# ─── Pitch / opportunity-cost constants ──────────────────────────────

PITCH_COUNTER_FREE_COST: int = 1
"""Rules-constant: effective cost of a "free" pitch counter on the
opponent's turn — 1 exiled card, no mana.

Used by `respond_to_stack` in `ai/response.py` to decide whether a
counter is cheap enough to fire even when a post-resolution creature-
exile is also available.  Counters with effective cost > 1 are reserved
when triage would otherwise skip them; pitch counters at cost 1 always
fire because the opportunity cost (one exiled card vs. opp's spell) is
strictly favorable.
"""


# ─── Stack-threat / response gating constants (ai/response.py) ───────
# Thresholds and scaling factors used by `evaluate_stack_threat` and
# `decide_response`.  These were inline literals with extensive in-line
# derivation comments; centralizing them here keeps the "no magic
# numbers" contract intact and allows re-tuning at a single point.

CLOCK_IMPACT_LIFE_SCALING: float = 20.0
"""Derived: factor that converts clock-impact units (cards/turn /
opp_life) into life-point units suitable for direct comparison with
threat values produced by `evaluate_stack_threat`.

Used throughout `ai/response.py`'s `evaluate_stack_threat` to lift
mana-clock and card-clock terms (cascade saved mana, equipment pump,
X-cost scalers, token engines, card-advantage engines, cost reducers,
held-counter floor) onto the same scale as the projected board-EV
delta.

Derivation: a typical Modern opponent's life total is 20.  Clock-
impact primitives (`mana_clock_impact`, `card_clock_impact`) return
values normalised by `opp_life`, so multiplying by 20 reverses the
normalisation and yields life-points / turn.  The same factor anchors
the held-counter floor (`_held_counter_floor_ev`) so the floor is
directly comparable to the threat values it gates against.

Sister scaling: the BASE / RAMP held-interaction constants above use
a different per-CMC scale (mana-cost units, not life units); they are
NOT interchangeable with this constant.
"""


HELD_COUNTER_FLOOR_MIN_EV: float = 1.0
"""Derived: minimum EV of a held counter, even when the snapshot
has no mana and no opponent life pressure.

Floored so an empty snapshot doesn't collapse the counter-gate
threshold to zero — the counter still costs at least a card.  1.0
matches the lower edge of `card_clock_impact × CLOCK_IMPACT_LIFE_SCALING`
for a typical Modern game state (avg power 2.5 / opp life 20 = 0.125
base; ×0.4 castable fraction at 1 mana; ×20 ≈ 1.0).

Used by `_held_counter_floor_ev` in `ai/response.py` as the floor
for the gate-threshold computation in `decide_response`.
"""


COUNTER_GATE_HIGH_MULTIPLIER: float = 1.5
"""Derived: multiplier applied to the held-counter floor EV to obtain
the high-confidence counter-fire gate.

A counter must clear the floor by 50%+ to justify firing on a
non-cheap trade — "clearly above replacement value", one held card
swap is worthwhile if the threat trades up by 50%+.  Mirrors the
legacy hardcoded 3.0 threshold for a typical floor of ~2.0 post-fix
(regression-safe).

Used by `decide_response` in `ai/response.py` as
``COUNTER_GATE_HIGH = held_counter_floor_ev × COUNTER_GATE_HIGH_MULTIPLIER``.

Sister constant: COUNTER_GATE_LOW_MULTIPLIER — same gate, lower
threshold for cheap-trade scenarios.
"""


COUNTER_GATE_LOW_MULTIPLIER: float = 0.5
"""Derived: multiplier applied to the held-counter floor EV to obtain
the cheap-trade counter-fire gate.

When the trade is favourable (cheap held counter OR cheaply-paid
threat), the gate drops to 0.5× floor EV.  Reflects that a cheap
counter firing on a free spell is a tempo-positive trade even if
the absolute threat value is below replacement — we're denying
opp's free value at minimal cost.  Pre-fix the legacy 1.5 floor
failed for affinity-discounted card-advantage spells whose
projection-based threat sat at ~1.0.

Used by `decide_response` in `ai/response.py` as
``COUNTER_GATE_LOW = held_counter_floor_ev × COUNTER_GATE_LOW_MULTIPLIER``.

Sister constant: COUNTER_GATE_HIGH_MULTIPLIER — same gate, higher
threshold for non-cheap trades.
"""


CHEAP_COUNTER_PAID_THRESHOLD: int = 2
"""Rules-constant: maximum effective mana cost at which a counter
qualifies as "cheap" for the LOW gate in `decide_response`.

A counter at effective cost ≤ 2 (Counterspell UU, free pitch
counters, 1U Spell Pierce-style) is cheap enough that firing it on
a moderate threat is a tempo-positive trade — we drop the gate to
COUNTER_GATE_LOW.  Counters above this threshold (Cryptic Command at
1UUU, Force of Will at 0+exile-and-life) are reserved for the HIGH
gate.

Sister constant: CHEAP_THREAT_PAID_THRESHOLD — same scale, applied
to the threat side of the trade.
"""


CHEAP_THREAT_PAID_THRESHOLD: int = 2
"""Rules-constant: maximum effective mana the opponent paid at which
their threat qualifies as "cheap" for the LOW gate in `decide_response`.

When opp casts a spell whose effective paid cost (printed CMC after
affinity / delve / domain / generic cost-reducers and X-paid) is ≤ 2,
countering it is a tempo-positive trade even if its absolute threat
value is below replacement — we're denying free / cheap value.

Sister constant: CHEAP_COUNTER_PAID_THRESHOLD — same scale, applied
to our counter's side of the trade.
"""


PROACTIVE_REMOVAL_MIN_VALUE: float = 3.0
"""Rules-constant: minimum `estimate_removal_value` at which an
instant-speed removal spell is fired reactively (in response to an
opponent's non-creature spell on the stack) against a creature on
their board.

3.0 is the "worth a card" floor on the `estimate_removal_value`
scale — one mana invested in removal must net at least the card-
swap value.  Below this we hold the removal for a higher-EV target
later.  Used by `decide_response` in `ai/response.py` to gate the
reactive-removal branch.
"""


EQUIPMENT_RESIDENCY_TURNS: int = 3
"""Rules-constant: typical equipment residency window in Modern.

Equipment is rarely removed (most decks lack artifact removal in the
main), so a resolved equipment is expected to contribute its pump
across ~3 combat turns before it leaves the battlefield.  Used by
`evaluate_stack_threat` in `ai/response.py` to scale equipment-pump
and X-cost / "for each" creature-scaler threat values.

Re-used as the residency window for X-cost creature scalers (Walking
Ballista, Hangarback Walker) — same intent: how many combat turns
the projected body contributes before opp's removal answers it.
"""


EQUIPMENT_DEFAULT_POWER_BONUS: int = 2
"""Derived: default +P/+T bonus assumed for an equipment whose oracle
text doesn't specify an explicit `+N/+N` clause.

+2 reflects the median power bonus on Modern-playable equipment
(Bonesplitter +3, Cranial Plating variable, Sword cycle +2, Skullclamp
+1, Shadowspear +1).  Used by `evaluate_stack_threat` in
`ai/response.py` as the fallback when the regex `\\+(\\d+)/\\+\\d+`
doesn't match the equipment's oracle text.
"""


# ─── Keyword clock multipliers (creature_clock_impact in ai/clock.py) ─
# Multiplicative bonuses applied to a creature's base clock contribution
# (`power / opp_life`) when it has a clock-relevant keyword. These were
# previously inline-commented bare literals in `creature_clock_impact`;
# centralized here so re-tuning is a single-point change and the
# derivation comments survive grep.

PURE_BLOCKER_TOUGHNESS_VALUE: float = 0.05
"""Derived: per-toughness clock value of a 0-power creature.

A pure blocker has no offensive clock — its only contribution is
absorbing damage. Each point of toughness is worth ~0.05 turns of
clock against an average opponent (delays opp's lethal by ~5%
per point on the typical 20-life curve). Multiplied against
`toughness` so a 0/4 wall returns 0.20 turns of defensive value.
"""

EVASION_VS_BLOCKERS_MULTIPLIER: float = 1.3
"""Derived: ground creatures lose ~30% damage to blockers on average.

Applied as `base *= 1.3` when a creature with flying/menace/trample
attacks an opponent who has at least one blocker. The +30% reflects
the marginal damage that ground-only attackers forfeit to blocking.
"""

FIRST_STRIKE_SURVIVAL_MULTIPLIER: float = 1.15
"""Derived: first-strike survives combat more often than a vanilla
attacker, preserving its clock contribution across turns.

+15% reflects the empirical survival rate against equal-toughness
ground blockers in Modern (most 2-drops are 2/2, so first-strike
3/2 trades up rather than dying).
"""

REMOVAL_RESISTANT_MULTIPLIER: float = 1.25
"""Derived: hexproof / indestructible creatures' clock is more
reliable because removal can't shorten their contribution window.

+25% reflects the typical 1-in-4 removal incidence per turn against
a relevant threat in Modern (one Bolt / Push / wrath per ~4 turns
during the threat's expected lifespan).
"""

UNDYING_RECURSION_MULTIPLIER: float = 1.5
"""Derived: undying creatures die and come back = roughly 1.5×
the clock contribution of a vanilla creature.

Not 2.0× because the +1/+1 counter precludes a second undying trigger,
and the returning body is +1/+1 but the original death still cost a
combat step. Net = ~1.5 baseline clocks.
"""

KEYWORD_HALF_WEIGHT: float = 0.5
"""Derived: lifelink and deathtouch each contribute ~half a vanilla
creature's clock to the offensive base.

Lifelink: each attack gains life = extends survival by `power/opp_power`
turns; weighted at 0.5 because the offensive clock and the survival
extension are partially redundant against a fast clock.

Deathtouch: effectively removes a blocker = improves ground clock by
`avg_opp_power / opp_life` per attack; weighted at 0.5 because the
"removed" blocker can be re-deployed by the opponent.

Sister constant: KEYWORD_MINOR_WEIGHT — same "fractional contribution"
intent for less impactful defensive keywords (vigilance, reach).
"""

KEYWORD_MINOR_WEIGHT: float = 0.3
"""Derived: vigilance / reach contribute ~0.3 of a vanilla creature's
worth of defensive clock.

Vigilance: attacks without tapping = also blocks; defensive value is
`min(toughness, opp_power) / my_life` per turn, weighted at 0.3
because the offensive clock dominates and the block availability is
conditional on opp choosing to attack.

Reach: same formula scoped to opp's evasion power. Same weighting
rationale.

Sister constant: KEYWORD_HALF_WEIGHT — the higher-impact bracket
(lifelink, deathtouch).
"""

TOUGHNESS_DEFENSIVE_WEIGHT: float = 0.15
"""Derived: a creature's raw toughness contributes a minor blocking
clock when the opponent has attacking power.

0.15 ≈ ½ × KEYWORD_MINOR_WEIGHT, reflecting that the implicit
"can block" baseline is worth half as much as an explicit
defense-improving keyword. Capped by the actual blocking math
(`min(toughness, opp_power) / my_life`) so over-toughness on
a single creature doesn't dominate.
"""

ANNIHILATOR_CHIP_PER_OPP_CREATURE: float = 0.3
"""Derived: each opp creature on board contributes ~0.3 expected
forced sacrifices per annihilator trigger, normalized by opp_life.

Captures the "annihilator chips through the opp's board" component
separately from the per-trigger sacrifice (ANNIHILATOR_BASE_SAC).
"""

ANNIHILATOR_BASE_SAC: float = 2.0
"""Derived: each annihilator trigger forces opp to sacrifice ~2 worth
of permanents (lands + creatures, weighted by typical Modern board
composition).
"""

PROWESS_TRIGGER_PER_TURN: float = 1.0
"""Derived: a prowess creature gains ~+1/+0 per turn from the typical
Modern noncreature-spell density (1 instant or sorcery per turn for
spell-heavy decks). Adds 1.0 / opp_life of clock.
"""

CASCADE_FREE_SPELL_VALUE: float = 2.5
"""Derived: cascade casts a free spell of CMC strictly less than the
caster — roughly worth another small creature's clock (~2.5 power
equivalent). Scaled by 1/opp_life for clock units.

Sister constant: AVG_CREATURE_POWER (this file, defined below) — the
2.5 here matches the same "average Modern creature" baseline.
"""

ETB_VALUE_BONUS: float = 2.0
"""Derived: a creature with the `etb_value` tag has an enter-the-
battlefield effect worth ~2 damage equivalent on average (typical
Modern ETBs: 2 damage, 2 life, +1/+1 counter, scry). Scaled by
1/opp_life for clock units.
"""

TOKEN_MAKER_BONUS: float = 1.5
"""Derived: a creature with the `token_maker` tag creates ~1 extra
body, worth ~1.5 of a vanilla creature's clock contribution. Scaled
by 1/opp_life for clock units. The token is typically smaller than
the parent, hence 1.5 rather than 2.5 (cascade) or 2.0 (etb_value).
"""

AVG_CREATURE_POWER: float = 2.5
"""Derived: average creature power in Modern. Used by
`card_clock_impact` to estimate the clock change one card in hand
provides on average. 2.5 reflects the empirical mean of relevant
creatures (excluding mana dorks) across the 16 tracked decks.

Sister constant: CASCADE_FREE_SPELL_VALUE — same baseline for the
"another creature's worth" intent.
"""

# ─── Spell-scoring constants (ai/ev_player.py) ────────────────────
# Bare-literal extraction pass. Each constant below was a numeric
# literal in `ai/ev_player.py`; the inline comment justifying the
# value is promoted into the docstring here so the derivation
# survives grep and any future re-tune is single-point.

REANIMATE_OVERRIDE_BONUS: float = 40.0
"""Sentinel: force-cast reanimation when a big graveyard target is
ready. 40.0 is large enough to dominate any other main-phase candidate
EV — the gameplan has already declared payoff readiness via gates
(`is_ready_for_payoff`) and the reanimate spell is THE win condition,
so the override pins it at the top regardless of projection noise.

Used by `decide_main_phase` in `ai/ev_player.py` for the reanimate
priority override branch (gameplan-gated).
"""

FREE_CAST_TEMPO_BONUS: float = 1.5
"""Derived: tempo bonus on top of projection-EV for any spell offered
at 0 effective mana (Ragavan exile, cascade, suspend, Wish-style).

Reflects the "got it for free" nature of the cast: even if the
projection is exactly zero, casting it is strictly better than
declining. 1.5 matches the trigger-value bonus applied elsewhere for
Ragavan combat triggers — same scale, same "one extra free action
this turn" intent.

Used by `_score_spell` in `ai/ev_player.py` after the
`_free_cast_opportunity` floor.
"""

EVOKE_CARD_LOSS_MULTIPLIER: float = 15.0
"""Derived: multiplier applied to `card_clock_impact(snap)` to convert
the second-card cost of evoke into clock-units.

Evoke pays an extra card on top of the mana cost; subtracting
`card_clock_impact × 15` converts that lost-card future-value into
the same scale as the projection EV. 15 is a midgame residency
window (≈ 2× EQUIPMENT_RESIDENCY_TURNS × AVG_CREATURE_POWER) — the
future turns the lost card was expected to contribute across before
the game ends.

Used by `_score_spell` evoke overlay in `ai/ev_player.py`.
"""

EVOKE_DESPERATE_BONUS: float = 10.0
"""Derived: additive bonus to evoke EV when `snap.am_dead_next` is
True. 10.0 ≈ ½ × LETHAL_THREAT in life-units, large enough to
overcome the default evoke card-loss penalty when dying makes the
trade worth the card.

Sister constant: EVOKE_NO_TARGET_PENALTY — same scale, opposite
direction (no targets means evoke fizzles).

Used by `_score_spell` evoke overlay in `ai/ev_player.py`.
"""

EVOKE_NO_TARGET_PENALTY: float = 20.0
"""Sentinel: penalty applied to evoke-removal when the opponent has
no creatures to target. -20.0 is below `pass_threshold` for every
archetype (the most permissive `pass_threshold` is around -5.0), so
the AI never evokes a removal-elemental into an empty board.

Used by `_score_spell` evoke overlay in `ai/ev_player.py`.
"""

PLANESWALKER_SURVIVAL_FLOOR: float = 3.0
"""Derived: floor on the planeswalker EV bonus. Represents the
minimum value of one activation (one card draw, one removal, one
Cat token) BEFORE the planeswalker dies.

When `card_clock_impact → 0` (early game with low opp board) the
loyalty-based bonus collapses; without this floor a planeswalker
loses to a vanilla creature of equal CMC. 3.0 matches the
`PROACTIVE_REMOVAL_MIN_VALUE` "worth a card" floor — at minimum a
planeswalker is worth one card swap.

Used by `_score_spell` non-creature-permanent overlay in
`ai/ev_player.py`.
"""

MIDGAME_HORIZON_TURNS: float = 6.0
"""Rules-constant: Modern midgame horizon, used as a sentinel when
combat-clock returns NO_CLOCK (no creatures = no clock).

6 turns matches the empirical Modern midgame turn (typical kill
turns 5-7 for aggro, 7-10 for midrange, 10+ for control). Used as
the fallback for cost-reducer EV scaling so the bonus integrates
over a sensible window even when the live clock is undefined.

Sister constant: MODERN_AVG_GAME_LENGTH (8.0) — same horizon family,
used for Tron-assembly compounding which expects a longer window.
"""

GAME_HORIZON_MIN_TURNS: float = 2.0
"""Rules-constant: lower bound for the cost-reducer / Tron-assembly
horizon clamp. 2.0 turns is the minimum window over which a mana
advantage compounds to a meaningful EV — anything shorter and the
game ends before the bonus realises.

Sister constant: GAME_HORIZON_MAX_COST_REDUCER (8.0) and
GAME_HORIZON_MAX_TRON (10.0) — upper bounds for the same clamp.
"""

GAME_HORIZON_MAX_COST_REDUCER: float = 8.0
"""Rules-constant: upper bound for cost-reducer EV horizon. 8.0
matches `MODERN_AVG_GAME_LENGTH` — a cost reducer realises its full
value across the average Modern game.

Used by `_score_spell` cost-reducer branch in `ai/ev_player.py`.
"""

MODERN_AVG_GAME_LENGTH: float = 8.0
"""Rules-constant: average Modern game length (in turns) used for
Tron-assembly mana-compounding when combat-clock returns NO_CLOCK.

8 turns is empirically the median game length across the 16 tracked
decks per `run_meta.py --matrix` data. Tron specifically has a
longer expected window than the cost-reducer case (which is
dominated by faster aggro matchups), so the upper-clamp differs.

Used by `_score_land` Tron-assembly branch in `ai/ev_player.py`.
"""

GAME_HORIZON_MAX_TRON: float = 10.0
"""Rules-constant: upper bound for Tron-assembly EV horizon. 10.0
caps the compounding window for the Tron mana advantage so a hung-
clock state can't inflate it without bound. Higher than the cost-
reducer cap (8.0) because Tron is specifically a long-game shell —
its mana advantage matters most in protracted games.

Used by `_score_land` Tron-assembly branch in `ai/ev_player.py`.
"""

BLINK_M1_HOLD_PENALTY: float = 2.0
"""Derived: small penalty applied to blink-instant EV when
- we're in MAIN1
- we hold a blink instant AND control an ETB-value creature
- we have at least one untapped attacker

The penalty nudges the AI to pass M1, swing for combat damage, then
blink in M2 — preserving the combat damage step. 2.0 is below most
spell scores (typical range 5-15) so it acts as a tie-breaker, not
a hard gate. Mirrors the threat-amplifier scale used by the
`_score_spell` removal premium term.

Used by `_score_spell` Jeskai/blink M1 hold branch in
`ai/ev_player.py`.
"""

NONCREATURE_COUNTER_DEAD_FLOOR: float = -3.0
"""Derived: ceiling EV for a noncreature-only counter (Negate / Dovin's
Veto) when the opponent's board and hand point to a creature-heavy
aggro deck (≥2 creatures in play, ≥4 power, ≤3 cards in hand).

The counter is "dead in hand" — there's no realistic non-creature
target to spend it on. -3.0 is just below `pass_threshold = -5.0`
on the COUNTER side but well above the "force a discard" sentinel,
so the AI shelves the counter without throwing it away.

Used by `_score_spell` noncreature-counter branch in
`ai/ev_player.py`.
"""

REMOVAL_THREAT_PREMIUM_SCALE: float = 0.5
"""Derived: scaling factor on the threat-premium overlay for non-
creature removal. A battle-cry / equipped / scaling target whose
threat-value exceeds its raw clock value gets `(threat - clock) × 0.5`
added to the removal EV.

0.5 brings a typical battle-cry premium (~+4 threat units) into the
+2 EV tiebreaker range with equal-CMC deploys — enough to lift
removal above a non-removal alternative when the target is amplified,
but not enough to override a clearly better deploy.

Sister constant: REMOVAL_DEFERRAL_TARGET_GAP — same threat-tier
unit space.

Used by `_score_spell` removal threat-premium overlay in
`ai/ev_player.py`.
"""

CHEAP_REMOVAL_ACTION_BONUS: float = 1.0
"""Derived: additive bonus for 1-CMC removal that has a valid target.

1-mana removal leaves room for a second action this turn (deploy,
draw, ramp), so its real EV is its kill-target value PLUS the value
of the additional play. 1.0 is one EV unit — the smallest meaningful
EV difference, standing in for "one extra card-equivalent action".

Used by `_score_spell` removal threat-premium overlay in
`ai/ev_player.py`.
"""

LANDFALL_DEFERRAL_PENALTY: float = 12.0
"""Derived: penalty applied to land-EV when a landfall creature is
castable with current mana. The land must be played AFTER the creature
so the landfall trigger fires (otherwise we waste the "first land"
event).

12.0 matches the `RAMP_TO_BIG_NOW` magnitude in the same scoring
function — same scale ("a land that materially changes our turn"),
applied as a defer rather than a ramp.

Used by `_score_land` landfall-deferral branch in `ai/ev_player.py`.
"""

X_BOARD_WIPE_WASTE_FLOOR: float = -20.0
"""Sentinel: floor EV for an X-cost board wipe whose X-budget can't
meaningfully clear. -20.0 is below `pass_threshold` for every
archetype, ensuring the AI holds X-wraths until enough mana to clear
the board is available (or we're at low life and forced to wipe
anyway).

Used by `_score_spell` X-cost-board-wipe gate in `ai/ev_player.py`.
"""

BLINK_FIZZLE_FLOOR: float = -50.0
"""Sentinel: floor EV for a blink/flicker spell with no legal target
(no creatures we control). The engine bails safely, but the AI must
never score a fizzling blink as positive EV. -50.0 is well below
every archetype's `pass_threshold` and below the X-board-wipe waste
floor — fizzle is the worst possible outcome (mana wasted AND card
consumed).

Used by `_score_spell` blink hard-gate in `ai/ev_player.py`.
"""

CHUMP_SENTINEL_VALUE: float = 999.0
"""Sentinel: initial value for `best_chump_val` when scanning chump
candidates. Any real creature_value will be smaller, so the first
candidate replaces the sentinel and the loop converges on the
cheapest chump.

Used by `decide_blockers` emergency-block path in `ai/ev_player.py`.
"""

NO_CLOCK_FACE_VAL_MULTIPLIER: float = 0.1
"""Derived: face-burn EV multiplier when we have no creatures and the
opponent is not low-life. Without an on-board clock, burning face
contributes only marginally to a kill — the burn must combine with a
future clock that doesn't exist yet.

0.1 collapses face-burn EV to near-zero so removal targeting takes
priority, which is correct: a 3-damage Bolt with no clock to follow
up should hit a Ragavan, not the dome.

Used by `_choose_targets` burn-vs-creature decision in
`ai/ev_player.py`.
"""

COMBO_FORCE_PAYOFF_STORM_THRESHOLD: int = 5
"""Rules-constant: storm count threshold above which the combo-kill
goal-advance fires in `decide_main_phase`. At storm count ≥ 5 the
deck has cast enough cheap fuel that even non-lethal storm payoffs
(Grapeshot for 5, Tendrils for 7) close the game on the next
finisher.

Used by `decide_main_phase` combo kill override in
`ai/ev_player.py`.
"""

LANDFALL_TRIGGER_VALUE: float = 3.0
"""Derived: per-landfall-trigger EV. Each landfall trigger ≈ ETB
effect value (1 life, 1 damage, 1 ramp event). 3.0 matches the
`PROACTIVE_REMOVAL_MIN_VALUE` "worth a card" floor — one landfall
trigger is roughly one card-quality event.

Used by `_score_land` landfall bonus in `ai/ev_player.py`.
"""

ARTIFACT_LAND_SYNERGY_BONUS: float = 4.0
"""Derived: per-synergy-card bonus for an artifact-typed land when
the player's visible cards carry artifact-scaling text.

Derivation: 1 power (or 1 mana) gained per synergy card × ~4
residency turns × ~0.05 mana_clock_impact × 20
(CLOCK_IMPACT_LIFE_SCALING) ≈ 4.0. Matches the
`EVOKE_BUDGET_PENALTY_PER_PRIOR` and `HELD_RESPONSE_VALUE_PER_CMC`
family — same "one card committed" scale.

Used by `_score_land` artifact-synergy branch in `ai/ev_player.py`.
"""

TRON_MANA_ADVANTAGE: float = 4.0
"""Derived: completed Tron yields {C}{C}{C}{C}{C}{C}{C} = 7 colorless
mana from 3 lands vs ~3 mana from 3 vanilla lands, so the assembly
advantage is +4 mana / turn.

Used by `_score_land` Tron-assembly branch in `ai/ev_player.py`.
"""

AMULET_TITAN_MANA_BONUS: float = 4.0
"""Rules-constant: 2 lands × 2 mana each = +4 mana when Primeval Titan
ETBs with Amulet of Vigor on the battlefield. Both fetched lands come
in tapped and Amulet untaps them, so all 4 mana are available the
same turn.

Bounce lands compound this further but are not modelled precisely;
the floor is 2 lands untapped.

Used by `_score_spell` Amulet+Titan synergy branch in
`ai/ev_player.py`.
"""

CYCLING_CASCADE_BOOST: float = 8.0
"""Derived: cycling EV bonus when a cascade spell is in hand.

When cascade is the deck's primary action, filling the graveyard
via cycling becomes the urgent setup move. 8.0 matches the
`HELD_COLOR_PRESERVATION_BONUS` scale — same "this enables our key
card" intent.

Used by `_score_cycling` in `ai/ev_player.py`.
"""

CYCLING_GY_URGENCY: float = 6.0
"""Derived: additional cycling EV when graveyard creature count < 3
AND a cascade is in hand.

Compounds with `CYCLING_CASCADE_BOOST` to express "we MUST cycle
before cascading or the cascade will hit an empty graveyard". 6.0
is sized to match the `card_clock_impact × 20` scale of a typical
cascade hit — losing the cascade payoff entirely costs roughly this
much EV.

Used by `_score_cycling` in `ai/ev_player.py`.
"""

CYCLING_GAMEPLAN_BOOST: float = 10.0
"""Derived: cycling EV bonus when the gameplan's current goal sets
`prefer_cycling = True` (Living End and similar reanimator shells).

10.0 is the largest cycling-overlay constant — when the gameplan
explicitly says "cycling IS the gameplan", cycling should beat
almost every other play. Matches the LAND_BASE_EV scale so a
cycling activation reads as "as important as a land drop".

Used by `_score_cycling` in `ai/ev_player.py`.
"""

CYCLING_FREE_COST_BONUS: float = 2.0
"""Derived: cycling EV bonus when cycling cost involves paying life
instead of mana ("free" cycling — Street Wraith, Decree of Pain).

Matches the per-trigger bonus on `ETB_VALUE_BONUS` — same "small
extra value at no mana cost" intent.

Used by `_score_cycling` in `ai/ev_player.py`.
"""

CYCLING_CHEAP_COST_BONUS: float = 1.0
"""Derived: cycling EV bonus for cheap cycling (mana cost ≤ 1).

1.0 = one EV unit, matching `CHEAP_REMOVAL_ACTION_BONUS` — same
"mana-efficient enough to leave room for a second action" intent.

Used by `_score_cycling` in `ai/ev_player.py`.
"""

CYCLING_GY_REANIMATE_BASE: float = 4.0
"""Derived: base cycling EV when cycling a creature into the graveyard
in a deck with a visible reanimation path.

The cycled creature becomes a future reanimation target, so its
graveyard value is roughly its hardcast value minus its mana cost.
4.0 ≈ the `ARTIFACT_LAND_SYNERGY_BONUS` scale — same "one card-
worth of future value" intent.

Sister constant: CYCLING_GY_REANIMATE_PER_POWER — power-scaling
addend.

Used by `_score_cycling` reanimation-path branch in
`ai/ev_player.py`.
"""

CYCLING_GY_REANIMATE_PER_POWER: float = 0.5
"""Derived: per-power cycling EV addend on top of
`CYCLING_GY_REANIMATE_BASE`. A power-5 creature in graveyard is
worth more as a reanimation target than a power-2 creature —
0.5 × power roughly tracks the threat-value gap (a 5/5 vs 2/2 in
`creature_value` units).

Used by `_score_cycling` reanimation-path branch in
`ai/ev_player.py`.
"""

# ─── Combat / turn-planning constants (ai/turn_planner.py) ────────
# Bare-literal extraction pass for ai/turn_planner.py. Several of
# these were already named module-level constants in turn_planner.py
# with derivation comments; centralising preserves the single point
# of review for future re-tunes.

LETHAL_BONUS: float = 100.0
"""Sentinel: structural lethal bonus for combat scoring.

100.0 matches `LETHAL_THREAT` (life-units sentinel for stack-side
threats) — same "game-ending event" scale, applied to combat.
Pinned at the top of the combat scale so any lethal attack
configuration outranks any non-lethal one regardless of trade math.

Used by `CombatPlanner.plan_attack` in `ai/turn_planner.py`.
"""

TWO_TURN_LETHAL_BONUS: float = 15.0
"""Derived: bonus for combat configurations that set up lethal NEXT
turn (surviving power minus opp's surviving block power ≥ opp life).

15.0 sits in the "strong but not game-ending" tier between
LETHAL_BONUS (100.0) and TRADE_UP_BONUS (2.0). Beats a typical
trade-up bonus by a factor of ~7, ensuring 2-turn lethal setups are
pursued even when the immediate combat trade is even.

Used by `CombatPlanner.plan_attack` in `ai/turn_planner.py`.
"""

TRADE_UP_BONUS: float = 2.0
"""Derived: bonus for combat configurations where opp loses more
total creature value than we do.

2.0 matches the `BLINK_M1_HOLD_PENALTY` / `CYCLING_FREE_COST_BONUS`
tier — small enough that it's a tie-breaker, not a hard gate. The
combat math already values surviving creatures via post-board
score; this is just a nudge to prefer trades where we come out
ahead.

Sister constant: TRADE_DOWN_PENALTY — same scale, opposite sign.

Used by `CombatPlanner.plan_attack` in `ai/turn_planner.py`.
"""

TRADE_DOWN_PENALTY: float = -2.5
"""Derived: penalty for combat configurations where we lose more
total creature value than opp does (and the attack isn't lethal).

Slightly larger than `TRADE_UP_BONUS` because the typical trade-down
also tempo-loses (we used the attack step as well as the creature),
so the penalty captures both the value loss and the lost combat
turn. Scaled at the call site by `min(my_lost_value / 5.0, 1.0)` so
small trade-downs (1/1 tokens) penalise less than big ones.

Sister constant: TRADE_UP_BONUS — same scale, opposite sign.

Used by `CombatPlanner.plan_attack` in `ai/turn_planner.py`.
"""

SHIELDS_DOWN_PENALTY: float = -1.5
"""Derived: penalty for tapping out attackers when opponent has open
mana for tricks (`board.opp_mana >= 2`).

1.5 mirrors the `FREE_CAST_TEMPO_BONUS` / `BLINK_M1_HOLD_PENALTY`
scale — a tie-breaker, not a hard gate. Scaled at the call site by
the damage being dealt: heavy attacks discount the penalty (tapping
out is worth it for big damage).

Used by `CombatPlanner.plan_attack` in `ai/turn_planner.py`.
"""

MAX_ATTACK_CONFIGS: int = 32
"""Rules-constant: computational budget for attack configuration
enumeration. 2^N grows quickly; 32 covers all realistic Modern board
states (≤5 attackers fully enumerated; larger boards prune via
heuristic categorisation in `_generate_attack_configs`).

Used by `CombatPlanner._generate_attack_configs` in
`ai/turn_planner.py`.
"""

COUNTER_THRESHOLD: float = 5.0
"""Derived: minimum threat-value to fire a non-cheap (CMC>2) counter.

5.0 ≈ a 3/3 with a keyword in `creature_value` units. Below this
threshold the counter is held for a more impactful target later.
Sister to `COUNTER_CHEAP_THRESHOLD = 2.0` — the cheap-counter
gate has a lower bar because the held-mana opportunity cost is
smaller.

Used by `TurnPlanner.evaluate_response` in `ai/turn_planner.py`.
"""

COUNTER_CHEAP_THRESHOLD: float = 2.0
"""Derived: minimum threat-value to fire a cheap (CMC≤2) counter.

2.0 ≈ a 1-power vanilla creature in `creature_value` units. Cheap
counters can fire on almost anything because the mana cost barely
constrains the rest of the turn.

Sister constant: COUNTER_THRESHOLD — same family, larger floor for
the non-cheap branch.

Used by `TurnPlanner.evaluate_response` in `ai/turn_planner.py`.
"""

REMOVAL_RESPONSE_THRESHOLD: float = 4.0
"""Derived: minimum net-value (threat × 0.8 minus removal cost) to
fire instant-speed removal in response to a creature spell on the
stack.

4.0 ≈ a 3/3 worth of threat in `creature_value` units. Below this
the removal is held for a higher-EV target post-resolution. Sister
to `PROACTIVE_REMOVAL_MIN_VALUE = 3.0` (the reactive non-creature
branch) — slightly higher here because we're spending the response
on a creature target instead of letting opp commit and answering
post-resolution.

Used by `TurnPlanner.evaluate_response` in `ai/turn_planner.py`.
"""

BLINK_SAVE_THRESHOLD: float = 3.5
"""Derived: minimum creature value at which blinking to save it from
removal is worth the response card.

3.5 ≈ a 2/2 with a keyword in `creature_value` units. Below this
the creature isn't worth a blink card; above it the save is correct
because the creature's ETB re-trigger plus the saved body exceeds
the response card's opportunity cost.

Used by `TurnPlanner.evaluate_response` in `ai/turn_planner.py`.
"""

PRE_COMBAT_REMOVAL_BONUS: float = 2.5
"""Derived: bonus for the "remove blocker pre-combat, then attack"
strategy.

Killing a blocker enables roughly 3 extra damage = 3/20 clock gain
× CLOCK_IMPACT_LIFE_SCALING (20) ≈ 3, scaled down to 2.5 because
the removed creature also costs us a card. Matches the
`PROACTIVE_REMOVAL_MIN_VALUE` "worth a card" floor.

Used by `TurnPlanner._evaluate_remove_then_attack` in
`ai/turn_planner.py`.
"""

MANA_RESERVATION_WEIGHT: float = 5.0
"""Derived: bonus for holding up mana for instant-speed responses.

Value ≈ avg_threat × P(needing_response) ≈ 5 × 0.5 ≈ 2.5, doubled
to 5.0 because the held mana also enables a future combat block /
trick the opponent must play around. Matches the
`COUNTER_THRESHOLD` scale — same "one piece of held interaction is
worth a 3/3" intent.

Used by `TurnPlanner._evaluate_hold_up_mana` in `ai/turn_planner.py`.
"""

INFORMATION_BONUS: float = 0.3
"""Derived: bonus for the "attack first (see blocks), deploy in
main 2" strategy.

0.3 is small enough to be a tie-breaker only — when the attack-then-
deploy and deploy-then-attack strategies score similarly, we prefer
seeing the opp's blocks first. Matches the chip-damage scale
(`CHIP_DAMAGE_VALUE = 0.3`) — same "small information / damage
nudge" intent.

Used by `TurnPlanner._evaluate_attack_then_deploy` in
`ai/turn_planner.py`.
"""

NO_INTERACTION_PENALTY: float = 5.0
"""Derived: penalty applied to the hold-up-mana strategy when the
hand contains no instant-speed interaction.

5.0 matches `MANA_RESERVATION_WEIGHT` — symmetric: the hold-up
strategy gains +5 when interaction is held, and loses -5 when it
isn't (because doing nothing this turn is strictly worse than ANY
proactive play).

Used by `TurnPlanner._evaluate_hold_up_mana` in `ai/turn_planner.py`.
"""

LETHAL_PUSH_INFEASIBLE_PENALTY: float = 10.0
"""Derived: penalty for the lethal-push strategy when total potential
damage falls short of opp's life.

10.0 sized to dominate any positive lethal-push delta: if we can't
actually kill, the strategy must rank below every other strategy.
Matches the `EVOKE_DESPERATE_BONUS` scale — same "decisive override"
sized event.

Used by `TurnPlanner._evaluate_lethal_push` in `ai/turn_planner.py`.
"""

CARD_IN_HAND_VALUE: float = 2.5
"""Derived: per-card EV in `VirtualBoard.score`.

Each card in hand ≈ avg_creature_power / opp_life × CLOCK_IMPACT_LIFE_SCALING
= 2.5 / 20 × 20 = 2.5. Matches `AVG_CREATURE_POWER` exactly — the
clock-units scale for "one extra creature's worth of action".

Used by `VirtualBoard.score` in `ai/turn_planner.py`.
"""

MANA_AVAILABLE_VALUE: float = 0.3
"""Derived: per-mana EV in `VirtualBoard.score`.

Mana ≈ ~1 power of deployment / opp_life × CLOCK_IMPACT_LIFE_SCALING
≈ 1.0, discounted to 0.3 because mana can't always be fully spent
each turn (color requirements, missing lands, hand composition).

Used by `VirtualBoard.score` in `ai/turn_planner.py`.
"""

LIFE_SCORE_SCALE: float = 5.0
"""Derived: scaling factor for the life-as-resource score in
`VirtualBoard._life_score`.

`life_as_resource(life, 3)` returns clock-survival turns; scaling
by 5 maps that 0-6 range onto the ~0-30 board score range expected
by `VirtualBoard.score` (roughly 1/4 of LETHAL_BONUS = 25, matching
the empirical ceiling of life-only board states).

Used by `_life_score` in `ai/turn_planner.py`.
"""

LIFE_SCORE_AVG_INCOMING: int = 3
"""Derived: assumed average incoming damage per turn used to evaluate
life-as-resource in a context-free `VirtualBoard`.

3 matches the empirical Modern incoming-damage median (one ~3-power
creature, or 1-2 burn spells per turn). Used as the second arg to
`life_as_resource(life, avg_incoming)` so the function returns a
sensible "turns until lethal" estimate without needing a live
opp-power signal.

Used by `_life_score` in `ai/turn_planner.py`.
"""

CHIP_DAMAGE_VALUE: float = 0.3
"""Derived: bonus per point of damage dealt to opp (chip damage
value).

0.3 is a small per-point reward — 5 chip damage = +1.5 EV, not
enough to override block math but enough to break ties between
"attack for 1 vs don't attack". Reused for the draw-step-prevention
bonus on the same scale.

Used by `CombatPlanner.plan_attack` in `ai/turn_planner.py`.
"""

AGGRESSION_BONUS_LIFE8: float = 0.8
"""Derived: per-power aggression bonus when opp_life ≤ 8.

In real MTG, players push damage hard at single-digit life because
the kill is reachable. 0.8 × total_attack_power means a 6-power
attack into 7 life nets +4.8 — large enough to override moderate
trade-down penalties when lethal is in reach.

Sister constants: AGGRESSION_BONUS_LIFE12 (0.4),
AGGRESSION_BONUS_LIFE16 (0.15) — tiered curve.

Used by `CombatPlanner.plan_attack` in `ai/turn_planner.py`.
"""

AGGRESSION_BONUS_LIFE12: float = 0.4
"""Derived: per-power aggression bonus when 8 < opp_life ≤ 12.
Half of `AGGRESSION_BONUS_LIFE8` — moderate aggression, kill is
plausible within 2 turns.

Used by `CombatPlanner.plan_attack` in `ai/turn_planner.py`.
"""

AGGRESSION_BONUS_LIFE16: float = 0.15
"""Derived: per-power aggression bonus when 12 < opp_life ≤ 16.
Roughly half of `AGGRESSION_BONUS_LIFE12` — slight aggression, the
kill is multiple turns away but pressure compounds.

Used by `CombatPlanner.plan_attack` in `ai/turn_planner.py`.
"""

DOUBLE_BLOCK_VALUE_THRESHOLD: float = 4.0
"""Derived: minimum attacker value at which the opponent will
consider a double-block.

4.0 ≈ a 4/4 vanilla creature in `creature_value` units. Below this
threshold, sacrificing two blockers to kill a single attacker
trades down — the opp's blocking heuristic only commits the second
blocker on real threats.

Used by `CombatPlanner._predict_blocks` Phase 4 (double-block) in
`ai/turn_planner.py`.
"""


# ─── Board-evaluator constants (ai/evaluator.py) ────────────────────
# `ai/evaluator.py` is the legacy "life-point equivalent" board
# evaluator (used by `ai/response.py`, `ai/turn_planner.py`, and
# `engine/game_runner.py` for permanent / spell / removal valuation).
# The whole module is calibrated so that +1.0 ≈ being one life ahead;
# every constant below preserves that scale.
#
# Bare-literal extraction pass: each constant was a numeric literal in
# `ai/evaluator.py`; the inline justification is promoted to the
# docstring here so future re-tunes are single-point.

# ── Role assessment (assess_role) ────────────────────────────────
ROLE_LIFE_BEATDOWN_GAP: int = 5
"""Rules-constant: opp-life deficit at which we adopt the BEATDOWN
role even when board power is even.

5 life ≈ one Lightning Bolt + a 2/2 attack in life-point units —
when the opponent is at least that far behind on life, the race is
already in our favour and aggression maximises EV. Symmetric with
`POWER_DELTA_BEATDOWN_GAP` on the board-power axis.

Used by `assess_role` in `ai/evaluator.py`.
"""

# ── Creature-stat scaling (P/T → life-point equivalent) ─────────
CREATURE_POWER_VALUE: float = 1.5
"""Rules-constant: life-point equivalent of one point of creature
power.

1.5 ≈ the average damage a 1-power creature deals over its
combat lifetime (≈ 1.5 unblocked attacks before it trades or is
removed), normalised so a 2/2 vanilla scores ~3.0 + ~1.6 = ~4.6 —
the canonical "average creature" baseline.

Sister constant: `CREATURE_TOUGHNESS_VALUE` — defensive side of the
same P/T → life-points conversion.
"""

CREATURE_TOUGHNESS_VALUE: float = 0.8
"""Rules-constant: life-point equivalent of one point of creature
toughness.

0.8 < `CREATURE_POWER_VALUE` because toughness is defensive (only
matters in combat) while power both attacks and blocks. Together
the pair calibrates a 2/2 vanilla to ~4.6 life-points.
"""

# ── Tag-derived ability bonuses (_ability_bonus) ────────────────
ABILITY_BONUS_ETB_VALUE: float = 2.0
"""Rules-constant: bonus for cards tagged `etb_value` — ETB
creatures are worth blinking / protecting.

2.0 ≈ one cantrip's worth of card-advantage value; ETB triggers
recur via blink / flicker effects so the bonus rewards engine
potential beyond raw P/T.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ABILITY_BONUS_CARD_ADVANTAGE: float = 3.0
"""Rules-constant: bonus for cards tagged `card_advantage` — they
draw cards each turn = snowball engine.

3.0 = `CARD_IN_HAND_VALUE` (2.5) + one turn's amortised draw — a
single activation already justifies the bonus, and the recurring
nature compounds across the rest of the game.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ABILITY_BONUS_COST_REDUCER: float = 2.5
"""Rules-constant: bonus for cards tagged `cost_reducer` — enables
cheaper spells = engine piece.

Slightly below `ABILITY_BONUS_CARD_ADVANTAGE` because cost reduction
is conditional (needs a spell to reduce) while draw is unconditional.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ABILITY_BONUS_TOKEN_MAKER: float = 1.5
"""Rules-constant: bonus for cards tagged `token_maker` — creates
board presence over time.

1.5 ≈ value of one token-creature per turn × 2 turn residency.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ABILITY_BONUS_THREAT: float = 1.0
"""Rules-constant: bonus for cards tagged `threat` — flagged as a
significant clock contribution.

1.0 = one life-point of additional clock pressure per turn vs the
default creature curve.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ABILITY_BONUS_COMBO: float = 2.0
"""Rules-constant: bonus for cards tagged `combo` (a piece in the
deck's combo line) at the permanent-evaluation layer.

2.0 captures "combo piece is worth more than vanilla" without
duplicating the heavier `COMBO_PIECE_SPELL_BONUS` (5.5) used at
spell-cast time — once on board, the piece's combo value is partly
realised, so the residual bonus is smaller.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ABILITY_BONUS_PROTECTION: float = 1.0
"""Rules-constant: bonus for cards tagged `protection` — protects
other pieces.

1.0 ≈ one prevented removal worth ~one card.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ABILITY_BONUS_EQUIPMENT: float = 1.0
"""Rules-constant: bonus for cards tagged `equipment` — force
multiplier when paired with a creature.

1.0 standalone (idle equipment); the active-buff case is handled by
`_permanent_value`'s equipped-detection branch.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

# ── Ability-type bonuses (recurring vs one-shot) ────────────────
ABILITY_TYPE_ATTACK_TRIGGER: float = 2.5
"""Rules-constant: bonus for creatures with attack-triggered
abilities (Goblin Guide reveal, Ragavan dash-treasure).

2.5 ≈ value of one trigger × ~2 attacks before removal — attack
triggers generate value EVERY combat phase, so they accumulate
faster than ETB.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ABILITY_TYPE_DIES_TRIGGER: float = 1.5
"""Rules-constant: bonus for creatures with dies-triggered
abilities (Mayhem Devil-style, Bloodghast-style return).

1.5 ≈ one mana-equivalent payback when the opponent removes the
creature; it makes the trade neutral-or-favourable.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ABILITY_TYPE_ACTIVATED: float = 1.5
"""Rules-constant: bonus for permanents with activated abilities
(loyalty-style options every turn).

1.5 = one activation per turn × residual residency value.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ABILITY_TYPE_STATIC: float = 1.0
"""Rules-constant: bonus for permanents with static abilities
(anthems, cost-reducer auras).

Smaller than activated/triggered because static effects only matter
when the relevant board state exists.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ABILITY_TYPE_UPKEEP: float = 1.5
"""Rules-constant: bonus for permanents with upkeep triggers
(Phyrexian Arena-style recurring value).

Equal to `ABILITY_TYPE_DIES_TRIGGER` — both fire reliably each turn
the permanent survives.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

# ── Oracle-derived effect magnitudes ────────────────────────────
ORACLE_DRAW_VALUE_PER_CARD: float = 1.0
"""Rules-constant: per-card-drawn life-point equivalent for
oracle-text-detected draw effects ("draw N cards").

Used by `_ability_bonus` in `ai/evaluator.py`. Each drawn card is
~1 life-point of advantage in the evaluator's calibrated scale.
"""

ORACLE_RECURRING_DRAW_BONUS: float = 2.0
"""Rules-constant: bonus when oracle text matches "whenever ... draw"
— recurring draw triggers (Phyrexian Arena, Sylvan Library).

2.0 = `ABILITY_BONUS_ETB_VALUE`-class engine bonus.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ORACLE_RECURRING_DAMAGE_PER_POINT: float = 0.8
"""Rules-constant: per-damage-point bonus when the source deals
damage to "each opponent" / "each player" each turn (Pulse of Murasa
class, Sulfuric Vortex, Underworld Dreams).

Slightly below `CREATURE_TOUGHNESS_VALUE` (0.8) on purpose — same
order, since recurring damage roughly equates to a defensive stat
on the opponent's side.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ORACLE_TRIGGER_DAMAGE_PER_POINT: float = 0.5
"""Rules-constant: per-damage-point bonus when the source deals
damage as an ETB or attack trigger (Hellrider-style).

½ × `ORACLE_RECURRING_DAMAGE_PER_POINT` because trigger damage is
conditional on the trigger firing (creature must attack / survive).

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ORACLE_LIFE_GAIN_PER_POINT: float = 0.3
"""Rules-constant: per-life-point bonus for oracle-text-detected
life gain effects.

Below `ORACLE_TRIGGER_DAMAGE_PER_POINT` because gaining life is
strictly less impactful than dealing damage at parity (20-life
default means damage closes the game while gain only stalls).

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ORACLE_TOKEN_CREATION_BONUS: float = 1.5
"""Rules-constant: bonus when oracle text contains both "create" and
"token" (an unconditional token-maker not already tagged).

Equal to `ABILITY_BONUS_TOKEN_MAKER` — same intent reached via
oracle scan.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ORACLE_TUTOR_BONUS: float = 2.0
"""Rules-constant: bonus when oracle text contains "search your
library" — tutoring is powerful (deck-thinning + threat-selection).

2.0 = one cantrip's worth of card selection.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ORACLE_LOCK_PIECE_BONUS: float = 3.0
"""Rules-constant: bonus when oracle text contains a tax/lock phrase
("can't cast", "spells cost", "additional cost").

3.0 = `ABILITY_BONUS_CARD_ADVANTAGE`-class — lock pieces are high-
priority targets because they invalidate multiple opponent cards.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ORACLE_RECUR_BONUS: float = 1.5
"""Rules-constant: bonus when oracle text contains a graveyard-
recursion keyword ("escape", "flashback", "undying", "persist",
"return ... from ... graveyard").

1.5 ≈ the marginal value of a second cast / activation cycle.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

ORACLE_SCALE_OVER_TIME_BONUS: float = 1.0
"""Rules-constant: bonus when oracle text describes self-scaling
("put a +1/+1 counter", "gets +", "additional +") — the permanent
grows over time.

1.0 = one scaling tick's life-point contribution.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

# ── CMC-tier residency bonuses ──────────────────────────────────
HIGH_CMC_THRESHOLD: int = 6
"""Rules-constant: CMC threshold above which a stuck permanent is
"expensive" and gets a top-tier residency bonus.

6 ≈ the line between midrange threats (4-5 CMC) and finishers
(6+ CMC) — finishers that resolve typically end the game.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

MID_CMC_THRESHOLD: int = 4
"""Rules-constant: CMC threshold above which a stuck permanent is
"midrange" and gets a smaller residency bonus.

4 = the standard midrange-threat CMC (Snapcaster Mage class is 2
CMC but its supporting cast lives at 4-5).

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

HIGH_CMC_RESIDENCY_BONUS: float = 1.5
"""Rules-constant: residency bonus for high-CMC (>= 6) permanents
that have stuck on the battlefield.

1.5 = `ABILITY_BONUS_TOKEN_MAKER`-class — represents the impact
amplification of a 6+ CMC threat (it would not have been worth
casting if the impact were small).

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

MID_CMC_RESIDENCY_BONUS: float = 0.5
"""Rules-constant: residency bonus for mid-CMC (>= 4 and < 6)
permanents.

⅓ × `HIGH_CMC_RESIDENCY_BONUS` — proportional to the impact gap
between 4-CMC and 6+-CMC threats.

Used by `_ability_bonus` in `ai/evaluator.py`.
"""

# ── Removal valuation (estimate_removal_value) ──────────────────
EQUIPMENT_BUFF_VALUE_MULTIPLIER: float = 1.5
"""Rules-constant: multiplier on (actual_power − base_power) when
the target carries equipment buff tags.

1.5 = `CREATURE_POWER_VALUE` — the buff is treated as raw extra
power on the carrier, valued at the same per-point rate.

Used by `estimate_removal_value` in `ai/evaluator.py`.
"""

REMOVAL_TEMPO_DELTA_PER_CMC: float = 0.5
"""Rules-constant: per-CMC tempo-swing weight applied to
(target_cmc − removal_cmc).

0.5 ≈ ½ life-point per mana of tempo gained — a 1-mana removal
killing a 4-mana threat scores +1.5 tempo (3 × 0.5), making cheap
removal on big threats clearly favourable.

Used by `estimate_removal_value` in `ai/evaluator.py`.
"""

# ── Spell-damage estimation (_estimate_spell_damage_for_eval) ───
SPELL_DAMAGE_DESTROY_EXILE_SENTINEL: int = 99
"""Sentinel: returned by `_estimate_spell_damage_for_eval` for
destroy/exile spells (Path to Exile, Swords to Plowshares, Wrath).

99 is large enough to clear any toughness in Modern (Eldrazi top
out at 15) — used as a "kills anything" marker so the lethality
gate in `estimate_spell_value` always passes for hard removal.

Used by `_estimate_spell_damage_for_eval` and the lethality gate in
`estimate_spell_value` (`ai/evaluator.py`).
"""

SPELL_DAMAGE_ENERGY_FALLBACK: int = 2
"""Rules-constant: conservative damage estimate for energy-scaling
removal (Galvanic Discharge) when oracle text doesn't yield an
explicit number.

2 ≈ assumes the caster has zero energy reserves on cast — the
floor case for energy-scaling burn.

Used by `_estimate_spell_damage_for_eval` in `ai/evaluator.py`.
"""

SPELL_DAMAGE_DOMAIN_MAX: int = 5
"""Rules-constant: maximum domain count in 5c decks — used as
upper-bound damage estimate for domain-scaling removal (Tribal
Flames).

Modern's max domain is 5 (Plains, Island, Swamp, Mountain, Forest);
this constant is the saturated case for 5c manabases.

Used by `_estimate_spell_damage_for_eval` in `ai/evaluator.py`.
"""

SPELL_DAMAGE_X_SPELL_FALLBACK: int = 3
"""Rules-constant: conservative damage estimate for X-cost burn
spells (Fireball, Crash Through-style X) when X isn't specified.

3 = avg X-payment in midgame (≈ available_mana − cmc_other_spells).

Used by `_estimate_spell_damage_for_eval` in `ai/evaluator.py`.
"""

# ── Spell-scoring constants (estimate_spell_value) ──────────────
ROLE_BEATDOWN_THREAT_MULTIPLIER: float = 1.2
"""Rules-constant: multiplier on creature value when we're in the
BEATDOWN role.

+20% reflects the role's "press the clock" intent — threats are
worth more when their job is to close the game.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

ROLE_CONTROL_SMALL_CREATURE_DAMPER: float = 0.7
"""Rules-constant: multiplier on creature value when we're CONTROL
and the creature has power <= 1.

−30% reflects that small bodies don't help control's plan — they
neither apply pressure nor stabilise vs real threats.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

HASTE_IMMEDIATE_BONUS: float = 1.1
"""Rules-constant: additive bonus on top of keyword pricing for
haste creatures (one extra attack the turn it lands).

1.1 ≈ `CREATURE_POWER_VALUE` (1.5) × 0.7 expected damage — a 2-power
haster swings for 1.4 expected damage on the deploy turn.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

REMOVAL_NO_LETHAL_PENALTY: float = 15.0
"""Sentinel: penalty applied to damage-based removal when no
opponent creature is killable by the spell's damage.

−15 is below `pass_threshold` for every archetype — the spell
short-circuits and won't be cast.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

REMOVAL_TARGET_VALUE_MULTIPLIER: float = 1.2
"""Rules-constant: multiplier on best target's permanent-value when
scoring removal.

1.2 (was 0.7 pre-iteration-2) properly values removing a Ragavan
T1 above merely deploying our own 2-drop. Anchored to
`ROLE_BEATDOWN_THREAT_MULTIPLIER` — symmetric across the
threat/answer axis.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

REMOVAL_MUST_KILL_THRESHOLD: float = 7.0
"""Rules-constant: target permanent-value at or above which the
target counts as "must-kill" (high-value engine / value generator).

7.0 ≈ `ABILITY_BONUS_CARD_ADVANTAGE` (3.0) + a midrange creature's
P/T (≈ 4.0) — the threshold where the creature has crossed into
engine territory.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

REMOVAL_MUST_KILL_BONUS: float = 3.0
"""Rules-constant: extra bonus added when removing a must-kill
target (>= REMOVAL_MUST_KILL_THRESHOLD).

3.0 = `ABILITY_BONUS_CARD_ADVANTAGE`-class — represents the
preserved EV of NOT letting the engine generate one more card.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

REMOVAL_NONCREATURE_FALLBACK_BONUS: float = 3.0
"""Rules-constant: bonus when removal can hit non-creature
permanents (artifacts, enchantments) but no creatures exist.

3.0 = the conservative lower bound on a non-creature target's
value (mana rocks, anthems, equipment).

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

REMOVAL_NO_TARGET_PENALTY: float = 5.0
"""Sentinel: penalty applied when removal has no legal targets at
all (or only land targets).

−5 = pass_threshold-class — gate the spell from being cast on an
empty board.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

ROLE_CONTROL_REMOVAL_MULTIPLIER: float = 1.4
"""Rules-constant: multiplier on removal value when we're in the
CONTROL role.

+40% reflects control's reliance on 1-for-1 trades — removal is
the deck's primary game plan, not a side effect.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

# ── Board-wipe scoring ──────────────────────────────────────────
WIPE_NET_DESTROYED_VALUE: float = 3.0
"""Rules-constant: per-net-creature life-point value when a board
wipe destroys more opp creatures than ours.

3.0 ≈ the average creature's life-point value in this evaluator's
scale (a 2-power creature × `CREATURE_POWER_VALUE`).

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

WIPE_FAVOURABLE_FLAT_BONUS: float = 2.0
"""Rules-constant: flat additive bonus on top of net-destroyed
value when a wipe is favourable.

2.0 ≈ `ABILITY_BONUS_ETB_VALUE` — the "we got initiative back"
component of a clean wipe.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

WIPE_OPP_CREATURE_VALUE: float = 2.0
"""Rules-constant: per-opp-creature value when wiping with
non-strict-favourable parity (>= 2 opp creatures).

2.0 ≈ a small body's life-point value.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

WIPE_MY_CREATURE_LOSS_VALUE: float = 1.5
"""Rules-constant: per-my-creature loss penalty when wiping with
parity (we're trading our own board too).

1.5 < `WIPE_OPP_CREATURE_VALUE` because we accept the wipe even
when slightly down (board reset > board down 1).

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

# ── Card-advantage / cantrip ────────────────────────────────────
CARD_DRAW_BASE_VALUE: float = 2.0
"""Rules-constant: base value for cards tagged `card_advantage` or
`cantrip` at spell-evaluation time.

Equal to `ABILITY_BONUS_ETB_VALUE` — single-cast draw is roughly
one cantrip's worth of EV.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

CARD_DRAW_CONTROL_BONUS: float = 1.5
"""Rules-constant: extra bonus to card draw when in CONTROL role.

Control wins via card-advantage attrition; +1.5 captures that
asymmetry without overlapping `ROLE_CONTROL_REMOVAL_MULTIPLIER`
(which scales removal, not draw).

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

CANTRIP_REPLACEMENT_BONUS: float = 0.5
"""Rules-constant: extra bonus when the spell is tagged `cantrip`
(replaces itself in hand).

0.5 ≈ ½ × `CARD_DRAW_BASE_VALUE` — a cantrip is "free" but the EV
captured here is just the no-card-cost adjustment.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

# ── Discard / disruption ────────────────────────────────────────
DISCARD_EARLY_GAME_VALUE: float = 4.0
"""Rules-constant: discard value during the early game (before
either side has set up a clock).

4.0 = `ORACLE_RECUR_BONUS` × ~3 — early discard is "powerful"
because the opponent's hand still contains the entire game plan.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

DISCARD_LATE_GAME_VALUE: float = 1.5
"""Rules-constant: discard value after the early-game window has
closed.

1.5 ≈ `ORACLE_RECUR_BONUS` — late discard is mostly just stripping
top-decks, not real game-plan disruption.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

# ── Silence / counterspell / blink ──────────────────────────────
SILENCE_THREAT_CMC_THRESHOLD: int = 3
"""Rules-constant: CMC threshold above which a hand card counts as
a "key threat" worthy of silence-protection.

3 = the inflection between cantrips and real threats (most Modern
threats live at 3+ CMC).

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

SILENCE_PROTECT_BONUS: float = 3.0
"""Rules-constant: bonus when casting silence to protect a key
deployment.

3.0 = `ABILITY_BONUS_CARD_ADVANTAGE`-class — protecting a 3+ CMC
play from a counter is worth roughly the EV preserved.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

SILENCE_NO_TARGET_PENALTY: float = 20.0
"""Sentinel: penalty when casting silence with nothing to protect.

−20 ≈ −2 × `SILENCE_PROTECT_BONUS` — strongly gates the spell from
being cast pre-emptively.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

COUNTERSPELL_PROACTIVE_PENALTY: float = 10.0
"""Sentinel: penalty for casting a counterspell during main phase
(rather than reactively).

−10 ensures counterspells never score as proactive plays — they
belong in `ai/response.py`'s decide_response path.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

BLINK_ETB_AVAILABLE_BONUS: float = 2.5
"""Rules-constant: bonus when casting blink with an ETB-creature
on board.

2.5 = `ABILITY_TYPE_ATTACK_TRIGGER`-class — re-triggering an ETB is
roughly one extra trigger's worth of value.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

BLINK_NO_ETB_PENALTY: float = 5.0
"""Sentinel: penalty when casting blink with creatures but no
ETB-tagged ones (the spell exists but has no high-value target).

−5 = pass_threshold-class — gates the cast unless held for
protection.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

BLINK_NO_CREATURES_PENALTY: float = 15.0
"""Sentinel: penalty when casting blink with no creatures on board
at all (spell fizzles).

−15 = `REMOVAL_NO_LETHAL_PENALTY`-class — strongly negative.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

# ── Prowess / spell-trigger synergy ─────────────────────────────
PROWESS_PER_CREATURE_BASE: float = 2.5
"""Rules-constant: per-prowess-creature bonus when casting a
noncreature spell.

2.5 ≈ +1/+1 to one prowess creature × ~2 attacks before removal —
the lifetime value of a single prowess trigger.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

PROWESS_PER_CREATURE_CHEAP_BONUS: float = 1.5
"""Rules-constant: extra per-prowess-creature bonus when the spell
costs 1 or less (chains better).

1.5 ≈ "cheap = more triggers per turn" amortisation.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

PROWESS_PER_CREATURE_FREE_BONUS: float = 2.0
"""Rules-constant: extra per-prowess-creature bonus when the spell
is free (cmc 0 — Mutagenic Growth, Lava Dart flashback).

2.0 ≈ pure-trigger value with no opportunity cost.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

# ── Combo piece ─────────────────────────────────────────────────
COMBO_PIECE_SPELL_BONUS: float = 5.5
"""Rules-constant: bonus when casting a card tagged `combo` (a
piece in the deck's combo line).

5.5 = `ORACLE_LOCK_PIECE_BONUS` (3.0) + `ABILITY_BONUS_CARD_ADVANTAGE`
(3.0) − `CANTRIP_REPLACEMENT_BONUS` (0.5) — combo pieces are nearly
must-cast for combo decks.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

# ── Ramp / mana production ──────────────────────────────────────
RAMP_EARLY_GAME_BONUS: float = 4.0
"""Rules-constant: ramp value during the early game (when the
mana boost compounds across many turns).

4.0 = `DISCARD_EARLY_GAME_VALUE` — early ramp is strictly tempo-
positive.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

RAMP_MID_GAME_BONUS: float = 2.0
"""Rules-constant: ramp value during the mid game.

½ × `RAMP_EARLY_GAME_BONUS` — fewer turns left to compound the
mana advantage.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

RAMP_LATE_GAME_BONUS: float = 0.5
"""Rules-constant: ramp value during the late game.

Near-zero — by the late game the mana boost rarely matters.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

# ── Mana efficiency ─────────────────────────────────────────────
MANA_EFFICIENCY_GOOD_LOW: float = 0.6
"""Rules-constant: lower bound of the "using mana well" efficiency
band (cmc / available_mana).

0.6 ≈ the threshold below which we're significantly under-using
our mana for the turn.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

MANA_EFFICIENCY_GOOD_HIGH: float = 1.0
"""Rules-constant: upper bound of the "using mana well" efficiency
band.

1.0 = full mana usage; above this would mean over-cost (impossible
without floating mana, so 1.0 is the natural cap).

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

MANA_EFFICIENCY_GOOD_BONUS: float = 1.1
"""Rules-constant: bonus when efficiency lies in the "using mana
well" band.

1.1 ≈ `HASTE_IMMEDIATE_BONUS`-scale — small but positive.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

MANA_EFFICIENCY_LOW_THRESHOLD: float = 0.3
"""Rules-constant: efficiency threshold below which a cheap spell
gets a small "fine but suboptimal" bonus.

0.3 ≈ casting a 1-mana spell with 4 available — common situation
worth a small acknowledgement bonus.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

MANA_EFFICIENCY_LOW_BONUS: float = 0.5
"""Rules-constant: bonus for cheap spells in the "low efficiency"
band (cmc > 0 and efficiency < 0.3).

½ × `MANA_EFFICIENCY_GOOD_BONUS` — recognises the cast without
endorsing the floating mana.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

UNCASTABLE_PENALTY: float = 10.0
"""Sentinel: hard floor for spells we don't have mana to cast
(mana_after < 0).

−10 ensures the spell is never selected. Acts as the upper bound
on the literal "−10.0 = can't cast" gate.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

TAP_OUT_HOLDING_REMOVAL_PENALTY: float = 2.0
"""Rules-constant: penalty when tapping out forfeits an instant-
speed removal in hand against an active opp board.

2.0 ≈ `WIPE_OPP_CREATURE_VALUE` — the EV of one removal cast we
gave up.

Used by `estimate_spell_value` in `ai/evaluator.py`.
"""

# ── Life valuation curve (_life_value) ──────────────────────────
LIFE_DEAD_PENALTY: float = 50.0
"""Sentinel: returned for life <= 0 (we're dead).

50.0 ≈ ½ × `LETHAL_THREAT` — large enough to dominate any other
evaluation term.

Used by `_life_value` in `ai/evaluator.py`.
"""

LIFE_BAND_CRITICAL_MAX: int = 3
"""Rules-constant: upper bound of the "critical" life band
(life <= 3).

3 ≈ one Bolt or one swing-from-2 away from death; each life
point in this band is worth `LIFE_PER_POINT_CRITICAL`.

Used by `_life_value` in `ai/evaluator.py`.
"""

LIFE_PER_POINT_CRITICAL: float = 3.0
"""Rules-constant: per-life-point value in the critical band.

3.0 = `WIPE_NET_DESTROYED_VALUE`-class — each point of life is
worth roughly one creature when we're about to die.

Used by `_life_value` in `ai/evaluator.py`.
"""

LIFE_BAND_DANGER_MAX: int = 7
"""Rules-constant: upper bound of the "dangerous" life band
(3 < life <= 7).

7 ≈ within-range of a 2-card combo kill (Lightning Bolt + Lava
Spike).

Used by `_life_value` in `ai/evaluator.py`.
"""

LIFE_DANGER_BAND_BASE: float = 9.0
"""Rules-constant: piecewise-linear base value at the start of the
dangerous band (life == 3).

9.0 = `LIFE_BAND_CRITICAL_MAX` × `LIFE_PER_POINT_CRITICAL`
(3 × 3.0) — preserves continuity with the critical band.

Used by `_life_value` in `ai/evaluator.py`.
"""

LIFE_PER_POINT_DANGER: float = 2.0
"""Rules-constant: per-life-point value in the dangerous band.

⅔ × `LIFE_PER_POINT_CRITICAL` — still scary but no longer one
shock from death.

Used by `_life_value` in `ai/evaluator.py`.
"""

LIFE_BAND_NORMAL_MAX: int = 15
"""Rules-constant: upper bound of the "normal" life band
(7 < life <= 15).

15 ≈ a Boros / Burn pre-game life total below which the average
shock-damage opp is making meaningful progress.

Used by `_life_value` in `ai/evaluator.py`.
"""

LIFE_NORMAL_BAND_BASE: float = 17.0
"""Rules-constant: piecewise-linear base at the start of the
normal band (life == 7).

17.0 = `LIFE_DANGER_BAND_BASE` (9.0) + (LIFE_BAND_DANGER_MAX − 3) ×
`LIFE_PER_POINT_DANGER` (4 × 2.0) — preserves piecewise continuity.

Used by `_life_value` in `ai/evaluator.py`.
"""

LIFE_PER_POINT_NORMAL: float = 1.0
"""Rules-constant: per-life-point value in the normal band.

½ × `LIFE_PER_POINT_DANGER` — life is "saved up" for later in this
range.

Used by `_life_value` in `ai/evaluator.py`.
"""

LIFE_DIMINISHING_BAND_BASE: float = 25.0
"""Rules-constant: piecewise-linear base at the start of the
diminishing band (life == 15).

25.0 = `LIFE_NORMAL_BAND_BASE` (17.0) + (LIFE_BAND_NORMAL_MAX − 7) ×
`LIFE_PER_POINT_NORMAL` (8 × 1.0) — preserves piecewise continuity.

Used by `_life_value` in `ai/evaluator.py`.
"""

LIFE_PER_POINT_DIMINISHING: float = 0.3
"""Rules-constant: per-life-point value above 15 life.

≈ ⅓ × `LIFE_PER_POINT_NORMAL` — extra life above the normal band
has clear diminishing returns (a 30-life Soul Sister won't lose to
chip damage, so the marginal point matters less).

Used by `_life_value` in `ai/evaluator.py`.
"""

# ── Permanent-value (_permanent_value) ──────────────────────────
EQUIPPED_CREATURE_VULNERABILITY_BONUS: float = 2.0
"""Rules-constant: bonus on a creature carrying equipment-tag
instance tags (removing it also wastes the equipment buff).

2.0 = `ABILITY_BONUS_ETB_VALUE`-class — represents the cascade
value lost on removal.

Used by `_permanent_value` in `ai/evaluator.py`.
"""

LAND_BASE_VALUE: float = 1.0
"""Rules-constant: base life-point value of any land permanent.

1.0 ≈ one mana × one cast (the lifetime contribution of a single
land used to cast a 1-mana spell).

Used by `_permanent_value` in `ai/evaluator.py`.
"""

LAND_PER_COLOR_BONUS: float = 0.3
"""Rules-constant: per-mana-symbol bonus for lands with multiple
producible colors (dual lands, fetch-fixed bases).

0.3 ≈ the colour-fixing optionality value per extra colour.

Used by `_permanent_value` in `ai/evaluator.py`.
"""

LAND_UNTAPPED_BONUS: float = 0.5
"""Rules-constant: bonus when a land is untapped (representing
options — cast or hold-up).

0.5 = `CANTRIP_REPLACEMENT_BONUS`-class — small but positive.

Used by `_permanent_value` in `ai/evaluator.py`.
"""

ARTIFACT_MANA_ROCK_VALUE: float = 2.0
"""Rules-constant: base value of a mana-producing artifact.

2.0 = `ABILITY_BONUS_ETB_VALUE`-class — a Sol Ring-class accelerator
contributes ~2 life-points of advantage.

Used by `_permanent_value` in `ai/evaluator.py`.
"""

ARTIFACT_EQUIPMENT_ACTIVE_VALUE: float = 4.0
"""Rules-constant: value of an equipment that's currently equipped
(actively boosting a creature).

4.0 ≈ `RAMP_EARLY_GAME_BONUS`-class — equipment turns a 1/1 into a
3/3 in many cases, which is large.

Used by `_permanent_value` in `ai/evaluator.py`.
"""

ARTIFACT_EQUIPMENT_IDLE_VALUE: float = 1.0
"""Rules-constant: value of an idle (un-equipped) equipment
permanent.

1.0 = `LAND_BASE_VALUE` — sitting equipment has potential but no
realised impact.

Used by `_permanent_value` in `ai/evaluator.py`.
"""

ARTIFACT_GENERIC_PER_CMC: float = 0.5
"""Rules-constant: per-CMC value for artifacts that aren't mana
sources or equipment.

0.5 = `MID_CMC_RESIDENCY_BONUS`-class — a 2-CMC artifact is worth
~1 life-point.

Used by `_permanent_value` in `ai/evaluator.py`.
"""

ENCHANTMENT_PER_CMC: float = 0.8
"""Rules-constant: per-CMC value for enchantments.

0.8 > `ARTIFACT_GENERIC_PER_CMC` because enchantments tend to have
stronger per-mana effects (Sphere of Resistance, Leyline class).

Used by `_permanent_value` in `ai/evaluator.py`.
"""

PLANESWALKER_BASE_VALUE: float = 4.0
"""Rules-constant: flat base value for planeswalker permanents.

4.0 = `RAMP_EARLY_GAME_BONUS`-class — represents the threat-of-
activation value beyond raw loyalty.

Sister constant: `PLANESWALKER_SURVIVAL_FLOOR` (3.0) — survival floor
in `ai/ev_player.py`'s spell-cast layer.

Used by `_permanent_value` in `ai/evaluator.py`.
"""

PLANESWALKER_LOYALTY_VALUE: float = 0.5
"""Rules-constant: per-loyalty-counter bonus for planeswalkers.

0.5 ≈ `MID_CMC_RESIDENCY_BONUS`-class — each loyalty counter is
~0.5 life-points of future activations.

Used by `_permanent_value` in `ai/evaluator.py`.
"""


# ─── Discard advisor constants (ai/discard_advisor.py) ──────────────
# `ai/discard_advisor.py` ranks self-discard candidates so the AI bins
# the most graveyard-useful card (or the most-excess card). The scores
# are an ORDER, not a calibration — only relative ordering matters, so
# the constants encode tier boundaries and within-tier nudges.
#
# Score tiers (ascending priority):
#   - Removal-keep nudge:   +DISCARD_REMOVAL_NUDGE      (10)
#   - Counterspell-keep:    +DISCARD_COUNTERSPELL_NUDGE (20)
#   - Combo/tutor protect:  -DISCARD_COMBO_TUTOR_PROTECT (30)
#   - Excess lands (3-deep):+DISCARD_LANDS_EXCESS_BONUS  (40)
#   - Excess lands (gluts): +DISCARD_LANDS_GLUT_BONUS    (50)
#   - Big creature fuel:    +DISCARD_BIG_CREATURE_BASE   (80) + cmc
#   - Flashback target:     +DISCARD_FLASHBACK_BONUS     (90)
#   - Escape target:        +100 (rules-exempt — see GV-1 docstring)
#   - Reanimation fuel:     +100 + cmc (rules-exempt — see GV-1)

DISCARD_FLASHBACK_BONUS: int = 90
"""Rules-constant: discard-score for cards tagged `flashback` — they
WANT to be in the graveyard (Faithful Mending, Lingering Souls).

90 sits below the +100 escape bonus and reanimation-fuel threshold,
above all generic "good-to-bin" tiers (60-50-40 land glut). This
ordering means: reanimation fuel > escape > flashback > heavy
creature fuel > land glut > nudges.

Used by `discard_score` in `ai/discard_advisor.py`.
"""

DISCARD_BIG_CREATURE_CMC_THRESHOLD: int = 5
"""Rules-constant: CMC at or above which a creature-in-hand is
treated as accidental reanimator fuel (generic fallback for decks
without a declared FILL_RESOURCE graveyard goal).

5 = the same floor as `_reanimation_fuel_min_cmc`'s
REANIMATION_FUEL_FLOOR — Goryo's / Persist / Unburial Rites all
target 5+ CMC creatures; cheaper creatures should be hard-cast.

Used by `discard_score` in `ai/discard_advisor.py`.
"""

DISCARD_BIG_CREATURE_BASE: int = 80
"""Rules-constant: base score for high-CMC creatures (>=
DISCARD_BIG_CREATURE_CMC_THRESHOLD) that could be reanimation
targets even without a declared graveyard plan.

80 < DISCARD_FLASHBACK_BONUS (90) — flashback cards are stronger
graveyard signals than accidental fat creatures. The +cmc tiebreaker
prefers binning the biggest body.

Used by `discard_score` in `ai/discard_advisor.py`.
"""

DISCARD_LANDS_GLUT_THRESHOLD: int = 3
"""Rules-constant: minimum lands-on-battlefield at which an extra
land-in-hand counts as excess (we already have enough mana).

3 lands ≈ enough to cast most Modern threats; additional lands
beyond this floor are excess in most matchups.

Used by `discard_score` in `ai/discard_advisor.py`.
"""

DISCARD_LANDS_GLUT_BONUS: int = 50
"""Rules-constant: discard-score for an excess land when we have
DISCARD_LANDS_GLUT_THRESHOLD lands on the battlefield AND > 1 land
in hand.

50 sits above the deep-glut (40) tier — once the battlefield has
enough mana, additional lands in hand are dead draws.

Used by `discard_score` in `ai/discard_advisor.py`.
"""

DISCARD_LANDS_EXCESS_BONUS: int = 40
"""Rules-constant: discard-score for a land when we have many
lands-in-hand (>2) but the battlefield doesn't yet show 3+ lands.

40 < DISCARD_LANDS_GLUT_BONUS (50) — flooded-but-still-developing
hands are slightly less keen to bin lands than hands that have
already hit critical mass on the battlefield.

Used by `discard_score` in `ai/discard_advisor.py`.
"""

DISCARD_COUNTERSPELL_NUDGE: int = 20
"""Rules-constant: small nudge to discard a non-creature
counterspell over a removal spell.

20 < DISCARD_COMBO_TUTOR_PROTECT (30) — counterspells are
protection but combo/tutor pieces are wincon-critical, so the
ordering correctly prioritises keeping the wincon.

Used by `discard_score` in `ai/discard_advisor.py`.
"""

DISCARD_COMBO_TUTOR_PROTECT: int = 30
"""Rules-constant: NEGATIVE score adjustment to keep combo / tutor
cards out of the discard pile.

-30 lowers the discard score below the keep-class baseline of
+10 (removal nudge) so combo/tutor pieces are the LAST to be
discarded. The exception in source (`if not (creature & cmc >= 5)`)
preserves reanimation-fuel intent.

Used by `discard_score` in `ai/discard_advisor.py`.
"""

DISCARD_REMOVAL_NUDGE: int = 10
"""Rules-constant: small nudge AGAINST discarding removal — we
slightly prefer keeping interaction.

+10 puts removal above bare-baseline (0) but well below land-excess
(40+) and graveyard-target (80+) tiers. Removal is moderately
important; we'd rather bin a flooded land than a Bolt.

Used by `discard_score` in `ai/discard_advisor.py`.
"""


# ─── Bayesian-hand-inference hold-rates (ai/bhi.py) ──────────────────
# Used by `BayesianHandTracker.observe_priority_pass` to model the
# probability that the opponent held interaction (counter / removal)
# even though they could have used it. Values reflect rational-player
# behaviour: hold rate decreases as opponent's life pressure increases
# (desperate players counter everything; comfortable players save
# interaction for bigger threats).

COUNTER_HOLD_RATE_DEFAULT: float = 0.3
"""Derived: default P(opp holds counter | opp has counter) when their
life is in the comfortable mid-range and we're not early game.

Reflects a rational player's willingness to hold interaction for a
better target ~30% of the time. Below this fraction the player feels
under enough pressure to fire on any target; above it the player has
slack to wait.

Sister constants: COUNTER_HOLD_RATE_DESPERATE (low-life) and
COUNTER_HOLD_RATE_EARLY_GAME (saving for bigger target).

Used by `observe_priority_pass` in `ai/bhi.py`.
"""


COUNTER_HOLD_RATE_DESPERATE: float = 0.15
"""Derived: P(opp holds counter | opp has counter) when opp_life ≤
COUNTER_HOLD_OPP_LIFE_DESPERATE (10). Half of the default rate —
desperate players counter almost any threat because untreated threats
will kill them quickly.

Sister constant: COUNTER_HOLD_RATE_DEFAULT (the comfortable mid-range
rate this halves from).

Used by `observe_priority_pass` in `ai/bhi.py`.
"""


COUNTER_HOLD_RATE_EARLY_GAME: float = 0.4
"""Derived: P(opp holds counter | opp has counter) in the early game
(detected via `ai.clock.is_early_game`). Slightly above the default
rate — early game the opponent has more turns ahead in which to use
the counter, so saving for a bigger target is more rational.

Sister constant: COUNTER_HOLD_RATE_DEFAULT.

Used by `observe_priority_pass` in `ai/bhi.py`.
"""


REMOVAL_HOLD_RATE_DEFAULT: float = 0.25
"""Derived: default P(opp holds instant removal | opp has it) when
not at desperate life. Slightly below COUNTER_HOLD_RATE_DEFAULT
because removal is more target-specific (must hit a creature) so
saving it is less commonly profitable than saving a counter.

Sister constant: REMOVAL_HOLD_RATE_DESPERATE.

Used by `observe_priority_pass` in `ai/bhi.py`.
"""


REMOVAL_HOLD_RATE_DESPERATE: float = 0.1
"""Derived: P(opp holds removal | opp has it) when opp_life ≤
REMOVAL_HOLD_OPP_LIFE_DESPERATE (8). Lower than the default rate
because at near-lethal life every creature is a kill threat — opp
must fire removal immediately or lose.

Sister constant: REMOVAL_HOLD_RATE_DEFAULT.

Used by `observe_priority_pass` in `ai/bhi.py`.
"""


COUNTER_HOLD_OPP_LIFE_DESPERATE: int = 10
"""Rules-constant: opp life at or below which COUNTER_HOLD_RATE_DESPERATE
applies. 10 = half of the Modern starting life total — the empirical
threshold below which players visibly play more reactively.

Used by `observe_priority_pass` in `ai/bhi.py`.
"""


REMOVAL_HOLD_OPP_LIFE_DESPERATE: int = 8
"""Rules-constant: opp life at or below which REMOVAL_HOLD_RATE_DESPERATE
applies. 8 ≈ two Lightning Bolts of distance from lethal — empirically
the threshold at which removal-based decks must answer every threat.

Used by `observe_priority_pass` in `ai/bhi.py`.
"""


NON_INTERACTION_CAST_DECAY: float = 0.9
"""Derived: multiplicative decay applied to `p_counter` and `p_removal`
when the opponent casts a non-interaction spell. Tapping mana for
something else is weak evidence they don't have interaction held.

10% decay matches the "weak evidence" intent — strong evidence
(observing a cast counter) triggers a full Bayesian recalculation
instead. Re-applied each non-interaction cast, so repeated tapping
out compounds the evidence.

Used by `observe_spell_cast` in `ai/bhi.py`.
"""


OBSERVATION_WEIGHT_CAP: float = 0.7
"""Derived: maximum weight given to observed-pass beliefs over the
fresh per-card density prior. Capped at 0.7 so the static prior
(library composition) always retains at least 30% influence —
prevents the belief from collapsing to a single observed pass when
the unobserved hand still contains relevant information.

Sister constant: OBSERVATION_WEIGHT_PER_OBS — per-observation
increment that ramps toward this cap.

Used by `_recalculate_priors` in `ai/bhi.py`.
"""


OBSERVATION_WEIGHT_PER_OBS: float = 0.1
"""Derived: per-observation increment applied to the observation
weight in the prior/posterior blend. After 7 observations the cap
(OBSERVATION_WEIGHT_CAP = 0.7) binds — empirically 7 priority-passes
is enough to dominate the static prior, matching the typical Modern
"by turn 7 we've seen the relevant cards from opp's hand" heuristic.

Sister constant: OBSERVATION_WEIGHT_CAP.

Used by `_recalculate_priors` in `ai/bhi.py`.
"""


BHI_DISCARD_FLAT_PRIOR: float = 0.5
"""Bayesian flat prior used when the opponent's published gameplan
declares a hand-attack spell (mulligan_keys / critical_pieces /
always_early). With no observational evidence yet, we estimate a 50%
chance they will deploy it before our combo turn — the standard
non-informative "present-or-absent" 0.5 prior.

Used by `BayesianHandTracker._compute_discard_prior` in `ai/bhi.py`.
Posterior probabilities are updated as priority-passes are observed
via `_recalculate_priors`.
"""


# ─── Mulligan keep-score weights (ai/mulligan.py) ────────────────────
# Used by `MulliganDecider._card_keep_score` to rank cards for the
# choose-cards-to-bottom decision. These are role-tag weights, NOT
# per-card scores; values are calibrated against the lands-first
# baseline so that a needed land outranks any spell role and a
# needed spell role outranks an unneeded one.
#
# All values are in the same "card keep score" scale used by
# `ai.gameplan.card_keep_score` (the gameplan-aware path), so the
# fallback heuristic and the gp-aware ordering live on the same
# scale and can swap in either direction.

LEGENDARY_DUPLICATE_PENALTY: float = 50.0
"""Sentinel: penalty subtracted from a duplicate legendary card's
keep-score so it sorts to the bottom of `choose_cards_to_bottom`.

Per CR 704.5j, when a player would control two or more legendaries
of the same name, all but one go to the graveyard. In hand, every
duplicate copy beyond the first is dead on resolution. Magnitude 50
is large enough to drop a duplicate below the highest non-land keep
score (~27 from `ai.gameplan.card_keep_score`) while staying above
the suspend-only sentinel (-100) so it doesn't compound with that.

Used by `_apply_legendary_dedup_penalty` and the inline note in
`MulliganDecider.choose_cards_to_bottom` in `ai/mulligan.py`.
"""

DEFAULT_MULLIGAN_MIN_LANDS: int = 2
"""Rules-constant: default `mulligan_min_lands` floor when no
gameplan declares it. 2 lands cover the typical Modern T1-T2 curve
(one land per turn, plays a 2-CMC spell on T2). Below this floor
the kept hand cannot meaningfully develop.

Used by `MulliganDecider.choose_cards_to_bottom` in `ai/mulligan.py`
as the fallback `min_lands` when the gameplan is silent.
"""

SUSPEND_ONLY_DEAD_PENALTY: float = -100.0
"""Sentinel: keep-score for a 0-CMC suspend-only card (Living End,
Ancestral Vision, Wheel of Fate). These cards cannot be hard-cast
from hand — they exist only to be cycled / suspended / cascaded
into. -100.0 is below every other keep-score so they always sort to
the bottom.

Used by `_card_keep_score` in `ai/mulligan.py`.
"""

KEEP_SCORE_LAND_NEEDED: float = 10.0
"""Derived: keep-score for a land in a hand that has at most 3 lands.
A "needed" land is more valuable than any spell role at this stage —
mana is the gating resource. 10.0 sits above the highest spell-role
weight (KEEP_SCORE_COMBO_AT_HOME = 5.0) plus the cmc bonus, ensuring
needed lands are never bottomed before role-positive spells.

Used by `_card_keep_score` in `ai/mulligan.py`.
"""

KEEP_SCORE_LAND_FLOOD: float = 2.0
"""Derived: keep-score for a land in a hand with 4+ lands. The
hand has enough mana — extra lands compete with spells for keep
priority. 2.0 matches `KEEP_SCORE_THREAT_TAG` so a 5th land sits at
the same keep tier as a generic threat.

Used by `_card_keep_score` in `ai/mulligan.py`.
"""

KEEP_SCORE_LAND_FLOOD_THRESHOLD: int = 3
"""Rules-constant: lands-in-hand count above which extra lands are
treated as flood. 3 lands cover the T1-T3 curve plus one — the
fourth land is the first one not strictly required for on-curve
development.

Used by `_card_keep_score` in `ai/mulligan.py`.
"""

KEEP_SCORE_LAND_PRODUCES_BONUS: float = 0.5
"""Derived: per-color-produced bonus for a land's keep-score.
A dual / shock / surveil land produces 2 colors → +1.0; a tri-land
produces 3 → +1.5; a basic produces 1 → +0.5. Caps the total land
bonus below the role-weighted spell tier so a 3-color land in a
flooded hand doesn't outrank a removal spell.

Used by `_card_keep_score` in `ai/mulligan.py`.
"""

KEEP_SCORE_CMC_INVERTED_CEIL: int = 5
"""Rules-constant: CMC ceiling above which the inverse-CMC bonus
collapses to zero. Modern's mana curve tops out around 5 mana for
midrange and 6+ for ramp; cards above 5 are kept on role weight
alone, not on CMC.

Used by `_card_keep_score` in `ai/mulligan.py` as `max(0, 5 - cmc)`.
"""

KEEP_SCORE_REMOVAL_TAG: float = 3.0
"""Derived: keep-score weight for a card tagged `removal`. 3.0 ≈
`PROACTIVE_REMOVAL_MIN_VALUE` — one card-swap of value, the floor
above which removal is "worth keeping" against a typical Modern
opener.

Used by `_card_keep_score` in `ai/mulligan.py`.
"""

KEEP_SCORE_THREAT_TAG: float = 2.0
"""Derived: keep-score weight for a card tagged `threat`. Slightly
below removal because a threat in opening hand requires mana to
develop, while removal can be reactively held.

Used by `_card_keep_score` in `ai/mulligan.py`.
"""

KEEP_SCORE_EARLY_PLAY_AT_HOME: float = 4.0
"""Derived: keep-score for an `early_play`-tagged card in an aggro
archetype. Above the removal weight because an aggro deck's clock
relies on T1-T2 deploys; without an early play the hand has no
pressure.

Sister constant: KEEP_SCORE_EARLY_PLAY_AWAY (the lower fallback for
non-aggro archetypes).

Used by `_card_keep_score` in `ai/mulligan.py`.
"""

KEEP_SCORE_EARLY_PLAY_AWAY: float = 2.0
"""Derived: keep-score for an `early_play`-tagged card in a non-aggro
archetype. Half the AT_HOME weight — useful but not essential.

Used by `_card_keep_score` in `ai/mulligan.py`.
"""

KEEP_SCORE_COMBO_AT_HOME: float = 5.0
"""Derived: keep-score for a `combo`-tagged card in a combo archetype.
Above every other role weight because a combo deck's hand without a
combo piece is a mulligan candidate — the piece is the entire reason
to keep the hand.

Sister constant: KEEP_SCORE_COMBO_AWAY (lower fallback).

Used by `_card_keep_score` in `ai/mulligan.py`.
"""

KEEP_SCORE_COMBO_AWAY: float = 1.0
"""Derived: keep-score for a `combo`-tagged card in a non-combo
archetype. Combo cards hard-cast (not as combo) are a card-swap of
value — same scale as `CHEAP_REMOVAL_ACTION_BONUS`.

Used by `_card_keep_score` in `ai/mulligan.py`.
"""

KEEP_SCORE_COUNTERSPELL_AT_HOME: float = 3.0
"""Derived: keep-score for a `counterspell`-tagged card in a control
or tempo archetype. Matches the removal weight — both are reactive
interaction the deck relies on.

Sister constant: KEEP_SCORE_COUNTERSPELL_AWAY.

Used by `_card_keep_score` in `ai/mulligan.py`.
"""

KEEP_SCORE_COUNTERSPELL_AWAY: float = 1.0
"""Derived: keep-score for a counterspell in a non-control / non-tempo
archetype. Same scale as `KEEP_SCORE_COMBO_AWAY` — a counter without
a defensive plan is a card-swap of value, not a primary lever.

Used by `_card_keep_score` in `ai/mulligan.py`.
"""


# ─── Sideboard-solver constants (ai/sideboard_solver.py) ─────────────
# Used by `plan_sideboard` and clause evaluators to gate swap decisions
# and supply fallbacks where the deck's own profile is unobservable.

SB_GY_FULL_RELIANCE_TARGET: float = 5.0
"""Rules-constant: number of graveyard creatures at which an opponent's
graveyard reliance saturates (returns 1.0). 5 = full Living End return
(the canonical reanimator-class effect that returns ~5 creatures).

Used by `_gy_reliance` in `ai/sideboard_solver.py` and consumed by
`_clause_gy_hate` to scale graveyard-hate value with opp reliance.
"""

SB_EXPECTED_GY_CREATURES_DENIED: float = 5.0
"""Rules-constant: expected creatures denied per resolved graveyard-
hate effect against a fully-relying opponent. Mirrors
`SB_GY_FULL_RELIANCE_TARGET` — when reliance saturates and we resolve
hate, we deny ~5 creatures' worth of value.

Sister constant: SB_GY_FULL_RELIANCE_TARGET — same canonical
reanimator scale.

Used by `_clause_gy_hate` in `ai/sideboard_solver.py`.
"""

SB_DEFAULT_AVG_CMC: float = 2.5
"""Rules-constant: fallback average CMC for our nonland cards when
the deck has zero non-land templates (degenerate case). 2.5 matches
the empirical Modern average across the 16 tracked decks (also the
value of `AVG_CREATURE_POWER`).

Used by `plan_sideboard` in `ai/sideboard_solver.py` as the
`my_avg_cmc` fallback.
"""

SB_SWAP_EPSILON_MANA_FRACTION: float = 0.5
"""Derived: minimum net-gain (in fractional mana-units) required to
commit a sideboard swap. Below ½ mana-unit the swap is "marginal" and
the churn cost (re-shuffling, missed synergy) outweighs the projected
gain. 0.5 mana-unit ≈ half a Bolt's worth of EV — below the noise
floor of the matrix run.

Used by `plan_sideboard` in `ai/sideboard_solver.py`.
"""


# ─── Combo-calc / storm-chain constants (ai/combo_calc.py) ───────────
# Used by `_compute_combo_value`, the zone assessors, and the
# mid-chain ritual gate to score storm chains and graveyard combos.

COMBO_IDEAL_POSITION_CEIL: float = 100.0
"""Sentinel: ideal `position_value` ceiling for `_compute_combo_value`.
Position-value scales bottom-out at 0 (winning) and top-out at the
NO_CLOCK sentinel (losing); the combo value is `ceil - current_pos`,
so 100.0 sets the upper edge for a chain that resolves into a win.

Matches the LETHAL_THREAT / LETHAL_BONUS scale — same "game-ending
event" tier in different unit spaces.

Used by `_compute_combo_value` in `ai/combo_calc.py`.
"""

COMBO_DIVERGENCE_RES_THRESHOLD: int = 3
"""Rules-constant: r_res (chain-reaction residue) divergence point.
At r_res >= 3 the chain has +3 mana surplus per spell — empirically
the threshold above which drawn fuel can be cast immediately
(cantrips at 1U, rituals at R/U). Below this value the chain hasn't
yet reached self-sustaining mana production.

Used by `_assess_storm_zone` (storm projection) and the mid-chain
ritual patience gate in `ai/combo_calc.py`.
"""

COMBO_EARLY_GAME_LAND_THRESHOLD: int = 4
"""Rules-constant: lands-on-battlefield count above which the early-
game patience factor collapses to zero. 4 lands = T4 land-drop
(typical "cast our 4-mana payoff" threshold); after this point the
deck has access to the resources it needs and waiting no longer
multiplies output.

Used by the patience-penalty gate in `ai/combo_calc.py`.
"""

COMBO_PATIENCE_PENALTY_SCALE: float = 0.2
"""Derived: scaling factor on the early-game patience penalty
(divergence_gap × early_factor × combo_value × scale). 0.2 = ⅕ of
combo_value — large enough to gate a speculative ritual on T1-T2
when no reducer is deployed, small enough that mid-game chains with
adequate r_res aren't hampered.

Used by the patience-penalty gate in `ai/combo_calc.py`.
"""

COMBO_NON_READY_POTENTIAL_FALLBACK: float = 0.5
"""Derived: fallback potential value when a non-storm payoff has no
declared `resource_target`. 0.5 = half of `opp_life` worth of payoff
— the "we still need a chunk of resources" baseline used to compute
the wasted-potential penalty when the combo isn't ready.

Used by the non-storm payoff branch of `card_combo_modifier` in
`ai/combo_calc.py`.
"""

COMBO_RITUAL_MISSED_FINISHER_SCALE: float = 5.0
"""Derived: penalty scale for mid-chain rituals when no finisher path
exists but draws remain in hand. 5.0 ≈ the COUNTER_THRESHOLD scale —
the "one premium-threat tier" weight, applied at storm-coverage
escalation to express the marginal-card cost of an empty chain.

Used by the mid-chain ritual gate in `ai/combo_calc.py`.
"""

COMBO_CASCADE_RISK_SCALE: float = 3.0
"""Derived: penalty scale for the draw-miss cascade-risk term at
storm >= 3 with one draw remaining. 3.0 = `PROACTIVE_REMOVAL_MIN_VALUE`
— the per-card-swap baseline; the chain at near-lethal storm is one
bad top-deck from collapse, and 3.0 expresses that as one card-swap
of expected loss per probability point of miss.

Used by the mid-chain ritual gate in `ai/combo_calc.py`.
"""

COMBO_FLIP_TRANSFORM_VALUE_FRACTION: float = 0.3
"""Derived: fraction of combo_value attributed to a successful flip-
transform on a creature with a "flip a coin" on-cast trigger. 0.3 =
COMBO_PATIENCE_PENALTY_SCALE + 0.1 — the transformation flips the
creature into a planeswalker / engine, contributing roughly ⅓ of the
chain's win value.

Used by the flip-transform stack-batching branch in
`ai/combo_calc.py`.
"""

COMBO_SEARCH_TAX_CARD_SCALE: float = 3.0
"""Derived: per-tax-permanent card value scale used by the search-tax
awareness branch. 3.0 = `PROACTIVE_REMOVAL_MIN_VALUE` — one card-swap
of value granted to the opp per resolved tutor against a search-tax
permanent. Scaled at the call site by `combo_value / opp_life` so the
absolute penalty stays in the EV-comparable range.

Used by the search-tax penalty branch in `ai/combo_calc.py`.
"""

COMBO_HALF_LETHAL_FRACTION: float = 0.5
"""Rules-constant: storm-coverage threshold (storm / opp_life) above
which the mid-chain escalation kicks in. 0.5 = half of opp_life worth
of storm already invested — at this point we've committed the chain's
resources and missing the closer is increasingly catastrophic.

Used by the mid-chain ritual gate in `ai/combo_calc.py`.
"""

COMBO_MIN_CHAIN_DEPTH: int = 3
"""Rules-constant: minimum storm count at which the draw-miss cascade-
risk term applies. 3 = the depth at which a chain has invested enough
spells that one bad top-deck causes the chain to collapse mid-way
(rather than fizzle on the first ritual).

Used by the mid-chain ritual gate in `ai/combo_calc.py`.
"""

COMBO_CASCADE_DRAW_FLOOR: int = 1
"""Rules-constant: minimum draws-in-hand at which the cascade-risk
penalty applies. 1 = one draw remaining = the chain is now "all in"
on top-deck luck. Below this floor the penalty is suppressed (we have
enough draws to dig).

Used by the mid-chain ritual gate in `ai/combo_calc.py`.
"""


# ─── Combo-chain arithmetic constants (ai/combo_chain.py) ────────────
# Used by `find_all_chains` and `ChainOutcome` to bound enumeration
# and convert storm count to per-spell payoff units.

STORM_TOKEN_PAYOFF_PER_COPY: int = 2
"""Rules-constant: tokens created per Empty-the-Warrens-class storm
copy. Empty the Warrens reads "Create two 1/1 red Goblin creature
tokens" — every storm copy is also worth 2 tokens.

Used by `ChainOutcome.storm_tokens` in `ai/combo_chain.py`.
"""

CHAIN_EXHAUSTIVE_FUEL_BUDGET: int = 7
"""Rules-constant: maximum fuel-card count for which the exhaustive
chain enumerator runs. Beyond 7! permutations × subsets the search
space exceeds the per-decision budget; we fall through to the greedy
heuristic. 7 matches the Modern starting-hand size — empirically the
deepest hand a storm chain ever needs to reason over.

Used by `find_all_chains` in `ai/combo_chain.py`.
"""


# ─── Combo-evaluator constants (ai/combo_evaluator.py) ───────────────
# Used by the simulator-driven combo evaluator to score flip-coin
# transform progress, tutor-tax penalties, and hold-vs-fire decisions.
# These constants tune the bridge between the chain simulator's
# `(expected_damage, success_probability, coverage_ratio)` projection
# and the per-card EV scoring used by `ev_player`.

FLIP_COIN_TRANSFORM_VALUE_FRACTION: float = 0.3
"""Derived: fraction of `combo_value` credited per untransformed
flip-creature when adding marginal coin-flip transform progress. 0.3
reflects "one chained instant/sorcery is roughly a third of the value
the eventual transform brings" — the transform itself is the payoff,
each marginal flip is incremental progress. Below 0.3 the AI under-
prioritizes filling the chain; above 0.3 it over-credits the
incremental flip relative to other build-up actions.

Used by `_flip_transform_bonus` in `ai/combo_evaluator.py`.
"""


TUTOR_TAX_LIFE_NORMALIZER: float = 3.0
"""Derived: scaling factor on `combo_value / opp_life` when computing
per-card tutor-tax penalty. 3.0 = three EV units (CHEAP_REMOVAL_ACTION
scale × 3) — the empirical magnitude at which a tax-piece (e.g.
Thalia, Sphere of Resistance) on the opp's battlefield reduces our
willingness to fetch by enough to cancel the tutor's marginal value.
Below 3 the tutor still scores positively against tax; above 3 the
tutor scores too negatively even when its target wins on the spot.

Used by `_search_tax_penalty` in `ai/combo_evaluator.py`.
"""


COMBO_FIRE_SUCCESS_THRESHOLD: float = 0.5
"""Derived: minimum `success_probability` at which a sub-lethal-but-
expected-lethal chain is considered "fire" rather than "hold". 0.5 is
the fair-coin floor — below 50% confidence the combo is too volatile
to commit, so the AI prefers to hold for a more reliable line. Above
50% the AI fires because expected-damage already covers opp_life.

Used by the hold-vs-fire decision in `ai/combo_evaluator.py`.
"""


COMBO_COVERAGE_HALF_LETHAL: float = 0.5
"""Rules-constant: chain-coverage ratio at which mid-chain investment
becomes catastrophic if abandoned. 0.5 = half-lethal — at coverage
above 0.5 we've committed half our resources to the chain, and any
non-extending fuel-burn wastes that investment. Threshold derives
from clock arithmetic, not tuning: it's the point where "this turn
or never" applies to the chain.

Used by the mid-chain coverage gate in `ai/combo_evaluator.py`.
"""


# ─── Finisher-simulator constants (ai/finisher_simulator.py) ────────
# Used by `simulate_finisher_chain` and pattern projectors. Every
# value below is either a rules-derived sentinel ("one extra rules
# step required") or an unreachable-CMC sentinel.

CHAIN_EXTRA_RULES_STEP_SUCCESS: float = 0.5
"""Rules-derived sentinel: success probability when the chain
requires one extra rules step beyond its primary access. Examples:
tutor must resolve uncountered before fetching the closer; discard
outlet must succeed before the reanimator can target; cascade or
draw must reveal the cycling payoff before Living End fires.

0.5 reflects "one fair-coin event must succeed for the chain to
work" — not a tuning weight, the same fair-coin floor used in
`COMBO_FIRE_SUCCESS_THRESHOLD`. All four pattern projectors share
this floor when the primary access is one rules step away.

Used by `_project_storm`, `_project_storm_via_tutor`,
`_project_reanimation`, `_project_cycling`, and `chain_lethal_turn`
in `ai/finisher_simulator.py`.
"""


CHAIN_NO_CLOCK_DEFAULT: int = 99
"""Sentinel: opp_clock fallback when `opp_clock_discrete` is missing
on the snapshot. 99 turns is unreachable in any realistic Modern game
and matches the sentinel semantics of `NO_CLOCK_SENTINEL` (gameplan)
and `NO_CLOCK = 99.0` (clock.py): "no clock means opponent cannot
reach lethal in any meaningful timeframe".

Used by the hold-value computation in `ai/finisher_simulator.py`.
"""


CHAIN_CYCLING_COST_UNREACHABLE: int = 99
"""Sentinel: cycling-cost fallback when a card lacks parsed
`cycling_cost_data['mana']`. 99 mana is unreachable in Modern, so a
card with missing cost-data sorts to the bottom of the cheapest-
cycler search and never gets picked as the chain's cheapest fuel.

Used by `_project_cycling` in `ai/finisher_simulator.py`.
"""


CHAIN_ARCHETYPE_MATCH_PRIORITY: int = 4
"""Tiebreaker: priority assigned to a candidate pattern whose name
matches the deck's archetype hint (storm/cascade/reanimation/cycling).
4 sits above the default ordering (storm=3, reanimation=2, cascade=1,
cycling=0) so an archetype-hinted match always wins ties — used only
when EV proxies are equal between candidates.

Used by `_priority` in `simulate_finisher_chain`.
"""


CHAIN_DEFAULT_PRIORITY_ORDER: dict = {"storm": 3, "reanimation": 2,
                                       "cascade": 1, "cycling": 0}
"""Tiebreaker: default candidate-pattern ordering when no archetype
hint binds. Reflects how directly each pattern translates to damage:
storm and reanimation deal damage outright; cascade and cycling set
up boards that need an extra turn to convert. Highest = preferred.

Used by `_priority` in `simulate_finisher_chain`.
"""


# ─── Stax / lock-piece valuation constants (ai/stax_ev.py) ──────────
# Used by per-pattern lock evaluators (Chalice / Blood Moon / Canonist
# / Torpor Orb). Constants are intentionally conservative — tests
# validate sign and rough magnitude, not precise calibration. The
# 20.0 life-as-resource scaling factor is the same `CLOCK_IMPACT_LIFE_SCALING`
# used elsewhere in the AI scoring layer; it is imported by stax_ev.

STAX_TURN_DECAY_PER_TURN: float = 0.25
"""Derived: per-turn decay factor for stax lock value as the game
progresses. 0.25 = -25% per turn = full decay over 4 turns (T1=1.0,
T2=0.75, T3=0.5, T4=0.25, T5+=0.0). Reflects "by T5 opp has resolved
their cheap spells, so the lock only catches topdecks". Calibrated
against the v1-vs-Boros trace where casting Chalice on T5 cost tempo.

Used by `_turn_decay` in `ai/stax_ev.py`.
"""


STAX_LOCK_DECAY_BURNOUT_TURN: int = 5
"""Sentinel: turn at and after which the stax-lock decay returns 0.0.
5 = 1 / STAX_TURN_DECAY_PER_TURN + 1 (T1=1.0, decays by 0.25 each
turn, hits 0 by T5). Encoded as a separate constant so future re-tunes
of the decay rate also move the sentinel.

Used by `_turn_decay` in `ai/stax_ev.py`.
"""


CHALICE_PRACTICAL_X_CEIL: int = 3
"""Rules-constant: maximum X value scanned for Chalice of the Void
counter-density. Reflects Modern's practical X bound: X=0 freely,
X=1 on T1 with untapped land, X=2 on T2, X=3 on T3. Higher X is rare
in practice and burns enough mana to skip the rest of the curve.

Used by `_chalice_lock_ev` in `ai/stax_ev.py`.
"""


BLOOD_MOON_DISRUPTION_COEFFICIENT: float = 0.3
"""Derived: per (nonbasic_count × missing_colors) coefficient on
Blood Moon disruption. 0.3 keeps magnitudes in the same range as
Chalice's net-lock × clock_impact product. Below 0.3 Blood Moon
under-fires vs 5c manabases; above 0.3 it dominates EV calculations
even against decks playing only one off-color.

Used by `_blood_moon_lock_ev` in `ai/stax_ev.py`.
"""


BLOOD_MOON_DISRUPTION_CAP: float = 15.0
"""Derived: cap on Blood Moon disruption magnitude. 15.0 sits in the
same ceiling band as `MAX_NET_LOCK × clock_impact` (Chalice ceiling)
— a Blood Moon disrupting 50 nonbasics × 3 missing colors theoretically
yields 45.0 raw, which would dominate every other EV term. The cap
keeps Blood Moon in proportion to other lock pieces.

Used by `_blood_moon_lock_ev` in `ai/stax_ev.py`.
"""


CANONIST_DENSITY_FLOOR: float = 0.3
"""Derived: minimum low-CMC fraction of opp's nonland library at
which a Canonist / Rule-of-Law lock fires. 30% reflects "if less than
3-of-10 spells are CMC ≤ 2, the lock barely bites" — control decks
typically run ~25% low-CMC spells, so the floor correctly skips
control mirrors.

Used by `_canonist_lock_ev` in `ai/stax_ev.py`.
"""


CANONIST_DISRUPTION_TURN_COUNT: float = 3.0
"""Rules-constant: turns of spell-limiting credited to a Canonist /
Rule-of-Law per density unit. 3 turns reflects the typical "lock
lasts ~3 turns before opp answers it" window, scaled by density (so
density 0.5 yields 1.5 effective turns of disruption).

Used by `_canonist_lock_ev` in `ai/stax_ev.py`.
"""


CANONIST_DISRUPTION_COEFFICIENT: float = 0.4
"""Derived: post-product coefficient on Canonist disruption × impact ×
lifetime. 0.4 is "slightly lower than the Chalice/Blood Moon line"
because Canonist's lock is per-turn-skippable (opp casts the highest-
EV spell first then stops), whereas Chalice's lock is total. The
40% coefficient calibrates the magnitude difference.

Used by `_canonist_lock_ev` in `ai/stax_ev.py`.
"""


TORPOR_ORB_ETB_DENSITY_FLOOR: int = 3
"""Rules-constant: minimum count of `etb_value`-tagged creatures in
opp's library for Torpor Orb's lock to fire. Below 3 the orb's
disruption is too small to justify the artifact slot — opp's deck
just doesn't lean on ETBs enough.

Used by `_torpor_orb_lock_ev` in `ai/stax_ev.py`.
"""


TORPOR_ORB_PER_ETB_VALUE: float = 0.4
"""Derived: per-disrupted-ETB value multiplier. 0.4 reflects "not all
ETBs are huge" — a typical mix of small ETB triggers (1/1 Solitude
companion) up to medium ETBs (Reflector Mage flicker) averages around
40% of a card's worth of value.

Used by `_torpor_orb_lock_ev` in `ai/stax_ev.py`.
"""


# ─── Mana-planner constants (ai/mana_planner.py) ─────────────────────
# Used by `score_land`, `choose_best_land`, and `choose_fetch_target`
# to rank land candidates against the hand's color demand and the
# turn-bounded tempo curve. All weights live on the same "card swap
# value" scale used elsewhere in the AI scoring layer.

LAND_SCORE_PER_MISSING_COLOR_DEMAND: float = 8.0
"""Derived: per-spell-demand bonus for a land that supplies a missing
color. A color needed by 3 spells × 8.0 = 24 — three card-swaps of
value, the empirical scale at which "this land enables three plays
this turn" outranks every other land-scoring axis. Matches the
`HELD_COLOR_PRESERVATION_BONUS` per-demand weight so the held-color
preservation guard reads as the same magnitude.

Used by `score_land` block (A) in `ai/mana_planner.py`.
"""

LAND_SCORE_PAYOFF_MISSING_COLOR_BONUS: float = 10.0
"""Derived: bonus for a land that supplies a color demanded by a
high-CMC multi-color payoff (CMC ≥ 3, ≥2-color identity — Omnath WURG,
Leyline Binding, etc.). 10.0 is above the per-demand weight (8.0)
because these payoffs are deck-defining and missing their color
strands the win condition.

Used by `score_land` block (A) in `ai/mana_planner.py`.
"""

LAND_SCORE_REDUNDANT_COLOR_WEIGHT: float = 2.0
"""Derived: per-demand weight for a land supplying a color we already
have (redundancy bonus). Quarter of the missing-color weight (8.0)
because the marginal value of the second source is one-quarter of
the first — covers tap-out scenarios and color-screw protection.

Used by `score_land` block (B) in `ai/mana_planner.py`.
"""

LAND_SCORE_ENABLED_SPELL_URGENCY: float = 3.0
"""Derived: multiplier on the per-CMC urgency curve for the
"this land enables a spell currently uncastable" bonus. 3.0 = one
card-swap (`PROACTIVE_REMOVAL_MIN_VALUE`) per spell-tier — the value
of unblocking one specific play in hand.

Used by `score_land` block (C) in `ai/mana_planner.py`.
"""

LAND_SCORE_URGENCY_CMC_CEIL: int = 8
"""Rules-constant: CMC ceiling above which the per-CMC urgency curve
floors at 1. 8 covers the high end of Modern's mana curve (Eldrazi
Tron, Amulet Titan); a 1-mana spell is worth 7 turns of clock, an
8-mana spell is worth 0 turns of curve-relevant urgency.

Used by `score_land` block (C) in `ai/mana_planner.py` as
`max(1, LAND_SCORE_URGENCY_CMC_CEIL - cmc) * LAND_SCORE_ENABLED_SPELL_URGENCY`.
"""

LAND_SCORE_TAPPED_PENALTY_EARLY: float = 0.15
"""Derived: tapped-spell-enablement multiplier on T1-T2. A tapped
land delays the spell by 1 turn — early game that's a 6-turn-clock
loss out of ~7 turns to lethal, ≈ 0.15 of the un-tapped value
(empirically calibrated against the Boros vs Affinity matrix
baseline).

Used by `score_land` block (C) in `ai/mana_planner.py`.
"""

LAND_SCORE_TAPPED_PENALTY_LATE: float = 0.4
"""Derived: tapped-spell-enablement multiplier on T3+. After T2 the
delayed spell still costs a turn but the absolute clock impact is
smaller; the empirical Boros / Jeskai matrix shows ~0.4 of the
un-tapped value preserves correct behaviour.

Sister constant: LAND_SCORE_TAPPED_PENALTY_EARLY.

Used by `score_land` block (C) in `ai/mana_planner.py`.
"""

LAND_SCORE_DOMAIN_BASE: float = 2.0
"""Derived: base bonus per new basic-land-type for a domain-relevant
land. 2.0 = one redundant-color weight — the floor at which a new
type matters even without domain creatures in hand.

Used by `score_land` block (D) in `ai/mana_planner.py`.
"""

LAND_SCORE_DOMAIN_PER_CARD_BONUS: float = 2.0
"""Derived: per-domain-card-in-hand bonus for a new basic-land-type.
A hand with 3 domain cards × 2.0 = +6 per new type, so a fetched
shockland adding 2 new types is worth +12 — high enough to outrank
a mono-color basic in domain decks.

Used by `score_land` block (D) in `ai/mana_planner.py`.
"""

LAND_SCORE_DOMAIN_CARD_CAP: int = 5
"""Rules-constant: cap on the domain-card count used for the domain
bonus. Five domain cards already saturate the per-card bonus; beyond
that the marginal new-type value plateaus.

Used by `score_land` block (D) in `ai/mana_planner.py`.
"""

LAND_SCORE_TAPPED_BASE_PENALTY: float = 8.0
"""Derived: base tempo penalty for a land that enters tapped without
an optional-untap escape. 8.0 = one card-swap × `LAND_SCORE_PER_MISSING_COLOR_DEMAND`
— the cost of "this turn's mana is forfeit" against a typical hand
with one castable spell.

Used by `score_land` block (E) in `ai/mana_planner.py`.
"""

LAND_SCORE_TAPPED_TURN_DECAY: float = 0.15
"""Derived: per-turn decay multiplier on the tapped-tempo penalty.
Each turn that passes reduces the penalty: T1 = 8 × 0.85 = 6.8,
T2 = 8 × 0.7 = 5.6, … capped at 0.5 floor (4.0) by `max(0.5, ...)`.

Used by `score_land` block (E) in `ai/mana_planner.py`.
"""

LAND_SCORE_TAPPED_FLOOR_FRACTION: float = 0.5
"""Rules-constant: floor on the tapped-tempo decay. After T3+ the
penalty plateaus at half the early-game value (4.0) — a tapped land
in the late game still costs tempo but not as much as on T1.

Used by `score_land` block (E) in `ai/mana_planner.py`.
"""

LAND_SCORE_UNTAPPED_BONUS: float = 5.0
"""Derived: bonus for a land that enters untapped (or has the
untap-life option). 5.0 = ⅝ of the tapped penalty — captures the
asymmetry that "no penalty" is more valuable than "no bonus" because
the tap-out decision compounds across turns.

Used by `score_land` blocks (E) and the optional-untap branch in
`ai/mana_planner.py`.
"""

LAND_SCORE_FETCHLAND_FLEXIBILITY_BONUS: float = 4.0
"""Derived: flexibility bonus for fetchlands when scored directly
(not via the proxy-target rescore). 4.0 = ½ of the per-demand weight
— the option to find any color is worth half a single-color land.

Used by `score_land` block (F) in `ai/mana_planner.py`.
"""

LAND_SCORE_VERSATILITY_PER_COLOR: float = 1.0
"""Derived: per-color versatility bonus. Mono-color = +1, two-color
shock = +2, tri-land = +3 — small enough to break ties between equal-
demand candidates but not enough to override the missing-color block.

Used by `score_land` block (G) in `ai/mana_planner.py`.
"""

LAND_SCORE_BEST_INIT_SENTINEL: float = -999.0
"""Sentinel: initial value for `best_score` in `choose_best_land` and
`choose_fetch_target`. Lower than any realistic land score (the worst
case is a tapped colorless land at ~-8.0), so the first candidate
always replaces the sentinel.

Used by `choose_best_land` and `choose_fetch_target` in
`ai/mana_planner.py`.
"""

LAND_SCORE_FETCH_LIFE_TEMPO_PENALTY: float = 1.0
"""Derived: fetchland life + click cost when scoring fetch-as-proxy.
1.0 = one EV unit (`CHEAP_REMOVAL_ACTION_BONUS` scale) — the small
penalty for paying 1 life and using the fetch click. Net effect is
small because thinning the deck partly compensates.

Used by `choose_best_land`'s fetch-as-proxy branch in
`ai/mana_planner.py`.
"""

LAND_SHOCK_RACING_LIFE_THRESHOLD: int = 12
"""Rules-constant: opp life total at which `should_stagger_shock`
defers a second shock to preserve life. 12 = 60% of the Modern
starting life — empirically the threshold below which "one more
shock" can collapse into a Bolt + Push lethal sequence next turn.

Used by `should_stagger_shock` in `ai/mana_planner.py`.
"""

MANA_NEEDS_NO_SPELL_SENTINEL: int = 99
"""Sentinel: starting `cheapest_spell_cmc` / `cheapest_proactive_cmc`
on `ManaNeeds`. 99 is unreachable in Modern (max card CMC is the
emerge/cascade bound at ~15) — any real spell-CMC scan replaces it
with a smaller value. The sentinel makes "no spell in hand" trivial
to detect without a separate Optional/None branch.

Used by `ManaNeeds` in `ai/mana_planner.py`.
"""

PAYOFF_HIGH_CMC_THRESHOLD: int = 3
"""Derived: minimum CMC at which a multi-color (≥2-color identity)
spell counts as a "high-CMC payoff" whose colors should weight fetch
decisions. 3 is the boundary above which mana investment is high
enough to make color-screw catastrophic — Leyline Binding, Omnath
WURG, and Wrenn-and-Six all sit at or above CMC 3 with multi-color
costs. Below CMC 3 the color requirement is usually only one pip and
already covered by the per-demand color tracker.

Used by `compute_mana_needs` in `ai/mana_planner.py`.
"""


# ─── Clock-position constants (ai/clock.py) ──────────────────────────
# Used by `position_value` and `combo_clock` to bound clock-derived
# scoring under degenerate or extreme states.

CLOCK_LETHAL_ADVANTAGE_CAP: float = 20.0
"""Sentinel: clock-differential cap applied when the opponent has no
clock but we do. 20.0 ≈ Modern starting life total — the maximum
"clock advantage" value, reached when our clock = 1 turn (lethal next
turn) while opp_clock is NO_CLOCK. Below this cap the formula is
20 / my_clock, so clock=2 gives 10, clock=4 gives 5, etc.

Sister constant: CLOCK_IMPACT_LIFE_SCALING (also 20.0) — same Modern
life-total anchor, different unit space. Renamed locally in clock.py
to highlight that this cap is the "we are winning by lethal" tier in
position-value units.

Used by `position_value` in `ai/clock.py`.
"""

LIFELINK_LIFE_GAIN_WEIGHT: float = 0.3
"""Derived: scaling factor on lifelink life-extension turns when added
to the position-value life-advantage term. 0.3 matches
`COMBO_FLIP_TRANSFORM_VALUE_FRACTION` and `CHIP_DAMAGE_VALUE` — the
"small-but-real per-turn nudge" tier. Lifelink's full damage is
already counted in the combat clock; this bonus captures the
incremental survival extension.

Used by `position_value` in `ai/clock.py`.
"""

CLOCK_BLOCKER_ABSORPTION_TURN_CYCLE: float = 3.0
"""Rules-constant: assumed turn cycle over which opponent blockers are
"refreshed" by replacement creatures. 3 turns matches the empirical
mid-game replacement cadence on a typical Modern board (one creature
deployed per turn, a blocker dies in combat every ~3 turns).

Used by `combat_clock` in `ai/clock.py` as the divisor in the
blocker-absorption term `opp_total_toughness / 3.0`.
"""


# ─── Win-probability fallback constants (ai/win_probability.py) ──────
# Used by the position-only fallback featurizer when the calibrator
# artifact is missing the full numeric model.

WIN_PROB_LIFE_DIFF_NORMALIZER: float = 5.0
"""Derived: divisor that maps the (opp_life - my_life) gap into a
single fallback feature for the position-only featurizer. 5.0 ≈ 1/4
of Modern's starting life total — a 5-life swing corresponds to one
unit of feature magnitude, matching the typical "one attacker's worth
of pressure" the fallback is meant to approximate.

Used by `_featurize_position_only` in `ai/win_probability.py`.
"""


# ─── Structural / safety limits (migrated from ai/constants.py) ──────
# Computational and rule-anchored bounds used by the engine and
# response-modeling layers. Originally defined in `ai/constants.py`;
# centralised here so all AI-layer constants share a single review
# point. `ai/constants.py` retains a re-export shim for back-compat.

MAX_ACTIONS_COMBO: int = 40
"""Rules-constant: max main-phase actions for combo decks per turn.
40 = empirical ceiling for a long Storm chain (~10 cantrips ×
2 rituals + ~10 fuel spells + payoffs). Used by the engine to bound
runaway loops that don't otherwise self-terminate.

Used by `engine/game_runner.py` to gate the per-turn action budget.
"""

MAX_ACTIONS_NORMAL: int = 20
"""Rules-constant: max main-phase actions for non-combo decks per
turn. 20 covers all realistic Modern non-combo turns (typical: 1-3
spells + a land + combat). Used by the engine to bound runaway
loops in non-combo archetypes where 40 actions would never occur.

Used by `engine/game_runner.py` to gate the per-turn action budget.
"""

GAME_TIMEOUT_SECONDS: float = 8.0
"""Rules-constant: per-game wall-clock safety timeout. 8 seconds
covers the slowest registered Modern matchup (Tron mirror) with
~2× headroom. Beyond this, the game is aborted as a draw rather
than risk a hung simulator.

Used by `engine/game_runner.py` as the deadline anchor.
"""

SHOCK_LETHAL_LIFE_THRESHOLD: int = 2
"""Rules-constant: life total below which the AI should not
voluntarily pay 2 life for an untapped shockland (the loss would
either kill outright or hand the opponent a Bolt-lethal turn).

Used by mana-payment logic across the engine. Note: `ai/mana_planner.py`
runs the shock decision through `analyze_mana_needs()`; this constant
is the rules anchor cited by that decision.
"""

NO_CLOCK: float = 99.0
"""Sentinel: "no clock" — no win condition / no creatures / stalled
position. Pinned at 99.0 so it always exceeds any realistic
turns-to-lethal value (typical clock <= 8 turns). Used as a sentinel
in `ai/clock.py` and as a turn-cap throughout the EV pipeline.

Sister constant: STORM_HARD_HOLD (in `ai/combo_calc.py`) is derived
as -NO_CLOCK × 10 — the "bigger than any realistic EV" floor for
the storm-chain hard-hold.
"""

COUNTER_ESTIMATED_COST: int = 2
"""Rules-constant: estimated mana cost of a generic Modern
counterspell (Counterspell = UU, Mana Leak = 1U, Force Spike = U).
2 mana is the median; used by `estimate_opponent_response` in
`ai/ev_evaluator.py` to predict whether the opp can counter our
spell given their visible mana.
"""

REMOVAL_ESTIMATED_COST: int = 1
"""Rules-constant: estimated mana cost of generic Modern removal
(Lightning Bolt = R, Fatal Push = B, Unholy Heat = R). 1 mana is
the median; used by `estimate_opponent_response` in
`ai/ev_evaluator.py` to predict whether the opp can spot-remove
our creature given their visible mana.
"""

DAMAGE_REMOVAL_EFF_HIGH_TOUGH: float = 0.3
"""Derived: P(damage-based removal kills) when the target has
toughness >= 4. 30% reflects the 1-of-3 Bolt/Push hit rate against
a 4+ toughness creature in Modern — the creature lives through Bolt
and Push, dies to Heat / Unholy Anointment.

Used by `estimate_opponent_response` in `ai/ev_evaluator.py`.
"""

DAMAGE_REMOVAL_EFF_MID_TOUGH: float = 0.6
"""Derived: P(damage-based removal kills) when the target has
toughness == 3. 60% reflects the empirical hit rate at this
toughness band — Heat / Unholy Anointment / Phoenix Chasm hit, Bolt
/ Push miss.

Used by `estimate_opponent_response` in `ai/ev_evaluator.py`.
"""


# ─── Gameplan / goal-engine constants (ai/gameplan.py) ────────────
# Bare-literal extraction pass for ai/gameplan.py. The goal-engine
# scores three things with bare literals: (1) goal-transition pacing,
# (2) generic combo-readiness confidences, and (3) the mulligan-bottom
# `card_keep_score` weights. Centralising preserves the single point of
# review for future re-tunes and keeps the "no magic numbers" contract
# intact for the gameplan module too.

# ---- Goal-transition pacing ----

NO_CLOCK_SENTINEL: int = 999
"""Sentinel: clock value used when a player has no offensive power
and therefore cannot kill the opponent in a finite number of turns.

Anchored at 999 so any real clock comparison
(`opp_clock <= dying_clock`) treats "no clock" as the opposite of
imminent danger. Used by `BoardAssessor.assess` for both `my_clock`
and `opp_clock` when total power on the relevant side is zero.

Sister constant: `ai/clock.NO_CLOCK = 99.0` — same "cannot reach
lethal" intent in the float clock-impact subsystem. Different units
but identical meaning; the integer form here belongs to the discrete
turn-count sentinel used by `BoardAssessment`.
"""


DEPLOY_ENGINE_FORCE_ADVANCE_TURNS: int = 3
"""Rules-constant: turns spent in DEPLOY_ENGINE before the goal
auto-advances even without an engine on the battlefield.

3 turns matches the typical Modern T3-ish window for most engine
cards (Amulet of Vigor on T1, Medallion-style on T2, etc.). After
this window, sticking on DEPLOY_ENGINE risks freezing the deck on
a goal whose primary card is unreachable (mulligan bottomed, exiled,
or simply not drawn), so we advance to the next goal even though the
engine never landed. Used by `GoalEngine.check_transition` in
`ai/gameplan.py` for the DEPLOY_ENGINE branch.
"""


GENERIC_GOAL_TIMEOUT_TURNS: int = 2
"""Rules-constant: minimum turns spent in a non-engine goal before
auto-advancing to the next goal.

2 turns gives the goal one full main-phase cycle to make progress
(turn-N entry main, turn-N+1 main, advance). Used by
`GoalEngine.check_transition` for DISRUPT, INTERACT, GRIND_VALUE,
PUSH_DAMAGE, and the FILL_RESOURCE payoff-in-hand fallback. Smaller
than `DEPLOY_ENGINE_FORCE_ADVANCE_TURNS` because the engine slot has
a stickier "we really want this online" intent than the generic
disruption / value windows.

Sister constant: `Goal.min_turns` (per-goal override) — when a goal
declares its own min_turns, the larger of the two binds.
"""


# ---- Resource-target fallbacks ----

DEFAULT_FILL_RESOURCE_TARGET: int = 3
"""Rules-constant: fallback `resource_target` when a FILL_RESOURCE
goal does not declare its own target.

3 is the smallest "meaningful" pool size for the four resource zones
the FILL_RESOURCE branch tracks: 3 graveyard creatures (typical
reanimator threshold), 3 storm count (smallest pre-payoff chain),
3 mana available, 3 creatures on battlefield (token / go-wide
pre-payoff). Chosen as the floor at which most payoff effects start
producing meaningful EV. Goals that need a different threshold (e.g.
Storm at 5, Tron at 7) declare their own `resource_target`.

Used by `GoalEngine.check_transition` and `generic_combo_readiness`
in `ai/gameplan.py` for the graveyard and "default" zones.
"""


DEFAULT_STORM_RESOURCE_TARGET: int = 5
"""Rules-constant: fallback `resource_target` for the storm zone in
`generic_combo_readiness`.

5 matches `COMBO_FORCE_PAYOFF_STORM_THRESHOLD` in `ai/ev_player.py` —
the same "we have enough storm fuel that even non-lethal payoffs
close the game" threshold. Goals that declare their own storm target
override this.

Sister constant: `COMBO_FORCE_PAYOFF_STORM_THRESHOLD` (same value,
same intent, different decision site).
"""


DEFAULT_MANA_RESOURCE_TARGET: int = 5
"""Rules-constant: fallback `resource_target` for the mana zone in
`generic_combo_readiness`.

5 mana is the typical Modern "deploy-and-protect" threshold — enough
for a 4-drop payoff plus a 1-mana counter held up. Goals that need
more mana (Tron at 7, Amulet ramp at 6+) declare their own target.

Used by `generic_combo_readiness` mana-zone branch in
`ai/gameplan.py`.
"""


DEFAULT_RAMP_GOAL_MANA_TARGET: int = 6
"""Rules-constant: fallback mana target for a RAMP goal's resource-
ready check when `resource_target` is unset.

6 mana is the typical Modern "ramp goal complete" threshold —
enough for a 6-drop finisher (Primeval Titan, Cruel Ultimatum) on
the curve a ramp goal accelerates toward. Slightly higher than
`DEFAULT_MANA_RESOURCE_TARGET` because RAMP goals typically guard a
higher-CMC payoff than generic mana goals. Goals that need a
different threshold declare their own target.

Used by `BoardAssessor.assess` RAMP-goal branch in `ai/gameplan.py`.
"""


# ---- Generic combo-readiness confidence levels ----
# Confidence values returned by `generic_combo_readiness` to express
# "how ready is the combo right now". These are not thresholds — they
# are reported confidences (0-1) on the already-Boolean ready flag.
# Centralised so the readiness ladder is reviewable in one place.

COMBO_FIRED_CONFIDENCE: float = 0.9
"""Derived: confidence reported when the storm count already meets
the goal's target — the combo is *physically* ready to fire this turn.

0.9 not 1.0 because confidence is reserved space for "fires AND
resolves" (counterspell / hate-piece risk priced into the remaining
0.1). The downstream EV layer applies its own BHI discount on top.

Used by `generic_combo_readiness` storm-zone branch in
`ai/gameplan.py`.
"""


COMBO_MANA_FIRE_CONFIDENCE: float = 0.85
"""Derived: confidence reported when the mana zone meets the goal's
target AND a payoff is in hand.

Slightly lower than `COMBO_FIRED_CONFIDENCE` (storm) because mana
availability can be disrupted by Wasteland-style land destruction or
mana-tap effects mid-resolution, where storm count is locked once
declared. The 0.05 gap reflects that marginal disruption surface.

Used by `generic_combo_readiness` mana-zone branch in
`ai/gameplan.py`.
"""


COMBO_GY_FIRE_CONFIDENCE: float = 0.8
"""Derived: confidence reported when graveyard count meets the goal's
target AND a payoff is in hand.

Lower than the mana/storm cases because graveyards face active hate
(Grafdigger's Cage, Leyline of the Void, Endurance) at a higher
incidence than stack / mana disruption. The 0.05-0.10 gap below
storm/mana reflects that empirical hate-piece exposure.

Used by `generic_combo_readiness` graveyard-zone branch in
`ai/gameplan.py`.
"""


COMBO_PROJECTED_FIRE_CONFIDENCE: float = 0.7
"""Derived: confidence reported when the *projected* resource
(current + rituals in hand) meets the storm target AND a payoff is in
hand.

Lower than `COMBO_FIRED_CONFIDENCE` because the projection requires
casting all rituals successfully — each cast faces counter / removal
risk. 0.7 is empirically the survival probability of a 2-3 ritual
chain through typical Modern interaction (≈0.85 per cast, compounded
across 2 casts ≈ 0.72).

Used by `generic_combo_readiness` storm-projection branch in
`ai/gameplan.py`.
"""


COMBO_BASE_CONFIDENCE: float = 0.6
"""Derived: base confidence when a combo has both a payoff and an
enabler in hand but no resource-zone gate to consult.

0.6 reflects "we have the cards but not yet the timing" — the combo
is reachable, but the window hasn't arrived. Used as the starting
point of the ramp formula:

    confidence = COMBO_BASE_CONFIDENCE
                 + COMBO_PIECE_CONFIDENCE_BONUS × payoff_count
                 + COMBO_PIECE_CONFIDENCE_BONUS × enabler_count

so a hand with 2 payoffs and 2 enablers reports 0.6+0.2+0.2 = 1.0.

Used by `generic_combo_readiness` default branch in `ai/gameplan.py`.
"""


COMBO_PIECE_CONFIDENCE_BONUS: float = 0.1
"""Derived: per-piece additive confidence in the default-branch ramp.

0.1 = (1.0 - COMBO_BASE_CONFIDENCE) / 4 — saturates the confidence
to 1.0 with 4 redundant pieces total (any combination of payoffs +
enablers). The denominator 4 reflects the "two of each piece is
plenty for a Modern combo" rule of thumb.

Used by `generic_combo_readiness` default branch in `ai/gameplan.py`.
"""


COMBO_NO_PAYOFF_CONFIDENCE: float = 0.1
"""Derived: confidence reported when the goal declares payoffs but
none are available in hand or on the battlefield.

0.1 not 0.0 because the deck may still draw into a payoff via top-
deck or tutor effects in the remaining turns. Floored above zero so
downstream consumers can distinguish "no payoff in hand" from "no
combo plan declared at all".

Used by `generic_combo_readiness` payoff-availability gate in
`ai/gameplan.py`.
"""


COMBO_NO_PIECES_CONFIDENCE: float = 0.3
"""Derived: confidence reported when the goal has no zone gate, no
payoff / enabler match, and no other readiness signal — the
"fallback" branch.

0.3 is between `COMBO_NO_PAYOFF_CONFIDENCE` (0.1) and
`COMBO_BASE_CONFIDENCE` (0.6) — higher than no-payoff (we may still
have a payoff via topdeck), lower than the with-pieces case (the
combo is structurally reachable but materially absent right now).

Used by `generic_combo_readiness` final fallback in `ai/gameplan.py`.
"""


# ---- Mulligan card_keep_score weights (ai/gameplan.py) ----
# Weights used by `GoalEngine.card_keep_score` to rank cards when
# bottoming on a London-mulligan keep. Higher score = keep. The
# entire scale is internal to `card_keep_score` (it ranks cards
# against each other within a single hand), so absolute values
# matter less than relative ordering.

MULL_KEEP_LAND_TARGET: int = 3
"""Rules-constant: lands-in-hand threshold below which an additional
land is treated as "needed" for the keep score.

3 is the upper bound of the 2-3-land mulligan-keep band (
`mulligan_min_lands=2` for most decks, `mulligan_max_lands=4`). At
≤3 lands we still want lands; above that the next land becomes a
"flood risk" and scores lower.

Used by `GoalEngine.card_keep_score` land branch in
`ai/gameplan.py`.
"""


MULL_KEEP_LAND_NEEDED: float = 10.0
"""Derived: keep score for a land when the hand has ≤
`MULL_KEEP_LAND_TARGET` lands.

10.0 sits above the role-based keep weights below (max
`MULL_KEEP_ENGINE_ROLE` 8.0 + `MULL_KEEP_KEY_BONUS` 8.0 capped) so a
land we *need* outranks a non-land we'd love to keep. Lands are the
single highest-priority keep when the mana base is short; this
ranking is what prevents Storm-style mulligans from bottoming a
critical land in favour of a Manamorphose.
"""


MULL_KEEP_LAND_EXTRA: float = 2.0
"""Derived: keep score for a land when the hand already has more
than `MULL_KEEP_LAND_TARGET` lands.

2.0 is in the "tie-breaker" band — above noise (the floor of role
scores), below any meaningful keep card. A flood-risk land scores
lower than every non-land except `mulligan_require_creature_cmc`-
gated misses, so the bottoming logic prefers shipping the surplus
land first.
"""


MULL_KEEP_LAND_PRIORITY_SCALE: float = 0.5
"""Derived: weight applied to `gameplan.land_priorities[card.name]`
in the land keep score.

0.5 reflects "land priorities are a soft signal, not a hard order" —
a deck that prefers Battlefield Forge over Plains values that
preference at half-weight relative to the structural land/role
weights. Used by `GoalEngine.card_keep_score` land branch.
"""


MULL_KEEP_LAND_COLOR_PRODUCTION_SCALE: float = 0.5
"""Derived: per-color-produced bonus for a land that taps for
multiple colors.

A 2-color land contributes +1.0 over a 1-color land
(`len(produces_mana) × 0.5`), which is the same scale as the
land-priority weight — the two signals are roughly comparable in
mulligan-keep value.
"""


MULL_KEEP_CMC_BUDGET: int = 5
"""Rules-constant: CMC budget for the cheap-spell keep score.

A spell at CMC ≤ this value contributes `MULL_KEEP_CMC_BUDGET - cmc`
to the keep score — so a 1-mana spell scores 4, a 2-mana spell
scores 3, a 5-mana spell scores 0. The 5 cap matches the typical
Modern curve top: spells above CMC 5 are unlikely to be cast on the
keep window and don't deserve cheap-action credit.

Used by `GoalEngine.card_keep_score` non-land branch.
"""


MULL_KEEP_KEY_BONUS: float = 8.0
"""Derived: keep score bonus for a card declared in
`gameplan.mulligan_keys`.

8.0 matches the engine-role bonus `MULL_KEEP_ENGINE_ROLE` — a deck-
declared key card has equal weight to an engine. Same scale, same
"this card is what we mulligan for" intent. Cancelled by
`MULL_KEEP_REACTIVE_PENALTY` for cards also flagged reactive_only,
preventing decks from keeping answers as opening cards.
"""


MULL_KEEP_REACTIVE_PENALTY: float = 8.0
"""Derived: keep score penalty for a `reactive_only` card.

8.0 cancels the `MULL_KEEP_KEY_BONUS` cleanly — when a reactive-
flagged card is also in `mulligan_keys` (Zoo's Leyline Binding
audit case F-R3-3), the two terms net to zero so the card no longer
scores as a keep. Net result: opening hand prefers proactive cards
over answers waiting for a target.
"""


# Role weights for the mulligan keep score. Each goal's
# `card_roles[role]` set contributes `MULL_KEEP_ROLE_WEIGHTS[role]`
# (or `MULL_KEEP_ROLE_DEFAULT` if the role name isn't in the table)
# when `card.name` is in that role set, and we take the MAX across
# every goal so a payoff in any goal wins the payoff weight.
MULL_KEEP_ROLE_WEIGHTS: Dict[str, float] = {
    "engines": 8.0,
    "payoffs": 7.0,
    "enablers": 6.0,
    "interaction": 5.0,
    "protection": 4.0,
    "fillers": 3.0,
}
"""Derived: per-role keep weights for `card_keep_score`. Tiering:

- engines (8.0): the deck cannot execute its plan without these.
- payoffs (7.0): the win condition; one rank below engines because
  the deck can sometimes dig to a payoff but not to an engine.
- enablers (6.0): mid-curve setup pieces.
- interaction (5.0): non-reactive answers (proactive removal).
- protection (4.0): held-up answers (counterspells, blink).
- fillers (3.0): redundant role-fillers.

Sister constant: MULL_KEEP_REACTIVE_PENALTY — same scale, applied to
the `reactive_only` flag that overrides interaction/protection roles.
"""


MULL_KEEP_ROLE_DEFAULT: float = 4.0
"""Derived: default role weight for any role name not explicitly
listed in `MULL_KEEP_ROLE_WEIGHTS`.

4.0 matches the `protection` weight — a sensible "middle of the
pack" default for unknown roles. New role names added to gameplan
JSON without a matching entry here ship at this neutral weight.
"""


MULL_KEEP_ALWAYS_EARLY_BONUS: float = 6.0
"""Derived: keep bonus for a card declared in
`gameplan.always_early`.

6.0 matches `MULL_KEEP_ROLE_WEIGHTS["enablers"]` — an "always early"
card behaves as an opening enabler regardless of role. Same scale,
same "we want this in the opening hand" intent.
"""


MULL_KEEP_REMOVAL_TEXT_BONUS: float = 4.0
"""Derived: keep bonus for a non-`always_early` card whose oracle
text contains a removal keyword (`destroy`, `exile target`, `damage
to each`).

4.0 matches `MULL_KEEP_ROLE_DEFAULT` — generic removal is a default-
weight keep, slightly below explicit role-tagged interaction (5.0).
The discount reflects that oracle-text-derived removal hasn't been
explicitly role-tagged by the gameplan author, so the fit is less
certain than a tagged role match.
"""


MULL_KEEP_CRITICAL_SINGLETON_FLOOR: float = 20.0
"""Sentinel: floor keep score for a card declared in
`gameplan.critical_pieces` that has only one copy in hand.

The "floor" equals the maximum achievable score from normal
role+key+cmc weights:

    8 (engine) + 8 (key) + 5 (cmc_max) + 6 (always_early) ≈ 27 cap

Setting the floor at 20 keeps the singleton above almost every
normal keep but below the synthetic "max stack" (engine + key +
cmc + always_early all on one card). Bottoming the last copy of a
critical piece sabotages the deck's win condition; this constant
prevents that. Already named at the call site as
`CRITICAL_SINGLETON_FLOOR` — promoted here so the derivation
survives a centralised re-tune.

Used by `GoalEngine.card_keep_score` critical-singleton branch in
`ai/gameplan.py`.
"""


# ---- DecisionThresholds dataclass defaults ----
# These are the midrange/default values for the per-deck
# `DecisionThresholds` config. Each archetype's gameplan can override
# any of these via its DecisionThresholds(...) constructor; the values
# here are the defaults applied when no override is given. Kept as
# named module-level constants so re-tuning the default is a
# single-point edit visible in scoring_constants.

DECISION_DYING_CLOCK: int = 4
"""Derived: default `dying_clock` for `DecisionThresholds`. Triggers
the "I'm dying" SURVIVE branch when `opp_clock <= 4` AND the opp's
board has at least `DECISION_DYING_MIN_BOARD_POWER` power. 4 turns
matches the Modern "panic threshold" — under 4 turns to die we must
prioritise removal over deploys.

Used by `DecisionThresholds.dying_clock` in `ai/gameplan.py`.
"""


DECISION_DYING_MIN_BOARD_POWER: int = 3
"""Derived: default opp-board power that gates the SURVIVE branch's
"dying" trigger. 3 power is "one solid creature or two small ones"
— below this the threat is small enough that life-as-resource
arithmetic (`ai/clock.py`) prefers race over removal.

Sister constant: DECISION_DYING_CLOCK (turn axis).
"""


DECISION_ANSWER_MIN_POWER: int = 3
"""Derived: minimum creature power for "meaningful threat" at the MED
classification level when under pressure. Same magnitude as the
SURVIVE branch's board-power floor — the threshold for "this creature
warrants removal" matches "this board is dangerous".

Used by `DecisionThresholds.answer_min_power` in `ai/gameplan.py`.
"""


DECISION_WRATH_SINGLE_TARGET_MIN_VAL: float = 8.0
"""Derived: minimum threat value at which a single creature warrants
a board wipe. 8.0 sits at the role-bonus tier (`MULL_KEEP_ENGINE_ROLE`
scale) — a creature must be worth at least an engine-tier card before
we burn a wrath on it alone. Below 8 we save the wrath for a wider
board.

Used by `DecisionThresholds.wrath_single_target_min_val` in
`ai/gameplan.py`.
"""


DECISION_EVOKE_HARDCAST_NEXT_TURN: float = 0.7
"""Derived: pressure level (0.0-1.0) at which evoke fires when we
COULD hardcast next turn. 0.7 = "70% pressure, still close enough to
hold for the hardcast", so the AI prefers to wait. Above 70% pressure
we evoke now even at the cost of the body — the next-turn hardcast
arrives too late.

Used by `DecisionThresholds.evoke_hardcast_next_turn` in
`ai/gameplan.py`.
"""


DECISION_EVOKE_WRONG_COLORS: float = 0.4
"""Derived: pressure level at which evoke fires when we'd never be
able to hardcast (wrong colors). 0.4 = "40% pressure, the body is
unreachable so the only access is evoke" — lower than the hardcast
threshold (0.7) because hardcast isn't a viable alternative.

Used by `DecisionThresholds.evoke_wrong_colors` in `ai/gameplan.py`.
"""


# ---- DeckGameplan dataclass defaults ----

DEFAULT_MULLIGAN_MAX_LANDS: int = 4
"""Derived: default max-lands threshold for the mulligan keep
heuristic. 4 lands is the "flood ceiling" for a 7-card hand — at 5+
the hand is too land-heavy to develop a meaningful threat density.
Decks with high mana curves (Tron, Amulet Titan) override this
upward; aggro decks override downward.

Used by `DeckGameplan.mulligan_max_lands` in `ai/gameplan.py`.
"""


# ─── Outcome-distribution constants (ai/outcome_ev.py) ────────────────
# Bare-literal extraction pass for `ai/outcome_ev.py`. The remaining
# numerics in that module are probability bounds (0.0 / 1.0) and
# sentinels (best=0 for max-search, +1 for "one extra card seen") that
# are mathematical primitives, not magic numbers — they stay inline.
# The single rule-encoding literal is the lookahead window for the
# finisher-reachability hypergeometric, lifted below.

FINISHER_REACHABLE_LOOKAHEAD_DRAWS: int = 2
"""Rules-constant: number of upcoming draws to consider when computing
``p_finisher_reachable`` via the hypergeometric in
``p_draw_in_n_turns``.

2 matches the canonical short-horizon lookahead used elsewhere in the
codebase: ``HandBeliefs.p_higher_threat_in_n_turns`` defaults to
``turns=2`` and the spot-removal-deferral branch in
``ai/ev_player.py`` passes the same ``turns=2``. Same intent: "what
will be available within roughly the next two draws", which covers
the next untap step plus the typical search/cantrip cast on the
following turn without overcounting late-game draws that won't
arrive before the chain has either fired or fizzled.

Used by ``build_combo_distribution`` in ``ai/outcome_ev.py`` for the
``p_finisher_reachable`` probability prior.

Sister primitive: ``HandBeliefs.p_higher_threat_in_n_turns(turns=2)``
in ``ai/bhi.py`` — same lookahead window for the spot-removal-timing
decision.
"""
