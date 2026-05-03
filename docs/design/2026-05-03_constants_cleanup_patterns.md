---
title: Constants-cleanup patterns — what works and why
status: active
priority: secondary
session: 2026-05-03
depends_on:
  - tools/check_magic_numbers.py
  - ai/scoring_constants.py
tags:
  - patterns
  - abstraction-contract
  - refactor
  - constants
  - magic-numbers
summary: >
  Pattern catalogue distilled from the 2026-05-03 constants-cleanup wave (PRs
  #267, #268, #270, #271, #273, plus in-flight). Documents which abstractions
  worked, which didn't, and the rules-of-thumb that emerged for distinguishing
  magic numbers from mathematical primitives. Used as the reference for future
  cleanup branches and for the gates added in PR #267 (CI ratchet).
---

# Constants-cleanup patterns — 2026-05-03 wave

## Why this doc

A multi-PR cleanup wave on 2026-05-03 lifted ~206 bare numeric literals out of
`ai/*.py` into named constants in `ai/scoring_constants.py`. The work spanned 9
modules and produced PRs #267 (CI ratchet, baseline = 439), #268 (multi-module
sweep, ~76 lifted), #270 (outcome_ev, 1 lifted — informative because most of
its inline numerics turned out to NOT be magic), #271 (gameplan, ~30 lifted),
#273 (evaluator + discard_advisor, 99 lifted).

Across that wave we converged on a set of patterns. This doc captures them so
future cleanup branches don't re-discover them, and so the patterns can be
encoded into review heuristics or further linter rules.

## Core distinction — magic number vs mathematical primitive

Not every numeric literal in scoring code is a magic number. Treating them as
such inflates the cleanup scope and hides genuine signals behind noise. The
2026-05-03 outcome_ev.py audit (PR #270) was the clearest demonstration: of 15
inline numerics, only **1** was a true magic number. The other 14 were:

| Category | Example | Why it stays inline |
|---|---|---|
| Probability bound | `max(0.0, p)` / `min(1.0, p)` | These are the bounds OF a probability, not a tuning knob |
| Bool-as-float | `is_creature * 0.5` | `0.5` is a coefficient on a 0-or-1 indicator; replace by named constant only if the coefficient itself is the tuning knob (it usually is — but not always) |
| Zero-guard increment | `denom + 1` to avoid `/0` | Mathematical primitive, not strategic |
| Index / slice | `lst[0]`, `lst[:2]` | Not magic — the value reflects structure, not strategy |
| Sentinel `0`, `1`, `-1`, `2`, `100` | Loop bounds, percent conversions | Codified as the linter's `EXEMPT_VALUES` set |
| Rules-text cardinal | `{'a': 1, 'one': 1, 'two': 2, ...}` in oracle parser | Tagged with `# magic-allow:` — these are rules-text, not tuning |

**Rule of thumb:** if the literal would change with deck archetype, matchup, or
strategy tuning, it's a magic number. If it would change only when the laws of
arithmetic, MTG rules text, or Python semantics change, it's a primitive.

## Pattern 1 — section-grouped constants by calibration family

When a module yields many constants (evaluator.py: 96, gameplan.py: 30), group
them in `scoring_constants.py` under section headers reflecting **the
calibration family**, not alphabetical order.

evaluator.py's section list (PR #273):

- Role assessment
- Creature P/T scaling
- Tag-derived ability bonuses
- Ability-type bonuses
- Oracle-derived effect magnitudes
- CMC residency
- Removal valuation
- Spell-damage estimation
- Spell scoring across roles
- Life valuation curve
- Permanent valuation

Why: a future PR adjusting "removal valuation" will only need to read one
section. Alphabetical grouping forces the reader to do the calibration-family
mapping in their head every time.

## Pattern 2 — derive when a primitive exists

`ai/clock.py`, `ai/bhi.py`, `ai/predicates.py`, `ai/strategy_profile.py`, and
oracle text are derivation sources. When a constant can be derived from one of
these, **derive instead of hardcoding**.

Concrete win from PR #268: three sites had inlined `* 20.0` (sideboard_solver
×2, clock ×2). Replaced with the existing `CLOCK_IMPACT_LIFE_SCALING`. Future
retunes of the life-as-resource scaling factor are now a single-point edit.

