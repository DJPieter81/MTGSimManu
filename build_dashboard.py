#!/usr/bin/env python3
"""
build_dashboard.py — MTGSimManu metagame matrix dashboard builder.
Usage: python build_dashboard.py [output_path]

Reads D from metagame_data.jsx, embeds into standalone HTML dashboard.
Output defaults to /mnt/user-data/outputs/modern_meta_matrix_full.html
"""
import re, json, sys, os

# ── Step 1: Load D from canonical JSX ────────────────────────
def load_D(jsx_path='metagame_data.jsx'):
    with open(jsx_path) as f:
        src = f.read()
    m = re.search(r'const D = (\{.*?\});\nconst N', src, re.DOTALL)
    if not m:
        raise ValueError(f"Could not find D object in {jsx_path}")
    return json.loads(m.group(1))

# ── Step 2: Archetype map ─────────────────────────────────────
ARCH = {
    '4/5c Control': 'Control', '4c Omnath': 'Midrange', 'Affinity': 'Aggro',
    'Amulet Titan': 'Combo', 'Boros Energy': 'Aggro', 'Dimir Midrange': 'Midrange',
    'Domain Zoo': 'Aggro', 'Eldrazi Tron': 'Ramp', "Goryo's Vengeance": 'Combo',
    'Izzet Prowess': 'Aggro', 'Jeskai Blink': 'Midrange', 'Living End': 'Combo',
    'Ruby Storm': 'Combo', 'Azorius Control': 'Control',
    'Pinnacle Affinity': 'Aggro',
    'Kappa Cannoneer': 'Aggro',
    # Add new decks here
}

