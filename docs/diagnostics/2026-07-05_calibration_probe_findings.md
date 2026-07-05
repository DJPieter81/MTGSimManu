---
title: "Calibration-probe findings — ground-truth matchups expose 4 engine + 2 decision mechanisms"
status: active
priority: primary
session: 2026-07-05
depends_on:
  - docs/history/audits/2026-05-16_5panel_bo3_audit.md
  - docs/diagnostics/2026-05-10_affinity_85pct_opponent_side_root_cause.md
tags: [calibration, probe, rules-engine, karoo, multi-mana, imprint, loyalty, tokens, attack-gate, close-game]
summary: >
  Two ground-truth Bo3 probes (Azorius vs Affinity s60104; Amulet vs
  Dimir s60105) chosen for maximal real-Modern-prior divergence.  One
  match each surfaced six generic mechanisms.  E1 (multi-mana land
  units + karoo ETB return clause) is FIXED in this session's sister
  commits; same-seed re-run flips Amulet's 2-0 loss to a 2-1 win with
  Primeval Titan castable for the first time.  E2/E3/E4 and A1/A2
  remain open with seed-anchored evidence below.
---

# Calibration-probe findings — 2026-07-05

**Method.** Pick matchups whose real-world Modern prior diverges most
from the sim matrix; run one `--bo3 --dump-replay` each; read the log
for rules violations and both-sides decision failures.  The premise:
easy/known matchups are the cheapest detectors of *generic*
architecture bugs.

**Probes.**

| Seed  | Match                        | Matrix says      | Real prior | Result      |
|-------|------------------------------|------------------|-----------|-------------|
| 60104 | Azorius Control vs Affinity  | Affinity 90-95%  | ~45-55%   | Affinity 2-0|
| 60105 | Amulet Titan vs Dimir        | Amulet 17.6% fld | Amulet fav| Dimir 2-0   |

---

## E1 — Multi-mana lands + karoo ETB return clause  ✅ FIXED (this session)

**Evidence (s60105 pre-fix).** `Tap Simic Growth Chamber→G` — one
mana per tap; the "return a land you control" ETB clause never fires
across the Bo3; Primeval Titan drawn twice, never cast.

**Root.** `produces_mana: List[str]` stores only the color union —
the quantity dimension does not exist in the schema, so every land is
worth exactly one mana to the payment solver, the feasibility solver,
and every total-mana estimate.  The return clause was parsed nowhere
as a land property; only a fetch-priority heuristic knew the string.

**Fix (sister commits, this session).**
- `CardTemplate.mana_units: List[List[str]]` — one inner list of
  color options per unit; parsed from the plain `{T}: Add …` line
  (`OracleTextParser.detect_land_mana_units`).  Spend-restricted
  lines ("Spend this mana only …") excluded.
- `tap_lands_for_mana` MRV assignment now operates on (land, unit);
  a land taps once, every unit yields; spare units float to pool.
- `PlayerState.untapped_mana_capacity()` replaces
  `len(untapped_lands)` at 13 total-mana sites (engine + AI).
- `CardTemplate.etb_return_land` flag + uniform land-entry hook
  `LandManager.apply_land_etb_static` (untap watchers, then return
  clause) wired into ALL land-entry paths: land drop, fetch crack,
  mass land search.

**Validation.** Same-seed re-run (s60105): Amulet 2-1 win; karoo
taps log `→GU`; 6 ETB-return events; Titan cast three times (all
countered — a decision-layer matter, see A-family).  n=30 matchup WR
13% → decision layer is now the binding constraint, not rules.
WR-anchor drift: 3/17 entries shifted turn counts only, no winner
flips (baseline refreshed per protocol).

## E2 — Imprint/copy-cast broken  ❌ OPEN (P0)

**Evidence (s60104 G2 T8, upkeep).** "Cast Counterspell (UU)" with an
empty stack, no target, no mana tapped, no Scepter activation cost;
"Isochron Scepter copies Counterspell"; both resolve into nothing.
The exiled imprinted card was cast directly, for free, at an illegal
time, without a legal target (CR 601.2c).

**Subsystem.** `engine/cast_manager.py` cast-legality (target
requirement for counter-type spells; timing) + the imprint/copy
activation path.  Class: every "exile … you may cast a copy" clause.

## E3 — Planeswalker loyalty counters not accrued  ❌ OPEN (P0)

**Evidence (s60104 G2).** Teferi TTR cast T5 (base 4), `+1` logged T6
and T7 → should sit at 6; a single `-3` on T8 → "SBA 704.5p: zero
loyalty", dies.  The +1 activations never changed the counter.

**Subsystem.** `engine/planeswalker_manager.py` / loyalty_counters
persistence.  Class: all planeswalkers.

## E4 — Token cast as a spell  ❌ OPEN (P1)

**Evidence (s60104 G2 T7).** "Cast Construct Token (0)" from hand.
Ties to the unregistered-token detector work (commit 13b6d66).

## A1 — Lethal on board, no attack  ❌ OPEN (P0, decision)

**Evidence (s60104 G2 T7).** Affinity: 20 power on board vs Azorius
at 2 life with zero blockers — "P2 does not attack".  The matrix's
top deck under-attacks; its 79% is *despite* this.  Sibling evidence
s60105 post-fix: Amulet walks Titan into held UU three times with no
bait line (M2-family, combo side).

**Subsystem.** `ai/turn_planner.py` attack enumeration/gating.

## A2 — `close_game` inert (M4 confirmation on fresh seed)  ❌ OPEN (P0)

**Evidence (s60104).** G1 T3: Azorius at 11 vs 3 attackers, 6 cards
in hand, 3 lands — whole turn is a landcycle.  G2 T8 at 2 life:
passes Main 1, casts Teferi post-combat to bounce one artifact.
Matches the 5-panel audit's M4/M3 verbatim.

---

## Institutionalization (next)

1. **Calibration matchup table** — matchup-level EXPECTED bands
   (~10 ground-truth pairs) + `tools/check_calibration.py` run after
   every `--matrix --save`.
2. **Replay linter** — rules-legality pass over the `--dump-replay`
   NDJSON: cast-without-target, cast-in-upkeep, loyalty delta vs
   activations, once-per-walker-per-turn, token-cast, mana-produced
   vs oracle units, expected-ETB-clause-fired.
