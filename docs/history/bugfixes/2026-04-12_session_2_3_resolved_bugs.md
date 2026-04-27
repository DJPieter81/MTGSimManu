---
title: Resolved bugs — sessions 2 and 3 (2026-04-12, 2026-04-13)
status: archived
priority: historical
session: 2026-04-12
tags:
  - bug-resolution
  - p0
  - p1
  - p2
  - hardcoding-removal
  - generic-engine-patterns
summary: |
  Bug-fix changelogs lifted out of PROJECT_STATUS.md §7 during doc cleanup.
  All entries here have status FIXED and are kept for traceability only —
  do not re-investigate. Current bug list is in PROJECT_STATUS.md §7
  (P0 — OPEN, Remaining open bugs, Failed attempt — Chalice/Stax,
  Deep audit — WST v2 play-by-play bugs).
---

# Resolved bugs — sessions 2 and 3 (April 12-13, 2026)

### P0 — FIXED (session 3, 2026-04-13)

| # | Issue | Fix | Commit |
|---|-------|-----|--------|
| 9 | **Zero blocks across all games** | Rewrite `_eval_block` with direct damage/value scoring | `8149d0c` |
| 10 | **Not attacking with profitable boards** | Empty-board and combat trigger attack logic; verified 0 non-trivial refusals in Bo3 spot-check | Prior sessions |
| 11 | **0-land mulligan keep** | Mulligan guardrail + combo-mulligan activation | `11e8a57`, `e1d9361` |


### P0 — FIXED (session 2)
| # | Bug | Fix | Commit |
|---|-----|-----|--------|
| 1 | Wrath of Skies uses stored energy not cast X | Use `item.x_value` | `ba15c11` |
| 2 | Ocelot Pride energy on ETB (wrong trigger + oracle) | Noncreature cast trigger; combat damage Cat token | `ba15c11` |
| 3 | DRC misclassified as PROWESS → surveil/delirium never fires | Fix oracle detection; implement surveil GY bin | `eec7ec8` |
| 4 | EE double ETB (X-counter + sunburst both fire) | Gate X-counter path to cards without dedicated ETB handlers | `1c38354` |

### P1 — FIXED (session 2)
| # | Bug | Fix | Commit |
|---|-----|-----|--------|
| 5 | Token power wrongly subtracted in removed state | Tokens persist when parent removed | `9aff147` |
| 6 | Ragavan never attacks (no trigger bonus) | +1.5 EV combat trigger bonus | `704a671` |
| 7 | Storm tutor 20x mid-chain penalty | 20x → 5x | `704a671` |
| 8 | Holdback only fires when opp_power > 0 | Also fires vs creatureless spell decks | `704a671` |
| 9 | Sanctifier double "Resolve" log | Gate log to SPELL items only | `53d372a` |
| 10 | Ephemerate castable with no friendly creatures | `can_cast` blink tag check | `53d372a` |
| 11 | Duplicate Chalice no penalty | -8.0 EV if same name already on battlefield | `53d372a` |
| 12 | `_resolve_sac_effect` crash (undefined variables) | Fixed scoping | `53d372a` |
| 13 | Ephemerate rebound fires without valid target | Gate rebound on `player.creatures` check | `3d1d8a1` |

### P2 — FIXED (session 2)
| # | Bug | Fix | Commit |
|---|-----|-----|--------|
| 13 | CMC 2 removal scaling 0.6 too high | 0.6 → 0.4 | `9aff147` |
| 14 | Evasion creatures over-penalised | 50% damage-removal discount for conditional flyers | `9aff147` |
| 15 | Dovin's Veto positive EV vs aggro | Cap EV vs creature-heavy low-hand boards | `9aff147` |
| 16 | Tron no assembly bonus | +3/+8/+20 per piece via Urza's subtype | `705ea0b` |
| 17 | `rmv=` trace display not matching main path | Detailed path now mirrors main path scaling | `9aff147` |

### Hardcoding removed (session 2)
| Was hardcoded | Now uses |
|---|---|
| `permanent.name == 'Ocelot Pride'` (2 places) | Oracle: `'{e}' in oracle` + `'noncreature spell'` / `'combat damage'` pattern |
| `tron_lands = {'Urza\'s Tower', ...}` | `"Urza's" in land.template.subtypes` |
| `DELIRIUM_CREATURES = {"Dragon's Rage Channeler"}` | `template.power_scales_with == "delirium"` (oracle-derived at load) |
| `TARMOGOYF_CREATURES`, `DOMAIN_POWER_CREATURES`, `GRAVEYARD_SCALING_CREATURES` | `template.power_scales_with` field |
| `if name == "Construct Token"` | `'artifact you control' in oracle` |
| `c.template.name == "Amulet of Vigor"` (2 places) | `_apply_untap_on_enter_triggers()` — oracle pattern |

### Generic engine patterns added (session 2)
| Pattern | Trigger | Effect |
|---------|---------|--------|
| `"whenever a permanent you control enters tapped, untap it"` | `_apply_untap_on_enter_triggers()` | Covers Amulet of Vigor and any future card |
| `"lands you control enter the battlefield untapped"` | `_apply_lands_enter_untapped()` | Covers Spelunking static |
| `"when this land enters, return a land you control to hand"` | `resolve_etb_from_oracle()` | Covers Gruul Turf, Simic Growth Chamber, all bounce lands |
| `"when this [enters], draw a card, then you may put a land from hand onto battlefield"` | `resolve_etb_from_oracle()` | Covers Spelunking ETB |
| `"whenever this creature or another [Subtype] you control enters"` + top-card effect | `trigger_etb()` | Covers Risen Reef, any future Elemental-chain card |
| `"whenever you cast a noncreature spell, you get {E}"` | `resolve_spell_cast_trigger()` | Energy on noncreature spell cast |
| `"if you have more energy than that player has life, create a 1/1 token"` | `_assign_combat_damage()` | Combat damage energy→token trigger |
