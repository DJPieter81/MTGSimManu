"""
build_replay.py — MTG Bo3 Log → HTML Replay Viewer
Usage: python build_replay.py <log_file> <output_html> <seed>

Parses verbose --bo3 log output from run_meta.py and produces a standalone
interactive HTML replay following the replay_burn_vs_sneak_a.html reference spec.

Rules:
- NO AI-generated narrative. Only raw sim data from the log.
- Board state keyed by player name (not active/opp) — avoids per-turn swap bug.
- End-of-turn board uses next turn's header (state after plays resolved).
- Hand tracked: opening hand + draws - plays each turn.
- Reasoning from log: "→ Goal: X [role]" lines only.
"""

import re, ast, sys, os

P1C, P2C = '#0969da', '#d1242f'

def esc(s): return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

BADGE_CATS = {
    'land':    ('#dafbe1','#1a7f37','LAND'),
    'spell':   ('#ddf4ff','#0969da','SPELL'),
    'draw':    ('#f0f0ff','#5a5a9a','DRAW'),
    'cantrip': ('#e0f0ff','#0550ae','DIG'),
    'combat':  ('#ffebe9','#d1242f','COMBAT'),
    'counter': ('#f5f0ff','#8250df','COUNTER'),
    'trigger': ('#fff8c5','#9a6700','TRIGGER'),
    'mana':    ('#dafbe1','#1a7f37','MANA'),
    'removal': ('#ffebe9','#d1242f','REMOVE'),
    'combo':   ('#fff0f8','#bf4b8a','COMBO'),
    'fetch':   ('#f5f0ff','#6639ba','FETCH'),
    'discard': ('#fff8c5','#9a6700','DISCARD'),
    'damage':  ('#ffebe9','#d1242f','DAMAGE'),
    'other':   ('#f6f8fa','#656d76','OTHER'),
}

def badge(cat):
    bg, col, lbl = BADGE_CATS.get(cat, BADGE_CATS['other'])
    return f'<span class="cat-badge" style="background:{bg};color:{col}">{lbl}</span>'

def classify(play):
    p = play.lower()
    if p.startswith('play ') or 'enters tapped' in p: return 'land'
    if p.startswith('crack '): return 'fetch'
    if p.startswith('cast ') or p.startswith('escape '):
        if any(x in p for x in ['consign','negate','pierce','fluster','force of will','daze']): return 'counter'
        if any(x in p for x in ['thoughtseize','duress']): return 'discard'
        if any(x in p for x in ['lightning bolt','galvanic discharge','fatal push','unholy heat','solitude']): return 'removal'
        if any(x in p for x in ['grapeshot','empty the warrens']): return 'combo'
        if any(x in p for x in ['manamorphose','pyretic ritual','desperate ritual','seething song','reckless impulse',"wrenn's resolve"]): return 'mana'
        return 'spell'
    if 'ch.' in p and ('saga' in p or 'chapter' in p or 'fable' in p): return 'trigger'
    if 'equip' in p: return 'trigger'
    if 'attack with' in p: return 'combat'
    if 'deals' in p or ('damage' in p and 'to' in p): return 'damage'
    return 'other'

def pill(card):
    from urllib.parse import quote as _q
    sf = card.split(" (")[0].strip()
    img = "https://api.scryfall.com/cards/named?exact=" + _q(sf) + "&format=image&version=small"
    q = chr(39)
    art = f"<img class=\"hand-card-art\" src=\"{img}\" alt=\"{esc(sf)}\" loading=\"lazy\" onerror=\"this.style.display={q}none{q}\">"
    label = f"<span class=\"hand-card-label\">{esc(sf[:18])}</span>"
    return f"<span class=\"hand-card\">{art}{label}</span>"
def life_svg(turns, p1n, p2n):
    lp1, lp2 = [20], [20]
    for t in turns:
        lp1.append(t.get('life_p1', lp1[-1]))
        lp2.append(t.get('life_p2', lp2[-1]))
    n = len(lp1)
    if n < 2: return ''
    W, H = 760, 80
    def px(i): return 10 + int(i/(n-1)*(W-20))
    def py(v): return 5 + int((1-max(0,min(20,v))/20)*(H-12))
    def poly(ls, col):
        return f'<polyline points="{" ".join(f"{px(i)},{py(v)}" for i,v in enumerate(ls))}" fill="none" stroke="{col}" stroke-width="2" opacity=".85"/>'
    def dot(i, v, col, anc='middle'):
        return f'<circle cx="{px(i)}" cy="{py(v)}" r="3" fill="{col}"/><text x="{px(i)}" y="{py(v)-5}" fill="{col}" font-size="8" text-anchor="{anc}">{v}</text>'
    s = f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:80px">'
    # grid lines at 10 and 5
    for life in [5,10,15]:
        y=py(life)
        s+=f'<line x1="10" y1="{y}" x2="{W-10}" y2="{y}" stroke="#d0d7de" stroke-width="1" stroke-dasharray="3,3"/>'
        s+=f'<text x="6" y="{y+3}" fill="#484f58" font-size="7" text-anchor="end">{life}</text>'
    s += poly(lp1,P1C)+poly(lp2,P2C)
    s += dot(0,lp1[0],P1C)+dot(n-1,lp1[-1],P1C,'end')
    s += dot(0,lp2[0],P2C)+dot(n-1,lp2[-1],P2C,'end')
    s += f'<text x="10" y="{H-4}" fill="{P1C}" font-size="8" font-weight="600">{esc(p1n)}</text>'
    s += f'<text x="{W-10}" y="{H-4}" fill="{P2C}" font-size="8" font-weight="600" text-anchor="end">{esc(p2n)}</text>'
    s += '</svg>'
    return s

def parse_games(lines):
    bounds = [i for i,l in enumerate(lines) if re.match(r'\s+GAME \d+:', l)]
    bounds.append(len(lines))
    return [parse_game(lines[bounds[i]:bounds[i+1]], i+1) for i in range(len(bounds)-1)]

