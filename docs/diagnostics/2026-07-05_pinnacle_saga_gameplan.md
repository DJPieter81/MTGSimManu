---
title: Pinnacle Affinity 28.2% field after Saga fidelity fix — gameplan audit + mechanism handoff
status: active
priority: secondary
session: 2026-07-05
depends_on:
  - docs/diagnostics/2026-04-23_affinity_consolidated_findings.md
tags:
  - wr-outlier
  - pinnacle-affinity
  - urzas-saga
  - granted-abilities
  - improvise
  - warp
  - mulligan
summary: |
  Bo3 replay diagnosis of Pinnacle Affinity's 67.2% -> 27.5% field
  collapse after PR #451 (saga chapters grant abilities instead of
  producing free Construct tokens). Verdict: the AI DOES fire the
  granted {2},{T} Construct ability (engine-greedy dispatch), so the
  collapse is NOT a "never activates" gap. The 67% was inflated by
  free tokens; the deck's true payoffs are broken by an engine bug:
  the improvise and warp branches of engine/cast_manager.py test
  'Artifact' in str(card_types), which never matches the CardType
  enum's string forms — so Kappa Cannoneer prices at full {5}{U} and
  Pinnacle Emissary's warp path is dead. Both are engine mechanisms
  and are handed to Track H. Gameplan-side (this track): land
  bottoming priorities for Urza's Saga / Darksteel Citadel and a
  mulligan land ceiling of 4 (Sagas are threat-lands). Honest
  expectation: gameplan data alone cannot recover the band.
---

# Pinnacle Affinity after the Urza's Saga fidelity fix (PR #451)

## Baseline

`python run_meta.py --field "Pinnacle Affinity" -n 15` (Bo3, standard
seeds): **28.2% field average**. Expected band ~[45-60]. Worst:
Boros Energy 0%, Jeskai Blink / Eldrazi Tron / Izzet Prowess /
4-5c Control 7%. Best: Amulet Titan 93%, Goryo's 87%.

Replays examined:
- `run_meta.py --bo3 "Pinnacle Affinity" "Boros Energy" -s 50000` (0-2 loss)
- `run_meta.py --bo3 "Pinnacle Affinity" "Dimir Midrange" -s 50500` (0-2 loss)
- `--trace` on both pairings.

## Finding 1 — the granted Saga ability IS activated (not the gap)

The replay logs show the full post-#451 saga lifecycle working:
chapter I grants `{T}: Add {C}`, chapter II grants the Construct
ability, and the deck activates it whenever it can pay:

```
T4 P1: Urza's Saga Ch.2: gains "{2}, {T}: Create a 0/0 colorless Construct..."
T4 P1: Create 1x Construct token(s)
T4 P1: Urza's Saga activates "{2}, {T}: Create a 0/0 colorless Constru..." (pays {2}, {T})
```

Constructs are made on curve (T4 in both matches when Saga lands T2),
get equipped with Cranial Plating (a 16/8 Construct appears vs Boros
T6), and attack. So the deck did not lose the ability line — it lost
the *free* token stream that PR #451 removed. The 67.2% figure was
fidelity inflation, not deck strength.

### Mechanism site (for reference, owned by Track H)

The activation is an **engine-side greedy dispatch**, not an AI
decision:

- `engine/game_runner.py:1892` `_activate_tap_abilities` — scans
  `perm.granted_abilities` for `[{N},] {T}: Create ... token`, pays by
  tapping N other untapped lands, fires unconditionally.
- Called at `engine/game_runner.py:743` (after MAIN1) and `:860`
  (after MAIN2), i.e. after `_execute_main_phase`.

Consequences the gameplan cannot influence (no hook exists):
- No EV valuation: it fires even when the {2} should be held for
  Metallic Rebuke (declared `reactive_only`), and in MAIN1 where the
  token cannot attack that turn (only marginal Plating-count upside).
- No gameplan field is consulted anywhere in the dispatch.

## Finding 2 (P0, engine, Track H) — improvise and warp artifact detection never matches: both namesake payoffs are broken

Repro (minimal state: 4 untapped lands + 5 untapped nonland artifacts
+ Kappa Cannoneer in hand):

```
can_cast Kappa: False        # expected True (improvise: 6 - 5 = 1 generic + U)
legal: []                    # Kappa never enumerated by get_legal_plays
```

Root cause — `engine/cast_manager.py:356-367` (improvise branch):

```python
if "improvise" in oracle:
    untapped_artifacts = sum(
        1 for c in player.battlefield
        if hasattr(c, 'template')
        and 'Artifact' in str(getattr(c.template, 'card_types', []))
        ...
```

