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
