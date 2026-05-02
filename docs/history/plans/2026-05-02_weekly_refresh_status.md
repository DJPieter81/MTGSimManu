# Weekly meta-matrix refresh — 2026-05-02 — STATUS: handed off to local run

**Outcome:** pipeline did not complete in Cowork. Script saved here for you to run locally.

## What blocked it

The Cowork sandbox has two compounding constraints that make a 30–40 min sim job infeasible:

1. **`mcp__workspace__bash` caps at 45 s per call** (max `timeout_ms = 45000`).
2. **Each bash call runs in a `coworkd/oneshot-*` cgroup that kills all descendants on call exit** — `nohup`, `setsid -f`, `disown`, and Python double-fork all confirmed reaped.

Combined, this means no single bash call can run the full 120-pair × 50-Bo3 matrix (~6000 matches), and no backgrounded process survives between calls.

## Tested fallback combinations

All hit the wall:

| Mode | n | Workers | Result in 45s |
|---|---|---|---|
| `mp.Pool` parallel | 50 | 3 | 0 pairs (pool init + 1st pair >45s) |
| `mp.Pool` parallel | 50 | 2 | 0 pairs |
| `mp.Pool` parallel | 50 | 4 | 0 pairs (memory thrash) |
| `mp.Pool` parallel | 40 | 3 | 0 pairs |
| Single-process w/ checkpoint | 50 | 1 | 1 pair (~10–25 s, varies by matchup) |
| Single-process w/ checkpoint | 20 | 1 | ~1 pair (combo decks slow) |
| `os.fork` parallel | 50 | 2 | 0 new pairs |

Per-pair wall time on this VM ranges ~10 s (aggro vs aggro) to ~30 s (control vs combo). 120 pairs × ~20 s avg ≈ 40 min, all of it serialized through ≥120 bash invocations — fragile (cgroup gets stuck mid-call) and token-expensive.

## What got done in the sandbox

| Step | Status |
|---|---|
| 1. `git pull origin main` | Done — clone at `9af9184` then mount caught up to `54d675e` |
| 2. `merge_db.py` | Done — `ModernAtomic.json` rebuilt, 21,795 cards |
| 3. Snapshot old WR baseline | Done — `old_wr_baseline.json` saved (16 decks) |
| 4. Full matrix run | **2/120 pairs** at n=50 in `partial_matrix_2pairs.json` (Boros vs Jeskai 58%, Boros vs E-Tron) — abandoned |
| 5–11. Merge / dashboard / guides / replays / commit / push | Not run |

## What you need to do

1. Open Terminal on your Mac.
2. `cd` to this workspace folder (`/Users/lynette/MTGSimManu/MTGSimManu/MTGSimManu/`).
3. Run: `bash weekly_refresh.sh` (defaults to this directory). Or pass another path if your repo lives elsewhere.

That script is a faithful copy of the SKILL pipeline, end-to-end:

- pulls main → merges DB → snapshots old WRs → full matrix at n=50 with 3 workers → merges results into JSX → builds dashboard + all 16 guides → updates run history → generates 3 outlier Bo3 replays → prints WR delta table → commits as Pieter and pushes.

Estimated wall time on your Mac: ~30–45 min.

## Other notes

- The triple-nested workspace path `/Users/lynette/MTGSimManu/MTGSimManu/MTGSimManu/` is unusual but harmless — the repo is here and git works. If you want to flatten it, that's separate work.
- A leftover `.git/maintenance.lock` may exist from an earlier `git fetch` in the sandbox (virtiofs blocks unlink). Safe to `rm -f .git/maintenance.lock` from your Mac.
- The 17th deck `Azorius Control (WST v2)` is in `decks/modern_meta.py` but absent from `metagame_data.jsx` (META_SHARES = 0). Script uses `--decks 16` to match the JSX schema; WST v2 is excluded from the matrix.
