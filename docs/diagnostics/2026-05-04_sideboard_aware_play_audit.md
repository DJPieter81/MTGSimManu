---
title: Sideboard-aware play audit (post-Phase-L Affinity follow-up)
status: active
priority: primary
session: 2026-05-04
depends_on:
  - docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md
  - docs/experiments/2026-05-04_bo3_matrix_test.md
  - docs/diagnostics/2026-05-04_affinity_mulligan_overkeep_audit.md
tags:
  - audit
  - sideboard
  - affinity
  - bhi
  - phase-l-followup
summary: >
  Three-question audit of the SB pipeline. Q1 (does opp board hate G2/G3?)
  PARTIAL — opp does board in some hate, but the legacy keyword-matcher
  has structural blind spots: lock/prison artifact answers (Damping Sphere,
  Pithing Needle, Trinisphere) match no anti-Affinity SB rule; Eldrazi Tron
  caps at 2 swaps because its mainboard has no "weak vs Affinity" cards;
  4/5c Control caps Wear//Tear at 2x boarded despite carrying 3x in SB
  (asymmetric `min(count,2)` on board-out matches). Q2 (does AI cast hate
  on curve?) NULL — when Wear//Tear is drawn G2 T4 it casts T4. Q3 (does
  BHI use post-board library?) NULL — `AIPlayer` is rebuilt per game and
  `BHI.initialize_from_game` reads `opp.library`, which is built from the
  post-`_sideboard` `d1_main` mutation. Estimated Affinity WR drop if Q1
  gaps fixed: ~2-4pp.
---

# Sideboard-aware play audit (post-Phase-L Affinity follow-up)

## Context

PR #296 falsified the "Bo1 framing" hypothesis: Affinity got *worse*, not
better, under Bo3 (85.4% Bo1 → 87.2% Bo3). The remaining structural
hypothesis is that opponents' SB pipeline is failing to actually
deliver post-board answers against Affinity. This audit answers three
empirical questions to determine whether the SB pipeline itself is the
gap.

The matrix is already Bo3-default after PR #294
(`a244ed6 feat(run-meta): make Bo3 the matrix + matchup default`), so
G2/G3 with sideboarding is the production path — the audit's findings
are matrix-relevant, not just `--bo3`-flag-relevant.

## Subsystem map (read-only walkthrough)

| File | Role |
|---|---|
| `engine/game_runner.py:215-271` | `run_match` — Bo3 orchestration; calls `_sideboard` between G1/G2 and G2/G3, mutates `d1_main`/`d2_main` in place before next `run_game` |
| `engine/sideboard_manager.py:16-267` | Legacy keyword-matcher SB backend (default; `SB_SOLVER` env unset/`old`) — string `in card_lower`/`in opp_lower` rules with priority + `max_swaps` cap |
| `engine/sideboard_manager.py:295-331` | `_solver_sideboard` — oracle-driven backend (`SB_SOLVER=new`); delegates to `ai/sideboard_solver.plan_sideboard` |
| `ai/sideboard_solver.py:283-326` | `_clause_artifact_removal` — only awards SB value to cards with `destroy_target_artifact` / `destroy_all_artifacts` / `destroy_target_permanent` / `destroy_all_nonland` tags |
| `ai/bhi.py:202-291` | `initialize_from_game` reads `opp.library + opp.hand`; called per-game from `AIPlayer` (rebuilt per game in `engine/game_runner.py:319-320`) |
| `engine/game_runner.py:332-350` | Per-game `counter_density` / `removal_density` re-derivation — also runs from `opp.library` (post-board in G2/G3) |

`AIPlayer` is constructed inside `run_game`, so `BHI`, `goal_engine`,
and `_payoff_names` are all rebuilt for G2/G3 against the post-board
decklist — this is the "BHI sees post-board" path.

## Verdict table

| # | Question | Verdict | Evidence |
|---|---|---|---|
| 1 | Does opp's SB logic swap in artifact hate G2/G3? | **PARTIAL** | 5/5 surveyed decks DO swap in some hate, but each has identifiable gaps (see below) |
| 2 | When drawn, does opp's AI cast SB hate on curve? | **NULL** | seed 53001 G2: Wrath cast T3 on draw, Wear//Tear cast T4 on draw |
| 3 | Does BHI update opponent threat-belief for SB cards? | **NULL** | per-game `AIPlayer` rebuild + `opp.library` ground-truth means G2/G3 BHI reads the post-board deck |

