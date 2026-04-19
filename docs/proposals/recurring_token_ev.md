# Recurring-Token EV Subsystem — Proposal

**Status:** Draft, design-only. Awaiting approval before implementation.
**Author:** Claude Code session, 2026-04-19
**Scope:** Extend Bug 3 fix into a principled lifetime-token EV model.
**Supersedes:** The weighted-clause classifier draft discarded earlier in
this session (magic-number weights violated `CLAUDE.md` rules).

---

## 1. Problem

`_project_spell` in `ai/ev_evaluator.py` projects the board state
*immediately after spell resolution*. Under those semantics, tokens
produced by ETB triggers are correctly credited (token is on the
battlefield when the spell finishes resolving), but tokens produced by
any other trigger are **not yet created** at projection time:

| Card | Token trigger | Immediate credit | Lifetime credit |
|---|---|---|---|
| Ajani, Nacatl Pariah | ETB → 2/1 Cat | +2 | +2 |
| Orcish Bowmasters | ETB + opp-draw → amass 1 | +1 (ETB fire only) | +N (opp draws) |
| Ocelot Pride | EOT if life-gained → 1/1 Cat | 0 | +R (R = residency) |
| Pinnacle Emissary | cast-artifact → 1/1 Drone | 0 | +S (S = spells × residency) |
| Voice of Victory | attack → 2× 1/1 | 0 | +A (A = attacks) |
| Ragavan, Nimble Pilferer | combat damage → Treasure | 0 (non-creature) | 0 (non-creature) |

The Bug 3 fix (commit `22ba31a`) gave the correct answer for the
**immediate** column but left the **lifetime** column empty. That
under-rates Ocelot Pride, Pinnacle Emissary, and similar cards. The
re-baseline matchups showed Dimir and Jeskai regressing because the
AI now values these creatures by raw body only.

The quick fix (add per-trigger weights 0.5 / 0.8 / 0.3) introduced
magic numbers and was rejected — the repo rule in `CLAUDE.md` requires
numeric constants to be either (a) derived from a principled subsystem
or (b) justified rules constants. Arbitrary "this trigger is worth
0.8" fails both tests.

## 2. Design goals

1. **Capture lifetime token value** for recurring-token cards without
   regressing ETB-correct cards.
2. **Zero magic numbers.** Every rate / weight derived from
   `ai/clock.py`, `ai/bhi.py`, the active `EVSnapshot`, or inline
   rules constants with one-line justifications.
3. **No card names** in the classifier.
4. **Projection semantics stay honest.** `my_power` still represents
   creatures currently on the battlefield. Lifetime value lives in a
   new, explicitly-named field.
5. **Single commit per the brief's Option C.** Test + fix together,
   regressions guarded.

## 3. Architecture

### 3.1 Separate immediate from lifetime

Add a new field to `EVSnapshot`:

```python
@dataclass
class EVSnapshot:
    ...
    persistent_power: float = 0.0
    # Expected power contribution from recurring triggers over the
    # expected residency of permanents we control. Not a currently-
    # on-board quantity — this is a forward projection.
```

`_project_spell`:
- Immediate tokens (ETB, ETB-chained amass) → `projected.my_power`,
  `projected.my_creature_count` (existing behaviour).
- Recurring tokens (all other trigger classes) →
  `projected.persistent_power` weighted by expected triggers over
  residency.
- Treasure / food / clue / gold tokens → 0 power contribution (same
  as today). Note: treasure does have real value; it belongs in a
  future mana-clock extension, out of scope here.

Score consumers (`_score_spell`, `score_position`) incorporate
`persistent_power` with a discount = `urgency_factor` (already defined
in `EVSnapshot`, derived from `opp_clock` and `PERMANENT_VALUE_WINDOW`).
That keeps "future tokens matter less when I'm about to die" baked
into the score math without a new magic multiplier.

### 3.2 Trigger-rate derivation (no magic weights)

Each trigger class has a *rate* (expected firings per turn) and
*lifetime* (turns on the battlefield before removal). Multiply
rate × lifetime × token_power → lifetime contribution.

**Lifetime / residency.** Reuse `PERMANENT_VALUE_WINDOW = 2.0` from
`urgency_factor`. It is already the declared half-life for a typical
deferred permanent's payoff curve. Using it here keeps the
assumptions coherent across the evaluator.

**Trigger rate per class:**

| Trigger class | Rate per turn | Derivation |
|---|---|---|
| ETB | 1.0 / lifetime (fires once total) | rules: ETB triggers fire once |
| attack trigger | `1.0` if `my_power > 0 and opp_clock > 1`, else `0` | we attack when we're ahead and survive — derivable from `snap.my_clock` vs `snap.opp_clock` |
| combat-damage trigger | attack_rate × connection_prob | connection_prob = `1.0` if evasion or `opp_creature_count == 0`, else `my_power / (my_power + opp_toughness)` — derived from combat simulation already in `ai/turn_planner.py` |
| end-step trigger | `1.0` (unconditional, every turn) | rules: end step happens every turn |
| end-step trigger with condition | `life_gain_rate_per_turn` or equivalent | derive from snap: if `snap.my_lifelink_power > 0` then 1.0, else 0 |
| cast trigger | `spells_per_turn` | derive from `snap.storm_count` + `snap.my_mana / avg_cmc` (avg_cmc is a deck-declared constant in `gameplan`) |
| dies trigger | `1.0 / lifetime` (fires once total) | rules: dies once |

All inputs to these rates come from `EVSnapshot` fields or already-
declared rules constants (`PERMANENT_VALUE_WINDOW`, average CMC in the
gameplan). None require new tunables.

