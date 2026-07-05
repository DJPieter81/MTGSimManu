---
title: "Session handoff 2026-07-05 — E1 karoo/multi-mana fixed on local branch; recovery steps"
status: active
priority: primary
session: 2026-07-05
depends_on:
  - docs/diagnostics/2026-07-05_calibration_probe_findings.md
tags: [handoff, session-recovery, karoo, multi-mana, e1]
summary: >
  E1 (multi-mana land units + karoo ETB return clause) implemented,
  tested, committed on LOCAL branch claude/e1-karoo-multi-mana —
  4 commits, NOT pushed (no PAT in session).  Crash insurance:
  apply_session_2026-07-05.sh + format-patches delivered to the user
  as downloads.  This doc is the replay script for the whole session.
---

# Session handoff — 2026-07-05 (E1)

## State at handoff

- **Branch (local only):** `claude/e1-karoo-multi-mana`, 4 commits on
  top of `main@0dbc81b` (PR #440 merge):
  1. `engine(mana): multi-mana land units — one tap yields every unit (E1)`
  2. `engine(land): uniform land-entry hook fires the karoo return clause (E1b)`
  3. `test: refresh WR-anchor after E1; docs: calibration-probe findings`
  4. `replays: calibration-probe evidence (s60104, s60105 pre/post E1)`
- **Not pushed.** Recovery artifact: `apply_session_2026-07-05.sh`
  (+ `e1_patches/*.patch`) delivered via chat download.  Re-apply:
  `bash apply_session_2026-07-05.sh` from a clean clone of `main`.
- **Gates at commit time:** new tests 15/15; targeted regression 39
  passed; full suite 2151 passed / 18 skipped / 2 xfailed (parallel-
  run failures verified serial-green = xdist artifacts); abstraction
  0 card-name / 0 deck-gate; magic numbers 13/13; doc hygiene OK.

## What was done (exact steps)

1. **Probes** (calibration method — see diagnostics doc):
   - `run_meta.py --bo3 "Azorius Control" affinity -s 60104
     --dump-replay replays/az_aff_60104.ndjson > replays/az_aff_60104.txt`
   - `run_meta.py --bo3 amulet dimir -s 60105 --dump-replay …`
   - Findings E1–E4, A1–A2 logged in
     `docs/diagnostics/2026-07-05_calibration_probe_findings.md`.
2. **Failing tests first** (contract):
   - `tests/test_multi_mana_land_units.py` (11) — parser units,
     fixed-pip single-tap payment, no double-pay of one color,
     generic-2 alone, capacity, can_cast feasibility.
   - `tests/test_land_etb_returns_own_land.py` (4) — flag parse,
     bounce on land drop, self-return when only land, mass-land-
     search entry path fires the same hook.
3. **E1 implementation:**
   - `engine/cards.py`: `mana_units: List[List[str]]`,
     `etb_return_land: bool`, `mana_count` property.
   - `engine/card_database.py`:
     `OracleTextParser.detect_land_mana_units` (plain `{T}: Add …`
     lines only; payload cut at first period; "Spend this mana only"
     lines excluded; worded quantities; or-lists = one unit choice);
     karoo ETB-return regex → template flag; both wired into the
     land template constructor.
   - `engine/mana_payment.py`: `land_mana_units` helper; colored MRV
     assignment over `(land, unit)`; generic payment consumes spare
     units (committed lands first, Tron bonus once per land); tap
     executes ALL units, spares float to pool; log `Name→GU`;
     `_last_colors_spent` from assigned units.
   - `engine/cast_manager.py`: feasibility = one MRV source per
     unit (2 sites); Converge/X land loop unit-aware; totals →
     capacity.
   - `engine/player_state.py`: `untapped_mana_capacity()`.
   - Capacity swap at 13 `len(untapped_lands)`-as-mana sites:
     cast_manager, game_state:394, cycling, card_effects:1354,
     game_runner (2), mana_payment verbose line, ai/ev_player (3),
     ai/gameplan.
4. **E1b implementation:**
   - `engine/land_manager.py`: `apply_land_etb_static` = untap-on-
     enter watchers THEN mandatory return clause; deterministic
     engine-neutral bounce choice (prefer tapped, fewest units,
     fewest colors; self if only land).  All previous
     `apply_untap_on_enter_triggers` call sites swapped (land drop,
     crack_fetchland, 3× card_effects fetch/mass-search);
     `GameState._apply_land_etb_static` wrapper + back-compat alias.
5. **Validation:** same-seed s60105 flips Amulet 0-2 → 2-1; karoo
   taps `→GU`; 6 ETB-return events, ordering enters-tapped → Amulet
   untap → return; Titan cast 3× (all countered — decision layer).
   `--matchup amulet dimir -n 30` → 13%: rules unblocked, decision
   layer now binding.
6. **WR anchor:** 3/17 entries turn-count drift only, 0 winner
   flips; refreshed via `tools/refresh_wr_baseline.py`.

## Next session (priority order)

1. Push branch + open PR (standing approval).
2. A1 attack-with-lethal gate (`ai/turn_planner.py`) — Affinity T7
   s60104 G2 evidence; single predicate.
3. E3 loyalty accrual (`engine/planeswalker_manager.py`) — +1s never
   change loyalty_counters (s60104 G2 T5–T8 Teferi).
4. E2 imprint/copy-cast legality (`engine/cast_manager.py`) —
   upkeep, no target, no cost (s60104 G2 T8).
5. M2 counter triage payoff-vs-fuel — now also Amulet-side (Titan
   into held UU ×3, s60105 post-fix).
6. Institutionalize: matchup-level EXPECTED table +
   `tools/check_calibration.py`; replay linter over `--dump-replay`
   NDJSON (checks listed in the diagnostics doc).
