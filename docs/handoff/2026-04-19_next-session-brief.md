# Handoff Brief — 2026-04-19 → Next Session (Claude Code)

**Status:** Moving from claude.ai (analysis) to Claude Code (engine surgery). This doc is the context carry-over. Read start-to-finish before touching code.

---

## 1. Where we are

Today we ran 3 Bo3 traces vs Affinity (Boros / Jeskai / Dimir) to diagnose why Affinity sits at 88% sim WR vs ~55% real-world expected. The investigation found **5 engine + AI bugs that co-cause the gap**, not 1 root issue. Root audit doc: `docs/diagnostics/2026-04-19_affinity_investigation.md`. Failed brew log: `docs/experiments/2026-04-19_mardu_energy_failed.md`.

Meta-conclusion (human call, recorded here): **stop patching individual bugs in isolation.** Each fix must land alongside the test that would have caught it. Over time the test helpers graduate into CI-enforced invariants (target-fidelity, conservation, card-parity). This is the "Option C" workflow.

## 2. Operating protocol for every engine fix

```
1. Write failing test first  (demonstrates the bug)
2. Make the fix
3. Full suite green          (`pytest tests/ -q`)
4. Commit test + fix together, single commit
```

**If the test didn't fail before the fix, it's not the right test.** Reject the work, rewrite the test.

After each fix, ask: *does this test helper generalise?* If it's reusable across a class of bugs (e.g. "any spell with a declared target"), graduate it to a shared helper in `tests/invariants/` and mention it in the commit message.

## 3. Bug queue — priority order

Each entry includes: description, evidence, test shape, invariant-candidate.