Concrete win from PR #270: `outcome_ev.py`'s `n_draws=2` is the same lookahead
window as BHI's `p_higher_threat_in_n_turns(turns=2)` default and ev_player's
spot-removal-deferral `turns=2`. Lifted to a single constant
`FINISHER_REACHABLE_LOOKAHEAD_DRAWS` cross-imported by all three callsites.

Concrete win from PR #273: `discard_advisor.py`'s `REANIMATION_FUEL_FLOOR`
aliases to `DISCARD_BIG_CREATURE_CMC_THRESHOLD` — same rules-derived 5-CMC
definition for "big enough creature to be worth reanimating".

## Pattern 3 — sister-constant invariants

When two named constants must remain numerically equal because they describe
the same threshold seen from different angles, pin them with an
**invariant test** rather than relying on review discipline:

```python
# tests/test_gameplan_constants_linkage.py (from PR #271)
def test_storm_resource_target_matches_combo_force_threshold():
    """DEFAULT_STORM_RESOURCE_TARGET and COMBO_FORCE_PAYOFF_STORM_THRESHOLD
    describe the same axis from two angles. If one moves, both must move."""
    from ai.scoring_constants import DEFAULT_STORM_RESOURCE_TARGET, COMBO_FORCE_PAYOFF_STORM_THRESHOLD
    assert DEFAULT_STORM_RESOURCE_TARGET == COMBO_FORCE_PAYOFF_STORM_THRESHOLD
```

If a future PR retunes one without the other, the test goes red and surfaces
the inconsistency.

## Pattern 4 — `# magic-allow:` for rules-text cardinals

Some literals in scoring code ARE the Magic rules text. Examples:

- The english-word→integer parser map `{'a': 1, 'one': 1, 'two': 2, ...}` —
  these literals encode CR-defined cardinals. Renaming to `_INT_FOR_A = 1` would
  obscure the rules-text parsing intent.
- Rule-citation thresholds: `(life > 0)` — `0` is "rule 104.3a, a player at 0 or
  less life loses the game", not a tuning knob.
- Sentinel terminal values: `WIN_PROB_TERMINAL_WIN = 1.0`, `_LOSS = -100` —
  represent terminal states in a Markov decision process.

For these, the inline `# magic-allow: <reason>` comment is the right escape
hatch (mirrors `# abstraction-allow:` from the card-name ratchet). The reason
text must explain WHY it's a rules constant, not what the literal value is.

## Pattern 5 — discard-tier ladder as STRUCTURE

`discard_advisor.py`'s 9 priority constants form an ordered ladder:

```
+10  DISCARD_REMOVAL_NUDGE
+20  DISCARD_COUNTERSPELL_NUDGE
-30  DISCARD_COMBO_TUTOR_PROTECT     (negative → keep)
+40  DISCARD_LANDS_EXCESS_BONUS
+50  DISCARD_LANDS_GLUT_BONUS
+80  DISCARD_BIG_CREATURE_BASE
+90  DISCARD_FLASHBACK_BONUS
+100 (sentinel: escape, rules-exempt)
```

The numbers themselves matter less than the **ordering**. Document the ladder
as a structure in `scoring_constants.py` with a comment explaining: "These
constants form a tiered ladder where higher = stronger preference to discard.
The gaps (+10/+20 vs +80/+90) intentionally separate weak nudges from strong
defaults."

A future cleanup that re-tunes them must preserve the ordering.

## Pattern 6 — re-export shim during in-flight migrations

When a constants module is being deleted or restructured, but in-flight branches
import from the old location, **keep a re-export shim** for one PR cycle.