### 3.3 Token power derivation

Unchanged from current Bug 3 logic — parse oracle:

- `N/M creature token` → N
- `amass N` (numeric or word form) → N
- `treasure | food | clue | gold | blood | map | powerstone token` → 0
- generic `creature token` with no P/T → 1

Factored into a helper `_parse_token_power(clause) -> int`.

### 3.4 Clause-to-trigger mapping

Split oracle on sentence / newline boundaries. For each clause:

1. If clause contains `create` or `amass`:
2. Classify its trigger via pattern-match on the clause itself OR
   inherit from the most-recent preceding clause that had a trigger
   (handles Bowmasters-style "Then amass …" continuations).
3. Look up `rate(class, snap)` and `token_power(clause)` from §3.2 / §3.3.
4. If ETB class → immediate credit. Else → persistent_power credit.

No weights applied; rates come entirely from §3.2 formulas.

### 3.5 Consumers

Score functions that read `my_power` also read `persistent_power`
discounted by `urgency_factor`:

```python
effective_power = snap.my_power + snap.persistent_power * snap.urgency_factor
```

Callsites to touch:
- `ai/ev_evaluator.py` — `_score_spell`, `score_position` (if the
  latter treats power).
- Possibly `ai/ev_player.py` — any place that reads
  `projected.my_power` for decisions. Audit during implementation.

## 4. Test strategy

### 4.1 Regression anchors (must keep passing)

- Ajani ETB → +2 my_power, +1 persistent_power count
- Ragavan combat-damage treasure → 0 my_power, 0 persistent_power
- Ajani > Ragavan on combined score

### 4.2 New coverage

- Bowmasters ETB amass → +1 my_power (ETB amass credit)
- Bowmasters lifetime (opp-draw amass) → persistent_power > 0
- Ocelot Pride → 0 my_power, persistent_power > 0
- Pinnacle Emissary → 0 my_power, persistent_power > 0 proportional to
  deck's artifact density (mock via gameplan)
- Voice of Victory → 0 my_power, persistent_power > 0 only when
  attacks are profitable (vary opp_clock)
- Urgency gating → persistent_power contribution → 0 when
  `opp_clock == 1` (we're about to die)

### 4.3 Invariant candidate

Graduate to `tests/invariants/test_token_parity.py` eventually:
"Two cards whose oracle clauses classify to the same trigger class
and same token power must produce identical projection deltas."

## 5. Sequencing & risk

1. Implement §3.1 snapshot field.
2. Implement §3.2 rate derivation as a pure function taking
   `(clause, snap)` and returning a float — no state, easy to test.
3. Implement §3.3 power parser (refactor from existing Bug 3 code).
4. Wire into `_project_spell`.
5. Update consumers in `_score_spell`.
6. Write tests (§4.2).
7. Run full suite. Fix regressions if any.
8. Re-run section-6 baseline matchups.
9. Single commit.

**Risk — consumer audit.** `my_power` is read in many places
(score_position, BHI, turn_planner). If we add `persistent_power` as
a separate field, the risk is that some callsite does not pick up the
combined `effective_power`. Mitigation: grep every read of
`my_power` during implementation, wrap reads through a helper where
appropriate.

**Risk — rate formulas.** §3.2 trigger rates are first-pass.
Calibration against the full matrix may show they over- or
under-credit specific cards. Mitigation: tests pin expected ranges
(not exact values), so small formula tuning doesn't require rewriting
tests.

## 6. Open questions

1. **Spell density for cast-triggers.** The proposal uses
   `spells_per_turn = snap.storm_count + snap.my_mana / avg_cmc`. Is
   `avg_cmc` a gameplan-declared constant? If not, we'd need to add
   it. Alternative: derive from the deck's mainboard (`sum(CMC) /
   count`). Needs a look at `ai/gameplan.py`.
2. **persistent_power as a single scalar vs per-card.** A single
   scalar aggregates well but loses per-card attribution. For now
   scalar is fine; if card-level projection debugging becomes useful
   we can switch to a dict.
3. **Interaction with `creature_threat_value`.** That function is
   read by targeting heuristics (opp picks best target). Does it
   need to know about `persistent_power` too? For now no —
   `creature_threat_value` is about current-board threat, not future.
   But worth confirming during implementation.
4. **End-step life-gain condition.** Ocelot Pride's trigger requires
   "you gained life this turn". Currently approximated via
   `snap.my_lifelink_power > 0`. True condition needs a
   `life_gained_this_turn` snapshot field — may be worth adding
   (PlayerState already tracks it, just not propagated to snapshot).

## 7. Out of scope

- Mana-clock contribution from Treasure / Food / Clue / Gold /
  Powerstone tokens. These are real value but belong in a
  mana-clock subsystem extension, separate proposal.
- `card_ev_overrides` infrastructure (per-card manual EV hints).
  Handoff brief §8 explicitly defers it until engine bugs close.
- Adjustments to `ai/gameplan.py`'s card-role declarations.

## 8. Rollout plan

1. Review & approve this proposal.
2. Implement on a clean tree (currently clean at `22ba31a`).
3. Run the §6 baseline matchups and compare against the pre-C
   numbers (Boros 36, Jeskai 10, Dimir 16). Expected direction:
   Dimir and Jeskai recover some ground as Ocelot/Pinnacle /
   Bowmasters lifetime triggers return to EV. Boros may stay flat
   or tick slightly up (Ocelot Pride regains).
4. If deltas overshoot (more than ±10pp on any), treat as a
   tuning signal for §3.2 rate formulas — not a reason to abandon.
5. After C lands, resume the handoff queue with Bug 4.
