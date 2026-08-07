---
title: "Wiki: Calibration Methodology"
status: active
priority: secondary
session: 2026-07-05
tags:
  - wiki
  - calibration
  - bo3
summary: |
  Wiki page — ground-truth calibration bands, check_calibration workflow,
  the Bo3-canonical rationale, and standard seeds. Staged under docs/wiki/
  pending wiki publication.
---

# Calibration Methodology

How do you know a Magic simulator is *right*? MTGSimManu's answer: compare simulated win rates against explicit, sourced real-world priors, and treat every divergence as a bug probe.

## Ground-truth bands

`tools/calibration_bands.json` holds expected win-rate bands with written provenance for each:

- **Matchup bands** — real-world Modern priors for specific pairings (e.g. "Amulet Titan vs Dimir Midrange: 55–70% — big-mana ramp goes over the top of 1-for-1 midrange").
- **Field bands** — per-deck expected field win rates; any registered deck outside 30–70% is flagged as a structural outlier regardless of tuning.

After every matrix run:

```bash
python3 merge_db.py                                    # canonical DB first — always
python run_meta.py --matrix -n 20 --save
python tools/check_calibration.py metagame_results.json
```

Status as of the 2026-07-05 definitive matrix: **12 in band / 17 out of band.** Out-of-band entries are not embarrassments to hide — they are the prioritized work queue. Each one points at a generic subsystem (control's hold-mana discipline, multi-turn combo lookahead), never at "nerf this deck". Bands are data with provenance, not code constants; when the real metagame shifts, the bands change in JSON with a cited reason.

## Why Bo3 is canonical

All evaluation defaults to **Best-of-3 with sideboarding**, because that is the game Modern players actually play. Best-of-1 evaluation systematically over-rewards decks whose worst matchups are answered by sideboard cards: the canonical case is Affinity — opponents carry 2–3 artifact destroyers in their sideboards and near-zero mainboard, so Bo1 shows an unrealistic pre-board world that inflated Affinity by 15–25 percentage points on the matrix (a sim-era "93% win rate" that evaporated under Bo3). The same distortion hits graveyard combo, Storm, and prison decks.

`--bo1` exists for diagnostics only. Bo1-derived numbers are never used for tournament-relevant claims, and older documents citing them carry an explicit bias annotation.

## Reproducibility

Every game is seeded and deterministic. Standard seeds: matchups start at 50000, matrix runs at 40000, both stepping by 500. Any reported number can be regenerated exactly, and any single game can be replayed with full AI reasoning:

```bash
python run_meta.py --bo3 storm dimir -s 50000    # the exact match behind a stat
python run_meta.py --trace storm dimir -s 50000  # with the AI's reasoning
```

## The feedback loop

1. Run the matrix; merge into the dashboard.
2. `check_calibration.py` flags out-of-band entries.
3. For each outlier, replay its worst matchup (`--bo3`) and find the exact turn where the AI's EV diverges from correct play.
4. Name the responsible subsystem in a diagnostic doc *before* writing code (see [Protocols](Protocols)).
5. Fix the mechanism test-first; re-run; the band verdict is the acceptance test.
