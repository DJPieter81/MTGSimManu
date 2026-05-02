---
title: Affinity 89% — defenders evoke-trade Solitudes on Memnites (board_eval index bug)
status: superseded
priority: primary
session: 2026-04-28
superseded_by: docs/diagnostics/2026-05-02_me_index_sister_bug_audit.md
depends_on:
  - docs/diagnostics/2026-04-23_affinity_consolidated_findings.md
tags:
  - p0
  - wr-outlier
  - affinity
  - board-eval
  - evoke
  - api-bug
summary: |
  Replay (Affinity vs AzCon, seed 50001 T1) shows AzCon evoking
  BOTH Solitudes on 1/1 Memnites — a 4-card trade for ~2 dmg/turn
  prevented.  Root cause: `ai/board_eval.py::_eval_evoke` resolves
  the opponent index from `me.index`, but the Player object never
  carries an `index` attribute.  The `hasattr(me, 'index')` guard
  silently returns False and `opp_idx` defaults to 0.  When P1
  (AzCon) calls _eval_evoke, opp_idx=1-0=1 = AzCon itself.  The
  small-target gate at lines 207-219 reads opp.creatures (its own
  empty creature list) and is skipped; the function falls through
  to `return 1.0`.  AzCon greenlit every Solitude evoke regardless
  of target value.

  Fix: pass `player_idx` explicitly through `evaluate_action` →
  `_eval_evoke` (and sister functions `_eval_dash`, `_eval_combo`,
  `_eval_block`).  Remove the broken `me.index` fallback.

  Validation @ n=8: defenders vs Affinity gained +13-15pp:
    - Boros Energy:   25% → 38%  (+13pp)
    - Dimir Midrange: 25% → 38%  (+13pp)
    - Jeskai Blink:   10% → 25%  (+15pp)
    - Azorius Control: 0% → 0%   (still bottlenecked on holdback —
                                   docs/diagnostics/2026-04-23
                                   _affinity_consolidated_findings.md
                                   Bundle 3 A1-A5)
---

# Affinity — defenders evoke-trade Solitudes on Memnites

## Replay

`run_meta.py --verbose Affinity "Azorius Control" -s 50001` —
Affinity wins T5.  T1 trace (AzCon won die roll, P2 = Affinity):

```
T1 P2: Play Meticulous Archive (enters tapped)
T1 P1: Play Urza's Saga
T1 P1: Cast Memnite (0)
T1 P1: Cast Memnite (0)
T1 P1: Cast Ornithopter (0)
T1 P2: Evoke Solitude (exile Orim's Chant)   ← BUG
T1 P2: Solitude exiles Memnite                  (gains 1 life)
T1 P2: Evoke Solitude (exile Orim's Chant)   ← BUG
T1 P2: Solitude exiles Memnite                  (gains 2 life)
```

AzCon spends 4 cards (2 pitched Orim's Chants + 2 evoked Solitudes,
each pitched and sacrificed) to exile 2× Memnite (1/1 vanilla,
worth ~1 damage/turn each).  Ornithopter, Urza's Saga, and the
opponent's hand are all untouched.

## Root cause — silent index resolution

`ai/board_eval.py::_eval_evoke` opens with:

```python
opp_idx = 1 - (me.index if hasattr(me, 'index') else 0)
opp = game.players[opp_idx]
```

The `Player` class in `engine/game_state.py` does not assign an
`index` attribute.  `hasattr(me, 'index')` returns `False`, so
`opp_idx = 1 - 0 = 1`.  When `me` is player 1 (AzCon defending),
`opp_idx = 1 = AzCon itself`.  Every reference to `opp.creatures`
or `opp.battlefield` reads AzCon's own state.

Concrete consequence for the small-target gate at lines 207-219:

