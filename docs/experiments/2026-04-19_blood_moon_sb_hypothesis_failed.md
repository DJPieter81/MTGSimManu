---
title: FALSIFIED: Blood Moon SB retention vs Affinity
status: falsified
priority: historical
session: 2026-04-19
tags:
  - sideboard
  - falsified
  - do-not-retry
summary: Keeping Blood Moon MB post-SB vs Affinity dropped Boros 30% -> 10-15%. Slot pressure means better cuts preserved.
---
# Failed Experiment: Blood Moon MB-retention vs Affinity (Apr 2026)

**Designed:** 2026-04-19 (late session)  
**Hypothesis:** `engine/sideboard_manager.py:155-157` boards OUT Blood Moon vs Affinity. Since modern Affinity runs 4× Urza's Saga + bridge lands, Blood Moon should be a premier answer (turns Saga into Mountain, kills Construct engine). Keeping Blood Moon MB post-SB should increase Boros's WR.  
**Status:** FALSIFIED — reverting Blood Moon board-out cost Boros 10-15pp WR in the matchup.  
**Decision:** Reverted. Original SB logic preserved.

## Chain of reasoning

Found in Bo3 replay analysis (seed 60800 G2): Affinity won with 3× 10/10 Constructs from Urza's Saga T9. Boros never answered the Sagas themselves. Looked at SB logic, saw Blood Moon was being cut vs Affinity with rationale comment "their lands already produce R" — a heuristic written before Affinity's Saga pivot.

## Hypothesis tests (Option C protocol)

Wrote 3-test unit test `tests/test_blood_moon_vs_affinity.py`:
1. Blood Moon stays in MB vs Affinity (failing at HEAD)
2. Blood Moon stays in MB vs Pinnacle Affinity (failing at HEAD)
3. Blood Moon still cut vs Izzet Prowess (regression — passing)

Made two fix attempts against the failing tests:

### Attempt 1 — Remove from board-OUT + add to board-IN list
```python
# board-out: remove "affinity", "pinnacle"
# board-in: add "affinity", "pinnacle"
```
Result: N=20 seed 50000 → **Boros 10%** (was 30% pre-change). All 3 tests passed.

### Attempt 2 — Remove from board-OUT only (conservative)
Just don't cut Blood Moon; don't force extra copies IN.  
Result: N=20 seed 50000 → **Boros 15%** (was 30% pre-change). All 3 tests passed.

## Why it failed

Blood Moon vs Affinity is a good card **in theory** but the SB swap is already nearly-optimal:

| Slot | Pre-fix (cuts BM) | Post-fix (keeps BM) |
|---|---|---|
| IN | Wear/Tear ×2, Wrath ×2 | Wear/Tear ×2, Wrath ×2 |
| OUT | BM ×1, Bombardment ×1, Voice ×2 | Bombardment ×2, Voice ×2 |

**Key insight:** Goblin Bombardment is actually *good* vs Affinity (sac Phlage/Pyromancer tokens → 2 face damage, can also target opponent's critical artifact via sac-effect chain). Voice of Victory's Mobilize 2 makes it a great go-wide piece. Keeping Blood Moon forced OTHER cuts that mattered more per-slot.

Additionally: Blood Moon at 3CMC arrives T3 earliest, T4 typical. Affinity kill turn is 5-6. That's 1-2 turns of impact — not enough when Plating has already resolved and Construct tokens are CMC-0 lands-that-became-Mountains (Blood Moon doesn't un-resolve existing tokens).

## The lesson

**Sideboard heuristics encode tested outcomes, not first-principles card evaluation.** The original logic wasn't wrong — it was over-explained with a misleading comment ("their lands produce R"). The real reason to cut Blood Moon is: **slot pressure forces better cards (Bombardment, Voice) to the sideboard**. The comment misled the investigator (me).

## Follow-ups that might actually move the matchup

1. **Opponent-side anti-Saga tech**: not Blood Moon but a card that answers constructs. Boseiju-class land destruction, Kataki War's Wage (2 toughness, cost per upkeep to opp), or just not trying to answer Saga and instead racing harder.
2. **Comment the SB logic with the real reason** (slot pressure + T3-4 kill speed), not the stale "lands produce R" reason. This is a documentation bug, not a mechanics bug.
3. **The WR gap is not in the SB** — it's upstream. Fix opponent-side EV awareness of artifact count before touching SB again.

## Related files (not changed)

- `engine/sideboard_manager.py` — unchanged, reverted
- `tests/test_blood_moon_vs_affinity.py` — deleted (tested a false hypothesis)
- `ai/ev_player.py` — unchanged (Plating prototype from commit `4147fe4` still in place)

## Proof: prefix/postfix N=20 at same seed

```
Pre-experiment  (baseline): Boros 30%  Affinity 70%  (seed 50000, N=20)
Attempt 1 (BM in + out-list): Boros 10%  Affinity 90%  (seed 50000, N=20)
Attempt 2 (BM out-list only): Boros 15%  Affinity 85%  (seed 50000, N=20)
Post-revert  (back to base): should reproduce baseline ~30%
```
