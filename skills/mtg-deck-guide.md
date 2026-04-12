---
name: mtg-deck-guide
description: Generate comprehensive MTG deck guides with sim-verified data. Use this skill whenever the user wants to create a deck guide, deck primer, matchup guide, mulligan guide, or sideboard guide for any MTG deck. Triggers on requests like "make a guide for [deck]", "deck primer", "mulligan analysis", "sideboard plans", "hand analysis", or "what hands should I keep". Also triggers when the user wants to analyze opening hand win rates, extract hand archetypes from simulation data, or create tournament prep documents. Use this skill even if the format isn't Legacy — the methodology applies to any format with a sim engine.
---

# MTG Deck Guide Generator

Generates a comprehensive, single-file HTML deck guide with sim-verified data including real opening hands, hand archetype analysis, kill turn distributions, matchup spreads, and sideboard plans.

## When to Use

- User wants a deck guide, primer, or matchup analysis
- User asks for mulligan advice backed by data
- User wants to know which hands win and which lose
- User wants sideboard plans ordered by meta relevance
- User wants visualizations of matchup spreads or kill turn distributions

## Output Structure

A standalone HTML file (~40-60KB) with this flow:

1. **Hero** — 4-col grid: Format, Sim WR (flat + weighted), Rank/Tier, Best/Worst
2. **Decklist** — Two-column: Main 60 with role badges left, SB 15 + findings right
3. **Game Plan** — Phase-based timeline (3 phases with colored dots)
4. **Kill Turn Distribution** — Bar chart from sim data
5. **Hand Archetype Win Rates** — Horizontal bars with baseline marker (2,000 games)
6. **Real Sim Hands** — Keep (green) / Mull (red) boxes with turn-by-turn logs
7. **Metagame Strategy** — 7 visual components (archetype WR, tournament sim, triptych, arc, delta proof, danger cards, game plan)
8. **Matchup Spread** — All opponents grouped by meta tier with WR bars
9. **Provenance Footer** — Exact sim parameters, deck count, game count, date

## Data Requirements

The guide requires data from the meta matrix skill (`mtg-meta-matrix`), plus additional hand analysis:

| Data | Source | Purpose |
|------|--------|---------|
| `meta_N.json` | Meta matrix | Matchup WRs, kill turns, game stats |
| `deck_agg.json` | Meta matrix | MVPs, finishers, deck profile |
| `card_trimmed.json` | Meta matrix | Per-matchup card stats |
| Hand samples | New extraction | Real opening hands with outcomes |

## Hand Analysis Pipeline

Read `references/hand_analysis.md` for the full methodology. Summary:

### Step 1: Collect 2,000 games

```python
for _ in range(2000):
    opp = random.choice(opponents)
    r = run_symmetric_game(deck, opp)
    # Record: hand composition, won/lost, kill_turn, opponent
```

### Step 2: Classify hand composition

For each 7-card hand, count:
- Lands, creatures, cantrips, burn spells / counters / removal
- Presence of key cards (deck-specific)
- Has T1 threat? Has free counter? etc.

### Step 3: Calculate archetype win rates

Group hands by formula (e.g., "2L-1C-4S") and by named archetype (e.g., "T1 threat + Force + cantrip"). Calculate WR for each group vs baseline.

### Step 4: Select real example hands

- **Winning hands**: Pick 3 fastest kills with full game logs
- **Losing hands**: Pick 2-3 losses that illustrate WHY the deck loses (usually T1 combo, not bad hands)
- Verify mana math in all turn-by-turn sequences

### Critical: Mana Math Verification

ALWAYS verify that described play sequences are physically possible:
- Can't cast 2 spells with 1 mana source
- Fireblast needs 2 Mountains on battlefield (not in hand)
- Suspend costs mana (R for Rift Bolt)
- Goblin Guide reveals OPPONENT's top card, not yours
- Prowess only triggers from NONCREATURE spells

## Design System

Read `references/design_system.md` for the complete visual spec.

### Theme: Light Clean (Amulet Titan style)
```css
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #fff; color: #111; max-width: 960px; margin: 0 auto; }
Colors: green #1f7040 (good) / amber #854f0b (neutral) / red #b02020 (bad)
```

