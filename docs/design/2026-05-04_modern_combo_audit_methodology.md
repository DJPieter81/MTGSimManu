---
title: Combo deck audit methodology — Modern adaptation of Legacy lessons #29 + #30
status: active
priority: primary
session: 2026-05-04
depends_on:
  - PROJECT_STATUS.md  # §7 underperforming decks
  - docs/proposals/2026-05-03_p0_p1_backlog.md
tags:
  - audit
  - combo
  - methodology
  - cross-project-sync
supersedes: []
summary: >
  Adapts Legacy's 9-class combo-deck-audit checklist (lessons #29 + #30, PRs
  #111-113 in MTGSimClaude) to MTGSimManu. Bug taxonomy is closed under Magic;
  Legacy's audit lifted matchup WRs by 12-27pp from single-line fixes that
  triaged through these classes. **Apply this BEFORE assuming an outlier is an
  AI-scoring bug** (Phase J / Wave 2 P1 batch is paused pending this audit).
---

# Combo-deck audit methodology — Modern

## Caveat (added 2026-05-04 post-discussion): Class H is largely a Bo1-framing artifact

**Class H ("Bo1 hate-card density") was tagged as a methodology class while
the simulator defaulted to Bo1.** As of 2026-05-04 the canonical match
format is **Bo3 with sideboarding** (`run_meta.py` defaults are flipped;
see `CLAUDE.md` → "Match format: Bo3 by default (canonical)" and the
2026-05-04 user directive: *"many people sideboard against artifacts.
so we should rely on g1 stats, should always be bo3. we should note
this throughout"*).

Real Modern players carry matchup hate in their **sideboard**, not their
mainboard. Under Bo3, Class H findings should be reinterpreted:

1. **Sideboard density audit (NOT mainboard).** The right question is
   whether the deck's SB carries 2-3 of the relevant hate card and
   whether `engine/sideboard_manager.py` brings them in for the
   matchup. Bumping a 2-of in the **mainboard** to a 3-of as a
   "Class H fix" distorts the deck's mainboard evaluation toward a
   particular matchup at the cost of every other matchup — that is
   an **anti-pattern** under the canonical Bo3 framework.

2. **Class H "fixes" via mainboard edits are a specific anti-pattern.**
   They are a symptom of treating Bo1 framing as ground truth.

**Phase K's Class H batch (PR #288)** — adding +1 mainboard artifact
hate to **Boros Energy / Eldrazi Tron / Domain Zoo / Living End** —
was responding to the Bo1-framing artifact that inflated Affinity to
~84% sim WR. Under Bo3 those mainboard edits don't help (the SB
already covers them) and are a **candidate for revert** if Bo3
verification shows no benefit. The 4 affected decks and the specific
edits are tracked in `PROJECT_STATUS.md` §7 footnote.

The portion of Class H that **survives the Bo3 reframe** is
**inverted-H on Affinity** — under Bo3 the SB does answer Affinity, but
the sim should also confirm the SB's `sideboard_manager.py` rules
trigger the swap. That is a sideboard-logic audit, not a decklist
audit.

When a future deck appears to have a Class H finding, default to:
- Question 8 below now reads "Bo3 sideboard density," not "Bo1
  mainboard density." The 3-of minimum applies to the **sideboard**
  for the matchup-hinge card.
- If the deck-list edit is in the mainboard, it is almost certainly
  the wrong fix.

The other 8 classes (A, B, C, D, E, F, G, I) are unaffected by the
match-format change.

## Provenance

