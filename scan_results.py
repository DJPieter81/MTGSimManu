#!/usr/bin/env python3
"""Scan results/ and replays/ directories and produce run_history.json for the control panel."""

import json, os, re, glob
from datetime import datetime

def scan(root="."):
    runs = []
    results_dir = os.path.join(root, "results")
    replays_dir = os.path.join(root, "replays")

    # ── Matrix / custom matrix JSON files ──
    for f in sorted(glob.glob(os.path.join(results_dir, "*matrix*.json"))):
        name = os.path.basename(f)
        try:
            with open(f) as fh:
                data = json.load(fh)
            decks = len(data.get("decks", data.get("deck_names", [])))
            n = data.get("n_games", data.get("games_per_pair", 0))
            # Extract timestamp from filename: matrix_YYYYMMDD_HHMMSS.json
            m = re.search(r'(\d{8})_(\d{6})', name)
            if m:
                ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").isoformat()
            else:
                ts = datetime.fromtimestamp(os.path.getmtime(f)).isoformat()
            tag = "custom" if "custom" in name else "matrix"
        except Exception:
            continue
        runs.append({"file": name, "decks": decks, "n": n, "tag": tag, "ts": ts, "type": "matrix", "path": f"results/{name}"})

    # ── Trace files ──
    for f in sorted(glob.glob(os.path.join(results_dir, "trace_*.txt"))):
        name = os.path.basename(f)
        m = re.match(r'trace_(.+?)_vs_(.+?)_s(\d+)(?:_(\d{8})_(\d{6}))?\.txt', name)
        if not m:
            continue
        d1, d2, seed = m.group(1), m.group(2), m.group(3)
        if m.group(4) and m.group(5):
            ts = datetime.strptime(m.group(4) + m.group(5), "%Y%m%d%H%M%S").isoformat()
        else:
            ts = datetime.fromtimestamp(os.path.getmtime(f)).isoformat()
        size_kb = round(os.path.getsize(f) / 1024, 1)
        runs.append({"file": name, "d1": d1, "d2": d2, "seed": seed, "tag": "trace", "ts": ts,
                      "type": "trace", "path": f"results/{name}", "size_kb": size_kb})

    # ── Bo3 text files in results/ ──
    for f in sorted(glob.glob(os.path.join(results_dir, "bo3_*.txt"))):
        name = os.path.basename(f)
        m = re.match(r'bo3_(.+?)_vs_(.+?)\.txt', name)
        d1 = m.group(1) if m else "?"
        d2 = m.group(2) if m else "?"
        ts = datetime.fromtimestamp(os.path.getmtime(f)).isoformat()
        size_kb = round(os.path.getsize(f) / 1024, 1)
        runs.append({"file": name, "d1": d1, "d2": d2, "tag": "bo3", "ts": ts,
                      "type": "bo3", "path": f"results/{name}", "size_kb": size_kb})

    # ── HTML replays in results/ ──
    for f in sorted(glob.glob(os.path.join(results_dir, "replay_*.html")) + glob.glob(os.path.join(results_dir, "game_replay*.html"))):
        name = os.path.basename(f)
        m = re.match(r'replay_(.+?)_vs_(.+?)\.html', name)
        d1 = m.group(1) if m else ""
        d2 = m.group(2) if m else ""
        ts = datetime.fromtimestamp(os.path.getmtime(f)).isoformat()
        size_kb = round(os.path.getsize(f) / 1024, 1)
        runs.append({"file": name, "d1": d1, "d2": d2, "tag": "replay", "ts": ts,
                      "type": "replay", "path": f"results/{name}", "size_kb": size_kb})

    # ── Audit dashboard ──
    audit = os.path.join(results_dir, "audit_dashboard.html")
    if os.path.exists(audit):
        ts = datetime.fromtimestamp(os.path.getmtime(audit)).isoformat()
        size_kb = round(os.path.getsize(audit) / 1024, 1)
        runs.append({"file": "audit_dashboard.html", "tag": "audit", "ts": ts,
                      "type": "audit", "path": "results/audit_dashboard.html", "size_kb": size_kb})

    # ── Replays dir (Bo3 logs) ──
    for f in sorted(glob.glob(os.path.join(replays_dir, "*.txt"))):
        name = os.path.basename(f)
        if name == "README.md":
            continue
        m = re.match(r'(.+?)_vs_(.+?)_s(\d+)\.txt', name)
        d1 = m.group(1) if m else "?"
        d2 = m.group(2) if m else "?"
        seed = m.group(3) if m else ""
        ts = datetime.fromtimestamp(os.path.getmtime(f)).isoformat()
        size_kb = round(os.path.getsize(f) / 1024, 1)
        runs.append({"file": name, "d1": d1, "d2": d2, "seed": seed, "tag": "bo3", "ts": ts,
                      "type": "bo3-log", "path": f"replays/{name}", "size_kb": size_kb})

    # ── HTML replays in replays/ ──
    for f in sorted(glob.glob(os.path.join(replays_dir, "*.html"))):
        name = os.path.basename(f)
        m = re.match(r'(?:replay_)?(.+?)_vs_(.+?)(?:_s\d+)?\.html', name)
        d1 = m.group(1) if m else ""
        d2 = m.group(2) if m else ""
        ts = datetime.fromtimestamp(os.path.getmtime(f)).isoformat()
        size_kb = round(os.path.getsize(f) / 1024, 1)
        runs.append({"file": name, "d1": d1, "d2": d2, "tag": "replay", "ts": ts,
                      "type": "replay", "path": f"replays/{name}", "size_kb": size_kb})

    # ── Sweep/overnight JSON ──
    for f in sorted(glob.glob(os.path.join(results_dir, "*sweep*.json")) + glob.glob(os.path.join(results_dir, "overnight*.json"))):
        name = os.path.basename(f)
        if "matrix" in name:
            continue  # already handled
        ts = datetime.fromtimestamp(os.path.getmtime(f)).isoformat()
        size_kb = round(os.path.getsize(f) / 1024, 1)
        runs.append({"file": name, "tag": "sweep", "ts": ts,
                      "type": "sweep", "path": f"results/{name}", "size_kb": size_kb})

    # ── Metagame report / player guide / deck profiles ──
    for f in sorted(glob.glob(os.path.join(results_dir, "metagame_report.html")) +
                    glob.glob(os.path.join(results_dir, "player_guide.html")) +
                    glob.glob(os.path.join(results_dir, "meta_deck_profiles.txt"))):
        name = os.path.basename(f)
        ts = datetime.fromtimestamp(os.path.getmtime(f)).isoformat()
        size_kb = round(os.path.getsize(f) / 1024, 1)
        tag = "report" if "report" in name else "guide" if "guide" in name else "profile"
        runs.append({"file": name, "tag": tag, "ts": ts,
                      "type": "report", "path": f"results/{name}", "size_kb": size_kb})

    # Sort by timestamp descending
    runs.sort(key=lambda r: r.get("ts", ""), reverse=True)

    out = os.path.join(root, "run_history.json")
    with open(out, "w") as fh:
        json.dump({"generated": datetime.now().isoformat(), "count": len(runs), "runs": runs}, fh, indent=2)
    print(f"Wrote {len(runs)} entries to {out}")
    return runs

if __name__ == "__main__":
    scan(".")
