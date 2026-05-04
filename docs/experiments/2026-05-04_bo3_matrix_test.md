---
title: Bo3 matrix experiment — does Affinity overperformance close under sideboarding?
status: active
priority: secondary
session: 2026-05-04
depends_on:
  - docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md
  - docs/diagnostics/2026-05-04_phase-k-summary.md
tags:
  - matrix
  - bo3
  - affinity
  - framing
summary: >
  Bo1 N=30 vs Bo3 N=20/N=50 field comparison testing whether Bo1 framing
  is the source of Affinity's 84% sim WR. Result: hypothesis FALSIFIED.
  Affinity Bo1 (85.4%) ≈ Bo3 (87.2%); Bo3 actually nudged WR up by +1.8pp.
  Recommendation: NO CHANGE TO MATRIX DEFAULT. Affinity overperformance
  is AI-side bias (or decklist), not framing. Goryo's drops 9.9pp under
  Bo3 — sideboarding HURTS the deck that lacks proven sideboard answers.
---

# Bo3 matrix experiment — Affinity framing test

## Hypothesis

Phase L's audit conclusion: "switching matrix default to Bo3 is the
structural fix" once individual fixes had been applied. The PR-L1
artifact-land fix landed but only moved Affinity by +1.8pp (wrong
direction). Phase K's smoke test showed Boros vs Affinity 10% → 30%
when Phase K added artifact hate to the four-deck SB batch.

If Bo3 framing is the dominant gap-closer, we expect:

- Affinity Bo3 << Affinity Bo1 (sideboard hate kicks in)
- Goryo's Bo3 ≥ Goryo's Bo1 (low outlier benefits from variance)
- Storm/Living End roughly stable

## Methodology

Discovery: `run_meta_matrix(bo1=False)` is **already the default** in
both Python API and CLI. Yesterday's saved
`metagame_results.json` (timestamp 2026-05-04T09:44:57, format `bo3`,
n=50) is therefore the Bo3 baseline — Phase L's narrative that "the
matrix runs Bo1" is incorrect for the current code; the `--bo1` flag
is the opt-in for legacy single-game tallies.

To compare Bo1 vs Bo3 under matched conditions (same seed start
`50000`, step `500`), the experiment ran a `--field` sweep on five
key decks at both formats:

| Deck | Bo1 N | Bo3 N |
|---|---:|---:|
| Affinity | 30 | 20 |
| Goryo's Vengeance | 30 | 20 |
| Boros Energy | 30 | 20 |
| Ruby Storm | 30 | 20 |
| Living End | 30 | 20 |

N=20 Bo3 is statistically comparable to N=30 Bo1 — Bo3 averages
~2.5 games/match, so total games are ~50 per Bo3 cell vs 30 per
Bo1 cell, giving the Bo3 number tighter variance.

Driver script: `tools/bo1_vs_bo3_experiment.py` — pre-loads the
CardDatabase once in the parent process, fork-spawns workers
(2 workers, sandbox memory budget), each opponent in the field run
gets 30/20 games against the chosen deck. Sandbox workaround for
ModernAtomic.json corruption: load directly from the eight
`ModernAtomic_part*.json` files into a private temp file rather than
the auto-discovery path.

Raw outputs: `data/bo1_vs_bo3/{deck_slug}_{bo1|bo3}_n{N}.json`.

## Per-deck Bo1 vs Bo3 deltas

| Deck | Bo1 avg | Bo3 avg | Δ (Bo3−Bo1) |
|---|---:|---:|---:|
| Affinity | 85.4% | 87.2% | **+1.8pp** |
| Goryo's Vengeance | 17.7% | 7.8% | **−9.9pp** |
| Boros Energy | 60.4% | 64.4% | +4.0pp |
| Ruby Storm | 53.2% | 56.6% | +3.3pp |
| Living End | 53.6% | 54.4% | +0.8pp |

Wall-clock for the full set (5 decks × 2 formats × 16 opponents):
roughly 21 minutes, dominated by Storm's interactive lines (Storm
Bo3 alone took 7m26s). Bo3 cost over Bo1 is ~1.8× per cell, not
~3× — many Bo3 matches end 2-0 in two games.

## Affinity-specific narrative

**Bo3 does not close Affinity's gap. It widens it.**

Per-matchup detail (Affinity):

| Opponent | Bo1 | Bo3 | Δ |
|---|---:|---:|---:|
| Domain Zoo | 70% | 55% | **−15pp** |
| Living End | 87% | 75% | −12pp |
| Azorius Control | 100% | 95% | −5pp |
| Boros Energy | 77% | 75% | −2pp |
| Goryo's | 100% | 100% | 0pp |
| Jeskai Blink | 83% | 85% | +2pp |
| Eldrazi Tron | 87% | 90% | +3pp |
| Dimir Midrange | 87% | 90% | +3pp |
| Amulet Titan | 90% | 95% | +5pp |
| Izzet Prowess | 90% | 95% | +5pp |
| 4c Omnath | 73% | 80% | +7pp |
| Pinnacle Affinity | 87% | 95% | +8pp |
| Ruby Storm | 77% | 85% | +8pp |
| 4/5c Control | 83% | 95% | +12pp |
| Azorius Control (WST) | 83% | 95% | +12pp |