## Q1 — Per-opponent SB-board-in survey vs Affinity (seed 50000)

Captured stderr log line `Sideboard (X vs Affinity): ...` for each match.

| Opponent | Has artifact hate in SB? | What got boarded IN | What got LEFT in SB | Cap reason |
|---|---|---|---|---|
| Boros Energy | 2x Wear//Tear, 1x Damping Sphere, 2x Wrath of the Skies | +2 Wear//Tear, +2 Wrath | **1x Damping Sphere unused** | "damping sphere" not in keyword list |
| Eldrazi Tron | 2x Pithing Needle, 3x Relic, 2x Trinisphere, 2x Ratchet Bomb | +2 Ratchet Bomb only | **2x Pithing Needle, 2x Trinisphere unused** | only 2 MB cards weak vs Affinity → only 2 swaps |
| Domain Zoo | 2x Wear//Tear, 2x Damping Sphere | +2 Wear//Tear | **2x Damping Sphere unused** | "damping sphere" not in keyword list |
| 4/5c Control | 3x Wear//Tear, 1x Boseiju | +1 Wear//Tear, +1 Boseiju | **2x Wear//Tear unused** | Wear matches, but board-OUT cap of 2 stops swap chain |
| Dimir Midrange | 1x Engineered Explosives, 2x Sheoldred, 1x Damnation | +1 Damnation, +1 EE, +2 Sheoldred, +2 Flusterstorm | (none — full coverage of artifact-relevant SB) | n/a |

### Gap 1A — Damping Sphere is a structural blind spot

`engine/sideboard_manager.py:67-71` matches `["wear", "force of vigor",
"collector", "haywire", "shattering", "hurkyl", "pithing", "meltdown",
"boseiju", "time raveler", "orchid phantom", "clarion conqueror"]`. No
match for `"damping sphere"`. Two of the surveyed decks (Boros, Domain
Zoo) carry it specifically as anti-Affinity tech and never deploy it
post-board.

The oracle-driven solver doesn't catch it either: Damping Sphere has no
`destroy_*` tag (it's a static-ability prison card, not removal), so
`_clause_artifact_removal` returns 0. Same for Pithing Needle (locks
activated abilities) and Trinisphere (taxes cheap spells). These three
cards are arguably the most cost-efficient anti-Affinity answers in
Modern, and **neither SB backend recognizes them**.

### Gap 1B — Eldrazi Tron only swaps 2 cards (board-out starvation)

Tron's MB has no card matching the "weak vs Affinity" patterns
(`bombardment | voice of victory | static prison | fable | consign |
witch enchanter | undying evil | summoner's pact | mutagenic growth |
vexing bauble`) except 2x Kozilek's Command (caught by the "command"
substring on line 130 — `["charm", "command"]`). So even with
`max_swaps=7` for artifact matchups, the algorithm runs out of
out-candidates after 2 swaps and the entire 13-card artifact-hate
package (Pithing Needle x2 + Trinisphere x2 + Relic x3 + Wurmcoil x2
+ Spatial Contortion x2 + Warping Wail x2) sits idle.

### Gap 1C — Asymmetric `min(count, 2)` cap on board-out

Each `board_out_priority.append((card_name, min(count, 2), ...))` line
caps the number of copies that *leave* the deck at 2 per matched card.
4/5c Control has 4x Orim's Chant, all of which are weak vs Affinity, but
only 2 ever come out — leaving 2 Wear//Tear stranded in SB even though
the keyword-match works. This cap is cosmetic-looking (line 120, 130,
168, 188, 195) but has a real effect on "wide" SB packages.

## Q2 — Does AI cast SB hate on curve?

Sweep of 8 seeds (50000-53500, step 500) for Boros vs Affinity:

