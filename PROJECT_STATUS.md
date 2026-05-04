# MTGSimManu — Project Status & Planning Reference

> **Last updated:** 2026-04-26 (Storm + Goryo's deferral-gate iteration: Storm 29.2→39.8% +10.6pp, Goryo's 8.1→13.4% +5.3pp, three sister-fix PRs open #194 #195 #196)
> **Purpose:** Single-source-of-truth for Claude Code planning mode. Read this before any session.
> **Sister project:** MTGSimClaude (Legacy format, 38 decks). See `CROSS_PROJECT_SYNC.md`.

---

## Recent session summaries

The dated "Recent work" entries that previously occupied the top of this file have moved to `docs/history/sessions/` with frontmatter (`status: archived`, `priority: historical`). Most recent first:

- **2026-04-26** — Storm + Goryo's deferral-gate iteration (Storm 29.2→39.8% +10.6pp, Goryo's 8.1→13.4% +5.3pp). See `docs/history/sessions/2026-04-26_storm_goryos_deferral_gate.md`.
- **2026-04-25** — Phase 2c combo refactor complete. See `docs/history/sessions/2026-04-25_phase2c_combo_refactor.md`.
- **2026-04-20** — EV Correctness Overhaul complete. See `docs/history/sessions/2026-04-20_ev_correctness_overhaul.md`.

For older sessions and falsified hypotheses, use the frontmatter discovery commands in CLAUDE.md → "Session Priorities (discovery protocol)".

## Current work — frontmatter registry

Session priorities, active work, and falsified hypotheses are all declared in YAML frontmatter on every doc under `docs/`. See `CLAUDE.md` → "Session Priorities (discovery protocol)" for the grep commands. The frontmatter IS the registry — no curated list in this file to drift.

**To find current active work:**
```
grep -rEl '^status: active' docs/ --include='*.md' | xargs grep -l '^priority: primary'
```

**To avoid re-running dead hypotheses:**
```
grep -rEl '^status: falsified' docs/ --include='*.md'
```

Historical session content (architecture, API signatures, past bug queues, WR history) follows below. For what's active *right now*, use the grep commands above.

---

## 1. What this project is

A **Modern-format Magic: The Gathering game simulator** with EV-based AI decision-making. Pure Python 3.11, zero external dependencies. Simulates full Bo3 matches between 16 competitive Modern decks, produces interactive dashboards, deck guides, and replay viewers.

**Repository:** `github.com/DJPieter81/MTGSimManu` (branch: `main`)

