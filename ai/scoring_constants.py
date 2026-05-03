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
