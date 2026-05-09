---
title: "Phase 1+2 post-mortem — Affinity residual after 6 rules-correctness fixes"
status: active
priority: primary
session: 2026-05-09
supersedes: docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md
tags: [diagnostic, affinity, post-mortem, loop-break]
summary: >
  Phase 1+2 shipped 6 rules-correctness fixes (parse_cost_reduction
  false-positive, artifact-land synergy hand-side double-count,
  Construct/Germ token type lines, Construct virtual_power double-
  credit, Saga III tutor AI callback, sideboard anti-artifact
  oracle-driven categorization, equipment-aware blocking regression
  anchors). Affinity moved from 84.3% → 82.7% flat WR (-1.6pp).
  The residual is structural, not heuristic. This doc names the
  driver subsystem and triggers the loop-break protocol per CLAUDE.md.
---

# Phase 1+2 Post-Mortem — Affinity at 82.7%

## TL;DR

Six principled rules-correctness fixes shipped (PR #304). Each was
genuinely a bug. Each closed a specific test fixture. Aggregate WR
movement on the canonical Bo3 matrix (n=20):

| Deck | Pre-fix flat | Post-fix flat | Δ |
|---|---|---|---|
| Affinity | 84.3% | 82.7% | **-1.6pp** |
| Affinity weighted | 80.4% | 78.3% | -2.1pp |
| Pinnacle Affinity | (similar movement, smaller magnitude) | 64.0% | — |
| Eldrazi Tron | (likely benefited from Trinisphere FP correction) | 70.3% | — |
| Boros Energy | (gained from Phlage FP correction + Phase 2A SB hate) | 69.0% | — |

Plan target: Affinity in **50–65% band**. Gap remaining: **18–33pp**.
The heuristic ratchet has not closed it.

## What Phase 1+2 actually fixed

Each commit was a real rules-correctness gain. The Class A oracle-
parse bug (parse_cost_reduction false-positive on 'colorless' /
'mana cost {N}') affected 554 cards across the DB and 20 cards in
our 16 decks; under the bug, Saga gave every Affinity spell a -1
discount, but Phlage gave every Boros spell the same discount, and
Trinisphere gave every Eldrazi Tron spell the same discount.
Removing it *cancelled out* across most matchups.

| Phase | Bug | Affinity-side cost speed-up removed | Boros-side speed-up removed |
|---|---|---|---|
| 1A | parse_cost_reduction FP | -1 on every Affinity spell from Saga | -1 on every Boros spell from Phlage |
| 1B | synergy hand double-count | +20 EV per artifact-land play | (no impact) |
| 1C | Construct/Germ types | tokens count for metalcraft + Plating | (no impact) |
| 1C | virtual_power double credit | Construct overrated by ~3 power | (no impact) |
| 1D | Saga III tutor callback | engine-layer hardcoding eliminated | (no impact) |
| 2A | SB anti-artifact category | Damping Sphere now boards in vs Aff | (boards in for opp) |
| 2B | block-eval equipment regression | (anchor only, no code change) | (anchor only) |

The bug-cancellation pattern is why aggregate Affinity WR moved
only 1.6pp despite multiple Affinity-side fixes.

## What Phase 1+2 did NOT touch

The structural driver of Affinity overperformance is the
**combination of low-cost artifacts + artifact lands + scaling
effects**, none of which is a bug:

1. **Artifact land density**. Affinity runs 18+ artifact lands
   (Darksteel Citadel, Mistvault Bridge, Silverbluff Bridge,
   Razortide Bridge, Spire of Industry — itself an artifact land
   in this build). Each contributes to:
   - Mox Opal metalcraft (3+ artifacts threshold).
   - Cranial Plating's "+1/+0 for each artifact you control"
     scaling.
   - Affinity-keyword discount on Frogmite, Thought Monitor,
     Sojourner's Companion, Myr Enforcer.
   - The Construct token's self-referential "+1/+1 for each
     artifact you control."
   Eleven of these scaling effects all read the same artifact
   count; the deck is a closed feedback loop where each card
   makes the next more powerful.

2. **Zero-cost artifacts**. Memnite, Ornithopter, Mox Opal,
   Mishra's Bauble, Welding Sparks (proxy) all cost 0. Affinity
   T1 routinely deploys Saga + Memnite + Mox = 3 artifacts before
   the opponent has untapped a single mana source. By T2, with a
   second land + Springleaf Drum + a free creature, Affinity has
   5–6 artifacts and can cast Frogmite for free.

3. **Cranial Plating's compound scaling**. At 5 artifacts, equipped
   Memnite becomes 6/1. At 7 artifacts, 8/1. At 10, 11/1. With
   evasion via Inkmoth Nexus or Ornithopter, this is uncounterable
   damage in 2 attack steps. Removal targets the carrier; Plating
   rebinds to the next creature for {1}.

4. **Bo3 sideboard hate is anti-Tron, not anti-Affinity**. Damping
   Sphere's primary clause ("if a land is tapped for two or more
   mana, it produces {C} instead") taxes Tron, not Affinity (whose
   lands all produce 1 mana). Its secondary clause ("Each spell
   costs an additional {1} for each other spell you've cast this
   turn") DOES tax Affinity's multi-spell turns — but it lands too
   late (T2-T3) to disrupt the early-clock setup.

## Loop-break protocol triggered

Per CLAUDE.md:

> "If three consecutive commits target the same outlier deck without
> moving the win rate toward its expected band: **halt**. Run
> ``run_meta.py --bo3`` against the worst matchup, identify the
> exact turn where EV diverges from correct play, name the
> responsible subsystem in writing in ``docs/`` (with frontmatter
> ``status: active, priority: primary``). No further code until that
> document exists."

We moved 1.6pp toward the band, so the strict trigger ("without
moving") doesn't fire — but six commits with a 2pp aggregate is the
spirit of the trap. **This document is the loop-break record.**

## Bo3 trace evidence — Affinity vs Izzet Prowess (s=50000)

Replay file: `replays/affinity_vs_izzet_post_phase12_s50000.txt`

Game 1 turn-by-turn (Affinity on the play):

| Turn | Affinity action | Izzet response |
|---|---|---|
| T1 | Play Saga + Mox Opal | Play Wooded Foothills + Bauble |
| T2 | Play Darksteel Citadel + Cycle Sojourner's (tutor land) | Play Steam Vents + Cast DRC |
| T3 | Play Silverbluff Bridge + Cast Cranial Plating ({2}) | Bolt + Lava Dart + Swiftspear, attack DRC |
| T4 | Saga III tutor + Spire of Industry + Memnite (0) | Fiery Islet + Preordain |
| T5 | Replay Saga + attack Construct + Memnite | Play Arid Mesa + Swiftspear attack |
| T6 | Cast another Plating + attack | (game continues) |

Affinity's tempo is **unchanged from pre-fix**. Plating cast on T3
is now {2} (was {1} pre-fix), but Affinity's clock is 2 attacks ×
6+ damage = lethal in T5-T6 regardless. The 1-turn cost-tempo
delta from Phase 1A doesn't translate to clock difference because
Affinity's bottleneck is ARTIFACT COUNT, not mana.

## Subsystem responsible

**The structural driver is `engine/cards.py:_dynamic_base_power`'s
artifact-count read on a deck designed around maximizing that
count.** No single fix on the engine or AI side closes the gap.
The remaining levers are:

1. **Decklist constraint** — modify Affinity's decklist (fewer
   zero-cost artifacts, fewer artifact lands). Out of scope —
   the deck is canonical Modern.
2. **Opponent decklists** — add maindeck artifact hate to T1
   opponents (PR #288 already did this; Phase 3 was supposed to
   evaluate revert but Affinity is far from band).
3. **Sideboard tactics** — better SB decisions (currently keyword-
   based even after Phase 2A; oracle-tag templates per Phase 2C
   would be more comprehensive).
4. **Decision-search depth** — opponents currently react with
   single-ply scoring; ISMCTS (Phase 4A) would evaluate "remove
   the carrier vs the equipment vs the mana source" as a
   sequence, exposing the correct play.
5. **Learned policies** — the SLM advisor (Phase 4C) trained on
   tournament SB guides would correctly bring in Damping Sphere,
   Force of Vigor, Boseiju, AND understand that Affinity's
   tempo bottleneck is the equipment swap, not the artifact base.

## Recommendation

**Stop adding heuristic patches to Affinity.** The 6 fixes shipped
were correct as rules-fixes but the WR isn't responsive at this
margin. Two paths forward:

### Path A — Decklist re-evaluation (data-only, fast, lower ceiling)

Revisit the **PR #288 maindeck artifact hate** addition. If it's
still adding ~10pp of artificial Affinity-suppression, removing it
would push Affinity ABOVE 82.7%, but the absolute number is less
important than the rule that opponents in the matrix have realistic
Modern decklists. Action: A/B test PR #288 revert with golden Bo3
fixtures (no full matrix).

### Path B — Decision-architecture upgrade (slower, higher ceiling)

Execute Phase 4A (ISMCTS) and Phase 4C (SLM oracle parser /
sideboard advisor) per the scoping docs in
`docs/research/2026-05_phase_4a_ismcts_scoping.md` and
`docs/research/2026-05_phase_4c_slm_scoping.md`. Both are scoped
for Q3 2026 parallel execution; both have golden-fixture
acceptance gates so neither blocks on a matrix run.

The SLM sideboard advisor specifically would address the
"Damping Sphere only catches Tron" insight: a learned model
trained on tournament SB guides knows which hate cards Modern
players actually bring vs Affinity (Wear // Tear, Force of Vigor,
Hurkyl's Recall — all currently classified correctly post-Phase
2A but still under-deployed because the legacy SB manager's
swap-budget is capped at 7 against a deck with 18+ artifacts).

### Recommended sequence

1. **Now**: this doc lands as the loop-break record. No more
   Affinity-targeted heuristic commits in this session.
2. **Q3 2026**: Phase 4A ISMCTS pilot. Acceptance gate
   includes "Affinity matchups improve from opponent's
   side ≥ 5pp on golden fixtures."
3. **Q3 2026** (parallel): Phase 4C SLM sideboard advisor pilot.
   Acceptance gate includes "vs Affinity, the advisor's plan
   matches a community-curated 'good plan' on ≥ 70% of swaps."
4. **Q4 2026**: re-run matrix; if Affinity in band, file
   PROJECT_STATUS.md update; if not, revisit decklist
   constraints (Path A).

## What this document supersedes

`docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md` —
the Phase L audit. Its Class E (artifact-count includes lands)
and Class A (oracle-misread) findings are now closed by Phase
1A/1B/1C. Its Class F (T1 Plating-for-1-mana) was real but
caused by parse_cost_reduction, not Mox-Opal-not-tappable as
the audit hypothesized.

## Frontmatter discovery

Future sessions: read this doc before any Affinity-related
heuristic patch. If the proposed patch fits the "rules-correctness
fix" pattern but the predicted WR delta is < 3pp, ship it for
correctness only — don't expect WR movement. If the predicted
delta is ≥ 5pp, validate the prediction with a Bo3 trace BEFORE
writing code; the heuristic surface is saturated for Affinity.