def parse_game(block, gnum):
    g = {'num':gnum,'turns':[],'hands':{},'result':{},'p1name':'','p2name':'','on_play':''}
    hm = re.search(r'GAME \d+: (.+?) \(P1\) vs (.+?) \(P2\)', block[0])
    if hm: g['p1name'],g['p2name'] = hm.group(1),hm.group(2)
    op = next((re.search(r'on play\): (.+)',l) for l in block[:12] if 'on play' in l), None)
    if op: g['on_play'] = op.group(1)

    cur_p=None; mull_acc={1:[],2:[]}
    for line in block:
        ohm=re.match(r'P(\d) \(.+?\) opening hand', line)
        if ohm:
            cur_p=int(ohm.group(1))
            if cur_p not in g['hands']:
                g['hands'][cur_p]={'cards':[],'mull_hands':[],'mulled':False,'keep_size':7,'mull_reason':''}
        cm=re.match(r'\s+• (.+?) \[', line)
        if cm and cur_p and cur_p in g['hands']: g['hands'][cur_p]['cards'].append(cm.group(1))
        km=re.match(r'→ P(\d) KEEPS (\d+) — (.+)', line)
        if km:
            p=int(km.group(1))
            if p in g['hands']:
                g['hands'][p]['keep_size']=int(km.group(2))
                g['hands'][p]['keep_reason']=km.group(3).strip()
        elif re.match(r'→ P(\d) KEEPS (\d)', line):
            km2=re.match(r'→ P(\d) KEEPS (\d)', line)
            p=int(km2.group(1))
            if p in g['hands']: g['hands'][p]['keep_size']=int(km2.group(2))
        mm=re.match(r'→ P(\d) MULLIGANS \((.+?)\)', line)
        if mm:
            p=int(mm.group(1))
            if p in g['hands']:
                g['hands'][p]['mulled']=True; g['hands'][p]['mull_reason']=mm.group(2)
                mull_acc[p].append(list(g['hands'][p]['cards'])); g['hands'][p]['cards']=[]
        nm=re.match(r"  New hand \(\d+ lands, \d+ spells\): (\[.+?\])", line)
        if nm and cur_p and cur_p in g['hands']:
            try: g['hands'][cur_p]['cards']=ast.literal_eval(nm.group(1))
            except: pass
        kp=re.match(r"  Keeps: (\[.+?\])", line)
        if kp and cur_p and cur_p in g['hands']:
            try: g['hands'][cur_p]['cards']=ast.literal_eval(kp.group(1))
            except: pass
    for p in g['hands']: g['hands'][p]['mull_hands']=mull_acc.get(p,[])

    # Live hand tracking per player
    live_hands = {
        1: list(g['hands'].get(1,{}).get('cards',[])),
        2: list(g['hands'].get(2,{}).get('cards',[]))
    }

    cur=None; cur_board_player=None; pending_goal=None
    for line in block:
        tm=re.match(r"╔══ TURN (\d+) — (.+?) \(P(\d)\)", line)
        if tm:
            cur={'num':int(tm.group(1)),'player':tm.group(2),'pidx':int(tm.group(3)),
                 'life_active':20,'life_opp':20,'life_p1':20,'life_p2':20,
                 'plays':[],'combat':[],'drawn':None,'hand_snapshot':[],
                 'boards':{g['p1name']:{'creatures':'','lands':'','other':''},
                           g['p2name']:{'creatures':'','lands':'','other':''}},
                 'equip_map':{}}
            g['turns'].append(cur); cur_board_player=None; pending_goal=None; pending_response=None
        if not cur: continue

        lm=re.search(r'║ Life: (.+?) (\d+)\s+\|  (.+?) (\d+)', line)
        if lm:
            la,lo=int(lm.group(2)),int(lm.group(4))
            cur['life_active'],cur['life_opp']=la,lo
            if cur['pidx']==1: cur['life_p1'],cur['life_p2']=la,lo
            else: cur['life_p2'],cur['life_p1']=la,lo

        # Drawn card
        dm=re.search(r'\[Draw\] P(\d) draws: (.+)', line)
        if dm:
            p,card=int(dm.group(1)),dm.group(2).strip()
            cur['drawn']=card
            live_hands[p].append(card)
            cur['hand_snapshot']=list(live_hands[cur['pidx']])

        if '[Draw] Skipped' in line:
            cur['hand_snapshot']=list(live_hands[cur['pidx']])

        # Goal reasoning (line before play)
        gm=re.search(r'→ Goal: (.+)', line)
        if gm: pending_goal=gm.group(1).strip()

        # Board sections keyed by player name
        bm=re.match(r'║ (.+?) board:', line)
        if bm:
            pname=bm.group(1).strip()
            cur_board_player=pname if pname in cur['boards'] else None
        if cur_board_player:
            cbm=re.search(r'║\s+Creatures: (.+)', line)
            if cbm:
                v=cbm.group(1).strip()
                if v!='(empty)': cur['boards'][cur_board_player]['creatures']=v
            lbm=re.search(r'║\s+Lands: (.+)', line)
            if lbm:
                v=lbm.group(1).strip()
                if v not in ('(none)',''):  cur['boards'][cur_board_player]['lands']=v
            obm=re.search(r'║\s+Other: (.+)', line)
            if obm:
                v=obm.group(1).strip()
                if v: cur['boards'][cur_board_player]['other']=v

        # Track equipment attachments for creature badge rendering
        em=re.search(r'T\d+ P(\d+): Equip (.+?) to (.+?) \(cost', line)
        if em and cur:
            eq_name=em.group(2).strip(); cr_name=em.group(3).strip()
            emap = cur.setdefault('equip_map', {})
            # Remove this equipment from wherever it was
            for k in list(emap.keys()):
                emap[k] = [e for e in emap[k] if e != eq_name]
                if not emap[k]: del emap[k]
            emap.setdefault(cr_name, []).append(eq_name)
        falls=re.search(r': (.+?) falls off (.+?) \(unattached\)', line)
        if falls and cur:
            eq_name=falls.group(1).strip(); cr_name=falls.group(2).strip()
            emap = cur.get('equip_map', {})
            if cr_name in emap:
                emap[cr_name] = [e for e in emap[cr_name] if e != eq_name]
                if not emap[cr_name]: del emap[cr_name]

        # Response flag — "[Priority] P# responds with Card"
        resp=re.search(r'\[Priority\] P(\d) responds with (.+)', line)
        if resp: pending_response=(int(resp.group(1)), resp.group(2).strip())

        # Plays with goal reasoning attached
        pm=re.match(rf'T{cur["num"]} P(\d)+: (.+)', line)
        if pm:
            pidx=int(pm.group(1)); play=pm.group(2)
            is_response = bool(pending_response and pending_response[0]==pidx)
            if is_response: pending_response=None
            cur['plays'].append({'text':play,'reasoning':pending_goal,'is_response':is_response,'pidx':pidx})
            pending_goal=None
            pending_goal=None
            # Remove from live hand
            cm2=re.match(r'(?:Cast|Play|Escape|Equip) (.+?)(?:\s*\(|$)', play)
            if cm2:
                card=cm2.group(1).strip()
                matches=[c for c in live_hands[pidx] if card.lower() in c.lower() or c.lower() in card.lower()]
                if matches: live_hands[pidx].remove(matches[0])

        dam=re.search(r'\[Combat Damage\] (\d+) damage dealt → P\d+ life: (\d+) → (-?\d+)', line)
        if dam:
            lethal = int(dam.group(3)) <= 0
            prefix = 'LETHAL:' if lethal else ''
            cur['combat'].append(f'{prefix}{dam.group(1)} damage → life {dam.group(2)} → {dam.group(3)}')
        atk=re.search(r'P\d attacks with: (.+)', line)
        if atk: cur['combat'].append(f'⚔ {esc(atk.group(1))}')
        blk=re.search(r'P\d blocks: (.+)', line)
        if blk and blk.group(1).strip(): cur['combat'].append(f'🛡 {esc(blk.group(1))}')
        brk=re.search(r'P\d+:\s{2}(.+?) \((\d+)/(\d+)\) → (\d+) dmg to player(.*)', line)
        if brk:
            note = ' (trample)' if 'trample' in brk.group(5) else ''
            cur['combat'].append(f'BREAKDOWN:{esc(brk.group(1))} {brk.group(2)}/{brk.group(3)} · {brk.group(4)} dmg{note}')

    rm=re.search(r'>>> (.+?) wins Game \d+ on turn (\d+) via (.+)', '\n'.join(block))
    if rm: g['result']={'winner':rm.group(1),'turn':int(rm.group(2)),'how':rm.group(3)}
    return g

