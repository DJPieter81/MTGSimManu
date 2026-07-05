---
title: Goryo's Vengeance 13.1% field — root cause chain (blink-exile rules bug + deferred discard)
status: active
priority: primary
session: 2026-07-05
depends_on:
  - docs/diagnostics/2026-04-28_goryos_combo_mana_mulligan.md
  - docs/diagnostics/2026-05-04_goryos_vengeance_audit.md
tags:
  - p0
  - wr-outlier
  - goryos
  - reanimation
  - blink
  - deferral-gate
  - forced-discard
summary: |
  Bo3 replay root cause for Goryo's chronic 13.1% field WR [band 30-70]
  and 0% vs Dimir [prior 35-50]. Four findings:
  RC-1 (engine, rules bug): a blinked permanent is NOT released from
  `game._end_of_turn_exiles` — Ephemerate cannot save a Goryo's-
  reanimated creature, so the deck's primary win line (reanimate +
  blink -> permanent fatty) does not exist in the sim (CR 400.7
  new-object rule). Fix ships in sibling PR #462; the remaining
  decision-layer gap (AI never attempts the blink line) is named
  here as the top follow-up. RC-2 (ai, oracle predicate — FIXED
  here): `_is_immediate_interaction` matches only 'target opponent'
  discard templating; Thoughtseize / Inquisition of Kozilek say
  'target player', so the deferral gate scored all 7 of the deck's
  disruption slots at -exposure forever. RC-3 (gameplan data,
  FALSIFIED as standalone lever): relaxing the typed mulligan combo
  paths to flat 2-of-3 sets moved WR 0/20 -> 0/20 vs Dimir and Boros,
  pre- AND post-blink-fix — do not re-run. RC-4 (engine policy —
  FIXED here): pay-life-draw re-activated into a full hand, paying
  14 life for cleanup discards. Outcome: field 9.7% -> 17.9%,
  Dimir 0% -> 5%, AzCon 27% -> 60%.
---

# Goryo's Vengeance 13.1% — root cause chain

## Reproduction (this branch, clean parts-1-8 DB, 2026-07-05)

- `run_meta.py --matchup goryos dimir -n 20` (seed 50000): **0/20**
  (15 sweeps, 5 to game 3). Goryo's game wins land on turns 8-13 —
  a reanimator whose combo should close T3-5 is winning only via
  late-game beatdown.
- `run_meta.py --field goryos -n 15`: **9.7%** avg (0% vs Boros,
  Affinity, E-Tron, Zoo, Prowess, Dimir, Pinnacle, Instant
  Reanimator, Ponza; 40% vs Ruby Storm / Living End).
- Replays committed: `replays/goryos_vs_dimir_s50000.txt`,
  `replays/goryos_vs_boros_s50500.txt`.

## RC-1 — Engine rules bug: delayed end-of-turn exile survives blink

**Subsystem:** `engine/spell_resolution.py::ResolutionManager._blink_permanent`
(lines 594-604) + `engine/turn_manager.py::end_of_turn_cleanup`
(lines 211-220) + `engine/permanent_effects.py:59` (trigger registration).

**Replay cite:** `replays/goryos_vs_boros_s50500.txt`, game 1, turn 5:

```
T5 P1: Cast Goryo's Vengeance (1B)
T5 P1: Reanimate Griselbrand
...
T5 P1: Cast Ephemerate (W)
T5: Blink Griselbrand
T5: Griselbrand moved battlefield -> exile (Goryo's end-of-turn exile)
T5: Griselbrand exiled (end of turn)
```

The AI played the archetype's textbook line — reanimate, swing,
blink with Ephemerate to shed the exile clause — and the engine
exiled the creature anyway.

