---
title: "Wiki: Products"
status: active
priority: secondary
session: 2026-07-05
tags:
  - wiki
  - products
  - dashboard
  - replay
summary: |
  Wiki page — the four output products (dashboard, showcase, deck guides,
  replay viewer) with live GitHub Pages links and build commands. Staged
  under docs/wiki/ pending wiki publication.
---

# Products

Simulation data is only useful if you can read it. MTGSimManu ships four HTML products, all standalone (vanilla JS, no build step), all published via GitHub Pages.

## Metagame dashboard

**Live:** <https://djpieter81.github.io/MTGSimManu/modern_meta_matrix_full.html>

The 19×19 win-rate heatmap. Tier chips (T1–T4), weighted/flat win-rate toggle, archetype filters, and a slide-in detail panel per matchup: narrative insight, average game length, sweep and comeback counts, game-1 vs match win rate, key cards per side, and a data-driven sideboard guide (what actually came in/out in the sims, with post-board win-rate delta).

Build: `python run_meta.py --matrix -n 20 --save` then `python3 build_dashboard.py --merge`.

## Project showcase

**Live:** <https://djpieter81.github.io/MTGSimManu/templates/reference_showcase.html>

A single-page tour of the whole project: the layered architecture, the AI decision pipeline, validation methodology, and links to the other products. Good first link to send someone.

## Deck guides

**Live example (Boros Energy):** <https://djpieter81.github.io/MTGSimManu/templates/reference_deck_guide.html>

Tournament-style guides generated from simulation data: decklist with per-card sim stats (casts, damage, kills) and Scryfall hover previews, game-plan timeline derived from the deck's gameplan JSON, kill-turn distribution, real simulated opening hands with turn-by-turn commentary, tiered matchup spread, and strategic findings mined from hundreds of simulated matches. Built with `python build_guide.py "Deck Name" out.html`.

## Bo3 replay viewer

**Live example:** <https://djpieter81.github.io/MTGSimManu/replays/replay_boros_vs_zoo.html>

Step through a full Best-of-3 match turn by turn: board states, combat with per-attacker damage breakdowns, block reasoning, lethal callouts, sideboard swaps between games — and an expandable dot next to each play that reveals the AI's goal reasoning for that exact decision. Pipeline:

```bash
python run_meta.py --bo3 "Ruby Storm" "Affinity" -s 55555 > replays/log.txt
python build_replay.py replays/log.txt replay.html 55555
```

## Provenance rule

Every number in every product traces to one function and one data file — no hand-edited stats. Dashboards rebuild from `metagame_data.jsx`, guides read the same object, and replays parse committed simulation logs. If a page is meant to be viewed live, its HTML is committed to the repo (GitHub Pages serves from `main`).
