---
title: "Field gap: the fair decks are not too fast, the combo decks are too slow"
status: active
priority: secondary
session: 2026-11-08
depends_on: [docs/diagnostics/2026-11-07_ruby_storm_draw_tax_root_cause.md, tools/calibration_bands.json]
tags: [ai, calibration, amulet, tron, zoo, goldfish, leads]
summary: "(Lead 1 built as unit HL: Amulet goldfish median kill turn 8 -> 7.) Meta-weighted, the sim puts Domain Zoo at 72, Eldrazi Tron 67, Rakdos Aggro 63 against real-world 51, 45.5, 49; Broodscale matches (54 vs 55). Goldfish speed shows the fair decks are SLOWER than real play (Boros median kill turn 6, Zoo 7), while Amulet Titan kills on turn 8. The inflation is the combo and control decks underperforming. Two mechanisms named from replays, not built: a land search ignores a delivered land's same-turn ability (Primeval Titan never fetches the haste land), and the non-active player gets no priority in the upkeep (a held Silence never stops the turn's big cast)."
---

# Field gap: the fair decks are not too fast, the combo decks are too slow

## The numbers (2026-11-07 n=60 matrix, weighted by the 2026-11-08 shares)

| Deck | Flat | Meta-weighted | Real-world |
|---|---|---|---|
| Boros Energy | 71.9 | 73.1 | not found |
| Domain Zoo | 70.4 | 72.2 | 51.1 |
| Eldrazi Tron | 74.2 | 66.8 | 45.5 |
| Rakdos Aggro | 59.4 | 62.8 | 48.7 |
| Broodscale Bloodchief | 59.5 | 54.4 | 54.8 |
| Amulet Titan | 45.5 | 40.0 | Tier 1 (band 45-60) |
| Azorius Control | 40.5 | 30.0 | Tier 1 by share (7%) |
| Ruby Storm | 12.8 | 14.5 | 49.2 |

Weighting barely moves the fair decks: they beat the decks people actually play, not only the 0.5%-share ones.

## Goldfish speed (`tools/goldfish.py`, n=30, on the play, s70000)

| Deck | Median kill turn | Real-world expectation |
|---|---|---|
| Boros Energy | 6 | 4-5 |
| Domain Zoo | 7 | 5 |
| Amulet Titan | 8 | 4-5 (a turn-2/3 Titan) |

The fair decks are not over-fast; if anything they are slow. Their excess win rate is therefore what the other decks fail to do: combo kills too late, and control does not stop the cast that matters.

## Lead 1 (built as unit HL): a land search ignored what the delivered land does this turn

- **Replay:** goldfish s70002. Primeval Titan resolves on turn 4 and fetches Simic Growth Chamber + Urza's Saga; the second Titan on turn 5 does the same. Hanweir Battlements (haste) stays in the library. The kill comes on turn 7, three turns after the first Titan.
- **Where it diverges:** the land search asks the land-drop scorer (`ai/activation_ev._choose_land_delivery` → `EVPlayer._score_land`). That scorer values colours, Tron sets, landfall and the gameplan's `land_priorities`. It never values a land's activated ability that is usable this turn and would grant haste to a creature that can then attack.
- **Rule to build:** a land delivered untapped (or untapped by a watcher such as Amulet of Vigor) is worth the damage its own ability adds this turn.
- **Class:** `tools/class_census.py "gains haste until end of turn"` finds 183 pool cards, so the class is mechanic-sized. Two lands from it are in registered decks: Hanweir Battlements (Amulet Titan) and Arena of Glory (Boros Energy, Jeskai Blink, 4/5c Control).

## Lead 2 (named, not built): no priority for the non-active player in the upkeep

- **Replay:** Azorius Control vs Eldrazi Tron s50000, game 2. Azorius holds Silence from turn 10. On turn 13 Tron untaps with 10 mana and casts Ulamog. Silence is cast only on turn 15, in response to another spell.
- **Rule:** CR 117.3a/b: in the upkeep the active player receives priority, and then the non-active player does. The runner gives the non-active player only an imprint-copy hook there (`engine/game_runner.py`, upkeep step), a pre-combat window and an end-step window. A hand-held "can't cast spells this turn" restriction (`turn_scoped_restriction == "no_spells"`) therefore has no window in which it covers the opponent's whole turn.
- **Why it is not built yet:** in that game Azorius had no clock, so Silence would have bought one turn. The record is 0 of 10 matches (2 of 23 games). The AI rule of when such a restriction is worth its card still needs a primitive, such as the opponent's castable threat this turn read from BHI.

## Measured context
- Azorius Control vs Eldrazi Tron at n=10 Bo3: matches 0/10; games 1 2/10, games 2 0/10, games 3 0/3.

## Lead 1 built (unit HL, 2026-11-08)
- A regression from unit TU: the retired engine handler preferred the haste land, and the AI's land valuation never took that preference over. Built as unit HL (tracker); Amulet goldfish median kill turn 8 → 7. The `hl-post` arm measures the decks that carry the class.
