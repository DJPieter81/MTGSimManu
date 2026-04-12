#!/usr/bin/env python3
"""
build_guide.py — Generate deck guide HTML from metagame_data.jsx data.

Usage:
  python build_guide.py "Boros Energy"                    # stdout
  python build_guide.py "Boros Energy" out.html           # write file
  python build_guide.py --all /mnt/user-data/outputs/     # all T1/T2 decks

Reads: metagame_data.jsx (D object), decks/modern_meta.py, templates/reference_deck_guide.html
"""
import json, re, sys, os

def load_D(jsx_path='metagame_data.jsx'):
    with open(jsx_path) as f: jsx = f.read()
    d_start = jsx.index('const D = ') + 10
    d_end = jsx.index(';\nconst N')
    return json.loads(jsx[d_start:d_end])

ARCH = {
    '4/5c Control':'Control','4c Omnath':'Midrange','Affinity':'Aggro',
    'Amulet Titan':'Combo','Boros Energy':'Aggro','Dimir Midrange':'Midrange',
    'Domain Zoo':'Aggro','Eldrazi Tron':'Ramp',"Goryo's Vengeance":'Combo',
    'Izzet Prowess':'Aggro','Jeskai Blink':'Tempo','Living End':'Combo',
    'Ruby Storm':'Combo','Azorius Control':'Control','Azorius Control (WST)':'Control',
    'Pinnacle Affinity':'Aggro',
}

def esc(s): return s.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

def wr_color(wr):
    if wr >= 65: return '#1f7040'
    if wr >= 50: return '#854f0b'
    return '#b02020'

