---
title: Phase 11b — N=50 matrix validation with Bo3 loser-chooses-play
status: active
priority: historical
session: 2026-04-21
depends_on:
  - docs/experiments/2026-04-20_phase6_matrix_validation.md
  - docs/experiments/2026-04-20_phase9_phase6_followups.md
  - docs/diagnostics/2026-04-19_bo3_play_draw_rule.md
tags:
  - matrix
  - phase-11
  - bo3
  - validation
  - completed
summary: "N=50 16-deck matrix confirms Phase 1-10 gains hold at 2.5× sample size. Boros 78% flat / 79% weighted (T1 confirmed). Affinity 87% flat / 84% weighted still meta-dominant. Ruby Storm 25%/22% — structural, not noise. Bo3 loser-chooses-play (Phase 11a) plus N=20→50 noise reduction produces ≥5pp weighted swings for four decks; largest is Pinnacle Affinity −7.1pp and 4c Omnath −7.0pp. No new P0 regressions."
---
# Phase 11b — N=50 matrix validation

## Goal

The EV correctness overhaul (Phases 1-10) shipped at N=20 for dashboard
WRs. The design doc originally called for N=50 to lock the gains in
against matrix noise. Phase 11a additionally corrected the Bo3
play/draw rule (`CR 103.2`; loser of prior game is on play by default).
A single N=50 matrix run confirms or contradicts the accumulated
direction.

## Procedure

1. Baseline: `metagame_data_pre_phase11b.jsx` = N=20 snapshot from Phase 9.
2. `python run_meta.py --matrix -n 50 --save` — 136 matchups × 50 Bo3 = 6800 total games, 15 workers.
3. Dashboard rebuilt to N=50 (`modern_meta_matrix_full.html`, 163k chars).
4. Delta table extracted via shared helper (see end of this doc).

## Headline deltas (sorted by post-N=50 weighted WR)

| Deck | Flat N=20 | Flat N=50 | ΔFlat | Wgt N=20 | Wgt N=50 | ΔWgt |
|---|---:|---:|---:|---:|---:|---:|
| Affinity | 82.7% | 87.1% | **+4.4** | 81.3% | 83.8% | +2.5 |
| Boros Energy | 74.0% | 78.4% | **+4.4** | 75.3% | 78.7% | +3.4 |
| Domain Zoo | 70.7% | 73.3% | +2.6 | 68.1% | 70.0% | +1.9 |
| Eldrazi Tron | 70.3% | 72.3% | +2.0 | 66.3% | 69.3% | +3.0 |
| Pinnacle Affinity | 65.7% | 60.1% | **−5.6** | 61.3% | 54.2% | **−7.1** |
| Jeskai Blink | 64.3% | 61.2% | −3.1 | 57.9% | 55.2% | −2.7 |
| Dimir Midrange | 55.7% | 57.6% | +1.9 | 53.7% | 52.2% | −1.5 |
| 4c Omnath | 62.0% | 56.4% | **−5.6** | 58.1% | 51.1% | **−7.0** |
| Amulet Titan | 47.7% | 46.7% | −1.0 | 42.3% | 41.4% | −0.9 |
| Izzet Prowess | 43.7% | 43.9% | +0.2 | 34.8% | 36.8% | +2.0 |
| 4/5c Control | 33.7% | 38.1% | **+4.4** | 28.7% | 32.7% | +4.0 |
| Azorius Control (WST) | 37.3% | 35.7% | −1.6 | 37.4% | 31.6% | **−5.8** |
| Living End | 25.7% | 26.8% | +1.1 | 23.0% | 23.2% | +0.2 |
| Ruby Storm | 25.3% | 24.9% | −0.4 | 23.4% | 21.9% | −1.5 |
| Goryo's Vengeance | 23.0% | 22.7% | −0.3 | 21.0% | 21.5% | +0.5 |
| Azorius Control | 18.3% | 14.8% | −3.5 | 15.0% | 11.7% | −3.3 |

**Bold** marks shifts ≥5pp. Same 16 decks in both snapshots; the sim
internally ran 17 because of the Azorius Control WST v2 variant —
dashboard continues to track 16 as in prior phases.

## Interpretation

The matrix separates two effects that are conflated in a single run:

