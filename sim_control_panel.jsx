import { useState } from "react";

// ── Data ──────────────────────────────────────────────────────────────
const LEGACY_DECKS = [
  "affinity","belcher","boros","bug","burn","cephalid","cloudpost","depths",
  "dimir","dimir_b","dimir_flash","dnt","doomsday","eight_cast","eldrazi",
  "elves","goblins","infect","lands","mardu","mono_black","ocelot","oops",
  "painter","prison","reanimator","show","sneak_a","sneak_b","storm","tes",
  "ur_aggro","ur_delver","ur_tempo","uwx","wan_shi_tong"
];
const MODERN_DECKS = [
  "Boros Energy","Jeskai Blink","Ruby Storm","Affinity","Eldrazi Tron",
  "Amulet Titan","Goryo's Vengeance","Domain Zoo","Living End",
  "Izzet Prowess","Dimir Midrange","4c Omnath","4/5c Control","Azorius Control"
];

const LEGACY_RUNS = [
  { file: "matrix_20260411_134630.json", decks: 36, n: 100, tag: "matrix", ts: "2026-04-11T13:46:30" },
  { file: "matrix_20260411_131017.json", decks: 36, n: 100, tag: "matrix", ts: "2026-04-11T13:10:17" },
  { file: "matrix_20260411_121738.json", decks: 38, n: 100, tag: "matrix", ts: "2026-04-11T12:17:38" },
  { file: "matrix_20260411_104511.json", decks: 38, n: 100, tag: "matrix", ts: "2026-04-11T10:45:11" },
  { file: "custom_matrix_20260410_223608.json", decks: 38, n: 50, tag: "custom", ts: "2026-04-10T22:36:08" },
  { file: "custom_matrix_20260410_220305.json", decks: 38, n: 50, tag: "custom", ts: "2026-04-10T22:03:05" },
  { file: "custom_matrix_20260410_213914.json", decks: 38, n: 50, tag: "custom", ts: "2026-04-10T21:39:14" },
  { file: "custom_matrix_20260410_180102.json", decks: 38, n: 50, tag: "custom", ts: "2026-04-10T18:01:02" },
  { file: "matrix_20260410_180012.json", decks: 9, n: 50, tag: "matrix", ts: "2026-04-10T18:00:12" },
  { file: "matrix_20260406_163650.json", decks: 16, n: 200, tag: "matrix", ts: "2026-04-06T16:36:50" },
  { file: "matrix_20260406_163112.json", decks: 16, n: 200, tag: "matrix", ts: "2026-04-06T16:31:12" },
  { file: "matrix_20260406_162728.json", decks: 16, n: 50, tag: "matrix", ts: "2026-04-06T16:27:28" },
  { file: "custom_matrix_20260406_012114.json", decks: 17, n: 200, tag: "custom", ts: "2026-04-06T01:21:14" },
];
const MODERN_RUNS = [
  { file: "metagame_results.json", decks: 8, n: 10, tag: "matrix", ts: "2026-04-11T20:15:00" },
];

// ── Theme ─────────────────────────────────────────────────────────────
const bg = "#fafafa", surface = "#ffffff", border = "#e2e4e9", text = "#1a1a1a", muted = "#6b7280";
const accent = "#7c3aed", accentLight = "#ede9fe", accentText = "#5b21b6";
const blue = "#2563eb", blueLight = "#dbeafe";
const green = "#059669", greenLight = "#d1fae5";
const amber = "#d97706", amberLight = "#fef3c7";
const red = "#dc2626", redLight = "#fee2e2";

// ── Helpers ───────────────────────────────────────────────────────────
const fmt = (iso) => {
  if (!iso) return "\u2014";
  const d = new Date(iso);
  const ago = Math.floor((Date.now() - d) / 3600000);
  const rel = ago < 1 ? "< 1h ago" : ago < 24 ? `${ago}h ago` : `${Math.floor(ago / 24)}d ago`;
  return `${d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })} ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })} (${rel})`;
};

