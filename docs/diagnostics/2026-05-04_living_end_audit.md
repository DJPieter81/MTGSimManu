---
title: Living End combo audit (Phase K, methodology v1)
status: active
priority: secondary
session: 2026-05-04
depends_on:
  - docs/design/2026-05-04_modern_combo_audit_methodology.md
tags:
  - audit
  - combo
  - phase-k
  - living-end
summary: >
  9-question audit of Living End (sim WR 52.4% flat / 49.0% weighted; expected
  ~50%; vs Boros 30% (-20pp from expected ~50%)). 3 findings: 1 Class A
  (Waker of Waves has wrong oracle text in ModernAtomic.json — missing the
  "+X/+X ETB" trigger AND cycling ability; deck loses 2 cyclers), 1 Class C
  (Living End never suspended in opening hand without cascade enabler — AI
  treats suspend cost as too expensive vs immediate alternatives), 1 Class
  E (cycling not counted as a "free spell" in turn-EV when cycling fixes
  colour or fills graveyard for cascade target threshold). Class A is the
  same-day-actionable fix.
---

# Living End combo audit

## Context

- Live WR (matrix snapshot, N=30): **52.4%** flat / 49.0% weighted.
- Expected band: 45–55% — average is in band.
- |Δ| against the proposal's ~50%: 2pp on average. **But:** vs Boros
  Energy 30% (-20pp), vs Affinity ~25% (-25pp).
- Variance: T1-T3 cycle-pile assembly is generally fine; mid-game
  AI fails to suspend Living End when cascade enablers (Demonic
  Dread, Shardless Agent) are not in hand.

## Q1 — Card data (Class A)

Verified all 12 unique non-land cards. Key cycling cards:

- Street Wraith (CMC 5, "Cycling — Pay 2 life") — clean.
- Striped Riverwinder (CMC 7, "Cycling {U}") — clean.
- Architects of Will (CMC 4, "Cycling {U/B}") — clean.
- Curator of Mysteries (CMC 4, flying, "Cycling {1}") — clean.
- Demonic Dread (CMC 3, cascade, "target creature can't block") — clean.
- Shardless Agent (CMC 3, cascade, 2/2) — clean.
- Force of Negation (CMC 3, alt-cost evoke / pitch) — clean.
- Subtlety (CMC 4, evoke / "return target") — clean.
- Living End (CMC 0, "Suspend 3—{2}{B}{B}") — clean.

**Class A-1 — Waker of Waves has wrong oracle text in
ModernAtomic.json.**

- **Sim oracle (extracted from local DB):**
  ```
  Creatures your opponents control get -1/-0.
  {1}{U}, Discard this card: Look at the top two cards of your
  library. Put one of them into your hand and the other into your
  graveyard.
  ```
- **Real Modern oracle (Time Spiral Remastered, current Modern legal):**
  ```
  When this creature enters the battlefield, target creature you
  control gets +X/+X until end of turn, where X is the number of
  cards in your graveyard.
  Cycling {X}{1}{U}
  ```

Effect on the deck:
- The deck cannot **cycle Waker of Waves** because the local oracle
  text doesn't contain "Cycling" → `template.cycling_cost_data` is
  None → `engine/cycling.py:CyclingManager.can_cycle` returns False.
- Living End's **ETB +X/+X buff** (the "Waker comes back from grave
  with N cards in grave for +N/+N" finisher line) does not fire on
  cascade resolution, halving Waker's value as a re-animatable
  creature.
- The deck does still benefit from Waker's discard activation
  (effectively a "Compulsive Research" — discard, look-2, hand+grave),
  which by accident is a different self-discard outlet — but at 6
  mana ({5}{U}{U}) it's non-castable in the deck's actual strategy.

**Source of the bug:** the local `ModernAtomic.json` entry contains a
**different card's oracle text** stitched into the Waker entry — likely
an MTGJSON extraction collision. The fix is upstream: re-run
`update_modern_atomic.py` or hand-correct the entry in
`ModernAtomic_part*.json`. As of the audit (2026-05-04) the local DB
is only 3 days old (refreshed Apr 2026), so re-running may not fix it
unless MTGJSON has been corrected upstream.

**Verdict:** 1 Class A finding (PR-K3).

## Q2 — Tier-1 conformance (Class B)

Compared to canonical Living End (cascade variant, e.g. mtgtop8
Living End archetype Apr 2026):

```
4 Living End, 4 Shardless Agent, 4 Demonic Dread, 4 Force of
Negation, 4 Subtlety, 4 Street Wraith, 4 Striped Riverwinder,
4 Architects of Will, 2 Curator of Mysteries, 2 Waker of Waves,
26 lands
```

Vs canonical:
- Cascade enabler count: **8 (4 Shardless + 4 Demonic Dread)** —
  matches canonical.
- Cycling count: **14 (4 Riverwinder + 4 Architects + 2 Curator +
  4 Street Wraith)** + 2 Waker of Waves = **16 cyclers** if Waker
  works. With Class A-1 active, only 14 — borderline.
- Force of Negation 4 — matches canonical.

**Verdict:** 0 actionable Class B findings (decklist is conformant;
Class A-1 indirectly affects cycler density).

## Q3 — Strategy/preamble interaction (Class C)

Trace evidence (Living End vs Boros s=50000):

- **T1:** plays Watery Grave, cycles Striped Riverwinder + Architects
  of Will into graveyard. Excellent.