# ── Step 3: HTML template ─────────────────────────────────────
HEAD = '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width,initial-scale=1.0">\n<title>Modern Metagame Matrix — April 2026</title>\n<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">\n<style>\n*{margin:0;padding:0;box-sizing:border-box}\n:root{--bg:#0c0e14;--bg2:#12151e;--bg3:#181c28;--bg4:#1e2333;--tx:#c8cdd8;--tx2:#8891a4;--tx3:#5a6270;\n--acc:#60a5fa;--grn:#4ade80;--red:#f87171;--orn:#d97706;--pur:#a78bfa;--cyn:#22d3ee;--gold:#c9a227}\nbody{background:var(--bg);color:var(--tx);font-family:\'Outfit\',sans-serif;font-size:13px;overflow-x:hidden}\n.wrap{display:flex;height:100vh}\n.main{flex:1;overflow:auto;padding:16px 20px;min-width:0}\n/* SLIDE-IN DETAIL PANEL */\n.det{width:420px;min-width:420px;background:var(--bg2);border-left:1px solid #ffffff0a;overflow-y:auto;\ntransform:translateX(100%);transition:transform .25s ease}\n.det.show{transform:translateX(0)}\n.det-inner{padding:20px}\n/* HEADER */\nh1{font-size:20px;font-weight:700;letter-spacing:-.5px;margin-bottom:2px;color:var(--gold)}\n.sub-h{color:var(--tx2);font-size:11px;margin-bottom:14px}\n/* CONTROLS */\n.ctrls{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center}\n.ctrls label{font-size:10px;color:var(--tx3);text-transform:uppercase;letter-spacing:.5px}\n.ctrls select,.ctrls input[type=text]{background:var(--bg3);border:1px solid #ffffff10;color:var(--tx);\npadding:5px 8px;border-radius:4px;font-size:11px;font-family:\'JetBrains Mono\',monospace;outline:none}\n.ctrls select:focus,.ctrls input:focus{border-color:var(--acc)}\n.toggle-wrap{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--tx2);cursor:pointer}\n.toggle-wrap input{accent-color:var(--acc)}\n/* TIER CHIPS */\n.tiers{margin-bottom:14px}\n.tier-label{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin:6px 0 4px;\npadding:2px 6px;border-radius:2px;display:inline-block}\n.tier-label.t1{color:#4ade80;background:#4ade8015}\n.tier-label.t2{color:#60a5fa;background:#60a5fa15}\n.tier-label.t3{color:#d97706;background:#d9770615}\n.tier-label.t4{color:#f87171;background:#f8717115}\n.tier-row{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:2px}\n.rchip{font-family:\'JetBrains Mono\',monospace;font-size:10px;padding:3px 8px;border-radius:3px;\ncursor:pointer;transition:all .15s;white-space:nowrap;border:1px solid transparent}\n.rchip:hover{border-color:#ffffff30;transform:translateY(-1px)}\n.rchip.sel{border-color:var(--acc);box-shadow:0 0 8px #60a5fa30}\n/* LEGEND */\n.legend{display:flex;align-items:center;gap:8px;margin-bottom:12px;font-size:10px;color:var(--tx3)}\n.legend-bar{height:10px;width:160px;border-radius:3px;\nbackground:linear-gradient(90deg,hsl(0,55%,20%),hsl(24,45%,25%),hsl(60,35%,24%),hsl(100,45%,24%),hsl(140,50%,26%))}\n/* MATRIX */\ntable{border-collapse:collapse;font-family:\'JetBrains Mono\',monospace;font-size:10px}\nth,td{padding:0;text-align:center}\nth.ch{height:90px;position:relative;min-width:26px}\nth.ch div{position:absolute;bottom:4px;left:50%;transform:rotate(-55deg) translateX(-50%);\ntransform-origin:bottom left;white-space:nowrap;font-size:8px;color:var(--tx2);font-weight:400;cursor:pointer}\nth.ch.hl div{color:var(--acc);font-weight:600}\nth.rh{text-align:right;padding-right:6px;font-size:8px;color:var(--tx2);font-weight:400;\nwhite-space:nowrap;cursor:pointer;position:sticky;left:0;background:var(--bg);z-index:1}\nth.rh.hl{color:var(--acc);font-weight:600}\ntd.c{width:26px;height:20px;font-size:9px;font-weight:600;cursor:pointer;transition:opacity .1s;position:relative}\ntd.c:hover{outline:2px solid #fff;outline-offset:-2px;z-index:2}\ntd.c.sel{outline:2px solid var(--acc);outline-offset:-2px;z-index:3}\ntd.mir{background:var(--bg3)!important;color:var(--tx3);cursor:default;font-size:8px}\ntd.avg{font-size:9px;font-weight:700;padding-left:6px;background:var(--bg);position:sticky;right:0;white-space:nowrap}\n/* TOOLTIP */\n.ttip{position:fixed;background:#1a1e2e;border:1px solid #ffffff20;border-radius:6px;padding:8px 12px;\npointer-events:none;z-index:100;font-size:11px;box-shadow:0 8px 24px rgba(0,0,0,.5);\nopacity:0;transition:opacity .12s;max-width:280px}\n.ttip.vis{opacity:1}\n.ttip .tw{font-size:20px;font-weight:700;margin-bottom:2px;font-family:\'JetBrains Mono\',monospace}\n.ttip .tl{color:var(--tx2);font-size:10px;line-height:1.5}\n/* DETAIL PANEL SHARED */\n.close-btn{position:sticky;top:0;z-index:10;background:var(--bg2);padding:10px 16px;\ndisplay:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #ffffff08}\n.close-btn .det-label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--tx3)}\n.close-btn button{background:none;border:1px solid #ffffff15;color:var(--tx2);width:26px;height:26px;\nborder-radius:4px;cursor:pointer;font-size:14px}\n.close-btn button:hover{background:var(--bg4);color:var(--tx)}\n.det h2{font-size:16px;font-weight:700;letter-spacing:-.3px;margin-bottom:4px}\n.det .sub{color:var(--tx2);font-size:11px;margin-bottom:10px}\n.sec{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--tx3);\nmargin:16px 0 7px;padding-bottom:4px;border-bottom:1px solid #ffffff08}\n/* STATS ROW */\n.row{display:flex;justify-content:space-between;padding:3px 0;font-size:12px}\n.lbl{color:var(--tx2)}.val{font-family:\'JetBrains Mono\',monospace;font-weight:600}\n/* MATCHUP BARS IN DETAIL */\n.mu-row{display:flex;align-items:center;gap:5px;padding:2px 0;cursor:pointer}\n.mu-row:hover{background:#ffffff05;border-radius:2px}\n.mu-name{width:110px;font-size:9px;color:var(--tx2);text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\n.mu-bar{flex:1;height:8px;background:var(--bg3);border-radius:2px;overflow:hidden;max-width:110px}\n.mu-fill{height:100%;border-radius:2px}\n.mu-val{font-size:10px;font-weight:600;width:32px;text-align:right;font-family:\'JetBrains Mono\',monospace}\n.mu-ms{font-size:9px;color:var(--tx3);width:28px;text-align:right}\n/* TIER BADGE */\n.tier-badge{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;\nborder-radius:4px;font-size:12px;font-weight:700;margin-right:8px;flex-shrink:0}\n/* PILLS */\n.pill{display:inline-block;padding:2px 7px;border-radius:10px;font-size:9px;font-weight:600;\nmargin:0 3px 3px 0;font-family:\'JetBrains Mono\',monospace}\n.pill-cast{background:#6a8dae18;color:#6a8dae;border:1px solid #6a8dae25}\n.pill-dmg{background:#a0404018;color:#f87171;border:1px solid #f8717125}\n.pill-fin{background:#c9a22718;color:#c9a227;border:1px solid #c9a22725}\n/* FINISHER ROWS */\n.fin-row{display:flex;gap:8px;align-items:flex-start;padding:4px 0;border-bottom:1px solid #ffffff06}\n.fin-count{font-size:14px;font-weight:700;color:var(--gold);min-width:24px;text-align:right;font-family:\'JetBrains Mono\',monospace}\n.fin-card{font-size:11px;font-weight:600;color:var(--tx)}\n.fin-desc{font-size:10px;color:var(--tx3);line-height:1.5;margin-top:1px}\n/* SIDEBOARD */\n.sb-block{background:var(--bg3);border-radius:4px;padding:8px 10px;margin-bottom:6px}\n.sb-head{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}\n.sb-in{color:var(--grn)}.sb-out{color:var(--red)}.sb-cast{color:var(--tx3)}.sb-delta-up{color:var(--grn)}.sb-delta-dn{color:var(--red)}\n.sb-line{font-size:10px;margin-bottom:4px;line-height:1.5}\n/* NARRATIVE */\n.insight-box{background:var(--bg3);border-radius:4px;padding:10px 12px;border-left:3px solid #c9a22740;margin-bottom:8px}\n.insight-box p{font-size:11px;color:var(--tx2);line-height:1.65}\n/* OVERVIEW SUMMARY */\n.overview{background:var(--bg3);border-radius:4px;padding:10px 12px;border-left:3px solid #c9a22740;margin-bottom:8px;font-size:11px;color:var(--tx2);line-height:1.65}\n/* STATS GRID */\n.stats-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px}\n.stat-box{background:var(--bg3);border-radius:4px;padding:8px 10px;text-align:center}\n.stat-box .sv{font-size:18px;font-weight:700;font-family:\'JetBrains Mono\',monospace;margin-bottom:1px}\n.stat-box .sl{font-size:9px;color:var(--tx3);text-transform:uppercase;letter-spacing:.5px}\n</style>\n</head>\n<body>\n<div class="ttip" id="ttip"></div>\n<div class="wrap">\n  <div class="main" id="main">\n    <h1>⚔ MODERN METAGAME MATRIX</h1>\n    <div class="sub-h">15 decks · 500 Bo3/pair · 52,500 total games · Post-PR#85 · Apr 12 2026 · click cells or deck names</div>\n    <div class="ctrls">\n      <div><label>Sort</label><br>\n        <select id="srt">\n          <option value="wd">↓ Weighted WR</option>\n          <option value="fd">↓ Flat WR</option>\n          <option value="az">A → Z</option>\n        </select>\n      </div>\n      <div><label>Arch</label><br>\n        <select id="archFlt">\n          <option value="">All archetypes</option>\n          <option value="Aggro">Aggro</option>\n          <option value="Midrange">Midrange</option>\n          <option value="Control">Control</option>\n          <option value="Combo">Combo</option>\n          <option value="Ramp">Ramp</option>\n        </select>\n      </div>\n      <div><label>Highlight</label><br>\n        <select id="hl">\n          <option value="">None</option>\n        </select>\n      </div>\n      <div style="align-self:flex-end;padding-bottom:6px">\n        <label class="toggle-wrap"><input type="checkbox" id="wtToggle" checked> Weighted WR</label>\n      </div>\n    </div>\n    <div class="legend">\n      <div class="legend-bar"></div>\n      <span>0% → 50% → 100%</span>\n    </div>\n    <div class="tiers" id="tiers"></div>\n    <div id="matrix-wrap"><table id="matrix"></table></div>\n  </div>\n\n  <div class="det" id="det">\n    <div class="close-btn">\n      <span class="det-label" id="det-label">Detail</span>\n      <button onclick="closeDet()">✕</button>\n    </div>\n    <div class="det-inner" id="det-inner">\n      <div style="color:var(--tx3);font-size:12px;padding:20px 0">Click a deck name or matrix cell to view details.</div>\n    </div>\n  </div>\n</div>\n\n'