# ── HTML rendering ────────────────────────────────────────────

def hand_section(h):
    html=''
    for mh in h.get('mull_hands',[]):
        html+='<div class="mull-step"><span class="mull-tag">MULL</span><span class="mull-label">Mulled hand:</span></div>'
        html+='<div class="mull-pills">'+''.join(pill(c) for c in mh)+'</div>'
        if h.get('mull_reason'): html+=f'<div class="mull-reason">{esc(h["mull_reason"])}</div>'
    ks=h.get('keep_size',7)
    keep_reason=h.get('keep_reason','')
    kr_html = f'<span class="keep-reason">{esc(keep_reason)}</span>' if keep_reason else ''
    html+=f'<div class="mull-step"><span class="keep-tag">KEEP {ks}</span>{kr_html}</div>'
    html+='<div class="hand-pills">'+''.join(pill(c) for c in h.get('cards',[]))+'</div>'
    keys=[c for c in h.get('cards',[]) if any(k in c for k in
          ['Cranial Plating','Mox Opal','Ornithopter','Springleaf Drum','Memnite','Signal Pest',
           'Ragavan','Territorial','Phlage','Scion','Ruby Medallion','Grapeshot','Past in Flames',
           "Urza's Saga",'Thought Monitor'])]
    if keys and not keep_reason:
        html+=f'<div class="hand-analysis">✓ Key {"pieces" if len(keys)>1 else "piece"}: {", ".join(esc(c) for c in keys[:3])}</div>'
    return html

def creature_badges(s, equip_map=None):
    if not s: return '<span style="color:#484f58">empty</span>'
    bits=re.split(r',\s*(?=[A-Z])',s)
    out=''
    equip_map = equip_map or {}
    for b in bits:
        pm=re.search(r'(.+?)\s*\((\d+/\d+)\)',b.strip())
        name = pm.group(1).strip() if pm else re.sub(r'\s*\[.*?\]','',b.strip())
        pt   = pm.group(2) if pm else None
        if not name: continue
        # Equipment attached to this creature
        equips = equip_map.get(name, [])
        eq_html = ''.join(f'<span class="equip-tag" title="{esc(e)}">⚔{esc(e)}</span>' for e in equips)
        # Scryfall image tooltip
        sf_name = name.split(' (')[0].strip()
        from urllib.parse import quote as _qu
        img_url = "https://api.scryfall.com/cards/named?exact=" + _qu(sf_name) + "&format=image&version=art_crop"
        q = chr(39)
        art = f'<img class="badge-art" src="{img_url}" alt="{esc(sf_name)}" loading="lazy" onerror="this.style.display={q}none{q}">'
        pt_html = f'<span class="pt">{pt}</span>' if pt else ''
        out += f'<span class="creature-badge">{art}<span class="badge-text">{esc(name)}{pt_html}{eq_html}</span></span>'
    return out or '<span style="color:#484f58">empty</span>'


def other_badges(s):
    """Render non-creature permanents (equipment, artifacts, enchantments) with art thumbnails."""
    if not s: return ''
    from urllib.parse import quote as _qu
    items = [x.strip() for x in s.split(',') if x.strip()]
    out = ''
    for name in items:
        sf_name = name.split(' (')[0].strip()
        img_url = "https://api.scryfall.com/cards/named?exact=" + _qu(sf_name) + "&format=image&version=art_crop"
        q = chr(39)
        art = f'<img class="badge-art" src="{img_url}" alt="{esc(sf_name)}" loading="lazy" onerror="this.style.display={q}none{q}">'
        out += f'<span class="creature-badge other-badge">{art}<span class="badge-text">{esc(name)}</span></span>'
    return out