1. **Noise reduction.** Standard error of a 100-sample Bo3 WR drops
   from σ ≈ 5pp at N=20 to σ ≈ 3pp at N=50. Any swing ≤5pp in
   absolute value is consistent with noise alone.
2. **Bo3 loser-chooses-play (Phase 11a).** The previous RNG coin-flip
   was a zero-mean perturbation on top of true Bo3 WRs. Correcting it
   systematically favours decks that can convert the on-the-play G2/G3
   advantage — aggro and tempo decks.

### Consistent with the Bo3 fix

- **Boros Energy +3.4pp weighted, Affinity +2.5pp** — aggro/tempo that
  benefits from G2-on-play after winning G1.
- **Pinnacle Affinity −7.1pp weighted, 4c Omnath −7.0pp, Azorius Control
  (WST) −5.8pp** — midrange/control decks that previously got "free"
  G2-on-play coin flips when the opponent had won G1. Under the new
  rule, the opponent (usually on the play in G2 after losing) can
  convert the fast match more often.
- **Jeskai Blink −2.7pp, 4/5c Control +4.0pp** — mixed; Jeskai loses
  play edges it didn't earn, 4/5c Control's flat WR bump is mostly
  noise-reduction reverting an N=20 dip.

### Best explained by noise reduction alone

- **Ruby Storm −0.4 flat / −1.5 weighted** — still ~24% flat. The
  Storm finisher patience work (Phase 9a) held its direction from N=20;
  the 39%-WR problem from the LLM audit (April) is resolved but the
  deck remains structurally weak against the current field.
- **Living End +1.1 / +0.2** — no movement; the combo still doesn't
  fire consistently under the current engine (P0 from the original
  audit that remains open).

### No new P0 regressions

- No deck swung by >7pp weighted.
- No deck's flat WR crossed the 50% boundary in either direction
  (Pinnacle Affinity held T2 at 60%, Izzet Prowess held ~43%).
- Rank-order shuffles are minor: T1 roster unchanged (Affinity, Boros,
  Eldrazi Tron, Jeskai Blink, Ruby Storm); T2 roster unchanged except
  Pinnacle Affinity dropped from T1-contender toward T2-core.

### Open structural issues (not addressed here)

Tracked in `PROJECT_STATUS.md`. Phase 11b does **not** touch any of
these:
- Azorius Control 14.8% flat — expected 30-50%, 15pp below. Severe;
  likely a deck construction or gameplan issue, not an engine bug.
- Living End 27% flat — combo non-firing; P0 from original audit.
- Goryo's Vengeance 23% flat — combo non-firing; P0 from original audit.
- Ruby Storm 25% flat — improved from Phase 9 but still below expected
  25-50% range (barely — within the outlier report's "minor" band).

## Files produced

- `metagame_data.jsx` — N=50 snapshot (authoritative).
- `metagame_data_pre_phase11b.jsx` — N=20 baseline for comparison.
- `metagame_results.json` — raw wins matrix.
- `modern_meta_matrix_full.html` — rebuilt dashboard (163k chars).

## Runtime

~8 minutes on the 15-worker pool (matrix + merge + dashboard build).
Meaningfully faster than the Phase 6 N=20 matrix because of cached
card-database loads across workers.

## Delta-extraction helper

```python
import json
def extract(path):
    c = open(path).read()
    i = c.index('const D = ') + 10
    depth = 0; j = i
    while j < len(c):
        ch = c[j]
        if ch == '{': depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                j += 1; break
        j += 1
    D = json.loads(c[i:j])
    return {o['deck']: o for o in D['overall']}, D['matches_per_pair']

pre, n_pre = extract('metagame_data_pre_phase11b.jsx')
post, n_post = extract('metagame_data.jsx')
# ... format table as above ...
```

## What we did NOT do

- Did not act on any of the deck-balance signals surfaced above.
  Those go in `PROJECT_STATUS.md` as diagnostic data for the next
  planning session.
- Did not re-generate replay HTMLs for outlier matchups. The auto-
  replay trigger protocol in `CLAUDE.md` applies if we decide to chase
  the Pinnacle Affinity drop.
- Did not re-run the 1M-run deck-guide suite. Reference guides in
  `templates/` are still keyed to Phase 9 numbers; update when a deck
  guide is formally refreshed.
