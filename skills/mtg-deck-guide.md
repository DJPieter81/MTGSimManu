---
name: mtg-deck-guide
description: Generate comprehensive MTG deck guides with sim-verified data. Use this skill whenever the user wants to create a deck guide, deck primer, matchup guide, mulligan guide, or sideboard guide for any MTG deck. Triggers on requests like "make a guide for [deck]", "deck primer", "mulligan analysis", "sideboard plans", "hand analysis", or "what hands should I keep". Also triggers when the user wants to analyze opening hand win rates, extract hand archetypes from simulation data, or create tournament prep documents.
---

# MTG Deck Guide Generator

## Quick Reference

```bash
# All T1/T2 decks
python build_guide.py --all guides/

# Single deck
python build_guide.py "Boros Energy" guides/guide_boros_energy.html
```

Reads: `metagame_data.jsx` (D object), `decks/modern_meta.py` (decklists), `decks/gameplans/*.json` (goal sequences)

## REQUIRED Sections (8 minimum)

Every guide produced by `build_guide.py` MUST contain ALL of these. If any section is missing, the guide is incomplete — fix the generator, don't ship it.

| # | Section | Data source | Verification |
|---|---------|-------------|-------------|
| 1 | **Hero stats** (4-col) | D.overall[idx] | grep `hero-item` ≥ 4 |
| 2 | **Decklist** with card stats (casts/dmg/kills) | MODERN_DECKS + D.deck_cards | grep `dl-row` ≥ 20 |
| 3 | **Stars of the Sim** (Scryfall thumbnails) | D.deck_cards finishers + mvp_damage | grep `star-card` ≥ 2 |
| 4 | **Game Plan** (3 phases from gameplan JSON) | decks/gameplans/*.json | grep `Game Plan` = 1 |
| 5 | **Kill Turn Distribution** (bar per opponent) | matchup_cards[key].avg_turns | grep `Kill Turn` = 1 |
| 6 | **Non-Obvious Findings** (up to 6) | Derived from matchup data | grep `Non-Obvious` = 1 |
| 7 | **G1→Match Swing** table | matchup_cards g1_wins vs match WR | grep `Swing` = 1 |
| 8 | **Matchup Spread** (tiered T1/T2/Field) | D.wins + D.meta_shares | grep `mu-row` ≥ 5 |

Optional (requires verbose sim data not in JSX):
- Hand Archetype WR bars (needs hand extraction from game logs)
- Real Sim Hands with turn-by-turn (needs verbose game traces)
- Danger Cards with Scryfall art crops (included when d2_top_damage exists)

## Post-Generation Verification

After `build_guide.py --all`, run this check:

```bash
for f in guides/guide_*.html; do
  sections=$(grep -c "section-title" "$f")
  cards=$(grep -c "dl-row" "$f")
  stars=$(grep -c "star-card" "$f")
  echo "$f: $sections sections, $cards cards, $stars stars"
  if [ "$sections" -lt 7 ]; then echo "  WARNING: missing sections!"; fi
  if [ "$cards" -lt 15 ]; then echo "  WARNING: decklist incomplete!"; fi
done
```

## 6 Required Pro-Level Findings

Each finding is auto-derived from matchup data. Must be non-obvious and actionable:

1. **Damage ≠ kills paradox**: top dmg source ≠ top closer → different boarding rules
2. **Speed shapes closer**: fast matchups use different finisher than grindy ones
3. **SB asymmetry**: G1→match swing ≥12pp → one side's SB plan dominates
4. **Removal blind spots**: opponent's top damage source outside your removal range
5. **Hidden damage engines**: tokens deal massive damage but aren't in the decklist
6. **Weighted gap**: deck over/underperforms at top tables vs field average

Source fields: `D.deck_cards[idx].finishers`, `.mvp_damage`, `.mvp_casts`, `matchup_cards[key].g1_wins`, `.avg_turns`, `.sweeps`, `.comebacks`

## Token Filter Rule

Never show tokens (Construct Token, Germ, Cat Token) in Stars or Danger Cards sections — Scryfall API can't resolve token names. Filter with: `'Token' not in card and 'Germ' not in card`

## Card Stats in Decklist

Every mainboard card shows inline stats when available:
- Cast count (from D.deck_cards.mvp_casts)
- Damage dealt (from D.deck_cards.mvp_damage)  
- Kill count (from D.deck_cards.finishers)

Format: `4x Ragavan, Nimble Pilferer  369 casts · 557 dmg · 62 kills`

## Scryfall Card Hovers

All card names use `class="card-tip" data-card="CardName"`. JS mouseover fetches card image from `api.scryfall.com/cards/named?fuzzy=`. Cache per session. Works for all real cards, fails silently for tokens.

## What This Skill Was Missing (Lesson)

Previous version listed `build_guide.py` as producing only "hero, Stars, G1 swing, danger cards, spread" and deferred the rest to the hand-crafted template. This caused a regression where 5 sections were silently dropped. The fix: every derivable section MUST be in `build_guide.py`, verified by section count after generation. The hand-crafted template (`templates/reference_deck_guide.html`) adds polish but is NOT the source for missing sections.

## Role Badges (auto-derived from card tags)

`build_guide.py` reads `engine.card_database.CardDatabase` and maps card tags to display badges:

| Tag | Badge | Color |
|-----|-------|-------|
| efficient_threat, threat | threat | amber |
| energy | energy | teal |
| removal, board_wipe | removal / sweep | red |
| stax, interaction | hate | amber |
| token_maker | enabler | purple |
| etb_value | value | blue |
| graveyard | GY | purple |
| counter | protect | blue |

Fallback: creatures CMC≤2 → "threat", lands → "land".

## SB "vs" Targets (auto-derived from matchup data)

Sideboard cards show which matchups they come in against, parsed from `matchup_cards[key].d1_sb` / `d2_sb`:
- Format: `vs Zoo, Prowess (2×)`
- Source: `IN: 2x Wrath of the Skies` lines in SB data

## Deck Construction Findings Section

Three auto-derived data points after the decklist:
1. Meta-weighted vs flat WR gap (±pp)
2. #1 damage source (card, casts, total dmg)
3. #1 finisher (card, kill count, description)

## Updated Section Count: 9 minimum

| # | Section |
|---|---------|
| 1 | Hero stats (4-col) |
| 2 | Decklist with role badges + card stats + SB targets |
| 3 | Deck Construction Findings |
| 4 | Stars of the Sim |
| 5 | Game Plan (3 phases) |
| 6 | Kill Turn Distribution |
| 7 | Non-Obvious Findings (up to 6) |
| 8 | G1→Match Swing |
| 9 | Matchup Spread |

## Card Annotations (auto-derived from oracle text)

`get_card_annotation()` parses oracle text and tags to generate short italic annotations per card:

| Oracle pattern | Annotation |
|---------------|------------|
| `//` in name | flips → PW |
| sacrifice + damage | sac for damage |
| energy + board_wipe tag | energy sweeper |
| energy + removal tag | energy removal |
| energy + creature + etb_value | energy engine |
| nonbasic lands | shuts greedy mana |
| choose one / is_modal | modal · flex |
| escape in oracle | escape recursion |
| card_advantage + token_maker | draw + tokens |
| token_maker + creature | token synergy |

## Construction Findings (7 data points)

1. Meta-weighted vs flat WR gap (±pp)
2. #1 damage source (card, casts, total dmg)
3. #1 finisher (card, kill count, description)
4. Hidden token damage (total from all tokens)
5. Most cast card (total + per-game average)
6. Best matchup (deck, archetype, WR%)
7. Worst matchup (deck, archetype, WR%)

## Game Plan Detail

Each phase shows:
- Phase label (Setup / Develop / Close)
- Description from gameplans/*.json
- Card roles: Enablers, Payoffs, Interaction per phase
- Mulligan keys + always-deploy-early cards