Example from PR #268: `ai/constants.py` migrated 9 constants to
`ai/scoring_constants.py`. But `ai/ev_evaluator.py` (in-flight P0-B branch) and
`engine/game_runner.py` import from `ai.constants`. Solution: `ai/constants.py`
becomes a one-line re-export of all 9 names from `ai.scoring_constants`.
Follow-up PR after the in-flight branches land will delete the shim entirely.

This avoids forcing every in-flight branch to rebase mid-flight on the
restructure.

## Pattern 7 — AST-based detection beats regex

The naive regex `^\s+[a-z_]+\s*=\s*[0-9]+\.?[0-9]*\b` undercounted by 4–5×
because it misses inline expressions, function arguments, and multi-token
formulas. The AST-based `tools/check_magic_numbers.py` (PR #267) catches:

- `score += 8.0` (top-level expression)
- `if probability < 0.15:` (comparison)
- `func(threshold=0.7)` (keyword argument)
- `[5, 10, 15]` (list literal)
- `{'a': 1, 'one': 1}` (dict literal — but `# magic-allow:` exempts)

The AST visitor uses depth tracking so module-top constants (`THRESHOLD = 0.7`)
are NOT counted — those ARE the named constants. Only literals inside
function/class bodies count.

## Pattern 8 — per-archetype tuning lives in archetype-keyed config

`ai/gameplan.py`'s `_ARCHETYPE_THRESHOLDS` dataclass instantiation looks like
many bare literals. They are not — they're per-archetype tuning that already
lives in its proper home: an archetype-keyed dataclass with named fields and
adjacent comments. **Don't lift these into scoring_constants.py.**

The CLAUDE.md rule: "Per-archetype values belong in archetype-keyed config
rather than module-level constants." When in doubt, ask: would two archetypes
realistically use different values? If yes, it's tuning and stays in config; if
no, it's a global constant and goes to scoring_constants.

## Pattern 9 — mathematical primitives in `win_probability.py`

`win_probability.py` had 11 literals counted by the AST visitor. Most are
mathematical primitives in a Markov decision model:

- `EPS` (epsilon for numerical stability)
- `±100` terminal Win/Loss values
- `est_lib` offset for finite-deck approximation
- `0.0` / `1.0` probability bounds

Lifting these into named constants would obfuscate the math. The pattern: a
module that's primarily a numerical algorithm (vs. a strategic-scoring layer)
needs a lighter touch.

## Heuristics for distinguishing the cases

| Signal | Likely magic number | Likely primitive |
|---|---|---|
| Inside a math formula vs. as a coefficient | Coefficient | Bounded operation |
| Would change with archetype | Yes | No |
| Has a clear unit (mana, life, turns) | Yes | Often dimensionless |
| Could be derived from `clock`/`bhi`/`oracle` | Derive | n/a |
| Cited in MTG rules | `# magic-allow:` | n/a |
| Bool-as-float coefficient | Often magic | n/a |
| Probability bound | n/a | Primitive |
| Loop / index / slice | n/a | Primitive |
| Cardinality from oracle text | `# magic-allow:` | n/a |

## What this means for new fix PRs

Going forward (post-PR #267 merging), every fix PR is gated by the magic-number
ratchet. The patterns above are the toolkit for staying compliant:

1. Use the AST visitor to inventory before editing.
2. Triage: magic? primitive? rules-text? per-archetype?
3. For magic: lift to named constant in the right calibration-family section,
   with derivation comment.
4. For primitive: leave it.
5. For rules-text: `# magic-allow: <reason>` inline.
6. For per-archetype: leave in archetype-keyed config.
7. If two named constants describe the same axis from different angles, pin
   with an invariant test.
8. If you must restructure during in-flight migrations, leave a re-export shim.

## Cross-reference

- `tools/check_magic_numbers.py` — the linter (PR #267)
- `tools/magic_numbers_baseline.json` — per-file baselines
- `ai/scoring_constants.py` — section-grouped destination module
- `ai/constants.py` — re-export shim during ev_evaluator migration
- PRs #250, #252, #253, #254, #255, #259, #267, #268, #270, #271, #273 — the
  cleanup wave