`str(card_types)` renders as `[<CardType.ARTIFACT: 'artifact'>]` —
it contains `'ARTIFACT'` and `'artifact'` but never the title-case
`'Artifact'`. The count is always 0, improvise is dead code, and
Kappa Cannoneer ({5}{U}, 4 copies, the deck's primary finisher) is
castable only at full retail in a 15-land deck. In ~40 logged game
turns across both Bo3s, Kappa was drawn 5+ times and **cast zero
times** (twice discarded to Thoughtseize as a dead card).

The **same dead predicate** sits in the warp branch directly above
(`engine/cast_manager.py:347-352`): `has_artifact = any('Artifact' in
str(...))` — so Pinnacle Emissary's warp cost ({U/R}) is also
unreachable; the only Emissary cast observed was a T8 hardcast into a
counterspell.

AI-side corroboration: `ai/effective_cmc.py:351` gates the improvise
discount on `has_tag(card.name, Tag.IMPROVISE)`; the tag cache
`decks/gameplans/_oracle_classifier.json` (35 entries) does not
contain Kappa Cannoneer, so even the scoring layer prices it at 6.

**Handoff to Track H (claude/win-condition-activation-ev):** fixing
either site is engine/`ai` code and out of scope for this data-only
track. Class size: every improvise card (Kappa Cannoneer, Reverse
Engineer, Sweatworks Brawler, ...) and every warp card, in any deck —
well above the 10-card patch threshold, so the fix belongs on the
mechanic (use `CardType.ARTIFACT in c.template.card_types`).

## Finding 3 (mulligan, partially gameplan-addressable)

vs Dimir g1 the deck mulled 7 -> 4 on the land floor, discarding a
textbook artifact-aggro keep (Darksteel Citadel, Mox Opal, 2x
Springleaf Drum, 2x Ornithopter, Pinnacle Emissary). The
`mulligan_min_lands: 2` floor is a **deliberate, test-pinned**
decision (`tests/test_mulligan_artifact_aggro_one_land_floor.py`,
audit 2026-04-23 §M1) from the era when Affinity over-performed; this
track does not relitigate it. What IS in gameplan scope:

- `land_priorities` was undeclared, so mulligan bottoming
  (`ai/gameplan.py:831`) treated Urza's Saga as a generic land. Saga
  is the deck's engine-land (2 Construct activations + a tutor);
  Darksteel Citadel feeds metalcraft/Plating/affinity counts.
- `mulligan_max_lands: 3` mulls 4-land hands, but with 4 Sagas in a
  15-land manabase a "4-land" hand statistically contains a
  threat-land; ceiling raised to 4 (the loader default,
  `decks/gameplan_loader.py:320`). The 5-land soft flood ceiling in
  `ai/mulligan.py` still applies.

Note: `land_priorities` is consumed **only** by mulligan bottoming.
In-game land sequencing (`ai/ev_player.py:1751 _score_land`) has no
gameplan hook — the T1 trace scores Spire of Industry +45.0 vs
Urza's Saga +23.9 and no JSON field can reorder that. If Track H
wants a sanctioned data hook, wiring `gameplan.land_priorities` into
`_score_land` is the natural site.

## Gameplan changes shipped in this diff

`decks/gameplans/pinnacle_affinity.json`:
1. `land_priorities`: Urza's Saga 3.0 (precedent: amulet_titan.json
   declares the same value for the same card), Darksteel Citadel 1.5.
2. `mulligan_max_lands`: 3 -> 4 (threat-land manabase; matches loader
   default).
3. Goal 1 (`DEPLOY_ENGINE`) now names the Saga Construct line in its
   description and lists Urza's Saga among `engines` /
   `card_priorities` so role-derived consumers (BHI opponent model,
   discard advisor) see the deck's actual keystone.

`decks/gameplans/affinity.json` (shares the shape: its sim list runs
4x Urza's Saga in a 22-land manabase) receives the same
`land_priorities` declaration only. Its `mulligan_max_lands: 3` is
left alone — with 22 lands the 4-land ceiling argument does not carry
over, and no Affinity replay evidence was gathered in this track.

## Result (honest numbers)

`python run_meta.py --field "Pinnacle Affinity" -n 15`, standard seeds:

| | field avg |
|---|---|
| before (origin/main) | **28.2%** |
| after (this diff) | **30.3%** |

+2.1pp — consistent with the diagnosis that the dominant losses trace
to Finding 2 (uncastable payoffs) and the unhooked activation timing
(Finding 1), both Track H mechanisms. The remaining ~15-30pp gap to
the [45-60] band is expected to close only when Track H lands the
improvise/warp artifact-detection fix and an EV hook for granted
activations. Full suite after this diff: 2258 passed / 0 failed
(baseline 2253 + 5 new); all three ratchets green; the pinned
mulligan-floor tests still pass.