ENGINE = 'const N = D.matches_per_pair;\n\n// Build lookup maps\nconst decks = D.decks;\nconst wins = D.wins; // wins[i][j] = wins for deck i vs deck j\nconst MC = D.matchup_cards; // keyed "i,j" where i < j\nconst DC = {}; D.deck_cards.forEach(d => DC[d.idx] = d);\nconst OV = {}; D.overall.forEach(o => OV[o.idx] = o);\nconst MS = D.meta_shares;\n\n// Flat and weighted WR\nconst A = {}, W = {};\ndecks.forEach((d, i) => {\n  const o = OV[i];\n  if (o) { A[i] = o.win_rate; W[i] = o.weighted_wr || o.win_rate; }\n});\n\nlet sorted = [...decks.keys()];\nlet selCell = null, selDeck = null, hlDeck = null, archFilt = \'\', useWeighted = true, filt = \'\';\n\n// Populate highlight dropdown\nconst hlSel = document.getElementById(\'hl\');\ndecks.forEach((d, i) => { const opt = document.createElement(\'option\'); opt.value = i; opt.textContent = d; hlSel.appendChild(opt); });\n\nfunction getWR(i) { return useWeighted ? (W[i] || A[i]) : A[i]; }\nfunction getCT() { return useWeighted ? [65, 48, 33] : [65, 50, 35]; }\nfunction tierOf(w) { const c = getCT(); return w >= c[0] ? [\'S\', \'#4ade80\'] : w >= c[1] ? [\'A\', \'#60a5fa\'] : w >= c[2] ? [\'B\', \'#d97706\'] : [\'C\', \'#f87171\']; }\nfunction tierTag(w) { const c = getCT(); return w >= c[0] ? \'T1\' : w >= c[1] ? \'T2\' : w >= c[2] ? \'T3\' : \'T4\'; }\nfunction wc(w) {\n  if (w <= 25) return `hsl(0,55%,${18+w*.3}%)`;\n  if (w <= 40) return `hsl(${(w-25)*1.6},45%,25%)`;\n  if (w <= 50) return `hsl(${24+(w-40)*3.6},35%,24%)`;\n  if (w <= 65) return `hsl(${60+(w-50)*5.3},45%,24%)`;\n  return `hsl(140,50%,${24+(w-65)*.4}%)`;\n}\nfunction tc(w) { return w < 30 ? \'#f87171\' : w > 65 ? \'#4ade80\' : \'#e2e8f0\'; }\nfunction muc(w) { return w >= 70 ? \'#4ade80\' : w >= 55 ? \'#60a5fa\' : w >= 45 ? \'#d97706\' : w >= 30 ? \'#f87171\' : \'#ef4444\'; }\nfunction wr(i, j) { return wins[i][j] / N * 100; }\n\nfunction closeDet() { document.getElementById(\'det\').classList.remove(\'show\'); selCell = null; selDeck = null; render(); }\n\nfunction sort() {\n  const m = document.getElementById(\'srt\').value;\n  if (m === \'az\') sorted = [...decks.keys()].sort((a,b) => decks[a].localeCompare(decks[b]));\n  else if (m === \'fd\') sorted = [...decks.keys()].sort((a,b) => A[b]-A[a]);\n  else sorted = [...decks.keys()].sort((a,b) => getWR(b)-getWR(a));\n}\n\nfunction pills(cards, cls) {\n  if (!cards || !cards.length) return \'\';\n  return cards.map(c => `<span class="pill ${cls}">${c.card} ×${c.count}</span>`).join(\'\');\n}\n\nfunction sbLines(lines) {\n  if (!lines || !lines.length) return \'<div style="font-size:10px;color:var(--tx3)">No changes</div>\';\n  return lines.map(l => {\n    if (l.startsWith(\'IN:\')) return `<div class="sb-line sb-in">▲ ${l}</div>`;\n    if (l.startsWith(\'OUT:\')) return `<div class="sb-line sb-out">▼ ${l}</div>`;\n    if (l.startsWith(\'SB cards seen:\')) return `<div class="sb-line sb-cast">✓ ${l}</div>`;\n    if (l.includes(\'improves\')) return `<div class="sb-line sb-delta-up">↑ ${l}</div>`;\n    if (l.includes(\'drops\')) return `<div class="sb-line sb-delta-dn">↓ ${l}</div>`;\n    return `<div class="sb-line" style="color:var(--tx2)">${l}</div>`;\n  }).join(\'\');\n}\n\nfunction getMC(i, j) {\n  const ki = Math.min(i,j), kj = Math.max(i,j);\n  const p = MC[`${ki},${kj}`];\n  if (!p) return null;\n  if (i <= j) return p;\n  // Flip perspective\n  return {\n    ...p, d1: p.d2, d2: p.d1,\n    d1_wins: p.d2_wins, d2_wins: p.d1_wins,\n    sweeps: [p.sweeps[1], p.sweeps[0]],\n    went_to_3: p.went_to_3,\n    g1_wins: [p.g1_wins[1], p.g1_wins[0]],\n    comebacks: [p.comebacks[1], p.comebacks[0]],\n    d1_top_casts: p.d2_top_casts, d2_top_casts: p.d1_top_casts,\n    d1_top_damage: p.d2_top_damage, d2_top_damage: p.d1_top_damage,\n    d1_finishers: p.d2_finishers, d2_finishers: p.d1_finishers,\n    d1_sb: p.d2_sb, d2_sb: p.d1_sb,\n    insight: p.insight\n  };\n}\n\n// TOOLTIP\nconst ttip = document.getElementById(\'ttip\');\nfunction showTip(e, i, j) {\n  const w = wr(i,j), rw = wr(j,i);\n  ttip.innerHTML = `<div class="tw" style="color:${tc(w)}">${w.toFixed(0)}%</div>\n<div class="tl"><b>${decks[i]}</b> (${ARCH[decks[i]]||\'?\'}) vs <b>${decks[j]}</b> (${ARCH[decks[j]]||\'?\'})</div>\n<div class="tl" style="margin-top:3px">Reverse: <b style="color:${tc(rw)}">${rw.toFixed(0)}%</b> · Check: ${(w+rw).toFixed(0)}%</div>`;\n  ttip.classList.add(\'vis\');\n  ttip.style.left = Math.min(e.clientX+14, innerWidth-290) + \'px\';\n  ttip.style.top = Math.min(e.clientY+14, innerHeight-90) + \'px\';\n}\nfunction hideTip() { ttip.classList.remove(\'vis\'); }\ndocument.addEventListener(\'mousemove\', e => {\n  if (ttip.classList.contains(\'vis\')) {\n    ttip.style.left = Math.min(e.clientX+14, innerWidth-290) + \'px\';\n    ttip.style.top = Math.min(e.clientY+14, innerHeight-90) + \'px\';\n  }\n});\n\nfunction showDeckProfile(idx) {\n  selDeck = idx; selCell = null;\n  const det = document.getElementById(\'det\'); det.classList.add(\'show\');\n  document.getElementById(\'det-label\').textContent = \'Deck Profile\';\n  const flat = A[idx], wt = W[idx] || flat;\n  const tl = tierOf(getWR(idx));\n  const dc = DC[idx] || {};\n  const ov = OV[idx] || {};\n  const delta = (wt - flat).toFixed(1);\n\n  let h = `<div style="display:flex;align-items:center;margin-bottom:8px">\n<span class="tier-badge" style="background:${tl[1]}20;color:${tl[1]};border:1px solid ${tl[1]}40">${tl[0]}</span>\n<h2>${decks[idx]}</h2></div>`;\n  h += `<div class="sub">${ARCH[decks[idx]]||\'?\'} · ${MS[decks[idx]]||0}% meta share</div>`;\n  h += `<div style="display:flex;gap:16px;margin:10px 0 14px;align-items:flex-end">\n<div><div style="font-size:9px;color:var(--tx3);text-transform:uppercase;letter-spacing:.5px">Flat Avg</div>\n<div style="font-size:26px;font-weight:700;color:${tc(flat)};font-family:\'JetBrains Mono\'">${flat.toFixed(1)}%</div></div>\n<div><div style="font-size:9px;color:var(--tx3);text-transform:uppercase;letter-spacing:.5px">⚖ Weighted</div>\n<div style="font-size:26px;font-weight:700;color:${tc(wt)};font-family:\'JetBrains Mono\'">${wt.toFixed(1)}%</div></div>\n<div style="padding-bottom:6px"><span style="font-size:12px;color:${wt<flat?\'var(--red)\':\'var(--grn)\'};font-family:\'JetBrains Mono\'">${delta>0?\'+\':\'\'}${delta}pp</span></div>\n</div>`;\n\n  if (dc.summary) h += `<div class="overview">${dc.summary}</div>`;\n\n  if (dc.mvp_casts && dc.mvp_casts.length) {\n    h += `<div class="sec">MVP Cards (Most Cast)</div>`;\n    h += dc.mvp_casts.map(c => `<span class="pill pill-cast">${c.card} ×${c.count}</span>`).join(\'\');\n  }\n  if (dc.mvp_damage && dc.mvp_damage.length) {\n    h += `<div style="margin-top:6px">`;\n    h += dc.mvp_damage.map(c => `<span class="pill pill-dmg">${c.card} ×${c.count}</span>`).join(\'\');\n    h += `</div>`;\n  }\n  if (dc.finishers && dc.finishers.length) {\n    h += `<div class="sec">How It Wins</div>`;\n    h += dc.finishers.map(f => `<div class="fin-row">\n<div class="fin-count">${f.count}</div>\n<div><div class="fin-card">${f.card}</div>${f.desc?`<div class="fin-desc">${f.desc}</div>`:\'\'}</div>\n</div>`).join(\'\');\n  }\n\n  h += `<div class="sec">All Matchups (by opponent tier)</div>`;\n  const byTier = decks.map((_, j) => j).filter(j => j !== idx).sort((a,b) => getWR(b)-getWR(a));\n  let curTier = \'\';\n  byTier.forEach(j => {\n    const oppW = getWR(j);\n    const t = tierTag(oppW);\n    const myW = wr(idx, j);\n    const c = muc(myW);\n    const tc2 = {\'T1\':\'#4ade80\',\'T2\':\'#60a5fa\',\'T3\':\'#d97706\',\'T4\':\'#f87171\'}[t];\n    if (t !== curTier) {\n      curTier = t;\n      h += `<div style="font-size:9px;font-weight:700;color:${tc2};letter-spacing:.8px;margin:10px 0 4px;padding:2px 0;border-bottom:1px solid ${tc2}20">${t}</div>`;\n    }\n    h += `<div class="mu-row" onclick="showMatchup(${idx},${j})">\n<span class="mu-name">${decks[j]}</span>\n<div class="mu-bar"><div class="mu-fill" style="width:${myW}%;background:${c}"></div></div>\n<span class="mu-val" style="color:${c}">${myW.toFixed(0)}%</span>\n<span class="mu-ms">${(MS[decks[j]]||0)}%</span>\n</div>`;\n  });\n\n  document.getElementById(\'det-inner\').innerHTML = h;\n  render();\n}\n\nfunction showMatchup(i, j) {\n  selCell = [i,j]; selDeck = null;\n  const det = document.getElementById(\'det\'); det.classList.add(\'show\');\n  document.getElementById(\'det-label\').textContent = \'Matchup Detail\';\n  const p = getMC(i, j);\n  const w = wr(i,j), rw = wr(j,i);\n\n  let h = `<h2>${decks[i]} <span style="color:var(--tx3)">vs</span> ${decks[j]}</h2>`;\n  h += `<div class="sub">${ARCH[decks[i]]||\'?\'} vs ${ARCH[decks[j]]||\'?\'} · ${N} Bo3</div>`;\n  h += `<div style="font-size:36px;font-weight:700;color:${tc(w)};font-family:\'JetBrains Mono\';margin:6px 0 10px">${w.toFixed(0)}%</div>`;\n  h += `<div class="row"><span class="lbl">Reverse (${decks[j]} wins)</span><span class="val" style="color:${tc(rw)}">${rw.toFixed(0)}%</span></div>`;\n  h += `<div class="row"><span class="lbl">Symmetry check</span><span class="val">${(w+rw).toFixed(0)}%</span></div>`;\n\n  if (p) {\n    if (p.insight) h += `<div class="insight-box" style="margin-top:10px"><p>${p.insight}</p></div>`;\n\n    h += `<div class="stats-grid">\n<div class="stat-box"><div class="sv" style="color:var(--acc)">${p.avg_turns}</div><div class="sl">Avg Turns</div></div>\n<div class="stat-box"><div class="sv" style="color:var(--pur)">${p.went_to_3}%</div><div class="sl">Went to G3</div></div>\n<div class="stat-box"><div class="sv" style="color:var(--grn)">${p.g1_wins[0]}%</div><div class="sl">${decks[i].split(\' \')[0]} G1 Win%</div></div>\n<div class="stat-box"><div class="sv" style="color:var(--orn)">${p.comebacks[0]}-${p.comebacks[1]}</div><div class="sl">Comebacks</div></div>\n</div>`;\n\n    if (p.d1_finishers && p.d1_finishers.length) {\n      h += `<div class="sec">How ${decks[i]} Wins</div>`;\n      h += p.d1_finishers.map(f => `<div class="fin-row"><div class="fin-count">${f.count}</div><div><div class="fin-card">${f.card}</div>${f.desc?`<div class="fin-desc">${f.desc}</div>`:\'\'}</div></div>`).join(\'\');\n    }\n    if (p.d1_top_casts && p.d1_top_casts.length) {\n      h += `<div class="sec">Key Cards — ${decks[i]}</div>`;\n      h += pills(p.d1_top_casts, \'pill-cast\');\n      if (p.d1_top_damage && p.d1_top_damage.length) h += \'<br>\' + pills(p.d1_top_damage, \'pill-dmg\');\n    }\n    if (p.d2_top_casts && p.d2_top_casts.length) {\n      h += `<div class="sec">Key Cards — ${decks[j]}</div>`;\n      h += pills(p.d2_top_casts, \'pill-cast\');\n      if (p.d2_top_damage && p.d2_top_damage.length) h += \'<br>\' + pills(p.d2_top_damage, \'pill-dmg\');\n    }\n    if (p.d2_finishers && p.d2_finishers.length) {\n      h += `<div class="sec">How ${decks[j]} Wins</div>`;\n      h += p.d2_finishers.map(f => `<div class="fin-row"><div class="fin-count">${f.count}</div><div><div class="fin-card">${f.card}</div></div></div>`).join(\'\');\n    }\n\n    // Sideboard\n    const hasSb = (p.d1_sb && p.d1_sb.length) || (p.d2_sb && p.d2_sb.length);\n    if (hasSb) {\n      h += `<div class="sec">Sideboard Guide (from game data)</div>`;\n      h += `<div class="sb-block"><div class="sb-head sb-in">${decks[i]}</div>${sbLines(p.d1_sb)}</div>`;\n      h += `<div class="sb-block"><div class="sb-head sb-out">${decks[j]}</div>${sbLines(p.d2_sb)}</div>`;\n    }\n  } else {\n    h += `<div style="margin-top:12px;padding:10px;background:var(--bg3);border-radius:4px;font-size:11px;color:var(--tx3)">Card-level data pending verbose run for this matchup.</div>`;\n  }\n\n  document.getElementById(\'det-inner\').innerHTML = h;\n  render();\n}\n\nfunction render() {\n  sort();\n  let fd = sorted;\n  if (archFilt) fd = fd.filter(i => ARCH[decks[i]] === archFilt);\n\n  // Tier chips\n  const rk = [...decks.keys()].sort((a,b) => getWR(b)-getWR(a));\n  const ct = getCT();\n  const tiers = {t1:[], t2:[], t3:[], t4:[]};\n  rk.forEach(i => {\n    const w = getWR(i);\n    if (w >= ct[0]) tiers.t1.push(i); else if (w >= ct[1]) tiers.t2.push(i);\n    else if (w >= ct[2]) tiers.t3.push(i); else tiers.t4.push(i);\n  });\n  const tl = useWeighted\n    ? [[\'t1\',`Tier 1 — Dominant (≥${ct[0]}%)`],[\'t2\',`Tier 2 — Competitive (≥${ct[1]}%)`],[\'t3\',`Tier 3 — Fringe (≥${ct[2]}%)`],[\'t4\',\'Tier 4 — Struggling\']]\n    : [[\'t1\',`Tier 1 (≥${ct[0]}%)`],[\'t2\',`Tier 2 (≥${ct[1]}%)`],[\'t3\',`Tier 3 (≥${ct[2]}%)`],[\'t4\',\'Tier 4\']];\n  let th = \'\';\n  tl.forEach(([cls, label]) => {\n    if (!tiers[cls].length) return;\n    th += `<div class="tier-label ${cls}">${label}</div><div class="tier-row">`;\n    tiers[cls].forEach(i => {\n      const w = getWR(i);\n      const isSel = selDeck === i;\n      const bg = tierOf(w)[1];\n      th += `<div class="rchip ${isSel?\'sel\':\'\'}" style="background:${bg}18;color:${bg}"\nonclick="showDeckProfile(${i})">${decks[i]} <span style="font-size:9px;opacity:.7">${w.toFixed(1)}%</span></div>`;\n    });\n    th += \'</div>\';\n  });\n  document.getElementById(\'tiers\').innerHTML = th;\n\n  // Matrix\n  const cols = fd;\n  let mt = \'<thead><tr><th class="rh" style="min-width:120px"></th>\';\n  cols.forEach(j => {\n    const isHL = hlDeck !== null && hlDeck == j;\n    mt += `<th class="ch${isHL?\' hl\':\'\'}" onclick="showDeckProfile(${j})"><div>${decks[j]}</div></th>`;\n  });\n  mt += `<th style="font-size:8px;color:var(--tx3);padding-left:6px;text-align:left">${useWeighted?\'⚖ WR\':\'Flat\'}</th></tr></thead><tbody>`;\n\n  fd.forEach(i => {\n    const isHL = hlDeck !== null && hlDeck == i;\n    mt += `<tr><th class="rh${isHL?\' hl\':\'\'}" onclick="showDeckProfile(${i})">${decks[i]}</th>`;\n    cols.forEach(j => {\n      if (i === j) { mt += `<td class="c mir">—</td>`; return; }\n      const w = wr(i,j);\n      const isSel = selCell && ((selCell[0]===i&&selCell[1]===j)||(selCell[0]===j&&selCell[1]===i));\n      const isDimmed = (selDeck!==null && selDeck!==i && selDeck!==j) || (hlDeck!==null && hlDeck!=i && hlDeck!=j);\n      mt += `<td class="c${isSel?\' sel\':\'\'}" style="background:${wc(w)};opacity:${isDimmed?.3:1}"\nonmouseenter="showTip(event,${i},${j})" onmouseleave="hideTip()"\nonclick="showMatchup(${i},${j})">${w.toFixed(0)}</td>`;\n    });\n    const wr_val = getWR(i);\n    const tcolor = tierOf(wr_val)[1];\n    mt += `<td class="avg" style="color:${tcolor}">${wr_val.toFixed(1)}%</td></tr>`;\n  });\n\n  mt += \'</tbody>\';\n  document.getElementById(\'matrix\').innerHTML = mt;\n}\n\ndocument.getElementById(\'srt\').onchange = render;\ndocument.getElementById(\'archFlt\').onchange = e => { archFilt = e.target.value; render(); };\ndocument.getElementById(\'hl\').onchange = e => { hlDeck = e.target.value || null; render(); };\ndocument.getElementById(\'wtToggle\').onchange = e => { useWeighted = e.target.checked; render(); };\n\nrender();\n</script>\n</body>\n</html>'

