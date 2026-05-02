---
title: me.index sister-bug audit — board_eval fallback uses wrong attribute
status: active
priority: primary
session: 2026-05-02
supersedes: docs/diagnostics/2026-04-28_affinity_evoke_overtrading.md
depends_on:
  - docs/diagnostics/2026-04-28_affinity_evoke_overtrading.md
tags:
  - p0
  - api-bug
  - board-eval
  - audit
summary: |
  Followup to PR ~#210 (commit 03ccff3).  That fix plumbed
  `player_idx` explicitly through `evaluate_action` in
  `ai/board_eval.py` and left a defensive
  `getattr(me, 'index', 0)` fallback in three private helpers
  (`_eval_evoke`, `_eval_dash`, `_eval_combo`).  PlayerState
  carries `player_idx`, not `index`, so the fallback silently
  returned 0 — collapsing opp_idx to self when me was player 1.

  This audit grepped every `.index` / `hasattr(*, 'index')` /
  `getattr(*, 'index'*)` / `1 - .*index` reference in `ai/` and
  `engine/`.  The only sister sites were the three fallbacks in
  `ai/board_eval.py` already noted above.  All other opponent-
  index resolution in the codebase already uses `player_idx`
  (callers explicitly pass it through, or use `self.player_idx`
  on the AI player object, or `p.player_idx` on PlayerState).

  Fix: change the three fallbacks from
  `getattr(me, 'index', 0)` to `getattr(me, 'player_idx', 0)`.
  This is a structural one-line correction that turns the
  fallback from a silent bug into a correct backup path —
  PlayerState always carries `player_idx`, so the fallback is
  guaranteed to resolve to the right index.
---

# me.index sister-bug audit

## Method

```bash
grep -rn "hasattr(me, 'index')" ai/ engine/
grep -rn "hasattr(.*, ['\"]index['\"])" ai/ engine/
grep -rn "me\.index" ai/ engine/
grep -rn "getattr(me, 'index'" ai/ engine/
grep -rn "getattr(.*, 'index', " ai/ engine/
grep -rn "1 - .*index" ai/ engine/
```

## Hits classified

| Site | Pattern | Classification |
|---|---|---|
| `ai/board_eval.py:210` (`_eval_evoke`) | `getattr(me, 'index', 0)` fallback | **(a) bug-affected** — fixed |
| `ai/board_eval.py:298` (`_eval_dash`)  | `getattr(me, 'index', 0)` fallback | **(a) bug-affected** — fixed |
| `ai/board_eval.py:329` (`_eval_combo`) | `getattr(me, 'index', 0)` fallback | **(a) bug-affected** — fixed |
| All other `1 - player_idx` / `1 - self.player_idx` sites in `ai/` and `engine/` | Caller-passed index | **(b) pre-fixed** — no change |
| `card.instance_id`, `card_types.index(...)`, list `.index(...)` | Unrelated | **(c) false-positive** — no change |

The bug-affected sites are unreachable from production paths
because all current callers (`evaluate_action`, `should_evoke`,
`should_dash` in `engine/game_runner.py`, `decide_blockers` in
`ai/ev_player.py`) pass `player_idx` explicitly.  The fallback
was, however, *latently buggy* — any future caller that omitted
the argument would have silently routed wrong-opponent state.

## Why fix the fallback rather than delete it?

Two principled options were considered:

1. **Per-site plumbing** (the prior approach): make `player_idx`
   mandatory and remove the fallback.  Pro: forces correctness at
   call sites.  Con: brittle for tests and any future caller; the
   helpers are private (`_eval_*`) and a sentinel-based fallback
   is reasonable defensive coding.
2. **Structural fix**: keep the fallback, but read the right
   attribute (`player_idx`).  Pro: PlayerState always carries
   `player_idx` (set in `engine/game_state.py::GameState.__init__`
   when the two PlayerStates are constructed), so the fallback is
   guaranteed correct.  One-line change.

We picked option 2.  The fallback now matches the actual class
contract — PlayerState carries `player_idx`, not `index` — and
the silent-zero failure mode is impossible.

## Tests

`tests/test_player_idx_explicit_plumbing.py` — three tests, one
per affected helper, each constructing a 2-player state where
P0 is the actual opponent (with creatures / low life) and me is
P1.  Each test calls the helper with `player_idx=None` to
exercise the fallback, and asserts the helper reads P0's state
(not P1's).  All three are red on pre-fix and green on post-fix.

## Notes

- The audit found **zero** sister sites in `ai/evaluator.py`,
  `ai/response.py`, or any other AI/engine module.  The original
  diagnostic's "may exist in evaluator.py, response.py" note can
  be closed: it does not.
- Class size: any future call to `_eval_evoke`/`_eval_dash`/
  `_eval_combo` that omits `player_idx`.  Currently 0; the fix
  is preventive, not behavior-changing in production.
