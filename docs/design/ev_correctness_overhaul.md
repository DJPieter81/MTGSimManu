# EV Correctness Overhaul — Design Doc

**Created:** 2026-04-19 (end of claude.ai session)
**Target executor:** Claude Code
**Supersedes:** `docs/handoff/2026-04-19_next-session-brief.md` for EV-related work
**Prerequisites:** Read `docs/diagnostics/2026-04-19_affinity_investigation.md`, the two failed-experiment logs in `docs/experiments/`, and the prototype at `ai/ev_player.py` commit `4147fe4`

---

## Executive summary

The AI's EV scoring system has a **foundational baseline problem** that produces multiple downstream bugs. Investigation over the session identified five related problems, all traceable to the same underlying issue: **the EV system compares "cast this spell" against "do nothing this turn," when the correct comparison is "cast this spell" against "the best alternative action, including doing the same thing later."**

This doc specifies the general principle, lists the specific bugs that arise from it, and details a proposed fix. The fix is structural, not local — it lives in `compute_play_ev` and changes what EV "means" for every spell. All specific bug reports in this doc resolve when the general fix lands.

**Zero magic numbers.** All signals are oracle-derived or state-derived. All costs are computable from existing BHI / removal-density data.

---

## 1. The general principle

### Current (broken)

```
EV(cast action) = V(state after cast) − V(current state before any action)
```

Baseline is "did nothing." For any spell whose post-cast state is not strictly worse, EV ≥ 0, so the spell is cast. This produces **false-positive casts**: spells that deliver no immediate value but aren't strictly-harmful enough to be below the pass threshold.

### Correct (derived)

```
EV(cast action) = V(state after cast) − V(state after best alternative this turn, incl. "hold and cast later")
```

Baseline is the best alternative available. For spells whose effect is deferrable — same effect achievable next turn at equal cost — the "cast later" state has equal or better V, so cast-now EV ≤ 0, and the cast should not fire.

### Intuition

Every cast answers: **"what does casting *this turn* achieve that casting *any other turn* wouldn't?"**

If nothing, don't cast. Preserve hand optionality. Let the card sit until its effect has a same-turn payoff.

---

## 2. The five bugs this fix resolves

### Bug A — Ornithopter (and other 0-cost creatures) cast T1 without enabler

**Observed:** `replays/boros_rarakkyo_vs_affinity_s63000_bo3.txt` line 54 — Affinity T1 casts Ornithopter when no Mox Opal metalcraft, no Plating in hand, no sacrifice outlet. The creature will do the same thing next turn (summon sick, blocks nothing, attacks for 0).

**Scored EV pre-fix:** 0.0 (cast).
**Correct EV:** small negative (exposes a card to removal for no this-turn gain).
**What changes:** when no this-turn signal fires, the cast's baseline shifts to "played same card next turn for same cost" → EV ≤ 0.

### Bug B — Cranial Plating cast with no creature to equip this turn

**Observed:** Known from Plating prototype session (`ai/ev_player.py:526` prototype block). AI would cast Plating at 2 mana with no carrier on board, leaving it unattached. The prototype tuned this to a +0.7-+1.0 with a hold-bonus, but the principle is the same as Bug A.

**What changes:** Plating with no carrier → no this-turn value → EV near 0. With carrier and equip mana available → this-turn enabler fires → EV positive. The prototype's tuning coefficients retire once the general principle lands.

### Bug C — Wrath of the Skies cast at X=0 / sub-optimal X

**Observed:** `replays/boros_rarakkyo_vs_affinity.html` G2 T4 Wrath X=1 when X=0 would have swept all opp permanents including Sagas. Also X-selection in general: current code at `engine/game_state.py:1546-1600` has a Chalice-specific branch and falls through to "default to max X" for Wrath.

**What changes:** the right X is "the X that destroys the most threat-value on opp's current board, minus threat-value destroyed on own board, subject to available mana." This is the same marginal-value framework — just applied to a choice-of-mode rather than choice-of-cast.

### Bug D — Discharge goes face when Ornithopter/Signal Pest are on opp board

