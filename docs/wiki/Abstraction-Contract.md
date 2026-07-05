---
title: "Wiki: Abstraction Contract"
status: active
priority: secondary
session: 2026-07-05
tags:
  - wiki
  - abstraction-contract
  - ci
summary: |
  Wiki page — the four questions, the hard prohibitions, and the ratchet
  tools that keep card knowledge out of engine/AI source. Staged under
  docs/wiki/ pending wiki publication.
---

# The Abstraction Contract

The single biggest risk in a card-game simulator is death by a thousand patches: `if card.name == "Lightning Bolt"` fixes today's bug and rots into an unmaintainable pile. MTGSimManu counters this with a binding contract, enforced by CI, on every change to `engine/` or `ai/`.

## The four questions

Before writing any engine/AI diff, answer all four:

1. **Class size** — how many of Modern's 20,000+ cards could legitimately hit this code path? Fewer than 10 means you are patching a card, not fixing a mechanic. Stop and find the mechanic.
2. **Subsystem** — which *single* module owns this rule? If the change spans two or more modules, the module boundary is wrong; fix the boundary first.
3. **Failing test, rule-phrased** — write the test before the fix, and name it after the *mechanic*, not the card. "equipment instance_id stacks correctly" — yes. "Cranial Plating works" — no. If you can't phrase the rule without naming a card, you don't understand the bug yet.
4. **Knowledge location** — card-specific knowledge lives in oracle text, MTGJSON, or gameplan JSON. Never in `.py` source.

## Hard prohibitions (CI-enforced)

- **No new card-name conditionals** in `engine/` or `ai/` (`card.name == "X"`, `name in {...}`).
- **No new deck-name conditionals** — if a gate controls a mechanic, express it as an oracle predicate, tag, or per-archetype config.
- **No new bare numeric literals** in AI scoring code — every number is either derived from a principled subsystem (clock, Bayesian model, combo math) or a named constant with an inline justification.
- **No fix without a failing test in the same diff** — red first, then green, both in one commit.
- **No root-level doc sprawl and no `_V2` file versioning** — docs carry YAML frontmatter; supersession is recorded in frontmatter, never by spawning a sibling file.

Legitimate exceptions (enum checks, true rules constants) are annotated inline with `# abstraction-allow: <reason>` or `# magic-allow: <reason>`.

## The ratchet tools

Enforcement is by *ratchet*: each tool counts violations against a committed baseline and fails any commit that increases the count. Reducing the count requires lowering the baseline in the same commit — so the codebase can only get cleaner.

| Tool | Guards against |
|------|----------------|
| `tools/check_abstraction.py` | card-name and deck-name conditionals in `engine/` + `ai/` |
| `tools/check_magic_numbers.py` | bare numeric literals in AI scoring modules |
| `tools/check_doc_hygiene.py` | root `.md` allowlist violations and `_V2`-style filenames |

Each is runnable standalone (`python tools/check_abstraction.py --list`) and fires at three levels: GitHub Actions on every push/PR (binding), a pytest wrapper in the regular suite, and an optional local pre-commit hook (`bash tools/install_hooks.sh`).

## Why it works

The contract turns "no hardcoding" from a slogan into a mechanical gate. A fix that only helps one deck is treated as a smell — the required workflow is: diagnose in deck-specific terms, lift the diagnosis to a generic mechanism, implement on the mechanism, then verify at least one *other* deck benefits. The rule of thumb: **the formula should still make sense for a card the deck has never seen before.**