### ⭐ Bug 1 — Phlage targeting ignored on resolution
- **Effect:** Phlage declares target correctly, log reads `[Target] → Signal Pest`. On resolution, damage routed to face. Target survives.
- **Evidence:** `replays/boros_vs_affinity_bo3.txt:205-207` (G1 T3 Boros)
- **Test:** Board: opp has Ornithopter (0/2). Boros casts Phlage targeting Ornithopter. Assert `Ornithopter in graveyard` OR `Phlage was countered`. Assert `opponent life unchanged from Phlage damage` (life gain from Phlage's gain-life is fine).
- **Invariant candidate:** target-fidelity. *"For every targeted spell/ability that resolves, the declared target must receive the effect."* Generalise this test into `tests/invariants/test_target_fidelity.py`.
- **Expected WR impact:** Boros vs Affinity 24% → ~30-34%. Similar for Jeskai.

### ⭐ Bug 2 — Orim's Chant unkicked behaves as kicked
- **Effect:** Unkicked Chant logs `queues silence for P2's next turn`. Real card only prevents spells on the **current** turn. The AI eagerly casts Chant on own turn because the engine promises a Time Walk the card doesn't provide.
- **Evidence:** `replays/boros_vs_affinity_bo3.txt:146` (G1 T2 Boros)
- **Test:** Boros casts unkicked Orim's Chant targeting opponent on own main phase, passes. Opp untaps, draws, tries to cast any spell. Assert: spell resolves normally (not silenced).
- **Second test:** Boros casts **kicked** Chant. Opp on their next turn: creatures can't attack. Assert that behavior is preserved (regression safety).
- **Invariant candidate:** none yet — but note. This is a kicker-state-propagation bug. Watch for similar issues with other modal/kicker/X-cost spells.
- **Expected WR impact:** Boros vs Affinity +2-3pp. Also fixes Jeskai Blink (uses similar effects).

### ⭐ Bug 3 — `token_maker` tag conflates creature tokens with treasure tokens
- **Effect:** Ragavan (makes treasures on combat damage) and Ajani (ETB creates a 2/1 Cat) both tagged `token_maker` and both get +2 projected power in `_project_spell`. Ragavan's +2 is phantom (treasures are mana, not board; trigger conditional on combat damage). Leads to Ragavan incorrectly scoring +2.4 EV over Ajani.
- **Evidence:** Trace in session (Boros T2 decision: Ragavan +7.5 vs Ajani +5.1). Card template dumps done, oracle text verified.
- **Test:** Build a minimal `EVSnapshot` + two `CardInstance`s for Ragavan and Ajani. Project each via `_project_spell`. Assert: Ajani's projection adds +2 power (ETB cat, guaranteed, immediate). Ragavan's projection adds +0 power from the token_maker bonus unless (has_haste AND opp_creature_count == 0). Treasure tokens should contribute to mana-clock impact, not power.
- **Fix direction:** Replace the binary `token_maker` check in `ai/ev_evaluator.py:_project_spell` with oracle-based branching:
  - `when .+ enters.*create .+ creature` → immediate power bonus (parse size)
  - `whenever .+ deals combat damage.*create.*treasure` → 0 power, small mana-impact bonus
  - `whenever .+ attacks.*create` → conditional; only credit if attacking this turn is likely
  - No hardcoded card names (per repo convention).
- **Invariant candidate:** card-parity. *"Two cards with equivalent oracle-text clauses must produce equivalent projection bonuses."* Enforce via a tag-audit test that groups cards by parsed oracle and asserts tag-assignment parity.
- **Expected WR impact:** Broad. Affects every creature-vs-creature EV comparison across all decks. Most likely to produce surprising re-rankings.

### Bug 4 — Galvanic Discharge targets face when removal target exists
- **Effect:** G1 T1 Boros casts Discharge → face for 3 when Ornithopter (0/2) is on opponent's board. Log even rationalises: `[Target] → face (3 dmg): no clock yet — build pressure`. The targeting heuristic doesn't recognise that Ornithopter is a future Plating target whose removal has outsized value vs Affinity.
- **Evidence:** `replays/boros_vs_affinity_bo3.txt:79-80` (G1 T1 Boros)
- **Test:** Boros has Discharge + 2 mana. Opp has Ornithopter (0/2, 0 power). Affinity archetype tag present. Assert: Discharge targets Ornithopter, not face.
- **Fix direction:** In targeting heuristic, weight artifact creatures higher when opponent is artifact-synergy archetype (oracle-based detection: opp deck has >4 artifacts, or opp has Cranial Plating / Nettlecyst / Mox Opal family).
- **Invariant candidate:** none dedicated; this is AI-layer.
- **Expected WR impact:** +2pp Boros vs Affinity.

### Bug 5 — Cycle doesn't log drawn card
- **Effect:** `T2 P2: Cycle Sojourner's Companion (pay 2 mana, draw a card)` — doesn't name the drawn card. Pieter's #7 confusion: Springleaf Drum "appears from nowhere" on T3.
- **Evidence:** `replays/boros_vs_affinity_bo3.txt:108` (G1 T2 Affinity)
- **Test:** Mock a cycle action. Assert the log output contains the drawn card's name.
- **Fix direction:** One-line change in `commentary_engine.py` (or wherever cycle text is composed). Pattern: match existing `[Draw] P? draws: <name>` format.
- **Invariant candidate:** **conservation invariant**. *"Every change to hand size must be attributed in the log with the specific card name."* Graduate to `tests/invariants/test_log_conservation.py` — cheap to implement, catches a whole class of log-drops.
- **Expected WR impact:** 0 (UX-only); enables faster human debugging.

### Bug 6 — Land-order heuristic for artifact decks
- **Effect:** Affinity T1 plays Spire of Industry (pain/shock) over Darksteel Citadel (free artifact). `_project_land` or `_score_land` uses "colored > colorless" and misses that Darksteel is a free artifact that enables Mox Opal metalcraft.
- **Evidence:** `replays/boros_vs_affinity_bo3.txt:53-58` (G1 T1 Affinity)
- **Test:** Affinity deck, T1, hand contains Spire of Industry + Darksteel Citadel + Ornithopter + Mox Opal. Assert: Darksteel played, not Spire.
- **Fix direction:** Land-scoring heuristic should factor `artifact_count_delta` when archetype is affinity/artifact-synergy. Likely oracle-driven: any land typed `artifact` gets a bonus when Mox Opal / metalcraft / Plating is in the deck.
- **Invariant candidate:** none dedicated.
- **Expected WR impact:** Affinity self-adjusts (the bug currently hurts Affinity slightly — counter-intuitive since we want Affinity to go *down*). Fix the fix anyway: the sim should play correctly on both sides.

### ⏸ Bug 7 (deprioritised) — Engine-piece exposure awareness (Ornithopter T1)
- **Status:** Logged but subjective. Holding Ornithopter T1 to avoid removal is a tempo call, not a rules issue. Revisit after Bugs 1-6.

### Secondary items (logging / gameplan-data, not engine)

**Bug 8 — Affinity doesn't cast Plating on curve pre-equip (gameplan JSON fix, not engine).** Plating should be deployable even without equip mana because it still counts as an artifact (Plating damage scaling, Mox Opal metalcraft, Thought Monitor affinity discount). Edit `decks/gameplans/affinity.json` — add Plating to `CURVE_OUT` `enablers` with a "cast on curve" priority. Test: N=30 sim run, assert avg-turns-Plating-cast < 4.

**Bug 9 — Thought Monitor affinity discount not logged.** Cosmetic. When a cost-reduced spell is cast, log should show `base cost: X, discount: -Y (reason), effective: Z`. Touch: `ai/mana_planner.py` or the cost-paying logger.

## 4. Three invariants to graduate into CI (as they emerge)

Don't build these upfront. Build them when the first bug needs them.

1. **target-fidelity** — emerges from Bug 1. *"Declared target = resolved target effect."*
2. **conservation** — emerges from Bug 5. *"Every state delta is attributed in the log."*
3. **card-parity** — emerges from Bug 3. *"Equivalent oracle clauses produce equivalent tags & projection bonuses."*

Each invariant lives in `tests/invariants/` once graduated, is automatically applied to future cards during test runs, and runs on every CI pass.

## 5. Updated decklist — Boros Energy from MTGTop8 (Rashek, Apr-16-2026)

After Bugs 1-3 are fixed and the matrix is re-baselined, **update the Boros list** in `decks/modern_meta.py` to the Rashek Apr-16 list. URL: https://mtgtop8.com/event?e=83607&d=835037&f=MO

Structural changes from our current list:
- **Orim's Chant moved MB → SB (2 copies in SB only).** Aligns with Bug 2 — our current list had it MB, which combined with the Time Walk bug caused massive phantom value. Tournament list correctly treats it as a SB-only card.
- **+1 Ranger-Captain of Eos MB** — Silence effect, real anti-combo tech.
- **+1 Blood Moon MB** — proactive vs greedy manabases (aligns with H2 hypothesis we tabled).
- **−1 Static Prison** — cut for creature count.
- Replace Flooded Strand (4) with Windswept Heath (4) — different color coverage.
- +1 Dalkovan Encampment (new manland).
- +1 The Legend of Roku MB (recursive threat).

Do this as a separate commit after the engine work lands, **not before** — we want a clean before/after WR comparison on bugs, not mixed with a decklist swap.

## 6. Re-baseline steps after bugs 1-3 land

```bash
# 1. Verify tests green
python -m pytest tests/ -q

# 2. Smoke matchups — same seeds as the diagnostic
python run_meta.py --matchup "Boros Energy" "Affinity" -n 50 -s 50000
python run_meta.py --matchup "Jeskai Blink" "Affinity" -n 50 -s 50500
python run_meta.py --matchup "Dimir Midrange" "Affinity" -n 50 -s 51000

# 3. Expected deltas:
#    Boros vs Affinity: 24% → 30-35% (bugs 1+2 combined)
#    Jeskai vs Affinity: 12% → 22-28%
#    Dimir vs Affinity: 24% → slight gain (Dimir uses less of the bugged cards)
#
#    If WR gains exceed ~10pp, investigate whether we overshot.
#    If WR gains are <2pp, investigate whether fixes are actually engaged.

# 4. Full matrix re-baseline
python run_meta.py --matrix -n 50 --save
python build_dashboard.py --merge

# 5. Commit result + updated dashboard
```

## 7. Known-good context for the next session

- Repo is clean at commit `2fee191`.
- `ModernAtomic.json` is current (21,759 cards).
- `tests/` has 149 tests, all passing as of this commit.
- Memory-scope data is in `/mnt/user-data/` — won't be available in Claude Code's local env; all persistent context is committed to the repo.
- Existing replays committed today (`replays/boros_vs_affinity_*`, `replays/jeskai_vs_affinity_*`, `replays/dimir_vs_affinity_*`) are evidence — don't delete them.

## 8. What's NOT on the queue (intentionally)

- ❌ New brew design. Mardu Energy was tested and falsified today. Don't spend time on new brews until Bugs 1-6 close and the matrix re-baselines.
- ❌ The streamlining toolchain (anomaly report, A/B harness). Deferred until after the engine fixes. Tools built on buggy foundations calibrate to the wrong reality.
- ❌ `card_ev_overrides` infrastructure. Deferred for the same reason — we don't want to override EV that's based on broken projection (Bug 3 must land first).

## 9. Suggested first session opening move

```
Start with Bug 1. Read this brief. Run:
  python -m pytest tests/ -q     (confirm 149 green)
  grep -rn "phlage\|Phlage" engine/ ai/   (locate resolution code)

Then write the failing test. Do not fix until the test fails.
```

## 10. Rotate GitHub token

The token used in the claude.ai chat session should be rotated at https://github.com/settings/tokens before any new work begins. GitHub's secret scanner may have already auto-revoked it.