**Origin:** Initial engine shell and card database integration by [ManusAI](https://manus.im). Strategy layer, EV scoring, output products, Claude skills, and ongoing development by DJPieter81 + Claude.

---

## 2. Architecture (6 layers)

```
┌─────────────────────────────────────────────────────────────┐
│  SKILLS LAYER (Claude automation)                           │
│  /mtg-meta-matrix  /mtg-dashboard-refresh                   │
│  /mtg-deck-guide   /mtg-bo3-replayer-v2                     │
├─────────────────────────────────────────────────────────────┤
│  OUTPUT PIPELINE                                            │
│  build_dashboard.py → metagame_data.jsx → HTML heatmap    │
│  build_replay.py → Bo3 HTML replayer (light theme)          │
│  commentary_engine.py → strategic annotations               │
├─────────────────────────────────────────────────────────────┤
│  SIMULATION RUNNER                                          │
│  run_meta.py (CLI + Python API)                             │
│  --matrix --matchup --bo3 --field --audit --verbose --trace │
│  import_deck.py  match_trace.py  build_replay.py            │
├─────────────────────────────────────────────────────────────┤
│  AI LAYER — EV-based decision engine (14 modules, 7757 ln)  │
│  ev_player.py (1224 ln) — score plays, pick best            │
│  gameplan.py (545 ln) — GoalEngine, goal sequences          │
│  turn_planner.py (1113 ln) — combat sim, 5 turn orderings   │
│  ev_evaluator.py (712 ln) — EVSnapshot, board projection    │
│  combo_calc.py (652 ln) — storm/graveyard/mana zones        │
│  clock.py (328 ln) — turns-to-kill position evaluation      │
│  bhi.py (275 ln) — Bayesian hand inference                  │
│  response.py (267 ln) — counterspell decisions              │
│  mulligan.py (210 ln) — keep/mull per archetype             │
│  board_eval.py (468 ln) — assess + evoke/dash/combo eval    │
│  mana_planner.py (373 ln) — fetch/land selection            │
│  combo_chain.py (359 ln) — storm chain simulation           │
│  strategic_logger.py (279 ln) — reasoning traces            │
│  strategy_profile.py — per-archetype weights                │
├─────────────────────────────────────────────────────────────┤
│  ENGINE LAYER — rules & state machine                       │
│  game_state.py (3160 ln)  game_runner.py  card_effects.py   │
│  card_database.py  combat_manager.py  event_system.py       │
│  continuous_effects.py  sideboard_manager.py                │
│  zone_manager.py  stack.py  sba_manager.py  oracle_parser.py│
├─────────────────────────────────────────────────────────────┤
│  DATA LAYER                                                 │
│  ModernAtomic.json (21,795 cards, 8 parts merged)           │
│  decks/modern_meta.py (16 decks + METAGAME_SHARES)          │
│  decks/gameplans/*.json (15 goal sequences)                 │
│  ai/strategy_profile.py (archetype AI weights)              │
│  decks/card_knowledge.json (card role tags)                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. AI decision architecture

### Decision flow (per main phase)
```
EVSnapshot ← snapshot_from_game()          # ev_evaluator.py
    ↓
GoalEngine.current_goal                    # gameplan.py
    ↓
Enumerate legal plays → Play objects       # ev_player.py
    ↓
Score each: heuristic EV + clock Δ         # ev_player.py + clock.py
    + combo_modifier (if combo goal)       # combo_calc.py
    ↓
Discount by P(countered), P(removed)       # bhi.py
    ↓
TurnPlanner: evaluate 5 orderings          # turn_planner.py
    (deploy→attack, remove→attack,
     attack→deploy, hold mana, lethal)
    ↓
Pick highest EV → execute → log            # strategic_logger.py
```

### Key fix (session 2): token removal bug
The `removed` EVSnapshot was subtracting ETB token power alongside the removed creature — tokens persist on battlefield after their parent is removed. Fixing this moved Orcish Bowmasters from -32.7 → +14.7 EV.

---

## 4. Python API signatures

```python
# ── Simulation ──
from run_meta import (
    run_meta_matrix,     # (top_tier=14, n_games=50, seed_start=40000) → {matrix, rankings, names}
    run_matchup,         # (deck1, deck2, n_games=50, seed_start=50000) → {wins, pct1, pct2, avg_turn, turn_dist}
    run_field,           # (deck, n_games=30) → {deck, matchups: {opp: pct}, average}
    run_verbose_game,    # (d1, d2, seed=42000) → str
    run_trace_game,      # (d1, d2, seed=42000) → str (+ AI reasoning)
    run_bo3,             # (d1, d2, seed=55555) → str
    inspect_deck,        # (deck_name) → str
    audit_deck,          # (deck_name, n_games=60) → str
    resolve_deck_name,   # (alias) → str (canonical name)
    save_results, load_results,
)

# ── AI internals ──
from ai.ev_evaluator import EVSnapshot, snapshot_from_game, evaluate_board
#   EVSnapshot fields: my_life, opp_life, my_power, opp_power, my_creature_count,
#     opp_creature_count, my_hand_size, my_mana, turn_number, storm_count, my_energy
#   Properties: .my_clock, .opp_clock, .has_lethal, .am_dead_next
from ai.clock import combat_clock, life_as_resource
from ai.bhi import BayesianHandTracker, HandBeliefs
from ai.gameplan import GoalEngine, create_goal_engine, get_gameplan
from ai.strategy_profile import get_profile, StrategyProfile, DECK_ARCHETYPES
from engine.card_database import CardDatabase  # singleton pattern
```

---

## 5. Runtime performance

| Metric | Value | Notes |
|--------|-------|-------|
| DB load | 6.5s | 21,795 cards from 8 JSON parts |
| Per Bo3 match | ~0.68s | Avg across aggro/combo/control |
| 50-pair batch | ~170s | Tool timeout limit per batch |
| Full 14×14 × 50 | ~95 min | 4,550 Bo3 matches |
| σ at n=50 | Not measured | **TODO:** run 5× same matchup to quantify |

---

## 6. AI strategy accuracy

**Current grade:** C- (improving). Active P0 outliers are tracked in `userMemories` and the diagnostic docs under `docs/diagnostics/`. The most recent grade movement (B → C-) reflects fresh-eye scoring of the four open WR-band outliers (Affinity 87%, Azorius 15%, Living End 27%, Ruby Storm 25%) discovered after the 2026-04-13 to 2026-04-17 iteration run.

**Detailed iteration log (April 13-17, 2026):** unified refactor Phases A-I, Affinity matchup iter2 + re-verify, Iteration 5/6 fixes, Session 4/5 fixes, WR shifts from session 3 full re-run — see `docs/history/sessions/2026-04-13_to_17_iteration_changelogs.md`.

## 7. Known bugs

> Resolved bugs from sessions 2 and 3 (2026-04-12 and 2026-04-13) moved to `docs/history/bugfixes/2026-04-12_session_2_3_resolved_bugs.md`. This file now lists only currently-open issues and the failed-attempt / deep-audit context that's still relevant.

### P0 — OPEN

| # | Issue | Location | Evidence | Impact |
|---|-------|----------|----------|--------|
| 12 | ~~**Affinity 93% WR**~~ — **RE-FRAMED 2026-05-04 by Phase K audit (PR #284-#288).** Was misclassified as AI-scoring bug; actual root cause is Class H (inverted) — 9 of 15 opposing decks had 0 mainboard artifact hate for Bo1. Fixed via decklist edits in PR #288 (+1 MB hate to Boros / ETron / Zoo / LE). Boros vs Affinity smoke 10% → 30% (+20pp). Awaiting Phase D matrix re-run to confirm overall WR drop into 65-70% expected band. | `decks/modern_meta.py` (decklist data, NOT AI) | n/a — RESOLVED via data, not code | Closes once matrix verifies. |
| 13 | ~~**Living End 5% WR**~~ — **RE-FRAMED 2026-05-04 by Phase K audit.** 53.3% in latest matrix; sample variance. Class A bug (Waker of Waves wrong oracle) fixed in PR #287; LE vs Boros 30% → 40% (+10pp). | `ModernAtomic_part8.json` (data) | n/a — RESOLVED via data, not code | Closes once matrix verifies. |


### Remaining open bugs

#### P1
| # | Bug | Location |
|---|-----|----------|
| 1 | ~~Amulet Titan WR ~23%~~ — **WITHDRAWN 2026-05-04 by Phase K audit.** Matrix-recent flat WR is 45.1%, in expected band. Outliers are Class C/E (multi-turn lookahead, multi-Amulet stacking — see P2 #4 promotion below). | n/a |
| 2 | Living End ~12% vs Boros — AI doesn't attack aggressively after Living End resolves; Force of Negation not held for protection | `ai/ev_player.py`, `ai/response.py` |
| 3 | Psychic Frog early EV still negative when Orcish Bowmasters is better option (correct priority, but EV magnitude off) | `ai/ev_evaluator.py` |
| 4 | Chalice of the Void (and other stax permanents) undervalued by `_score_spell` — treated as generic 2-mana artifact. First attempt with `ai/stax_ev.py` built but not wired; see "Failed attempt" below. Next try needs threat-gating. | `ai/ev_player.py`, `ai/stax_ev.py` |
| 5 | Removal target selection inverted vs Affinity-style boards — `_threat_score` rates mana rocks (Springleaf Drum) above 1/1 attackers (Memnite). Affects all removal (March/PE/Solitude/Verdict). Repro state drift — hand-built state scores correctly (Δ=4.3× favoring Memnite), live sim inverts. See session 2 writeup bug A. | `engine/card_effects.py:683`, `ai/permanent_threat.py` |
| 6 | `March of Otherworldly Light` `x_val` computed as `len(lands)` instead of X actually paid — makes March at X=1 resolve as X=total-lands. See session 2 bug B. | `engine/card_effects.py:675` |
| 7 | Wrath of the Skies cast on T3 with 0 mana for X, kills own Chalice and leaves CMC-2 threats alive. AI doesn't weigh "cast now" vs "hold mana for Counterspell next turn." See session 2 bug C. | `ai/ev_player.py` (sweeper-timing EV) |
| 8 | T2 Chalice-over-pass misplay — AI jams Chalice @ X=1 on T2 with Counterspell in hand and no opp threat on stack. Cast-vs-pass EV threshold is wrong; gameplan priority tweak didn't fix it. See session 2 bug D. | `ai/ev_player.py` (pass EV) |
| 9 | Mulligan heuristic doesn't deduplicate legendaries — keeps 3×Wan Shi Tong + 2 land as a valid 7. See session 2 bug E. | `decks/gameplan_loader.py` / mulligan scorer |
| **NEW-A** | **Goal-fallback** (from Phase K Goryo's audit) — when GoalEngine's selected goal has no plays, AI passes instead of trying next-priority goal. Goryo's selects FILL_RESOURCE on T1, has no plays, falls through. Affects Goryo's, Storm, LE. **Will be addressed by Phase J-4** (Goal/GoalEngine state machine refactor). | `ai/gameplan.py` GoalEngine |
| **NEW-B** | **Wish-as-finisher EV** (from Phase K Storm audit) — Wish should score as finisher-access path when SB ∪ library has payoff and storm count ≥ opp_life. Currently underweighted. | `ai/ev_evaluator.py` / `ai/combo_calc.py` |
| **NEW-C** | **Suspend-as-payoff** (from Phase K Living End audit) — Living End AI should suspend on T2 when no cascade enabler in hand. Currently treats suspend as deferred play with no payoff signal. | `ai/ev_player.py` |

#### P2
| # | Bug | Location |
|---|-----|----------|
| 4 | **PROMOTED P2 → P1 (2026-05-04 Phase K Amulet audit)** — Amulet of Vigor multiple copies don't stack (only 1 untap applied per land ETB). Multi-Amulet bounce-land cascade is the deck's late-game ceiling; this is rules-incorrect. | `engine/game_state.py:_apply_untap_on_enter_triggers()` or `engine/triggered_abilities.py` |
| 5 | Spelunking "Lands you control enter untapped" not applied to normal `play_land` path consistently | `engine/game_state.py` |
| 6 | Elesh Norn trigger doubling not implemented | `engine/game_state.py` |
| 7 | Phelia blink-on-attack not fully implemented | `engine/card_effects.py` |

---

### Failed attempt — Chalice/Stax EV overlay (session, 2026-04-20)

**What was tried:** Oracle-driven stax EV module (`ai/stax_ev.py`) covering Chalice of the
Void, Blood Moon, Ethersworn Canonist/Rule of Law, Torpor Orb. Family detection by oracle
pattern (no hardcoded names). Chalice valuator picks best X by `opp_cmcs[X] - my_cmcs[X]`,
mirroring `engine/game_state.py:1557`. Turn decay zeroes the bonus by T5. Capped at 6 net
locked spells. 13 unit tests, all passing. Module wired into `_score_spell` via one-line
call alongside the existing duplicate-Chalice penalty.

**Why it failed:** At n=30 field sweep, WST v1 regressed from ~36% → 32% field WR after
the overlay was added in isolation. Direct cause identified in Bo3 replay
`v2_vs_boros_60100.txt` and `v1_vs_boros_60100.txt`:
  - **Same seed, same opening hand, same opp T2 Ajani cast.**
  - v1 (no overlay): holds mana T2, Counterspells Ajani on cast → opp enters T3 with
    empty board.
  - v2 (with overlay): taps out for Chalice @ X=1 on T2 → Ajani resolves, creates Cat
    token → opp enters T3 with Ajani + 2/1 Cat.

G2 T2 is even clearer: v2 has Prismatic Ending in hand, Ragavan already in play stealing
cards, and casts Chalice instead of PE on the Ragavan. The concrete answer on the actual
threat is always beaten by the projected lock value on future draws.

**Root cause of the miscalibration:** Stax EV is computed from opponent's library
composition in a vacuum, without reference to the current-turn threat picture. On T2 vs
Boros, when Counterspell/PE has a concrete target, the overlay makes Chalice EV compete
with and often beat Counterspell's heuristic score — so the AI swaps the concrete answer
for a probabilistic lock. In Storm (where there's no board threat and Chalice really does
lock the whole game), the same overlay is correct and v2 gains +7pp.

**Next attempt must be threat-gated:** stax EV should only fire when
(a) no active opp threat requires this turn's mana, or (b) the AI would otherwise idle
the turn. Effectively: "stax is downtime insurance, not on-curve tempo."

**What was shipped (this session):**
  - `ai/stax_ev.py` — module present but **not** imported by any caller. Kept as
    reference for the oracle patterns + 13 unit tests that verify sign/magnitude. Anyone
    picking up the Chalice problem edits this file rather than starting fresh.
  - `tests/test_stax_ev.py` — passes standalone.
  - `Azorius Control (WST v2)` — new deck entry with `METAGAME_SHARES = 0.0`. +4 Solitude
    MD, −3 Sanctifier (→SB), −1 Supreme Verdict vs v1 WST. At n=30 post-overlay: 34.6%
    field WR. Pre-overlay comparison unavailable because the WST v2 deck was introduced
    in the same session as the overlay; the 34.6% number is NOT directly comparable to
    v1's ~36% baseline.
  - This writeup.

**What was NOT shipped:**
  - Wiring in `_score_spell`. Reverted.
  - Any change to v1 WST.

---

### Deep audit — WST v2 play-by-play bugs (session, 2026-04-20 #2)

Read 6 Bo3 replays: seeds 60100/60200/60400 vs Boros Energy and vs Affinity. Five
distinct misplay patterns found, documented below so they don't get lost.

**Bug A — March of Otherworldly Light picks wrong target.** Seed 60200 G2 T2: P1
March @ X=1 exiles Springleaf Drum instead of Memnite (the 1/1 attacker). Live-sim
instrumentation of `_threat_score` confirms scoring inversion:
  - Drum: 1.333  (picked)
  - Mox Opal: 1.333 (tied)
  - Memnite: **1.150** (not picked)

Isolated reproduction of the same battlefield state — without the turn-by-turn game
history — scores Memnite at 1.15 and Drum at 1.00, the *correct* order. So there
is state drift between reproduction and live sim: something cumulative inflates
non-creature artifact threat. The `position_value` delta for actually removing
Memnite is 5.12 vs Drum's 1.18 — the marginal-contribution formula is correct in
isolation. Upstream state in the live sim is distorting the snapshot.

**Next step:** dump the full EVSnapshot at live-sim decision point, compare field-
by-field to the isolated reproduction, identify the inflation source. Caller:
`engine/card_effects.py:683`. Likely affects all removal spells (PE, Solitude,
Supreme Verdict), not just March.

**Bug B — March `x_val` computed from lands, not mana paid.** `engine/card_effects.py:675`:
```python
x_val = len(game.players[controller].lands)
```
This treats X as `total lands controlled` regardless of X actually paid. A T5 March
cast with 5 lands at X=1 (pay 1W + W) resolves as if X=5, widening the candidate
pool to CMC ≤ 5. Orthogonal to Bug A but also wrong; may benefit Boros/Affinity
sims by making March look stronger than it is.

**Bug C — Wrath fires on T3 on a low-value board, kills own Chalice.** Seed 60200
G1 T3: P1 has Chalice + 2 Fountains. Opp board: Voice of Victory (CMC 2, 1/3),
2 Warrior tokens. P1 casts Wrath of the Skies (WW) with 0 mana for X → picks X=0
which destroys 2 tokens + own Chalice but leaves Voice of Victory alive. Net: −1
card (Chalice), traded for 2 tokens, and Voice continues attacking.

The X-choice logic at `engine/game_state.py:1601` is correct given available mana
(X=0 is the best X possible with 0 mana left); the misplay is casting Wrath at
all. Pattern: AI treats "opp has creatures + I have Wrath" as sufficient trigger
to sweep, without weighing tempo cost vs holding mana for Counterspell next turn
(which would have caught Ranger-Captain of Eos on T4).

**Bug D — T2 Chalice-over-Counterspell still fires in 4/5 seeds.** Seed 60100 G1
T2 and analogous seeds (60200, 60400): AI jams Chalice @ X=1 on T2 with Counterspell
in hand and no opp threat on stack. Demoting Chalice priority (this session's
gameplan tweak from 24 → 14 with `always_early` cleared) fixed *some* hands (seed
60100 G2 now correctly PE's Ragavan) but not the fundamental cast-vs-pass decision.
The AI's "pass and hold mana" EV is lower than Chalice's projection. Gameplan
priorities only affect relative ranking between cast choices, not the cast-vs-hold
threshold.

**Bug E — Mulligan keeps redundant-legendary hands.** Seed 60400 G1: P1 keeps
7-card hand with 3× Wan Shi Tong + 2 lands. Legend rule means 2 of 3 WSTs are
dead. Effectively a 5-card keep. The mulligan heuristic at
`decks/gameplan_loader.py` doesn't deduplicate legendaries.

**Session 2 outcome:** Gameplan patch landed in a separate commit (Chalice
priority 24 → 14, `always_early` cleared). v2 field WR improved from ~42% →
~45% at n=30+ pooled across two seed ranges. Bugs A–E remain open.

---

## 8. Deck status

| Deck | Flat WR | Wtd WR | Sim grade | Notes |
|------|---------|--------|-----------|-------|
| Affinity | 93% | 91% | ⚠️ Inflated | P0: dominates all matchups. Blocking fix insufficient. |
| Eldrazi Tron | 72% | 57% | ✅ Working | Stable; Tron assembly bonus working |
| Boros Energy | 67% | 61% | ✅ Working | Down from 88%, now realistic T1 |
| Pinnacle Affinity | 66% | 61% | ✅ Working | Reasonable T2 performance |
| Domain Zoo | 65% | 59% | ✅ Working | Slightly above expected ceiling |
| Dimir Midrange | 65% | 55% | ✅ Working | Midrange performing well |
| Jeskai Blink | 58% | 47% | ✅ Working | Up from 53%; solid midrange |
| 4c Omnath | 58% | 44% | ✅ Working | Massive improvement from 17%; Risen Reef/landfall chain working |
| Izzet Prowess | 55% | 48% | ✅ Working | Down from 75%; realistic T2 |
| Amulet Titan | 49% | 39% | ⚠️ Underperforms | Expected ~45% weighted; mana loop value still not modelled |
| Azorius Control (WST) | 37% | 31% | ⚠️ Underperforms | Up from 19%; still weak vs aggro |
| 4/5c Control | 34% | 23% | ⚠️ Underperforms | Up from 22%; still below expected |
| Ruby Storm | 30% | 22% | ⚠️ Regressed | Down from 51%; needs investigation |
| Goryo's Vengeance | 30% | 22% | ✅ Working | Up from 2%; combo fires now |
| Azorius Control | 18% | 12% | ⚠️ Deflated | Isochron Scepter not implemented |
| Living End | 5% | 3% | ❌ Broken | P0: down from 45%, cascade fires but post-combo AI non-functional |

---

## 9. Never do / always do

### Never do
- Read meta shares from JSON — always from METAGAME_SHARES in `decks/modern_meta.py`
- Edit metagame_data.jsx manually — always `python build_dashboard.py --merge`
- Force-push to GitHub
- Mix data sources — every figure traces to one function + one data file
- Use heuristic SB tips — only game log data
- Replace deck variant without being told — run alongside existing
- Skip `git pull origin main`
- Hardcode card names in engine — always detect from oracle text or template field. Enforced by `tools/check_abstraction.py` ratchet (see CLAUDE.md ABSTRACTION CONTRACT). Pre-commit hook blocks any commit that increases the hardcoded-name count.

### Always do
- `git pull origin main` before any work
- Merge ModernAtomic_part*.json before first sim run
- Confirm metrics at each stage before proceeding
- Use `_apply_untap_on_enter_triggers()` when putting lands onto battlefield
- Call `resolve_etb_from_oracle()` for lands placed by non-standard paths

### Post-action verification
```bash
# After dashboard rebuild
python3 -c "
import re
with open('metagame_data.jsx') as f: c=f.read()
n = re.search(r'const N = (\d+)', c)
d = re.findall(r'\"decks\":\[(.+?)\]', c)
print(f'N={n.group(1) if n else \"MISSING\"}, decks={len(d[0].split(\",\")) if d else \"MISSING\"}')"

# After deck import
python3 -c "
from decks.modern_meta import MODERN_DECKS, METAGAME_SHARES
print(f'Decks: {len(MODERN_DECKS)}, Shares: {len(METAGAME_SHARES)}')
assert len(MODERN_DECKS) == len(METAGAME_SHARES), 'MISMATCH'"

# Smoke test new/edited deck (both orderings — should sum to ~100%)
python run_meta.py --matchup NEW_DECK dimir -n 10
python run_meta.py --matchup dimir NEW_DECK -n 10

# Rules audit: check for double ETB, wrong triggers, incorrect P/T
python run_meta.py --verbose DECK OPPONENT -s 50000 | grep -E "Resolve.*Resolve|damage.*dies|X=0.*dies"
```

---

## 10. Deck guide minimum spec

Guides must match the Legacy Burn guide (`guide_burn.html`) feature-for-feature. Reference: `/mtg-deck-guide` skill.

| # | Section | Data source | Interactive? |
|---|---------|-------------|-------------|
| 1 | Hero 4-col grid | Matrix: flat WR, weighted WR, rank, best/worst | — |
| 2 | Mainboard with role badges + Scryfall hovers | `modern_meta.py` decklist + card tags | Hover → card image popup |
| 3 | Sideboard with "vs" targets | `sideboard_manager.py` bool flags | — |
| 4 | Deck construction findings (±pp) | Matrix: compare hand archetypes | — |
| 5 | Game plan (3 phases with timeline) | `gameplans/*.json` goal sequences | — |
| 6 | Kill turn distribution chart | Matrix: `turn_dist` from matchup data | Bar chart |
| 7 | Hand archetype WR bars + baseline | 2,000 games hand analysis (run_matchup loop) | Baseline marker |
| 8 | Real sim hands (2 keep + 1 mull) | `run_verbose_game()` with specific seeds | Turn-by-turn |
| 9 | Metagame strategy | Matrix: archetype WRs + triptych (prey/competitive/danger) | — |
| 10 | Matchup spread tiered T1/T2/Field | Matrix WRs + `METAGAME_SHARES` + `DECK_ARCHETYPES` | Bars with type+meta% |
| 11 | Provenance footer | Sim params: date, N, seeds, engine version, attribution | — |

### Scryfall hover implementation
```html
<span class="card-tip" data-card="Ragavan, Nimble Pilferer">Ragavan</span>
```
```javascript
// JS: mouseover → fetch api.scryfall.com/cards/named?fuzzy=NAME&format=image&version=normal
// Display in fixed popup div (244×340px, border-radius:8px, box-shadow)
```

### Game plan derivation
Game plans come from `decks/gameplans/*.json` goal sequences, NOT from manual writing. Each goal has `enablers`, `interaction`, and `payoffs` arrays. The 3-phase timeline maps to goals 1-2-3 in the JSON. Card names in the guide must match the gameplan entries.

### Hand analysis pipeline (for full guide)
```python
# Run 2,000 games across all opponents, weighted by meta share
for _ in range(2000):
    opp = random.choices(opponents, weights=meta_shares)[0]
    result = run_matchup(deck, opp, n_games=1, seed_start=next_seed)
    # Record: hand composition (lands/creatures/spells), won/lost, kill turn
# Group by formula (e.g. "2L-1C-4S"), calculate WR per group vs baseline
```

---

## 11. Infrastructure proposals (from Legacy cross-pollination)

Six proposals tracked in **`MODERN_PROPOSAL.md`** (canonical, 316 lines): plugin deck architecture, template dashboard, parallel processing, meta audit + expected ranges, symmetry measurement, provenance footer.

Items already landed (per session-3 changelog): meta audit (#7), symmetry (#8), provenance footers (#12), `--workers` parallel flag (#11). Remaining: plugin deck architecture (#9, deferred), template dashboard (#10, deferred). Cross-project adoption status of items in both directions: see `CROSS_PROJECT_SYNC.md`.

## 12. Backlog

> Session-3 changelogs, validation tables, LLM-judge re-grading, Matrix-v3 outlier summary, and the original Legacy-cross-pollination Infrastructure / Validation tables (all 2026-04-12 vintage) moved to `docs/history/sessions/2026-04-12_session_3_unified_backlog.md`. Most items are either landed (see that file's Status columns) or have moved to active tracking elsewhere.

**Current backlog lives in three places — there is no curated list here:**
- `userMemories` — active P0/P1 outliers and the immediate session priority (currently the four WR-band outliers: Affinity, Azorius Control, Living End, Ruby Storm).
- `MODERN_PROPOSAL.md` — six infrastructure proposals (plugin deck architecture, template dashboard, parallel processing, etc.).
- `CROSS_PROJECT_SYNC.md` — pending cross-pollinations between MTGSimManu (Modern) and MTGSimClaude (Legacy).
- `docs/diagnostics/` (status: active, priority: primary) — open diagnostic threads. Use the grep commands in §"Current work — frontmatter registry" above.

**Known still-open from session 3 (re-verify before touching):**
- Wish tutor Grapeshot-vs-Warrens balance (audit P2). Attempted shift toward Warrens regressed Storm at session-3 sample sizes; original 0.6 threshold restored. Needs an EV-weighted decision, not a threshold tweak.

## 13. Codebase stats

~28,500 Python LOC · 66 files · 14 AI modules · 21,795 cards · 16 decks · 16 gameplans · 149 passing tests · 4 Claude skills · 0 external deps

---

*See also: docs/ARCHITECTURE.md · CLAUDE.md · docs/history/audits/2026-04-11_LLM_judge.md*
