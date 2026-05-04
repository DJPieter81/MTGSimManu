---
title: Phase L — Affinity AI-side audit (post-Phase-K decklist fix)
status: active
priority: primary
session: 2026-05-04
depends_on:
  - docs/diagnostics/2026-05-04_affinity_overperformance_audit.md
  - docs/diagnostics/2026-05-04_phase-k-summary.md
  - docs/design/2026-05-04_modern_combo_audit_methodology.md
tags:
  - audit
  - affinity
  - ai-scoring
  - phase-l
summary: >
  Inversion of Phase K's opponent-side audit: tests whether Affinity AI
  (or the engine) is over-rewarding plays a real-world player wouldn't
  pull off. 6 substantive findings: 1 Class A (Nettlecyst's "and/or
  enchantment" clause unscored), 1 Class A/F (T1 Plating cast paid
  with apparent 1 mana when CMC=2 — needs deeper engine repro), 1
  Class D (Affinity / Amulet substring deck-name gates in mulligan,
  invisible to current ratchet), 2 Class E (artifact-count includes
  artifact lands → linear position_value bonus inflated; Saga-III
  hardcoded tutor priority dict scoring engine layer), 1 Class C
  (engine scoring decision in Saga-III tutor — should live in AI). No
  Class B/F/G/I findings. Estimated additional WR drop from the most
  load-bearing fix (artifact_count excludes lands): ~5–8pp on
  Affinity, partially offset by Pinnacle Affinity benefiting too.

  Bottom line: Phase K's "Affinity AI is well-calibrated, only
  opponent decklists are at fault" claim is partially right but
  understates the engine/AI surface area. Two of the findings (Class
  E artifact-count, and Class D mulligan gates) materially affect
  Affinity's win rate in a direction that supports the
  overperformance hypothesis. The other findings are correctness
  issues that don't substantially move the meter alone.
---

# Phase L — Affinity AI-side audit

## Context

- Phase K (`docs/diagnostics/2026-05-04_affinity_overperformance_audit.md`) classified Affinity's 84% sim WR as 9 Class H findings on opponent decklists (MB artifact-hate density too low). Filed PR-K4..PR-K12.
- Smoke post-PR #288: Boros vs Affinity 10% → 30%. The user's hypothesis: decklists alone don't bring Affinity into the expected 50–60% band. There's also AI/engine bias on the Affinity side.
- This audit applies the same 9-question methodology, but turning the lens around: where does the simulator **give Affinity credit** that a real player wouldn't earn?

## Q1 — Card data (Class A, Affinity-side)

Verified each non-land card via `ModernAtomic_part*.json` cross-check.

