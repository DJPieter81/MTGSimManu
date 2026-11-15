---
name: abstract-first
description: Mandatory workflow for ANY change to engine/ or ai/ in MTGSimManu — rules fixes, AI scoring/decision changes, new effects, parser work, win-rate lanes. Use it before writing code whenever a replay, audit finding, census row, failing test or win-rate outlier points at a card, deck or matchup. It turns the card-level symptom into the mechanic-level fix (sized class, one owner, typed field, rule-phrased test, auditor invariant, pinned measurement). Triggers on "fix", "bug", "doesn't work", "resolves as nothing", "outlier", "win rate", "replay shows", "why does X", "unit", "implement", card names in an engine/AI context.
---

# Abstract first

A replay, an audit row or a win-rate cell always arrives as a **card** ("Silence
does nothing", "Reflection survives lethal damage", "Solitude never attacks
Ugin"). The fix is never about that card. This skill is the path from the
symptom to the mechanic. Run the steps in order; do not write engine/AI code
before step 5.

The binding rules live in `CLAUDE.md` → *ABSTRACTION CONTRACT*. This skill is
how to satisfy them without re-deriving them each time.

## 1. State the symptom, then the rule

Write two sentences:

- **Symptom** (card-level): what the replay/audit showed, with the evidence
  (log line, audit rule id, cell).
- **Rule** (card-free): the Comprehensive Rules sentence, or for AI the
  decision principle, that the engine/AI violated. Cite the CR number.

If you cannot write the rule without naming a card, you do not understand the
bug yet. Read more of the replay before touching code.

## 2. Size the class, with the tool

```bash
python tools/class_census.py "<regex over oracle text>" [--field <CardTemplate field>]
```

It prints the pool count, which registered decks carry the shape, and, with
`--field`, which matches an existing typed field misses.

- **≥ 10 pool cards** → mechanic-sized; continue.
- **< 10** → the shape is too narrow. **Widen it before building**: what CR
  family does it belong to? Examples:
  - "can't cast spells this turn" is 9 cards, but it is one case of CR 101.2
    "can't" effects, which also covers draw limits.
  - "Silence" is not a class; "turn-scoped cast prohibition by scope × spell
    filter" is.

  If no honest widening exists, **do not build it**. Record the card in the
  tracker as a known gap (e.g. Hex Magic: one spell in the pool).
- Also ask the **inverse**: which *other* code paths or card types hit the same
  rule? Recent examples:
  - A front-face gate was wrong for all 153 DFCs whose creature-ness differs
    by face, not just Fable.
  - Planeswalkers read 0.0 in *every* threat valuation, not only the
    attacker's.

## 3. Find the owner, then find existing machinery

- **Owner:** exactly one module owns each rule (`tools/check_single_owner.py`).
  Targets go through `engine/target_solver`, damage and life through
  `engine/damage.py`, counters through `CardInstance.add_plus_counters`, zone
  moves through `zone_mgr` / `zone_transfer`, discards through the discard
  funnel, draws through `GameState.draw_cards`.
  - If the fix wants to touch 2+ owners, the boundary is wrong. Fix the
    boundary first.
- **Reuse before you write.** Grep for the typed field, parser or primitive that
  already half-does it. Examples:
  - `effective_is_creature` already existed; four gates simply were not
    reading it.
  - `deal_damage` already handled planeswalker targets.
  - `turn_scoped_restriction` already parsed Silence.

  A second implementation of an existing rule is the most common defect in
  this repo.
- **Engine enforces, AI chooses.** A rule goes in `engine/`, a preference in
  `ai/`. If both are needed, give the engine a parameter and let the AI
  supply it: `declare_attackers(..., attack_targets)` plus
  `ai/attack_targets.py`.

## 4. Type it once, at load

Card knowledge enters as a **typed `CardTemplate` field parsed from oracle text
at DB load** (`engine/oracle_parser.py` → `engine/card_database.py`, and the
lazy fallback in `engine/cards.py`). No runtime oracle regex
(`check_oracle_runtime_parse`), no `card.name ==` (`check_abstraction`), no new
name-keyed `EFFECT_REGISTRY` entry (`check_card_name_registry`, which may only
shrink).

- **Shape the field as the mechanic's parameters, not the card's text**: `{who,
  filter}`, `{mode, graveyard, count, ends_turn}`, `{who, max}`.
- **Refuse what you cannot run.** Conditional or unusual variants return `None`
  and stay recorded, rather than being half-applied.
- **A field populated by ≤ 2 cards fails `check_narrow_typed_fields`.**
  Generalise the parser; this is step 2 again. For example, "players can't draw
  cards" joined the draw limit as a cap of 0.
- **Delete what the field replaces.** When the class covers a card that had a
  name-keyed handler, remove the handler and lower the registry baseline in the
  same commit.

## 5. Test the rule, red first

- **Name tests for the rule**, e.g. `test_a_noncreature_filter_stops_instants_but_not_creature_spells`.
  Card names appear only as fixture carriers. Prefer synthetic `CardTemplate`s
  for the mechanic, plus one real member of the class resolved through the
  real path.
- **Cover the edges**:
  - scope and filter variants;
  - the refusal case;
  - turn-boundary reset;
  - "a free cast is still a cast";
  - "only on your own turn".
- **Run it and see it fail before the fix.** A test that was never red proves
  nothing.
- **AI tests assert the decision rule, not a guessed outcome**: "attacks the
  planeswalker *exactly when* killing it beats the face damage", computed from
  the same primitives.

## 6. Add the auditor invariant

Every rules fix lands with a CR-phrased invariant in the same commit
(`engine/rules_audit.check(rule_id, ok, detail)`), placed at the seam.

- **Restate the rule independently.** Read the raw fields; never call the
  function under test.
- **Observation only.** It must be byte-identical with `MTG_RULES_AUDIT` unset.
- **Test both directions**: it fires when the engine is broken (monkeypatch
  the owner back to the defect), and it stays silent when the rule holds.

## 7. AI numbers come from primitives

- **No literals in `ai/`** (`check_magic_numbers`; a new module gets baseline
  `0`).
- **Compare in one currency.** Values should be deltas of `position_value`, or
  outputs of `ai/clock.py`, `permanent_threat`, `expected_future_value` or BHI.
- **When a comparison comes out "wrong", the primitive is miscalibrated.** Do
  not tune a threshold. Record the primitive as its own unit (e.g. a loyalty
  tick valued as one "average card").

## 8. Verify, measure from pinned worktrees

- **Checks:**
  - `for t in tools/check_*.py; do python $t || echo FAIL $t; done`
  - The anchor (`tests/test_wr_baseline_anchor.py`). Turn-only drift is
    refreshed with `tools/refresh_wr_baseline.py`. A winner flip is replayed
    anchor-exact and accepted only as rules-correct play.
  - Both chunks, one at a time: `tests/test_[a-g]*.py` then
    `tests/test_[h-z]*.py` minus `test_llm_embeddings.py`.
- **Measurement:** same seeds, n=20 Bo3, `MTG_LLM_DECISION_SCORER_OFFLINE=1`.
  - **Pre and post each run in a `git worktree` pinned at a commit**, never
    the working tree you are editing. A measurement that read a half-edited
    tree was discarded on 2026-09-27.
  - Measure the decks that carry the class, plus guards.
  - When one step moves a field by more than ~10pp, suspect a second defect
    before celebrating. For example, the unsick exile-returned Reflection
    inflated Jeskai +11.9.
- **Record it:** a tracker section in
  `docs/design/rules-foundation-sweep-tracker.md`: symptom, rule, class size,
  commits, measurement, leads not built.

## Quick self-check before committing

- [ ] Rule stated without a card name; CR cited.
- [ ] Class ≥ 10 (or widened, or recorded as a gap and not built).
- [ ] One owner; existing field or primitive reused; nothing re-implemented.
- [ ] Typed at load; no runtime oracle parse; no name keys; handlers replaced by the class are deleted.
- [ ] Tests named for the rule, seen red first.
- [ ] Auditor invariant plus its two tests (rules changes).
- [ ] AI values come from primitives, with zero new literals.
- [ ] Ratchets, anchor and both chunks green; measured from pinned worktrees; tracker entry written.
