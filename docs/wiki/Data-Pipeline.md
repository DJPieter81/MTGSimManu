---
title: "Wiki: Data Pipeline"
status: active
priority: secondary
session: 2026-07-05
tags:
  - wiki
  - data
  - provenance
summary: |
  Wiki page — ModernAtomic parts, MTGJSON provenance, merge_db numeric
  ordering, and the fabricated-part9 forensics story. Staged under
  docs/wiki/ pending wiki publication.
---

# Data Pipeline

Everything the simulator knows about cards comes from one file: `ModernAtomic.json` — 21,795 Modern-legal cards derived from [MTGJSON](https://mtgjson.com). Because the file is too large to commit whole, it lives in the repo as numbered part files and is assembled locally.

## Assembling the database

```bash
python3 merge_db.py
```

That command is the **single source of truth** for assembly. It:

- globs *all* `ModernAtomic_part*.json` files — never a hand-picked subset;
- merges them in **numeric** order (part10 after part9 — lexicographic order would sort `part10` before `part9` and let stale parts win);
- handles both part shapes (`{"meta","data"}` wrapper and bare card dict).

Never hand-roll the merge. A hand-rolled "parts 1–8" recipe once ran whole sessions against a silently stale database and produced win-rate snapshots that diverged between CI and local runs — a bug class that looks like AI regression and is actually stale data.

## Provenance is enforced

Every part file must carry MTGJSON provenance (`meta.version`). This is a test, not a convention: `tests/test_db_part_provenance.py` fails the suite if any part lacks it. New card data enters exclusively via `update_modern_atomic.py` from a real MTGJSON export — never by hand-authoring a part file.

## The part9 incident (cautionary tale)

On 2026-05-10 an unprovenanced ninth part file, `ModernAtomic_part9.json`, entered the repo carrying roughly 30 card texts that had been **fabricated** — hand-written oracle text that never came from MTGJSON. The cards looked plausible; the simulator loaded them happily; and for almost two months a fraction of simulated games were played with rules text that does not exist in Magic.

The corruption surfaced indirectly: the Ruby Storm vs Dimir Midrange matchup produced systematically different win rates depending on which database a session had assembled — the "canonical-DB outlier" that resisted every AI-side explanation. Forensics on 2026-07-05 traced it to part9, which was removed the same day; the provenance test above was added so an unprovenanced part can never land again.

**Morals:**

1. Data bugs masquerade as AI bugs. When a win rate diverges between environments, diff the *data* before profiling the code.
2. Provenance must be machine-checked. A convention ("parts come from MTGJSON") held for eight parts and failed silently on the ninth.
3. Assembly logic must be centralized. Two of the three incidents in this pipeline's history came from bypassing `merge_db.py`.

## Refreshing for new sets

```bash
python3 update_modern_atomic.py       # pulls a fresh MTGJSON export, rewrites parts
git add ModernAtomic_part*.json
git commit -m "chore: refresh ModernAtomic for new sets"
```

A freshness check (part-file mtime > 14 days → warning) runs before any new deck is registered, so new-set cards are never silently missing from imports.