const Tag = ({ t, color, bg: bgc }) => (
  <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 10, background: bgc || accentLight, color: color || accentText, fontWeight: 700, letterSpacing: .4, textTransform: "uppercase" }}>{t}</span>
);

const Chip = ({ active, onClick, children }) => (
  <button onClick={onClick} style={{
    padding: "6px 14px", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer", transition: "all .15s",
    border: `1.5px solid ${active ? accent : border}`,
    background: active ? accentLight : surface,
    color: active ? accentText : muted,
  }}>{children}</button>
);

const Check = ({ checked, onClick, label, desc }) => (
  <div style={{ display: "flex", alignItems: "flex-start", cursor: "pointer", padding: "6px 0" }} onClick={onClick}>
    <div style={{
      width: 18, height: 18, borderRadius: 4, marginRight: 10, marginTop: 1, flexShrink: 0, transition: "all .15s",
      border: `2px solid ${checked ? accent : "#d1d5db"}`,
      background: checked ? accent : "#fff",
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>{checked && <span style={{ color: "#fff", fontSize: 12, fontWeight: 800 }}>&#10003;</span>}</div>
    <div>
      <div style={{ fontSize: 13, fontWeight: 600, color: text }}>{label}</div>
      {desc && <div style={{ fontSize: 11, color: muted, marginTop: 1 }}>{desc}</div>}
    </div>
  </div>
);

// ── Main ──────────────────────────────────────────────────────────────
export default function SimControlPanel() {
  const [format, setFormat] = useState("legacy");
  const [tab, setTab] = useState("history");
  const [runType, setRunType] = useState("matrix");
  const [gamesPerPair, setGamesPerPair] = useState(50);
  const [deck1, setDeck1] = useState("");
  const [deck2, setDeck2] = useState("");
  const [seed, setSeed] = useState(55555);
  const [guideDeck, setGuideDeck] = useState("");
  const [bo3Count, setBo3Count] = useState(1);
  const [outputs, setOutputs] = useState({ dashboard: true, replays: false, audit: false, deckGuide: false, bo3Replay: true, gitPush: false });
  const [submitted, setSubmitted] = useState(null);

  // Deck picker for matrix (use array, not Set — Sets don't trigger React re-renders)
  const allDecks = format === "legacy" ? LEGACY_DECKS : MODERN_DECKS;
  const [selectedDecks, setSelectedDecks] = useState([...allDecks]);
  const toggleDeck = (d) => setSelectedDecks(prev => prev.includes(d) ? prev.filter(x => x !== d) : [...prev, d]);
  const selectAll = () => setSelectedDecks([...allDecks]);
  const selectNone = () => setSelectedDecks([]);

  const runs = format === "legacy" ? LEGACY_RUNS : MODERN_RUNS;
  const latest = runs[0];
  const toggleOut = (k) => setOutputs(p => ({ ...p, [k]: !p[k] }));

  // Reset deck selection when switching format
  const switchFormat = (f) => {
    setFormat(f);
    setSelectedDecks([...(f === "legacy" ? LEGACY_DECKS : MODERN_DECKS)]);
    setDeck1(""); setDeck2(""); setGuideDeck("");
  };

  const buildPrompt = () => {
    const repo = format === "legacy" ? "MTGSimClaude (Legacy) at ~/MTGSimManu/MTGSimClaude" : "MTGSimManu (Modern) at ~/MTGSimManu/MTGSimManu/MTGSimManu";
    const deckList = selectedDecks;
    let step = 1;
    let lines = [`Run simulation in ${repo}:\n`];

    if (runType === "matrix") {
      if (deckList.length === allDecks.length) {
        lines.push(`${step++}. Run: python3 run_meta.py --matrix -n ${gamesPerPair}${format === "modern" ? " --save" : ""}`);
      } else {
        lines.push(`${step++}. Run: python3 run_meta.py --matrix ${deckList.join(" ")} -n ${gamesPerPair}${format === "modern" ? " --save" : ""}`);
      }
      lines.push(`   Decks (${deckList.length}): ${deckList.join(", ")}`);
    } else if (runType === "matchup") {
      lines.push(`${step++}. Run: python3 run_meta.py --matchup ${deck1 || "deck1"} ${deck2 || "deck2"} -n ${gamesPerPair}`);
    } else if (runType === "bo3") {
      const d1 = deck1 || "deck1", d2 = deck2 || "deck2";
      if (bo3Count === 1) {
        const flag = format === "legacy" ? "--verbose" : "--bo3";
        lines.push(`${step++}. Run: python3 run_meta.py ${flag} "${d1}" "${d2}" -s ${seed}`);
      } else {
        lines.push(`${step++}. Run ${bo3Count} Bo3 matches between "${d1}" and "${d2}" starting at seed ${seed} (increment by 1000 per match)`);
        lines.push(`   Seeds: ${Array.from({length: bo3Count}, (_, i) => seed + i * 1000).join(", ")}`);
      }
      if (outputs.bo3Replay) {
        lines.push(`${step++}. Generate interactive HTML Bo3 replay(s) using /mtg-bo3-replayer-v2 skill. Save to output folder with descriptive names (e.g., replay_${d1}_vs_${d2}_s${seed}.html)`);
      }
    } else if (runType === "field") {
      lines.push(`${step++}. Run: python3 run_meta.py --field ${deck1 || "deck1"} -n ${gamesPerPair}`);
    }

    if (outputs.dashboard) lines.push(`${step++}. Rebuild the metagame matrix dashboard (use /mtg-meta-matrix skill). For Modern: run 'python3 -c "from build_dashboard import merge_results, build; merge_results(\\'metagame_results.json\\', \\'metagame_14deck.jsx\\'); build(\\'metagame_14deck.jsx\\', \\'./modern_meta_matrix_full.html\\')"'`);
    if (outputs.replays) lines.push(`${step++}. Generate Bo3 replays for the 3 most interesting outlier matchups (use /mtg-bo3-replayer-v2 skill, seeds 80000+)`);
    if (outputs.audit) lines.push(`${step++}. Run meta_audit.py to generate audit_dashboard.html`);
    if (outputs.deckGuide) lines.push(`${step++}. Generate a comprehensive deck guide for "${guideDeck || "selected deck"}" (use /mtg-deck-guide skill). Include mulligan analysis, matchup guide, sideboard plans, and key hand archetypes.`);
    if (outputs.gitPush) lines.push(`${step++}. Git add all new/changed result files, replays, and dashboards. Commit with a descriptive message summarizing what was run (decks, games/pair, outputs generated). Push to origin main.`);
    lines.push(`\nPresent all generated files when done.`);
    return lines.join("\n");
  };

  // ── Styles ──
  const S = {
    root: { fontFamily: "'Inter', 'Segoe UI', system-ui, sans-serif", background: bg, color: text, minHeight: "100vh", padding: 0 },
    header: { background: "#fff", padding: "24px 28px 18px", borderBottom: `1px solid ${border}`, boxShadow: "0 1px 3px rgba(0,0,0,.04)" },
    title: { fontSize: 22, fontWeight: 800, color: text, margin: 0, letterSpacing: -.5 },
    subtitle: { fontSize: 13, color: muted, marginTop: 4 },
    tabs: { display: "flex", gap: 0, borderBottom: `1px solid ${border}`, padding: "0 28px", background: "#fff" },
    tab: (active) => ({ padding: "10px 20px", fontSize: 13, fontWeight: 600, color: active ? accent : muted, borderBottom: active ? `2px solid ${accent}` : "2px solid transparent", cursor: "pointer", transition: "all .15s" }),
    body: { padding: "20px 28px", maxWidth: 900 },
    card: { background: surface, border: `1px solid ${border}`, borderRadius: 10, padding: 16, marginBottom: 12, boxShadow: "0 1px 2px rgba(0,0,0,.03)" },
    label: { fontSize: 11, fontWeight: 700, color: muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 8, display: "block" },
    stat: { fontSize: 28, fontWeight: 800, fontFamily: "'JetBrains Mono', monospace", color: text },
    statSub: { fontSize: 11, color: muted, marginTop: 2 },
    select: { background: "#fff", border: `1.5px solid ${border}`, borderRadius: 8, color: text, padding: "7px 10px", fontSize: 13, width: "100%", outline: "none" },
    input: { background: "#fff", border: `1.5px solid ${border}`, borderRadius: 8, color: text, padding: "7px 10px", fontSize: 13, width: 80, fontFamily: "'JetBrains Mono', monospace", outline: "none" },
    btn: { background: accent, color: "#fff", border: "none", borderRadius: 8, padding: "10px 24px", fontSize: 14, fontWeight: 700, cursor: "pointer", transition: "all .15s", boxShadow: "0 1px 3px rgba(124,58,237,.3)" },
    btnSec: { background: "#fff", color: accent, border: `1.5px solid ${border}`, borderRadius: 8, padding: "8px 16px", fontSize: 13, fontWeight: 600, cursor: "pointer" },
    prompt: { background: "#1e1b2e", border: `1px solid #312e45`, borderRadius: 10, padding: 16, fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color: "#a5f3a0", whiteSpace: "pre-wrap", lineHeight: 1.6, marginTop: 12 },
    row: { display: "flex", gap: 12, marginBottom: 12 },
    mono: { fontFamily: "'JetBrains Mono', monospace", fontSize: 12 },
    histRow: (i) => ({ display: "grid", gridTemplateColumns: "1fr 55px 55px 80px 180px", gap: 8, padding: "9px 12px", borderRadius: 8, background: i === 0 ? amberLight : i % 2 === 0 ? "#fff" : "#fafafa", borderLeft: i === 0 ? `3px solid ${amber}` : "3px solid transparent", alignItems: "center", fontSize: 13 }),
    deckGrid: { display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 },
    deckChip: (active) => ({ padding: "4px 10px", borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: "pointer", transition: "all .12s", border: `1.5px solid ${active ? accent : "#e5e7eb"}`, background: active ? accentLight : "#fff", color: active ? accentText : muted }),
  };

  return (
    <div style={S.root}>
      <div style={S.header}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ width: 32, height: 32, borderRadius: 8, background: accentLight, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <span style={{ fontSize: 18 }}>&#9876;</span>
          </div>
          <div>
            <h1 style={S.title}>MTG Sim Control Panel</h1>
            <div style={S.subtitle}>Manage simulations, track runs, configure outputs</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <Chip active={format === "legacy"} onClick={() => switchFormat("legacy")}>Legacy ({LEGACY_DECKS.length})</Chip>
          <Chip active={format === "modern"} onClick={() => switchFormat("modern")}>Modern ({MODERN_DECKS.length})</Chip>
        </div>
      </div>

      <div style={S.tabs}>
        {["history", "newrun", "artifacts"].map(t => (
          <div key={t} style={S.tab(tab === t)} onClick={() => setTab(t)}>
            {t === "history" ? "History" : t === "newrun" ? "New Run" : "Artifacts"}
          </div>
        ))}
      </div>

      <div style={S.body}>
        {/* Quick open bar */}
        <div style={{ ...S.card, display: "flex", alignItems: "center", gap: 12, marginBottom: 12, padding: "10px 16px" }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: text }}>Open:</span>
          {format === "modern" && (
            <a href="computer:///Users/lynette/MTGSimManu/MTGSimManu/MTGSimManu/modern_meta_matrix_full.html" target="_blank" rel="noreferrer"
              style={{ fontSize: 13, fontWeight: 600, color: blue, textDecoration: "none", padding: "4px 12px", borderRadius: 6, background: blueLight, border: `1px solid ${blue}33` }}>
              Modern Matrix Dashboard
            </a>
          )}
          {format === "legacy" && (
            <>
              <a href="computer:///Users/lynette/MTGSimManu/MTGSimClaude/results/metagame_report.html" target="_blank" rel="noreferrer"
                style={{ fontSize: 13, fontWeight: 600, color: blue, textDecoration: "none", padding: "4px 12px", borderRadius: 6, background: blueLight, border: `1px solid ${blue}33` }}>
                Legacy Meta Report
              </a>
              <a href="computer:///Users/lynette/MTGSimManu/MTGSimClaude/results/audit_dashboard.html" target="_blank" rel="noreferrer"
                style={{ fontSize: 13, fontWeight: 600, color: red, textDecoration: "none", padding: "4px 12px", borderRadius: 6, background: redLight, border: `1px solid ${red}33` }}>
                Audit Dashboard
              </a>
              <a href="computer:///Users/lynette/MTGSimManu/MTGSimClaude/results/game_replay.html" target="_blank" rel="noreferrer"
                style={{ fontSize: 13, fontWeight: 600, color: green, textDecoration: "none", padding: "4px 12px", borderRadius: 6, background: greenLight, border: `1px solid ${green}33` }}>
                Game Replay
              </a>
            </>
          )}
        </div>

        {/* Stats */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 12, marginBottom: 20 }}>
          {[
            { label: "Total runs", val: runs.length },
            { label: "Latest", val: latest ? fmt(latest.ts).split("(")[0].trim() : "\u2014", sub: latest ? fmt(latest.ts).match(/\(.*\)/)?.[0] : "", small: true },
            { label: "Biggest run", val: runs.length ? Math.max(...runs.map(r => r.decks)) : 0, sub: "decks" },
            { label: "Max games/pair", val: runs.length ? Math.max(...runs.map(r => r.n)) : 0 },
          ].map((s, i) => (
            <div key={i} style={S.card}>
              <span style={S.label}>{s.label}</span>
              <div style={s.small ? { ...S.mono, fontSize: 14, color: amber, fontWeight: 700 } : S.stat}>{s.val}</div>
              {s.sub && <div style={S.statSub}>{s.sub}</div>}
            </div>
          ))}
        </div>

        {/* ── History ── */}
        {tab === "history" && (
          <div style={S.card}>
            <div style={{ ...S.histRow(-1), color: muted, fontSize: 10, fontWeight: 700, letterSpacing: .5, background: "transparent" }}>
              <span>FILE</span><span>DECKS</span><span>N</span><span>TYPE</span><span>DATE</span>
            </div>
            {runs.map((r, i) => (
              <div key={r.file} style={S.histRow(i)}>
                <span style={S.mono}>{r.file.replace(".json", "")}</span>
                <span style={S.mono}>{r.decks}</span>
                <span style={S.mono}>{r.n}</span>
                <Tag t={r.tag} color={r.tag === "matrix" ? blue : amber} bg={r.tag === "matrix" ? blueLight : amberLight} />
                <span style={{ fontSize: 12, color: i === 0 ? amber : muted }}>{fmt(r.ts)}</span>
              </div>
            ))}
          </div>
        )}

        {/* ── New Run ── */}
        {tab === "newrun" && (
          <div>
            {/* Run type */}
            <div style={S.card}>
              <span style={S.label}>Run Type</span>
              <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
                {[
                  { k: "matrix", l: "Full Matrix" }, { k: "matchup", l: "Head-to-Head" },
                  { k: "bo3", l: "Single Bo3" }, { k: "field", l: "Field Sweep" },
                ].map(t => <Chip key={t.k} active={runType === t.k} onClick={() => setRunType(t.k)}>{t.l}</Chip>)}
              </div>

              {/* Matrix config */}
              {runType === "matrix" && (
                <div>
                  <div style={S.row}>
                    <div style={{ flex: 1 }}>
                      <span style={S.label}>Games per pair</span>
                      <input type="number" value={gamesPerPair} onChange={e => setGamesPerPair(+e.target.value)} style={S.input} min={5} step={5} />
                    </div>
                    <div style={{ flex: 1, textAlign: "right", paddingTop: 20 }}>
                      <span style={{ ...S.mono, color: accent, fontWeight: 700 }}>{selectedDecks.length}</span>
                      <span style={{ fontSize: 12, color: muted }}> / {allDecks.length} decks selected</span>
                    </div>
                  </div>
                  <span style={S.label}>Include Decks</span>
                  <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
                    <button onClick={selectAll} style={{ ...S.btnSec, padding: "4px 12px", fontSize: 11 }}>All</button>
                    <button onClick={selectNone} style={{ ...S.btnSec, padding: "4px 12px", fontSize: 11 }}>None</button>
                  </div>
                  <div style={S.deckGrid}>
                    {allDecks.map(d => (
                      <span key={d} style={S.deckChip(selectedDecks.includes(d))} onClick={() => toggleDeck(d)}>{d}</span>
                    ))}
                  </div>
                </div>
              )}

              {/* Matchup config */}
              {runType === "matchup" && (
                <div style={S.row}>
                  <div style={{ flex: 1 }}>
                    <span style={S.label}>Deck 1</span>
                    <select value={deck1} onChange={e => setDeck1(e.target.value)} style={S.select}>
                      <option value="">Select...</option>
                      {allDecks.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                  </div>
                  <div style={{ flex: 1 }}>
                    <span style={S.label}>Deck 2</span>
                    <select value={deck2} onChange={e => setDeck2(e.target.value)} style={S.select}>
                      <option value="">Select...</option>
                      {allDecks.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                  </div>
                  <div style={{ width: 100 }}>
                    <span style={S.label}>Games</span>
                    <input type="number" value={gamesPerPair} onChange={e => setGamesPerPair(+e.target.value)} style={S.input} min={5} />
                  </div>
                </div>
              )}

              {/* Bo3 config */}
              {runType === "bo3" && (
                <div>
                  <div style={S.row}>
                    <div style={{ flex: 1 }}>
                      <span style={S.label}>Deck 1</span>
                      <select value={deck1} onChange={e => setDeck1(e.target.value)} style={S.select}>
                        <option value="">Select...</option>
                        {allDecks.map(d => <option key={d} value={d}>{d}</option>)}
                      </select>
                    </div>
                    <div style={{ flex: 1 }}>
                      <span style={S.label}>Deck 2</span>
                      <select value={deck2} onChange={e => setDeck2(e.target.value)} style={S.select}>
                        <option value="">Select...</option>
                        {allDecks.map(d => <option key={d} value={d}>{d}</option>)}
                      </select>
                    </div>
                  </div>
                  <div style={S.row}>
                    <div style={{ width: 100 }}>
                      <span style={S.label}>Seed</span>
                      <input type="number" value={seed} onChange={e => setSeed(+e.target.value)} style={S.input} />
                    </div>
                    <div style={{ width: 120 }}>
                      <span style={S.label}>Matches</span>
                      <input type="number" value={bo3Count} onChange={e => setBo3Count(Math.max(1, +e.target.value))} style={S.input} min={1} max={100} />
                      <span style={{ fontSize: 11, color: muted, marginLeft: 6 }}>Bo3{bo3Count > 1 ? "s" : ""}</span>
                    </div>
                    <div style={{ flex: 1, display: "flex", alignItems: "flex-end", paddingBottom: 2 }}>
                      <Check checked={outputs.bo3Replay} onClick={() => toggleOut("bo3Replay")} label="Generate HTML replay" desc="" />
                    </div>
                  </div>
                </div>
              )}

              {/* Field config */}
              {runType === "field" && (
                <div style={S.row}>
                  <div style={{ flex: 1 }}>
                    <span style={S.label}>Deck</span>
                    <select value={deck1} onChange={e => setDeck1(e.target.value)} style={S.select}>
                      <option value="">Select...</option>
                      {allDecks.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                  </div>
                  <div style={{ width: 100 }}>
                    <span style={S.label}>Games</span>
                    <input type="number" value={gamesPerPair} onChange={e => setGamesPerPair(+e.target.value)} style={S.input} min={5} />
                  </div>
                </div>
              )}
            </div>

            {/* Outputs */}
            <div style={S.card}>
              <span style={S.label}>Post-Run Outputs</span>
              <Check checked={outputs.dashboard} onClick={() => toggleOut("dashboard")} label="Rebuild metagame dashboard" desc="Heatmap matrix, tier chips, weighted WR" />
              <Check checked={outputs.replays} onClick={() => toggleOut("replays")} label="Generate outlier replays" desc="Bo3 HTML replays for top 3 suspicious matchups" />
              <Check checked={outputs.audit} onClick={() => toggleOut("audit")} label="Run meta audit" desc="Outlier detection, strategy audit, audit_dashboard.html" />
              <div style={{ borderTop: `1px solid ${border}`, marginTop: 8, paddingTop: 8 }}>
                <Check checked={outputs.deckGuide} onClick={() => toggleOut("deckGuide")} label="Generate deck guide" desc="Mulligan analysis, matchup guide, sideboard plans, hand archetypes" />
                {outputs.deckGuide && (
                  <div style={{ marginLeft: 28, marginTop: 6 }}>
                    <span style={{ ...S.label, marginBottom: 4 }}>Deck to guide</span>
                    <select value={guideDeck} onChange={e => setGuideDeck(e.target.value)} style={{ ...S.select, maxWidth: 260 }}>
                      <option value="">Select deck...</option>
                      {allDecks.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                  </div>
                )}
              </div>
              <div style={{ borderTop: `1px solid ${border}`, marginTop: 8, paddingTop: 8 }}>
                <Check checked={outputs.gitPush} onClick={() => toggleOut("gitPush")} label="Git commit & push" desc="Stage results, commit with summary, push to origin main" />
              </div>
            </div>

            {/* Submit */}
            <button style={S.btn} onClick={() => setSubmitted(buildPrompt())}>Generate Task</button>

            {submitted && (
              <div style={{ marginTop: 16 }}>
                <span style={{ fontSize: 11, color: muted, fontWeight: 600, letterSpacing: .5 }}>TASK PROMPT</span>
                <div style={S.prompt}>{submitted}</div>
                <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                  <button style={S.btnSec} onClick={() => navigator.clipboard?.writeText(submitted)}>Copy to clipboard</button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Artifacts ── */}
        {tab === "artifacts" && (
          <div>
            <div style={S.card}>
              <span style={S.label}>Dashboards & Reports</span>
              {[
                ...(format === "modern" ? [{ name: "modern_meta_matrix_full.html", type: "Matrix", c: blue, bg: blueLight }] : []),
                ...(format === "legacy" ? [
                  { name: "audit_dashboard.html", type: "Audit", c: red, bg: redLight },
                  { name: "game_replay.html", type: "Replay", c: green, bg: greenLight },
                  { name: "metagame_report.html", type: "Report", c: blue, bg: blueLight },
                  { name: "player_guide.html", type: "Guide", c: accent, bg: accentLight },
                ] : []),
              ].map(a => (
                <div key={a.name} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderRadius: 8, background: bg, marginTop: 6 }}>
                  <div style={{ width: 8, height: 8, borderRadius: "50%", background: a.c }} />
                  <span style={{ ...S.mono, flex: 1 }}>{a.name}</span>
                  <Tag t={a.type} color={a.c} bg={a.bg} />
                </div>
              ))}
            </div>
            <div style={S.card}>
              <span style={S.label}>Data Files</span>
              {[
                ...(format === "modern" ? [
                  { name: "metagame_14deck.jsx", desc: "14-deck data (wins, matchup_cards, deck_cards)" },
                  { name: "metagame_results.json", desc: "Latest matrix results" },
                ] : []),
                ...(format === "legacy" ? [
                  { name: latest?.file || "matrix_*.json", desc: `Latest: ${latest?.decks || "?"}d x ${latest?.n || "?"}g` },
                ] : []),
              ].map(a => (
                <div key={a.name} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderRadius: 8, background: bg, marginTop: 6 }}>
                  <span style={{ ...S.mono, flex: 1, color: muted }}>{a.name}</span>
                  <span style={{ fontSize: 11, color: muted }}>{a.desc}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
