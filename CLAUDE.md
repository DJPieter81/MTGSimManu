# CLAUDE.md — MTG Game Simulator

## Project Overview

This is a Magic: The Gathering game simulator with AI decision-making. It simulates full Modern-format games between two decks, with an AI system that makes strategic decisions (mulligans, spell casting, combat, targeting, counterspells). It also generates replay data and commentary for a web-based replay viewer.

**Python 3.11** — no external dependencies beyond the standard library.

## Required Data File

**`ModernAtomic.json`** (114MB) must be in the project root. This is the card database from MTGJSON containing oracle text, mana costs, types, power/toughness for every Modern-legal card. Without it, all cards become placeholders and the simulator will not function correctly.

If the file is missing, download it:

```bash
cd /home/ubuntu/mtg_simulator
curl -L -o ModernAtomic.json https://mtgjson.com/api/v5/AtomicCards.json
```

Alternatively, download from [MTGJSON Downloads](https://mtgjson.com/downloads/all-files/) — get the "Atomic Cards" JSON file and rename it to `ModernAtomic.json`.

The `CardDatabase` class auto-discovers this file in the project root on initialization.

## Quick Start

```bash
cd /home/ubuntu/mtg_simulator

# Run a single game (Zoo vs Dimir Midrange)
python3 -c "
from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from decks.modern_meta import MODERN_DECKS
import random
random.seed(42)
db = CardDatabase()
runner = GameRunner(db)
result = runner.run_game('Domain Zoo', 'Dimir Midrange', verbose=True)
print(f'Winner: P{result.winner+1} ({result.winner_deck}) in {result.total_turns} turns')
for line in result.game_log:
    print(line)
"

# Run a batch of games
python3 -c "
from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from decks.modern_meta import MODERN_DECKS
import random
db = CardDatabase()
runner = GameRunner(db)
wins = {}
for i in range(30):
    random.seed(50000 + i * 500)
    result = runner.run_game('Domain Zoo', 'Dimir Midrange')
    wins[result.winner_deck] = wins.get(result.winner_deck, 0) + 1
print(wins)
"

# Dump a full game with AI reasoning (for debugging)
python3 dump_game.py
```

## File Structure

```
engine/                         # Game engine layer — rules enforcement
  card_database.py              # CardTemplate, CardInstance, ManaCost, CardDatabase
  game_state.py                 # GameState, PlayerState — all game mutations
  game_runner.py                # GameRunner — turn loop, phases, GameResult
  card_effects.py               # EFFECT_REGISTRY — 60+ card-specific handlers
  combat_manager.py             # CombatManager — attack/block optimization
  continuous_effects.py         # Static abilities, P/T modifications
  sba_manager.py                # State-based actions (lethal damage, legend rule)

ai/                             # AI decision layer — strategy
  ai_player.py                  # AIPlayer — top-level decision coordinator
  gameplan.py                   # GoalEngine, DeckGameplan, Goal — deck-specific strategy
  spell_decision.py             # Concern-based spell selection (SURVIVE/ANSWER/ADVANCE/EFFICIENT)
  board_eval.py                 # BoardAssessor — evaluates board state, actions
  mana_planner.py               # Fetch target selection, mana needs analysis, land sequencing
  turn_planner.py               # Multi-turn sequencing
  combat_ai.py                  # Attack/block decisions
  combo_system.py               # Combo detection and execution

decks/
  modern_meta.py                # MODERN_DECKS dict — 12 deck definitions with mainboard/sideboard

replay_generator.py             # Generates replay snapshots for the web viewer
replay_generator_v2.py          # Updated replay generator
commentary_engine.py            # Generates natural-language commentary for replays
```

## Available Decks (12)

Amulet Titan, Ruby Storm, Living End, Goryo's Vengeance, Boros Energy, Domain Zoo, Affinity, Eldrazi Tron, Jeskai Blink, Izzet Prowess, Dimir Midrange, 4c Omnath

## Architecture — Three Layers

### Layer 1: Engine (rules enforcement)

The engine layer enforces Magic rules. It does NOT make decisions — it only validates and executes.

**GameState** (`engine/game_state.py`) is the central mutable object. Key methods:
- `play_land(player_idx, card)` — puts a land onto the battlefield
- `cast_spell(player_idx, card)` — resolves a spell (calls EFFECT_REGISTRY handlers)
- `can_cast(player_idx, card)` — checks mana availability and color requirements
- `check_state_based_actions()` — lethal damage, legend rule, zero toughness
- `_crack_fetchland(player_idx, card)` — fetch land resolution with mana planning

**GameRunner** (`engine/game_runner.py`) drives the turn loop:
1. Untap → Draw → Main Phase (land + spells) → Combat → Main Phase 2 → End Step
2. Main phase calls `ai_player.decide_main_phase()` in a loop until AI passes
3. End step has an instant-speed window for removal and flash creatures
4. Combat calls `combat_ai` for attack/block decisions

**EFFECT_REGISTRY** (`engine/card_effects.py`) uses a decorator pattern:
```python
@EFFECT_REGISTRY.register("Orcish Bowmasters", trigger="etb")
def bowmasters_etb(game, player_idx, card, **kwargs):
    # Deal 1 damage, create Orc Army token
```
Every card with a special ability has a registered handler. The registry is checked during `cast_spell` resolution.

### Layer 2: AI (strategy)

**AIPlayer** (`ai/ai_player.py`) coordinates all decisions:
- `decide_mulligan()` — keep/mulligan based on hand quality
- `decide_main_phase()` — calls GoalEngine for spell selection
- `decide_response()` — counterspell decisions when opponent casts
- `decide_land_play()` — which land to play from hand

**GoalEngine** (`ai/gameplan.py`) is the strategic brain. Each deck has a `DeckGameplan` with ordered `Goal` objects. Goals have types:
- `AGGRO` — deploy threats, attack, burn face
- `DISRUPT` — deploy threats + hold interaction
- `INTERACT` — value-based midrange play
- `GRIND_VALUE` — card advantage and attrition
- `COMBO` — assemble and execute combos
- `STORM` — chain spells for storm count

GoalEngine tracks which goal is active and transitions between them based on board state.

**Spell Decision System** (`ai/spell_decision.py`) uses a **concern pipeline**:
1. **SURVIVE** — if dying (opponent's clock ≤ 4 turns), find emergency plays
2. **ANSWER** — if opponent has must-answer threats, find removal
3. **ADVANCE** — progress the active goal (deploy threats, draw cards, etc.)
4. **EFFICIENT** — mana efficiency tiebreaker

Each concern returns a spell or None. First non-None result wins.

**Key function:** `choose_spell(engine, castable, game, player_idx, assessment)` — this is the main entry point. It builds a `_DecisionContext`, runs the concern pipeline, and returns the chosen spell.

### Layer 3: Presentation (replay)

**ReplayGenerator** creates snapshots of each game turn for the web viewer. Each snapshot includes board state, life totals, hand sizes, and the action taken.

**CommentaryEngine** generates natural-language commentary for key moments (removal, counterspells, combat tricks, etc.).

## Key Data Types

```python
# Card identity (immutable template)
CardTemplate:
    name: str
    cmc: int                    # Converted mana cost
    mana_cost: ManaCost         # Colored mana requirements
    types: set[str]             # {"creature", "instant", "sorcery", "land", ...}
    subtypes: set[str]          # {"human", "wizard", "fetch", "shock", ...}
    tags: set[str]              # {"removal", "counterspell", "flash", "cantrip", ...}
    power: int
    toughness: int
    oracle_text: str
    colors: set[str]            # {"W", "U", "B", "R", "G"}

# Card instance (mutable, in-game)
CardInstance:
    template: CardTemplate
    tapped: bool
    counters: dict
    damage_marked: int
    zone: str                   # "hand", "battlefield", "graveyard", "exile", "library"

# Player state
PlayerState:
    life: int
    hand: list[CardInstance]
    library: list[CardInstance]
    creatures: list[CardInstance]
    lands: list[CardInstance]
    graveyard: list[CardInstance]
    exile: list[CardInstance]
    mana_pool: ManaPool

# Board assessment (computed each decision point)
BoardAssessment:
    my_mana: int                # Untapped lands count
    opp_mana: int
    my_creatures: list
    opp_creatures: list
    am_dying: bool              # Opponent kills us in ≤ 4 turns
    pressure: float             # 0.0 to 1.0, how much pressure opponent has
    opp_clock: int              # Turns until opponent kills us
```

## How the AI Picks a Spell (Full Trace)

1. `GameRunner._execute_main_phase()` calls `ai_player.decide_main_phase()`
2. `AIPlayer.decide_main_phase()` builds castable list via `game.can_cast()` for each hand card
3. Calls `GoalEngine.choose_action()` which calls `spell_decision.choose_spell()`
4. `choose_spell()` builds `_DecisionContext` with categorized cards (threats, removal, cantrips, etc.)
5. Runs concern pipeline: SURVIVE → ANSWER → ADVANCE → EFFICIENT
6. Returns the chosen spell (or None to pass)

## Gameplan Configuration Pattern

Each deck's gameplan is built by a `_build_*` function in `gameplan.py`. Example:

```python
def _build_dimir_midrange() -> DeckGameplan:
    return DeckGameplan(
        deck_name="Dimir Midrange",
        archetype=Archetype.MIDRANGE,
        goals=[
            Goal(
                name="DISRUPT",
                goal_type=GoalType.DISRUPT,
                priority=1.0,
                card_roles={
                    "enablers": ["Orcish Bowmasters", "Dauthi Voidwalker", "Psychic Frog"],
                    "payoffs": ["Murktide Regent"],
                    "interaction": ["Fatal Push", "Counterspell", "Drown in the Loch"],
                },
                card_priorities={
                    "Murktide Regent": 20.0,
                    "Orcish Bowmasters": 16.0,
                    # ...
                },
            ),
        ],
    )
```

`card_roles` determines how the spell decision system treats each card:
- **enablers** — deployed proactively to support the gameplan
- **payoffs** — the high-impact cards the deck is building toward
- **interaction** — removal, counterspells, disruption
- **engines** — card advantage or mana engines

## Shockland Decision

Shocklands (dual lands that can enter tapped or untapped for 2 life) use `mana_planner.analyze_mana_needs()` to decide. If the hand has spells that need the mana or the colors, the land enters untapped (pay 2 life). Otherwise it enters tapped.

This logic is in `engine/game_state.py` at two points:
1. Direct shockland play from hand (~line 804)
2. Shockland fetched by a fetch land (~line 902)

## Fetch Land Target Selection

`mana_planner.choose_fetch_target()` scores each fetchable land based on:
- +20 per missing color it provides (colors needed by hand but not on battlefield)
- +3 per needed color (colors needed by hand, even if already available)
- +5 for domain count increase
- +2 for tempo (enters untapped)
- -5 for duplicate (already have this exact land)

## Known Bugs Fixed (as of latest)

| Bug | File | Fix |
|-----|------|-----|
| SURVIVE filters all midrange creatures as "combo pieces" | `spell_decision.py` | Only filter for COMBO/STORM archetypes |
| `_advance_reactive` only deploys on empty boards | `spell_decision.py` | Also deploy when dying with no board |
| `_should_hold_for_interaction` holds while dying | `spell_decision.py` | Never hold when dying with no creatures |
| Fatal Push `_can_kill` ignores CMC | `spell_decision.py` | Added CMC ceiling check from oracle text |
| Fetch lands don't trigger revolt | `game_state.py` | Track `permanents_left_this_turn` |
| Flash creatures with removal not deployed at end of turn | `game_runner.py` | Also add to `flash_creatures` fallback |
| Emergency re-include adds counterspells in main phase | `spell_decision.py` | Skip counterspells during main phase |
| Subtlety has no ETB handler | `card_effects.py` | Added bounce-to-top handler |
| Subtlety evoked into empty board | `board_eval.py` + `game_state.py` | Target validation before evoke |
| `_eval_shock` standalone heuristic disconnected from AI | `board_eval.py` | Removed; wired to `mana_planner.analyze_mana_needs` |

## Known Open Issues

1. **4c Omnath wins only 4% vs Zoo** — should be ~50-60%. Root causes:
   - Omnath (WURG, 4 mana) often drawn but not castable because AI spends mana on lower-priority spells first
   - Solitude evoked into empty boards (same class of bug as Subtlety — needs target validation)
   - Shocklands sometimes enter tapped when they should shock to enable Omnath
   - Games end too fast (average 11 turns) before Omnath's value engine comes online

2. **Tribal Flames always goes face** — never used as creature removal. For Zoo's aggro plan this is usually correct, but sometimes killing a blocker would be better.

3. **Phlage escape interaction** — Phlage from graveyard via escape is not always handled correctly by the AI's threat evaluation.

## Debugging Workflow

Use `dump_game.py` as a template for debugging. It monkey-patches:
- `GameState.__init__` to capture log lines as they happen
- `spell_decision.choose_spell` to print the AI's reasoning (hand, castable, board, decision)
- `AIPlayer.decide_response` to print counterspell decisions

To debug a specific matchup:
1. Set the seed in `dump_game.py`
2. Change the deck names in the `run_game()` call
3. Run `python3 dump_game.py` and read `/tmp/game_dump.txt`

## Testing

There is no formal test suite. Testing is done via:
- Batch game runs with specific seed ranges
- Log analysis scripts (grep for specific card names, actions, etc.)
- The `dump_game.py` diagnostic script for single-game traces

## Important Conventions

- **Never modify engine layer to make strategic decisions.** The engine enforces rules; the AI layer makes choices.
- **Card effects use the EFFECT_REGISTRY decorator pattern.** To add a new card effect, register it with `@EFFECT_REGISTRY.register("Card Name", trigger="etb|cast|activated|etc")`.
- **The concern pipeline order matters.** SURVIVE > ANSWER > ADVANCE > EFFICIENT. Do not reorder.
- **`can_cast` is the source of truth** for whether a spell is castable. It checks mana amount, color requirements, and special costs (evoke, escape, etc.).
- **`BoardAssessment` is computed fresh each decision point.** Do not cache or reuse across turns.
- **Deck gameplans are pure data.** They define priorities and roles but contain no logic. The logic lives in `spell_decision.py` and `gameplan.py`'s `GoalEngine`.
- **Seeds are used for reproducibility.** Always set `random.seed()` before `run_game()` for deterministic replays.