**Observed:** `replays/boros_vs_affinity_bo3_s62000.txt` T4 Boros — `[Target] → face: no killable target` when opp had Ornithopter (0/2) and Signal Pest (0/1), both killable by 3 damage.

**Root cause:** `ai/permanent_threat.py:permanent_threat` computes `V_owner(with card) − V_owner(without card)` using `ai.clock.position_value` **with default archetype='midrange'**. Ornithopter's value as an artifact-count enabler (Mox Opal metalcraft, Plating scaling, Thought Monitor affinity discount) is invisible from a midrange perspective because `position_value` doesn't track artifact count at all.

**What changes:** `position_value` gains an artifact-count term (and potentially other count-based resource terms for enchantment-matter, graveyard, spells-cast-this-turn). Then `permanent_threat` correctly returns non-zero for Ornithopter when the owner has artifact-scaling cards visible.

### Bug E — Sojourner's Companion cycled for 2 mana in non-reanimate deck

**Observed:** `replays/boros_rarakkyo_vs_affinity_s63000_trace.txt` — Affinity T2 scores cycle at +8.0 EV vs cast Engineered Explosives at −20.0. Cycles Sojourner for 2 mana, draws another Ornithopter.

**Root cause:** `ai/ev_player.py:_score_cycling` gives a flat +4 "creature cycled to GY = future reanimation target" bonus **unconditionally**, even when the deck has no reanimation path. Affinity doesn't reanimate. Sojourner in GY is dead equity.

**ALSO a separate rules bug:** Sojourner's Companion oracle is "**Artifact landcycling {2}** — search your library for an artifact land card, reveal it, put it into your hand, then shuffle." Not plain cycling. `grep -r landcycling engine/ ai/` returns zero hits — **the engine doesn't implement landcycling at all**. It's being processed as regular cycling (draw a random card). This is an engine-rules bug separate from the scoring bug.

**What changes:** (1) scoring gate on cycle bonus fires only when a reanimation path exists in deck/board/hand. (2) Engine implements landcycling as a distinct action that tutors a specified land type. (3) AI scoring for landcycling reflects the tutor value (≈ value of the specific land tutored, minus 2 mana cost).

---

## 3. The core fix — this-turn-value signals

In `compute_play_ev` at `ai/ev_evaluator.py:902`, before or after the existing `projected_value - current_value` computation, add a deferral check:

```python
# DEFERRAL CHECK — is this cast's value deferrable to a later turn?
# If every benefit of casting this spell would also be available next
# turn at equivalent cost, the cast has no this-turn value and the EV
# baseline becomes "cast on a future turn" rather than "do nothing."
#
# Oracle-driven enumeration of this-turn-value signals. ALL signals
# are derived from game state or card template — zero constants.

this_turn_signals = _enumerate_this_turn_signals(card, snap, game, player_idx)

if not this_turn_signals:
    # No same-turn payoff. Cast-now's marginal value is the exposure
    # cost (negative). Next-turn cast delivers identical board state.
    exposure_cost = _compute_exposure_cost(card, snap, game, player_idx)
    return -exposure_cost
else:
    # This-turn value exists. Proceed with standard projection-based EV.
    # (existing code path)
```

### Signal enumeration (`_enumerate_this_turn_signals`)

Returns a list of reasons this cast has same-turn value. Empty list ⇒ deferrable.

All oracle/state-derived. Each check is independently falsifiable.