def split_lands(s):
    """Split a land string into (saga_names[], plain_land_string)."""
    if not s or s == 'none': return [], s or 'none'
    # Known saga / enchantment land names that should show as visual badges
    SAGA_KEYWORDS = ('saga', 'urza', 'fable', 'witch')
    items = [x.strip() for x in s.split(',') if x.strip()]
    sagas, plains = [], []
    for item in items:
        name_part = item.replace('[T]','').strip().rstrip()
        tapped = '[T]' in item
        # Detect saga/enchantment land by name keywords
        if any(k in name_part.lower() for k in SAGA_KEYWORDS):
            sagas.append((name_part, tapped))
        else:
            plains.append(item)
    plain_str = ', '.join(plains) if plains else 'none'
    return sagas, plain_str

def saga_badges(sagas):
    """Render saga/enchantment lands as visual badges."""
    if not sagas: return ''
    from urllib.parse import quote as _qu
    out = ''
    for name, tapped in sagas:
        sf = name.split(' (')[0].strip()
        img_url = "https://api.scryfall.com/cards/named?exact=" + _qu(sf) + "&format=image&version=art_crop"
        q = chr(39)
        art = f'<img class="badge-art" src="{img_url}" alt="{esc(sf)}" loading="lazy" onerror="this.style.display={q}none{q}">'
        tap_icon = '<span class="saga-tapped" title="Tapped">↷</span>' if tapped else ''
        label = f'<span class="badge-text saga-label">{tap_icon}{esc(sf[:14])}</span>'
        out += f'<span class="creature-badge saga-badge">{art}{label}</span>'
    return out

def lc(s): _, plain = split_lands(s); return len([x for x in plain.split(',') if x.strip()]) if plain and plain!='none' else 0

SKIP = {'untaps all','upkeep','goal:','[mana]','[priority]','main 1','begin combat',
        'declare attackers p','declare blockers p','end combat','main 2','end step','resolve '}

def turn_html(t, next_t, gnum, p1name, p2name, star_turns):
    is_p1=(t['pidx']==1); cls='bug' if is_p1 else 'opp'
    la,lo=t['life_active'],t['life_opp']
    star=f'<span class="star-marker" title="Key turn">★{star_turns.index(t["num"])+1}</span>' if t['num'] in star_turns else ''

    # Draw
    drawn_html=''
    if t['drawn']:
        drawn_html=f'<div class="section-label">Draw</div><div class="draw-row">{badge("draw")}<span class="pill">{esc(t["drawn"])}</span></div>'

    # Hand (always shown — tracked from opening hand + draws - plays)
    hand_html=''
    if t['hand_snapshot']:
        hand_html=(f'<div class="section-label">Hand ({len(t["hand_snapshot"])} cards)</div>'
                  f'<div class="hand-pills">'+''.join(pill(c) for c in t["hand_snapshot"])+'</div>')

    # Plays with reasoning from log
    plays_html=''
    step=0
    for play in t['plays']:
        text=play['text']; reason=play.get('reasoning','')
        is_response=play.get('is_response',False)
        play_pidx=play.get('pidx', t['pidx'])
        if any(s in text.lower() for s in SKIP): continue
        step+=1
        cat=classify(text)
        is_key=any(k in text.lower() for k in ['cranial plating','grapeshot','lethal','equip cranial'])
        rid = f'r{gnum}t{t["num"]}p{play_pidx}s{step}'
        rtoggle = (f'<span class="reason-toggle" onclick="toggleReason(\'{rid}\')" title="Show reasoning">\xb7</span>') if reason else ''
        rhtml = (f'<div class="reasoning" id="{rid}" style="display:none">\u2190 {esc(reason)}</div>') if reason else ''
        # Response: opponent plays during active player's turn
        if is_response:
            resp_cls = 'opp' if is_p1 else 'bug'
            resp_name = (p2name if is_p1 else p1name).split()[0]
            resp_badge = f'<span class="respond-badge" style="color:{"#f85149" if is_p1 else "#58a6ff"}">⚡ {resp_name}</span>'
            plays_html += f'<div class="play play-response">{resp_badge}{badge(cat)}<span class="action">{esc(text)}</span>{rtoggle}{rhtml}</div>\n'
        else:
            plays_html += f'<div class="play"><span class="step">{step}.</span>{badge(cat)}<span class="action{" key" if is_key else ""}">{esc(text)}</span>{rtoggle}{rhtml}</div>\n'
    if not plays_html: plays_html='<div class="play"><span class="pass-label">— pass —</span></div>'

    # Combat
    combat_html=''
    if t['combat']:
        breakdown_lines = [c for c in t["combat"] if c.startswith("BREAKDOWN:")]
        has_breakdown = len(breakdown_lines) > 0

        def _atk_card(b):
            from urllib.parse import quote as _qu2
            import re as _re2
            m = _re2.match(r"BREAKDOWN:(.+?)\s+(\d+)/(\d+)\s+·\s+(\d+)\s+dmg(.*)", b[10:])
            if not m: return f"<div class=\"combat-breakdown\">{b[10:]}</div>"
            name,pw,tg,dmg,note = m.group(1),m.group(2),m.group(3),m.group(4),m.group(5).strip()
            sf = name.split(" (")[0].strip()
            img = "https://api.scryfall.com/cards/named?exact=" + _qu2(sf) + "&format=image&version=art_crop"
            q = chr(39)
            art = f"<img class=\"atk-art\" src=\"{img}\" alt=\"{esc(sf)}\" loading=\"lazy\" onerror=\"this.style.display={q}none{q}\">"
            trample = "<span class=\"atk-trample\" title=\"Trample\">↠</span>" if "trample" in note else ""
            return (f"<div class=\"atk-card\">{art}"
                    f"<div class=\"atk-info\"><span class=\"atk-name\">{esc(sf[:14])}</span>"
                    f"<span class=\"atk-pt\">{pw}/{tg}</span>"
                    f"<span class=\"atk-dmg\">⚔{dmg}</span>{trample}</div></div>")

        def _combat_line(c):
            if c.startswith("BREAKDOWN:"): return ""
            if c.startswith("BLOCK-EMRG:"): return f"<div class=\"combat-block emergency\">🚨 {c[11:]}</div>"
            if c.startswith("BLOCK:"): return f"<div class=\"combat-block\">🛡 {c[6:]}</div>"
            if c.startswith("LETHAL:"): return f"<div class=\"combat-lethal\">☠ LETHAL — {c[7:]}</div>"
            if c.startswith("⚔") and has_breakdown: return ""
            return f"<div class=\"combat-detail\">{c}</div>"
        atk_strip = ("<div class=\"atk-strip\">" + "".join(_atk_card(b) for b in breakdown_lines) + "</div>") if breakdown_lines else ""
        combat_html = "<div class=\"section-label\">Combat</div>" + atk_strip + "".join(_combat_line(c) for c in t["combat"])

    # Board — from next turn's header = state AFTER this turn's plays
    src=next_t if next_t else t
    p1b=src['boards'].get(p1name,{'creatures':'','lands':'','other':''})
    p2b=src['boards'].get(p2name,{'creatures':'','lands':'','other':''})
    p1_cr,p1_l_raw,p1_o=p1b['creatures'],p1b['lands'] or 'none',p1b.get('other','')
    p2_cr,p2_l_raw,p2_o=p2b['creatures'],p2b['lands'] or 'none',p2b.get('other','')
    p1_sagas, p1_l = split_lands(p1_l_raw)
    p2_sagas, p2_l = split_lands(p2_l_raw)

    return f'''<div class="turn {cls}" id="g{gnum}t{t["num"]}">
  <div class="turn-header" onclick="toggle(this.parentElement)">
    <div class="left">
      <span class="tnum {cls}">T{t["num"]}</span>
      <span class="player {cls}">{esc(t["player"])}</span>
      <span class="life">Life: <b>{la}</b> &nbsp;|&nbsp; Opp: {lo}</span>
      {f'<span class="hand-count">{len(t["hand_snapshot"])}c</span>' if t["hand_snapshot"] else ''}
      {star}
    </div>
    <span class="arrow">&#9654;</span>
  </div>
  <div class="turn-body">
    {drawn_html}
    {hand_html}
    <div class="section-label">Plays</div>{plays_html}
    {combat_html}
    <div class="section-label">Board after turn</div>
    <div class="board-grid">
      <div class="board-side bug">
        <h4><span style="color:{P1C}">{esc(p1name)}</span> — {lc(p1_l)} land{"s" if lc(p1_l)!=1 else ""}</h4>
        <div class="board">{creature_badges(p1_cr, src.get('equip_map',{}))}</div>
        {f'<div class="other-list">{other_badges(p1_o)}</div>' if p1_o else ''}
        {f'<div class="other-list saga-row">{saga_badges(p1_sagas)}</div>' if p1_sagas else ''}
        <div class="land-list">{esc(p1_l[:140])}</div>
      </div>
      <div class="board-side opp">
        <h4><span style="color:{P2C}">{esc(p2name)}</span> — {lc(p2_l)} land{"s" if lc(p2_l)!=1 else ""}</h4>
        <div class="board">{creature_badges(p2_cr, src.get('equip_map',{}))}</div>
        {f'<div class="other-list">{other_badges(p2_o)}</div>' if p2_o else ''}
        {f'<div class="other-list saga-row">{saga_badges(p2_sagas)}</div>' if p2_sagas else ''}
        <div class="land-list">{esc(p2_l[:140])}</div>
      </div>
    </div>
  </div>
</div>'''

