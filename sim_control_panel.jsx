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
  "Boros Energy","Jeskai Blink","Ruby Storm","Affinity","Pinnacle Affinity","Eldrazi Tron",
  "Amulet Titan","Goryo's Vengeance","Domain Zoo","Living End",
  "Izzet Prowess","Dimir Midrange","4c Omnath","4/5c Control","Azorius Control"
];

const LEGACY_BASE = "/Users/lynette/MTGSimManu/MTGSimClaude";
const MODERN_BASE = "/Users/lynette/MTGSimManu/MTGSimManu/MTGSimManu";

// ── Run history (regenerate with: python3 scan_results.py) ───────────
const LEGACY_HISTORY = [
  { file:"matrix_20260411_134630.json", tag:"matrix", type:"matrix", ts:"2026-04-11T13:46:30", decks:36, n:100, path:"results/matrix_20260411_134630.json" },
  { file:"matrix_20260411_131017.json", tag:"matrix", type:"matrix", ts:"2026-04-11T13:10:17", decks:36, n:100, path:"results/matrix_20260411_131017.json" },
  { file:"matrix_20260411_121738.json", tag:"matrix", type:"matrix", ts:"2026-04-11T12:17:38", decks:38, n:100, path:"results/matrix_20260411_121738.json" },
  { file:"matrix_20260411_104511.json", tag:"matrix", type:"matrix", ts:"2026-04-11T10:45:11", decks:38, n:100, path:"results/matrix_20260411_104511.json" },
  { file:"custom_matrix_20260410_223608.json", tag:"custom", type:"matrix", ts:"2026-04-10T22:36:08", decks:38, n:50, path:"results/custom_matrix_20260410_223608.json" },
  { file:"custom_matrix_20260410_220305.json", tag:"custom", type:"matrix", ts:"2026-04-10T22:03:05", decks:38, n:50, path:"results/custom_matrix_20260410_220305.json" },
  { file:"custom_matrix_20260410_213914.json", tag:"custom", type:"matrix", ts:"2026-04-10T21:39:14", decks:38, n:50, path:"results/custom_matrix_20260410_213914.json" },
  { file:"custom_matrix_20260410_180102.json", tag:"custom", type:"matrix", ts:"2026-04-10T18:01:02", decks:38, n:50, path:"results/custom_matrix_20260410_180102.json" },
  { file:"matrix_20260410_180012.json", tag:"matrix", type:"matrix", ts:"2026-04-10T18:00:12", decks:9, n:50, path:"results/matrix_20260410_180012.json" },
  { file:"matrix_20260406_163650.json", tag:"matrix", type:"matrix", ts:"2026-04-06T16:36:50", decks:16, n:200, path:"results/matrix_20260406_163650.json" },
  { file:"matrix_20260406_163112.json", tag:"matrix", type:"matrix", ts:"2026-04-06T16:31:12", decks:16, n:200, path:"results/matrix_20260406_163112.json" },
  { file:"matrix_20260406_162728.json", tag:"matrix", type:"matrix", ts:"2026-04-06T16:27:28", decks:16, n:50, path:"results/matrix_20260406_162728.json" },
  { file:"custom_matrix_20260406_012114.json", tag:"custom", type:"matrix", ts:"2026-04-06T01:21:14", decks:17, n:200, path:"results/custom_matrix_20260406_012114.json" },
  // ── Replays ──
  { file:"replay_oops_vs_dimir_flash.html", tag:"replay", type:"replay", ts:"2026-04-12T09:14:24", d1:"oops", d2:"dimir_flash", path:"results/replay_oops_vs_dimir_flash.html", size_kb:70.0 },
  { file:"game_replay.html", tag:"replay", type:"replay", ts:"2026-04-12T06:58:51", path:"results/game_replay.html", size_kb:40.8 },
  // ── Audit ──
  { file:"audit_dashboard.html", tag:"audit", type:"audit", ts:"2026-04-12T06:58:51", path:"results/audit_dashboard.html", size_kb:134.7 },
  // ── Bo3 logs ──
  { file:"bo3_ur_delver_vs_dimir.txt", tag:"bo3", type:"bo3", ts:"2026-04-12T06:58:51", d1:"ur_delver", d2:"dimir", path:"results/bo3_ur_delver_vs_dimir.txt", size_kb:67.9 },
  // ── Traces (grouped by matchup — 89 total, showing unique matchups) ──
  { file:"trace: burn vs dimir (6 runs)", tag:"trace", type:"trace", ts:"2026-04-11T14:15:51", d1:"burn", d2:"dimir", path:"results/trace_burn_vs_dimir_s100_20260411_141551.txt", count:6 },
  { file:"trace: burn vs dnt (11 runs)", tag:"trace", type:"trace", ts:"2026-04-10T21:45:24", d1:"burn", d2:"dnt", path:"results/trace_burn_vs_dnt_s5_20260410_214524.txt", count:11 },
  { file:"trace: bug vs storm (1 run)", tag:"trace", type:"trace", ts:"2026-04-12T06:58:51", d1:"bug", d2:"storm", path:"results/trace_bug_vs_storm_s42.txt", count:1 },
  { file:"trace: bug vs burn (1 run)", tag:"trace", type:"trace", ts:"2026-04-10T22:25:10", d1:"bug", d2:"burn", path:"results/trace_bug_vs_burn_s5_20260410_222510.txt", count:1 },
  { file:"trace: bug vs depths (1 run)", tag:"trace", type:"trace", ts:"2026-04-11T14:12:09", d1:"bug", d2:"depths", path:"results/trace_bug_vs_depths_s103_20260411_141209.txt", count:1 },
  { file:"trace: burn vs lands (1 run)", tag:"trace", type:"trace", ts:"2026-04-10T20:38:25", d1:"burn", d2:"lands", path:"results/trace_burn_vs_lands_s5_20260410_203825.txt", count:1 },
  { file:"trace: oops vs burn (1 run)", tag:"trace", type:"trace", ts:"2026-04-10T20:22:38", d1:"oops", d2:"burn", path:"results/trace_oops_vs_burn_s5_20260410_202238.txt", count:1 },
  { file:"trace: oops vs dimir (1 run)", tag:"trace", type:"trace", ts:"2026-04-10T22:04:58", d1:"oops", d2:"dimir", path:"results/trace_oops_vs_dimir_s2_20260410_220458.txt", count:1 },
  { file:"trace: oops vs dnt (1 run)", tag:"trace", type:"trace", ts:"2026-04-11T14:07:13", d1:"oops", d2:"dnt", path:"results/trace_oops_vs_dnt_s106_20260411_140713.txt", count:1 },
  { file:"trace: storm vs burn (1 run)", tag:"trace", type:"trace", ts:"2026-04-10T20:22:48", d1:"storm", d2:"burn", path:"results/trace_storm_vs_burn_s3_20260410_202248.txt", count:1 },
  { file:"trace: storm vs dimir (4 runs)", tag:"trace", type:"trace", ts:"2026-04-11T14:14:10", d1:"storm", d2:"dimir", path:"results/trace_storm_vs_dimir_s108_20260411_141410.txt", count:4 },
  { file:"trace: infect vs dimir (3 runs)", tag:"trace", type:"trace", ts:"2026-04-10T22:23:09", d1:"infect", d2:"dimir", path:"results/trace_infect_vs_dimir_s5_20260410_222309.txt", count:3 },
  { file:"trace: reanimator vs dimir (5 runs)", tag:"trace", type:"trace", ts:"2026-04-10T22:25:27", d1:"reanimator", d2:"dimir", path:"results/trace_reanimator_vs_dimir_s5_20260410_222515.txt", count:5 },
  { file:"trace: show vs bug (2 runs)", tag:"trace", type:"trace", ts:"2026-04-11T14:16:12", d1:"show", d2:"bug", path:"results/trace_show_vs_bug_s104_20260411_141612.txt", count:2 },
  { file:"trace: ur_tempo vs bug (3 runs)", tag:"trace", type:"trace", ts:"2026-04-10T22:26:53", d1:"ur_tempo", d2:"bug", path:"results/trace_ur_tempo_vs_bug_s3_20260410_222640.txt", count:3 },
  { file:"trace: ur_tempo vs dimir (2 runs)", tag:"trace", type:"trace", ts:"2026-04-10T22:18:17", d1:"ur_tempo", d2:"dimir", path:"results/trace_ur_tempo_vs_dimir_s7_20260410_221817.txt", count:2 },
  { file:"trace: uwx vs burn (5 runs)", tag:"trace", type:"trace", ts:"2026-04-10T22:29:18", d1:"uwx", d2:"burn", path:"results/trace_uwx_vs_burn_s7_20260410_222918.txt", count:5 },
  { file:"trace: lands vs dimir (1 run)", tag:"trace", type:"trace", ts:"2026-04-10T22:08:11", d1:"lands", d2:"dimir", path:"results/trace_lands_vs_dimir_s0_20260410_220811.txt", count:1 },
  // ── Reports ──
  { file:"metagame_report.html", tag:"report", type:"report", ts:"2026-04-12T06:58:51", path:"results/metagame_report.html", size_kb:29.6 },
  { file:"player_guide.html", tag:"guide", type:"report", ts:"2026-04-12T06:58:51", path:"results/player_guide.html", size_kb:29.7 },
  { file:"meta_deck_profiles.txt", tag:"profile", type:"report", ts:"2026-04-12T06:58:51", path:"results/meta_deck_profiles.txt", size_kb:40.0 },
  // ── Sweeps ──
  { file:"expanded_sweep.json", tag:"sweep", type:"sweep", ts:"2026-04-12T06:58:51", path:"results/expanded_sweep.json" },
  { file:"overnight_sweep.json", tag:"sweep", type:"sweep", ts:"2026-04-12T06:58:51", path:"results/overnight_sweep.json" },
];

