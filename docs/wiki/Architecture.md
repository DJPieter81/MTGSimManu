---
title: "Wiki: Architecture"
status: active
priority: secondary
session: 2026-07-05
tags:
  - wiki
  - architecture
summary: |
  Wiki page — the three layers (engine rules / AI EV / deck data) and the
  per-turn decision pipeline. Staged under docs/wiki/ pending wiki
  publication.
---

# Architecture

MTGSimManu is built as three strictly separated layers. The separation is the project's core invariant: **the engine enforces rules and never scores; the AI scores and never bends rules; card- and deck-specific knowledge lives in data, never in Python.**

## The three layers

### 1. Engine — rules enforcement (`engine/`)

A Magic rules state machine. `GameState` owns zones, mana, the stack, and combat; `GameRunner` drives the turn structure (untap → upkeep → draw → main → combat → main 2 → end → cleanup). State-based actions (lethal damage, deathtouch, poison, legend rule) live in a dedicated `SBAManager`; spell resolution re-checks target legality on resolution (CR 608.2b) and fizzles spells whose targets all became illegal. Object identity across zone changes follows CR 400.7 via a per-entry sequence number, so delayed effects (like end-of-turn exile) correctly lose track of a card that left and re-entered the battlefield.

Card behaviour is derived from **oracle text**, not hardcoded names: a shared clause splitter (`engine/oracle_clauses.py`) scopes predicates to the ability paragraph they belong to, sagas parse into real chapter abilities (CR 714), and an `EFFECT_REGISTRY` decorator pattern handles the cards that need bespoke resolution logic.

### 2. AI — EV-based decisions (`ai/`)

Every choice is an expected-value computation. The AI never receives strategy hints from the engine; it reads the same game state a player would and scores its legal options. Numeric weights are not scattered literals — they derive from principled subsystems (a combat clock, a Bayesian opponent model, combo resource math) or live as named, justified constants. Per-archetype tuning is pure data in `ai/strategy_profile.py`.

### 3. Deck data (`decks/`)

Decklists (`decks/modern_meta.py`), per-deck **gameplans** (`decks/gameplans/*.json` — ordered goals, card roles, mulligan keys), and metagame shares. Adding a deck means adding data, not code: `import_deck.py` auto-detects the archetype and generates a gameplan skeleton.

## The decision pipeline

On every main phase the AI runs this pipeline:

```
EVSnapshot ← snapshot_from_game()        # ai/ev_evaluator.py — compact board projection
    ↓
GoalEngine.current_goal                  # ai/gameplan.py — which phase of the deck's plan is live
    ↓
Enumerate legal plays                    # ai/ev_player.py
    ↓
Score each play                          # heuristic EV + clock Δ (ai/clock.py)
                                         #   + combo modifier (ai/combo_calc.py)
    ↓
Discount by P(countered), P(removed)     # ai/bhi.py — Bayesian hand inference over
                                         #   what the opponent is likely holding
    ↓
TurnPlanner: evaluate 5 turn orderings   # ai/turn_planner.py — deploy→attack,
                                         #   remove→attack, attack→deploy, hold mana, lethal
    ↓
Execute best → log reasoning             # ai/strategic_logger.py — inspect with --trace
```

Supporting subsystems: `ai/mulligan.py` (keep/mull/bottoming per archetype), `ai/response.py` (counterspell decisions with oracle-derived targeting restrictions), `ai/combo_chain.py` (storm chain simulation), `ai/pw_ability.py` (planeswalker loyalty choice), `ai/finisher_simulator.py` (kill-turn projection).

Every step is observable: `python run_meta.py --trace deck1 deck2 -s SEED` prints the full reasoning chain for each decision.

## Scale

~136,700 lines of Python across 472 files: 47 AI modules (~34k lines), engine ~20k lines, and a 313-file test suite (2253 tests, all green as of 2026-07-05). Full reference with per-module line counts: `docs/ARCHITECTURE.md` and `PROJECT_STATUS.md` in the repository.