def legend_html():
    cats = [
        ('land','LAND','Land drops'),('spell','SPELL','Spells cast'),('mana','MANA','Mana rituals / cantrips'),
        ('fetch','FETCH','Fetchlands'),('removal','REMOVE','Removal'),('counter','COUNTER','Counterspells'),
        ('combat','COMBAT','Attacks / blocks'),('combo','COMBO','Combo finishers'),
        ('discard','DISCARD','Discard effects'),('trigger','TRIGGER','Abilities / ETBs'),
        ('damage','DAMAGE','Direct damage'),('draw','DRAW','Draw step'),('other','OTHER','Other'),
    ]
    pills = ''.join(f'<span class="leg-item">{badge(c)} <span class="leg-label">{desc}</span></span>' for c,_,desc in cats)
    return f'''<div class="legend-box">
  <div class="legend-title">Legend</div>
  <div class="legend-row">
    <span class="leg-item"><span style="display:inline-block;width:10px;height:10px;background:{P1C};border-radius:2px;margin-right:4px;vertical-align:middle"></span><span class="leg-label">P1 (blue)</span></span>
    <span class="leg-item"><span style="display:inline-block;width:10px;height:10px;background:{P2C};border-radius:2px;margin-right:4px;vertical-align:middle"></span><span class="leg-label">P2 (red)</span></span>
    <span class="leg-item"><span style="color:#e3b341;font-weight:700;margin-right:4px">gold text</span><span class="leg-label">Key play</span></span>
    <span class="leg-item"><span style="color:#f778ba;font-weight:700;margin-right:4px">★</span><span class="leg-label">Key turn</span></span>
    <span class="leg-item"><span style="color:#6e7681;font-style:italic;margin-right:4px">← goal</span><span class="leg-label">AI goal from log</span></span>
  </div>
  <div class="legend-row" style="margin-top:6px">{pills}</div>
  <div class="legend-note">Hand shown = active player's cards (tracked from opening hand + draws − plays). Board shown = state <em>after</em> turn resolves.</div>
</div>'''

