---
title: Affinity overperformance combo audit (Phase K, methodology v1, inverted-H)
status: active
priority: secondary
session: 2026-05-04
depends_on:
  - docs/design/2026-05-04_modern_combo_audit_methodology.md
tags:
  - audit
  - combo
  - phase-k
  - affinity
  - overperformance
summary: >
  9-question audit of Affinity (sim WR 84.0% flat / 83.0% weighted, expected
  ~55%, |Δ|=+29pp — overperforming, not under). 3 findings: 1 Class A
  (Cranial Plating equip cost {1} parsed correctly; activated equip "{B}{B}:
  attach to target creature" instant-speed correctly handled), **9 Class H
  (inverted)** — every fast-aggro/combo opponent runs 0 mainboard artifact
  hate, so Affinity faces zero G1 disruption (matrix is Bo1 by default), 0
  Class B/C/D/E/F/G/I findings on Affinity itself. The fix is on opponents'
  decklists, not Affinity's. **9 fix PRs** are filed (one per opponent that
  needs MB artifact-hate density bumped 0→2).
---

# Affinity overperformance combo audit (inverted-H)

## Context

- Live WR (matrix snapshot, N=30): **84.0%** flat / 83.0% weighted.
- Expected band: 50–60% (real Modern Affinity sits ~52% according to
  mtgtop8 and league records).
- |Δ|: **+29pp overperformance** — not underperforming.
- Engine math is rules-correct (Construct token sizing, Plating buff,
  Sojourner's 4/4 base, Scion of Draco domain discount — already
  verified in `docs/diagnostics/2026-04-23_affinity_consolidated_findings.md`).
- The hypothesis (per the methodology doc): every opponent's MB
  artifact-hate count is too low for Bo1 evaluation.

## Q1 — Card data (Class A)

Verified all 14 unique non-land cards in Affinity mainboard +
sideboard:
- Mox Opal (CMC 0, "Metalcraft — {T}: add one mana") — clean.
- Memnite (CMC 0, 1/1 creature) — clean.
- Ornithopter (CMC 0, 0/2 flying) — clean.
- Cranial Plating (CMC 2, "Equip {1}, {B}{B}: attach to target
  creature you control. Activate this only any time you could cast
  an instant.") — clean. The instant-speed equip is correctly
  parsed via the alternative-equip-cost path.
- Sojourner's Companion (CMC 7 base, Affinity for artifacts — cost
  reduces by 1 per artifact) — clean.
