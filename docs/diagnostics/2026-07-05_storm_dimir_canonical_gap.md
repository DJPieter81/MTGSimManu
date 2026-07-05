---
title: Storm vs Dimir 14% under canonical 9-part DB — part9 is a corrupt, unprovenanced override, not an update
status: active
priority: primary
session: 2026-07-05
supersedes: []
superseded_by: []
depends_on:
  - docs/history/audits/2026-05-16_5panel_bo3_audit.md
tags:
  - ruby-storm
  - dimir
  - data-layer
  - modernatomic
  - part9
  - merge-db
  - provenance
summary: >
  Reproduced Ruby Storm vs Dimir Midrange at 14% (N=50 Bo3, seeds 50000+500k)
  on the canonical 9-part DB vs a 35-45% real-world band. Root cause is NOT a
  stale handler or classifier binding — it is ModernAtomic_part9.json itself.
  Part9 carries no MTGJSON meta wrapper and its 30 entries are pre-errata or
  outright fabricated texts (Ral, Monsoon Mage as {U}{R} 1/2 with a
  three-spells transform clause; the real MH3 card is {1}{R} 1/3 with the
  coin-flip trigger, exactly what the provenanced parts 1-8 MTGJSON
  5.3.0+20260410 export says). merge_db.py's glob-all-parts semantics let the
  unprovenanced part clobber 30 validated entries. Responsible subsystem:
  data layer (ModernAtomic part files + merge ordering). Fix: remove part9,
  add a provenance guard test on part files.
---

# Storm vs Dimir ≈14% under the canonical DB — root cause

## Reproduction (canonical 9-part DB, 2026-07-05)

- `python3 merge_db.py` → 21795 cards from parts 1-9 (part9 contributes 30
  overriding entries, zero new names — all 30 already exist in parts 1-8).
- `python run_meta.py --matchup storm dimir -n 50` → **Ruby Storm 14%**
  (32 sweeps, 18 three-gamers). Matches PR #454's corrected-DB finding.
- Real-world band: 35-45%. Earlier parts-1-8-only sessions measured 25-35%.

## Turn-level divergence (Bo3 replays)

| Seed | Result | Ral, Monsoon Mage behavior |
|---|---|---|
| 50000 | Dimir 2-0 | Ral in 2 opening hands, **cast 0 times**; mulligan logic bottoms Ral (`P1 mulligans to 5, bottoms: ['Ral, Monsoon Mage // ...']`) |
| 60101 | Storm 2-1 | Ral drawn, cast 0 times |
| 70100 | Dimir 2-1 | Ral **discarded to hand size on T2 and T6**; cast only twice, both times only because Gemstone Caverns produced U (`Tap Gemstone Caverns→U, Mountain→R`) |

The EV divergence is structural, not decision-layer: the deck's 4-of engine
card (gameplan weight 20.0, listed in `primary_engine`) is uncastable on the
deck's mana base for most games, so Storm plays 56-card Bo3s while paying
mulligan/discard costs for the 4 dead copies.

## Part9 stale-binding audit (systematic, all 30 cards)

Classifier (`decks/gameplans/_oracle_classifier.json`, 30 entries total):

- 29 of the 30 part9 cards have **no classifier entry** (not classifier-bound
  at all).
- 1 entry — **Force of Negation** — is flagged STALE against part9's text.
  Its pinned sha256 `0ba9d8a8…` matches the **parts 1-8** text exactly. The
  classifier was built against the genuine MTGJSON export; part9's override
  is what un-pinned it.

Handlers / oracle paths:

- `engine/oracle_resolver.py` `_handle_coin_flip_transform` (~L567-720) keys
  on `'flip a coin'` — matches the genuine parts 1-8 Ral text; never fires
  under part9's fabricated wording.
- `ai/combo_calc.py` / `ai/combo_evaluator.py` flip-coin transform EV
  (`'flip a coin' in oracle_text`) — same: correct vs parts 1-8, dead vs
  part9.
- `engine/card_effects.py` Ral ETB (cost-reduction log line) is
  wording-independent.

Conclusion of the audit: **no handler or classifier entry is stale relative
to the real card.** Every binding is keyed to the parts 1-8 (MTGJSON
5.3.0+20260410) texts. The "drift" is entirely inside part9.

## Why part9 is corrupt, not an update

1. **Provenance.** Parts 1-8 carry `meta: {date: 2026-04-10, version:
   "5.3.0+20260410"}` — a genuine MTGJSON export. Part9 is a bare
   name→faces dict with **no meta wrapper**.
