#!/usr/bin/env python3
"""Scan results/ and replays/ directories and produce run_history.json for the control panel.

Covers: matrix JSON, traces, bo3 logs, HTML replays, dashboards, audits, reports,
sweeps, showcases, deck guides, data files, graded traces, symmetry audits.

Usage: python3 scan_results.py [--js]   (--js also emits JS snippet for embedding)
"""

import json, os, re, glob, subprocess, sys
from datetime import datetime


def git_date(path):
    """Get git commit date for a file, fallback to mtime."""
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%aI", "--", path],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        if out:
            return out
    except Exception:
        pass
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
    except Exception:
        return datetime.now().isoformat()


def size_kb(path):
    try:
        return round(os.path.getsize(path) / 1024, 1)
    except Exception:
        return 0


def extract_ts_from_name(name):
    """Extract YYYYMMDD_HHMMSS or YYYYMMDD from filename."""
    m = re.search(r'(\d{8})[_T](\d{6})', name)
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").isoformat()
    m = re.search(r'(\d{8})', name)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%d").isoformat()
    return None


def scan(root="."):
    runs = []
    results_dir = os.path.join(root, "results")
    replays_dir = os.path.join(root, "replays")
    seen = set()

    def add(entry):
        key = entry.get("path", entry["file"])
        if key not in seen:
            seen.add(key)
            runs.append(entry)

    # ── Matrix / custom matrix JSON files ──
    for pattern in ["*matrix*.json", "matrix_bo3_*.json"]:
        for f in glob.glob(os.path.join(results_dir, pattern)):
            name = os.path.basename(f)
            ts = extract_ts_from_name(name) or git_date(f)
            tag = "bo3-matrix" if "bo3" in name else "custom" if "custom" in name else "matrix"
            try:
                with open(f) as fh:
                    data = json.load(fh)
                decks = len(data.get("decks", data.get("deck_names", [])))
                n = data.get("n_games", data.get("games_per_pair", 0))
            except Exception:
                decks, n = 0, 0
            add({"file": name, "decks": decks, "n": n, "tag": tag, "ts": ts,
                 "type": "matrix", "path": f"results/{name}", "size_kb": size_kb(f)})

    # ── Root-level matrix data (Modern) ──
    for name in ["metagame_results.json"]:
        f = os.path.join(root, name)
        if os.path.exists(f):
            ts = git_date(f)
            try:
                with open(f) as fh:
                    data = json.load(fh)
                decks = len(data.get("decks", data.get("deck_names", [])))
                n = data.get("n_games", data.get("games_per_pair", 0))
            except Exception:
                decks, n = 0, 0
            add({"file": name, "decks": decks, "n": n, "tag": "matrix", "ts": ts,
                 "type": "matrix", "path": name, "size_kb": size_kb(f)})

    # ── Trace files (individual) → group by matchup ──
    trace_groups = {}
    for f in glob.glob(os.path.join(results_dir, "trace_*.txt")):
        name = os.path.basename(f)
        m = re.match(r'trace_(.+?)_vs_(.+?)_s(\d+)(?:_(\d{8})_(\d{6}))?\.txt', name)
        if not m:
            continue
        d1, d2 = m.group(1), m.group(2)
        key = f"{d1}_vs_{d2}"
        if m.group(4) and m.group(5):
            ts = datetime.strptime(m.group(4) + m.group(5), "%Y%m%d%H%M%S").isoformat()
        else:
            ts = git_date(f)
        if key not in trace_groups:
            trace_groups[key] = {"d1": d1, "d2": d2, "count": 0, "latest_ts": ts, "latest_file": name, "latest_path": f"results/{name}"}
        trace_groups[key]["count"] += 1
        if ts > trace_groups[key]["latest_ts"]:
            trace_groups[key]["latest_ts"] = ts
            trace_groups[key]["latest_file"] = name
            trace_groups[key]["latest_path"] = f"results/{name}"

    for key, g in trace_groups.items():
        label = f"trace: {g['d1']} vs {g['d2']} ({g['count']} run{'s' if g['count']>1 else ''})"
        add({"file": label, "d1": g["d1"], "d2": g["d2"], "tag": "trace", "ts": g["latest_ts"],
             "type": "trace", "path": g["latest_path"], "count": g["count"]})

    # ── Graded trace JSON files ──
    for f in glob.glob(os.path.join(results_dir, "traces", "*_graded.json")):
        name = os.path.basename(f)
        ts = git_date(f)
        add({"file": name, "tag": "graded-trace", "ts": ts, "type": "trace",
             "path": f"results/traces/{name}", "size_kb": size_kb(f)})

    # ── Bo3 text files in results/ ──
    for f in glob.glob(os.path.join(results_dir, "bo3_*.txt")):
        name = os.path.basename(f)
        m = re.match(r'bo3_(.+?)_vs_(.+?)\.txt', name)
        d1 = m.group(1) if m else "?"
        d2 = m.group(2) if m else "?"
        ts = git_date(f)
        add({"file": name, "d1": d1, "d2": d2, "tag": "bo3", "ts": ts,
             "type": "bo3", "path": f"results/{name}", "size_kb": size_kb(f)})

    # ── Replays dir: Bo3 log files ──
    for f in glob.glob(os.path.join(replays_dir, "*.txt")):
        name = os.path.basename(f)
        if name == "README.md":
            continue
        m = re.match(r'(.+?)_vs_(.+?)_s(\d+)\.txt', name)
        d1 = m.group(1).replace("_", " ").title() if m else "?"
        d2 = m.group(2).replace("_", " ").title() if m else "?"
        seed = m.group(3) if m else ""
        ts = git_date(f)
        add({"file": name, "d1": d1, "d2": d2, "seed": seed, "tag": "bo3", "ts": ts,
             "type": "bo3-log", "path": f"replays/{name}", "size_kb": size_kb(f)})

    # ── HTML replays (results/ and replays/) ──
    for d, prefix in [(results_dir, "results"), (replays_dir, "replays")]:
        for f in glob.glob(os.path.join(d, "replay_*.html")) + glob.glob(os.path.join(d, "game_replay*.html")):
            name = os.path.basename(f)
            m = re.match(r'(?:replay_)?(.+?)_vs_(.+?)(?:_s\d+)?\.html', name)
            d1 = m.group(1).replace("_", " ").title() if m else ""
            d2 = m.group(2).replace("_", " ").title() if m else ""
            ts = git_date(f)
            add({"file": name, "d1": d1, "d2": d2, "tag": "replay", "ts": ts,
                 "type": "replay", "path": f"{prefix}/{name}", "size_kb": size_kb(f)})

    # ── Meta matrix HTML dashboards ──
    for f in (glob.glob(os.path.join(results_dir, "meta_matrix_*.html")) +
              glob.glob(os.path.join(results_dir, "mtg_meta_matrix.html")) +
              glob.glob(os.path.join(root, "modern_meta_matrix_full.html"))):
        name = os.path.basename(f)
        ts = extract_ts_from_name(name) or git_date(f)
        rel = f"results/{name}" if "results" in f else name
        tag = "bo3-matrix" if "bo3" in name else "dashboard"
        add({"file": name, "tag": tag, "ts": ts, "type": "dashboard",
             "path": rel, "size_kb": size_kb(f)})

    # ── Showcase ──
    for f in glob.glob(os.path.join(results_dir, "*showcase*.html")):
        name = os.path.basename(f)
        ts = git_date(f)
        add({"file": name, "tag": "showcase", "ts": ts, "type": "showcase",
             "path": f"results/{name}", "size_kb": size_kb(f)})

    # ── Deck guide / reference ──
    for f in glob.glob(os.path.join(results_dir, "*deck_guide*.html")) + glob.glob(os.path.join(results_dir, "reference_*.html")):
        name = os.path.basename(f)
        ts = git_date(f)
        add({"file": name, "tag": "guide", "ts": ts, "type": "guide",
             "path": f"results/{name}", "size_kb": size_kb(f)})

    # ── Audit dashboard ──
    for f in glob.glob(os.path.join(results_dir, "audit_dashboard*.html")):
        name = os.path.basename(f)
        ts = git_date(f)
        add({"file": name, "tag": "audit", "ts": ts, "type": "audit",
             "path": f"results/{name}", "size_kb": size_kb(f)})

    # ── Reports: metagame_report, player_guide, meta_deck_profiles, LLM audit, symmetry ──
    report_patterns = ["metagame_report.html", "player_guide.html", "meta_deck_profiles.txt",
                       "llm_audit_report.md", "symmetry_audit*.md", "tempo_mirror*.md"]
    for pat in report_patterns:
        for f in glob.glob(os.path.join(results_dir, pat)):
            name = os.path.basename(f)
            ts = git_date(f)
            if "audit" in name.lower():
                tag = "audit"
            elif "guide" in name:
                tag = "guide"
            elif "profile" in name:
                tag = "profile"
            else:
                tag = "report"
            add({"file": name, "tag": tag, "ts": ts, "type": "report",
                 "path": f"results/{name}", "size_kb": size_kb(f)})

    # ── Sweeps ──
    for f in glob.glob(os.path.join(results_dir, "*sweep*.json")):
        name = os.path.basename(f)
        if "matrix" in name:
            continue
        ts = git_date(f)
        add({"file": name, "tag": "sweep", "ts": ts, "type": "sweep",
             "path": f"results/{name}", "size_kb": size_kb(f)})

    # ── Data files (card_trimmed, deck_agg, interact, meta) ──
    for f in glob.glob(os.path.join(results_dir, "data", "*.json")):
        name = os.path.basename(f)
        ts = git_date(f)
        add({"file": f"data/{name}", "tag": "data", "ts": ts, "type": "data",
             "path": f"results/data/{name}", "size_kb": size_kb(f)})

    # Sort by timestamp descending
    runs.sort(key=lambda r: r.get("ts", ""), reverse=True)

    out = os.path.join(root, "run_history.json")
    with open(out, "w") as fh:
        json.dump({"generated": datetime.now().isoformat(), "count": len(runs), "runs": runs}, fh, indent=2)
    print(f"Wrote {len(runs)} entries to {out}")

    # Optionally emit JS const for embedding
    if "--js" in sys.argv:
        js_out = os.path.join(root, "run_history_embed.js")
        with open(js_out, "w") as fh:
            fh.write("// Auto-generated by scan_results.py — do not edit\n")
            fh.write(f"// Generated: {datetime.now().isoformat()}\n")
            fh.write(f"const SCAN_HISTORY = {json.dumps(runs, indent=2)};\n")
        print(f"Wrote JS embed to {js_out}")

    return runs


if __name__ == "__main__":
    scan(".")