def build_guide(deck_name, D):
    decks = D['decks']
    idx = decks.index(deck_name)
    wins = D['wins'][idx]
    N = D['matches_per_pair']
    overall = D['overall'][idx]
    dc = D['deck_cards'][idx]
    ms = D['meta_shares']

    flat_wr = overall['win_rate']
    wgt_wr = overall['weighted_wr']
    gap = round(wgt_wr - flat_wr, 1)
    
    # Matchup WRs
    mu = {}
    for i, d in enumerate(decks):
        if i == idx: continue
        wr = round(wins[i] / N * 100)
        key = f"{idx},{i}"
        mc = D['matchup_cards'].get(key, {})
        mu[d] = {'wr': wr, 'mc': mc, 'meta': ms.get(d, 0), 'arch': ARCH.get(d, '?')}
    
    best = max(mu.items(), key=lambda x: x[1]['wr'])
    worst = min(mu.items(), key=lambda x: x[1]['wr'])
    
    # Rank by weighted WR
    ranked = sorted(D['overall'], key=lambda x: -x['weighted_wr'])
    rank = next(i+1 for i,o in enumerate(ranked) if o['idx'] == idx)
    
    # Tier
    meta_pct = ms.get(deck_name, 0)
    tier = 'T1' if meta_pct >= 5 else 'T2' if meta_pct >= 3 else 'Field'

    # Stars: top 2 finishers + top 2 by damage that aren't the same
    stars_fin = dc['finishers'][:2]
    stars_dmg = [d for d in dc['mvp_damage'] if d['card'] not in [f['card'] for f in stars_fin]][:2]
    
    # Sort matchups by meta for spread
    t1 = [(d,m) for d,m in mu.items() if m['meta'] >= 5]
    t2 = [(d,m) for d,m in mu.items() if 3 <= m['meta'] < 5]
    field = [(d,m) for d,m in mu.items() if m['meta'] < 3]
    for lst in [t1, t2, field]: lst.sort(key=lambda x: -x[1]['meta'])

    # Strategic findings data
    # F1: damage efficiency
    fin_map = {f['card']: f['count'] for f in dc['finishers']}
    dmg_map = {d['card']: d['count'] for d in dc['mvp_damage']}
    
    # F3: G1 vs match swings
    swings = []
    for d, m in mu.items():
        mc = m['mc']
        if not mc.get('g1_wins'): continue
        g1 = mc['g1_wins'][0]
        match_wr = m['wr']
        delta = match_wr - g1
        if abs(delta) >= 10:
            swings.append((d, g1, match_wr, delta, mc.get('went_to_3', 0), mc.get('comebacks', [0,0])))
    swings.sort(key=lambda x: -abs(x[3]))

    # F4: What kills us (d2_top_damage from losing matchups)
    danger_cards = []
    for d, m in sorted(mu.items(), key=lambda x: x[1]['wr']):
        if m['wr'] >= 55: continue
        mc = m['mc']
        if mc.get('d2_top_damage'):
            for card in mc['d2_top_damage'][:1]:
                danger_cards.append((card['card'], card['count'], d))
    
    # F6: weighted gap comparison
    gaps = []
    for o in D['overall']:
        gaps.append((o['deck'], round(o['weighted_wr'] - o['win_rate'], 1)))
    gaps.sort(key=lambda x: x[1], reverse=True)

    # Build HTML
    html = []
    h = html.append
    
    h('<!DOCTYPE html>')
    h(f'<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">')
    h(f'<title>{esc(deck_name)} — Modern Deck Guide</title>')
    
    # CSS (from template)
    h('<style>')
    h('*{box-sizing:border-box;margin:0;padding:0}')
    h("body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fff;color:#111;font-size:14px;padding:24px;max-width:960px;margin:0 auto}")
    h('h1{font-size:24px;font-weight:700;margin-bottom:6px}')
    h('.subtitle{font-size:12px;color:#888;margin-bottom:20px}')
    h('.hero{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid #e0e0e0;border-radius:4px;margin-bottom:24px;overflow:hidden}')
    h('.hero-item{padding:14px 16px;border-right:1px solid #e0e0e0}.hero-item:last-child{border-right:none}')
    h('.hero-label{font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:#888;margin-bottom:4px}')
    h('.hero-val{font-size:28px;font-weight:700;line-height:1}')
    h('.hero-val.g{color:#1f7040}.hero-val.r{color:#b02020}.hero-val.a{color:#854f0b}')
    h('.hero-sub{font-size:11px;color:#666;margin-top:5px}')
    h('.section-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#666;margin-bottom:12px;margin-top:24px;border-bottom:1px solid #e8e8e8;padding-bottom:6px}')
    h('.mu-row{display:flex;align-items:center;gap:6px;padding:3px 0;font-size:12px}')
    h('.mu-name{width:130px;text-align:right;color:#555;font-size:11px}')
    h('.mu-type{width:52px;font-size:9px;color:#aaa;text-align:center}')
    h('.mu-meta{width:36px;font-size:9px;color:#aaa;text-align:center}')
    h('.mu-bar{flex:1;height:10px;background:#f0f0f0;border-radius:2px;overflow:hidden;max-width:160px}')
    h('.mu-fill{height:100%;border-radius:2px}')
    h('.mu-val{width:36px;font-weight:700;font-size:11px;text-align:right}')
    h('.tier-hdr{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#aaa;margin:14px 0 4px;padding:4px 0;border-bottom:1px solid #f0f0f0}')
    h('.star-cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:28px}')
    h('.star-card{text-align:center}')
    h('.star-card img{width:100%;border-radius:12px;box-shadow:0 4px 16px rgba(0,0,0,.15);transition:transform .2s}')
    h('.star-card img:hover{transform:scale(1.04)}')
    h('.star-label{font-size:9px;text-transform:uppercase;letter-spacing:.08em;font-weight:700;margin-bottom:6px;padding:2px 8px;border-radius:3px;display:inline-block}')
    h('.star-label.mvp{background:#e8f0e8;color:#1f7040}')
    h('.star-label.surprise{background:#fff0e0;color:#c06010}')
    h('.star-stat{font-size:20px;font-weight:700;margin-top:8px;line-height:1}')
    h('.star-desc{font-size:10px;color:#888;margin-top:4px;line-height:1.4}')
    h('.star-name{font-size:12px;font-weight:600;color:#333;margin-top:6px}')
    h('.card-tip{position:relative;cursor:pointer;border-bottom:1px dotted #ccc}.card-tip:hover{color:#c04010}')
    h('#card-popup{position:fixed;z-index:999;pointer-events:none;display:none;border-radius:8px;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,.35);width:244px;height:340px;background:#111}')
    h('#card-popup img{width:100%;height:100%;object-fit:contain}')
    h('.prov{font-size:9px;color:#bbb;text-align:center;margin-top:30px;border-top:1px solid #eee;padding-top:10px}')
    h('@media(max-width:640px){.hero{grid-template-columns:1fr 1fr}.star-cards{grid-template-columns:1fr 1fr}}')
    h('</style>')
    
    # Scryfall JS
    h('<script>')
    h("document.addEventListener('DOMContentLoaded',()=>{")
    h("const p=document.createElement('div');p.id='card-popup';p.innerHTML='<img id=\"card-img\" src=\"\" alt=\"\">';document.body.appendChild(p);")
    h("const img=document.getElementById('card-img'),cache={};")
    h("document.addEventListener('mouseover',e=>{const el=e.target.closest('.card-tip');if(!el)return;const n=el.dataset.card;if(!n)return;const u='https://api.scryfall.com/cards/named?fuzzy='+encodeURIComponent(n)+'&format=image&version=normal';img.src=cache[n]||u;if(!cache[n])cache[n]=u;p.style.display='block'});")
    h("document.addEventListener('mouseout',e=>{if(e.target.closest('.card-tip'))p.style.display='none'});")
    h("document.addEventListener('mousemove',e=>{if(p.style.display==='block'){p.style.left=Math.min(e.clientX+16,window.innerWidth-260)+'px';p.style.top=Math.max(8,Math.min(e.clientY-170,window.innerHeight-350))+'px'}});")
    h('});')
    h('</script>')
    h('</head><body>')
    
    # Hero
    h(f'<h1>{esc(deck_name)}</h1>')
    arch = ARCH.get(deck_name, 'Unknown')
    h(f'<div class="subtitle">{arch} · Modern · April 2026 · Sim-verified · {N} Bo3 per pair</div>')
    
    wr_cls = 'g' if flat_wr >= 55 else 'a' if flat_wr >= 45 else 'r'
    h('<div class="hero">')
    h(f'  <div class="hero-item"><div class="hero-label">Format</div><div class="hero-val" style="font-size:18px;padding-top:4px">Modern</div><div class="hero-sub">{arch} · {meta_pct}% meta</div></div>')
    h(f'  <div class="hero-item"><div class="hero-label">Sim WR (flat)</div><div class="hero-val {wr_cls}">{flat_wr}%</div><div class="hero-sub">⚖ <span style="color:{wr_color(wgt_wr)}">{wgt_wr}%</span> T1/T2 weighted</div></div>')
    h(f'  <div class="hero-item"><div class="hero-label">Rank</div><div class="hero-val {wr_cls}" style="font-size:22px;padding-top:2px">#{rank}</div><div class="hero-sub">{tier} · {gap:+.1f}pp weighted gap</div></div>')
    h(f'  <div class="hero-item"><div class="hero-label">Best / Worst</div><div class="hero-val g" style="font-size:18px;padding-top:2px">{best[1]["wr"]}%</div><div class="hero-sub">vs {best[0][:12]} / worst {worst[1]["wr"]}% vs {worst[0][:12]}</div></div>')
    h('</div>')
    
    # Stars
    h(f'<div class="section-title">Stars of the Sim — {overall["total_matches"]} Games</div>')
    h('<div class="star-cards">')
    for f in stars_fin[:2]:
        url = 'https://api.scryfall.com/cards/named?fuzzy=' + f['card'].split('//')[0].strip().replace(' ','+') + '&format=image&version=normal'
        dmg = dmg_map.get(f['card'], dmg_map.get(f['card'].split(',')[0], '?'))
        h(f'  <div class="star-card"><span class="star-label mvp">MVP</span>')
        h(f'    <img src="{url}" alt="{esc(f["card"])}" loading="lazy">')
        h(f'    <div class="star-name">{esc(f["card"].split("//")[0].strip())}</div>')
        h(f'    <div class="star-stat" style="color:#1f7040">{f["count"]} kills</div>')
        h(f'    <div class="star-desc">{dmg} total dmg · {f["desc"]}</div></div>')
    for d in stars_dmg[:2]:
        url = 'https://api.scryfall.com/cards/named?fuzzy=' + d['card'].split('//')[0].strip().replace(' ','+') + '&format=image&version=art_crop'
        kills = fin_map.get(d['card'], '?')
        h(f'  <div class="star-card"><span class="star-label surprise">Overperformer</span>')
        h(f'    <img src="{url}" alt="{esc(d["card"])}" loading="lazy" style="height:200px;object-fit:cover;width:100%;border-radius:12px">')
        h(f'    <div class="star-name">{esc(d["card"])}</div>')
        h(f'    <div class="star-stat" style="color:#c06010">{d["count"]} dmg</div>')
        h(f'    <div class="star-desc">{kills} kills · hidden damage engine</div></div>')
    h('</div>')
    
    # G1 → Match Swing findings
    if swings:
        h('<div class="section-title">G1 → Match Swing — Sideboard Asymmetry</div>')
        h('<div style="border:1px solid #e0e0e0;border-radius:4px;padding:14px;margin:12px 0">')
        for d, g1, match_wr, delta, g3, cbacks in swings[:6]:
            dc2 = '#1f7040' if delta > 0 else '#b02020'
            h(f'<div style="display:flex;align-items:center;gap:8px;font-size:11px;padding:3px 0;border-bottom:1px solid #f5f5f5">')
            h(f'<span style="width:110px;text-align:right;color:#555;font-weight:600">{esc(d[:20])}</span>')
            h(f'<span style="color:{wr_color(g1)};font-weight:700;width:35px">{g1}%</span>')
            h(f'<span style="color:#888">→</span>')
            h(f'<span style="color:{wr_color(match_wr)};font-weight:700;width:35px">{match_wr}%</span>')
            h(f'<span style="color:{dc2};font-weight:700;font-size:10px">{delta:+d}pp</span>')
            h(f'<span style="font-size:9px;color:#aaa">G3={g3}%</span></div>')
        h('</div>')
    
    # Danger cards
    if danger_cards:
        h('<div class="section-title">What Kills You — Removal Blind Spots</div>')
        h(f'<div style="display:grid;grid-template-columns:repeat({min(len(danger_cards),4)},1fr);gap:12px;margin:12px 0">')
        for card, dmg, opp in danger_cards[:4]:
            curl = 'https://api.scryfall.com/cards/named?fuzzy=' + card.split('//')[0].strip().replace(' ','+') + '&format=image&version=art_crop'
            h(f'<div style="text-align:center;border:1px solid #e0e0e0;border-radius:8px;padding:10px">')
            h(f'<img src="{curl}" alt="{esc(card)}" style="width:100%;border-radius:6px;height:70px;object-fit:cover">')
            h(f'<div style="font-size:11px;font-weight:700;color:#b02020;margin-top:4px">{esc(card[:25])}</div>')
            h(f'<div style="font-size:10px;color:#555">{dmg} dmg · {esc(opp[:15])}</div></div>')
        h('</div>')
    
    # Matchup spread
    h('<div class="section-title">Matchup Spread</div>')
    for label, group in [('T1 opponents (≥5% meta)', t1), ('T2 opponents (3-5%)', t2), ('Field', field)]:
        if not group: continue
        h(f'<div class="tier-hdr">{label}</div>')
        for d, m in group:
            c = wr_color(m['wr'])
            h(f'<div class="mu-row"><span class="mu-name">{esc(d[:20])}</span><span class="mu-type">{m["arch"].lower()}</span><span class="mu-meta">{m["meta"]}%</span><div class="mu-bar"><div class="mu-fill" style="width:{m["wr"]}%;background:{c}"></div></div><span class="mu-val" style="color:{c}">{m["wr"]}%</span></div>')
    
    # Provenance — date + engine SHA resolved at build time
    import datetime, subprocess
    try:
        sha = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'],
                                      stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        sha = 'unknown'
    today = datetime.date.today().isoformat()
    h('<div class="prov">')
    h(f'Simulated: {today} · {len(decks)} decks · {N} Bo3/pair ({overall["total_matches"]} games for {esc(deck_name)}) · Engine: MTGSimManu@{sha}<br>')
    h(f'Source: metagame_data.jsx (D object) · Card stats: deck_cards[{idx}] · Matchups: matchup_cards["{idx},*"]<br>')
    h('Shell: ManusAI · Strategy + EV scoring: Claude · Owner: DJPieter81')
    h('</div>')
    h('</body></html>')
    
    return '\n'.join(html)

if __name__ == '__main__':
    D = load_D()
    
    if '--all' in sys.argv:
        outdir = sys.argv[sys.argv.index('--all') + 1] if len(sys.argv) > sys.argv.index('--all') + 1 else '.'
        for o in D['overall']:
            ms = D['meta_shares'].get(o['deck'], 0)
            if ms < 3: continue  # skip field decks
            name = o['deck']
            slug = name.lower().replace(' ','_').replace("'",'').replace('/','').replace('(','').replace(')','')
            path = os.path.join(outdir, f'guide_{slug}.html')
            html = build_guide(name, D)
            with open(path, 'w') as f: f.write(html)
            print(f'{name:28s} → {path} ({len(html):,} chars)')
    else:
        name = sys.argv[1] if len(sys.argv) > 1 else 'Boros Energy'
        html = build_guide(name, D)
        if len(sys.argv) > 2:
            with open(sys.argv[2], 'w') as f: f.write(html)
            print(f'Written {len(html):,} chars to {sys.argv[2]}')
        else:
            print(html)
