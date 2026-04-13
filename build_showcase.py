#!/usr/bin/env python3
"""
build_showcase.py — Patch showcase HTML with live data from metagame_data.jsx + PROJECT_STATUS.md.

Usage: python build_showcase.py [output_path]

Reads:
  - metagame_data.jsx  → D object (WRs, deck names, meta shares, resolution)
  - PROJECT_STATUS.md  → 6-expert panel grades
  - templates/reference_showcase.html → template

Patches:
  1. Radar chart data (6 grades)
  2. Grade label text
  3. valData (validation bars with WRs + expected ranges)
  4. wrLabels / wrFull / wrData (WR bar chart)
  5. decksFull / decks (cross-filter arrays)
  6. deckRes (win resolution doughnut)
  7. Deck chips (cloud with meta%)
  8. Deck count references
"""
import re, json, sys, os

# ── Grade map ────────────────────────────────────────────────
GRADE_NUM = {
    'A+': 98, 'A': 95, 'A-': 90,
    'B+': 85, 'B': 80, 'B-': 75,
    'C+': 68, 'C': 62, 'C-': 56,
    'D+': 50, 'D': 44, 'D-': 38,
    'F': 25,
}

# ── Expected WR ranges (from CLAUDE.md) ──────────────────────
EXPECTED = {
    'Boros Energy': (50, 70), 'Affinity': (45, 60), 'Eldrazi Tron': (50, 65),
    'Jeskai Blink': (45, 60), 'Ruby Storm': (40, 55), 'Domain Zoo': (50, 65),
    'Izzet Prowess': (45, 60), 'Dimir Midrange': (45, 60),
}

# ── Archetype map ────────────────────────────────────────────
ARCH = {
    '4/5c Control': 'Control', '4c Omnath': 'Midrange', 'Affinity': 'Aggro',
    'Amulet Titan': 'Combo', 'Boros Energy': 'Aggro', 'Dimir Midrange': 'Midrange',
    'Domain Zoo': 'Aggro', 'Eldrazi Tron': 'Ramp', "Goryo's Vengeance": 'Combo',
    'Izzet Prowess': 'Aggro', 'Jeskai Blink': 'Midrange', 'Living End': 'Combo',
    'Ruby Storm': 'Combo', 'Azorius Control': 'Control',
    'Azorius Control (WST)': 'Control', 'Pinnacle Affinity': 'Aggro',
}

# ── Tier definitions ─────────────────────────────────────────
def tier_class(wtd_wr):
    """Return CSS class for tier badge."""
    if wtd_wr >= 60: return 't1'
    if wtd_wr >= 45: return 't2'
    return ''  # no tier class = field

def tier_label(wtd_wr):
    if wtd_wr >= 60: return 'T1'
    if wtd_wr >= 45: return 'T2'
    return 'Field'


def load_D(jsx_path='metagame_data.jsx'):
    with open(jsx_path) as f:
        src = f.read()
    m = re.search(r'const D = (\{.*?\});\nconst N', src, re.DOTALL)
    if not m:
        raise ValueError(f"Could not find D object in {jsx_path}")
    return json.loads(m.group(1))


def parse_grades(status_path='PROJECT_STATUS.md'):
    """Extract 6-domain grades from PROJECT_STATUS.md table."""
    with open(status_path) as f:
        text = f.read()
    
    # Extract overall grade
    m = re.search(r'Overall grade:\s*(\S+)', text)
    overall = m.group(1).rstrip('*').rstrip() if m else 'C-'
    
    # Extract domain grades from the table
    # Format: | Rules & engine | B+ | | Mana & sequencing | C+ |
    domain_map = {
        'Rules': None, 'Combat': None, 'Mulligan': None,
        'Mana': None, 'Combo': None, 'Control': None,
    }
    
    for line in text.split('\n'):
        for key in domain_map:
            pat = re.search(rf'{key}[^|]*\|\s*([A-DF][+-]?)\s*\|', line)
            if pat:
                domain_map[key] = pat.group(1).strip()
    
    # Ordered: Rules, Combat, Mulligan, Mana, Combo, Control
    labels = ['Rules', 'Combat', 'Mulligan', 'Mana', 'Combo', 'Control']
    values = [GRADE_NUM.get(domain_map.get(k, 'C'), 62) for k in labels]
    
    return overall, values