def game_html(g, gi, seed):
    res=g['result']; winner=res.get('winner','?')
    win_cls='bug-win' if winner==g['p1name'] else 'opp-win'
    on_draw=g['p2name'] if g['on_play']==g['p1name'] else g['p1name']
    turns=g['turns']

    # Star turns: high damage or key spells
    star_turns=[]
    for t in turns:
        for c in t['combat']:
            dm=re.search(r'(\d+) damage',c)
            if dm and int(dm.group(1))>=6 and t['num'] not in star_turns: star_turns.append(t['num'])
        for p in t['plays']:
            if any(k in p['text'].lower() for k in ['cranial plating',"urza's saga",'grapeshot','past in flames','equip']) and t['num'] not in star_turns:
                star_turns.append(t['num'])
    star_turns=sorted(star_turns)[:4]

    turns_html=''.join(turn_html(t,turns[i+1] if i+1<len(turns) else None,gi,g['p1name'],g['p2name'],star_turns) for i,t in enumerate(turns))
    last=turns[-1] if turns else {}

    return f'''<div class="meta">
  <span>{esc(on_draw)} is ON THE DRAW</span>
  <span style="color:#484f58">Seed: {seed}</span>
</div>
<div class="hands">
  <div class="hand-box bug">
    <h3><span style="color:{P1C}">{esc(g["p1name"])}</span> — Opening Hand (P1)</h3>
    {hand_section(g["hands"].get(1,{}))}
  </div>
  <div class="hand-box opp">
    <h3><span style="color:{P2C}">{esc(g["p2name"])}</span> — Opening Hand (P2)</h3>
    {hand_section(g["hands"].get(2,{}))}
  </div>
</div>
<div class="life-chart">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
    <h3>Life Totals</h3>
    <span style="font-size:10px;color:#484f58">— dashed lines at 5, 10, 15 —</span>
  </div>
  {life_svg(turns,g["p1name"],g["p2name"])}
</div>
<div class="controls">
  <button onclick="expandAll()">Expand All</button>
  <button onclick="collapseAll()">Collapse All</button>
  <span class="kbd-hint">↑↓ navigate turns &nbsp;·&nbsp; Enter: expand/collapse</span>
</div>
{turns_html}
<div class="result">
  <h2 class="{win_cls}">{esc(winner)} WINS</h2>
  <div class="reason">Via {res.get("how","damage")} on turn {res.get("turn","?")}</div>
  <div class="stats">
    Final life: <span style="color:{P1C}">{esc(g["p1name"])} {last.get("life_p1",0)}</span>
    &nbsp;|&nbsp;
    <span style="color:{P2C}">{esc(g["p2name"])} {last.get("life_p2",0)}</span>
    &nbsp;|&nbsp; Length: T{res.get("turn","?")}
  </div>
</div>'''

