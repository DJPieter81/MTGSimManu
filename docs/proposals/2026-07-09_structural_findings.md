---
title: Structural findings from the 2026-07-05..09 calibration waves
status: active
priority: secondary
session: 2026-07-09
depends_on: docs/diagnostics/2026-07-05_calibration_probe_findings.md
tags: [architecture, structural, registry, pipeline, methodology]
summary: >
  Six structural patterns extracted from ~35 merged PRs of calibration
  work. Each is evidenced by at least one shipped fix that was an
  instance of it. Ordered by leverage; item 2 (interaction-class
  registry) has the best effort-to-payoff and its first increment
  ships alongside this doc.
---

# Structural findings — what the wave fixes keep rhyming about

## 1. Scoring/execution split-brain (highest leverage, largest change)

The AI layer scores a play by predicting resolution; the engine then
re-derives the same choices at cast time. When the two disagree, the
AI is confidently wrong.

Evidence: the X-wipe incident — `ai/ev_player.py::_gate_x_cost_board_wipe`
evaluated killables at `cap = snap.my_mana - base`, while
`engine/cast_manager.py::cast_spell` computed X *after* the base cost
drained the pool and picked X=0 (Wrath at X=0 into a 2-creature
board, 2026-07-06). `pick_wipe_x_value` was extracted precisely to
share the picker, and the two callers STILL fed it different budgets.

Direction: a **planned-cast object** — the scorer commits X / targets
/ modes once; the executor honors the plan. Kills the class:
X-choices, mode choices, target re-derivation, "which lands to tap".

## 2. Interaction classes are ad-hoc allowlists, one per consumer

Every consumer keeps a private list of "cards that count".

Evidence (all shipped as separate fixes):
- `_holdback_penalty` matched `removal|counterspell` tags but not
  `silence` — control tapped out vs combo (fixed in #475).
- `ai/sideboard_solver.py::sb_value` had clause families for five
  hate classes but none for cast-rate denial (fixed in #475).
- `ai/effective_cmc.py` improvise gate trusted a tag cache with
  silent empty entries (structural fallback added in #472).

Direction: **central class registry** (`ai/card_classes.py`) —
oracle/tag-derived membership predicates consumed by every scorer.
First increment ships with this doc: the interaction-class predicate
(counterspell / removal / cast-lock) consumed by the holdback site.
Follow-ups: hate-vs-mechanic classes (chain, graveyard, artifact),
chain-component class (`_chain_reliance`'s predicate), blink class.

## 3. "Don't discard your win condition" exists three times

Mulligan bottoming (#461, `ai/mulligan.py`), the forced-discard picker
(`ai/ev_evaluator.py::choose_card_to_strip`), and self-discard
(#478, `ai/discard_advisor.py`) each independently consult gameplan
keystones with their own loading code and their own edge cases.

Direction: one **disposability service** — "how expendable is this
card for this deck right now" — consumed by all three sites (and by
the next one before it becomes a fourth incident).

## 4. The card-DB pipeline trusts local state it shouldn't

- `merge_db.py` merges INTO an existing `ModernAtomic.json`; stale
  keys survive rebuilds. Caused two phantom WR-anchor failures
  (2026-07-06 session) and was the mechanism behind the part9
  poisoning persisting across "rebuilds".
- The auto-merge recursion CI outage (#473) came from the loader
  falling through to a committed 48-card fixture.

Direction: `merge_db.py --fresh` semantics by default (rebuild from
parts, never inherit), plus a provenance/count stamp verified at load
(the loader's one-shot bound from #473 is the containment, not the
cure).

## 5. Measurement tools don't share a seed geometry

`--matrix`, `--field`, and `--matchup` use different seed grids: the
same Goryo's build read 25.6% (matrix n=20) and 8.4% (field n=8) on
identical code the same day. Every honest before/after this week
required re-baselining per command.

Direction: one standard probe grid (or numbers always stamped with
their generating command); a `--probe` alias with fixed grid + n
would make cross-wave comparisons trustworthy by construction.

## 6. Field calibration is coupled through the broken decks

Three below-band decks donate ~95% rows to everyone above them:
~8pp of Affinity's "overshoot" is farming Goryo's/Amulet/Pinnacle.
Conversely the Storm defender fixes moved five other decks. The
headline in/out count (12/17) has been flat for three waves while
composition visibly improved.

Direction: report **trend-per-deck vs a fixed opponent pool** and/or
weight known-broken rows out of field averages until their P0s close;
track band-composition deltas, not counts.
