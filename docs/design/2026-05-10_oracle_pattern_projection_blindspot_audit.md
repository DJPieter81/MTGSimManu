---
title: Oracle-pattern projection blindspot — class-of-bug audit
status: active
priority: secondary
session: 2026-05-10
depends_on:
  - PROJECT_STATUS.md
tags:
  - design
  - methodology
  - ev-evaluator
  - card-projection
  - cross-mechanic
summary: >
  Distillation of the impulse-draw bug fixed in PR #334
  (Storm vs Dimir −15pp regression). The class: a card-effect
  projection in `ai/ev_evaluator.py` matches oracle text by literal
  phrase ("draw two") and assigns a flat estimate, missing
  alternative phrasings of the same mechanic ("exile the top two
  cards … may play"). The under-projected card scores below
  pass_threshold and the AI declines to cast it. Four other sites
  in `ai/ev_evaluator.py` follow the same pattern; each is a
  candidate for the same kind of fix.
---

# Oracle-pattern projection blindspot — class-of-bug audit

## The pattern (rule-phrased)

The simulator projects the on-resolution effect of a spell by
inspecting its oracle text against a finite list of literal phrases.
When the oracle text uses an alternative templated phrasing for the
**same mechanic**, the literal-phrase test misses, the projection
falls back to a flat estimate (or to baseline-1), and the cast's
projected EV is materially underestimated. The AI then declines to
cast it.

The bug is invisible in tests written against the literal phrases
the projection already handles — only the *alternative templated
phrasing* surfaces it.

## The exemplar (PR #334)

- **Mechanic:** card-draw projection in `compute_play_ev`
  (`ai/ev_evaluator.py:1903`).
- **Original detection:** literal "draw N cards" plus
  `is_draw_engine` tag baseline (+1).
- **Missed phrasing:** impulse-draw `exile the top N cards … may
  play/cast` (Reckless Impulse, Wrenn's Resolve, March of Reckless
  Joy). N=2 cards projected as N=1.
- **Symptom:** Reckless Impulse raw_delta = −3.33 EV ⇒ score
  below `pass_threshold = −5.0` once any counter discount applied
  ⇒ Storm passes turn after turn vs Dimir.
- **Fix shape:** unify the projection on a parsed N (English
  numeral or digit), covering all three current patterns
  (literal `draw N`, impulse-draw `exile top N … may play`,
  library-search `into your hand`) under one extracted count.

## Audit — other sites with the same shape

Each row is a flat-estimate projection inside
`ai/ev_evaluator.py::compute_play_ev` that matches oracle text by
keyword fragment and applies a per-mechanic constant. None of them
extract the actual N from oracle text; each is a likely
under-projection for cards using non-default phrasings.

| Site | Line | Detection | Estimate | Risk |
|---|---|---|---|---|
| ETB life gain | `ai/ev_evaluator.py:1962-1966` | `'gain' in oracle and 'life' in oracle` | `REANIMATION_LIFE_GAIN_ESTIMATE = 3` (flat) | Thragtusk (5), Omnath / Beanstalk Giant (variable), Lifecraft Cavalry (3) all project the same; non-`gain` phrasings ("you gain life equal to …") miss entirely. |
| Ritual mana | `ai/ev_evaluator.py:1956` | `is_ritual(card)` tag | `RITUAL_MANA_PRODUCED = 3` (flat) + `-1` for cantrip-tagged | Manamorphose produces 2 (already corrected), Cabal Ritual produces 3 base / 5 with threshold, Dark Ritual produces 3, Pyretic Ritual produces 3 — but the flat constant doesn't read `template.ritual_mana[1]` which already has the parsed N. |
| Energy ETB | `ai/ev_evaluator.py:1969-1970` | `'energy' in tags` | `ENERGY_PRODUCED_ESTIMATE = 2` (flat) | Galvanic Discharge produces N (X-cost), Guide of Souls produces 1, Voltaic Visionary produces 1+1, Phlage produces 0/incidental — all flatten to 2. |
| Tutor "into your hand" search | `ai/ev_evaluator.py:1273-1278` (`_is_real_dig`) | `'search your library'` | boolean | **FALSIFIED 2026-05-10** — printed Modern pool has 40 multi-card "search for up to N → hand" cards, but the active 16-deck pool runs zero of them. Below the abstraction-contract class-size floor; no fix shipped. See `docs/diagnostics/2026-05-10_multi_card_tutor_projection_audit.md`. Re-open if a future deck registers ≥ 4 copies of any matching card (Cultivate, Kodama's Reach, Gifts Ungiven, Tooth and Nail, Tiamat, etc.). |

## Workflow (the fix shape that landed in PR #334, repeatable)

1. **Test names the rule.** A test for the projection of a
   `mechanic` X should set up a card whose oracle uses an
   alternative templated phrasing of X, assert the projected
   delta matches the literal-phrasing case. If the unit test
   needs to name a card to construct the rule, you don't have
   the rule yet — restate.

2. **Failing test first.** The literal-phrase case stays green;
   the alternative-phrase case goes red.

3. **Unify on a parsed quantity.** Replace the flat estimate
   with a value extracted from oracle (regex over
   `(one|two|three|four|five|six|seven|\d+)`, plus the per-mechanic
   verb predicate). The English-numeral lookup uses a tuple whose
   index IS the integer (`('zero','one','two',…)[2] == 'two'`,
   `index('two') == 2`) — no new bare numeric literals.

4. **Generalise across all known phrasings before merge.** For
   the audited mechanic, list every phrasing in the printed
   Modern pool (search MTGJSON oracle text by mechanic verb),
   confirm the parsed extractor covers each one. PR #334 covered
   `draw N`, `exile top N … may play`, and `into your hand`.

## Failing-test signatures for the audit follow-ups

Each of these is the named rule the failing test should encode.
Naming convention follows PR #334's
`test_storm_baits_counters_with_cantrips_at_high_counter_density`:
the test name describes the *mechanic* and the *failure mode*, not
any one card.

- `test_etb_life_gain_projects_actual_amount_not_flat_estimate.py`
  — Thragtusk-class ETB ("gain 5 life") and small-gain creatures
  ("gain 1 life") must project distinct life deltas, not the flat
  3-life estimate.

- `test_ritual_mana_projects_template_ritual_mana_data_not_constant.py`
  — Pyretic Ritual (R → 3R), Cabal Ritual (B → 3B / 5B threshold),
  Manamorphose (RG → 2 any) must project their actual mana
  production from `template.ritual_mana`, not a flat 3-mana
  constant.

- `test_energy_etb_projects_actual_amount_not_flat_estimate.py`
  — Guide of Souls (1 energy) and Galvanic Discharge (X energy)
  must project distinct energy deltas, not the flat 2-energy
  estimate.

- `test_tutor_projects_card_count_when_multiple_targets.py`
  — Mastermind's Acquisition / Bring to Light variants whose
  oracle is "search … and put up to N cards into your hand" must
  project N cards of effective hand value, not a flat 1-card
  baseline.

## Anti-patterns to reject in PRs

- **`if 'WORD' in oracle: ev += FLAT_CONSTANT`.** If the same
  mechanic produces variable amounts across the printed pool, a
  flat constant is the wrong shape. Use a parsed extractor
  instead.
- **Per-card overrides patching the flat constant.** Storm's
  Manamorphose-cantrip line `projected.my_mana -= 1  # Manamorphose
  only produces 2` is a one-card patch covering the same shape
  this audit names. The principled replacement reads the parsed
  ritual mana from `template.ritual_mana[1]`.
- **Tests that name cards instead of mechanics.** A test named
  `test_thragtusk_lifegain` is a tell — restate the rule with
  the projected mechanic in the name.

## Class size

For each row in the audit table:
- ETB life gain: ~80 cards in the Modern pool with `gain N life` oracle phrasings, N in {1, 2, 3, 4, 5, 6, 7, X}.
- Ritual mana: ~25 cards with `add N mana` ritual phrasings, N in {1, 2, 3, 5}, plus colour and conditional variants.
- Energy: ~12 cards with `gain N energy` oracle phrasings, N in {1, 2, 3, 4, X}.
- Tutor target count: ~30 cards with `search … and put up to N cards into your hand`, N in {1, 2, 3}.

Each row hits well above the abstraction-contract's class-size
floor (10 cards). Per-card patches are **not** the right shape;
unify on parsed extraction.

## When to apply this audit again

Anywhere `ai/ev_evaluator.py::compute_play_ev` (or the projection
helpers it calls) reads `oracle` and writes a flat constant onto
`projected.<field>`. Repeat the audit whenever a new mechanic is
added or a new printing introduces an alternative templated
phrasing of an existing mechanic.

Cross-reference: this is a generalisation of the same lesson
recorded in `docs/diagnostics/2026-04-28_storm_wasted_enablers.md`
(impulse-draw vs flashback-grant detection) and complements the
oracle-driven principles in
`docs/design/2026-05-04_modern_combo_audit_methodology.md`.