CSS = '''
*{box-sizing:border-box;margin:0;padding:0}
body{background:#ffffff;color:#1f2328;font-family:'Segoe UI',system-ui,sans-serif;padding:20px;max-width:920px;margin:0 auto;font-size:13px}
/* HEADER */
.header{background:linear-gradient(135deg,#f0f4f8,#e8edf2);border:1px solid #d0d7de;border-radius:12px;padding:24px;margin-bottom:16px}
.header h1{font-size:1.5em;margin-bottom:6px;color:#1f2328}
.header h1 .vs{color:#9198a1}
.meta{display:flex;justify-content:space-between;color:#656d76;font-size:.85em;margin-bottom:12px;padding:6px 0;border-bottom:1px solid #d0d7de}
.series-score{font-size:1.3em;font-weight:700;margin-top:6px}
.bug-s{color:#0969da}.opp-s{color:#d1242f}
/* LEGEND */
.legend-box{background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:11px}
.legend-title{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#9198a1;margin-bottom:8px}
.legend-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.leg-item{display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
.leg-label{color:#656d76;font-size:10px}
.legend-note{margin-top:8px;font-size:10px;color:#9198a1;font-style:italic;border-top:1px solid #d0d7de;padding-top:6px}
/* TABS */
.game-tabs{display:flex;gap:4px;margin-bottom:0}
.game-tab{background:#eaeef2;color:#656d76;border:1px solid #d0d7de;border-radius:8px 8px 0 0;padding:10px 20px;cursor:pointer;font-weight:600;font-size:.9em;transition:background .15s}
.game-tab:hover{background:#d0d7de}
.game-tab.active{background:#ffffff;color:#1f2328;border-bottom-color:#ffffff}
.winner-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-left:6px;vertical-align:middle}
.winner-dot.bug{background:#0969da}.winner-dot.opp{background:#d1242f}
.game-panel{display:none;background:#ffffff;border:1px solid #d0d7de;border-top:none;border-radius:0 8px 8px 8px;padding:16px;margin-bottom:16px}
.game-panel.active{display:block}
/* HANDS */
.hands{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.hand-box{background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;padding:12px}
.hand-box h3{font-size:.8em;color:#656d76;margin-bottom:8px;font-weight:600}
.hand-box.bug{border-left:3px solid #0969da}.hand-box.opp{border-left:3px solid #d1242f}
.pill{display:inline-block;background:#eaeef2;border:1px solid #d0d7de;border-radius:10px;padding:2px 8px;margin:2px;font-size:.78em;font-family:'Fira Code','Consolas',monospace;color:#9a6700}
.hand-pills{display:flex;flex-wrap:wrap;gap:5px;margin:4px 0 6px;align-items:flex-end}
.hand-card{display:inline-flex;flex-direction:column;align-items:center;width:60px;border-radius:5px;overflow:hidden;border:1px solid #d0d7de;background:#fff;flex-shrink:0;vertical-align:bottom}
.hand-card-art{width:60px;height:84px;object-fit:cover;object-position:top;display:block}
.hand-card-label{font-size:.58em;padding:2px 3px;text-align:center;color:#57606a;font-family:'Fira Code',monospace;line-height:1.25;width:100%;background:#f6f8fa;word-break:break-word}
.atk-strip{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 8px;align-items:flex-end}
.atk-card{display:inline-flex;flex-direction:column;align-items:center;width:66px;border-radius:5px;overflow:hidden;border:1px solid #e6ac00;background:#fffbf0;flex-shrink:0}
.atk-art{width:66px;height:48px;object-fit:cover;object-position:top;display:block}
.atk-info{width:100%;padding:2px 4px 3px;text-align:center;background:#fffbf0}
.atk-name{display:block;font-size:.58em;color:#57606a;font-family:'Fira Code',monospace;line-height:1.2;word-break:break-word}
.atk-pt{font-size:.58em;color:#656d76}
.atk-dmg{font-size:.65em;font-weight:700;color:#cf222e;margin-left:3px}
.atk-trample{font-size:.68em;color:#0969da;margin-left:2px}
.mull-pills{opacity:.5;margin:2px 0}.mull-pills .pill{font-size:.7em;text-decoration:line-through}
.mull-step{display:flex;align-items:center;gap:6px;margin:5px 0 2px;font-size:.8em}
.mull-label{color:#656d76;font-weight:600}
.keep-tag{color:#1a7f37;font-weight:700;font-size:.82em;padding:1px 6px;background:#dafbe1;border-radius:3px}
.mull-tag{color:#d1242f;font-weight:700;font-size:.82em;padding:1px 6px;background:#ffebe9;border-radius:3px}
.mull-reason{font-size:.75em;color:#d1242f;margin:2px 0 6px;font-style:italic;padding-left:6px;border-left:2px solid #f5b8b0}
.keep-reason{font-size:.78em;color:#1a7f37;margin-left:8px;font-style:italic}
.hand-analysis{font-size:.75em;color:#1a7f37;margin-top:5px;padding:3px 7px;background:#dafbe1;border-radius:3px;border-left:2px solid #4ac26b}
/* LIFE CHART */
.life-chart{background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;padding:14px;margin-bottom:12px}
.life-chart h3{font-size:.82em;color:#656d76;font-weight:600}
/* CONTROLS */
.controls{display:flex;gap:8px;margin-bottom:12px;align-items:center}
.controls button{background:#f6f8fa;color:#1f2328;border:1px solid #d0d7de;border-radius:5px;padding:5px 12px;cursor:pointer;font-size:.82em}
.controls button:hover{background:#eaeef2;border-color:#0969da}
.kbd-hint{color:#9198a1;font-size:.78em;margin-left:4px}
/* TURNS */
.turn{background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;margin-bottom:6px;overflow:hidden;transition:border-color .15s}
.turn.bug{border-left:3px solid #0969da}.turn.opp{border-left:3px solid #d1242f}
.turn.active{border-color:#bf8700!important}
.turn-header{padding:10px 14px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;user-select:none;transition:background .1s}
.turn-header:hover{background:#eaeef2}
.turn-header .left{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.tnum{font-weight:700;font-size:1.05em;min-width:32px;font-family:'Fira Code',monospace}
.tnum.bug{color:#0969da}.tnum.opp{color:#d1242f}
.player{font-weight:600;font-size:.82em;padding:2px 7px;border-radius:4px}
.player.bug{background:#ddf4ff;color:#0969da}.player.opp{background:#ffebe9;color:#d1242f}
.life{font-size:.85em;color:#656d76}.life b{color:#1f2328}
.hand-count{font-size:.75em;color:#9198a1;background:#eaeef2;padding:1px 5px;border-radius:3px;font-family:'Fira Code',monospace}
.star-marker{color:#bf4b8a;font-size:.8em;font-weight:700}
.arrow{color:#9198a1;transition:transform .2s;font-size:.75em;flex-shrink:0}
.turn.open .arrow{transform:rotate(90deg)}
.turn-body{display:none;padding:0 14px 14px;border-top:1px solid #d0d7de}
.turn.open .turn-body{display:block}
.section-label{font-size:.7em;text-transform:uppercase;letter-spacing:1px;color:#9198a1;margin:10px 0 5px;font-weight:600}
.draw-row{margin-bottom:4px;display:flex;align-items:center;gap:4px}
/* PLAYS */
.play{padding:5px 0;display:flex;gap:6px;align-items:flex-start;flex-wrap:wrap;border-bottom:1px solid #eaeef2}
.step{color:#9198a1;font-size:.82em;min-width:18px;text-align:right;padding-top:2px;flex-shrink:0}
.action{font-family:'Fira Code','Consolas',monospace;font-size:.82em;color:#1f2328}
.action.key{color:#9a6700;font-weight:600}
.reasoning{font-size:.75em;color:#656d76;font-style:italic;width:100%;padding-left:24px;margin-top:1px}
.pass-label{color:#9198a1;font-size:.82em;font-style:italic;padding-left:24px}
/* BADGE */
.cat-badge{font-size:.62em;text-transform:uppercase;letter-spacing:.5px;padding:1px 5px;border-radius:3px;font-weight:700;margin-right:3px;min-width:46px;text-align:center;display:inline-block;flex-shrink:0;font-family:system-ui}
/* COMBAT */
.combat-detail{background:#fff8f8;border:1px solid #f5b8b0;border-radius:5px;padding:6px 10px;margin:3px 0;font-family:'Fira Code',monospace;font-size:.8em;color:#1f2328}
.combat-breakdown{padding:3px 10px 3px 22px;font-size:.78em;color:#6e7781;font-family:'Fira Code',monospace;border-left:2px solid #f5b8b0;margin:1px 0 1px 10px}
.combat-block{padding:4px 10px 4px 16px;font-size:.8em;color:#0550ae;font-family:'Fira Code',monospace;border-left:3px solid #0969da;margin:2px 0;background:#ddf4ff;border-radius:0 4px 4px 0}
.combat-block.emergency{color:#82071e;border-left-color:#cf222e;background:#ffebe9}
/* BOARD */
.board-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:6px}
.board-side{background:#ffffff;border:1px solid #d0d7de;border-radius:6px;padding:8px 10px}
.board-side h4{font-size:.75em;color:#656d76;margin-bottom:6px;font-weight:600}
.board{margin-bottom:4px;min-height:22px;display:flex;flex-wrap:wrap;gap:5px;align-items:flex-start}
.creature-badge{background:#ddf4ff;border:1px solid #a8d8f0;border-radius:6px;font-family:'Fira Code',monospace;font-size:.72em;color:#0969da;display:inline-flex;flex-direction:column;align-items:center;overflow:hidden;width:72px;vertical-align:top;text-align:center}
.badge-art{width:72px;height:52px;object-fit:cover;object-position:top;display:block;flex-shrink:0}
.badge-text{padding:2px 4px 3px;line-height:1.3;word-break:break-word;width:100%}
.creature-badge .pt{color:#656d76;font-size:.88em;display:block}
.land-list{color:#9198a1;font-size:.72em;margin-top:4px;line-height:1.5}
.other-list{margin-top:4px;display:flex;flex-wrap:wrap;gap:5px;align-items:flex-start}
.equip-tag{background:#fff3cd;border:1px solid #e6ac00;border-radius:3px;color:#7a5c00;font-size:.7em;padding:1px 5px;margin-left:3px;font-style:normal}
.saga-badge{background:#f0fff4;border-color:#2da44e;color:#1a7f37}
.saga-label{color:#1a7f37}
.saga-tapped{color:#9198a1;margin-right:2px}
.saga-row{margin-top:3px}
.other-badge{background:#f6f0ff;border-color:#b39ddb;color:#5e35b1}
.combat-lethal{background:#fff0f0;border:1px solid #f5b8b0;border-left:4px solid #cf222e;border-radius:0 5px 5px 0;padding:7px 12px;margin:4px 0;font-weight:600;color:#cf222e;font-size:.85em}
.has-thumb{position:relative;cursor:default}
.has-thumb .card-thumb{display:none;position:absolute;bottom:calc(100% + 4px);left:50%;transform:translateX(-50%);width:130px;border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.35);z-index:999;pointer-events:none}
.has-thumb:hover .card-thumb{display:block}
/* RESULT */
.result{background:linear-gradient(135deg,#f0f4f8,#e8edf2);border:2px solid #d0d7de;border-radius:12px;padding:24px;text-align:center;margin-top:16px}
.result h2{font-size:1.8em;margin-bottom:6px}
.reason{color:#656d76;margin-bottom:4px;font-size:.9em}
.stats{color:#9198a1;font-size:.85em}
.bug-win{color:#0969da}.opp-win{color:#d1242f}
.play-response{background:#fff8e1;border-left:3px solid #bf8700;border-radius:0 4px 4px 0;padding:5px 6px;margin:2px 0}
.respond-badge{font-size:.75em;font-weight:700;margin-right:6px;letter-spacing:.3px}
.reason-toggle{color:#9198a1;font-size:1.1em;cursor:pointer;padding:0 4px;border-radius:3px;user-select:none;flex-shrink:0}.reason-toggle:hover{color:#1f2328;background:#eaeef2}
.reason-toggle.open{color:#0969da}
.reasoning{font-size:.75em;color:#656d76;font-style:italic;width:100%;padding:2px 0 2px 24px;margin-top:1px;border-left:2px solid #d0d7de;margin-left:24px}
'''

