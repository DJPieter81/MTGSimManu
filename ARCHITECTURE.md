# MTG Simulator — Codebase Architecture

> ⚠️ **STALE — do not rely on this document.** It references deleted modules
> (`ai_player.py` as orchestrator, `spell_decision.py`, `combo_readiness.py`,
> `spell_sequencer.py`, `replay_generator.py`, `replay_generator_v2.py`) and a
> 12-deck count. Current architecture and deck count live in
> [`PROJECT_STATUS.md`](PROJECT_STATUS.md). This file is retained pending a
> full rewrite; scheduled as part of the docs/ reshuffle work.

> A Modern-format Magic: The Gathering game simulator with AI opponents, replay generation, and a web-based replay viewer. The system simulates full games between 12 Modern decks, produces structured replay data, and renders it in an interactive browser UI.

---

## Table of Contents

1. [High-Level Overview](#1-high-level-overview)
2. [Directory Structure](#2-directory-structure)
3. [Data Model Layer (`engine/cards.py`, `engine/mana.py`)](#3-data-model-layer)
4. [Card Database (`engine/card_database.py`)](#4-card-database)
5. [Game State (`engine/game_state.py`)](#5-game-state)
6. [Game Runner (`engine/game_runner.py`)](#6-game-runner)
7. [Card Effects Registry (`engine/card_effects.py`)](#7-card-effects-registry)
8. [Combat System (`engine/combat_manager.py`)](#8-combat-system)
9. [Supporting Engine Systems](#9-supporting-engine-systems)
10. [AI Layer Overview](#10-ai-layer-overview)
11. [AI Player (`ai/ai_player.py`)](#11-ai-player)
12. [Gameplan Engine (`ai/gameplan.py`)](#12-gameplan-engine)
13. [Spell Decision System (`ai/spell_decision.py`)](#13-spell-decision-system)
14. [Board Evaluation (`ai/board_eval.py`)](#14-board-evaluation)
15. [Mana Planning (`ai/mana_planner.py`)](#15-mana-planning)
16. [Turn Planning and Combat AI (`ai/turn_planner.py`, `ai/evaluator.py`)](#16-turn-planning-and-combat-ai)
17. [Combo Systems (`ai/combo_chain.py`, `ai/combo_readiness.py`, `ai/spell_sequencer.py`)](#17-combo-systems)
18. [Strategic Logger (`ai/strategic_logger.py`)](#18-strategic-logger)
19. [Deck Definitions (`decks/modern_meta.py`)](#19-deck-definitions)
20. [Replay Generation (`replay_generator.py`, `replay_generator_v2.py`)](#20-replay-generation)
21. [Commentary Engine (`commentary_engine.py`)](#21-commentary-engine)
22. [Parallel and Best-of-Three Runners](#22-parallel-and-best-of-three-runners)
23. [Full Game Loop Walkthrough](#23-full-game-loop-walkthrough)
24. [AI Decision Flow Walkthrough](#24-ai-decision-flow-walkthrough)
25. [Known Architectural Patterns and Conventions](#25-known-architectural-patterns-and-conventions)

---

## 1. High-Level Overview

The simulator is structured in three major layers:

```
┌──────────────────────────────────────────────────────────┐
│                    PRESENTATION LAYER                     │
│  replay_generator.py → JSON replay data → React viewer   │
│  commentary_engine.py → strategic annotations             │
├──────────────────────────────────────────────────────────┤
│                       AI LAYER                            │
│  ai_player.py ← gameplan.py ← spell_decision.py          │
│  board_eval.py, mana_planner.py, evaluator.py             │
│  turn_planner.py, combo_chain.py, combo_readiness.py      │
├──────────────────────────────────────────────────────────┤
│                     ENGINE LAYER                          │
│  game_state.py (core state machine)                       │
│  game_runner.py (game loop orchestrator)                  │
│  card_effects.py (per-card effect handlers)               │
│  combat_manager.py, continuous_effects.py                 │
│  event_system.py, zone_manager.py, sba_manager.py         │
│  cards.py, mana.py, stack.py                              │
├──────────────────────────────────────────────────────────┤
│                      DATA LAYER                           │
│  card_database.py (MTGJSON loader)                        │
│  decks/modern_meta.py (12 Modern decklists)               │
└──────────────────────────────────────────────────────────┘
```

**Data flows downward** (AI calls into Engine, Engine mutates GameState). **Information flows upward** (Engine exposes state to AI, AI decisions flow to Replay Generator).

---

## 2. Directory Structure

```
mtg_simulator/
├── ai/                          # AI decision-making
│   ├── ai_player.py             # Top-level AI controller (1242 lines)
│   ├── gameplan.py              # GoalEngine + deck gameplans (2078 lines)
│   ├── spell_decision.py        # Concern-based spell selection (1293 lines)
│   ├── board_eval.py            # Board assessment + binary decisions (421 lines)
│   ├── mana_planner.py          # Fetch/land selection (358 lines)
│   ├── evaluator.py             # Card/board value estimation (1030 lines)
│   ├── turn_planner.py          # Combat planning + sequencing (1078 lines)
│   ├── combo_chain.py           # Storm/combo chain simulation (300 lines)
│   ├── combo_readiness.py       # Combo kill assessment (227 lines)
│   ├── spell_sequencer.py       # Spell ordering for combos (182 lines)
│   └── strategic_logger.py      # Structured strategy annotations (279 lines)
│
├── engine/                      # Game rules engine
│   ├── game_state.py            # Core state machine (3160 lines)
│   ├── game_runner.py           # Game loop orchestrator (1063 lines)
│   ├── card_effects.py          # Per-card effect handlers (1260 lines)
│   ├── card_database.py         # MTGJSON card loader (1273 lines)
│   ├── cards.py                 # CardTemplate + CardInstance (435 lines)
│   ├── combat_manager.py        # Combat resolution (305 lines)
│   ├── continuous_effects.py    # Layered P/T modifications (379 lines)
│   ├── event_system.py          # Trigger/replacement event bus (362 lines)
│   ├── zone_manager.py          # Card zone transitions (258 lines)
│   ├── sba_manager.py           # State-based actions (189 lines)
│   ├── mana.py                  # ManaCost + ManaPool (220 lines)
│   ├── stack.py                 # Spell stack (204 lines)
│   ├── bo3_runner.py            # Best-of-three match runner (473 lines)
│   ├── parallel_runner.py       # Multiprocess game runner (420 lines)
│   ├── log_export.py            # CSV/JSON export (182 lines)
│   ├── turn_manager.py          # Turn step sequencing (149 lines)
│   └── priority_system.py       # Priority passing (107 lines)
│
├── decks/
│   └── modern_meta.py           # 12 Modern decklists + metagame shares (514 lines)
│
├── replay_generator.py          # v1 replay generator with Bo3 support (916 lines)
├── replay_generator_v2.py       # v2 replay generator (828 lines)
└── commentary_engine.py         # Strategic commentary annotations (371 lines)
```

---

## 3. Data Model Layer

### `engine/cards.py` — Card Representation

Two core dataclasses define every card in the system:

**`CardTemplate`** is the immutable blueprint for a card (shared across all copies):

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | Card name (e.g., "Ragavan, Nimble Pilferer") |
| `card_types` | `List[CardType]` | CREATURE, INSTANT, SORCERY, LAND, etc. |
| `mana_cost` | `ManaCost` | Parsed mana cost object |
| `power`, `toughness` | `Optional[int]` | Base P/T for creatures |
| `keywords` | `Set[Keyword]` | FLYING, TRAMPLE, FLASH, HASTE, etc. (30 keywords) |
| `tags` | `Set[str]` | Semantic tags: "removal", "threat", "ramp", "cantrip", etc. |
| `oracle_text` | `str` | Full rules text |
| `evoke_cost` | `Optional[ManaCost]` | Alternate evoke cost (Solitude, Subtlety, etc.) |
| `dash_cost` | `Optional[int]` | Dash CMC (Ragavan) |
| `escape_cost` | `Optional[int]` | Escape CMC (Phlage) |
| `produces_mana` | `List[str]` | Colors this land produces |
| `enters_tapped` | `bool` | Whether the land enters tapped |
| `has_delve` | `bool` | Delve mechanic (Murktide Regent) |
| `conditional_mana` | `Optional[Dict]` | Conditional mana production (Tron lands) |

**`CardInstance`** is a mutable game object tracking a specific card's current state:

| Field | Type | Purpose |
|-------|------|---------|
| `template` | `CardTemplate` | Reference to the immutable blueprint |
| `owner` | `int` | Player index (0 or 1) who owns this card |
| `controller` | `int` | Current controller (can differ from owner) |
| `instance_id` | `int` | Unique ID per game (auto-incremented) |
| `zone` | `str` | Current zone: "library", "hand", "battlefield", "graveyard", "exile", "stack" |
| `tapped` | `bool` | Whether the permanent is tapped |
| `summoning_sick` | `bool` | True on the turn it enters the battlefield |
| `plus_counters`, `minus_counters` | `int` | +1/+1 and -1/-1 counters |
| `loyalty_counters` | `int` | Planeswalker loyalty |
| `damage_marked` | `int` | Combat/spell damage marked this turn |
| `attacking`, `blocking` | `bool`, `Optional[int]` | Combat state |

Key computed properties on `CardInstance`:

- `.name` → `template.name`
- `.cmc` → `template.mana_cost.cmc`
- `.power` → `template.power + plus_counters - minus_counters + temp_power_mod`
- `.toughness` → `template.toughness + plus_counters - minus_counters + temp_toughness_mod`
- `.is_land`, `.is_creature`, `.is_instant`, `.is_sorcery` → type checks

### `engine/mana.py` — Mana System

**`ManaCost`** represents a parsed mana cost with fields for each color (W, U, B, R, G) plus generic and special costs (X, Phyrexian). The `parse()` static method converts strings like `"1WU"` or `"WURG"` into structured costs. The `cmc` property returns the total converted mana cost.

**`ManaPool`** tracks available mana with `can_pay(cost)` and `pay(cost)` methods. The `can_pay` method uses a greedy constraint solver that assigns the most-constrained colors first (colors with fewest available sources get assigned before less-constrained ones).

---

## 4. Card Database

### `engine/card_database.py` — MTGJSON Loader (1273 lines)

Loads card data from the MTGJSON `AtomicCards.json` file and converts each entry into a `CardTemplate`. The database handles:

- **Mana cost parsing** from MTGJSON format to `ManaCost` objects
- **Keyword extraction** from oracle text (flying, trample, flash, haste, etc.)
- **Tag inference** from card types and oracle text ("removal" for destroy/exile effects, "cantrip" for draw effects, etc.)
- **Special mechanic detection**: evoke costs, dash costs, escape costs, delve, domain
- **Land color production** from type line and oracle text
- **Double-faced card handling** using the "Front // Back" naming convention

The database is loaded once and shared across all game instances. It contains approximately 21,759 cards.

---

## 5. Game State

### `engine/game_state.py` — Core State Machine (3160 lines)

This is the largest and most critical file. It contains `PlayerState`, `GameState`, and all the mutation methods that implement MTG rules.

### `PlayerState` — Per-Player State

Each player has:

| Zone | Type | Description |
|------|------|-------------|
| `library` | `List[CardInstance]` | Draw pile (shuffled at game start) |
| `hand` | `List[CardInstance]` | Cards in hand |
| `battlefield` | `List[CardInstance]` | Permanents in play |
| `graveyard` | `List[CardInstance]` | Discard pile |
| `exile` | `List[CardInstance]` | Exiled cards |

Plus tracking fields: `life`, `mana_pool`, `lands_played_this_turn`, `spells_cast_this_turn`, `creatures_died_this_turn`, `energy_counters`, `poison_counters`, etc.

Key computed properties:
- `.creatures` → filters `battlefield` for creature type
- `.lands` → filters `battlefield` for land type
- `.planeswalkers` → filters `battlefield` for planeswalker type
- `.untapped_lands` → lands that are not tapped
- `.available_mana_estimate` → count of untapped lands + conditional mana bonuses

### `GameState` — The Full Game

| Field | Purpose |
|-------|---------|
| `players` | `List[PlayerState]` — exactly 2 players |
| `active_player` | `int` — index of the player whose turn it is (0 or 1) |
| `turn_number` | `int` — current turn (increments each time active player switches) |
| `stack` | `Stack` — the spell stack |
| `event_bus` | `EventBus` — trigger/replacement event system |
| `zone_manager` | `ZoneManager` — handles card zone transitions |
| `sba_manager` | `SBAManager` — state-based action checker |
| `continuous_effects` | `ContinuousEffectsManager` — layered P/T modifications |
| `log` | `List[str]` — game log (populated when `verbose=True`) |
| `rng` | `random.Random` — seeded RNG for reproducibility |

### Key Methods on GameState

**Card Operations:**
- `draw_cards(player_idx, count)` — draw from library to hand
- `play_land(player_idx, card)` — play a land (handles fetches, shocklands, landfall triggers)
- `cast_spell(player_idx, card, targets, evoke)` — put spell on stack, pay costs
- `resolve_stack()` — resolve the top item on the stack

**Mana System:**
- `can_cast(player_idx, card)` — checks mana availability AND color requirements using a greedy constraint solver
- `tap_lands_for_mana(player_idx, cost)` — selects which lands to tap, preferring to leave diverse colors untapped

**Land Mechanics:**
- `_crack_fetchland(player_idx, fetch_card)` — searches library for a matching land, uses `mana_planner.choose_fetch_target()` for AI selection, handles shockland tapped/untapped decision
- `_trigger_landfall(player_idx)` — fires landfall triggers (Omnath life gain, etc.)

**Combat:**
- `combat_damage(attackers, blocks)` — resolves combat damage
- `_assign_combat_damage(damage_dealers, targets)` — handles first strike, trample, deathtouch

**Spell Resolution:**
- `_execute_spell_effects(stack_item)` — dispatches to `EFFECT_REGISTRY` for card-specific effects, then falls back to generic logic
- `_handle_permanent_etb(card, controller)` — handles enter-the-battlefield triggers
- `_handle_storm(item)` — copies storm spells
- `_handle_cascade(item)` — cascade mechanic (Living End)

**Zone Transitions:**
- `_creature_dies(creature)` — handles death triggers, graveyard placement
- `_blink_permanent(card, controller)` — exile and return (Ephemerate)
- `_bounce_permanent(permanent)` — return to hand
- `_exile_permanent(permanent)` — move to exile zone

**State-Based Actions:**
- `check_state_based_actions()` — checks lethal damage, 0 toughness, legend rule, planeswalker uniqueness, etc.

---

## 6. Game Runner

### `engine/game_runner.py` — Game Loop Orchestrator (1063 lines)

The `GameRunner` class orchestrates the full game loop. It creates `GameState`, instantiates `AIPlayer` for each side, and drives the turn structure.

### `GameResult` — Output Data

| Field | Type | Description |
|-------|------|-------------|
| `winner` | `Optional[int]` | 0 or 1, None for draw |
| `winner_deck` | `str` | Name of winning deck |
| `turns` | `int` | Total turns played |
| `winner_life` | `int` | Winner's final life total |
| `win_condition` | `str` | "damage", "mill", "combo", "concede", "timeout" |
| `game_log` | `List[str]` | Full game log (only when `verbose=True`) |

### `run_game()` — The Main Game Loop

```python
def run_game(self, deck1_name, deck1_list, deck2_name, deck2_list,
             verbose=False, max_turns=100) -> GameResult:
```

The game loop follows this structure per turn:

```
For each turn (up to max_turns):
  1. Untap step — untap all permanents
  2. Draw step — active player draws a card
  3. Main Phase 1 — _execute_main_phase()
     a. Play a land (AI chooses which)
     b. Loop: AI chooses spell → cast → opponent may respond → resolve
     c. Repeat until AI passes
  4. Combat Phase
     a. AI declares attackers → declare_attackers()
     b. Opponent AI declares blockers → decide_blockers()
     c. Resolve combat damage
  5. Main Phase 2 — _execute_main_phase() again
  6. End Step — _end_step_instant_window()
     a. Opponent may cast instant-speed removal
     b. Opponent may deploy flash creatures
  7. Cleanup — reset turn-tracking, discard to hand size
  8. Switch active player
```

### `_execute_main_phase()` — Spell Casting Loop

This is where most AI decisions happen:

```python
def _execute_main_phase(self, game, ai, opponent_ai):
    # 1. AI decides land to play
    land_decision = ai.decide_main_phase(game)  # may return land
    if land_decision and land_decision[0] == "land":
        game.play_land(active, land_decision[1])

    # 2. Spell casting loop
    while True:
        decision = ai.decide_main_phase(game)
        if decision is None:  # AI passes
            break
        action_type, card, targets = decision
        game.cast_spell(active, card, targets)

        # 3. Opponent response window
        response = opponent_ai.decide_response(game, stack_top)
        if response:
            game.cast_spell(opponent, response[0], response[1])

        # 4. Resolve stack
        self._resolve_stack_loop(game)
```

### `_end_step_instant_window()` — End-of-Turn Plays

At end of turn, the non-active player gets a window to:
1. Cast instant-speed removal (if opponent has threatening creatures)
2. Deploy flash creatures (Bowmasters, Subtlety)
3. Use the `_cast_instant_removal()` method which evaluates threats and selects removal spells

### `_activate_planeswalkers()` — Planeswalker Abilities

After spells, the AI activates planeswalker abilities. Each planeswalker has hardcoded ability logic in `_choose_pw_ability()` (Teferi bounce, Wrenn land recursion, etc.).

---

## 7. Card Effects Registry

### `engine/card_effects.py` — Per-Card Effect Handlers (1260 lines)

A decorator-based registry system where each card's unique effect is registered by name and timing:

```python
EFFECT_REGISTRY = CardEffectRegistry()

@EFFECT_REGISTRY.register("Solitude", EffectTiming.ETB, description="Exile target creature")
def solitude_etb(game, card, controller):
    # Find best opponent creature and exile it
    ...
```

### Effect Timings

| Timing | When It Fires | Example Cards |
|--------|---------------|---------------|
| `ETB` | When permanent enters battlefield | Solitude, Omnath, Bowmasters, Primeval Titan |
| `SPELL_RESOLVE` | When spell resolves from stack | Lightning Bolt, Fatal Push, Thoughtseize |
| `DEATH` | When creature dies | (used for death triggers) |
| `ATTACK` | When creature attacks | (used for attack triggers) |

### Registered Cards (60+ handlers)

**ETB Effects:** Solitude (exile creature), Subtlety (bounce creature to library), Endurance (shuffle graveyards), Omnath (gain 4 life), Murktide Regent (delve + counters), Bowmasters (1 damage + create Orc Army), Primeval Titan (search 2 lands), Archon of Cruelty (drain + discard + draw), Leyline Binding (exile permanent), and more.

**Spell Resolution Effects:** Lightning Bolt (3 damage), Fatal Push (destroy CMC ≤ 2/4), Thoughtseize (discard), Tribal Flames (domain damage), Grapeshot (storm copies), Past in Flames (flashback), Goryo's Vengeance (reanimate), Ephemerate (blink), Wrath of the Skies (board wipe), and more.

### Fallback Logic

If a card has no registered handler, `_execute_spell_effects()` in `game_state.py` uses generic logic based on card tags:
- "removal" tag → destroy target creature
- "cantrip" tag → draw a card
- "ramp" tag → search for a land
- Creature spell → enters battlefield as permanent

---

## 8. Combat System

### `engine/combat_manager.py` — Combat Resolution (305 lines)

The `CombatManager` handles the full combat sequence:

1. **`declare_attackers()`** — marks creatures as attacking, applies battle cry triggers
2. **`declare_blockers()`** — assigns blockers to attackers, validates blocking legality
3. **`resolve_combat_damage()`** — handles first strike, regular damage, trample, deathtouch, lifelink
4. **`end_combat()`** — cleans up combat state, handles dash return-to-hand

Combat damage assignment follows MTG rules:
- First strike damage is dealt first, then state-based actions are checked
- Trample excess damage goes to the defending player
- Deathtouch requires only 1 damage to be lethal
- Lifelink gains life equal to damage dealt

---

## 9. Supporting Engine Systems

### `engine/event_system.py` — Trigger and Replacement Events (362 lines)

An event bus that handles triggered abilities and replacement effects:

- **`EventType`** enum: CREATURE_DIES, CREATURE_ETB, SPELL_CAST, DAMAGE_DEALT, LIFE_GAINED, CARD_DRAWN, LANDFALL, etc.
- **`register_trigger()`** — registers a callback for an event type (e.g., "when a creature dies")
- **`register_replacement()`** — registers a replacement effect (e.g., "if you would draw, instead...")
- **`fire_event()`** — fires an event, applies replacements first, then collects and executes triggers

### `engine/zone_manager.py` — Card Zone Transitions (258 lines)

Handles moving cards between zones (library, hand, battlefield, graveyard, exile, stack) with proper cleanup:
- Removes continuous effects when leaving battlefield
- Resets damage, counters, and combat state
- Fires appropriate events (ETB, death, etc.)

### `engine/sba_manager.py` — State-Based Actions (189 lines)

Checks and performs state-based actions in a loop until no more actions are needed:
- Creatures with lethal damage or 0 toughness die
- Players at 0 or less life lose
- Legend rule (keep newest, sacrifice older copy)
- Planeswalker uniqueness rule
- Poison counter check (10+ = lose)

### `engine/continuous_effects.py` — Layered Effects (379 lines)

Implements MTG's layer system for continuous effects:
- Equipment bonuses (Cranial Plating, Nettlecyst)
- Lord effects (tribal pumps)
- Pump spells (Mutagenic Growth)
- Effects are applied in layer order and recalculated when the board changes

### `engine/stack.py` — The Spell Stack (204 lines)

A LIFO stack for spells and abilities with priority tracking. Supports `push`, `pop`, `peek`, and priority passing between players.

### `engine/turn_manager.py` — Turn Structure (149 lines)

Defines the turn step sequence (untap, upkeep, draw, main1, combat phases, main2, end, cleanup) and tracks which steps allow priority.

---

## 10. AI Layer Overview

The AI system is structured as a pipeline where each layer adds strategic intelligence:

```
AIPlayer (top-level controller)
  ├── GoalEngine (deck-specific strategy)
  │   ├── DeckGameplan (static configuration)
  │   ├── BoardAssessor (board state evaluation)
  │   └── Goal transitions (when to shift strategy)
  │
  ├── SpellDecision (concern-based spell selection)
  │   ├── SURVIVE concern (am I dying?)
  │   ├── ANSWER concern (must I remove a threat?)
  │   ├── ADVANCE concern (progress my gameplan)
  │   └── EFFICIENT concern (mana efficiency)
  │
  ├── BoardEval (binary action decisions)
  │   ├── Evoke evaluation
  │   ├── Dash evaluation
  │   └── Combo go/wait evaluation
  │
  ├── ManaPlanner (land/fetch decisions)
  │   ├── analyze_mana_needs() — what colors does my hand need?
  │   ├── score_land() — which fetch target is best?
  │   └── choose_best_land() — which land to play from hand?
  │
  ├── TurnPlanner (combat sequencing)
  │   ├── CombatPlanner — attack/block optimization
  │   └── Spell sequencing around combat
  │
  └── ComboChain / ComboReadiness (combo-specific)
      ├── Storm chain simulation
      └── Kill assessment
```

---

## 11. AI Player

### `ai/ai_player.py` — Top-Level AI Controller (1242 lines)

`AIPlayer` is the main interface between the game engine and the AI decision system. Each player in a game has an `AIPlayer` instance.

### Construction

```python
class AIPlayer:
    def __init__(self, player_idx, deck_name, card_db, rng):
        self.player_idx = player_idx
        self.deck_name = deck_name
        self.goal_engine = GoalEngine(DECK_GAMEPLANS[deck_name])
        self.strategic_logger = StrategicLogger()
```

### Key Decision Methods

**`decide_mulligan(hand, cards_in_hand)`** — Delegates to `GoalEngine.decide_mulligan()`. Checks for minimum lands (2-4), key cards (`mulligan_keys`), and combo pieces (`mulligan_combo_sets`).

**`decide_main_phase(game)`** — The primary decision method, called repeatedly during each main phase:

```
1. Get legal plays (lands + castable spells)
2. If lands available → choose best land via GoalEngine._choose_land()
3. If spells available → delegate to GoalEngine.choose_action()
4. Return ("land", card, []) or ("spell", card, targets) or None (pass)
```

**`decide_attackers(game)`** — Uses `TurnPlanner.plan_attack()` to evaluate all possible attack configurations and pick the best one. Considers evasion, trample, opponent blockers, and life race math.

**`decide_blockers(game, attackers)`** — Evaluates blocking assignments to minimize damage while preserving valuable creatures. Uses `TurnPlanner` for block optimization.

**`decide_response(game, stack_item)`** — Called when opponent casts a spell. Evaluates whether to counter it:
1. Check if we have counterspells in hand
2. Evaluate the threat level of the spell on the stack (`_evaluate_stack_threat()`)
3. If threat is high enough, counter it
4. Also checks for instant-speed removal as a response

**`_choose_targets(game, spell)`** — Target selection for removal spells. Picks the highest-value opponent creature, considering P/T, keywords, and strategic importance.

---

## 12. Gameplan Engine

### `ai/gameplan.py` — GoalEngine + Deck Gameplans (2078 lines)

This is the strategic brain. Each deck has a `DeckGameplan` that defines its goals, card roles, and transition conditions. The `GoalEngine` manages goal progression during a game.

### `DeckGameplan` — Static Deck Configuration

```python
@dataclass
class DeckGameplan:
    deck_name: str
    goals: List[Goal]              # Ordered list of strategic goals
    mulligan_keys: Set[str]        # Cards that make a hand keepable
    mulligan_min_lands: int        # Minimum lands to keep (default 2)
    mulligan_max_lands: int        # Maximum lands to keep (default 4)
    reactive_only: Set[str]        # Cards that should only be cast reactively
    always_early: Set[str]         # Cards to deploy ASAP (e.g., Ragavan)
    archetype: str                 # "aggro", "midrange", "control", "combo"
    land_priorities: Dict[str, float]  # Which lands to play first
```

### `Goal` — A Strategic Objective

```python
@dataclass
class Goal:
    goal_type: GoalType            # DEPLOY, DISRUPT, INTERACT, COMBO, GRIND_VALUE, etc.
    description: str               # Human-readable description
    card_roles: Dict[str, Set[str]]  # Maps role → set of card names
    transition_check: Optional[str]  # Method name for goal transition
    hold_mana: bool                # Whether to hold mana for interaction
    resource_target: int           # Resource threshold for transition
```

**`card_roles`** maps role names to card sets. Roles include:
- `"payoffs"` — the cards this goal wants to deploy (highest priority)
- `"engines"` — value engines that generate advantage
- `"enablers"` — cards that enable the payoffs
- `"interaction"` — removal and counterspells
- `"setup"` — cantrips and card selection

### `GoalType` Enum

| Type | Description | Example Decks |
|------|-------------|---------------|
| `DEPLOY` | Deploy threats efficiently | Domain Zoo, Boros Energy |
| `DISRUPT` | Interact while deploying threats | Dimir Midrange |
| `INTERACT` | Interact early, deploy value later | 4c Omnath |
| `COMBO` | Assemble and execute combo | Ruby Storm, Living End |
| `GRIND_VALUE` | Generate incremental advantage | Dimir Midrange (late game) |
| `TEMPO` | Cheap threats + disruption | Izzet Prowess |
| `RAMP` | Accelerate mana development | Amulet Titan, Eldrazi Tron |
| `SETUP` | Set up graveyard or resources | Goryo's Vengeance |

### `GoalEngine` — Runtime Goal Management

The `GoalEngine` tracks the current goal and manages transitions:

```python
class GoalEngine:
    def __init__(self, gameplan: DeckGameplan):
        self.gameplan = gameplan
        self.goal_idx = 0  # Start at first goal

    @property
    def current_goal(self) -> Goal:
        return self.gameplan.goals[self.goal_idx]

    def choose_action(self, game, player_idx, player, spells, assessment):
        # 1. Check goal transitions
        self._check_transitions(game, player_idx, assessment)
        # 2. Check emergency overrides
        self._check_overrides(game, player_idx, assessment)
        # 3. Choose spell via concern-based system
        return self._choose_spell(game, player_idx, player, spells, assessment)
```

**Goal Transitions** happen when conditions are met (e.g., "graveyard has 5+ creatures" → transition from SETUP to COMBO). Each goal can specify a `transition_check` method.

**Emergency Overrides** fire when the board state demands immediate action regardless of the current goal (e.g., opponent has lethal on board → switch to survival mode).

### Example: Domain Zoo Gameplan

```python
DeckGameplan(
    deck_name="Domain Zoo",
    goals=[
        Goal(
            goal_type=GoalType.DEPLOY,
            description="Deploy efficient threats: Ragavan → Kavu/Brawler → Scion",
            card_roles={
                "payoffs": {"Ragavan, Nimble Pilferer", "Nishoba Brawler",
                           "Territorial Kavu", "Scion of Draco"},
                "interaction": {"Orcish Bowmasters", "Tribal Flames"},
            },
        ),
    ],
    always_early={"Ragavan, Nimble Pilferer"},
    archetype="aggro",
)
```

### Example: Dimir Midrange Gameplan

```python
DeckGameplan(
    deck_name="Dimir Midrange",
    goals=[
        Goal(
            goal_type=GoalType.DISRUPT,
            description="Disrupt opponent while deploying efficient threats",
            card_roles={
                "enablers": {"Orcish Bowmasters", "Dauthi Voidwalker", "Psychic Frog"},
                "payoffs": {"Murktide Regent"},
                "interaction": {"Fatal Push", "Counterspell", "Drown in the Loch"},
                "setup": {"Consider", "Thoughtseize"},
            },
            hold_mana=True,  # Hold mana for interaction
        ),
        Goal(
            goal_type=GoalType.GRIND_VALUE,
            description="Grind value with Frog and Murktide",
            ...
        ),
    ],
    reactive_only={"Counterspell", "Spell Pierce", "Subtlety"},
    archetype="midrange",
)
```

### `BoardAssessor` — Board State Evaluation

The `BoardAssessor.assess()` method produces a `BoardAssessment` snapshot:

```python
@dataclass
class BoardAssessment:
    my_clock: int          # Turns until I kill opponent (999 = no clock)
    opp_clock: int         # Turns until opponent kills me
    my_life: int
    opp_life: int
    my_board_power: int    # Total power on my board
    opp_board_power: int
    my_creatures: int      # Number of my creatures
    opp_creatures: int
    my_hand_size: int
    my_mana: int
    opp_mana: int
    turn_number: int
    has_lethal: bool       # Can I kill this turn?
    am_dead_next: bool     # Will I die next turn?
    resource_ready: bool   # Is my goal's resource condition met?
```

The `am_dead_next` flag is critical — it triggers the SURVIVE concern in spell decision.

---

## 13. Spell Decision System

### `ai/spell_decision.py` — Concern-Based Spell Selection (1293 lines)

This is the core decision-making algorithm. It uses a **concern pipeline** — a prioritized list of strategic concerns that are checked in order. The first concern that returns a spell wins.

### `SpellDecision` — Output

```python
@dataclass
class SpellDecision:
    card: Optional[CardInstance]    # The spell to cast (None = pass)
    concern: str                   # Which concern chose it ("survive", "answer", "advance", "efficient")
    reasoning: str                 # Human-readable explanation
    alternatives: List[Tuple[str, str]]  # Other considered options
```

### `choose_spell()` — Entry Point

```python
def choose_spell(engine, castable, game, player_idx, assessment):
    ctx = _build_context(castable, game, player_idx, assessment, engine)
    castable = _apply_pre_filters(ctx)  # Remove reactive-only cards, etc.

    # Concern pipeline (checked in order):
    result = _check_cycling_priority(ctx)   # Cycling for combo decks
    if not result: result = _concern_survive(ctx)    # Am I dying?
    if not result: result = _concern_answer(ctx)     # Must I remove a threat?
    if not result: result = _concern_advance(ctx)    # Progress my gameplan
    if not result: result = _concern_efficient(ctx)  # Mana-efficient plays

    if not result:
        result = SpellDecision(None, "pass", _pass_reasoning(ctx))
    return result
```

### Concern Pipeline Detail

**1. `_concern_survive(ctx)` — Survival Priority**

Fires when `am_dying` is True (opponent's clock ≤ 4 turns). Looks for:
- Removal that kills the biggest threat
- Creatures that can block (sorted by toughness)
- Any castable spell as a last resort

**Important:** For non-combo archetypes, enablers are NOT filtered from survival candidates. This was a critical bug fix — previously, all Dimir creatures were classified as "combo pieces" and filtered out, leaving only cantrips.

**2. `_concern_answer(ctx)` — Threat Response**

Fires when opponent has a high-value threat on board. Uses `_best_removal_for_threats()` to find the most efficient removal spell for the biggest threat.

**3. `_concern_advance(ctx)` — Gameplan Progression**

The most complex concern. Dispatches based on `GoalType`:

| Goal Type | Handler | Behavior |
|-----------|---------|----------|
| DEPLOY | `_advance_proactive()` | Deploy threats on curve, prioritize payoffs |
| DISRUPT | `_advance_reactive()` | Deploy threats when safe, hold mana for interaction |
| INTERACT | `_advance_reactive()` | Same as DISRUPT |
| COMBO | `_advance_combo()` | Execute combo chain when ready |
| GRIND_VALUE | `_advance_grind()` | Deploy value engines, prioritize card advantage |
| TEMPO | `_advance_proactive()` | Deploy cheap threats aggressively |
| RAMP | `_advance_setup()` | Play ramp spells, then deploy threats |
| SETUP | `_advance_setup()` | Set up resources (graveyard, etc.) |

Before dispatching, `_concern_advance` checks `role_cards` — if the current goal has cards in specific roles (payoffs, engines, enablers), it tries to deploy them using `_best_role_card()`.

**`_best_role_card()`** prioritizes roles: engines (6) > payoffs (5) > enablers (4) > interaction (3) > setup (2).

**`_should_hold_for_interaction()`** — For reactive decks (DISRUPT, INTERACT), checks whether to hold mana open for counterspells/removal instead of deploying a threat. Returns False (don't hold) when dying or when the deck has no board presence.

**4. `_concern_efficient(ctx)` — Mana Efficiency**

Last resort — if no other concern fires, play the most mana-efficient spell to avoid wasting mana. Uses `_most_mana_efficient()` to find the spell closest to available mana.

### `_can_kill()` — Removal Targeting

Evaluates whether a removal spell can kill a target creature:

```python
def _can_kill(removal, target, game, player_idx):
    # Check damage-based removal (Bolt deals 3, target has 3 toughness → kills)
    # Check destroy effects (Fatal Push → CMC check)
    # Check exile effects (Solitude, Leyline Binding → always kills)
    # Check conditional destroy (Fatal Push CMC ≤ 2, or ≤ 4 with revolt)
```

**Fatal Push CMC awareness** was a bug fix — `_can_kill` now checks whether revolt is active (a permanent left the battlefield this turn) to determine the CMC ceiling (2 without revolt, 4 with revolt).

### `_apply_pre_filters()` — Card Filtering

Before the concern pipeline runs, pre-filters remove cards that shouldn't be cast proactively:
- `reactive_only` cards (Counterspell, Subtlety) are removed during main phase
- Cards that violate the legend rule are removed
- Cards with no valid targets are removed

When `am_dying` is True, an emergency re-include adds back reactive cards (except counterspells during main phase — this was a bug fix).

---

## 14. Board Evaluation

### `ai/board_eval.py` — Board Assessment and Binary Decisions (421 lines)

### `assess_board()` — Board State Snapshot

Produces a `BoardAssessment` with clock calculations:

```python
def assess_board(game, player_idx) -> BoardAssessment:
    # Calculate my clock: how many turns until I kill opponent
    # Calculate opp clock: how many turns until opponent kills me
    # Factor in evasion (flying, unblockable)
    # Factor in burn spells in hand
    # Determine am_dead_next, has_lethal
```

### `evaluate_action()` — Binary Action Decisions

Dispatches to specialized evaluators for binary choices:

| Action Type | Evaluator | Decision |
|-------------|-----------|----------|
| `EVOKE` | `_eval_evoke()` | Should I evoke this creature or hard-cast it? |
| `DASH` | `_eval_dash()` | Should I dash Ragavan or hard-cast it? |
| `COMBO` | `_eval_combo()` | Should I go for the combo now or wait? |
| `BLOCK` | `_eval_block()` | Should I block with this creature? |

**`_eval_evoke()`** — Evaluates whether to evoke a creature (pay alternate cost, get ETB, sacrifice). Checks:
- Can I hard-cast it now or next turn?
- How much pressure am I under?
- **Does the ETB have valid targets?** (Bug fix: Subtlety won't evoke into empty board)

---

## 15. Mana Planning

### `ai/mana_planner.py` — Land and Fetch Decisions (358 lines)

### `analyze_mana_needs()` — Hand Analysis

Scans the hand and produces a `ManaNeeds` object:

```python
@dataclass
class ManaNeeds:
    total_needed: int          # Total mana needed for all spells
    colors_needed: Dict[str, int]  # How many of each color needed
    colors_missing: Set[str]   # Colors we can't produce yet
    max_cmc: int               # Highest CMC spell in hand
    min_cmc: int               # Lowest CMC spell in hand
    domain_count: int          # Number of basic land types we control
```

### `score_land()` — Fetch Target Scoring

When a fetch land is cracked, `score_land()` evaluates each possible target:

```
Score = (missing_color_bonus * 20)    # +20 per missing color this land provides
      + (needed_color_bonus * 3)      # +3 per needed color
      + (domain_bonus * 5)            # +5 for new basic land type (domain)
      + (tempo_bonus)                 # Bonus for entering untapped
      - (life_cost)                   # -2 for shockland life payment
```

### `choose_best_land()` — Land from Hand

When multiple lands are in hand, selects the best one to play based on mana needs, tempo (untapped vs tapped), and color coverage.

### `choose_fetch_target()` — Fetch Land Resolution

Filters the library for legal targets (matching fetch colors), scores each one, and returns the best. The shockland tapped/untapped decision uses `analyze_mana_needs()` — if the hand has spells that need the mana this turn, the land enters untapped (pay 2 life).

---

## 16. Turn Planning and Combat AI

### `ai/turn_planner.py` — Combat Optimization (1078 lines)

The `TurnPlanner` and `CombatPlanner` handle attack/block decisions using virtual board simulation.

### `VirtualBoard` — Simulation State

A lightweight copy of the board state used for combat simulation:

```python
@dataclass
class VirtualBoard:
    my_creatures: List[VirtualCreature]
    opp_creatures: List[VirtualCreature]
    my_life: int
    opp_life: int
    my_spells: List[VirtualSpell]
```

### `CombatPlanner.plan_attack()` — Attack Optimization

Generates all possible attack configurations (up to a pruned subset for large boards), simulates each one including predicted blocks, and returns the best configuration:

```
For each attack configuration:
  1. Predict opponent's blocks (greedy: block most valuable attacker first)
  2. Simulate combat damage (first strike, trample, deathtouch)
  3. Score the resulting board state
  4. Track best configuration
```

### `TurnPlanner.plan_turn()` — Spell + Combat Sequencing

Evaluates different orderings of spells and combat:
- Deploy creature → attack (creature can't attack due to summoning sickness, but adds to board)
- Remove blocker → attack (removal clears the way)
- Attack → deploy post-combat (save mana for post-combat plays)
- Hold up mana (leave mana open for opponent's turn)

---

## 17. Combo Systems

### `ai/combo_chain.py` — Storm Chain Simulation (300 lines)

Simulates storm combo sequences for Ruby Storm:

```python
def find_all_chains(hand, mana, storm_count, medallion_count, ...):
    # Try all permutations of castable spells
    # Track mana generation (rituals), storm count, card draw
    # Find the sequence that maximizes Grapeshot damage or Empty the Warrens tokens
```

### `ai/combo_readiness.py` — Kill Assessment (227 lines)

Evaluates whether a combo deck should "go off" this turn:

```python
@dataclass
class ComboReadiness:
    best_damage: int       # Max damage from best chain
    best_tokens: int       # Max tokens from best chain
    missing_pieces: List   # What's needed to combo
    confidence: float      # 0-1 confidence in kill

def decide_go_or_wait(readiness) -> ComboAction:
    # GO if we can definitely kill
    # WAIT if we need more pieces
    # SETUP if we should cantrip/tutor first
```

### `ai/spell_sequencer.py` — Spell Ordering (182 lines)

Orders spells for optimal sequencing in combo turns:
- Rituals before payoffs
- Cantrips before rituals (to find more pieces)
- Cost reducers (Medallions) before everything

---

## 18. Strategic Logger

### `ai/strategic_logger.py` — Structured Annotations (279 lines)

Produces structured strategy annotations that feed into the replay viewer's commentary:

```python
class StrategicLogger:
    def log_spell(self, player_idx, spell_name, concern, reasoning, ...):
        # Creates a StrategicAnnotation with category, action, reasoning
    def log_attack(self, player_idx, attackers, expected_damage, ...):
    def log_mulligan(self, player_idx, deck_name, hand, keep, ...):
    def log_transition(self, player_idx, old_goal, new_goal, ...):
```

Annotations are drained by the replay generator and embedded in the replay JSON for the viewer to display.

---

## 19. Deck Definitions

### `decks/modern_meta.py` — 12 Modern Decklists (514 lines)

Contains full 60-card mainboards and 15-card sideboards for each deck, plus metagame share percentages:

| Deck | Archetype | Metagame Share | Key Cards |
|------|-----------|----------------|-----------|
| Boros Energy | Aggro-Midrange | 21.1% | Ragavan, Ajani, Phlage, Galvanic Discharge |
| Jeskai Blink | Control | 9.2% | Solitude, Ephemerate, Teferi, Spell Queller |
| Eldrazi Tron | Ramp | 7.1% | Thought-Knot Seer, Reality Smasher, Tron lands |
| Ruby Storm | Combo | 6.2% | Grapeshot, Past in Flames, Rituals |
| Affinity | Aggro | 6.1% | Mox Opal, Cranial Plating, Thought Monitor |
| Izzet Prowess | Tempo | 4.9% | Monastery Swiftspear, Lightning Bolt |
| Amulet Titan | Combo-Ramp | 4.1% | Primeval Titan, Amulet of Vigor |
| Goryo's Vengeance | Combo | 3.6% | Griselbrand, Goryo's Vengeance |
| Living End | Combo | 3.6% | Living End, Cascade spells |
| Domain Zoo | Aggro | 2.9% | Ragavan, Scion of Draco, Tribal Flames |
| Dimir Midrange | Midrange | 2.8% | Murktide, Bowmasters, Counterspell |
| 4c Omnath | Control-Midrange | 3.5% | Omnath, Solitude, Teferi, Endurance |

---

## 20. Replay Generation

### `replay_generator.py` / `replay_generator_v2.py` — Structured Replay Output

The `ReplayGenerator` class runs a game while capturing snapshots at every meaningful state change:

```python
class ReplayGenerator:
    def run(self, d1_name, d1_list, d2_name, d2_list):
        # 1. Set up game state
        # 2. For each turn:
        #    a. Capture snapshot before each action
        #    b. Execute action (land, spell, combat, etc.)
        #    c. Capture snapshot after action
        #    d. Classify log lines into structured events
        # 3. Return JSON with all snapshots + events + annotations
```

### Replay JSON Structure

```json
{
  "metadata": {
    "deck1": "Domain Zoo",
    "deck2": "Dimir Midrange",
    "winner": "Domain Zoo",
    "turns": 12,
    "seed": 42
  },
  "snapshots": [
    {
      "turn": 1,
      "phase": "main1",
      "active_player": 0,
      "players": [
        {
          "name": "Domain Zoo",
          "life": 20,
          "hand_size": 7,
          "library_size": 53,
          "battlefield": [...],
          "graveyard": [...],
          "lands": [...]
        },
        ...
      ],
      "events": [
        {"category": "spell", "type": "cast", "player": 0, "card": "Ragavan", "text": "..."}
      ],
      "commentary": [
        {"text": "Ragavan on turn 1 is the ideal opener for Zoo.", "category": "strategy"}
      ]
    }
  ]
}
```

### Event Classification

The `classify_log()` function parses raw game log lines into structured events:

| Category | Types | Example |
|----------|-------|---------|
| `spell` | cast, resolve, counter | "Cast Lightning Bolt" |
| `combat` | attack, block, damage | "Attack with Ragavan" |
| `land` | play, fetch, crack | "Play Misty Rainforest" |
| `life` | gain, lose, damage | "Gain 4 life" |
| `zone` | die, exile, bounce, discard | "Ragavan dies" |
| `ability` | activate, trigger | "Teferi [-3]" |
| `game` | win, lose, draw | "P1 wins" |

---

## 21. Commentary Engine

### `commentary_engine.py` — Strategic Annotations (371 lines)

Post-processes replay snapshots to add strategic commentary by detecting patterns:

| Pattern | Detection Logic | Example Commentary |
|---------|----------------|-------------------|
| Blink + ETB | Ephemerate targeting a creature with ETB | "Ephemerate on Solitude re-triggers the exile effect" |
| Evoke sacrifice | Creature evoked then sacrificed | "Solitude evoked for free exile, then sacrificed" |
| Cascade → Living End | Cascade spell finding Living End | "Cascade into Living End wipes the board and reanimates" |
| Fetch + Shock | Fetch cracking into shockland | "Fetching Steam Vents untapped costs 3 life total" |
| Storm sequence | Multiple spells cast in one turn | "Storm count reaches 7 before Grapeshot" |
| Counter war | Multiple counterspells on the stack | "Counter war over Omnath" |
| Board wipe | Wrath effect clearing 3+ creatures | "Wrath of the Skies clears 4 creatures" |
| Big life swing | Life total changes by 5+ in one snapshot | "Phlage swings the life race by 6" |

---

## 22. Parallel and Best-of-Three Runners

### `engine/parallel_runner.py` — Multiprocess Simulation (420 lines)

Uses Python's `multiprocessing.Pool` to run many games in parallel:

```python
class ParallelSimulator:
    def __init__(self, config: SimulationConfig):
        self.config = config  # decks, games_per_matchup, num_workers

    def run(self) -> List[dict]:
        # Distribute games across CPU cores
        # Each worker loads its own CardDatabase
        # Results aggregated into win rates, game lengths, etc.
```

### `engine/bo3_runner.py` — Best-of-Three Matches (473 lines)

Simulates full best-of-three matches with sideboarding:

```python
class Bo3Runner:
    def run_match(self, deck1, deck2) -> Bo3MatchResult:
        # Game 1: mainboard vs mainboard
        # Sideboard: compute_sideboard_plan() based on matchup
        # Game 2: post-sideboard
        # Game 3 (if needed): post-sideboard
```

Sideboarding uses `_compute_sideboard_plan()` which evaluates which sideboard cards are good against the opponent's deck and which mainboard cards are weak.

---

## 23. Full Game Loop Walkthrough

Here is the complete flow for a single game from start to finish:

```
GameRunner.run_game()
  │
  ├── 1. Build decks: CardDatabase.lookup() for each card name
  ├── 2. Create GameState with 2 PlayerStates
  ├── 3. Shuffle libraries (seeded RNG)
  ├── 4. Draw opening hands (7 cards each)
  ├── 5. Mulligan decisions (AIPlayer.decide_mulligan())
  │
  ├── 6. GAME LOOP (up to 100 turns):
  │   │
  │   ├── Untap Step: game.untap_step(active)
  │   ├── Draw Step: game.draw_cards(active, 1)
  │   │
  │   ├── Main Phase 1: _execute_main_phase()
  │   │   ├── AI chooses land → game.play_land()
  │   │   │   ├── Fetch land? → _crack_fetchland()
  │   │   │   │   ├── mana_planner.choose_fetch_target()
  │   │   │   │   ├── Shockland? → analyze_mana_needs() for tapped/untapped
  │   │   │   │   └── _trigger_landfall()
  │   │   │   └── Regular land → add to battlefield
  │   │   │
  │   │   └── Spell loop:
  │   │       ├── AI: GoalEngine.choose_action()
  │   │       │   ├── BoardAssessor.assess() → BoardAssessment
  │   │       │   ├── _check_transitions() → maybe advance goal
  │   │       │   └── choose_spell() → SpellDecision
  │   │       │       ├── _apply_pre_filters()
  │   │       │       ├── _concern_survive()
  │   │       │       ├── _concern_answer()
  │   │       │       ├── _concern_advance()
  │   │       │       └── _concern_efficient()
  │   │       │
  │   │       ├── game.cast_spell() → put on stack
  │   │       │   ├── Pay mana (tap_lands_for_mana)
  │   │       │   ├── Evoke? → evaluate_action(EVOKE)
  │   │       │   └── Push to stack
  │   │       │
  │   │       ├── Opponent response: decide_response()
  │   │       │   ├── _evaluate_stack_threat()
  │   │       │   └── Maybe counter with Counterspell/Force
  │   │       │
  │   │       └── _resolve_stack_loop()
  │   │           ├── EFFECT_REGISTRY.execute() for card-specific effects
  │   │           ├── _handle_permanent_etb() for permanents
  │   │           ├── check_state_based_actions()
  │   │           └── process_triggers()
  │   │
  │   ├── Combat Phase:
  │   │   ├── AI: decide_attackers() → TurnPlanner.plan_attack()
  │   │   ├── CombatManager.declare_attackers()
  │   │   ├── Opponent: decide_blockers()
  │   │   ├── CombatManager.declare_blockers()
  │   │   └── CombatManager.resolve_combat_damage()
  │   │
  │   ├── Main Phase 2: _execute_main_phase() (same as MP1)
  │   │
  │   ├── End Step: _end_step_instant_window()
  │   │   ├── Opponent may cast instant removal
  │   │   └── Opponent may deploy flash creatures
  │   │
  │   ├── Cleanup: end_of_turn_cleanup()
  │   │   ├── Reset turn tracking
  │   │   ├── Discard to hand size
  │   │   └── Clear damage marked
  │   │
  │   └── Switch active player
  │
  └── 7. Build GameResult
```

---

## 24. AI Decision Flow Walkthrough

When the AI needs to choose a spell during main phase, here is the complete decision flow:

```
AIPlayer.decide_main_phase()
  │
  ├── Get castable spells: game.can_cast() for each card in hand
  │   └── can_cast() checks:
  │       ├── Enough mana? (CMC ≤ available mana)
  │       ├── Right colors? (greedy constraint solver)
  │       ├── Sorcery speed? (only during main phase)
  │       └── Evoke available? (alternate cost check)
  │
  ├── Filter legend rule violations
  │
  └── GoalEngine.choose_action()
      │
      ├── BoardAssessor.assess() → produces BoardAssessment
      │   ├── my_clock: turns until I kill (based on board power vs opp life)
      │   ├── opp_clock: turns until opponent kills me
      │   ├── am_dead_next: opp_clock ≤ 1
      │   └── has_lethal: my_clock ≤ 1
      │
      ├── _check_transitions()
      │   └── If current goal's transition condition met → advance to next goal
      │
      ├── _check_overrides()
      │   └── Emergency: if combo is ready, override to COMBO goal
      │
      └── choose_spell(engine, castable, game, player_idx, assessment)
          │
          ├── _build_context() → _DecisionContext
          │   ├── Categorize cards: threats, removal, cantrips, interaction
          │   ├── Identify combo_pieces from all goals' card_roles
          │   └── Calculate am_dying (opp_clock ≤ 4)
          │
          ├── _apply_pre_filters()
          │   ├── Remove reactive_only cards (Counterspell, etc.)
          │   ├── If am_dying → emergency re-include (except counterspells)
          │   └── Remove cards with no valid targets
          │
          ├── CONCERN PIPELINE:
          │
          │   ┌── _concern_survive() [if am_dying]
          │   │   ├── Find removal that kills biggest threat
          │   │   ├── Find creatures that can block
          │   │   ├── For non-combo: enablers ARE valid survival plays
          │   │   └── Last resort: any castable spell
          │   │
          │   ├── _concern_answer() [if high-value threat on board]
          │   │   └── _best_removal_for_threats()
          │   │       └── _can_kill() for each removal × each threat
          │   │
          │   ├── _concern_advance() [progress gameplan]
          │   │   ├── Check role_cards → _best_role_card()
          │   │   ├── Check always_early cards
          │   │   ├── _should_hold_for_interaction()?
          │   │   └── Dispatch by GoalType:
          │   │       ├── DEPLOY → _advance_proactive()
          │   │       ├── DISRUPT/INTERACT → _advance_reactive()
          │   │       ├── COMBO → _advance_combo()
          │   │       ├── GRIND_VALUE → _advance_grind()
          │   │       └── RAMP/SETUP → _advance_setup()
          │   │
          │   └── _concern_efficient() [mana efficiency]
          │       └── _most_mana_efficient(castable)
          │
          └── Return SpellDecision(card, concern, reasoning, alternatives)
```

---

## 25. Known Architectural Patterns and Conventions

### Seeded Randomness

All randomness flows through a single `random.Random` instance seeded at game start. This makes games fully reproducible — the same seed always produces the same game.

### Tag-Based Card Classification

Cards are classified by `tags` on their `CardTemplate` (e.g., "removal", "threat", "cantrip", "ramp"). The AI uses these tags extensively for decision-making without needing to know specific card names.

### Concern-Based Decision Making

The spell decision system uses a prioritized concern pipeline rather than a single scoring function. This makes decisions more interpretable and debuggable — you can see exactly which concern chose the spell and why.

### Effect Registry Pattern

Card-specific effects are registered by name in a global registry. This decouples card logic from the engine — new cards can be added by registering a handler without modifying the engine code.

### Virtual Board Simulation

Combat decisions use a lightweight "virtual board" copy to simulate outcomes without mutating the real game state. This allows the AI to evaluate multiple attack/block configurations.

### Goal Progression

Deck strategies are modeled as a sequence of goals with transition conditions. This captures the natural flow of a game (e.g., Storm: SETUP → COMBO, Dimir: DISRUPT → GRIND_VALUE).

### Archetype-Aware Behavior

Many AI decisions branch on `archetype` ("aggro", "midrange", "control", "combo"). For example, the SURVIVE concern filters combo pieces for combo decks but not for midrange decks.
