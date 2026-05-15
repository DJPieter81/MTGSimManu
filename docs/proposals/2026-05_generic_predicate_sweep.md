---
title: "Generic-Predicate Sweep — comprehensive removal of card-name and deck-name gates"
status: archived
priority: historical
session: 2026-05-10
completion_session: 2026-05-15
tags: [proposal, refactor, abstraction-contract, generic, sweep]
summary: >
  Multi-PR plan to remove every hardcoded card-name and deck-name
  gate flagged in the 2026-05-10 audit. All PRs A through F-6 shipped
  by 2026-05-15 (#339 strategy_tags, #341 Mox Opal generic, #344
  archetype enum, #346 tutor predicate, #347 prune redundant overrides,
  #349 removal predicate, #353 discard predicate, #354 cantrip text
  fallback, #356 graveyard_hate predicate, #358 effective_cmc oracle-
  derived). Three follow-up audits tightened over-eager predicates
  (#372 removal self-bounce, #373 tutor basic-land, #374 graveyard_hate
  self-use). Sweep complete.
---

# Generic-Predicate Sweep

## Why this exists

The 2026-05-10 audit (run by the Explore agent) and the user's
directive surfaced multiple sites where engine and AI behavior
gate on card-name strings or deck-name substrings. CLAUDE.md's
abstraction contract forbids this pattern; the ratchet caught
some, but several pre-existing sites slipped through.

This plan sequences a focused removal of every flagged site.
Each item is its own PR with a single concern.

## Constraints (from user directives)

1. **Comprehensive sweep** — covers every flagged engine/ + ai/ site.
2. **Failing-first** — each PR adds a test pinning the OLD behavior
   first, then makes the parser/predicate change, then verifies
   the test still passes. No semantic-shift slipups (the WST
   mulligan regression I caused mid-session is the cautionary
   example).
3. **No deferred TODOs** — when an item is in scope, fix it; if
   it requires data-model changes (e.g. adding a `strategy_tags`
   field to `DeckGameplan`), include those changes.

## Audit findings — sites to fix

The Explore agent's audit produced this prioritized list. Each
becomes a discrete PR.

### Tier 1 — outright deck-name / card-name gates

| # | File:Line | Pattern | Plan |
|---|---|---|---|
| 1 | `ai/mulligan.py:227` (current) | `if "amulet" not in deck_name.lower():` flood-ceiling exception | Add `strategy_tags: Set[str]` to `DeckGameplan`. Tag Amulet's gameplan JSON with `"ramp_into_payoff"`. Replace the gate with `if "ramp_into_payoff" not in tags:`. |
| 2 | `ai/board_eval.py:600-601` | `if hasattr(player, 'effective_cmc_overrides') and card.name in player.effective_cmc_overrides:` | Replace with `card.template.domain_reduction > 0` lookup; the parser already surfaces this on the template. |
| 3 | `engine/mana_payment.py:85` | `if card.template.name == "Mox Opal":` for metalcraft mana | Replace with oracle-pattern detection: card has `"metalcraft" in oracle.lower()` AND oracle has `"{T}: Add"` AND `card.template.produces_mana` is non-empty. Generic across any card with a metalcraft mana ability. |

### Tier 2 — partially-hardcoded patterns

| # | File:Line | Pattern | Plan |
|---|---|---|---|
| 4 | `ai/ev_player.py:201-203, 419` | `if self.archetype in ("combo", "storm")` | Convert string literals to `ArchetypeStrategy` enum members. Already mostly enum-driven; finish the migration. |
| 5 | `engine/permanent_effects.py:241` | `if creature.template.name not in EFFECT_REGISTRY._handlers:` | Document with `# abstraction-allow: registry membership for hardcoded vs. generic dies-trigger dispatch` (legitimate routing decision, not a gate on card behavior). |

### Tier 3 — TOKEN_DEFS migration (extends generic-token-spec work)

The current `claude/phase-1c-followup-wurm-drone-tokens` PR added
`source_oracle` to `create_token` and migrated three callers
(Saga, Nettlecyst, Pinnacle Emissary). Tier 3 finishes the job:

| # | File:Line | Caller | Plan |
|---|---|---|---|
| 6 | `engine/card_effects.py:Goblin Bombardment, Phyrexian Tower, Anointed Procession, etc.` | All other `create_token` call sites | Audit every `create_token(...)` call; pass `source_oracle=card.template.oracle_text` everywhere. Once all callers pass it, retire `TOKEN_DEFS` entirely (or keep only for tokens with no canonical oracle text — Treasure, Food, Clue if their spawning oracle is "create a Treasure token"). |

### Tier 4 — Card-name fallback tables in `engine/card_database.py`

`TAG_OVERRIDES` (~60 entries) and `ABILITY_OVERRIDES` (~8 entries)
are oracle-parse fallbacks. The agent flagged these as
"acceptable / documented" but the user's directive is "really
broad sweep" — these still violate the abstraction-contract
spirit even if they're data-driven.

| # | File:Line | Plan |
|---|---|---|
| 7 | `engine/card_database.py:1162-1234` (TAG_OVERRIDES) | For each entry, scan its oracle text and identify the regex pattern that *should* have caught it. Extend `engine/oracle_parser.py` predicates until 95% of TAG_OVERRIDES entries are redundant. Migrate them out one by one. |
| 8 | `engine/card_database.py:1239-1248` (ABILITY_OVERRIDES) | Same pattern — likely 100% migratable to oracle predicates. |

### Tier 5 — clock / scoring constants

| # | File:Line | Pattern | Plan |
|---|---|---|---|
| 9 | `ai/clock.py:190` | `"storm": 8, "midrange": 5, ...` archetype scoring map | Move to `ai/scoring_constants.py` as a named dict. Rename to make the data-driven nature explicit. |
| 10 | `ai/finisher_simulator.py:918-925` | `if archetype_lc.startswith("storm")` | Convert to enum match (already-existing `ArchetypeStrategy.STORM`). |

## Sequencing — 6 PRs

Each PR is its own branch. Ordered for minimal cross-conflict.

### PR A — `claude/sweep-1-mulligan-strategy-tags` (Tier 1 #1)

1. Add `strategy_tags: Set[str] = field(default_factory=set)` to
   `DeckGameplan`.
2. Update `ai/gameplan.py`'s JSON loader to read the new field.
3. Add `"strategy_tags": ["ramp_into_payoff"]` to
   `decks/gameplans/amulet_titan.json`.
4. **Failing-first**: write a test that calls `MulliganDecider.decide`
   on Amulet (5 lands + 1 spell) and asserts keep=True; verify
   the existing pass.
5. Replace the `"amulet" not in deck_name.lower()` gate with
   `"ramp_into_payoff" not in tags`.
6. Verify the keep=True test still passes; verify the existing
   WST regression test (`test_duplicate_legendaries_treated_as_dead_in_keep_decision`)
   still passes (it pinned the OPPOSITE behavior for non-Amulet
   decks).

### PR B — `claude/sweep-2-effective-cmc-overrides` (Tier 1 #2)

1. **Failing-first**: write a test that exercises the current
   `effective_cmc_overrides` path on Scion of Draco / Leyline
   Binding (domain reducers).
2. Replace `card.name in player.effective_cmc_overrides` with
   `card.template.domain_reduction > 0` and compute on-the-fly.
3. Verify the test passes; remove `effective_cmc_overrides` once
   no callers remain.

### PR C — `claude/sweep-3-mox-opal-metalcraft-generic` (Tier 1 #3)

1. **Failing-first**: write a test that calls
   `ManaPayment.effective_produces_mana` on Mox Opal (metalcraft
   active) and asserts `[W,U,B,R,G]`. Verify it passes today.
2. Replace `card.template.name == "Mox Opal"` with the oracle
   predicate: `_is_metalcraft_mana_source(template)`. Move the
   detection into `engine/oracle_parser.py` as a parse-time
   template flag (`template.metalcraft_mana_any_color: bool`).