JS = '''
function toggle(el){el.classList.toggle('open')}
function expandAll(){document.querySelectorAll('.game-panel.active .turn').forEach(t=>t.classList.add('open'))}
function collapseAll(){document.querySelectorAll('.game-panel.active .turn').forEach(t=>t.classList.remove('open'))}
function showGame(idx){
  document.querySelectorAll('.game-tab').forEach((t,i)=>t.classList.toggle('active',i===idx));
  document.querySelectorAll('.game-panel').forEach((p,i)=>p.classList.toggle('active',i===idx));
}
document.addEventListener('keydown',e=>{
  const active=document.querySelector('.game-panel.active');
  if(!active)return;
  const turns=active.querySelectorAll('.turn');
  let cur=[...turns].findIndex(t=>t.classList.contains('active'));
  if(e.key==='ArrowDown'){e.preventDefault();if(cur<turns.length-1){turns.forEach(t=>t.classList.remove('active'));turns[cur+1].classList.add('active');turns[cur+1].scrollIntoView({behavior:'smooth',block:'center'});}}
  else if(e.key==='ArrowUp'){e.preventDefault();if(cur>0){turns.forEach(t=>t.classList.remove('active'));turns[cur-1].classList.add('active');turns[cur-1].scrollIntoView({behavior:'smooth',block:'center'});}}
  else if(e.key==='Enter'&&cur>=0){e.preventDefault();toggle(turns[cur]);}
});
function toggleReason(id){
  const el=document.getElementById(id);
  const btn=el.previousElementSibling;
  const visible=el.style.display!=='none';
  el.style.display=visible?'none':'block';
  if(btn&&btn.classList.contains('reason-toggle')){
    btn.classList.toggle('open',!visible);
    btn.title=visible?'Show reasoning':'Hide reasoning';
  }
}
'''

def build(log_path, out_path, seed):
    with open(log_path) as f: lines = f.read().splitlines()
    games = parse_games(lines)
    p1name,p2name = games[0]['p1name'],games[0]['p2name']
    score={p1name:0,p2name:0}
    for g in games:
        w=g['result'].get('winner','')
        if w in score: score[w]+=1
    match_winner=max(score,key=score.get)
    p1_score,p2_score=score[p1name],score[p2name]

    tabs_html=''; panels_html=''
    for gi,g in enumerate(games):
        w=g['result'].get('winner',''); dc='bug' if w==g['p1name'] else 'opp'; active='active' if gi==0 else ''
        tabs_html+=f'<div class="game-tab {active}" onclick="showGame({gi})">Game {gi+1} <span class="winner-dot {dc}"></span></div>\n'
        panels_html+=f'<div class="game-panel {active}" id="game-{gi}">\n{game_html(g,gi+1,seed)}\n</div>\n'

    html=f'''<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bo3 Replay: {esc(p1name)} vs {esc(p2name)}</title>
<style>{CSS}</style></head><body>
<div class="header">
  <h1><span style="color:{P1C}">{esc(p1name)}</span> <span class="vs">vs</span> <span style="color:{P2C}">{esc(p2name)}</span></h1>
  <div class="series-score"><span class="bug-s">{p1_score}</span> – <span class="opp-s">{p2_score}</span> <span style="font-size:.6em;color:#484f58;font-weight:400">({esc(match_winner)} wins)</span></div>
  <div style="color:#484f58;font-size:.8em;margin-top:4px">Modern Bo3 · Seed {seed} · Apr 2026</div>
</div>
{legend_html()}
<div class="game-tabs">{tabs_html}</div>
{panels_html}
<script>{JS}</script>
</body></html>'''

    with open(out_path,'w') as f: f.write(html)
    print(f'{os.path.basename(out_path)}: {len(html):,} chars')

if __name__ == '__main__':
    if len(sys.argv) == 4:
        build(sys.argv[1], sys.argv[2], sys.argv[3])