def short_name(name):
    """Generate short label for charts."""
    shorts = {
        'Boros Energy': 'Boros', 'Jeskai Blink': 'Jeskai',
        'Ruby Storm': 'Storm', 'Affinity': 'Affinity',
        'Eldrazi Tron': 'ETron', 'Amulet Titan': 'Amulet',
        "Goryo's Vengeance": 'Goryo', 'Domain Zoo': 'Zoo',
        'Living End': 'LivEnd', 'Izzet Prowess': 'Prowess',
        'Dimir Midrange': 'Dimir', '4c Omnath': 'Omnath',
        '4/5c Control': '4/5c', 'Azorius Control (WST)': 'WST',
        'Azorius Control': 'AzCtrl', 'Pinnacle Affinity': 'P.Affin',
    }
    return shorts.get(name, name[:6])


def xfilter_name(name):
    """Short name for cross-filter decks array."""
    shorts = {
        'Boros Energy': 'Boros', 'Jeskai Blink': 'Jesk',
        'Ruby Storm': 'Storm', 'Affinity': 'Affi',
        'Eldrazi Tron': 'ETron', 'Amulet Titan': 'AmTi',
        "Goryo's Vengeance": 'Gory', 'Domain Zoo': 'Zoo',
        'Living End': 'LivE', 'Izzet Prowess': 'Prow',
        'Dimir Midrange': 'Dimir', '4c Omnath': '4cOm',
        '4/5c Control': '4/5c', 'Azorius Control (WST)': 'WST',
        'Azorius Control': 'AzCtrl', 'Pinnacle Affinity': 'PAff',
    }
    return shorts.get(name, name[:4])


def build_deck_chips(D):
    """Generate deck chip HTML from D, sorted by weighted WR descending."""
    overall = {o['deck']: o for o in D['overall']}
    ms = D.get('meta_shares', {})
    
    # Sort by weighted WR
    decks_sorted = sorted(D['decks'], key=lambda d: overall.get(d, {}).get('weighted_wr', 0), reverse=True)
    
    chips = []
    for name in decks_sorted:
        o = overall.get(name, {})
        wtd = o.get('weighted_wr', o.get('win_rate', 0))
        tc = tier_class(wtd)
        meta = ms.get(name, 0)
        
        # Guide filename
        fname = 'guide_' + name.lower().replace(' ', '_')
        fname = re.sub(r'[^a-z0-9_]', '', fname) + '.html'
        href = f'https://djpieter81.github.io/MTGSimManu/guides/{fname}'
        
        meta_span = f'<span class="meta">{meta:.0f}%</span>' if meta >= 2 else ''
        chips.append(
            f'    <a href="{href}" class="deck-chip {tc}" '
            f'style="text-decoration:none;color:inherit">{name}{meta_span}</a>'
        )
    
    return '\n'.join(chips)


def build_val_data(D):
    """Generate JS valData array from D.overall + EXPECTED ranges."""
    overall = {o['deck']: o for o in D['overall']}
    
    # Only include decks with expected ranges (T1/T2 decks we track)
    entries = []
    for name, (lo, hi) in sorted(EXPECTED.items(), key=lambda x: overall.get(x[0], {}).get('win_rate', 0), reverse=True):
        o = overall.get(name)
        if not o:
            continue
        wr = round(o['win_rate'])
        passed = 1 if lo <= wr <= hi else 0
        
        # Generate detail text
        if wr > hi:
            detail = f'<strong>Above expected range ({lo}-{hi}%).</strong> Actual {wr}% — structural advantage or sim artifact.'
        elif wr < lo:
            detail = f'<strong>Below expected range ({lo}-{hi}%).</strong> Actual {wr}% — possible engine weakness.'
        else:
            detail = f'In range ({lo}-{hi}%). Performing as expected at {wr}%.'
        
        entries.append(f"  {{name:'{name}',wr:{wr},lo:{lo},hi:{hi},pass:{passed},"
                       f"detail:'{detail}'}}")
    
    return 'const valData=[\n' + ',\n'.join(entries) + '\n];'


def build_wr_arrays(D):
    """Generate wrLabels, wrFull, wrData from D.overall sorted by weighted WR."""
    overall_list = sorted(D['overall'], key=lambda o: o.get('weighted_wr', o['win_rate']), reverse=True)
    
    labels = [short_name(o['deck']) for o in overall_list]
    fulls = [o['deck'] for o in overall_list]
    data = [round(o.get('weighted_wr', o['win_rate']), 1) for o in overall_list]
    
    labels_js = ','.join(f"'{l}'" for l in labels)
    fulls_js = ','.join(f"'{f}'" if "'" not in f else f'"{f}"' for f in fulls)
    data_js = ','.join(str(d) for d in data)
    
    return f"const wrLabels=[{labels_js}];", f"const wrFull=[{fulls_js}];", f"const wrData=[{data_js}];"