**Rule:** CR 400.7 — a permanent that changes zones becomes a new
object. Goryo's Vengeance's delayed trigger ("Exile it at the
beginning of the next end step") tracks the specific object it
returned; after Ephemerate exiles and returns the creature, the
delayed trigger no longer applies. This interaction is the entire
reason the Ephemerate package exists in real Goryo's / Esper
Reanimator lists: blink converts a one-turn 7/7 rental into a
permanent Griselbrand/Atraxa.

**Engine defect:** `_blink_permanent` removes and re-appends the SAME
`CardInstance` and never touches `game._end_of_turn_exiles`; the
cleanup loop's only guard is `if card.zone == "battlefield"`, which
is true again after the blink, so the exile fires.

**Consequence for the WR:** every reanimation in the sim is a
one-turn haste rental (7 damage max), so the deck needs ~3 resolved
Vengeances to win instead of 1 Vengeance + 1 Ephemerate. This is why
Goryo's only wins on turns 8-13, why 4 MB Ephemerate + 2 Undying
Evil are near-dead slots, and why the deck loses to every clock in
the field. Class size: every delayed-exile effect (Goryo's
Vengeance, sneak/breach-style effects) x every blink effect
(Ephemerate, Phelia, Flickerwisp, Teleportation-class) — mechanic,
not card.

**Sibling beneficiary:** Instant Reanimator (4.88% meta share, 0%
matchup vs Goryo's in the July matrix) is BUILT on this exact
interaction — its decklist comment reads "cheat Atraxa/Griselbrand
into play with Goryo's Vengeance, keep it with Ephemerate". The fix
lifts both decks through one mechanism.

**Fix ownership (coordination update, same session):** sibling
Track D ships this exact fix in **PR #462** (branch
`claude/eot-exile-object-identity`, stacked on the same base):
delayed EOT-exile riders drop when the tracked object changes zones
(`engine/cards.py` battlefield_entry_seq +
`game_state.register_end_of_turn_exile` + turn_manager staleness
check). A locally-validated duplicate implementation (zone-manager
purge + blink purge, red->green under a mechanic-named test) was
REMOVED from this track's branch to avoid a two-PR collision on the
same mechanism — this PR depends on #462 for RC-1.

**Follow-on finding (verified on this branch with #462
cherry-picked):** the engine fix alone produces NO WR delta —
`--bo3 goryos boros -s 50500` contains ZERO `Blink` lines with the
fix applied, and the field sweep is flat (17.1% with vs 17.9%
without, same seeds, n=15 noise). Track D independently measured
the same (seeds 50000/50500/51000, no blink lines, 0%->0% vs
Boros). This track's PRE-fix replay shows the line CAN fire
(`goryos_vs_boros_s50500.txt` G1 T5: Cast Ephemerate -> Blink
Griselbrand) — but only when Ephemerate survives to hand with W
open, which the frequent mull-to-5 rarely allows. So RC-1 has two
layers: the RULES layer (#462) and a DECISION layer — Ephemerate is
`reactive_only` + `protection`-role, and no scoring term values
"blink my own reanimated body to shed the pending exile rider"
(`ai/ev_player.py` enumerates it reactively only). The
decision-layer gap is documented here, not fixed: it needs a
generic "blink-removes-pending-detriment" EV term in the
reanimation/blink scoring subsystem; no open PR owns it yet. This
is the highest-leverage remaining Goryo's work item once #462
merges.

## RC-2 — AI deferral gate: 'target player' forced discard never fires

**Subsystem:** `ai/ev_evaluator.py::_is_immediate_interaction`
(line 868) consumed by `_enumerate_this_turn_signals` (signal 4)
consumed by the deferral gate in `compute_play_ev` (line 2884).

**Trace cite:** `--trace goryos dimir -s 50000`: T3, T5, T7, T9,
T11 all show `cast_spell: Thoughtseize -0.1 ... >>> PASS` with 2+
mana open and 0 board pressure. Goryo's ended game 1 having cast
ZERO spells while holding double Thoughtseize from turn 5 on. In
game 2 (seed 50001) Dimir's Counterspell — which a T1-3 Thoughtseize
strips in real play — sat safely in hand and countered the turn-5
Goryo's Vengeance.

**Defect:** the oracle fallback for forced discard is

```python
if 'target opponent' in oracle and 'discard' in oracle:   # line 868
```

Thoughtseize and Inquisition of Kozilek are templated "**Target
player** reveals their hand ... discards that card" — the predicate
returns False, no `immediate_interaction` signal fires, and the
deferral gate scores the spell at `-exposure` (-0.1) every turn
forever. Verified empirically: `_is_immediate_interaction` returns
True for Duress ("target opponent ...") and False for Thoughtseize /
IoK, while the oracle classifier's `Tag.FORCED_DISCARD` (used by the
cast-time projection at line 2308) already returns True for all
three — the projection knows the mechanic; the deferral predicate
does not.

**Monkeypatch experiment (no repo change):** adding
`'reveals their hand' in oracle and 'discard' in oracle` to the
predicate makes Thoughtseize cast on T3/T5 in the seed-50000 trace
and moves goryos-dimir n=20 from 0W/0D to 1W/1D. Directionally
correct, small on its own — the matchup stays capped until RC-1
lands (both players cast their discard; the combo still cannot
stick a threat).

**Class size:** every "target player ... discards" printing (~40+
Modern cards: Thoughtseize, IoK, Wrench Mind, Funeral Charm,
Raven's Crime, ...). Beneficiary decks in the current field:
Goryo's (7 MB slots), Instant Reanimator (3 MB Thoughtseize),
Dimir Midrange (4 MB Thoughtseize — also never cast them).

**Fix shape (this PR):** broaden the oracle fallback to the
mechanic ("target player" OR "target opponent", plus the
reveal-and-choose form). Failing test first:
`tests/test_forced_discard_is_immediate_interaction.py` —
rule-phrased: "targeted forced discard is immediate interaction
regardless of target-player vs target-opponent templating".

## RC-3 — Gameplan mulligan strictness: TESTED, NOT a standalone lever

The typed `mulligan_combo_paths` (single enabler bucket = 4x
Faithful Mending, required at virtual size 7 AND 6) produce a
mull-to-5 in 3 of the 4 replay games (P(keep 7) ~ 0.26 by
hypergeometric on 4 enablers + 8 payoffs). Hypothesis: relaxing to
the flat 2-of-3 `mulligan_combo_sets` rule lifts WR.

**Experiment (2026-07-05, this branch):** removed
`mulligan_combo_paths` from the gameplan JSON (falls back to flat
2-of-3 sets):

| Config | vs Dimir n=20 | vs Boros n=20 |
|---|---|---|
| baseline | 0W 0D | (0/20 in July matrix) |
| flat sets only | 0W 0D | — |
| flat sets + RC-2 patch | 0W 0D | 0W 0D |
| RC-2 patch only | **1W 1D** | — |

Relaxation is neutral-to-NEGATIVE (the extra speculative keeps are
non-functional without RC-1: a kept payoff hand still cannot stick
a threat). **Falsified as a standalone lever — do not re-run** the
"loosen Goryo's mulligan" experiment until after RC-1 is merged;
re-evaluate then.

## RC-4 — Pay-life-draw suicide loop (fixed here: hand-cap gate)

`engine/game_runner.py::_activate_pay_life_draw` (line 1501)
allowed pay-life-draw re-activation up to a 14-card hand: in
`goryos_vs_boros_s50500` G1 T5 it paid 14 life (double draw-7)
into a discard-10-at-cleanup, dying next turn to a 10-power board.
The second activation is a pure life loss by rule (CR 514.1 — the
excess cards are discarded at cleanup). **Fixed in this PR** with a
narrow rules-derived gate: the activation loop's hand bound is
`MAX_HAND_SIZE` (first dig from a small hand still fires; re-
activation on a full hand does not). Failing test first:
`tests/test_pay_life_draw_respects_hand_cap.py` (2 red -> green).
Class: every repeatable pay-life-draw ability via the
`pay_life_draw` tag. The broader layering violation (strategic
policy in the engine layer) remains flagged for a future
lift-to-AI refactor; no open PR owns this region.

## Outcome (2026-07-05, this branch at ship state)

Same seeds as the reproduction (field n=15 from 50000; dimir n=20):

| Measurement | Before | After (this PR) | After + #462 cherry-picked |
|---|---|---|---|
| Field avg (n=15) | 9.7% | **17.9%** | 17.1% (flat — see RC-1 decision layer) |
| vs Dimir (n=20) | 0% | **5%** (1W 1D) | 5% |
| vs Azorius Control | 27% | **60%** | 60% |
| vs 4/5c Control | 7% | **47%** | 47% |
| vs Jeskai Blink | 13% | **33%** | 33% |
| vs Boros / Affinity / Prowess / Pinnacle | 0% | 0% | 0% |

The gain concentrates where it should: slower matchups where casting
7 mainboard discard spells and not halving your own life matter.
The fast-aggro cells stay pinned at 0% because the deck still cannot
keep a reanimated body (RC-1 decision layer) and still mulls to 5 in
most games (typed-path strictness is data-honest given only 4
self-discard outlets — a decklist-construction question, audit item
B-2, out of scope here). Target was >=25% field; 17.9% is honest —
the remaining lever is named above, not re-run into the ground
(loop-break respected: RC-3 falsified twice, halted).

## Prior-doc status check (step-1 protocol)

- 2026-04-28 doc: bugs #1/#3/#4 (color-sound mulligan, 6-card
  escape, >=2-of-3 predicate) all LANDED and still in effect —
  observed firing correctly in both replays.
- 2026-05-04 audit: B-1 (+4 Atraxa) LANDED (list has 4 Atraxa, 0
  Solitude MB). Class C ("disruption never fires") was diagnosed as
  goal-fallback but is actually RC-2 above — the deferral gate, not
  the GoalEngine, is the responsible subsystem (the trace shows the
  spell enumerated and scored, then passed on value, not skipped by
  goal selection). E-1 / B-2 remain open. The gameplan JSON still
  references Solitude (cut from the list) and omits Atraxa and
  Inquisition of Kozilek — cleaned up in this PR's gameplan commit.
- Falsified docs scanned (`grep -l 'status: falsified' docs/`):
  none in the Goryo/reanimation domain before this doc's RC-3.