| Signal | Oracle / State check |
|---|---|
| Has ETB trigger firing | oracle matches `r'when .+ enters'` and the trigger's effect is non-trivial (deals damage, draws, makes tokens, gains life, etc.) |
| Has cast trigger | oracle matches `r'when you cast ~'` or has storm keyword |
| Has haste-like combat impact | keyword `haste`, or dash-cast this turn, or is an instant in opp's combat |
| Enables metalcraft / affinity / threshold | check opp's `artifact_count`/`graveyard_count`/etc. after this cast against thresholds in my hand's other cards' oracle text |
| Enables mana this turn (Mox Opal, Springleaf Drum, etc.) | oracle of cards on my battlefield contains `{T}: add {X}` conditioned on artifact count / creature count that this cast changes |
| Is equip target for Plating-class with equip mana available | Plating-class = `equipment` tag AND (oracle includes `+N/+0 for each X`); equip mana = my_mana post-cast ≥ equip_cost |
| Is sacrifice fodder for outlet on battlefield with activation budget | oracle of my permanents matches `sacrifice a creature:` and I have mana to activate |
| Is reactive during opp priority | opp has something on stack, or spell has flash/instant and opp has combat phase reached |
| Counterspell with valid stack target | opp's stack is non-empty with a counterable spell |
| Discard with non-empty opp hand | `opp.hand_size > 0` and card's effect is `discard` |
| Storm/chain continuation | `archetype in ("storm", "combo")` and storm_count > 0 and chain is live |
| Is a tutor for a specific card-in-need | oracle matches `search your library` and my current hand/board lacks something a valid tutor target provides |
| Last turn before we die | `snap.opp_clock_discrete <= 1` — any play is better than no play |
| Reduces opp hand/board in a way they can't recover | discard vs topdeck-only opp, removal targeting card that leaves zone at end of turn |

### Cost computation (`_compute_exposure_cost`)

Returns the expected negative utility of this cast when no signal fires.

```python
def _compute_exposure_cost(card, snap, game, player_idx):
    # Already-implemented counterfactuals:
    # - removal_probability (from estimate_opponent_response)
    # - card_clock_impact (from ai/clock.py)
    # Multiply: P(removed before we get use of it) × value of card in hand
    
    # "Value of card in hand" = what casting it next turn would deliver.
    # For a card whose effect is state-equivalent across turns, this is
    # the standard projection minus current. If projection minus current
    # ≈ 0 (vanilla 0/2 like Ornithopter), exposure_cost ≈ 0 and EV ≈ 0 —
    # which correctly ties the decision and the pass-preference tiebreak
    # handles it (see §4).
    ...
```

### Tiebreaker rule (new)