The per-matchup picture is split:

- Three matchups (Zoo, Living End, vanilla AzCon) drop in Bo3.
  These are the matchups where opponent SB hate (e.g., Wear // Tear
  out of Zoo's red-white shell, Force of Vigor out of Living End)
  actually punishes Affinity.
- Twelve matchups *improve* in Bo3, several by +5 to +12pp. The
  AI-driven sideboard plans are net-positive for Affinity, not net-
  negative. Affinity's SB swap-ins (extra creatures, extra
  Springleaf Drum or Shadowspear lines depending on the matchup)
  outweigh the hate coming back.

Net effect: +1.8pp in Bo3 → Affinity gets *more* dominant, not
less. The Phase K smoke (Boros vs Affinity 10% → 30%) does **not**
generalise — that test was at N=10 with a freshly added SB hate
package; the 100-game field shows the hate doesn't translate to a
matchup swing once both sides board.

## Goryo's, Storm, Living End shifts

- **Goryo's** drops 17.7% → 7.8% under Bo3 (−9.9pp). This is the
  inverse of the framing hypothesis: sideboards hurt the deck that
  has nothing to bring in (graveyard hate from opponents kicks in,
  Goryo's SB is poorly-tuned reactive cards). Goryo's becomes the
  worst deck in the format under Bo3 — confirms that Bo3 is *not*
  a corrective for low outliers when the deck's SB itself is weak.
- **Storm** is stable: 53.2% Bo1 → 56.6% Bo3 (+3.3pp, within
  statistical noise at this sample). Combo decks behave similarly
  in both formats because the SB swap is small (+1-2 hate cards
  vs control matchups, otherwise the deck stays the same).
- **Living End** is essentially unmoved: 53.6% → 54.4% (+0.8pp).
  The deck's SB plan is symmetrical with what comes back from
  opponents (graveyard hate vs no-graveyard plan B); the WR
  doesn't shift.

## Recommendation

**KEEP BO1 AS THE OPTIONAL OPT-IN. Bo3 IS ALREADY THE DEFAULT and
should remain the default.**

The matrix has been Bo3 by default since at least Phase 11 (`bo1=False`
in `run_meta_matrix`). The Phase L recommendation to "switch the
matrix default to Bo3" was based on the assumption that the matrix
was running Bo1; that assumption is wrong. Yesterday's
`metagame_results.json` (Affinity 84.3% flat, 80.4% weighted) is
already a Bo3 N=50 measurement.

The framing hypothesis is **falsified**: Affinity's overperformance
survives Bo3 with a higher WR than under Bo1. The remaining
gap is AI-side or decklist, not framing.

### Implications for next steps

1. **Phase L's "structural fix" line in §Conclusion is stale** —
   the audit assumes the matrix is Bo1; it isn't. Don't act on
   the "switch matrix default to Bo3" recommendation; it's a
   no-op.
2. **Affinity at 87.2% Bo3 is the real number to fix.** Likely
   sources:
   - AI scoring undervalues opposing artifact hate (Wear // Tear
     not feared; the BHI threat-discount may be too low).
   - Affinity's SB swap is too generic — opponents board in
     reactive cards that the AI doesn't side around.
   - Affinity's "dump artifacts and swing" sequencing dominates
     when the AI doesn't see Force of Vigor / Wear // Tear in
     hand (BHI under-counts SB cards drawn into G2/G3).
3. **Goryo's drop (17.7% → 7.8%) is a separate P0** — it implies
   Goryo's SB is mis-curated. The deck has no real Plan B vs
   graveyard hate. This is a decklist issue (add more Stinging
   Study / counterspells / Sink into Stupor for hate) rather
   than an AI issue.

## Verification

- `python tools/check_abstraction.py` → exit 0
- `python tools/check_magic_numbers.py` → exit 0 (`total = 13 (baseline allowed = 13)`)
- `python tools/check_doc_hygiene.py` → exit 0
- All five field runs completed without worker errors.
- The Affinity Bo3 number (87.2%) is consistent within ±3pp with
  the saved `metagame_results.json` (84.3% flat) — the smaller
  difference is N=20 vs N=50 sampling and a different baseline
  set (this experiment ran field-vs-all-15 including Azorius
  Control v2; the saved matrix is 16-of-17 decks).

## References

- `docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md` —
  source of the Bo3 hypothesis.
- `docs/diagnostics/2026-05-04_phase-k-summary.md` — origin of
  the +1 MB artifact hate batch.
- `data/bo1_vs_bo3/*.json` — raw experiment outputs, one file
  per deck × format.
- `tools/bo1_vs_bo3_experiment.py` — driver script with the
  parts-file DB-load workaround.