Legacy (`MTGSimClaude`) ran a three-round combo-deck audit on 2026-05-03 that
found **9 bugs across 6 classes** in 3 rounds. The taxonomy is the bug surface
of any Magic simulator. This doc adapts it to Modern. Original writeup:
`MTGSimClaude/docs/lessons/2026-05-03_combo_deck_audit.md` (PRs #111-#113).

## Bug taxonomy (closed under Magic)

| Class | Where it lives | Failure mode |
|---|---|---|
| **A. Card data** | `engine/card_database.py` / `ModernAtomic.json` parsing | CMC, mana cost, types, oracle keywords don't match the printed card. AI gates lie. |
| **B. Deck construction** | `decks/lists/*.txt` and `decks/modern_meta.py` | Tier-1 staples missing or undercounted. Strategy works but deck never assembles. |
| **C. Strategy/preamble interaction** | `engine/game_runner.py` `_execute_turn`, `ai/turn_planner.py`, `ai/gameplan.py` | Shared turn-step consumes resources the strategy needs for its combo line (Thoughtseize fires before Storm has T1 ritual mana, etc.). |
| **D. Off-by-one in deck-name gate** | `ai/strategy_profile.py`, `decks/gameplans/*.json` | Gate names one deck where it should name a *class* of decks. |
| **E. Heuristic over/under-counting** | `ai/bhi.py`, `ai/ev_evaluator.py`, scoring layer | Heuristic fires on wrong cardinality (cycling counted as missed cast, graveyard-growth proxy miscounts bypass channels). |
| **F. Rule violation** | `engine/card_effects.py` win-condition handlers | Sim's win condition is looser than the real card text (`≤` vs strict `<`, `at least N` vs `exactly N`). |
| **G. Fetch validity** *(round-3)* | `decks/lists/*.txt` | Fetchland targets a basic-type that the deck doesn't run → fetch is pure life-loss. |
| **H. Bo1 hate-card density** *(round-3 — largely Bo1-artifact, see caveat above)* | `decks/lists/*.txt` mainboard counts (LEGACY framing) / SB counts + `engine/sideboard_manager.py` (Bo3 framing) | Under the legacy Bo1 default, mainboard count was too low for the sim. **Under canonical Bo3 (2026-05-04 onward), this class folds into "is the SB carrying 2-3 of the matchup-hinge card AND does sideboard_manager bring them in?"** Mainboard edits are an anti-pattern under Bo3. |
| **I. Hand-rolled cantrip resolution** *(round-2)* | `decks/*.py` (Modern doesn't use this pattern, but check) | `player.draw(1)` instead of canonical `resolve_cantrip(...)` halves the dig (Brainstorm draws 3, puts 2 back; `draw(1)` only sees 1). Modern primarily routes cards through `engine/card_effects.py` so this should be rare — but verify. |

## Diagnostic workflow (per-deck)

1. Rank matchups by `|sim_wr − expected_wr|` from `metagame_data.jsx`.
2. Generate 5–10 deep traces of the worst with `python run_meta.py --trace <deck> <opponent> -s 42000 +1000 +2000 ...`.
3. Read every line.
4. Break down conditional WR by turn — discontinuities in the cast-turn → win-rate curve point at the bug.
5. Classify each finding into A–I above.

## Modern audit targets (priority order)

Per `PROJECT_STATUS.md §7` and `docs/proposals/2026-05-03_p0_p1_backlog.md`:

| Deck | Sim WR | Expected | |Δ| | Suspected classes |
|---|---|---|---|---|
| Goryo's Vengeance | ~10% | ~45% | 35pp | A (Atraxa CMC), B (combo enablers), C (T1-T2 mana for Goryo's line) |
| Amulet Titan | ~23% | ~45% | 22pp | G (fetch validity for bounce-land suite), B (Pact of Negation count?), C (T1 Amulet ramp consumed) |
| Ruby Storm | ~20% on outlier seeds | ~45% | up to 25pp | C (T1 ritual mana), H (opponent's Mystical Dispute density), A (Wish CMC?) |
| Living End | 38–53% (variance) | ~50% | up to 12pp | C (cycling under shared step), E (cycling counted as missed cast) |
| Affinity (overperformance) | 84.9% (Bo1-distorted) | ~55% | 30pp (estimated 15-25pp comes from Bo1 framing alone — re-measure under canonical Bo3) | **Inverted H, but largely a Bo1-framing artifact.** Real Modern players carry artifact hate in their SB. The fix is on the SB / sideboarding logic, not the MB; under Bo3 most of the gap should already close without code or list changes. |

## Per-deck audit checklist

For each target deck, run all 9 questions in order. Each "no" answer is a fix candidate. Single-line fixes per Legacy's experience routinely deliver 12-27pp WR lifts.

1. **Card data:** for every key combo card, does `engine/card_database.py` extract `cmc` and `mana_cost` matching `ModernAtomic.json`'s printed values? Add a regression test naming the card (still allowed under ABSTRACTION CONTRACT — tests are the sanctioned home for card-name knowledge).
2. **Tier-1 conformance:** does the decklist match a current MTGgoldfish/mtgtop8 top-8 list at the 4-of staple slots? Regression test: `assert mainboard.get('<key card>', 0) >= <expected>`.
3. **Strategy/preamble:** does any shared `_execute_turn` step or AI-side decision (`turn_planner` block-prediction, BHI inference) consume mana the gameplan reserved for the combo line on T1-T2?
4. **Single-deck gates:** `grep -rn "active_deck ==\|deck_name ==\|deck in (" ai/ engine/ decks/ --include='*.py'`. Each hit controlling a *mechanic* (combo-land priority, fast-mana priority, color-fix preference) should be tag/oracle-driven, not name-driven (per CLAUDE.md ABSTRACTION CONTRACT — this overlaps with our existing card-name ratchet but for deck-name gates).
5. **Heuristic cardinality:** does `ai/bhi.py` or `ai/ev_evaluator.py` use any "graveyard growth" / "cards-cast count" proxy? Trace the bypass channels (cycling, evoke, exile-from-hand, suspend) explicitly.
6. **Rule strictness:** re-read each win-condition card's oracle text. Strict `<` vs loose `≤` (Helix Pinnacle, Lab Maniac, Thassa's Oracle, Approach of the Second Sun) — does `engine/card_effects.py` match the printed text exactly?
7. **Fetch validity:** for every fetchland in `decks/lists/<deck>.txt`, count basics with matching subtypes. If zero, the fetch is pure life-loss — replace with a basic that matches its targets. Legacy: +9.5pp on Eldrazi vs Burn after replacing 4 invalid Countrysides with basic Wastes.
8. **Sideboard density (formerly "Bo1 hate-card density"; reframed 2026-05-04):** under the canonical Bo3 framework, count the matchup-specific hate card in the deck's **sideboard** and verify `engine/sideboard_manager.py` brings them in for the matchup. **3-of in the SB** is the minimum for a matchup-hinge card. **Do NOT bump mainboard counts as a "Class H fix"** — that is an anti-pattern under Bo3 (it distorts every other matchup to chase one). The Legacy +10pp on `wst vs burn` after Sanctifier en-Vec 2→3 was discovered under Bo1 framing; re-verify under Bo3 before generalising. Phase K's PR #288 mainboard hate edits to Boros / ETron / Zoo / Living End are tracked as a Bo1-artifact candidate-for-revert in `PROJECT_STATUS.md` §7.
9. **Hand-rolled cantrip resolution:**
    ```bash
    grep -rn "player.draw(1)\|drawCard\|self.hand.append.*deck.pop" decks/ ai/ --include='*.py'
    ```
    Every hit should be a non-Brainstorm cantrip (Ponder, Preordain, Mishra's Bauble) or activated ability (cycling). Brainstorm/Index/Sphinx's Insight should route through `engine/card_effects.py:resolve_brainstorm` or equivalent.

## Lesson #30 — "structural gaps" are usually Class B

Legacy initially deferred two matchups as "architectural sim gaps":
`painter vs eldrazi 30%` and `wan_shi_tong 32%`. Round-3 re-audit found
both were **deck-construction bugs** (Class B) — not architectural.

**Lesson for Modern:** before declaring any underperformance is "AI scoring
needs work" or "engine refactor required," run questions **1, 2, 7, 8** thoroughly. The original P0/P1 backlog (`docs/proposals/2026-05-03_p0_p1_backlog.md`) frames most outliers as AI-scoring bugs, but several may dissolve once fetch validity and Bo1 hate-card density get checked properly.

The truly architectural Modern gap was originally framed as **Affinity
overperformance** under the Bo1 default — every opponent runs too few
maindeck artifact-hate cards for Bo1. **As of 2026-05-04, the canonical
match format is Bo3** (see CLAUDE.md → "Match format: Bo3 by default"),
which dissolves most of this gap: real opponents carry artifact hate in
their SB, not their MB. The Affinity gap should be **re-measured under
Bo3** before declaring it architectural. Phase K's PR #288 mainboard
hate edits (Boros / ETron / Zoo / Living End) are a candidate for revert
under the Bo3 framework.

## What this changes for the in-flight backlog

Phase K (the audit) **inserts before** the queued P1 fix dispatch:

```
Wave 2 (P1 batch) ──pause──> Phase K (audit) ──> reorder fixes by class
```

After the audit lands, P1 fixes that turn out to be Class B/G/H become
**deck-list-edit PRs** (not AI-scoring PRs) and are typically 1-line changes.
Phase K's output redirects which P1s actually need code work.

## Workflow

Single sweep agent (mirrors the constants-cleanup pattern):
- Process each of the 5 audit-target decks in priority order.
- For each, work through all 9 questions.
- Per-deck output: a markdown report at `docs/diagnostics/2026-05-04_<deck>_audit.md` with frontmatter `status: active, priority: secondary` and per-class findings.
- For each Class-A/B/F/G/H/I finding, open a 1-line fix PR.
- Class C/D/E findings go to a follow-up dispatch since they're typically deeper changes.

## Cross-project sync

Append to `CROSS_PROJECT_SYNC.md`:
- "Legacy → Modern adoption: combo-deck audit methodology (lessons #29 + #30)" — adopted as `docs/design/2026-05-04_modern_combo_audit_methodology.md`.

## References

- MTGSimClaude PRs #111, #112, #113
- MTGSimClaude `docs/lessons/2026-05-03_combo_deck_audit.md`
- Modern `PROJECT_STATUS.md §7` (open bugs)
- Modern `docs/proposals/2026-05-03_p0_p1_backlog.md` (P0/P1 schedule — pause queued items pending audit)
- Modern `docs/design/2026-05-03_constants_cleanup_patterns.md` (sister patterns doc)

## Cumulative Legacy WR deltas (for reference — proves the methodology)

| Matchup | Baseline | After audit | Δ |
|---|---|---|---|
| depths vs burn | 35% | 62% | +27pp |
| storm vs dimir | 40% | 60% | +20pp |
| storm vs dnt | 34% | 50% | +16pp |
| storm vs burn | 30% | 43% | +13pp |
| doomsday vs storm | ~20% | 37% | +17pp |
| doomsday vs dnt | ~30% | 44% | +14pp |
| eldrazi vs burn | 39% | 48.5% | +9.5pp |
| wan_shi_tong vs burn | 30.5% | 40.5% | +10pp |
| reanimator vs ur_delver | ~30% | 38% | +8pp |
| reanimator vs burn | 20% | 25% | +5pp |
| cephalid vs dimir | 31.7% | 36.5% | +5pp |

Tests: 149 → 160 (added regression tests for each Class-A finding).

If Modern's outliers respond similarly, ~5pp average lift across the 16x16
matrix is plausible from this audit alone.
