#!/usr/bin/env bash
# Weekly MTGSimManu metagame refresh — run this locally on your Mac.
# Mirrors the SKILL.md pipeline. Stops on the first failure.
#
# Usage: bash weekly_refresh.sh [REPO_PATH]
#   REPO_PATH defaults to the directory this script lives in.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${1:-$SCRIPT_DIR}"

if [[ ! -d "$REPO/.git" ]]; then
  echo "No git repo at $REPO. Either pass the correct path, or clone first:"
  echo "  git clone https://github.com/DJPieter81/MTGSimManu.git $REPO"
  exit 2
fi

cd "$REPO"

echo "=== Step 1: git pull origin main ==="
git pull origin main

echo "=== Step 2: merge_db.py ==="
python3 merge_db.py

echo "=== Step 3: snapshot old WR baseline ==="
python3 - <<'PY'
import json, re
with open('metagame_data.jsx') as f: src = f.read()
i = src.index('const D = ') + len('const D = ')
d = 0; j = i
for k, ch in enumerate(src[i:], i):
    if ch == '{': d += 1
    elif ch == '}':
        d -= 1
        if d == 0: j = k+1; break
D = json.loads(src[i:j])
old = {o['deck']: {'win_rate': o.get('win_rate'), 'weighted_wr': o.get('weighted_wr'), 'idx': o.get('idx')} for o in D['overall']}
with open('/tmp/old_wr.json', 'w') as f: json.dump(old, f, indent=2)
print(f"Saved {len(old)} old WRs to /tmp/old_wr.json")
PY

echo "=== Step 4: full matrix (16 decks, n=50, 3 workers) — ~30-40 min ==="
python3 run_meta.py --matrix --decks 16 -n 50 --save --workers 3

echo "=== Step 5: merge results into JSX ==="
python3 merge_matrix_results.py

echo "=== Step 6: build dashboard ==="
python3 build_dashboard.py metagame_data.jsx modern_meta_matrix_full.html
ls -lh modern_meta_matrix_full.html

echo "=== Step 7: build all deck guides ==="
python3 build_guide.py --all guides/

echo "=== Step 8: scan_results.py --js ==="
python3 scan_results.py --js

echo "=== Step 9: 3 outlier Bo3 replays ==="
mkdir -p replays
python3 - <<'PY'
import json, re, subprocess, os
with open('metagame_data.jsx') as f: src = f.read()
i = src.index('const D = ') + len('const D = ')
d = 0; j = i
for k, ch in enumerate(src[i:], i):
    if ch == '{': d += 1
    elif ch == '}':
        d -= 1
        if d == 0: j = k+1; break
D = json.loads(src[i:j])

EXPECTED = {
    'Boros Energy': (50,70), 'Affinity': (45,60), 'Eldrazi Tron': (50,65),
    'Jeskai Blink': (45,60), 'Ruby Storm': (40,55), 'Domain Zoo': (50,65),
    'Izzet Prowess': (45,60), 'Dimir Midrange': (45,60),
    'Amulet Titan': (40,55), "Goryo's Vengeance": (40,55), 'Living End': (40,55),
    '4c Omnath': (45,60), '4/5c Control': (40,55),
    'Azorius Control': (40,55), 'Azorius Control (WST)': (40,55),
    'Pinnacle Affinity': (45,60),
}

scored = []
for o in D['overall']:
    lo, hi = EXPECTED.get(o['deck'], (40, 60))
    wr = o['win_rate']
    if wr < lo: scored.append((lo - wr, o['deck'], o['idx'], 'below', wr))
    elif wr > hi: scored.append((wr - hi, o['deck'], o['idx'], 'above', wr))
scored.sort(reverse=True)
top3 = scored[:3]

print("Top 3 outliers:")
for s in top3: print(" ", s)

seed = 60200
for _, deck, idx, direction, wr in top3:
    wins = D['wins'][idx]
    worst_i = min(range(len(wins)), key=lambda i: wins[i] if i != idx else 999)
    opp = D['decks'][worst_i]
    slug = f"{deck.replace(' ','_').replace('/','_').replace(chr(39),'')}__vs__{opp.replace(' ','_').replace('/','_').replace(chr(39),'')}_s{seed}".lower()
    print(f"\n=== Replay: {deck} vs {opp} (seed {seed}) ===")
    txt = f"replays/{slug}.txt"
    html = f"replays/replay_{slug}.html"
    subprocess.run(['python3', 'run_meta.py', '--bo3', deck, opp, '-s', str(seed)],
                   stdout=open(txt, 'w'), check=True)
    subprocess.run(['python3', 'build_replay.py', txt, html, str(seed)], check=True)
    print(f"  wrote {txt} and {html}")
    seed += 1
PY

echo "=== Step 10: WR delta table ==="
python3 - <<'PY'
import json, re
with open('/tmp/old_wr.json') as f: old = json.load(f)
with open('metagame_data.jsx') as f: src = f.read()
i = src.index('const D = ') + len('const D = ')
d = 0; j = i
for k, ch in enumerate(src[i:], i):
    if ch == '{': d += 1
    elif ch == '}':
        d -= 1
        if d == 0: j = k+1; break
D = json.loads(src[i:j])
new = {o['deck']: o.get('win_rate') for o in D['overall']}
print(f"\n{'Deck':<26} {'Old WR':>8} {'New WR':>8} {'Delta':>8}  Flag")
print("-" * 65)
for d_name in sorted(set(list(old.keys()) + list(new.keys()))):
    o = old.get(d_name, {}).get('win_rate')
    n = new.get(d_name)
    if o is None or n is None:
        print(f"{d_name:<26} {str(o):>8} {str(n):>8} {'N/A':>8}")
        continue
    delta = n - o
    flag = '⚠ ≥5pp' if abs(delta) >= 5 else ''
    print(f"{d_name:<26} {o:>8.1f} {n:>8.1f} {delta:>+8.1f}  {flag}")
PY

echo "=== Step 11: commit + push ==="
git config user.name  "Pieter"
git config user.email "pieterv@infomet.com"
git add metagame_data.jsx modern_meta_matrix_full.html guides/*.html run_history.json run_history_embed.js replays/*.txt replays/replay_*.html
git status --short
git commit -m "data: weekly matrix refresh — n=50 Bo3, 16 decks

Auto-generated by weekly_refresh.sh. See run_history.json for deltas.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>" || { echo "Nothing to commit"; exit 0; }

git push origin main || { git pull --rebase origin main && git push origin main; }

echo "=== DONE ==="
git log -1 --oneline