When best cast EV ≈ 0 (within floating-point noise of pass's implicit 0), **prefer pass.** Rationale: preserving hand optionality is strictly ≥0 in expected value; committing is strictly ≤0 in expected value given symmetric noise. Implementation: after all EV scores computed, if best spell EV is within ε of 0 AND no signal fires, return pass.

ε is computed from floating-point precision, not a tuning constant.

---

## 4. The `permanent_threat` / `position_value` extension (Bug D)

`ai/clock.py:position_value` currently sees: life, power, toughness, creature count, hand, mana, lands, turn, storm_count, energy, evasion_power, lifelink_power.

It does **not** see: artifact count, enchantment count, graveyard contents, card-type counts other than creatures. These are invisible resources for artifact-synergy, enchantress, delirium, etc. decks.

**Extend `EVSnapshot` with:**
- `my_artifact_count`, `opp_artifact_count`
- `my_enchantment_count`, `opp_enchantment_count`  
- `my_graveyard_card_types` (set of `CardType`s in own GY — enables delirium threshold)

**Populate in `snapshot_from_game`.** Direct count over `player.battlefield` / `player.graveyard`.

**Extend `position_value` to use them:**
- Only when the owner's deck/hand/board contains a card whose oracle references "for each artifact", "metalcraft", "affinity for artifacts", etc. The signal activates conditionally — no blanket artifact-count bonus for non-artifact decks.
- Value contribution: oracle-derived. A card that says "+1/+0 for each artifact" means each marginal artifact = +1 power. A card that says "costs {1} less for each artifact" means each marginal artifact = `mana_clock_impact(snap)` of value. Both computed from existing clock primitives.

**Result:** `permanent_threat(Ornithopter)` when opp plays Affinity returns a meaningful number (≈ the bonus Ornithopter provides to Plating scaling + Mox Opal metalcraft + Thought Monitor cost reduction). `permanent_threat(Ornithopter)` when opp plays Zoo still returns ≈0.

Zero magic numbers — contributions derived from oracle text of deck-visible cards.

---

## 5. Engine rules: landcycling (Bug E.2)

`engine/oracle_parser.py` likely classifies all cycling variants uniformly. Need separate handling:

- **Plain cycling** (`cycling {cost}`) — pay cost, discard, draw a card
- **Typecycling** (`<type>cycling {cost}`) — pay cost, discard, **search library for a card of that type, reveal, put into hand, shuffle**
- **Landcycling** (`landcycling {cost}`, `basic landcycling`, `artifact landcycling`, etc.) — same as typecycling, constrained to lands of the specified type

Current `grep -r landcycling engine/ ai/` → zero hits. Fix is likely:
1. Parse the specific cycling variant from oracle
2. Route to the correct resolver
3. Landcycling's resolver: library search with type predicate, reveal, to hand, shuffle

Test shape (Option C):
```
# Setup: Affinity deck with Sojourner's Companion in hand, artifact
# lands in library. Trigger cycle.
# Assert: an artifact land is added to hand, deck is shuffled.
# Assert (regression): plain cycling still draws a random card.
```

---

## 6. Test-first protocol (Option C) — per bug

Each bug listed in §2 gets a failing test **before** any fix. Tests must demonstrate the bug reproduces.

| Bug | Test name | Shape |
|---|---|---|
| A | `test_zero_cost_creature_no_enabler_held.py` | Setup: Affinity T1 hand has Mox Opal + Ornithopter + unenabling lands. Assert: Ornithopter not cast (or cast only when metalcraft threshold would fire). |
| B | `test_plating_no_carrier_held.py` | Setup: Affinity T3 hand has Plating, board empty. Assert: Plating not cast. |
| C | `test_wrath_x_optimizes_sweep.py` | Setup: Boros has 5 mana, opp has Memnite+Ornithopter+Mox+Saga (4× CMC-0) and Thought Monitor (CMC 7). Assert: X=0 chosen (destroys 4 for min mana) — because X=higher adds zero destruction. |
| D | `test_permanent_threat_ornithopter_vs_affinity.py` | Setup: opp plays Affinity with Plating on board. Assert: `permanent_threat(Ornithopter, opp, game) > 0`. Regression: same setup vs Zoo-owner, threat should be ≈0. |
| E.1 | `test_cycle_non_reanimate_deck_scores_low.py` | Setup: Affinity has cyclable creature + no reanimation in deck. Assert: cycle EV < cast EV of the same card next turn. |
| E.2 | `test_landcycling_searches_library.py` | Setup: Sojourner's Companion in hand, library contains artifact lands. Trigger cycle. Assert: artifact land in hand, library shuffled. |

Each test must fail at HEAD before the corresponding fix. After the fix, all pass, and the full suite must still be green (currently 180/181, with `test_pinnacle_emissary_cast_trigger_accrues_persistent` a pre-existing unrelated failure).

---

## 7. Ordering — what to do first

**Phase 1: foundational** (must precede everything else)
1. **Write all six failing tests first.** This establishes the specification before any implementation. Confirms each bug reproduces.
2. Implement `_enumerate_this_turn_signals` + `_compute_exposure_cost` in `ai/ev_evaluator.py`.
3. Wire the deferral check in `compute_play_ev`.
4. Add pass-preference tiebreaker in `ev_player.py` candidate selection.
5. Run the suite. Expect Bugs A, B, parts of E.1 to pass. Bugs C, D, E.2 still failing.

**Phase 2: snapshot extension (Bug D)**
6. Add `my_artifact_count`, `opp_artifact_count`, etc. to `EVSnapshot`.
7. Populate in `snapshot_from_game`.
8. Extend `position_value` to use artifact_count conditionally on oracle-visible scaling cards in the owner's deck.
9. Bug D test passes.

**Phase 3: X-cost optimization (Bug C)**
10. Add generic `destroy-by-CMC sweeper` X-selection branch in `engine/game_state.py` alongside Chalice branch.
11. Bug C test passes.

**Phase 4: rules engine (Bug E.2)**
12. Implement `landcycling` / `typecycling` as distinct paths in the cycling resolver.
13. Bug E.2 test passes.

**Phase 5: retire prototype**
14. Delete the `TODO(prototype/card_ev_overrides)` block in `ai/ev_player.py:533-611`. The general fix subsumes it.
15. Re-run N=20 Boros vs Affinity at seed 50000. Expected: parity with prototype (≥30% Boros WR) or better.

**Phase 6: validate**
16. Full N=50 matrix. Compare to the baseline committed as `metagame_data.jsx` before Phase 1.
17. Commit deltas. Document any unexpected regressions in new experiment log.

---

## 8. What this explicitly does not do

- **Does not introduce card-specific overrides.** No `card_ev_overrides.json` in this doc. The general principle covers the known cases. If edge cases emerge after Phase 6, then `card_ev_overrides` becomes the extension point for overrides — but only for cards where oracle text can't express the signal.
- **Does not touch sideboarding.** Bug F list from the handoff brief stays deferred (sideboarding was already shown not to be the lever — `docs/experiments/2026-04-19_blood_moon_sb_hypothesis_failed.md`).
- **Does not touch mulligan.** Separate concern.
- **Does not add archetype-specific EV paths.** All signals are oracle-driven.
- **Does not add new numeric constants.** All derived from clock primitives, BHI probabilities, or oracle counts.

---

## 9. Risks

**Risk 1 — over-conservatism.** The AI holds too much, develops board too slowly, loses races. Mitigation: Phase 1 test set includes cases where casting IS correct (Mox Opal enabling metalcraft, equip-target-in-hand) to catch this. If mitigation insufficient, review `_enumerate_this_turn_signals` for missing signal types.

**Risk 2 — signal-detection misses a known case.** The 13 signals in §3 are comprehensive but not exhaustive. A card or pattern not covered will deferral-score negative even when casting is correct. Mitigation: Option C protocol — each bug surfaced in testing gets a new signal type added.

**Risk 3 — snapshot extension breaks existing tests.** Adding fields to `EVSnapshot` requires checking every construction site. Mitigation: new fields default to zero/empty; existing call sites keep old behavior until they explicitly use new fields.

**Risk 4 — `position_value` conditional activation is wrong.** "Activates when oracle-visible scaling card exists" requires scanning cards. If the scan is expensive, performance degrades. Mitigation: cache per-game or per-snapshot.

**Risk 5 — Claude Code overscopes.** This is a multi-hour structural change. Temptation is to land phase 1+2 partially and claim progress. Protocol: every phase ends with green tests and a committable snapshot. If running out of time, stop at a phase boundary with everything green.

---

## 10. Opening move for Claude Code

```bash
git pull origin main
python -m pytest tests/ -q  # confirm 180/181 baseline

# Read docs in this order:
#   1. docs/design/ev_correctness_overhaul.md  (this doc)
#   2. docs/handoff/2026-04-19_next-session-brief.md  (older, partial context)
#   3. docs/diagnostics/2026-04-19_affinity_investigation.md
#   4. docs/experiments/2026-04-19_blood_moon_sb_hypothesis_failed.md
#   5. docs/experiments/2026-04-19_mardu_energy_failed.md

# Phase 1 opening task:
# Write all six failing tests from §6. Do not write any fix code.
# Commit each test as a separate commit — single file per commit for
# clean review. Confirm each test fails at HEAD before committing.

# After all six tests committed, proceed to Phase 1 implementation.
```

---

## 11. Session notes worth preserving

Three corrections from the investigator (Pieter) during design, incorporated in this doc:

1. **"No magic numbers. Should be a general calculation."** Early drafts had `ev -= 1.0` and similar penalties. Removed. All costs derived from existing clock/BHI primitives.

2. **"It is not specific to zero mana spells — it is just that there is no clear reason to play the card."** Early drafts limited the rule to 0-cost creatures. Generalized to all spells: the principle is deferrability, not cost.

3. **"We should include the other fixes."** Early drafts focused narrowly on Bug A (Ornithopter). Expanded to Bugs A-E as one coherent overhaul, since they share root cause.

These corrections improved the design significantly. The current form treats the five bugs as symptoms of one missing piece: **EV baseline is "do nothing this turn" when it should be "best alternative action this turn, including future casts of the same card."**