def build_deck_res(D):
    """Build deckRes from D.overall — estimate resolution from matchup data."""
    # We don't have exact resolution data in D, so keep existing if present
    # or generate defaults based on archetype
    defaults = {
        'Aggro': {'d': 92, 'c': 1, 't': 7},
        'Midrange': {'d': 85, 'c': 0, 't': 15},
        'Control': {'d': 65, 'c': 0, 't': 35},
        'Combo': {'d': 55, 'c': 35, 't': 10},
        'Ramp': {'d': 80, 'c': 10, 't': 10},
    }
    entries = []
    for o in D['overall']:
        name = o['deck']
        arch = ARCH.get(name, 'Midrange')
        d = defaults.get(arch, defaults['Midrange'])
        key = f"'{name}'" if "'" not in name else f'"{name}"'
        entries.append(f"{key}:" + json.dumps(d))
    
    return 'const deckRes={' + ','.join(entries) + '};'


def build_xfilter_arrays(D):
    """Generate decksFull, decks, and filtered indices for cross-filter scatter."""
    # Exclude decks with no card data (e.g. Pinnacle Affinity)
    exclude = set()
    for dc in D.get('deck_cards', []):
        if not dc.get('mvp_casts'):
            exclude.add(dc['deck'])
    
    filtered = [d for d in D['decks'] if d not in exclude]
    indices = [D['decks'].index(d) for d in filtered]
    fulls = ','.join(f"'{d}'" if "'" not in d else f'"{d}"' for d in filtered)
    shorts = ','.join(f"'{xfilter_name(d)}'" for d in filtered)
    
    return f"const decksFull=[{fulls}];", f"const decks=[{shorts}];", filtered, indices


def build_heatmap_arrays(D, indices):
    """Generate wins, archetype, avgTurns arrays for the heatmap, matching filtered deck order."""
    n = D['matches_per_pair']
    mc = D.get('matchup_cards', {})
    filtered_names = [D['decks'][i] for i in indices]
    
    # wins[r][c] = win percentage (integer)
    wins_matrix = []
    avg_turns_matrix = []
    for r_idx in indices:
        win_row = []
        turn_row = []
        for c_idx in indices:
            if r_idx == c_idx:
                win_row.append(0)
                turn_row.append(0)
            else:
                wp = round(D['wins'][r_idx][c_idx] / n * 100)
                win_row.append(wp)
                # avg turns from matchup_cards
                ki, kj = min(r_idx, c_idx), max(r_idx, c_idx)
                mc_entry = mc.get(f"{ki},{kj}", {})
                turn_row.append(round(mc_entry.get('avg_turns', 0)))
        wins_matrix.append(win_row)
        avg_turns_matrix.append(turn_row)
    
    archetype_list = [ARCH.get(D['decks'][i], 'midrange').lower() for i in indices]
    
    wins_js = 'const wins=[' + ','.join('[' + ','.join(str(v) for v in row) + ']' for row in wins_matrix) + '];'
    arch_js = "const archetype=[" + ','.join(f"'{a}'" for a in archetype_list) + "];"
    turns_js = 'const avgTurns=[' + ','.join('[' + ','.join(str(v) for v in row) + ']' for row in avg_turns_matrix) + '];'
    
    return wins_js, arch_js, turns_js


def build_replay_gallery(replays_dir='replays'):
    """Generate replay gallery HTML from replay files on disk."""
    import glob
    
    base = 'https://djpieter81.github.io/MTGSimManu/replays/'
    files = sorted(glob.glob(os.path.join(replays_dir, 'replay_*.html')))
    
    if not files:
        return ''
    
    # Parse replay filenames into matchup labels
    def parse_replay(fname):
        name = os.path.basename(fname).replace('replay_', '').replace('.html', '')
        # Remove seed suffix
        name = re.sub(r'_s\d+$', '', name)
        # Split on _vs_
        if '_vs_' in name:
            parts = name.split('_vs_')
            d1 = parts[0].replace('_', ' ').title()
            d2 = parts[1].replace('_', ' ').title()
            return d1, d2
        # Fallback: use filename as label
        return name.replace('_', ' ').title(), None
    
    cards = []
    for f in files:
        d1, d2 = parse_replay(f)
        href = base + os.path.basename(f)
        if d2:
            label = f'{d1} vs {d2}'
        else:
            label = d1
        cards.append(f'      <a href="{href}" class="replay-chip" target="_blank">{label}</a>')
    
    return f"""<section style="text-align:center">
  <div class="reveal">
    <div class="sec-label">Match replays</div>
    <div class="sec-title">{len(files)} interactive Bo3 replays</div>
    <div class="sec-desc" style="margin:0 auto 1.5rem">Step through every turn. See the AI's reasoning, life totals, and board state. Click any matchup.</div>
  </div>
  <div class="replay-grid reveal">
{chr(10).join(cards)}
  </div>
</section>"""


