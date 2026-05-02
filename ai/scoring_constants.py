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

Matches the per-demand weight in block (A) of the same scoring
function (8.0 per enabled spell) — held interaction is worth the
same as the spell it protects being castable.

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

# ─── Pitch / opportunity-cost constants ──────────────────────────────

PITCH_COUNTER_FREE_COST: int = 1
"""Effective cost of a "free" pitch counter on the opponent's turn — 1
exiled card, no mana.

Used by `respond_to_stack` in `ai/response.py` to decide whether a
counter is cheap enough to fire even when a post-resolution creature-
exile is also available.  Counters with effective cost > 1 are reserved
when triage would otherwise skip them; pitch counters at cost 1 always
fire because the opportunity cost (one exiled card vs. opp's spell) is
strictly favorable.
"""
