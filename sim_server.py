#!/usr/bin/env python3
"""Local sim server — accepts POST requests from the control panel to run sims.

Start: python3 sim_server.py
Default port: 8765
CORS enabled for GitHub Pages origin.

POST /run  { run_type, decks, games, seed, bo3_count, outputs, guide_deck, format }
GET  /status  → { running, last_run, log_tail }
"""

import http.server, json, subprocess, threading, time, os, sys

# Detect which repo we're in
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
IS_MODERN = "MTGSimManu" in os.path.basename(REPO_DIR)
DEFAULT_PORT = 8765 if IS_MODERN else 8766
PORT = int(os.environ.get("SIM_PORT", DEFAULT_PORT))

state = {"running": False, "last_run": None, "log": "", "pid": None}

def run_sim(params):
    state["running"] = True
    state["log"] = ""
    run_type = params.get("run_type", "matrix")
    decks = params.get("decks", "")
    games = params.get("games", "50")
    seed = params.get("seed", "55555")
    bo3_count = int(params.get("bo3_count", "1"))
    outputs = params.get("outputs", "")
    guide_deck = params.get("guide_deck", "")

    cmds = []

    # Build sim command
    if run_type == "matrix":
        deck_arg = decks.replace(",", " ") if decks else ""
        save_flag = " --save" if IS_MODERN else ""
        if deck_arg:
            cmds.append(f"python3 run_meta.py --matrix {deck_arg} -n {games}{save_flag}")
        else:
            cmds.append(f"python3 run_meta.py --matrix -n {games}{save_flag}")
    elif run_type == "matchup":
        parts = decks.split(",", 1)
        d1, d2 = parts[0].strip(), parts[1].strip() if len(parts) > 1 else "dimir"
        cmds.append(f'python3 run_meta.py --matchup "{d1}" "{d2}" -n {games}')
    elif run_type == "bo3":
        parts = decks.split(",", 1)
        d1, d2 = parts[0].strip(), parts[1].strip() if len(parts) > 1 else "dimir"
        flag = "--verbose" if not IS_MODERN else "--bo3"
        for i in range(bo3_count):
            s = int(seed) + i * 1000
            cmds.append(f'python3 run_meta.py {flag} "{d1}" "{d2}" -s {s}')
    elif run_type == "field":
        d1 = decks.split(",")[0].strip() if decks else "storm"
        cmds.append(f'python3 run_meta.py --field "{d1}" -n {games}')

    # Post-run outputs
    if "dashboard" in outputs and IS_MODERN:
        cmds.append('python3 -c "from build_dashboard import merge_results, build; merge_results(\'metagame_results.json\', \'metagame_14deck.jsx\'); build(\'metagame_14deck.jsx\', \'./modern_meta_matrix_full.html\')"')
    if "audit" in outputs:
        cmds.append("python3 meta_audit.py --run")

    # Scan results
    cmds.append("python3 scan_results.py")

    # Git push
    if "gitpush" in outputs:
        cmds.append('git add -A && git diff --cached --quiet || git commit -m "data: sim run via local server" && git push origin main')

    # Execute
    full_cmd = " && ".join(cmds)
    state["log"] = f"$ {full_cmd}\n\n"
    try:
        proc = subprocess.Popen(
            full_cmd, shell=True, cwd=REPO_DIR,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        state["pid"] = proc.pid
        for line in proc.stdout:
            state["log"] += line
        proc.wait()
        state["log"] += f"\n[exit code: {proc.returncode}]"
    except Exception as e:
        state["log"] += f"\n[error: {e}]"
    finally:
        state["running"] = False
        state["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        state["pid"] = None


class Handler(http.server.BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({
                "running": state["running"],
                "last_run": state["last_run"],
                "log_tail": state["log"][-2000:] if state["log"] else "",
                "repo": "Modern" if IS_MODERN else "Legacy",
                "pid": state["pid"],
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/run":
            if state["running"]:
                self.send_response(409)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Simulation already running"}).encode())
                return

            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}

            thread = threading.Thread(target=run_sim, args=(body,), daemon=True)
            thread.start()

            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started", "params": body}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        print(f"[sim-server] {args[0]}" if args else "")


if __name__ == "__main__":
    print(f"MTG Sim Server ({'Modern' if IS_MODERN else 'Legacy'}) — http://localhost:{PORT}")
    print(f"Repo: {REPO_DIR}")
    print(f"POST /run   — start simulation")
    print(f"GET  /status — check progress")
    server = http.server.HTTPServer(("", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
