---
title: "Wiki: Home"
status: active
priority: secondary
session: 2026-07-05
tags:
  - wiki
  - overview
summary: |
  GitHub wiki landing page — project overview, quickstart, live product
  links, and the wiki page index. Staged under docs/wiki/ pending wiki
  publication (see docs/wiki/README.md).
---

# MTGSimManu

**A Magic: The Gathering Modern-format game simulator with EV-based AI decision-making.**

MTGSimManu plays complete, deterministic, offline Best-of-3 matches — with sideboarding — between 19 competitive Modern decks. Every decision the AI makes (mulligans, spell sequencing, combat, targeting, counterspells, storm chains, reanimation, cascade) is scored by expected value and logged so you can audit its reasoning line by line. The output side turns those simulations into an interactive metagame dashboard, tournament-grade deck guides, and step-through match replays.

Python 3.11, one dependency (`pydantic>=2.0`), 21,795-card MTGJSON database.

## Quickstart

```bash
# 1. Assemble the card database (required once per clone)
python3 merge_db.py

# 2. Explore
python run_meta.py --list                      # all 19 decks
python run_meta.py --deck storm                # deck profile + gameplan
python run_meta.py --matchup storm dimir -n 50 # head-to-head win rate (Bo3)
python run_meta.py --field boros -n 30         # one deck vs the field
python run_meta.py --matrix -n 20              # full 19x19 matrix
python run_meta.py --bo3 storm affinity -s 55555   # play-by-play match log
python run_meta.py --trace storm dimir -s 42000    # full AI reasoning trace

# 3. Tests
python -m pytest tests/ -q
```

Matchups, field sweeps, and the matrix all default to **Bo3 with sideboarding** — see [Calibration-Methodology](Calibration-Methodology) for why. Deck aliases work everywhere: `storm`, `boros`, `dimir`, `affinity`, `amulet`, `goryos`, ...

## Live products

- **Metagame dashboard** — interactive 19-deck win-rate heatmap with per-matchup detail and sideboard guides: <https://djpieter81.github.io/MTGSimManu/modern_meta_matrix_full.html>
- **Project showcase** — architecture, AI pipeline, and validation story in one page: <https://djpieter81.github.io/MTGSimManu/templates/reference_showcase.html>
- **Bo3 replay viewer** — turn-by-turn match replay with AI reasoning expandos: <https://djpieter81.github.io/MTGSimManu/replays/replay_boros_vs_zoo.html>

## Wiki index

| Page | What it covers |
|------|----------------|
| [Architecture](Architecture) | The three layers and the AI decision pipeline |
| [Abstraction-Contract](Abstraction-Contract) | The engineering rules that keep the engine mechanic-driven |
| [Data-Pipeline](Data-Pipeline) | Card database parts, MTGJSON provenance, and the part9 incident |
| [Products](Products) | Dashboard, deck guides, replay viewer, showcase |
| [Calibration-Methodology](Calibration-Methodology) | Ground-truth bands, Bo3 canon, standard seeds |
| [Protocols](Protocols) | Session protocols: test-first, loop-break, doc registry |
| [Session-Log-2026-07-05](Session-Log-2026-07-05) | The 25-PR engine+AI overhaul session |

## Sister project

[MTGSimClaude](https://github.com/DJPieter81/MTGSimClaude) simulates the Legacy format (38 decks). The two projects share skills and cross-pollinate architecture; Legacy has deeper per-deck strategy functions, Modern has the better generic architecture (EV scoring, Bayesian hand inference, combat simulation).
