# MTGSimManu — Architecture (canonical)

> Current architecture for the Modern simulator. Replaces the prior content
> of this file (which referenced deleted modules and was marked stale).
> Operational summary lives in `CLAUDE.md`; this file is the reference.

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

**Key scoring flow:**

1. `decide_main_phase()` gets legal plays from `game.get_legal_plays()`
2. Each spell scored by `_score_spell()` → base EV + archetype modifier
3. Storm patience gate: at storm=0, hold rituals/tutors unless ready to go off
4. Landfall deferral: hold land play when landfall creature is castable
5. Best play above `pass_threshold` is selected

---

## Layer 3: Deck Configuration

**Decklists** (`decks/modern_meta.py`) — mainboard + sideboard for all 16 decks.

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
