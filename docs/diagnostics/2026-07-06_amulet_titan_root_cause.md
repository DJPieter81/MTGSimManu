---
title: Amulet Titan 15.3% field — self-discard binned the payoff; conversion remains passive
status: active
priority: primary
session: 2026-07-06
depends_on: docs/diagnostics/2026-07-05_storm_overshoot_root_cause.md
tags: [amulet, discard, ramp, conversion, titan]
summary: >
  Replay-evidenced: end-of-turn self-discard treated the deck's 6-drop
  keystone as reanimation fuel and binned BOTH copies (fixed in this
  PR, gameplan-keystone protection). The aggregate stays ~11-15%
  because conversion is passive — Titan fetch selection has no
  haste-line pattern, attack prioritization never swings, and
  Scapeshift is cast with no payoff on board. Each named below with
  its subsystem; one fix per diagnosis.
---

# Evidence — `--bo3 "Amulet Titan" "Boros Energy" -s 50000` (merged main, 2026-07-06)

G1 timeline: T1 Vexing Bauble, T3 Amulet of Vigor, T4 land-go, then
**T5 Scapeshift for five tapped lands with no payoff on board,
followed by discarding BOTH Primeval Titans to hand size.** The deck's
only win conditions went to the graveyard of a deck with no graveyard
plan; the game was unwinnable from there.

## Fixed in this PR (single subsystem: `ai/discard_advisor.py`)

The self-discard scorer's generic fallback treats every creature at or
above `DISCARD_BIG_CREATURE_CMC_THRESHOLD` as a reanimation target
worth +80+cmc. For a deck whose gameplan declares that creature a
keystone (`critical_pieces` — Amulet declares Primeval Titan and
Cultivator Colossus) and declares NO graveyard `FILL_RESOURCE` plan,
that bonus discards the win condition ahead of spare lands.

Rule: a declared keystone in a no-graveyard-plan deck is the payoff
the deck exists to CAST — protect it (same `DISCARD_COMBO_TUTOR_PROTECT`
penalty the combo/tutor tags get). Reanimators are unaffected: the
GV-1 short-circuit (declared graveyard plan) still bins fat payoffs
first — pinned by test.

Class size: every ramp/toolbox deck with a declared expensive payoff x
every hand-size overflow. Test-first:
`tests/test_self_discard_protects_keystones_without_graveyard_plan.py`.

## Measured effect

Field n=8 same seeds: 11.0% (main) → 11.7% (fix) — flat. Keeping the
Titans stops the catastrophic games but the deck still doesn't convert
resolved Titans into wins. The aggregate gap lives in the subsystems
below; per loop-break, they are named here and NOT patched in this PR.

## Named remaining subsystems (one fix per future diagnosis)

1. **Titan fetch selection has no haste/aggro line** (engine ETB land
   search + AI land chooser): resolved Titans fetch value/karoo lands;
   the fetch never assembles a haste-granting line even when the race
   math wants damage now. Subsystem: the land-search chooser that
   serves "search your library for up to two land cards" — it needs a
   clock-aware mode (behind on race → fetch the line that converts
   the body into damage; ahead/stable → value lands). Cross-deck: any
   land-tutor creature/spell.
2. **Attack prioritization**: a 6/6 trampler sat home for multiple
   turns in G2 of the 2026-07-05 probe while the opponent raced.
   Likely the attack planner prices the Titan as a blocker without
   comparing racing lines; needs its own trace.
3. **Scapeshift enumeration lacks a payoff predicate** (`ai` scoring):
   cast for five tapped lands with zero landfall/payoff permanents and
   two uncastable Titans in hand — pure card disadvantage. The
   sacrifice-fetch class needs "net position improves" gating, similar
   to the X-wipe gate shape.
4. **Keep policy** accepted "3 lands, 1 castable spell" (2026-07-05
   probe G1) — mulligan subsystem; check overlap with the #461
   bottoming/keystone machinery before new code.