- **Mox Opal** — `cmc=0`, `produces_mana=[]` (dynamic via metalcraft). The grandfathered `card.template.name == "Mox Opal"` ratchet exception in `engine/mana_payment.py:85` correctly gates on `artifact_count >= METALCRAFT_THRESHOLD (3)`. Clean.
- **Cranial Plating** — `cmc=2`, `equip_cost=1`. Oracle parsed by `engine/oracle_parser.py:436 parse_equip_cost` correctly extracts the `Equip {1}` cost. The alternate ability `{B}{B}: Attach Cranial Plating to target creature you control` (the iconic Affinity flash-equip in combat) is **NOT implemented as a separate instant-speed attach path**. `engine/game_state.py:317 equip_creature` is sorcery-speed only (called from main phase). **This makes Affinity slightly UNDERperform**, not over — combat-step Plating swings are a known Affinity edge play that the sim never executes. Listed for completeness; not a driver of overperformance.
- **Springleaf Drum** — oracle is "Add one mana of any color" (no creature-color restriction). Engine's `springleaf_drum_etb` populates `produces_mana=["W","U","B","R","G"]`. Rules-correct.
- **Nettlecyst** — oracle is "+1/+1 for each artifact **and/or enchantment** you control." Engine's `_dynamic_base_power` (`engine/cards.py:378`) and `_dynamic_base_toughness` (`engine/cards.py:423`) call `_get_artifact_count`, which counts **only artifacts**. Enchantments are silently dropped. **Class A finding** — but for Affinity (zero enchantments mainboard), this is a wash. Affects future variants (Pinnacle Affinity if it ever splashes Saheeli/enchantments). Tracked but not a driver.
- **Sojourner's Companion / Frogmite / Thought Monitor / Myr Enforcer** — affinity-cost-reduction implemented in `engine/mana_payment.py:166-172` (`reduction += artifact_count`). Rules-correct. Cost solver in `engine/cast_manager.py:174-179` mirrors it for `can_cast`.
- **Construct Token (Saga II)** — oracle says "0/0 colorless Construct artifact creature token with 'This token gets +1/+1 for each artifact you control.'" The token's static ability scales **continuously** (CR 611 — characteristic-defining static abilities are continuous, not one-shot). Engine's `_dynamic_base_power` regex matches `+\d+/\+\d+ for each artifact you control` and adds `_get_artifact_count()` dynamically. Continuous, rules-correct. (The audit prompt's claim that the CR specifies one-shot is incorrect — verified.)
- **Steel Overseer** — not in Affinity's list. Skip.

### A-1 (potential, needs engine repro)

`run_meta.py --verbose Affinity Boros -s 50000` shows T1 Affinity casts Mox Opal then Cranial Plating with only Urza's Saga (1 colorless mana, untapped) as a mana source. Standalone reproduction (`engine.cast_manager.CastManager.can_cast` with the exact state Saga + Mox Opal + Plating-in-hand) correctly returns `False`. The full-game cast nonetheless succeeds in the visible log. **Either the verbose log under-reports tap events, or there's a code path through which Plating is being cast for fewer than 2 mana.** Either way the user-visible artifact (Plating on board T1 with apparent under-payment) merits a focused repro — could be a Class F rule violation. Filed as `phase-l-A1`; needs a 5-minute targeted test before claiming a real bug.

**Verdict:** 1 confirmed minor (Nettlecyst enchantment scaling), 1 potential (T1 Plating cast). Net Class A driver of overperformance: small.

## Q2 — Tier-1 conformance (Class B, Affinity-side)

Affinity decklist (`decks/modern_meta.py:189-222`) matches mtgtop8 April 2026 canonical lists within 1-of variance. Phase K verified this. **Verdict: 0 Class B findings.**

## Q3 — Strategy/preamble interaction (Class C, Affinity-side)

`decks/gameplans/affinity.json`:
- `mulligan_min_lands=2`, `mulligan_max_lands=3`, `mulligan_keys=["Cranial Plating", "Memnite", "Mox Opal", "Ornithopter", "Springleaf Drum"]`. Tight curve_out plan, T1-T3 deploy.
- Trace evidence (`run_meta.py --trace Affinity Boros -s 50000`): Affinity plays Mox Opal T1 even with 0/2 metalcraft, then Plating T1 (potentially Class A). T3 cycles Sojourner's for landcycling. T5 Saga II Construct Token (4/4). T7 wins.

Two observations:

- **C-1 (linked to Class E below):** The AI doesn't differentiate "I have Plating in hand and a board with no carrier" from "I have Plating + 3 carriers." Per the design doc (`docs/design/ev_correctness_overhaul.md` Bug B), this was supposed to be addressed by `_has_equipment_carrier_and_mana` — the gate IS in `_enumerate_this_turn_signals`. Confirmed wired: line 734-736 of `ai/ev_evaluator.py`. So this is fine.
- **C-2 (engine layer scoring choice):** `engine/game_runner.py:1335` hardcodes `tutor_priority = {"Cranial Plating": 10, ..., "Mox Opal": 8, ...}` for Saga III's "search library for an artifact card with mana cost {0} or {1}." This is **a strategic decision in the engine layer** that the convention "engine enforces rules; AI makes choices" forbids. It also encodes a debatable preference (Plating > Mox Opal regardless of game state). Real Affinity AI would prefer Mox Opal early (mana acceleration) and Plating later (when carriers exist).

  **This is hidden from the abstraction-contract ratchet** because the lookup is `dict.get(c.name, 1)`, not `c.name == "X"` or `c.name in {...}`. The ratchet's AST visitor doesn't catch dict-lookup-by-name as a name conditional. A Class D-adjacent ratchet hole.

**Verdict:** 1 Class C/D finding (Saga-III tutor scoring belongs in `ai/`). The dict's preference order is debatable but probably a small WR mover — Saga III only fires on T4+, by which point the deck is already curving out.

## Q4 — Single-deck gates (Class D, Affinity-side)

`grep "active_deck\|deck_name" ai/ engine/` finds 2 hits matching the Affinity name, both in `ai/mulligan.py`:

```python
# line 148 — Affinity 0-land mulligan exception
if "affinity" in deck_name.lower():
    ...
# line 165 — Amulet flood mulligan exception
if "amulet" not in deck_name.lower():
    ...
```

**Phase K's audit reported "Q4: 0 hits" — this is wrong.** The current `tools/check_abstraction.py` deck-gate detector (`DeckGateVisitor.visit_Compare`) has a blind spot:

- Pattern 3 in the visitor (`<deck-name>` in `<expr>.deck_name`) requires `_matches_known_deck(left.value)`, which checks `value in self.known_decks` for an EXACT-CASE match against canonical names ("Affinity", "Amulet Titan").
- The lowercase substring check `"affinity" in deck_name.lower()` is structurally `"<lower-name>" in <method_call>` — `_looks_like_deck_ref` doesn't recognise method calls. So the visitor never reaches `_matches_known_deck`.

**Net:** 2 Class D gates exist; the ratchet is silently passing them. The ratchet is the thing to fix here, not the gates themselves (which are reasonable narrow exceptions for the 0-land / flood corner cases). Both are MULLIGAN-side, not main-phase scoring — they don't drive overperformance directly, but they are abstraction-contract violations that should be visible to the ratchet.

**Verdict:** 1 Class D finding — the abstraction ratchet has a blind spot for `"<lit>" in <expr>.lower()` substring patterns. Filed as `phase-l-D1`.

## Q5 — Heuristic cardinality (Class E, Affinity-side) — **the most important section**

### E-1: `my_artifact_count` includes artifact lands

`ai/ev_evaluator.py:287-290` populates `snap.my_artifact_count` by iterating `me.battlefield`, which **includes lands**. Affinity's typical deployed board has:

- 4 Darksteel Citadel + 8 Bridges + 2 Treasure Vault + 3 Spire of Industry + 4 Urza's Saga = up to 21 artifact lands available
- Plus 3-4 artifact creatures + 1-2 Mox Opal + Springleaf Drum + Plating

A typical T4 board for Affinity has ~7 artifacts (lands + Mox + creatures + Plating). The opponent has 0–2.

`ai/clock.py:444` then computes:

```python
artifact_value = (artifact_diff * mana_clock_impact(snap)
                  * CLOCK_IMPACT_LIFE_SCALING)
```

With `mana_clock_impact = 1.0 / opp_life` and `CLOCK_IMPACT_LIFE_SCALING = 20.0`:
- Early game (opp_life=20): each marginal artifact ≈ +1 life-point of position value.
- Mid-game (opp_life=10): each marginal artifact ≈ +2 life-points.
- Late game (opp_life=5): each marginal artifact ≈ +4 life-points.

So Affinity's `position_value` carries a +7 to +14 to +28 life-point bonus over a typical game, gated only on `_has_artifact_scaling_card` (which fires the moment any artifact-scaling oracle is in hand or BF — i.e., basically every turn for Affinity). This is BEFORE the actual game value of the lands themselves (which is captured separately via `mana_value`).

**The design doc (`docs/design/ev_correctness_overhaul.md`) §4 says** "A card that says '+1/+0 for each artifact' means each marginal artifact = +1 power." That's the implementation intent — a *power-equivalent* bonus per artifact. But:

1. The bonus is INDISCRIMINATE — it counts all artifacts, lands included. A real Plating-with-3-Bridges board is +3 power per attached creature (worth ~6 in `creature_threat_value` for one carrier). `position_value` adds ~3 *additional* artifact_value-life-points on top of that — double-counting.
2. The bonus doesn't decay with redundancy. Going from 5 to 6 artifacts (adding one more Bridge land) doesn't give you another +1 power — the Plating bonus on the existing carrier is already capped by the carrier's blockability. But `position_value` adds another +1 life-point per land regardless.
3. Lands are already scored by `mana_value` (line 408). Adding `artifact_value` for the same lands is partial double-counting.

**Severity:** the per-card magnitude is ~1 life-point per artifact land, growing late-game. Across a 6-turn Affinity game with 4-7 artifact lands, this adds 4–28 life-points of phantom advantage to `position_value`. Given that `position_value` drives EV-scoring downstream (and lethal triggers at `position_value ≈ 100`), this is a 5-25% over-rating bias.

**Recommended fix (Class E, principled):** in `ai/ev_evaluator.py:287-290`, exclude lands from `snap.my_artifact_count`/`snap.opp_artifact_count`. The lands' artifact-typed status is already valuable for metalcraft activation (which fires the scaling-active flag), but each individual artifact land beyond the threshold shouldn't add another +1-power-equivalent. One-line change:

```python
if CardType.ARTIFACT in types and not c.template.is_land:
    snap.my_artifact_count += 1
```

This survives the abstraction contract (no card names, no thresholds added) and matches the design intent of §4. **Pair with a regression test** asserting that `position_value(snap)` for Affinity with 4 Bridges + Plating + 1 carrier is materially lower than for Affinity with 4 Bridges + Plating + 1 carrier + 4 extra Bridges.

**Estimated WR impact:** -5pp to -8pp on Affinity. Bridges/Citadel are the deck's biggest land density; cutting their artifact_value contribution roughly halves the inflated bonus. Pinnacle Affinity will see a similar drop.

### E-2: ARTIFACT_LAND_SYNERGY_BONUS at land-play time

`ai/ev_player.py:1572-1596` adds `synergy_signals × 4.0` to the EV of playing any artifact land, where `synergy_signals` counts oracle-text matches in hand+BF for `'for each artifact'`, `'metalcraft'`, `'affinity for artifacts'`. With 8+ synergy cards in a typical Affinity hand, every artifact land scores +32 EV bonus over a non-artifact land of equivalent mana. This is a per-LAND-PLAY bias, not a board-state evaluation bias, but it nudges the AI toward fast curve-out. Probably a small contributor to Affinity playing optimally (which a human can also do); not a big over-scorer.

### E-3: Construct Token power amplification check

The Construct token regex match (`+\d+/\+\d+ for each artifact you control`) DOES match the saga's token (which has continuous "+1/+1 for each artifact" static). `_get_artifact_count` is called from `_dynamic_base_power` and the token's stats grow with the BF artifact count. **Rules-correct, includes lands** (which also matches MTG rules — "artifact you control" includes artifact lands). So this is not a bug per se. But note: same lands-included effect as E-1, just at the token-stat level — doubly amplifies.

**Verdict:** **2 substantive Class E findings (E-1, E-2)**. E-1 is the most likely smoking gun. **E-1 alone could explain 5–8pp of overperformance.**

## Q6 — Rule strictness (Class F, Affinity-side)

- Equip {1}: parsed correctly. Sorcery-speed only — no flash-equip {B}{B} path (Class A noted above; this UNDERrates Affinity, not over).
- Affinity for artifacts: cost reduction stacks with itself in both `engine/cast_manager.py:174-179` (can_cast) and `engine/mana_payment.py:166-172` (tap_lands_for_mana). Multiple affinity creatures don't double-discount because each calls `count_cost_reducers` once per spell, not per affinity creature. Rules-correct.
- Construct token: continuous static, not one-shot. Verified in `engine/cards.py:378`.
- Metalcraft (Mox Opal): dynamic re-evaluation at activation time, not snapshot at ETB. Bug fix history in `engine/card_effects.py:310-328` confirms.

**Verdict:** 0 substantive Class F findings (the A-1 T1-Plating-cast question is queued under Class A pending a focused repro; could end up being Class F).

## Q7 — Fetch validity (Class G, Affinity-side)

Affinity runs no traditional fetchlands. **Verdict:** 0 findings.

## Q8 — Bo1 hate-card density (Class H, Affinity-side, inverted-of-inverted)

This was Phase K's territory. Affinity's own mainboard runs zero artifact-hate-self-mirror; relies on SB Hurkyl's Recall + Haywire Mite vs the mirror. Real-world Affinity also runs mirror-targeting hate in SB only. Matches canonical lists. **Verdict:** 0 findings.

## Q9 — Hand-rolled cantrip resolution (Class I)

`grep -rn "player.draw(1)\|drawCard" decks/ ai/ --include='*.py'` returns 0 hits relevant to Affinity. **Verdict:** 0 findings.

## Summary table

| Class | Count | Severity (toward overperformance) | Actionable |
|---|---|---|---|
| A — Card data | 1 confirmed (Nettlecyst) + 1 pending (T1 Plating cast) | Low (Nettlecyst irrelevant for Affinity-MB; T1 Plating could be high if confirmed) | Yes (file PR for Nettlecyst; repro for T1 Plating) |
| B — Decklist | 0 | n/a | No |
| C — Strategy/preamble | 1 (Saga III tutor scoring in engine layer) | Low | Yes (lift to AI layer) |
| D — Single-deck gates | 1 (ratchet detector blind spot for `"<lit>" in <expr>.lower()`) | n/a (gates are narrow & defensible; the bug is the ratchet) | Yes (improve detector) |
| E — Heuristic cardinality | **2 (artifact_count includes lands; artifact-land synergy bonus)** | **High (E-1: 5–8pp on Affinity)** | **Yes — primary fix** |
| F — Rule strictness | 0 (modulo A-1) | n/a | No |
| G — Fetch validity | 0 | n/a | No |
| H — Bo1 hate density | 0 (covered by Phase K on opponents) | n/a | No |
| I — Hand-rolled cantrips | 0 | n/a | No |

## Top 3 most likely AI-overscoring sites (smoking guns)

1. **E-1: artifact_count includes artifact lands.** `ai/ev_evaluator.py:287-290`. Linear position_value bonus on every artifact land. Typical Affinity deploys 4-7 artifact lands; each contributes 1–4 life-points of phantom advantage to `position_value`. **Estimated impact: -5pp to -8pp on Affinity post-fix.**
2. **E-2: ARTIFACT_LAND_SYNERGY_BONUS at land-play time.** `ai/ev_player.py:1594-1596`. Adds `synergy_signals × 4.0` per artifact land considered. Smaller magnitude than E-1; primarily nudges land-choice (Affinity prefers Bridge over Spire of Industry early, which is already correct). **Estimated impact: -1pp to -2pp post-fix.**
3. **C/D: Saga III tutor priority dict.** `engine/game_runner.py:1335`. Hardcoded card-name preferences in engine layer. Both an abstraction-contract violation (engine making strategic choices) AND a hardcoded card-name table (hidden from the ratchet because it's `dict.get`). **Estimated impact: ~0pp on overperformance, but a structural improvement.**

## Recommended fix PRs

| PR | Class | Branch | Change | Test |
|---|---|---|---|---|
| **PR-L1** | E-1 | `claude/fix-classE-artifact-count-excludes-lands` | `ai/ev_evaluator.py`: exclude lands from `my_artifact_count` / `opp_artifact_count` | `tests/test_artifact_count_excludes_lands.py` — assert position_value(Affinity with 4 Bridges) ≈ position_value(Affinity with 4 Bridges replaced by 4 basic lands) up to mana_value contribution |
| **PR-L2** | A (Nettlecyst) | `claude/fix-classA-nettlecyst-counts-enchantments` | `engine/cards.py`: equipment scaling reads "and/or enchantment" clause and adds enchantment count | `tests/test_nettlecyst_enchantment_scaling.py` — assert Nettlecyst on a creature with 2 artifacts + 2 enchantments → +4/+4 |
| **PR-L3** | C/D | `claude/fix-classC-saga-tutor-lifted-to-ai` | Move Saga III tutor selection to AI callback (`game.callbacks.choose_artifact_tutor_target`) with default = highest-CMC eligible. Engine still enforces "must be CMC ≤ 1, must be artifact." | `tests/test_saga_iii_tutor_uses_ai_callback.py` |
| **PR-L4** | A-1 | `claude/diag-phase-l-A1-t1-plating-cast` | (Investigation only — write a failing test that reproduces the apparent under-payment. If it reproduces a real engine bug, fix in a separate PR; if not, add a regression test pinning the correct behavior.) | `tests/test_t1_plating_requires_two_mana.py` |
| **PR-L5** | D (ratchet) | `claude/fix-ratchet-deck-gate-substring-detection` | Extend `tools/check_abstraction.py`'s `DeckGateVisitor` to match `<str-lit> in <expr>.lower()` patterns | Update `tools/abstraction_baseline.json` to reflect the 2 newly-visible gates (and add `# abstraction-allow:` if they're judged defensible) |

## Best estimate of additional WR drop on Affinity beyond Phase K decklist edits

- After PR-K4..PR-K12 (Phase K decklist edits): Affinity drops 84% → 65–70% (Phase K's projection).
- After PR-L1 (Class E artifact-count): an additional **-5pp to -8pp** → 60–65%.
- After PR-L2 + PR-L3 + PR-L4 + PR-L5: ~**-1pp to -2pp** combined (mostly correctness, not overperformance drivers).

**Net post-Phase-L Affinity WR: 58–62%.** That's at the upper edge of the expected band (50–60%). If still high, the residual is most likely:

- The Bo1-vs-Bo3 default (matrix runs Bo1; Bo3 SBs add hate). Phase K's summary recommendation to switch matrix to Bo3 is still the structural fix.
- Affinity AI is genuinely well-curved; real Modern Affinity is also a top deck.

## If the audit found NOTHING substantive

It found something. **E-1 (artifact_count includes lands)** is the smoking gun. Recommend dispatching PR-L1 first; if WR doesn't move 3+pp from that change alone, escalate to "switch matrix default to Bo3" as Phase K already proposed.

## References

- Phase K Affinity audit: `docs/diagnostics/2026-05-04_affinity_overperformance_audit.md`
- Phase K summary: `docs/diagnostics/2026-05-04_phase-k-summary.md`
- Methodology: `docs/design/2026-05-04_modern_combo_audit_methodology.md`
- Design doc on artifact_count: `docs/design/ev_correctness_overhaul.md` §4
- Affinity decklist: `decks/modern_meta.py:189-222`
- Affinity gameplan: `decks/gameplans/affinity.json`
- Key code sites:
  - `ai/ev_evaluator.py:287-290` — artifact_count populator (E-1)
  - `ai/clock.py:435-445` — artifact_value formula (E-1 consumer)
  - `ai/ev_player.py:1594-1596` — ARTIFACT_LAND_SYNERGY_BONUS (E-2)
  - `engine/cards.py:378, 423` — Construct token / Plating power scaling
  - `engine/game_runner.py:1335` — hardcoded Saga III tutor priority dict (C/D)
  - `ai/mulligan.py:148, 165` — Affinity / Amulet substring deck-name gates (D)
  - `tools/check_abstraction.py:206-248` — deck-gate visitor with substring blind spot (D)
