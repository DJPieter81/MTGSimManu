---
title: Recent work — Phase 2c combo refactor complete
status: archived
priority: historical
session: 2026-04-25
tags:
  - session-summary
  - combo
  - refactor
  - ev-correctness
summary: |
  Session summary moved out of PROJECT_STATUS.md during doc cleanup
  (see CLAUDE.md ABSTRACTION CONTRACT — root allowlist).
---

# Phase 2c combo refactor complete (2026-04-25)

The Phase 2 series (`/root/.claude/plans/lets-first-do-a-curried-rocket.md`)
unified the combo-scoring logic onto a single principled module.  The
legacy 440-LOC `_combo_modifier` in `ai/ev_player.py` is gone; its role
is now owned by `ai/combo_calc.py::card_combo_modifier`, which is
zone-aware (storm / graveyard / mana), role-aware (payoff / fuel /
engine / dig), and arithmetic-derived (no per-card scoring tables).

| PR | Phase | Outcome |
|---|---|---|
| #181 | engine fix | Living End graveyard mutation race (merged) |
| #182 | engine fix | Seed `runner.rng` for matrix determinism (merged) |
| #184 | PR-A | Subtlety ETB references `game.stack._items`, not `.items` (merged) |
| #185 | PR-B (2c.1) | State-query routing + bridge calibration — closed, superseded by #189 |
| #186 | PR-C (2c.2-prep) | `card_combo_modifier` hardened with 14 new unit tests (merged) |
| #189 | PR-D+E hard | Delete `_combo_modifier`, port 5 logic blocks, identity cache (merged) |

**Matrix gate (N=20):** all 17 decks within ±5pp tolerance vs pre-2c
baseline.  Headlines: Ruby Storm **+1.1pp**, Goryo's Vengeance
**+4.1pp**, Living End −0.4pp, Amulet Titan −3.2pp.  Compared to the
abandoned Phase 2b retry (#183) which regressed Storm −20.4pp and
Goryo's −13.0pp, the hard-refactor approach restored or improved
every combo deck.

**What was ported into `card_combo_modifier`:**
- `STORM_HARD_HOLD = -1000.0` rules constant (phase-end mana empty
  is strictly worse than passing the turn)
- `_has_storm_finisher(card, me)` — direct STORM keyword OR tutor
  with valid SB ∪ library target (no hardcoded Wish / Grapeshot)
- `_has_viable_pif(card, me, snap, …)` — flashback-combo card
  requires GY fuel + mana to cast + finisher access (no hardcoded
  Past in Flames)
- `_has_draw_in_hand(card, me)` — cantrip / card_advantage / draw
- Storm=0 ritual chain gate (proper SB-validation)
- Storm≥1 mid-chain gate with hard-hold + soft-penalty + storm-coverage
  escalation (`HALF_LETHAL=0.5`) + draw-miss cascade risk
  (`MIN_CHAIN_DEPTH=3`, `CASCADE_DRAW_FLOOR=1`)

**Performance:** identity-based per-snapshot cache (`id(snap)`) on
`assess_combo`.  All spells scored within one `decide_main_phase`
call share a snap so the assessment runs once per decision instead
of once per spell.  3.6× speedup vs. uncached: 5 Storm vs Azorius
games dropped from 102s → 28s (≈ baseline 5s/game).

**Phase 2 superseded artefacts:**
- Phase 2a (`build_combo_distribution` dispatcher, `OUTCOME_DIST_COMBO`
  flag, PR #179 merged) — flag stays `False`; the dispatcher is
  dormant after #189 and may be revisited in a future phase if the
  single-turn distribution model gains multi-turn lookahead.
- Phase 2b (PR #183, closed) — single-turn distribution couldn't
  represent multi-turn combo setup.
- Phase 2c.1 (PR #185, closed) — state-query routing + bridge
  calibration; obsoleted by the hard refactor.

Anti-patterns rejected during this work:
- Magic constants in `card_combo_modifier` — every numeric value is
  derived from CR damage rules, ritual_mana oracle parsing, or
  STORM-profile fuel thresholds (with inline justification)
- Hardcoded card names — Past in Flames, Wish, Grapeshot, Empty the
  Warrens all detected by tags / oracle text patterns
- Per-card EV tables — would have re-introduced the `card_ev_overrides`
  pattern retired in EV correctness Phase 5