const MODERN_HISTORY = [
  { file:"metagame_results.json", tag:"matrix", type:"matrix", ts:"2026-04-11T20:15:00", decks:8, n:10, path:"metagame_results.json" },
  // ── Bo3 logs (replays/) ──
  { file:"boros_energy_vs_domain_zoo_s55555.txt", tag:"bo3", type:"bo3-log", ts:"2026-04-12T09:09:00", d1:"Boros Energy", d2:"Domain Zoo", seed:"55555", path:"replays/boros_energy_vs_domain_zoo_s55555.txt", size_kb:61.7 },
  { file:"azorius_wst_vs_boros_s55555.txt", tag:"bo3", type:"bo3-log", ts:"2026-04-12T09:09:00", d1:"Azorius Control", d2:"Boros Energy", seed:"55555", path:"replays/azorius_wst_vs_boros_s55555.txt", size_kb:37.4 },
  { file:"ruby_storm_vs_affinity_s55555.txt", tag:"bo3", type:"bo3-log", ts:"2026-04-12T09:08:00", d1:"Ruby Storm", d2:"Affinity", seed:"55555", path:"replays/ruby_storm_vs_affinity_s55555.txt", size_kb:25.3 },
  { file:"eldrazi_tron_vs_izzet_prowess_s55555.txt", tag:"bo3", type:"bo3-log", ts:"2026-04-11T18:13:00", d1:"Eldrazi Tron", d2:"Izzet Prowess", seed:"55555", path:"replays/eldrazi_tron_vs_izzet_prowess_s55555.txt", size_kb:60.4 },
  { file:"jeskai_blink_vs_affinity_s55555.txt", tag:"bo3", type:"bo3-log", ts:"2026-04-11T18:13:00", d1:"Jeskai Blink", d2:"Affinity", seed:"55555", path:"replays/jeskai_blink_vs_affinity_s55555.txt", size_kb:33.3 },
  { file:"affinity_vs_domain_zoo_s55555.txt", tag:"bo3", type:"bo3-log", ts:"2026-04-11T18:13:00", d1:"Affinity", d2:"Domain Zoo", seed:"55555", path:"replays/affinity_vs_domain_zoo_s55555.txt", size_kb:37.0 },
  { file:"affinity_vs_izzet_prowess_s55555.txt", tag:"bo3", type:"bo3-log", ts:"2026-04-11T18:13:00", d1:"Affinity", d2:"Izzet Prowess", seed:"55555", path:"replays/affinity_vs_izzet_prowess_s55555.txt", size_kb:26.2 },
];

