---
title: Recent work — EV Correctness Overhaul complete
status: archived
priority: historical
session: 2026-04-20
tags:
  - session-summary
  - ev-correctness
  - overhaul
summary: |
  Session summary moved out of PROJECT_STATUS.md during doc cleanup
  (see CLAUDE.md ABSTRACTION CONTRACT — root allowlist).
---

# EV Correctness Overhaul complete (2026-04-20)

The EV-correctness design doc (`docs/design/ev_correctness_overhaul.md`,
`status: superseded`) is closed.  Nine phases shipped across PRs #122,
#128, #130, #132, #133:

| Phase | Focus | Outcome |
|---|---|---|
| 1 | Deferral baseline + pass-preference tiebreaker (Bugs A, B, E.1) | Signal framework + `_enumerate_this_turn_signals` |
| 2 | Conditional artifact-count term (Bug D) | `EVSnapshot` + `position_value` extended |
| 3 | Marginal-destruction X optimizer (Bug C) | Wrath of the Skies picks best X |
| 4 | Landcycling / typecycling resolver (Bug E.2) | Sojourner's Companion tutors lands |
| 4.5 | Signal-based mulligan escape (Bug F) | Anti-matchup hands kept |
| 5 | Retire card_ev_overrides prototype | Phase 2's artifact term subsumes it |
| 6 | N=20 matrix validation | Flagged Storm / Amulet Titan / Pinnacle Affinity |
| 7 | Pinnacle Emissary `other_enters` trigger | Closed last failing test — suite 226/226 |
| 8 | Life / energy persistent_power | Closed Phase 7's Boros regression |
| 9 | Phase 6 follow-ups | Storm finisher patience + Amulet engine signal + Pinnacle hypothesis falsified |

Full suite **232/232** (from baseline 196/197 with 1 pre-existing failure).
Matrix trend: weighted WRs up (more balanced meta), flat WRs down
(AI defers junk casts across the board).  Affinity still top (~83% wtd),
Boros stable, Storm / Amulet Titan / Goryo's all recovered from their
post-Phase-6 lows.

Experiment log chain (all `status: archived`):
`docs/experiments/2026-04-20_phase6_matrix_validation.md` →
`docs/experiments/2026-04-20_phase7_pinnacle_emissary_fix.md` →
`docs/experiments/2026-04-20_phase8_life_energy_persistent.md` →
`docs/experiments/2026-04-20_phase9_phase6_followups.md` (tip).
