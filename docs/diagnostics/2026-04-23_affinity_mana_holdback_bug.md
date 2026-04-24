---
title: Affinity overperformance — mana holdback gap on control side (narrow, superseded)
status: superseded
priority: historical
session: 2026-04-23
supersedes:
  - docs/diagnostics/2026-04-21_affinity_overperformance.md
superseded_by:
  - docs/diagnostics/2026-04-23_affinity_consolidated_findings.md
depends_on:
  - docs/experiments/2026-04-20_phase11_n50_matrix_validation.md
tags:
  - p0
  - wr-outlier
  - affinity
  - azorius-control
  - mana-holdback
  - diagnostic
  - phase-12
summary: "Corrected diagnostic after the 2026-04-21 response-gate hypothesis was falsified. Real root cause: Azorius Control (and other control / counter-holding decks) taps out on its own turn and arrives at opponent's priority window with insufficient mana to cast its counterspell. In s60102 G1 T3, AzCon holds Counterspell (opening hand, never discarded) with threat=11.37 scored for Sojourner's Companion — but untapped_lands=1 (Hallowed Fountain only). Counterspell requires UU, so can_cast filters it out at ai/response.py:39 before decide_response can fire it. The bug is in the main-phase scorer's mana holdback term: the existing penalty at ai/ev_player.py:740-752 is -2.0 (too weak to offset a 3-5 EV plays) and doesn't cover activated abilities like cycling."
---

# P0 WR outlier diagnostic — Affinity overperformance (mana holdback)

## Headline

AzCon's problem vs. Affinity is not "fails to counter Sojourner's" — it's "can't counter Sojourner's" because the Counterspell in hand is stranded behind an insufficient mana base. AzCon consistently taps out on its own turn for mid-value plays (cycling, non-Counterspell spells) and arrives at opponent's priority window without UU open.

## Evidence (seed 60102 G1 — Affinity 2-0 vs Azorius Control)

Instrumentation of `ai/response.py::ResponseDecider.decide_response` on every invocation where AzCon is defender:

```
[T1] decide_response on Ornithopter (CMC 0), threat=0.96, has_counter=True, untapped=0
[T2] decide_response on Frogmite (CMC 4), threat=3.48, has_counter=True, untapped=0
[T3] decide_response on Sojourner's Companion (CMC 7), threat=11.37, has_counter=True, untapped=1
[T3] decide_response on Cranial Plating (CMC 2), threat=25.85, has_counter=True, untapped=1
[T4] decide_response on Cranial Plating (CMC 2), threat=35.94, has_counter=True, untapped=1
```

At **every** priority window where threat exceeds the Counterspell gate (≥3.0), Counterspell is in hand but `untapped ≤ 1`. The Counterspell cannot cast (CMC 2 colored). `can_cast` filter at `ai/response.py:39` excludes it from the candidate list. `decide_response` returns None correctly — there's no instant it can legally cast.

### Why AzCon is tapped out

Replaying AzCon's mana decisions in G1:

| Turn | Plays | Mana spent | UU open after? |
|---|---|---:|:---:|
| T1 | Play Steam Vents (tapped) | 0 | no |
| T2 | Play Arid Mesa → fetch Hallowed Fountain (pay 2 life); **cycle Lórien Revealed** (tap Steam Vents for U) | 1 blue | **no** — Steam Vents tapped, only Hallowed Fountain untapped |
| T3 | Play Flooded Strand → fetch Island; **cast Isochron Scepter** (CMC 2, taps Island + Steam Vents) | 2 | no |

The cycling on T2 and the Isochron Scepter cast on T3 both choose to spend mana on medium-value plays, leaving AzCon unable to Counterspell. The main-phase scorer's holdback penalty (`ai/ev_player.py:735-752`) is too weak:

```python
if has_instant and not t.is_instant and not t.has_flash:
    remaining_mana = snap.my_mana - cmc
    if remaining_mana < 2:
        ev -= 2.0  # tapping out loses instant-speed interaction
```

Penalty of `-2.0` is the only deterrent. A CMC-2 cast like Isochron Scepter with base EV ~5 remains at +3 after holdback, well above `pass_threshold`.

## Diagnosis

Three gaps compound:

1. **Holdback penalty is too weak.** `-2.0` doesn't offset typical mid-value main-phase plays. For counter-in-hand + threat-on-stack-expected archetypes, holdback should scale with (a) how many counterspells are held, (b) the counter's CMC (need to preserve that much mana), (c) expected opp-threat value.

2. **Holdback doesn't cover activated abilities / cycling.** The scorer loop only sees spell casts. Activated abilities (cycle Lórien, activate Urza's Saga) go through a separate action path without the holdback check.

3. **Holdback requires colored-source preservation, not just total mana.** At T3 AzCon has 3 lands in play (Steam Vents, Hallowed Fountain, Flooded Strand pre-crack). The current check `remaining_mana < 2` is raw count. Counterspell needs UU specifically — the AI should ensure two blue sources remain untapped, not just two lands.

## Candidate fix loci

Not a fix proposal — diagnostic only. Likely loci for the next session:

- **`ai/ev_player.py:735-752`** — strengthen the holdback penalty and make it colored-source-aware. Scale penalty by counter CMC and the probability that opp will cast a threat on their next turn.
- **`ai/ev_player.py:_score_activated_ability`** (or equivalent) — add holdback to cycling / artifact activation scoring. Today cycling Lórien ignores "would this leave me without UU for Counterspell".
- **`ai/mana_planner.py`** — add `held_for_counter` to the mana-allocation plan when counter+counter_cmc is in hand. The fetch-crack targeting logic (`choose_fetch_target`) should prefer sources that open UU on the next priority window.

## Validation

Option C (failing-test-first) plan for any fix:

1. Unit test on `_score_spell` for a non-instant play that would leave the player without colored-counter mana, with Counterspell in hand and opp likely to cast. Assert EV below pass_threshold.
2. After fix, reproduce s60102 G1 and confirm AzCon holds up UU through T3, fires Counterspell on Sojourner's.
3. N=50 AzCon vs Affinity + AzCon vs Boros to confirm AzCon WR recovers from 16% floor (target: 30%+).

## Scope note

Fixing AzCon's mana management should also improve its matchups against Jeskai Blink (similar pattern — holds Counterspell, taps out), Dimir Midrange (holds removal, same pattern), and Pinnacle Affinity. The Affinity overperformance is partly an artifact of the AzCon underperformance: if AzCon were playing at 40-50% the matrix would look different.

Related open P0s not addressed by this fix:
- Living End 24% — different root cause (cascade into Living End without GY asymmetry check).
- Ruby Storm 24% — fixed in PR #142.
- Goryo's Vengeance 24% — needs separate investigation.