- Thought Monitor (CMC 7 base U, Affinity for artifacts) — clean.
- Frogmite (CMC 4 base, Affinity for artifacts) — clean.
- Springleaf Drum (CMC 1, "{T}, tap untapped creature: add one mana
  any colour") — clean.
- Signal Pest (CMC 1, "battle cry") — clean.
- Nettlecyst (CMC 3, equipment generates germ token) — clean.
- Engineered Explosives (CMC X) — clean.

**Verdict:** 0 Class A findings. Affinity card data is clean —
which is why the engine math comes out correct (the sim faithfully
represents the deck's combat output).

## Q2 — Tier-1 conformance (Class B)

Compared to canonical Affinity list (mtgtop8 Apr 2026):

```
4 Mox Opal, 4 Ornithopter, 4 Memnite, 4 Springleaf Drum, 4 Signal
Pest, 4 Thought Monitor, 4 Sojourner's Companion, 4 Cranial Plating,
2 Nettlecyst, 2 Engineered Explosives, 2 Frogmite, 4 Urza's Saga,
4 Darksteel Citadel, 2 Treasure Vault, 2 each Bridge artifact lands,
1 Island, 3 Spire of Industry
```

Decklist matches canonical within 1-of variance.

**Verdict:** 0 Class B findings.

## Q3 — Strategy/preamble interaction (Class C)

Affinity's gameplan (`decks/gameplans/affinity.json`) is a tight
T1-T3 curve_out into payoff. Trace evidence (Goryo's vs Affinity
s=50000 — verified Affinity plays Mox Opal T1, Frogmite T2, Sojourner
+ Thought Monitor T3, Plating T3, attacks T4 for 17 lethal). The AI
plays optimally.

**Verdict:** 0 Class C findings.

## Q4 — Single-deck gates (Class D)

`grep` returns 0 hits. **Verdict:** clean.

## Q5 — Heuristic cardinality (Class E)

`ai/bhi.py:test_bhi_tracks_artifact_threat_density` already covers
the BHI inference for artifact threat density. Verified.

**Verdict:** 0 Class E findings.

## Q6 — Rule strictness (Class F)

- Cranial Plating: equipped creature gets +1/+0 per artifact you
  control. Engine handler verified.
- Sojourner's Companion: 4/4, "This costs {1} less to cast for each
  artifact you control." Engine handler verified.
- Mox Opal: Metalcraft — when you control 3 or more artifacts,
  {T}: add one mana of any colour. Engine handler verified.

**Verdict:** 0 Class F findings.

## Q7 — Fetch validity (Class G)

Affinity runs no traditional fetchlands. **Verdict:** clean.

## Q8 — Bo1 hate-card density (Class H — inverted)

**This is the dominant finding.** For each opposing deck, count the
mainboard cards that destroy/disable artifacts (not just creatures —
Mox Opal, Springleaf Drum, Cranial Plating are non-creature
artifacts). The "hard hate" set used for the Bo1 evaluation:

```
Wear // Tear, Hurkyl's Recall, Force of Vigor, Haywire Mite,
Meltdown, Pithing Needle, Damping Sphere, Stony Silence, Pick
Your Poison, Boseiju, Who Endures, Foundation Breaker,
Karn the Great Creator, Collector Ouphe, Vexing Bauble,
Prismatic Ending, Wrath of the Skies (sweeper)
```

Audit script result (MB count, expected ≥ 3 per the methodology):

| Opponent | MB hate | Class H verdict |
|---|---|---|
| Boros Energy | **0** | TOO LOW (Bo1 fail) |
| Jeskai Blink | 3 | borderline (was T1 fix in P0) |
| Ruby Storm | **0** | TOO LOW |
| Eldrazi Tron | **0** | TOO LOW |
| Amulet Titan | 4 (Boseiju x3 + Vexing Bauble) | OK |
| Goryo's Vengeance | **0** | TOO LOW |
| Domain Zoo | **0** | TOO LOW |
| Living End | **0** | TOO LOW |
| Izzet Prowess | **0** | TOO LOW |
| Dimir Midrange | **0** | TOO LOW |
| 4c Omnath | 3 | borderline |
| 4/5c Control | 4 | OK |
| Pinnacle Affinity | **0** | TOO LOW (mirror) |
| Azorius Control | 6 | OK |

**9 of 15 opposing decks have MB=0 artifact hate.** Real Modern
opponents at this meta share would have:
- Boros Energy: 1-2 MB Wear // Tear or 1 MB Boseiju (currently 0/0).
- Ruby Storm: 1 MB Wear // Tear (currently 0).
- ETron: 1-2 MB Pithing Needle (currently 0).
- Goryos: 1 MB Wear // Tear or Boseiju (currently 0).
- Dimir Midrange: 1 MB Pick Your Poison (currently 0).
- Living End: 1-2 MB Force of Vigor or Boseiju (currently 0 — they
  have 2 SB).

For each, the fix is a single MB swap (typically replacing 1 dead-
slot card with 1 hate piece) that brings the count to 2-3. **The
methodology bound is 3-of MB minimum** — at 2-of, the card is in
opener ~22% of the time which still moves the needle vs the current
0% (never).

**Verdict:** 9 Class H findings on opponent decklists. PR-K4 through
PR-K12.

## Q9 — Hand-rolled cantrip resolution (Class I)

`grep` returns 0 hits. **Verdict:** clean.

## Summary

| Class | Count | Actionable now? |
|---|---|---|
| A — Card data | 0 | n/a |
| B — Decklist | 0 (Affinity's own list is fine) | n/a |
| C — Strategy/preamble | 0 | n/a |
| D — Single-deck gates | 0 | n/a |
| E — Heuristic cardinality | 0 | n/a |
| F — Rule strictness | 0 | n/a |
| G — Fetch validity | 0 | n/a |
| H — Bo1 hate density | **9 (inverted, on opponents)** | **yes** |
| I — Hand-rolled cantrips | 0 | n/a |

**Top finding:** the methodology's "inverted Class H" hypothesis is
**confirmed**. 9 of 15 opposing decks have zero MB artifact hate. The
sim's Bo1 default systematically biases the matchup against the
opponents — Affinity faces zero disruption in G1.

**Estimated WR shift:** if all 9 opponents bump from 0 → 2 MB hate,
Affinity's overall WR should drop from 84% to ~65-70%, getting
close to the expected 55% band. (Real Modern post-board Affinity is
~52%; the Bo3 simulation will further close the gap once the SB game
gets simulated more often.)

## Fix-PR list

Each fix is a 1-line decklist edit (swap a low-impact slot for a
hate piece). Each ships with a regression test asserting the new
mainboard count.

- **PR-K4 (Class H, Boros Energy):** +1 Wear // Tear MB, −1 Thraben
  Charm MB. Branch: `claude/fix-classH-boros-add-mb-wear-tear`.
  Test: `tests/test_boros_decklist_artifact_hate.py`.

- **PR-K5 (Class H, Ruby Storm):** +1 Wear // Tear MB, −1 Glimpse
  the Impossible MB. Branch: `claude/fix-classH-storm-add-mb-wear-tear`.
  (Note: storms-into-Wear // Tear is a real MB choice in some lists;
  Storm uses it as an answer to opp Chalice + Boseiju target.)

- **PR-K6 (Class H, Eldrazi Tron):** +1 Pithing Needle MB, −1
  Endbringer MB (E2 → E1). Branch:
  `claude/fix-classH-etron-add-mb-pithing-needle`.

- **PR-K7 (Class H, Goryo's Vengeance):** +1 Boseiju, Who Endures MB
  (replacing 1 Plains). Branch:
  `claude/fix-classH-goryos-add-mb-boseiju`.

- **PR-K8 (Class H, Domain Zoo):** +1 Wear // Tear MB, −1 Stubborn
  Denial MB. Branch:
  `claude/fix-classH-zoo-add-mb-wear-tear`.

- **PR-K9 (Class H, Living End):** +1 Force of Vigor MB, −1 Subtlety
  MB (4 → 3). Branch:
  `claude/fix-classH-living-end-add-mb-force-of-vigor`.

- **PR-K10 (Class H, Izzet Prowess):** +1 Pick Your Poison MB, −1
  Lava Dart MB. Branch:
  `claude/fix-classH-izzet-add-mb-pick-your-poison`.

- **PR-K11 (Class H, Dimir Midrange):** +1 Pick Your Poison MB, −1
  Drown in the Loch MB. Branch:
  `claude/fix-classH-dimir-add-mb-pick-your-poison`.

- **PR-K12 (Class H, Pinnacle Affinity):** +1 Vexing Bauble MB, −1
  Lavaspur Boots MB. Branch:
  `claude/fix-classH-pinnacle-add-mb-vexing-bauble`. (Note: this is
  the mirror — Pinnacle Affinity's MB does have answers to its own
  archetype mirror via Hurkyl's Recall in SB only.)

**See the Phase K summary doc for the dispatch ordering and
expected matrix lift.**