// ── Theme ─────────────────────────────────────────────────────────────
const bg = "#fafafa", surface = "#ffffff", border = "#e2e4e9", text = "#1a1a1a", muted = "#6b7280";
const accent = "#7c3aed", accentLight = "#ede9fe", accentText = "#5b21b6";
const blue = "#2563eb", blueLight = "#dbeafe";
const green = "#059669", greenLight = "#d1fae5";
const amber = "#d97706", amberLight = "#fef3c7";
const red = "#dc2626", redLight = "#fee2e2";
const teal = "#0d9488", tealLight = "#ccfbf1";
const pink = "#db2777", pinkLight = "#fce7f3";

// ── Tag colors ───────────────────────────────────────────────────────
const TAG_COLORS = {
  matrix: { c: blue, bg: blueLight },
  custom: { c: amber, bg: amberLight },
  trace: { c: teal, bg: tealLight },
  bo3: { c: green, bg: greenLight },
  replay: { c: accent, bg: accentLight },
  audit: { c: red, bg: redLight },
  report: { c: pink, bg: pinkLight },
  guide: { c: pink, bg: pinkLight },
  profile: { c: muted, bg: "#f3f4f6" },
  sweep: { c: amber, bg: amberLight },
};

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

const isClickable = (r) => !!r.path;
const fileLink = (basePath, r) => `computer://${basePath}/${r.path}`;

