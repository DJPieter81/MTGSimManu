---
title: Generic oracle-text resolver over-match / under-filter audit — findings
status: active
priority: secondary
session: 2026-08-31
depends_on: []
supersedes: []
superseded_by: []
tags:
  - engine
  - oracle-resolver
  - abstraction
  - audit
  - adversarially-verified
summary: >
  A 25-agent adversarially-verified workflow swept engine/ generic
  oracle-text resolvers for the "matches a keyword against the whole
  oracle text, then applies an effect without honoring the matched
  clause's own restriction" bug class. Records the confirmed findings,
  which are fixed, and which are deferred (modal resolution) or dormant
  (real bug, trigger card not in the current deck pool).
---

# Resolver over-match / under-filter audit — findings

## Method

Four discovery lenses (reminder-text / cross-ability matches, ignored
restrictions, fabricated effects, plus a completeness critic) fanned out over
`engine/oracle_resolver.py`, `engine/game_runner.py`,
`engine/spell_resolution.py`, `engine/target_solver.py`,
`engine/card_effects.py`, `engine/land_manager.py`. Each candidate was then
refuted by two distinct-lens skeptics (correctness + reachability) that read
the code, traced every upstream gate (EFFECT_REGISTRY handlers, typed-field
short-circuits, target-solver filters), and loaded the real oracle text from
`ModernAtomic.json`. 15 unique candidates → 7 CONFIRMED verdicts.

The class is the same one four replay audits surfaced independently: a
resolver reads a card's whole shape but drops the restriction the resolving
clause states (mana-value cap, card type, count, recipient, owner scope), or
matches a keyword inside reminder text / another ability.

## Fixed this session

- **Targeted-discard ignored its choose-clause restriction** —
  `oracle_resolver.py` "reveals hand, you choose a card, discard it" took the
  highest-mana-value nonland unconditionally. Inquisition of Kozilek
  (Goryo's Vengeance mainboard, cap "mana value 3 or less") could discard an
  above-cap bomb; Duress / Despise / Divest could take a forbidden card type.
  Fixed generically (`_targeted_discard_candidates` parses the cap + type
  predicate from the clause). Commit `19b1ee9`. **Reachable — in pool.**
- (Prior, same class, already merged: mana-value-capped reanimation
  `a3d041f`; Converge removal reachability `09e2c70`/`7341aa2`; painland
  mana-unit parse `7cd055a`; phantom draw off Cycling reminder text and
  fabricated Aftermath-Analyst sac damage `a3d041f`.)

## Deferred — needs a dedicated mechanic

- **Modal "choose one" board wipe resolves as "destroy all creatures"** —
  `spell_resolution.py:678`. Brotherhood's End ("Choose one — deal 3 to each
  creature and planeswalker; OR destroy all artifacts with mana value 3 or
  less") parses as `is_modal=False` (the engine populates no `modes` for
  resolution — `CardTemplate.is_modal`/`modes` exist but are unwired; only
  `ai/card_features._detect_is_modal` reads modality, separately). The generic
  `destroy` + `all` branch then destroys every creature on both battlefields —
  a behavior **neither mode produces**. **Reachable** (Ruby Storm + Hollow One
  sideboards, Bo3 post-board only). The correct fix is a modal spell-resolution
  mechanic — parse the "Choose one —" modes, let the AI pick one, resolve the
  chosen mode honoring its own type/mv/scope — not a surgical filter, so it is
  scoped as its own follow-up rather than rushed. Class: every modal removal /
  wipe / charm.

## Dormant — real bug, trigger card not in the current deck pool

Confirmed as genuine code defects but not reachable in any registered deck
today (verified `grep` of `decks/modern_meta.py`); fix if the exemplar enters
the pool, or fold into a general pass:

- **dies+draw hardcodes `draw_cards(controller, 1)`** — `oracle_resolver.py`
  dies-trigger ignores the parsed count and recipient: "each player draws a
  card" draws only for the controller; "draw two cards" draws one. Exemplars
  Runed Servitor / Youthful Scholar — not in pool.
- **dies+draw fires off Clue/investigate reminder text** — same branch scans
  un-stripped reminder text, so "investigate" (make a Clue) mis-fires an
  unconditional draw. Exemplar Byway Courier — not in pool.
- **Attack-trigger damage hits the player instead of "target creature
  defending player controls"** — `oracle_resolver.py` attack-trigger damage
  treats "not 'any target'" as "hit the opponent's face". Exemplar Mage-Ring
  Responder — not in pool.
- **Amass builds a 0/0 token and drops the +1/+1 counters** —
  `oracle_resolver.py` generic create-token branch scans un-stripped Amass
  reminder text. Exemplar Lazotep Plating — not in pool.

## Refuted

Battlefield "mana value N or less" / "power N or less" removal caps
(`target_solver.py`), symmetric-wrath owner scope (`game_runner.py` charge
branch), and the Goblin-Bombardment sac-damage routine were each traced to an
upstream gate or found unreachable, and dropped.