```python
if 'removal' in tags and card.template.is_creature:
    if not opp.creatures and not opp.battlefield:
        return -10.0   # ← skipped: AzCon has Meticulous Archive on bf
    heals_opponent = "gains life" in oracle and "power" in oracle
    if heals_opponent and opp.creatures:
        # ← skipped: opp.creatures is AzCon's own (empty) creature list
        ...
        if target_power <= th.evoke_skip_small_power and target_cmc <= ...:
            return -2.0
```

Both branches that would reject the evoke are gated on the wrong
opponent's state.  Function falls through to `return 1.0` — evoke
greenlit.

This bug masks the evoke-skip-small-target heuristic that has been
in the codebase for some time.  The heuristic was correct; it just
never ran with the right opponent.

## Fix

Pass `player_idx` explicitly through `evaluate_action` →
`_eval_evoke`.  Remove the unsafe `me.index` fallback (or keep it
as a backup branch only).

```python
# evaluate_action (caller knows player_idx)
def evaluate_action(game, player_idx, action):
    me = game.players[player_idx]
    if action.action_type == ActionType.EVOKE:
        return _eval_evoke(game, me, assessment, action.context,
                            player_idx=player_idx)

# _eval_evoke (now receives the correct index)
def _eval_evoke(game, me, a, ctx, player_idx=None):
    if player_idx is None:
        player_idx = getattr(me, 'index', 0)
    opp_idx = 1 - player_idx
    opp = game.players[opp_idx]
    ...
```

Same pattern applied to `_eval_dash`, `_eval_combo`, `_eval_block`,
and the `me.index` references in `castable_*` accumulator
expressions.  All six call sites now use explicit `player_idx`.

## Class-size

This bug affected EVERY decision routed through `evaluate_action`
when called from player 1's perspective (always-broken since the
Player class never had `.index`).  The fix is mechanical: every
caller of `evaluate_action` already knows the right `player_idx`;
plumbing it through is correct API hygiene.

The evoke-skip heuristic itself (lines 207-219) is generic across
all evoke pitch elementals — Solitude, Subtlety, Endurance, Grief,
Fury — and any future evoke printing.  No card names; the rule is
"`heals_opponent = "gains life" in oracle and "power" in oracle"`
+ `target_power <= 2 AND target_cmc <= 2`.

## Validation

Defenders vs Affinity, n=8 each, seeds 50000+:

| Defender | Pre-fix | Post-fix | Δ |
|----------|--------:|---------:|----:|
| Azorius Control | 0% | 0% | flat |
| Boros Energy | 25% | **38%** | **+13pp** |
| Dimir Midrange | 25% | **38%** | **+13pp** |
| Jeskai Blink | 10% | **25%** | **+15pp** |

3 of 4 defenders gained +13-15pp.  AzCon stayed flat — its issue
is the holdback-overhaul work (Bundle 3 A1-A5 in
`docs/diagnostics/2026-04-23_affinity_consolidated_findings.md`),
not evoke.

Affinity perspective (P1, on the play): essentially unchanged
(~88% aggregate), as expected — Affinity's wins were always
asymmetric on the play; the evoke fix matters for the defender
matchups specifically.

## Test

`tests/test_evoke_not_wasted_on_vanilla.py` — 2 tests:
1. AzCon does NOT evoke Solitude on Memnite (bug repro)
2. AzCon DOES evoke Solitude on Plating-equipped 7/7 (regression)

Both green post-fix.

## What this does NOT cover

- AzCon vs Affinity at 0%: the holdback bottleneck (Bundle 3 in
  the consolidated doc) is the next P0 for that matchup.  AzCon
  also has an unimplemented Isochron Scepter line which is a
  known gap.
- Affinity's high WR vs other defenders is partly genuine (the
  deck IS fast in Modern); the fixes here trim the worst trades
  but don't change the matchup curve dramatically.
- The `me.index` references in other modules — this fix only
  touched `ai/board_eval.py`.  Sister bugs may exist in
  `ai/evaluator.py`, `ai/response.py`, etc. (separate scope).