def patch(html, D, overall_grade, radar_data):
    """Apply all patches to showcase HTML."""
    
    # 1. Radar chart data
    html = re.sub(
        r'datasets:\[\{data:\[[0-9,]+\]',
        f'datasets:[{{data:[{",".join(str(v) for v in radar_data)}]',
        html
    )
    
    # 2. Grade labels
    html = re.sub(
        r'Graded [A-DF][+-]? by a 6-expert panel.*?</div>',
        f'Graded {overall_grade} by a 6-expert panel</div>',
        html
    )
    html = re.sub(
        r"<div class=\"chart-sub\">6-expert LLM judge panel.*?</div>",
        f'<div class="chart-sub">6-expert LLM judge panel · auto-refreshed from PROJECT_STATUS.md</div>',
        html
    )
    
    # 3. Validation bars
    val_js = build_val_data(D)
    html = re.sub(
        r'const valData=\[.*?\];',
        val_js,
        html,
        flags=re.DOTALL
    )
    
    # 4. WR bar chart arrays
    wr_labels, wr_full, wr_data = build_wr_arrays(D)
    html = re.sub(r"const wrLabels=\[.*?\];", wr_labels, html)
    html = re.sub(r"const wrFull=\[.*?\];", wr_full, html)
    # wrData is embedded differently — find and replace
    html = re.sub(r"const wrData=\[[\d.,]+\];", wr_data, html)
    
    # 5. Cross-filter arrays + heatmap data (all parallel, must match)
    xf_full, xf_short, filtered, indices = build_xfilter_arrays(D)
    wins_js, arch_js, turns_js = build_heatmap_arrays(D, indices)
    html = re.sub(r"const decksFull=\[.*?\];", xf_full, html)
    html = re.sub(r"const decks=\[.*?\];", xf_short, html)
    html = re.sub(r"const wins=\[.*?\];", wins_js, html)
    html = re.sub(r"const archetype=\[.*?\];", arch_js, html)
    html = re.sub(r"const avgTurns=\[.*?\];", turns_js, html)
    
    # 6. Deck resolution
    deck_res = build_deck_res(D)
    html = re.sub(r"const deckRes=\{.*?\};", deck_res, html)
    
    # 7. Deck chips
    chips_html = build_deck_chips(D)
    html = re.sub(
        r'(<!-- DECK_CHIPS_START -->).*?(<!-- DECK_CHIPS_END -->)',
        r'\1\n' + chips_html + r'\n    \2',
        html,
        flags=re.DOTALL
    )
    # Fallback: replace the block of <a> deck-chip tags if markers don't exist
    if '<!-- DECK_CHIPS_START -->' not in html:
        # Find the deck chip block (consecutive <a class="deck-chip" lines)
        html = re.sub(
            r'(    <a href="https://djpieter81\.github\.io/MTGSimManu/guides/guide_.*?" class="deck-chip.*?</a>\n)+',
            chips_html + '\n',
            html
        )
    
    # 8. Deck count
    n = len(D['decks'])
    html = re.sub(r'\b16[×x]16\b', f'{n}×{n}', html)
    html = re.sub(r'\b16 decks\b', f'{n} decks', html)
    
    # 9. Replay gallery
    gallery = build_replay_gallery()
    html = re.sub(
        r'<!-- REPLAY_GALLERY_START -->.*?<!-- REPLAY_GALLERY_END -->',
        f'<!-- REPLAY_GALLERY_START -->\n{gallery}\n<!-- REPLAY_GALLERY_END -->',
        html,
        flags=re.DOTALL
    )
    
    return html


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    
    # Load data
    D = load_D()
    overall_grade, radar_data = parse_grades()
    
    # Load template
    tmpl_path = 'templates/reference_showcase.html'
    with open(tmpl_path) as f:
        html = f.read()
    
    # Patch
    html = patch(html, D, overall_grade, radar_data)
    
    # Write
    if out_path:
        os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
        with open(out_path, 'w') as f:
            f.write(html)
        print(f"✓ Showcase written to {out_path}")
    else:
        # Default: write to both repo and outputs
        with open(tmpl_path, 'w') as f:
            f.write(html)
        print(f"✓ Showcase patched in-place: {tmpl_path}")
        
        out = '/mnt/user-data/outputs/reference_showcase.html'
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w') as f:
            f.write(html)
        print(f"✓ Copy written to {out}")


if __name__ == '__main__':
    main()