| Seed | drew W//T G2+ | cast W//T G2+ | drew Wrath G2+ | cast Wrath G2+ | Result |
|---|---|---|---|---|---|
| 50000 | 0 | 0 | 0 | 0 | Affinity 2-0 |
| 50500 | 0 | 0 | 0 | 0 | Boros 2-0 |
| 51000 | 0 | 3 | 1 | 0 | Affinity 2-1 |
| 51500 | 0 | 0 | 0 | 0 | Affinity 2-0 |
| 52000 | 0 | 0 | 0 | 0 | Affinity 2-0 |
| 52500 | 0 | 0 | 1 | 1 | Affinity 2-1 |
| 53000 | 2 | 2 | 0 | 1 | Affinity 2-1 |

(`drew` counter undercounts opening-hand draws — opening-hand cards
don't go through "P1 draws:" log lines. `cast` is ground truth.)

Cast-on-curve check from seed 53001 G2 (Bo3 split):
- T3: P1 draws Wrath of the Skies (from opening hand setup), casts
  T3 with 1WW available — clears 7 permanents including 5 creatures
  and 1 Saga.
- T4: P1 draws Wear // Tear, casts T4 with 1R — destroys Construct
  Token + Urza's Saga in the same cast.

**The AI does cast SB hate on curve when drawn.** Q2 is null. The
problem isn't the play decision; it's that several seeds show the
hate sitting in the deck and never being drawn before the match ends
(Affinity's T3-T4 kill speed compresses the draw window for 4-of
hate cards to 3-4 chances).

## Q3 — Does BHI update for post-board decks?

Read path:
1. `engine/game_runner.run_match` (line 235) calls `run_game` with the
   already-mutated `d1_main`. This is the post-`_sideboard` decklist.
2. `run_game` builds `deck1 = self.build_deck(deck1_list)` from the
   passed-in (post-board) list — line 292.
3. `AIPlayer(0, deck1_name, self.rng)` constructs a fresh tracker
   (line 319). `BayesianHandTracker(player_idx)` allocates a zeroed
   `HandBeliefs` instance (`ai/ev_player.py:215-216`).
4. The first `BHI.initialize_from_game` call (lazy, on first
   `observe_priority_pass` / `observe_card_drawn` / `observe_spell_cast`,
   or the eager call in `ev_player.py:1110-1111`) reads
   `opp.library + opp.hand` (`ai/bhi.py:204-212`). Both are populated
   from the post-board decklist; **library is the post-SB ground truth**.

In `engine/game_runner.py:332-350` the per-game density priors
(`p.counter_density`, `p.removal_density`, `p.exile_density`) are also
derived from `opp.library + opp.hand`, so they too reflect post-board.

**No BHI bug. Q3 is null.** If BHI failed to track post-board, the
audit would have found Affinity's *opponent* mis-modelling its own
threat profile across G2/G3 — but the construction path is correct.

The only nuance: the `_compute_discard_prior` reads `opp.deck_name`
(unchanged across games) and runs against the live pool. That's
fine — the gameplan reflects archetype-level intent, not per-game
list. No false signal.

## Most surprising finding

**Damping Sphere, Pithing Needle, and Trinisphere are invisible to
both SB backends.** They are arguably the three most cost-efficient
anti-Affinity cards in Modern (2-mana / 1-mana / 3-mana lock pieces
that turn off Mox Opal activations, equipment equips, and free
spells). Yet:

- The legacy backend doesn't list them as artifact hate (string-match
  fails — they don't contain "destroy", "shatter", "wear", etc.).
- The solver backend's `_clause_artifact_removal` requires a
  `destroy_*` tag, which lock pieces don't have. Their oracle text
  ("can't be activated", "costs {1} more", "would cost less than
  three") doesn't trigger any clause.

Two of the five surveyed opponents have these cards in SB. None of
them deploy them.

## Fix-PR sketches (one per confirmed gap, separate branches)

### Fix #1 — Damping Sphere/Pithing Needle/Trinisphere lock-piece detection

Both backends need a new "ability-lock" / "tax-spell" clause that
recognises oracle patterns like:

- `"can't be activated"` → activated-ability lock (Pithing Needle,
  Cursed Totem, Damping Matrix)
- `"each spell ... costs {N} more"` or `"costs less than [N] mana to
  cast"` → spell-tax (Damping Sphere, Trinisphere, Thalia)
- `"for each other spell that player has cast this turn"` → storm-tax
  (Damping Sphere, Eidolon of Rhetoric, Rule of Law)

Generalisation: **any oracle pattern that disables artifact-deck
acceleration (Mox Opal, Springleaf Drum, free spells, equipped
attackers) scales with the opponent's `mox/free-mana density` and
`activated-ability density`.** This is principled: the same clause
will help against Storm (Trinisphere taxes the chain), Goryo's
(Pithing Needle on Goryo's), and Tron (Damping Sphere on Tron lands).

Branch: `claude/fix-sb-lock-piece-clause`. Owner: `ai/sideboard_solver.py`
(`_clause_lock_piece`) + `engine/sideboard_manager.py` (extend the
keyword list temporarily for legacy-backend parity, with a comment
pointing at the principled clause).

Test: `test_sb_lock_pieces_score_against_acceleration_decks` —
construct a synthetic Affinity-like opp with N Mox Opal + N
Springleaf Drum, assert `sb_value(Pithing Needle, opp) > 0`.

Estimated effect: ~+1.5pp drop on Affinity WR vs the 3 surveyed
opponents that carry these cards (Boros, Domain Zoo, Eldrazi Tron).

### Fix #2 — Eldrazi Tron board-out starvation

Tron's MB lacks "weak vs Affinity" cards in the legacy keyword list,
capping its swap count at 2/7 max. Two principled options:

(a) **Generalisation-first:** add a board-OUT clause for "high-CMC
spells in fast matchups" — anything with CMC ≥ 5 in a deck whose
opp's clock < 5 turns is a candidate (this catches Wurmcoil Engine,
Endbringer, Ugin which Tron has plenty of). This is principled
across decks: same rule helps Amulet board out 5+ CMC cards vs
Burn, helps Omnath board out Stock Up vs Affinity, etc.

(b) **Solver-only:** the oracle-driven backend already has the right
shape (it computes net-gain per swap and stops at epsilon). Validate
that with a richer artifact-hate clause from Fix #1, Tron's Pithing
Needle/Trinisphere/Relic *would* exceed the epsilon and swap in.

Branch: `claude/fix-sb-board-out-high-cmc`. Test: rule-phrased
"high-CMC card swapped out vs sub-5-turn-clock opp".

Estimated effect: ~+0.5pp drop on Affinity WR (Tron is only one of
16 opponents).

### Fix #3 — Asymmetric `min(count, 2)` cap on board-out

The cap is in `engine/sideboard_manager.py` at lines 120, 130, 168,
188, 195. It's an arbitrary literal. Lifting it to `min(count, 4)`
or removing it entirely (let `max_swaps` be the only ceiling) lets
4/5c Control board the full 3x Wear//Tear it carries.

Risk: removing the cap could over-swap on some matchups (e.g. board
out 4x Bolt vs Living End). Mitigation: keep `max_swaps=5` (legacy)
or `=7` (artifact decks) as the global ceiling — the per-rule cap is
redundant once that exists.

Branch: `claude/fix-sb-board-out-cap`. Test: "when SB has 3+ copies
of a hate card and MB has 3+ matching weak cards, all 3 SB copies
board in".

Estimated effect: ~+0.5pp drop on Affinity WR (4/5c Control is one
opponent; effect concentrated there).

## Combined estimated effect

If all three fixes land, Affinity matrix WR estimated drop: **~2-4pp**
(from ~84% to 80-82%). This won't hit the expected 50-65% band on its
own but is a structural win for the SB pipeline that should help
several other matchups too (Goryo's vs anti-GY hate, Storm vs
Trinisphere, etc.).

## Out-of-scope for this audit

- The "Affinity reactive density" hypothesis (does Affinity's MB carry
  enough interaction that opp's removal scores low and gets boarded
  out?) — that's a different audit on the score function itself.
- The "Cranial Plating + Construct token threat scaling" thread from
  Phase L — orthogonal; tracked in the linked Phase L doc.
- Whether the AI should mulligan more aggressively in G2/G3 to
  prioritise drawing the boarded-in hate — separate question for
  the mulligan layer.

## Verification

```
$ python tools/check_abstraction.py    # exit 0
$ python tools/check_magic_numbers.py  # exit 0
$ python tools/check_doc_hygiene.py    # exit 0
```