2. **Templating regression.** Part9 texts use pre-2024 self-naming oracle
   templating ("Sacrifice Tormod's Crypt", "Haywire Mite dies", "enters the
   battlefield") where the 2026 MTGJSON export in parts 1-8 uses the current
   short templating ("this artifact", "this creature", "enters"). A newer
   export cannot revert templating pool-wide.
3. **Fabricated card identities** (checked against the printed cards):
   - *Ral, Monsoon Mage*: part9 says {U}{R}, 1/2, "If you've cast three or
     more instant and/or sorcery spells this turn, exile Ral…". The printed
     MH3 card is **{1}{R}, 1/3**, coin-flip trigger. Parts 1-8 match the
     printed card.
   - *Kappa Cannoneer*: part9 drops the **Artifact** card type and rewrites
     the trigger to exclude itself.
   - *Pinnacle Emissary*: part9 says {3}{R}, "Warp {1}", non-artifact — the
     printed EOE card is {1}{U}{R} artifact creature with Warp {U/R}.
   - *Lavaspur Boots*: part9 drops ward {1}.
   - *Force of Negation*: part9 drops the exile-instead-of-graveyard rider.
   - *Fable of the Mirror-Breaker*: part9 replaces the token's Treasure
     trigger with "haste".
   - *Sink into Stupor*: part9 drops the "target spell or" mode.
4. **Zero new names.** All 30 part9 keys already exist in parts 1-8 — it is
   a pure override layer, adding nothing.

The 2026-05-10 commit (bbbe093) that introduced part9 landed all part files
in one squash, so the corruption was never visible as a diff. The likely
origin is an LLM-authored "update" generated from stale memory (the old
templating is characteristic).

## Named responsible subsystem

**Data layer — ModernAtomic part files + `merge_db.py` merge semantics.**
`merge_db.py` (and `tests/conftest.py`'s sidecar assembly) apply parts in
numeric order with last-writer-wins and no provenance validation, so a
single unprovenanced part silently clobbers validated MTGJSON data for
30 staple cards. The engine, AI, and classifier layers are all correct
relative to the real cards.

## Fix (this PR)

1. Remove `ModernAtomic_part9.json`. The correct, provenanced texts for all
   30 cards are already in parts 1-8; removal restores them.
2. Guard test (red first): every `ModernAtomic_part*.json` must carry
   MTGJSON provenance (`meta.version`), so a future hand-authored override
   part is rejected at test time instead of silently merging.

## Post-fix measurement (honest reporting)

| Matchup | Before (9-part DB) | After (part9 removed) |
|---|---|---|
| Ruby Storm vs Dimir Midrange, n=50 Bo3 | 14% | **16%** (26 sweeps, 23 three-gamers) |
| Ruby Storm vs Boros Energy, n=20 Bo3 | 20% | **30%** |
| Pinnacle Affinity vs Boros Energy, n=20 Bo3 | 30% | **35%** |

The data fix is real and lifts multiple decks (Pinnacle Affinity plays two
of the fabricated cards — Kappa Cannoneer, which part9 stripped of its
Artifact type, and Pinnacle Emissary, which part9 gave a fabricated cost),
and post-fix replays confirm the restored mechanics work (s50000 G2 T6:
`Cast Ral … (1R)`, `won coin flip!`, `transforms!`). But **Storm vs Dimir
itself moves only 14% → 16%** — within noise at n=50. The part9 corruption
was a genuine defect and the biggest single data error in the pool, yet it
is NOT the dominant factor in this specific matchup.

A second, non-obvious payoff: with part9 removed,
`tests/test_wr_baseline_anchor.py` goes **19/19 green on this branch**
(verified under PYTHONHASHSEED 1/42/7 and on the conftest sidecar path with
`ModernAtomic.json` moved aside). The committed anchor fixture was built
against the genuine parts-1-8 data; the "4 drifted entries" that got the
anchor test exiled from CI, and the #451/#454 CI-vs-local divergence, are
this same part9 corruption. No `refresh_wr_baseline.py` run is needed — the
committed snapshot is already correct for the de-corrupted DB.

## Residual gap — turn-level divergence and owning subsystems

Post-fix Bo3 replay, seed 50000 (Dimir 2-0), shows two distinct
decision-layer divergences:

1. **Sub-lethal payoff fired mid-chain, lethal line abandoned** (G2 T6):
   Storm chains 15+ spells, fires Grapeshot at storm 15 → 16 damage into
   19 life (opponent at 3), then *keeps chaining to storm 22* and closes
   with Empty the Warrens (tokens can't attack until next turn) while a
   flashback-able Grapeshot sits in the graveyard behind a resolved Past
   in Flames. Dimir untaps and kills. This is the partial-chain payoff
   math named by the 5-panel audit Unresolved #4 and owned by **PR #454
   (`ai/ev_player.py` finisher-lockout gate)** — not duplicated here.
   Measured on top of #454's branch (local cherry-pick, measurement only):
   see the number recorded below.
2. **Mulligan/hand-evaluation undervalues the engine** (G1, G2): G1 keeps
   a 6 with 2× Past in Flames + Wish and no ritual/cantrip (first play
   T3); G2 mulls to 5 and **bottoms Ral + Pyretic Ritual** — the cost
   reducer and the fuel. Subsystem: mulligan hand scoring (not owned by
   any open sibling PR; candidate for a follow-up diagnostic with its own
   failing test).

With #454's fix cherry-picked locally on top of this branch (measurement
only, their code not committed here): **Ruby Storm vs Dimir n=50 Bo3 =
30%** (avg kill T6.0, kills now as early as T3-T4; 22 sweeps / 28
three-gamers). The two fixes are super-additive — 14% on main, 16% with
the DB fix alone, 12% with #454 alone (their own corrupt-DB measurement),
30% combined — because #454's effective-cost gate can only credit cost
reducers that are actually castable, and part9's fabricated {U}{R} Ral
removed the deck's main reducer from play. Remaining distance to the
35-45% band is the mulligan divergence above.

## Scope of this PR

Data-layer only: remove the corrupt part, pin the provenance invariant
(`tests/test_db_part_provenance.py`, red→green in this diff), record the
diagnosis. The residual Storm-Dimir gap is decision-layer and tracked
against #454 (payoff sequencing) plus a to-be-opened mulligan diagnostic.