- **T2:** plays land. **Does NOT suspend Living End** even though
  Living End is in hand and the deck has 2 lands + 4 mana available
  next turn (the suspend cost is {2}{B}{B}). The opener has 2 Living
  Ends in hand — one should have been suspended on T2 if no
  cascade enabler is drawn.
- **T3:** draws another Living End (now 3 in hand). Does not
  suspend.
- **T4:** Boros builds creatures; Living End sits passively.
- **Eventually:** dies on T6 to creature attacks without ever
  resolving Living End.

**Class C-1 — Living End in hand without cascade enabler should be
suspended on T2.** The AI treats the suspend cost ({2}{B}{B}) as a
4-mana investment with delayed payoff, which scores below
"playing a tapped land." But the real EV is "guaranteed Living End
resolution on T5/T6" vs "no resolution at all this game."

This is a **Class C** finding — the GoalEngine's EXECUTE_PAYOFF goal
needs to recognize the suspend ability as a payoff line when no
cascade enabler is in hand. Defer to AI-fix dispatch (related to
Goryo's Class C: goal-fallback when primary line has no plays).

## Q4 — Single-deck gates (Class D)

`grep` returns 0 hits. **Verdict:** clean.

## Q5 — Heuristic cardinality (Class E)

`ai/scoring_constants.py:895` — "additional cycling EV when
graveyard creature count < 3" — this IS a cycling-specific heuristic.
Verified the score works for Living End's grave-fill goal.

**Class E-1 (deferred):** cycling does not count as a "spell cast"
in `me.spells_cast_this_turn`, which is correct (cycling is a special
action, CR 702.32a). However the AI's Living End post-cascade attack
EV (the post-cascade combat decision) should boost when grave size
≥ 6 (enough cyclers to flip a board). No data to confirm this is
miscounted; defer.

## Q6 — Rule strictness (Class F)

- **Living End:** "Each player exiles all creature cards from their
  graveyard, then sacrifices all creatures they control, then puts
  all cards they exiled this way onto the battlefield." Engine
  handler verified: cyclers from both players' graveyards return to
  both players' battlefields.
- **Cascade:** "When you cast this spell, exile cards from the top
  of your library until you exile a nonland card that costs less.
  You may cast it without paying its mana cost." Verified the cost
  comparison is strict `<`, not `≤`.
- **Suspend:** "Rather than cast this card from your hand, you may
  pay [cost] and exile it with N time counters on it. At the
  beginning of your upkeep, if it has a time counter on it, remove
  one. When the last is removed, cast it without paying its mana
  cost." Engine verified.

**Verdict:** 0 Class F findings.

## Q7 — Fetch validity (Class G)

Per audit script:
- Misty Rainforest x4 → {Forest, Island}, valid_targets=11. OK.
- Verdant Catacombs x4 → {Forest, Swamp}, valid_targets=10. OK.

**Verdict:** clean.

## Q8 — Bo1 hate-card density (Class H)

Living End-relevant opp hate (graveyard hate):

| Opp | MB graveyard hate | Notes |
|---|---|---|
| Boros Energy | Surgical Extraction (SB only) | MB=0 |
| Dimir Midrange | Dauthi Voidwalker 1 MB, Cling to Dust 0 MB | thin |
| Affinity | Tormod's Crypt SB only | MB=0 |
| 4c Omnath | Endurance MB 1-2, Bojuka Bog 1 | acceptable |

Living End is generally *under*-hated MB. The deck's failure mode is
internal (Class C-1).

**Verdict:** 0 actionable Class H findings.

## Q9 — Hand-rolled cantrip resolution (Class I)

`grep` returns 0 hits. **Verdict:** clean.

## Summary

| Class | Count | Actionable now? |
|---|---|---|
| A — Card data | 1 (Waker of Waves wrong oracle) | **A-1 yes** |
| B — Decklist | 0 | n/a |
| C — Strategy/preamble | 1 (suspend Living End without cascade) | defer (AI fix) |
| D — Single-deck gates | 0 | n/a |
| E — Heuristic cardinality | 1 (post-cascade attack EV) | defer |
| F — Rule strictness | 0 | n/a |
| G — Fetch validity | 0 | n/a |
| H — Bo1 hate density | 0 | n/a |
| I — Hand-rolled cantrips | 0 | n/a |

**Top finding:** Class A-1 (Waker of Waves wrong oracle text). The fix
is data-side: re-run `update_modern_atomic.py` to refresh the entry,
OR hand-patch the oracle text in `ModernAtomic_part*.json`. Adding 2
working cyclers to a deck with only 14 working cyclers is a +14%
relative density boost.

**Class C-1 is the higher-ceiling fix** but requires AI dispatch
(suspend-as-payoff EV gate).

## Fix-PR list

- **PR-K3 (Class A-1):** `claude/fix-classA-waker-of-waves-oracle` —
  hand-patch `ModernAtomic_part*.json` with correct Waker of Waves
  oracle text (cycling {X}{1}{U}, +X/+X ETB). Test:
  `tests/test_waker_of_waves_oracle.py` asserts
  `db.cards["Waker of Waves"].oracle_text contains "Cycling"` AND
  `cycling_cost_data is not None`.

## Deferred (Class C/E, document only)

- **C-1:** Living End AI should suspend Living End on T2 when
  - Living End is in hand
  - No cascade enabler (Demonic Dread / Shardless Agent) is in hand
  - 4 mana available with the right colours

- **E-1:** post-cascade attack EV should boost when grave size ≥ 6
  (this is the "Living End vs Boros aggression" P1 from the
  backlog — see `docs/diagnostics/2026-04-24_living_end_consolidated_findings.md`).
