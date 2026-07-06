---
title: "Wiki: Session Log 2026-07-05"
status: active
priority: secondary
session: 2026-07-05
tags:
  - wiki
  - session-summary
summary: |
  Wiki page — condensed record of the 2026-07-05 engine+AI overhaul
  session (25 PRs). Full record in
  docs/history/sessions/2026-07-05_engine_ai_overhaul.md. Staged under
  docs/wiki/ pending wiki publication.
---

# Session Log — 2026-07-05: The Engine + AI Overhaul

25 pull requests merged to `main` in a single day of parallel session waves. End state: **2253 tests passed / 0 failed**, a 19-deck July 2026 metagame, and a definitive Bo3 matrix audited against ground-truth calibration bands.

## The waves

| Wave | PRs | Theme |
|------|-----|-------|
| Engine/AI fixes | #441–#445 | Karoo multi-mana, imprint-copy, token cessation, impulse-draw EV, replay linter |
| Probe wave | #447–#455 | Resolver/SBA unification, clause-scoped oracle predicates, calibration bands, close-game gear-shift, saga chapters, storm chain math, canonical DB merge fix |
| Meta refresh | #456–#458 | Part9 forensics + removal, per-task model registry, 19-deck July metagame |
| Tuning wave | #460–#465 | Instant Reanimator + Goryo's gameplans, mulligan bottoming, CR 400.7 object identity, anchor reconciliation |
| Definitive matrix | #466 | 19-deck Bo3 n=20 on fully merged main + dashboard |
| Showcase | #467 | Regenerated guide/replay artifacts and showcase stats |

## Headline changes

- **Four Comprehensive Rules restored from dead code** (#447): CR 608.2b (spell with all targets illegal fizzles), CR 704.5i (deathtouch destruction), CR 704.5h (lethal-damage destruction, indestructible exempt), CR 704.5c (10+ poison loses). All were implemented but unreachable — the live resolution path never called them.
- **CR 400.7 object identity** (#462): permanents get a battlefield-entry sequence number; delayed riders (end-of-turn exile) drop when their tracked object has changed zones.
- **CR 714 saga chapters** (#451): saga oracle text parses into real chapter abilities.
- **Fabricated data purged** (#455/#456): `ModernAtomic_part9.json` contained ~30 hand-fabricated card texts; removed, with MTGJSON provenance now test-enforced. See [Data-Pipeline](Data-Pipeline).
- **Calibration bands** (#449): every matrix save is now audited against sourced real-world win-rate priors. See [Calibration-Methodology](Calibration-Methodology).
- **July 2026 meta** (#458/#466): 19 decks including Instant Reanimator and Boros Ponza.

## Findings worth remembering

1. **Fabricated data hides for months.** Part9's invented card texts survived from May to July because nothing checked provenance. The fix is a test, not vigilance.
2. **"Implemented" is not "reachable."** Four CR rules existed as clean, plausible code that no live path invoked. Rule coverage claims need tests that go through the real resolution path.
3. **Stacked-PR retargeting is a trap.** Retargeting a stacked PR after its base merges silently pulls the base's commits into the child's diff; two reconciliation PRs (#464, #465) exist because of it. Re-verify the diff after every retarget.
4. **CI/local divergence usually means data skew.** The mysterious win-rate divergence between CI and local runs was a stale hand-merged card database, not nondeterminism. Canonical `merge_db.py` before any snapshot work.

Full session record with the complete PR table: `docs/history/sessions/2026-07-05_engine_ai_overhaul.md` in the repository.
