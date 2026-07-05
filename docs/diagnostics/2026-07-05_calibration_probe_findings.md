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

## E2 — Imprint/copy-cast broken  ✅ FIXED (branch claude/e2-imprint-copy)

**Evidence (s60104 G2 T8, upkeep).** "Cast Counterspell (UU)" with an
empty stack, no target, no mana tapped, no Scepter activation cost;
"Isochron Scepter copies Counterspell"; both resolve into nothing.
The exiled imprinted card was cast directly, for free, at an illegal
time, without a legal target (CR 601.2c).

**Root (verified in code).** `game_runner._process_upkeep_activations`
auto-fired every upkeep with no target check, cast the imprinted card
ITSELF from exile (exile→graveyard after one use — lock destroyed),
and blind-tapped two lands.  Fixed: main-phase
`_process_imprint_copy_activations` with a CR 601.2c target gate,
true copy (CR 707.10a — copies cease to exist on resolution, new
generic rule in spell_resolution), payment via tap_lands_for_mana.
Follow-up open: reactive counter-copy via `ai/response.py`.

## E3 — Planeswalker loyalty counters not accrued  ❌ FALSIFIED

**Tracer result (live s60104 re-run, descriptor on every
loyalty_counters write).** Counters track perfectly: enters 4 → −3
(ETB turn) → +1 → +1 → −3 → 0 → SBA death.  Every step CR-legal
(one activation per turn, cost ≤ loyalty, dying at exactly 0 is
legal).  The original log read missed the ETB-turn −3.

**Reclassified (decision layer, M4-family):** the CHOICE quality is
the issue — `game_runner._choose_pw_ability` minused a fresh walker
to 1, then later suicided it at 3 loyalty for a single bounce.
Belongs to the planeswalker-EV / close_game work, not the engine.

**Side finding (dormant):** `engine/stack.py::Stack._resolve_spell`
is a parallel legacy resolver that skips `_handle_permanent_etb`
(walkers would enter at 0 loyalty).  The live runner never calls it
(canonical path is `ResolutionManager.resolve_stack`), but any test
or tool driving `stack.resolve_top` directly gets wrong state —
candidate for deletion in the resolver-unification work.

## E4 — Token cast as a spell  ✅ FIXED (branch claude/e4-tokens-cease)

**Root chain (verified).** T5 "Teferi bounces Construct Token" →
token to HAND → T7 cast.  SBA 704.5f existed in `sba_manager.py` but
filtered on an `is_token` flag nothing ever set — AND the live SBA
path (`game_state.check_state_based_actions`) never delegates to
SBAManager (docstring lies; `check_and_perform_loop` has zero
callers).  Fixed: `CardInstance.is_token` set by the creation funnel;
704.5f extracted to `SBAManager.perform_token_cleanup` (one
implementation, both callers); CR 111.2 cast gates in
`can_cast`/`cast_spell`.

**Second duplicate-subsystem finding:** SBAManager mirrors the
`stack.py` legacy-resolver situation — full parallel implementation,
zero live callers, rules that only exist in the dead copy.  Two data
points now; resolver/SBA unification should be a named proposal.

**Fidelity gap noted (separate, open):** Urza's-Saga-pattern Ch.II
auto-creates the token instead of granting the '{2},{T}: create'
activated ability (s60104 G1 T3).  Class: ability-granting sagas.

## A1 — Lethal on board, no attack  ❌ RETRACTED (s60104 evidence)

**Correction.** Supreme Verdict wiped Affinity's board on Azorius's
T7; all three attackers entered on Affinity's T7 → summoning sick →
"does not attack" is CORRECT.  No confirmed reproducer for A1
remains; the 5-panel M12 (chump-block) is a separate, still-open
mechanism.  What stands from s60105 post-E1: Amulet jams Titan into
held UU three times with no bait line — M2-family (combo side),
tracked there.

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