def build(jsx_path='metagame_data.jsx', out_path=None):
    if out_path is None:
        out_path = '/mnt/user-data/outputs/modern_meta_matrix_full.html'

    D = load_D(jsx_path)
    d_json = json.dumps(D, separators=(',', ':'))
    arch_json = json.dumps(ARCH, separators=(', ', ': '))

    html = (HEAD
            + '<script>\nconst D = ' + d_json + ';\n'
            + 'const ARCH = ' + arch_json + ';\n'
            + ENGINE)

    # Inject pro-level strategic insights function
    PRO_INSIGHTS = """
function proInsights(p, i, j) {
  if (!p || !p.avg_turns) return '';
  let ins = [];
  const w = wr(i,j), g1 = p.g1_wins[0], delta = Math.round(w - g1);
  const d1 = decks[i].split(' ')[0], d2 = decks[j].split(' ')[0];

  // G1 vs Match swing
  if (Math.abs(delta) >= 12) {
    if (delta > 0) ins.push('<b>'+d1+"'s SB dominates:</b> G1 WR is "+g1+'% but match WR is '+w.toFixed(0)+'% (+'+delta+"pp). "+p.comebacks[0]+' comebacks from behind.');
    else ins.push('<b>'+d2+' adapts better post-board:</b> '+d1+' has '+g1+'% G1 WR but drops to '+w.toFixed(0)+'% match ('+delta+"pp). Opponent's sideboard plan is more effective.");
  }

  // Sweep asymmetry
  var s1 = p.sweeps[0], s2 = p.sweeps[1], g3 = p.went_to_3;
  if (s1 + s2 > 3 && Math.abs(s1-s2) >= 3) {
    var sweeper = s1 > s2 ? d1 : d2;
    ins.push('<b>Polarized:</b> '+sweeper+' sweeps '+Math.max(s1,s2)+'x vs '+Math.min(s1,s2)+'. When '+sweeper+' wins, it is decisive. But '+g3+'% reach G3.');
  }

  // Speed gap between closers
  if (p.d1_finishers && p.d1_finishers.length && p.d2_finishers && p.d2_finishers.length) {
    var f1 = p.d1_finishers[0], f2 = p.d2_finishers[0];
    if (f1.desc && f2.desc) {
      var m1 = f1.desc.match(/T([0-9.]+)/), m2 = f2.desc.match(/T([0-9.]+)/);
      if (m1 && m2) {
        var t1 = parseFloat(m1[1]), t2 = parseFloat(m2[1]);
        if (Math.abs(t1-t2) >= 1.5) {
          var faster = t1 < t2 ? d1 : d2;
          var fCard = (t1 < t2 ? f1 : f2).card.split(',')[0];
          ins.push('<b>Speed gap:</b> '+faster+' closes with '+fCard+' (T'+Math.min(t1,t2).toFixed(1)+') vs T'+Math.max(t1,t2).toFixed(1)+'. '+Math.abs(t1-t2).toFixed(1)+' turns difference.');
        }
      }
    }
  }

  // Damage source blind spot
  if (w < 45 && p.d2_top_damage && p.d2_top_damage.length) {
    var killer = p.d2_top_damage[0];
    ins.push('<b>'+d1+"'s problem:</b> "+killer.card+' deals '+killer.count+' total damage. Likely outside '+d1+"'s removal range.");
  } else if (w > 75 && p.d1_top_damage && p.d1_top_damage.length) {
    var killer2 = p.d1_top_damage[0];
    ins.push('<b>Why '+d1+' dominates:</b> '+killer2.card+' ('+killer2.count+' dmg) goes unanswered by '+d2+'.');
  }

  // Zero comebacks
  if (p.comebacks[0] === 0 && p.comebacks[1] === 0 && g3 >= 30) {
    ins.push('<b>No comebacks:</b> Despite '+g3+'% going to G3, neither side reverse-swept. G1 play/draw advantage is decisive.');
  }

  if (!ins.length) return '';
  return '<div style="margin:10px 0;padding:10px 14px;background:var(--bg2);border-left:3px solid var(--acc);border-radius:0 6px 6px 0">' +
    '<div style="font-size:9px;text-transform:uppercase;letter-spacing:.8px;color:var(--tx3);margin-bottom:6px;font-weight:700">Strategic Insights</div>' +
    ins.map(function(s){return '<div style="font-size:11px;color:var(--tx2);line-height:1.5;margin-bottom:6px">'+s+'</div>';}).join('') + '</div>';
}
"""
    # Insert proInsights function before showMatchup, and call it in showMatchup
    html = html.replace(
        'function showMatchup(i, j) {',
        PRO_INSIGHTS + '\nfunction showMatchup(i, j) {'
    )
    html = html.replace(
        "if (p.insight) h += `<div class=\"insight-box\"",
        "h += proInsights(p, i, j);\n    if (p.insight) h += `<div class=\"insight-box\""
    )

    # Provenance footer — injected before </body>. Captures the session's
    # sim parameters so anyone loading the HTML can trace its origin.
    html = html.replace('</body>', _provenance_footer(D) + '\n</body>')

    with open(out_path, 'w') as f:
        f.write(html)
    print(f"Built: {out_path} ({len(html):,} chars)")


