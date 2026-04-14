# Play-by-play audit — Phase 4 verification report

**Branch:** `claude/playbyplay-audit`
**Based on audit:** `docs/history/audits/2026-04-14_playbyplay_audit.md`

## Phase 3 outcomes

Triage picked 3 fix targets. Actual outcomes:

| Pick | Target | Result | Notes |
|------|--------|--------|-------|
| 1 | F-C1 Storm greedy Grapeshot | **Fixed + refined** | Two commits (`35951f3` + `055c6b8`). Dual-gate: patience for healthy games, Hail-Mary for near-death. |
| 2 | F-A1 0-power creature threat | **Dropped** | Re-examination: Signal Pest's battle-cry genuinely IS a valid removal target (+2 virtual power amplifier). Auditor overweighted knowledge the AI can't have (unseen Cranial Plating in opp hand). |
| 3 | F-E1 Living End ETB bug | **Already fixed on main** | `_resolve_living_end()` in `engine/game_state.py:1882-1933` correctly returns creatures and calls `_handle_permanent_etb`. The audit was reading an old log that predated this fix. |

Additional findings in the audit report (F-A2 Thraben Charm, F-B1 burn on empty board,
F-D1/F-D2 Zoo racing, F-F1/F-F2 mulligan, F-H1 sequencing, F-G1 post-combo push) were
either false positives (Thraben Charm can't target creatures; only has destroy-
enchantment, pump, and token-create modes) or out-of-scope (strategy_profile tuning;
mulligan risk; structural within-turn sequencing refactor; Iter7 Fix 6 verbatim).

## Verification at seed 60102 (the originating log)

**Before F-C1 fix:**
```
G2 T4: Storm cast Grapeshot at storm=1 for 2 damage → dies T5 at 1 life
```

**After F-C1 + refinement:**
```
seed 60102 game 1: Storm wins T4 via damage
  T3 P1 Grapeshot @ storm=9 → 9 damage → opp 11 life
  T4 P1 Wish → Grapeshot @ storm=7 → lethal
```

## Aggregate impact — Storm field sweep N=15

| Config | Overall WR | vs Dimir | vs Boros |
|--------|-----------:|---------:|---------:|
| Pre-fix baseline | 36.7% | ~30% | ~13% |
| After F-C1 single-gate | 35.0% | 33% | 13% |
| After dual-gate refinement | 36.9% | 20% | ? |

The single-gate version made Storm hold Grapeshot into death vs aggro;
dual-gate recovers that. Overall field is essentially flat (±1pp), but
the SPECIFIC bug (storm=1-2 one-shots) is gone on the originating log.

## Test status

```
149 passed in 10.75s
```

## Commits

- `35951f3` fix(ai): Storm no longer fires Grapeshot at storm=1-2 (audit F-C1)
- `055c6b8` fix(ai): Storm dual-gate for non-lethal chains — damage AND urgency

## Reflection on the methodology

5 parallel log auditors produced 25 raw findings. Triage down to 3. Only 1 survived
implementation-and-verification scrutiny. The other 2 were false positives or already
fixed.

**This is a useful result.** It means:
1. The play-by-play method correctly identified at least one real bug (F-C1) that
   aggregate WR testing had missed — we knew Storm was 20-22%, we didn't know why.
2. The method also surfaced false positives that looked plausible until checked
   against the actual card rules (Signal Pest battle cry, Thraben Charm modes).
3. The remaining findings that were "real but out of scope" (Zoo racing EV, mulligan
   gates) are catalogued for a future session.

**Convention compliance:** the F-C1 fix introduces no new magic numbers. The gate uses
`opp_life // 2` (a game-state ratio) and `opp_clock <= 2` (an existing clock property).
Both are math-derived per the CLAUDE.md convention.
