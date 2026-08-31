---
title: Domain Zoo "obvious matchup" replay audit — one real bug found (unrelated pump text stacking on spell cast)
status: active
priority: primary
session: 2026-08-31
depends_on:
  - docs/diagnostics/2026-08-20_domain_zoo_overperformance_root_cause.md
  - docs/diagnostics/2026-08-30_zoo_decklist_hypothesis_falsified.md
supersedes: []
superseded_by: []
tags:
  - domain-zoo
  - izzet-prowess
  - affinity
  - boros-energy
  - prowess
  - delirium
  - dragons-rage-channeler
  - replay-audit
  - wr-outlier
summary: >
  Replay-based plausibility audit of three Domain Zoo cells (Affinity, Izzet
  Prowess — mandatory, Boros Energy) against a matrix snapshot handed to this
  session. Found and fixed a real, generic engine bug: CastManager's
  "prowess-like trigger" detector searched a creature's WHOLE oracle text for
  a "+N/+M" pattern once it saw the substring "noncreature spell"/"instant or
  sorcery" ANYWHERE in that text, so an unrelated static elsewhere (a
  delirium condition, an oil-counter static, an anthem) got re-applied as a
  stacking per-spell-cast pump. Dragon's Rage Channeler (Izzet Prowess) hit
  this every game — replay showed a 1/1 DRC displayed and fought as 7/7 off
  three unrelated spell casts, which flipped an entire Bo3 (2-0 Izzet ->
  2-0 Zoo at the same two seeds once fixed). 11 Modern creatures share the
  bug's shape (class size floor is 10). Fixed by anchoring the pump regex to
  the actual "whenever you cast ... spell" triggered-ability clause instead
  of the full oracle-text blob. Affinity and Boros Energy audits found no
  bugs — both are legitimate, competitive races at the seeds sampled, and
  neither reproduced the matrix snapshot's stated WR (100% / 40%) at the
  seeds this session used, which is recorded as an open discrepancy rather
  than chased further (this worktree's data files predate the fix and the
  matrix snapshot's generating run/seed range is not reproducible from here).
---

# Domain Zoo "obvious matchup" audit — replay-based (2026-08-31)

## Scope and what was handed to this session

A prior session's 25-deck n=20 Bo3 matrix reportedly showed Domain Zoo at
100% vs Affinity, 70% vs Izzet Prowess, and 40% vs Boros Energy (the
project's stated per-turn task). This worktree's own on-disk
`metagame_results.json` is a *different*, earlier snapshot
(2026-08-27T16:14, n=20) showing 90% / 80% / 80% for the same three cells —
neither the CLAUDE.md in this worktree nor `tools/` here shows the
turn-cap/loyalty-dispatch fixes or the seven-ratchet state the task
described as merged. This worktree is genuinely behind that further-along
session state; the numbers below are what THIS worktree's current engine
actually produces, established by direct replay rather than trusted from
either stale file.

Per CLAUDE.md's loop-break rule: replay first, name the subsystem, quote the
log line. All runs below used `MTG_LLM_DECISION_SCORER_OFFLINE=1` and were
taken with `cat /proc/loadavg` < 1.5 beforehand.

## 1. Domain Zoo vs Izzet Prowess — mandatory cell — BUG FOUND AND FIXED

`python run_meta.py --bo3 "Domain Zoo" "Izzet Prowess" -s 55555` (pre-fix):
**Izzet Prowess wins 2-0.** Both games end with a lethal alpha strike
featuring a wildly oversized Dragon's Rage Channeler:

```
T5 P2:   Dragon's Rage Channeler (7/7) → 7 dmg to player
```//Game 2, single attacker, from a 1/1 base

```
T5 P2: Cast Cori-Steel Cutter (1R)
...
T5 P1:   [BLOCK-EMERGENCY] Scion of Draco (4/4) blocks Dragon's Rage Channeler (7/7) — lifespan_delta=-3.86
T5 P2:   Slickshot Show-Off (7/2) → 7 dmg to player
T5 P2:   Monastery Swiftspear (4/5) → 4 dmg to player
```//Game 1

DRC's real oracle text: *"Whenever you cast a noncreature spell, surveil 1
... Delirium — As long as there are four or more card types among cards in
your graveyard, this creature gets +2/+2, has flying, and attacks each
combat if able."* Its own cast trigger has **no P/T component** — only the
Delirium static does, and that static is gated on 4+ graveyard card types
being true *right now*, not on how many spells were cast this turn.

### Root cause

`engine/cast_manager.py`'s generic "prowess-like trigger" detector
(`CastManager.cast_spell`, the "Prowess and prowess-like triggers" block)
gates on the substring `"noncreature spell"` or `"instant or sorcery"`
appearing **anywhere** in a creature's oracle text, then searches the
**entire oracle text** for a `gets? +N/+N` pattern to decide the pump. For
Slickshot Show-Off or Monastery Swiftspear that's correct — the pump text
IS part of the same trigger. For Dragon's Rage Channeler the substring match
comes from the surveil trigger, but the `+2/+2` match comes from the
unrelated Delirium clause later in the same oracle-text string, and the
whole-text scan can't tell the two apart. The delirium static is *also*
correctly implemented once, via `_dynamic_base_power()`/`has_delirium` — so
the bug is a **double application**: the correct delirium math (0 or +2/+2,
recalculated continuously) plus an incorrect **per-spell-cast** re-grant of
the same +2/+2 from the same text, accumulating in `temp_power_mod`. Verified
arithmetic against the replay: Game 1 T5 had 3 noncreature spells cast that
turn (Preordain, Preordain, Cori-Steel Cutter) with delirium NOT active (only
3 graveyard types) → 1 (base) + 3×2 (bug) = 7/7, exactly matching the log.
Game 2 T3 had 2 Lightning Bolts cast with delirium genuinely active (4
graveyard types via 2 fetch-lands + a surveilled artifact + a surveilled
creature) → 1 (base) + 2 (correct delirium) + 2×2 (bug) = 7/7, also exactly
matching.

### Class size — mechanic, not a DRC patch

Scripted a scan of `ModernAtomic.json` for creatures whose oracle text
contains the trigger substring but whose `+N/+M` match is **not** inside the
same `"whenever you cast ... (noncreature spell|instant or sorcery) ..."`
sentence. **11 creatures** hit this shape: Disciple of the Ring, Dragon's
Rage Channeler, Gastal Raider, Ichor Synthesizer, Mausoleum Wanderer, Naru
Meha Master Wizard, Raff Weatherlight Stalwart, Splashy Spellcaster,
Storm-Kiln Artist, Trawler Drake, Vodalian Hexcatcher — each has a real
"whenever you cast a noncreature/instant-or-sorcery spell" trigger whose OWN
effect is not a P/T buff (a counter, a token, a Role aura on a different
creature, a static condition), sharing text with an unrelated `+N/+N`
elsewhere. Exceeds CLAUDE.md's 10-card floor for "this is a mechanic, not a
card".

### Fix

Anchored the pump-amount regex to the actual triggered-ability clause (the
substring from `"whenever you cast"` up to the following sentence boundary
that also contains the trigger phrase) instead of the whole oracle text.
True positives (Slickshot Show-Off, Kiln Fiend, Adeliz, Crackling Cyclops,
Frenzied Devils, ...) are unaffected — their pump text lives inside that
same clause. All 11 false positives now correctly contribute `(0, 0)` from
this code path, leaving delirium/counter/anthem statics to the mechanism
that actually owns them.

**Test-first, rule-phrased** (`tests/test_prowess_trigger_fires_on_noncreature_spell_types.py`,
class `TestProwessLikeDetectorAnchorsToTheCastTriggerClause`):
- `test_unrelated_pump_text_elsewhere_in_oracle_is_not_applied_per_cast`
- `test_unrelated_pump_text_does_not_stack_across_multiple_casts` — reproduces
  the exact +6/+6-off-three-casts shape from the replay.

Both were red before the fix (`2 == 0`, `6 == 0`) and green after. Full
existing prowess suite (11 tests) stays green — no regression to the
legitimate prowess/magecraft/Opus cards.

### Verified end-to-end (replay, same seeds, post-fix)

```
T4 P2:   Dragon's Rage Channeler (1/1) → 1 dmg to player     # game 2, no delirium yet
...
Creatures: Dragon's Rage Channeler (3/3) [tapped], ...        # once delirium genuinely active
```

`python run_meta.py --bo3 "Domain Zoo" "Izzet Prowess" -s 55555` post-fix:
**Domain Zoo wins 2-0** — the same two seeds that gave Izzet Prowess a 2-0
before the fix. This is a large, real effect on this cell, not a cosmetic
display fix.

### Sanity check on the fix's blast radius

`grep`-verified DRC's own surveil trigger and other true-positive
prowess-variant creatures (Slickshot Show-Off `(2,0)`, Kiln Fiend `(3,0)`,
Frenzied Devils `(2,2)`, Crackling Cyclops `(3,0)`) all keep their correct
pump amounts under the new anchored regex; all 11 false positives yield
`(0,0)`.

## 2. Domain Zoo vs Affinity — no bug found; genuinely competitive

`python run_meta.py --bo3 "Domain Zoo" "Affinity" -s 55555`: **Domain Zoo
wins 2-1** (not a sweep) — Affinity mulliganed to 6 in the two games it
lost (a 1-lander with Mox Opal as the only "land-like" source, and a
5-spell hand with Kappa Cannoneer as its only playable curve piece — both
legitimate mulligans, not AI misplay), then won game 2 outright as P1 on a
clean 7. Game 2's loss for Zoo is a real race: Zoo alpha-struck to bring
Affinity to 4 life, tapping out both its attackers, and Affinity's Kappa
Cannoneer (pumped correctly, +1/+1 per nontoken-artifact ETB — verified as
legitimate CR-702-style card behavior, not the prowess-detector bug: Kappa
Cannoneer's own text is the trigger and the pump, no unrelated clause)
swung back for lethal into an empty board:

```
T8 P1:   Kappa Cannoneer (8/8) → 8 dmg to player
T8 P1:   Pinnacle Emissary (3/3) → 3 dmg to player
T8 P1:   Drone Token (1/1) → 1 dmg to player
P2 loses: life total -8
```

`python run_meta.py --matchup "Domain Zoo" "Affinity" -n 10 -s 50000`:
**70% Zoo / 30% Affinity** (3 sweeps, 7 to-three) — a competitive,
non-blowout aggro-mirror-shaped race, consistent with the replay evidence.
This session could not reproduce the handed-off matrix's stated 100% at any
seed sampled; recorded as an **open discrepancy** rather than chased
further, since (a) this worktree is confirmed behind the further-along
session state the task described, and (b) 70/30 at n=10 (this session) vs
100/0 at n=20 (the prior session) is not itself proof of a bug — it's within
plausible small-sample variance range for a genuinely-close matchup, but
warrants a re-check once this worktree is rebased onto the fixes the task
described. Kappa Cannoneer's ward also correctly countered two of Zoo's
removal spells in the sampled Bo3 (Wear // Tear, Leyline Binding) —
Affinity's actual defensive tool is working as printed, not inflating Zoo.

## 3. Domain Zoo vs Boros Energy — no bug found; Zoo is not obviously the
   loser at the seeds sampled (discrepancy noted, not a fix)

`python run_meta.py --bo3 "Domain Zoo" "Boros Energy" -s 55555`: **Domain
Zoo wins 2-1.** Boros Energy's one win (game 2) is a legitimate fast curve
even off a mulligan to 5 — dashed Ragavan T2 into Ajani, Nacatl Pariah T2,
still closing by turn 6. `python run_meta.py --matchup "Domain Zoo" "Boros
Energy" -n 10 -s 50000`: **80% Zoo / 20% Boros Energy** (6 sweeps).

The handed-off matrix stated Zoo at 40% here (the LOW end, i.e. Zoo losing
more than winning) — the opposite direction from both replay samples taken
this session. Checked Boros Energy's mainboard against the 11-card false-
positive list from the Izzet Prowess bug: **no overlap**, so this session's
fix does not explain the direction of the discrepancy for this cell. Same
verdict as the Affinity cell: recorded as an open discrepancy attributable
to this worktree being behind the further-along session state (turn-cap and
loyalty-dispatch fixes affect BOTH sides of every matchup differently, and
this worktree predates both), not investigated further per the "no more
Zoo-specific code without new replay evidence naming a mechanism" loop-break
— no mechanism was found on the Zoo side or the Boros side in either replay;
both games read as genuine, well-played aggro races.

## Verdict

One real, generic, well-scoped engine bug found and fixed (Izzet Prowess
cell). The Affinity and Boros Energy cells show no engine defect on
either side in the replays sampled — both are legitimate competitive
aggro races — but this session's own numbers for those two cells disagree
with the matrix snapshot handed to it, in both directions (Affinity: this
session's engine is LESS lopsided than reported; Boros Energy: this
session's engine favors Zoo where the snapshot reported Zoo as an
underdog). Next step for whoever picks this up: re-run the Affinity and
Boros Energy cells from a worktree that actually has the turn-cap and
loyalty-dispatch fixes merged, at the standard matrix seed (40000 range,
n=20) rather than the diagnostic seeds used here, before treating either
as settled.

## Files touched

- `engine/cast_manager.py` — anchored the prowess-like pump regex to the
  triggered-ability clause.
- `tests/test_prowess_trigger_fires_on_noncreature_spell_types.py` — two new
  tests, red-then-green.