3. Verify the existing test still passes.
4. Run the abstraction ratchet — Mox Opal hit removed.

### PR D — `claude/sweep-4-archetype-string-to-enum` (Tier 2 #4)

1. **Failing-first**: write tests pinning current behavior for
   `EVPlayer.__init__` archetype-string branches.
2. Convert `if self.archetype in ("combo", "storm"):` to enum
   `if self.archetype in {ArchetypeStrategy.COMBO, ArchetypeStrategy.STORM}:`.
3. Identical for `ai/finisher_simulator.py:918`.

### PR E — `claude/sweep-5-token-defs-finish` (Tier 3 #6)

1. Audit every `create_token(...)` call site in `engine/`.
2. **Failing-first**: write tests for each new caller pattern
   (Goblin Bombardment, Treasure Cruise, etc.) using the
   spawning card's oracle text.
3. Pass `source_oracle=card.template.oracle_text` at each call.
4. Once every caller passes oracle, remove the canonical
   non-resource entries from `TOKEN_DEFS` (keep Treasure / Food
   / Clue / Goblin / Soldier / Spirit as "no-oracle fallback"
   for callers that don't have a spawning card).

### PR F — `claude/sweep-6-tag-overrides-migration` (Tier 4)

The longest project. Splits into sub-PRs of 10-20 cards each.
1. Categorize `TAG_OVERRIDES` entries by regex pattern (which
   parser predicate would have caught them).
2. Extend each parser predicate.
3. **Failing-first**: write parameterized tests over the
   `TAG_OVERRIDES` corpus, asserting the parser produces the
   override's tag set without the override.
4. Remove migrated entries one batch at a time.

## Acceptance gates per PR

For each PR:

- [x] Failing-first test added FIRST, on the OLD code path
- [x] All existing tests in the affected modules still pass
- [x] `python tools/check_abstraction.py` clean (or baseline
       reduced explicitly)
- [x] `python tools/check_magic_numbers.py` clean
- [x] `python tools/check_doc_hygiene.py` clean
- [x] The PR's commit message describes the data-model change
       (if any) AND the predicate replacement

## Sequencing target

```
PR A (mulligan strategy_tags)   →  closes the Amulet gate, immediate user-visible improvement
PR B (effective_cmc_overrides)  →  data-driven cost reduction
PR C (Mox Opal metalcraft)      →  closes the last abstraction-ratchet violation
PR D (archetype string→enum)    →  cleanup, no behavior change
PR E (TOKEN_DEFS finish)        →  retire the table for canonical tokens
PR F (TAG_OVERRIDES migration)  →  multi-week effort, one batch per session
```

PRs A, B, C, D land in the next session day — they're each <1 hour
of work + tests.

PR E lands as a follow-up to the current
`claude/phase-1c-followup-wurm-drone-tokens` PR (which already
seeded the `source_oracle` infrastructure).

PR F is its own multi-session project.

## What this plan supersedes

Nothing — extends the abstraction contract enforcement. The
running `tools/check_abstraction.py` ratchet keeps the new state
locked in.

## Risks

| Risk | Mitigation |
|---|---|
| WST-style semantic regression | Failing-first tests catch BEFORE the change ships |
| `strategy_tags` field migration breaks deck JSON loading | Add field with `default_factory=set`; existing JSON without the field falls through cleanly |
| Mox Opal generic predicate over-matches (e.g. Phyrexian Altar) | Scope predicate to "metalcraft" keyword + mana production; non-metalcraft mana sources untouched |
| TOKEN_DEFS removal misses a caller | Keep TOKEN_DEFS as fallback indefinitely; only retire entries when grep confirms zero callers |
| TAG_OVERRIDES migration accidentally drops an oracle-only-with-no-pattern card | Parameterized tests over the corpus catch this before merge |

## Out of scope

- Production scorer wiring for the ISMCTS A/B harness (Phase 5
  follow-up; tracked separately)
- SLM acceptance gates running live (waits for GGUF download)
- New decks / matchup additions
