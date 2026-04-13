---
name: mtg-bo3-replayer-v2
description: Generate interactive Bo3 match replay HTML files from MTG simulation data. Use this skill whenever the user asks to replay a match, generate a play-by-play, debug a specific matchup, save a Bo3 game log, create an HTML replayer, or investigate why a deck wins/loses a specific matchup. Also triggers on "simulate a match", "show me a game between X and Y", "replay", "play-by-play", "Bo3", "best of 3", "game log", "match viewer", or any request to visually step through a simulated MTG match. Use this skill even if the user just says "run a match" or "show me how X beats Y" in the context of MTG sim work.
---

# MTG Bo3 Match Replayer

Generates a standalone interactive HTML replay of a best-of-3 MTG match.
**Always use `build_replay.py` — never rewrite the parser from scratch.**

## Pipeline

```bash
# 1. Run Bo3 and capture log
python run_meta.py --bo3 "Deck A" "Deck B" -s SEED > replays/log.txt

# 2. Build HTML
python build_replay.py replays/log.txt replays/replay_deckA_vs_deckB_sSEED.html SEED

# 3. Commit both
git add replays/ && git commit -m "replay: Deck A vs Deck B sNNNN"
git push origin main
```

## Reference

`templates/reference_replay.html` — canonical output. Read this before regenerating CSS or JS.

## Current Features (build_replay.py)

| Feature | How it works |
|---|---|
| **Scryfall thumbnails** | Every card pill and creature badge shows card art on hover. URLs: `api.scryfall.com/cards/named?exact=URL_ENCODED_NAME&version=small`. Names encoded with `urllib.parse.quote`. DFCs, apostrophes, commas all handled. |
| **Equipment tags** | `⚔Cranial Plating` badge on creature badge when equipped. Tracked from `Equip X to Y` and `falls off` log lines into `equip_map` per turn. Survives re-equip and creature death. |
| **Lethal callout** | `☠ LETHAL — N damage → life X → -Y` red left-border banner when combat damage kills a player (life ≤ 0). |
| **Per-attacker damage** | `BREAKDOWN:` lines: each unblocked attacker's name, P/T, individual damage to player. Parsed from `P#:   Name (P/T) → N dmg to player` log lines. |
| **Block reasoning** | `🛡 BLOCK:` (normal) and `🚨 BLOCK-EMRG:` (emergency) lines with blocker/attacker P/T and reason (`chump block` / `trade (chump)` / `favorable trade`). |
| **Other permanents row** | Equipment, mana rocks, enchantments shown between creatures and lands in each board-side panel. |
| **Dot-click reasoning** | `·` on each play line expands AI goal reasoning. Unique IDs: `r{game}t{turn}p{pidx}s{step}`. |
| **Auto-merge DB** | `card_database.py` auto-runs `merge_db.py` if < 1000 cards loaded. |

## Design System

Light theme (GitHub Light palette):

| Token | Value |
|-------|-------|
| bg | `#ffffff` |
| border | `#d0d7de` |
| text | `#1f2328` |
| muted | `#656d76` |
| P1 | `#0969da` (blue) |
| P2 | `#d1242f` (red) |
| font | system-ui, Segoe UI |
| mono | Fira Code, Consolas |

## Parser Rules (critical)

- Board state keyed by **player name** (`boards['Boros Energy']`), never `active`/`opp`.
- End-of-turn board uses **next turn's header** (state after plays = next turn's header).
- `equip_map` carried per turn and passed to `creature_badges(s, equip_map)`.
- Combat detail lines stored as prefixed strings: `BREAKDOWN:`, `BLOCK:`, `BLOCK-EMRG:`, `LETHAL:`, plain.

## Debugging Checklist

1. **Missing cards** — run `python merge_db.py` (auto-runs if DB < 1000 cards)
2. **0-power creatures attacking** — check `_has_combat_value()` oracle detection
3. **Wrong battle cry values** — fires once in `combat_manager._apply_battle_cry`, NOT `oracle_resolver`
4. **Equipment not stacking** — tags are `equipped_{instance_id}`; 2× CP = 2× artifact bonus
5. **Bad blocks** — 0-power and battle cry sources filtered from non-emergency path

## Seed Reference

| Purpose | Seeds |
|---------|-------|
| Demo | 55555 |
| Matrix | 40000–49999 |
| H2H | 50000–59999 |
| Replay/debug | 60100+ |
| Deep debug | 80000+ |
