---
title: Micro-audit 2026-05-17 — 8 parallel mechanic-precise verifiers
status: active
priority: secondary
session: 2026-05-17
depends_on:
  - docs/history/audits/2026-05-16_5panel_bo3_audit.md
tags: [audit, post-merge, micro-verification]
summary: >
  8 small parallel auditors verified mechanic / CR fidelity across 5
  mechanically-distinct Bo3 matches (s70100-70500). 6 GO, 1 NO-GO
  data gap (fixed in-commit), 1 MIXED diagnostic.
---

# Micro-audit — 2026-05-17

Post-merge validation of PR #433 (the 2026-05-16 structural refactor).
Not a panel — eight lightweight read-only verifiers, each checking one
specific rule or AI invariant across five mechanically-distinct Bo3
replays. No win-rate analysis; pure rules + dispatch correctness.

## Corpus

5 Bo3 matches in `replays/micro_audit_2026-05-17/` exercising distinct
audit mechanics:

| # | Match (seed) | Mechanics |
|---|---|---|
| M1 | Ruby Storm vs Dimir Midrange (70100) | impulse-draw / Bowmasters / counter triage |
| M2 | Azorius Control vs Boros Energy (70200) | sweepers / planeswalker EV / race / chump |
| M3 | Goryo's Vengeance vs Living End (70300) | graveyard / reanimation / cascade / flashback |
| M4 | Amulet Titan vs Eldrazi Tron (70400) | bounce-land ETB / big mana |
| M5 | Affinity vs Jeskai Blink (70500) | improvise / Galvanic Discharge / Solitude evoke |

## Findings

| Auditor | Subject | Verdict |
|---|---|---|
| A1 | Impulse-draw vs real-draw fan-out (CR 121.1c) | ✅ GO across 5 matches |
| A2 | Land-ETB surveil triggers (R3) | ⚠️ NO-GO data gap → fixed in-commit |
| A3 | Damage routing (R2 + R6 + M10) | ✅ GO across 4 Galvanic Discharge casts, 4 Ral coin-flip losses, 1 burn-to-PW selection |
| A4 | Chain-aware counter triage (M2) | ⚠️ MIXED — see investigation note |
| A5 | Defender chump rule (M12) | ✅ GO across 9 lethal combat phases |
| A6 | Chain self-damage projection (M1-AI) | ✅ GO — 7 deaths, 0 self-kills (zero Bowmaster/Ral self-induced deaths) |
| A7 | Sorcery-speed lockout (R4) | ✅ GO — 2 Teferi-TR lifetimes, 0 violations |
| A8 | Discard imminence + planeswalker EV (M11 + M5) | ✅ PW EV (4/4 on-curve casts); discard finding inconclusive (samples not at PANIC threshold) |

## Detail — A2 (surveil triggers, now fixed)

**Before:** Meticulous Archive surveiled correctly on every ETB (4/4
plays across M2 + M5). Elegant Parlor and Thundering Falls did NOT
fire surveil despite identical oracle text.

**Root cause:** `decks/gameplans/_oracle_classifier.json` was missing 9
surveil-dual cycle entries (Elegant Parlor, Thundering Falls, Hedge
Maze, Lush Portico, Raucous Theater, Commercial District, Undercity
Sewers, Shadowy Backstreet, Underground Mortuary). The W1a-3 agent's
LLM build attempted to add them but the entries were lost during the
merge resolution. Meticulous Archive survived because it had been
classified in an earlier round.

**Fix:** Hand-classified the 9 cards with `ETB_SURVEIL_N` tag, marker
`_classification_source: "manual:a2-audit-data-gap-2026-05-17"`.
Oracle text is byte-identical to Meticulous Archive — unambiguous
classification with zero LLM call needed. Future prompt-v2 audit will
re-classify these and the marker makes them discoverable.

## Detail — A4 (counter triage, MIXED finding)

In M1 G1 T6, Dimir cast Spell Pierce on Wrenn's Resolve (chain-fuel).
In M1 G3 T3, Dimir cast Counterspell on Ral, Monsoon Mage (engine /
payoff) — correctly held through earlier chain fuel.

The G1 T6 case may be a real M2 gap: the auditor noted no
`chain-fuel hold` reasoning in the NDJSON for that decision. Two
possible interpretations:

1. **Real bug:** chain-state detection in `ai/combo_calc:bottleneck_
   probability` not gating Spell Pierce path.
2. **Defensible play:** Spell Pierce is a 1-mana counter; Wrenn's
   Resolve is a 2-mana cantrip; the EV math may legitimately favor
   fuel removal at low caster mana (lose 1, deny 2) versus holding
   for an uncertain payoff that may never arrive.

**Action:** documented; not blocking. Real-bug case needs a focused
unit test naming the scenario (Storm hand visible to Dimir, mid-chain
mana floated, Counterspell still in Dimir hand). Wave-2 follow-up.

## Detail — A8 (M11 discard imminence finding context)

The auditor rated 3/3 discard picks as "FLEX not imminent" but the
sampled cases were Goryo at 9 life vs Living End. M11's panic gate
triggers at `caster_life ≤ max(3, opp_one_turn_damage)`. Goryo at 9
vs a slow Living End board doesn't cross the threshold — so FLEX
behaviour is correct. The audit's discard-imminence fix only modifies
behaviour AT panic life.

Discard at panic life threshold isn't exercised in this 5-match
corpus. A targeted unit test pinning a `life ≤ 3, multi-attacker
board, imminent-attacker-in-hand` scenario is the right verification.
Not a regression.

## Status of the audit's 21 cures

Verified end-to-end in this micro-audit (post-#433):

| Cure | Status | Evidence |
|---|---|---|
| R1 impulse-draw split | ✅ confirmed | A1, A6 |
| R2 Galvanic Discharge | ✅ confirmed | A3 |
| R3 land-ETB surveil | ✅ confirmed after A2 fix | A2 + this commit |
| R4 Teferi-TR sorcery-speed | ✅ confirmed | A7 |
| R6 Ral coin-flip | ✅ confirmed | A3 (4 lose-flip events, all damage Ral not player) |
| M1-AI chain self-damage | ✅ confirmed | A6 (7 deaths, 0 self-kills) |
| M5 planeswalker EV | ✅ confirmed | A8 (4/4 on-curve casts) |
| M10 burn-target PW | ✅ confirmed | A3 (Teferi-TR killed over face at loyalty 3) |
| M12 defender chump | ✅ confirmed | A5 (9 lethal phases, all compliant) |
| M2 chain-aware counter | ⚠️ partial | A4 (1/2 correct; investigation pending) |
| M11 discard imminence | ⏸ unverified | corpus didn't hit panic-life threshold |

Other cures (M3, M4, M6, M7, M8, M9, M13, M14, M15, R5) not directly
exercised by these 5 matches; future audits with targeted scenarios
required.