// ── Filter types for history ─────────────────────────────────────────
const FILTER_TYPES = [
  { k: "all", l: "All" },
  { k: "matrix", l: "Matrix" },
  { k: "trace", l: "Traces" },
  { k: "bo3", l: "Bo3" },
  { k: "replay", l: "Replays" },
  { k: "audit", l: "Audit" },
  { k: "report", l: "Reports" },
  { k: "sweep", l: "Sweeps" },
];

// ── Main ──────────────────────────────────────────────────────────────
export default function SimControlPanel() {
  const [format, setFormat] = useState("legacy");
  const [tab, setTab] = useState("history");
  const [histFilter, setHistFilter] = useState("all");
  const [runType, setRunType] = useState("matrix");
  const [gamesPerPair, setGamesPerPair] = useState(50);
  const [deck1, setDeck1] = useState("");
  const [deck2, setDeck2] = useState("");
  const [seed, setSeed] = useState(55555);
  const [guideDeck, setGuideDeck] = useState("");
  const [bo3Count, setBo3Count] = useState(1);
  const [outputs, setOutputs] = useState({ dashboard: true, replays: false, audit: false, deckGuide: false, bo3Replay: true, gitPush: false });
  const [submitted, setSubmitted] = useState(null);

  const allDecks = format === "legacy" ? LEGACY_DECKS : MODERN_DECKS;
  const [selectedDecks, setSelectedDecks] = useState([...allDecks]);
  const toggleDeck = (d) => setSelectedDecks(prev => prev.includes(d) ? prev.filter(x => x !== d) : [...prev, d]);
  const selectAll = () => setSelectedDecks([...allDecks]);
  const selectNone = () => setSelectedDecks([]);

  const basePath = format === "legacy" ? LEGACY_BASE : MODERN_BASE;
  const allRuns = format === "legacy" ? LEGACY_HISTORY : MODERN_HISTORY;
  const filteredRuns = histFilter === "all" ? allRuns :
    allRuns.filter(r => r.type === histFilter || r.tag === histFilter ||
      (histFilter === "bo3" && (r.type === "bo3" || r.type === "bo3-log")));
  const matrixRuns = allRuns.filter(r => r.type === "matrix");
  const latest = allRuns[0];
  const toggleOut = (k) => setOutputs(p => ({ ...p, [k]: !p[k] }));

  const switchFormat = (f) => {
    setFormat(f);
    setSelectedDecks([...(f === "legacy" ? LEGACY_DECKS : MODERN_DECKS)]);
    setDeck1(""); setDeck2(""); setGuideDeck(""); setHistFilter("all");
  };

  // ── Count by type ──
  const typeCounts = {};
  allRuns.forEach(r => { const k = r.type === "bo3-log" ? "bo3" : r.type; typeCounts[k] = (typeCounts[k] || 0) + 1; });

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
    body: { padding: "20px 28px", maxWidth: 920 },
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
    histRow: (i, clickable) => ({
      display: "grid", gridTemplateColumns: "1fr 80px 140px", gap: 8,
      padding: "9px 12px", borderRadius: 8,
      background: i === 0 ? amberLight : i % 2 === 0 ? "#fff" : "#fafafa",
      borderLeft: i === 0 ? `3px solid ${amber}` : "3px solid transparent",
      alignItems: "center", fontSize: 13,
      cursor: clickable ? "pointer" : "default",
      transition: "all .1s",
    }),
    deckGrid: { display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 },
    deckChip: (active) => ({ padding: "4px 10px", borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: "pointer", transition: "all .12s", border: `1.5px solid ${active ? accent : "#e5e7eb"}`, background: active ? accentLight : "#fff", color: active ? accentText : muted }),
  };

  // ── Render description for history row ──
  const rowDesc = (r) => {
    if (r.type === "matrix") return `${r.decks || "?"}d × ${r.n || "?"}g`;
    if (r.d1 && r.d2) return `${r.d1} vs ${r.d2}`;
    if (r.d1) return r.d1;
    return r.file.replace(/\.(json|txt|html)$/, "");
  };

  const rowExtra = (r) => {
    const parts = [];
    if (r.seed) parts.push(`s${r.seed}`);
    if (r.size_kb) parts.push(`${r.size_kb}KB`);
    if (r.count && r.count > 1) parts.push(`×${r.count}`);
    return parts.join(" · ");
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
            {t === "history" ? `History (${allRuns.length})` : t === "newrun" ? "New Run" : "Artifacts"}
          </div>
        ))}
      </div>

      <div style={S.body}>
        {/* Quick open bar */}
        <div style={{ ...S.card, display: "flex", alignItems: "center", gap: 12, marginBottom: 12, padding: "10px 16px", flexWrap: "wrap" }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: text }}>Open:</span>
          {(format === "modern" ? [
            { label: "Modern Matrix", path: "modern_meta_matrix_full.html", c: blue, bg: blueLight },
            { label: "Control Panel", path: "sim_control_panel.html", c: green, bg: greenLight },
          ] : [
            { label: "Legacy Matrix", path: "results/mtg_meta_matrix.html", c: blue, bg: blueLight },
            { label: "Audit", path: "results/audit_dashboard.html", c: red, bg: redLight },
            { label: "Replay", path: "results/game_replay.html", c: green, bg: greenLight },
            { label: "Meta Report", path: "results/metagame_report.html", c: pink, bg: pinkLight },
            { label: "Player Guide", path: "results/player_guide.html", c: accent, bg: accentLight },
          ]).map(lnk => (
            <a key={lnk.label} href={`computer://${basePath}/${lnk.path}`} target="_blank" rel="noreferrer"
              style={{ fontSize: 13, fontWeight: 600, color: lnk.c, textDecoration: "none", padding: "4px 12px", borderRadius: 6, background: lnk.bg, border: `1px solid ${lnk.c}33` }}>
              {lnk.label}
            </a>
          ))}
        </div>

        {/* Stats */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 12, marginBottom: 20 }}>
          {[
            { label: "Total items", val: allRuns.length },
            { label: "Latest", val: latest ? fmt(latest.ts).split("(")[0].trim() : "\u2014", sub: latest ? fmt(latest.ts).match(/\(.*\)/)?.[0] : "", small: true },
            { label: "Matrix runs", val: matrixRuns.length, sub: matrixRuns.length ? `${Math.max(...matrixRuns.map(r => r.decks || 0))}d max` : "" },
            { label: "Traces/Bo3s", val: (typeCounts.trace || 0) + (typeCounts.bo3 || 0) },
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
          <div>
            {/* Filter chips */}
            <div style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap" }}>
              {FILTER_TYPES.map(ft => {
                const cnt = ft.k === "all" ? allRuns.length :
                  ft.k === "bo3" ? (typeCounts.bo3 || 0) : (typeCounts[ft.k] || 0);
                if (cnt === 0 && ft.k !== "all") return null;
                return (
                  <button key={ft.k} onClick={() => setHistFilter(ft.k)} style={{
                    padding: "4px 12px", borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: "pointer",
                    border: `1.5px solid ${histFilter === ft.k ? accent : border}`,
                    background: histFilter === ft.k ? accentLight : "#fff",
                    color: histFilter === ft.k ? accentText : muted,
                  }}>
                    {ft.l} ({cnt})
                  </button>
                );
              })}
            </div>

            <div style={S.card}>
              <div style={{ ...S.histRow(-1, false), color: muted, fontSize: 10, fontWeight: 700, letterSpacing: .5, background: "transparent" }}>
                <span>DESCRIPTION</span><span>TYPE</span><span>DATE</span>
              </div>
              {filteredRuns.map((r, i) => {
                const tc = TAG_COLORS[r.tag] || TAG_COLORS.matrix;
                const clickable = isClickable(r);
                const row = (
                  <div key={r.file + i} style={{
                    ...S.histRow(i, clickable),
                    ...(clickable ? { ":hover": { background: accentLight } } : {}),
                  }}>
                    <div>
                      <span style={{ ...S.mono, color: clickable ? blue : text }}>{rowDesc(r)}</span>
                      {rowExtra(r) && <span style={{ fontSize: 10, color: muted, marginLeft: 8 }}>{rowExtra(r)}</span>}
                    </div>
                    <Tag t={r.tag} color={tc.c} bg={tc.bg} />
                    <span style={{ fontSize: 12, color: i === 0 ? amber : muted }}>{fmt(r.ts)}</span>
                  </div>
                );
                return clickable ? (
                  <a key={r.file + i} href={fileLink(basePath, r)} target="_blank" rel="noreferrer" style={{ textDecoration: "none", color: "inherit" }}>
                    {row}
                  </a>
                ) : row;
              })}
              {filteredRuns.length === 0 && (
                <div style={{ padding: 20, textAlign: "center", color: muted, fontSize: 13 }}>No {histFilter} entries found</div>
              )}
            </div>
          </div>
        )}

        {/* ── New Run ── */}
        {tab === "newrun" && (
          <div>
            <div style={S.card}>
              <span style={S.label}>Run Type</span>
              <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
                {[
                  { k: "matrix", l: "Full Matrix" }, { k: "matchup", l: "Head-to-Head" },
                  { k: "bo3", l: "Single Bo3" }, { k: "field", l: "Field Sweep" },
                ].map(t => <Chip key={t.k} active={runType === t.k} onClick={() => setRunType(t.k)}>{t.l}</Chip>)}
              </div>

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
              {allRuns.filter(r => r.path?.endsWith(".html")).map(r => {
                const tc = TAG_COLORS[r.tag] || TAG_COLORS.report;
                return (
                  <a key={r.file} href={fileLink(basePath, r)} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderRadius: 8, background: bg, marginTop: 6, cursor: "pointer" }}>
                      <div style={{ width: 8, height: 8, borderRadius: "50%", background: tc.c }} />
                      <span style={{ ...S.mono, flex: 1, color: blue }}>{r.file}</span>
                      <Tag t={r.tag} color={tc.c} bg={tc.bg} />
                      {r.size_kb && <span style={{ fontSize: 10, color: muted }}>{r.size_kb}KB</span>}
                    </div>
                  </a>
                );
              })}
            </div>
            <div style={S.card}>
              <span style={S.label}>Data & Log Files</span>
              {allRuns.filter(r => r.path && !r.path.endsWith(".html")).map(r => {
                const tc = TAG_COLORS[r.tag] || TAG_COLORS.matrix;
                return (
                  <a key={r.file} href={fileLink(basePath, r)} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderRadius: 8, background: bg, marginTop: 6, cursor: "pointer" }}>
                      <span style={{ ...S.mono, flex: 1, color: blue }}>{r.file}</span>
                      <Tag t={r.tag} color={tc.c} bg={tc.bg} />
                      <span style={{ fontSize: 11, color: muted }}>{rowDesc(r)}</span>
                    </div>
                  </a>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
