# MTGSimManu — Architecture (canonical)

> Current architecture for the Modern simulator. Replaces the prior content
> of this file (which referenced deleted modules and was marked stale).
> Operational summary lives in `CLAUDE.md`; this file is the reference.
> Last freshness pass: 2026-07-05 (post engine+AI overhaul, PRs #441–#467).

---

## Decision flow at a glance

```
EVSnapshot ← snapshot_from_game()          # ai/ev_evaluator.py
    ↓
GoalEngine.current_goal                    # ai/gameplan.py (JSON-driven)
    ↓
Enumerate legal plays → Play objects       # ai/ev_player.py
    ↓
Score: heuristic EV + clock Δ + combo mod  # ai/ev_player.py + ai/clock.py + ai/combo_calc.py
    ↓
Discount by P(countered), P(removed)       # ai/bhi.py (Bayesian hand inference)
    ↓
TurnPlanner: 5 orderings evaluated         # ai/turn_planner.py
    ↓
Execute best → log reasoning               # ai/strategic_logger.py
```

**Known weakness:** generic `_score_spell()` has no per-card overrides → planeswalkers score ~0 (P0), storm rituals penalised mid-chain (P1). Fix path: `card_ev_overrides` in gameplan JSON + combo chain EV bypass.

---

## Layer 1: Engine (rules enforcement)

The engine enforces Magic rules. It does NOT make decisions.

**GameState** (`engine/game_state.py`) — central mutable game object:

- `play_land(player_idx, card)` — land onto battlefield
- `cast_spell(player_idx, card, targets)` — resolve spell via EFFECT_REGISTRY
- `can_cast(player_idx, card)` — mana + color check (backtracking color solver for 4+ colors)
- `check_state_based_actions()` — lethal damage, legend rule
- `resolve_stack()` — resolve top of stack (handles storm, cascade, flashback, rebound)
- `combat_damage(attackers, blockers)` — first strike, trample, lifelink, deathtouch
- `_trigger_landfall(player_idx)` — multi-trigger landfall (Omnath pattern)
- `reanimate(controller, card)` — put creature from GY to battlefield

**GameRunner** (`engine/game_runner.py`) — turn loop:

- Untap → Upkeep (rebound) → Draw → Main1 → Combat → Main2 → End Step → Cleanup
- Mana pools empty between phases (CR 500.4)
- Main phase loops `EVPlayer.decide_main_phase()` until AI passes
- Response windows after each spell for counterspells
- Tap-ability dispatch: activated tap abilities are enumerated from oracle text and fired via `_activate_tap_abilities()` during main phases — they are not auto-executed on ETB; planeswalker loyalty-ability *choice* is delegated to `ai/pw_ability.py` (the runner only enforces loyalty legality)

**SBAManager** (`engine/sba_manager.py`) — state-based actions as a static-method rule module (no instance state; every check takes the game object):

- CR 704.5c — player with lethal poison counters loses
- CR 704.5h — creature with lethal damage marked is destroyed (indestructible exempt)
- CR 704.5i — creature dealt damage by a deathtouch source is destroyed
- All four SBA rules above (plus CR 608.2b below) were restored from dead code in the 2026-07-05 resolver/SBA unification (PR #447) — they existed but were never invoked by the live resolution path

**Spell resolution** (`engine/spell_resolution.py`) — CR 608.2b: target legality is re-checked on resolution; a spell whose targets are ALL illegal fizzles (is countered by the rules). `engine/cast_manager.py` snapshots each card-target's zone at cast time to support the check.

**Object identity** (`engine/cards.py` / `engine/game_state.py`) — `battlefield_entry_seq` increments on every battlefield entry, giving each permanent-instance a distinct identity per CR 400.7. Delayed riders (e.g. end-of-turn exile) record the seq at registration and drop silently when the tracked object has since changed zones — a re-entered card is a new object.

**Oracle clause scoping** (`engine/oracle_clauses.py`) — shared clause-splitting primitives for oracle-text predicates: `split_abilities` (paragraph scope — the correct unit for pairing a trigger with its effect per CR 603.1) and sentence-level splitting. Fixes the whole-text-conjunction over-approximation where two substrings matched in *different* abilities falsely classified a card (E5, PR #448). New oracle predicates should scope through this module, not test raw full-text.

**Saga chapters** (`engine/oracle_parser.py`) — CR 714: saga oracle text parses into a sequence of chapter abilities (Roman numerals I..V), each granted as a real triggered ability rather than pattern-matched ad hoc.

**EFFECT_REGISTRY** (`engine/card_effects.py`) — 80+ card-specific handlers:

```python
@EFFECT_REGISTRY.register("Orcish Bowmasters", EffectTiming.ETB,
                           description="Deal 1 damage, create Orc Army token")
def bowmasters_etb(game, card, controller, targets=None, item=None):
    ...
```

---

## Layer 2: AI (EV-based decisions)

**EVPlayer** (`ai/ev_player.py`) — the AI decision engine:

- `decide_main_phase(game)` → `("cast_spell", card, targets)` or `None`
- Scores every legal play via `_score_spell()` using `StrategyProfile` weights
- Picks the highest-EV play above `pass_threshold`
- Archetype-specific modifiers: aggro curves out, combo holds fuel, control holds up mana

**StrategyProfile** (`ai/strategy_profile.py`) — per-archetype numerical weights:

- Profiles: AGGRO, MIDRANGE, CONTROL, COMBO, STORM, RAMP, TEMPO
- Per-deck overrides: `DECK_ARCHETYPE_OVERRIDES` (Ruby Storm → "storm")
- Key parameters: `pass_threshold`, `burn_face_mult`, `storm_patience`, `holdback_penalty`

**GoalEngine** (`ai/gameplan.py`) — strategic planning:

- Each deck has ordered Goals loaded from `decks/gameplans/*.json`
- Goals define card_roles (enablers, payoffs, interaction, engines)
- GoalEngine tracks which goal is active

**Planeswalker ability choice** (`ai/pw_ability.py`) — loyalty-ability selection lifted out of the engine (2026-07-05): ranking is description-driven from generic oracle-text patterns (no per-card logic), with two pinned decision rules — a defensive-minus urgency bonus when the race is failing, and a suicide guard that penalizes lines dropping the walker to zero loyalty unless they neutralize a threat. `engine/game_runner.py` only delegates and enforces legality.

**LLM tooling layer** (`ai/llm_*.py`) — offline-optional; sims never call it. Powers dev-side tools (gameplan synthesis, replay diagnosis, doc-freshness and handler audits):

- `ai/llm_models.py` — per-task default model registry with env-var override chain (`select_model`: explicit override → `MTG_LLM_MODEL_<TASK>` → `MTG_LLM_MODEL` → built-in default)
- `ai/llm_budgets.py` — per-task spend budgets
- `ai/llm_metrics.py` — token/cost accounting (price tables)
- `ai/llm_cache.py` — response cache keyed by model string + input
- `ai/llm_agents.py` — the agent definitions the tools in `tools/` drive

**Key scoring flow:**

1. `decide_main_phase()` gets legal plays from `game.get_legal_plays()`
2. Each spell scored by `_score_spell()` → base EV + archetype modifier
3. Storm patience gate: at storm=0, hold rituals/tutors unless ready to go off
4. Landfall deferral: hold land play when landfall creature is castable
5. Best play above `pass_threshold` is selected

---

## Layer 3: Deck Configuration

**Decklists** (`decks/modern_meta.py`) — mainboard + sideboard for all 19 decks (July 2026 meta refresh added Instant Reanimator and Boros Ponza).

**Gameplans** (`decks/gameplans/*.json`) — per-deck strategy:

```json
{
  "deck_name": "Ruby Storm",
  "archetype": "combo",
  "goals": [...],
  "mulligan_keys": ["Ruby Medallion", "Desperate Ritual", ...],
  "mulligan_min_lands": 1,
  "mulligan_max_lands": 3,
  "reactive_only": [],
  "always_early": ["Ruby Medallion"],
  "critical_pieces": ["Grapeshot", "Empty the Warrens"]
}
```

**card_roles** in each goal:

- **enablers** — deployed proactively to support the plan
- **payoffs** — high-impact cards the deck builds toward
- **interaction** — removal, counterspells, disruption
- **engines** — card advantage or mana engines
- **fillers** — role players, cantrips

---

## Counterspell targeting

Counterspells validate targeting restrictions from oracle text:

- `noncreature` in oracle → can't counter creature spells (Spell Pierce, Negate)
- `instant or sorcery` in oracle → can't counter permanents
- Checked at both AI layer (`ai/response.py`) and engine layer (`engine/game_state.py`)

---

## Storm mechanics

Ruby Storm uses a dedicated `STORM` strategy profile with:

- **storm_patience**: hold rituals at storm=0 unless enough fuel + finisher access
- **storm_go_off_bonus / storm_go_off_penalty**: gate the "go off" decision
- **PiF sequencing**: hold Past in Flames until GY has fuel; don't cast with empty GY
- **Finisher gating**: reduce ritual commitment when no Wish/Grapeshot in hand

Other combo decks (Goryo's, Amulet, Living End) use the base COMBO profile WITHOUT storm patience.