def merge(results_path='metagame_results.json',
          jsx_path='metagame_data.jsx',
          out_html='/home/user/MTGSimManu/modern_meta_matrix_full.html'):
    """Merge a matrix run into metagame_data.jsx and rebuild the dashboard.

    Preserves all matchup_cards / deck_cards narrative detail; only updates
    the wins matrix, matches_per_pair, and overall WR rollups. Called
    automatically by `run_meta.py --matrix --save`.
    """
    import json as _json
    import re as _re

    # Load existing JSX to preserve narrative data
    with open(jsx_path) as f:
        content = f.read()
    D = load_D(jsx_path)

    # Load fresh results
    with open(results_path) as f:
        results = _json.load(f)

    if results.get('type') != 'matrix':
        print(f"merge: skipping — results.json is not a matrix run", file=sys.stderr)
        return

    n_games = results['n_games']
    decks = D['decks']
    idx = {name: i for i, name in enumerate(decks)}

    # Detect subset (--decks N) runs and refuse to merge — partial data would
    # zero out every matchup not in the subset and corrupt the dashboard.
    results_names = set(results.get('names') or [])
    if results_names and len(results_names) < len(decks):
        missing = [n for n in decks if n not in results_names]
        print(f"merge: skipping — results only cover {len(results_names)}/{len(decks)} "
              f"decks (missing {len(missing)}). Run the full matrix "
              f"(drop --decks N) to update the dashboard.",
              file=sys.stderr)
        return

    D['matches_per_pair'] = n_games

    # Build new wins matrix from the flat matrix dict keyed "d1|d2". Seed from
    # the existing matrix so any pair not in results keeps its last-known wins
    # (scaled to the new N). Full-matrix runs will overwrite every pair.
    old_wins = D.get('wins') or [[0] * len(decks) for _ in decks]
    old_N = D.get('matches_per_pair', n_games) or n_games
    new_wins = [[round((old_wins[i][j] if i < len(old_wins) and j < len(old_wins[i]) else 0)
                       * n_games / old_N) for j in range(len(decks))]
                for i in range(len(decks))]
    for key, pct in results['matrix'].items():
        if '|' not in key:
            continue
        d1, d2 = key.split('|', 1)
        if d1 not in idx or d2 not in idx:
            continue
        i, j = idx[d1], idx[d2]
        new_wins[i][j] = round(pct / 100.0 * n_games)
    D['wins'] = new_wins

    # Recompute overall WR rollups
    meta_shares = D.get('meta_shares', {})
    for entry in D.get('overall', []):
        i = entry['idx']
        total_wins = sum(new_wins[i][j] for j in range(len(decks)) if j != i)
        total_matches = n_games * (len(decks) - 1)
        entry['total_wins'] = total_wins
        entry['total_matches'] = total_matches
        entry['win_rate'] = round(total_wins / total_matches * 100, 1) if total_matches else 0.0
        # Weighted WR across T1+T2 opponents only (matches run_meta_matrix logic)
        weighted_sum = 0.0
        weight_total = 0.0
        for j, opp in enumerate(decks):
            if j == i:
                continue
            share = meta_shares.get(opp, 0)
            if share <= 0:
                continue
            matches = n_games
            opp_wr = new_wins[i][j] / matches * 100 if matches else 50
            weighted_sum += opp_wr * share
            weight_total += share
        entry['weighted_wr'] = round(weighted_sum / weight_total, 1) if weight_total else entry['win_rate']

    # Serialize back: replace the `const D = {...};` body only. Split on
    # the sentinel to avoid re.sub interpreting escapes (\u, \n, etc.) in
    # the JSON payload as regex backreferences.
    new_body = _json.dumps(D, separators=(',', ':'))
    head_end = content.index('const D = ')
    tail_start = content.index(';\nconst N', head_end)
    new_content = content[:head_end] + f'const D = {new_body}' + content[tail_start:]
    with open(jsx_path, 'w') as f:
        f.write(new_content)
    print(f"merge: updated {jsx_path} (N={n_games}, {len(decks)} decks)")

    # Rebuild the HTML
    build(jsx_path, out_html)


def _provenance_footer(D: dict) -> str:
    """Small footer block listing date, deck count, games/pair, engine SHA."""
    import datetime, subprocess
    try:
        sha = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'],
                                      stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        sha = 'unknown'
    date = datetime.date.today().isoformat()
    n_decks = len(D.get('decks', []))
    n_games = D.get('matches_per_pair', '?')
    seed_range = D.get('seed_range', '')
    return (
        f'<footer style="margin:24px 12px 18px;padding:10px 14px;'
        f'background:var(--bg2);border-top:1px solid #ffffff08;'
        f'font-family:\'JetBrains Mono\',monospace;font-size:10px;'
        f'color:var(--tx3);border-radius:4px">'
        f'Simulated: {date} · Decks: {n_decks} · Games/pair: {n_games}'
        f'{" · Seeds: " + seed_range if seed_range else ""}'
        f' · Engine: {sha}'
        f'</footer>'
    )


if __name__ == '__main__':
    jsx = sys.argv[1] if len(sys.argv) > 1 else 'metagame_data.jsx'
    out = sys.argv[2] if len(sys.argv) > 2 else None
    build(jsx, out)