### Card Role Badges
Each card in the decklist gets a colored badge:
| Badge | CSS | Use |
|-------|-----|-----|
| `b-threat` | green bg | Creatures that attack |
| `b-burn` / `b-reach` | orange/purple bg | Direct damage spells |
| `b-engine` | warm bg | Repeatable value (Eidolon, Bowmasters) |
| `b-finisher` / `b-kill` | red bg | Closers (Fireblast, Emrakul) |
| `b-enabler` | green bg | Setup (Amulet, Dark Ritual) |
| `b-removal` | red bg | Interaction (Bolt, Push, Swords) |
| `b-draw` / `b-tutor` | blue/gold bg | Card selection |
| `b-hate` | purple bg | Sideboard hate (Pyroblast, Veil) |
| `b-flex` | grey bg | Lands, flexible slots |

### Scryfall Card Image Hovers
All card names get `class="card-tip" data-card="Card Name"` which shows the
actual MTG card image on mouseover via Scryfall API:
`api.scryfall.com/cards/named?fuzzy=NAME&format=image&version=normal`
JS popup follows cursor, caches images after first load.

### Layout
- **Hero**: 4-column grid (Format, WR, Rank, Best/Worst)
- **Decklist**: Two-column (mainboard left, sideboard + findings right)
- **Game Plan**: Vertical timeline with colored dots + phase boxes
- **Kill Turn**: CSS flex bar chart
- **Matchup Spread**: Tier-grouped horizontal bars with meta WR column

### Metagame Strategy Section (7 visual components)
1. **Archetype WR bars** — horizontal bars by opponent type
2. **Tournament histogram** — 8-round sim from 10,000 runs
3. **Prey/Competitive/Danger triptych** — 3 cards with big WR + text
4. **Tournament arc** — segmented color bar (R1-3 bank → R4-6 gauntlet → R7-8 top)
5. **Delta proof chart** — flat→weighted drop comparison across T1 decks
6. **Danger matchup cards** — red gradient header + ✗/⚡/★ icon bullets
7. **Game plan timeline** — vertical dots connecting phase boxes

## Ordering Principle

**Everything ordered by meta tier, not by win rate.** The decks you'll face most often (Tier 1) appear first in:
- Matchup spread bars
- Sideboard table rows
- Deck profile matchup lists

This is a tournament prep tool — you study the common matchups first.

## Sideboard Construction

For decks without a physical sideboard in the sim (BO1), construct a realistic 15-card sideboard based on:
1. The deck's weaknesses revealed by sim data
2. Common Legacy/Modern sideboard cards for the archetype
3. Which cards to cut in each matchup (weakest maindeck cards)

Always include the IN/OUT with specific card names and counts.

## File Structure

```
mtg-deck-guide/
├── SKILL.md (this file)
└── references/
    ├── hand_analysis.md    — Full hand extraction and archetype methodology
    ├── design_system.md    — CSS variables, fonts, component patterns
    └── mana_math.md        — Common mana math pitfalls to avoid
```

## Quick Generation (NEW)

```bash
# All T1/T2 decks at once
python build_guide.py --all /mnt/user-data/outputs/

# Single deck
python build_guide.py "Boros Energy" /mnt/user-data/outputs/guide_boros.html
```

`build_guide.py` reads `metagame_data.jsx` and generates: hero stats, Stars of Sim (Scryfall thumbnails), G1→match swing table, danger cards, tiered matchup spread, provenance footer.

For tournament-grade guides, read `templates/reference_deck_guide.html` first — it has the full 11-section spec including real sim hands, game plan phases, and 6 pro-level findings.

## 6 Required Pro-Level Findings

Each finding must be data-backed, non-obvious, and actionable:
1. Damage-to-kill efficiency paradox (top dmg source ≠ top closer)
2. Closer changes by matchup speed counterintuitively
3. G1→match WR swing showing SB asymmetry (≥12pp)
4. Structural removal blind spots from d2_top_damage
5. Hidden damage sources (tokens) → boarding rules
6. Weighted WR gap analysis

Source fields: `matchup_cards[key].d1_finishers`, `.d1_top_damage`, `.g1_wins`, `.sweeps`, `.comebacks`, `.d1_sb`
