---
name: mtg-bo3-replayer-v2
description: Generate interactive Bo3 match replay HTML files from MTG simulation data. Use this skill whenever the user asks to replay a match, generate a play-by-play, debug a specific matchup, save a Bo3 game log, create an HTML replayer, or investigate why a deck wins/loses a specific matchup. Also triggers on "simulate a match", "show me a game between X and Y", "replay", "play-by-play", "Bo3", "best of 3", "game log", "match viewer", or any request to visually step through a simulated MTG match. Use this skill even if the user just says "run a match" or "show me how X beats Y" in the context of MTG sim work.
---

# MTG Bo3 Match Replayer

Generates a standalone interactive HTML replay of a best-of-3 MTG match.

## Quick Reference

```bash
python run_meta.py --bo3 "Deck A" "Deck B" --seed 55555 -o /mnt/user-data/outputs/replay.html
python run_meta.py --bo3 --list-decks
```

## Pipeline

1. Run: `run_meta.py --bo3 "Deck A" "Deck B" --seed NNNNN -o /mnt/user-data/outputs/descriptive_name.html`
2. Present with `present_files` + brief summary (winner, score, key turns)
3. To find underdog wins: loop seeds until the underdog wins, then save that seed

## HTML Viewer Design

Read `references/replay_css.css` for CSS and `references/component_patterns.html` for HTML examples.

### Design System (GitHub-dark)

| Token | Value |
|-------|-------|
| bg | `#0d1117` |
| surface | `#161b22` |
| border | `#30363d` |
| text | `#c9d1d9` |
| muted | `#8b949e` |
| P1 | `#58a6ff` (blue) |
| P2 | `#f85149` (red) |
| card pill text | `#e3b341` on `#21262d` |
| font | Segoe UI, system-ui |
| mono | Fira Code, Consolas |

### Page Structure

```
HEADER — gradient bg, deck names colored (P1 blue, P2 red), series score
GAME TABS — [Game 1 •] [Game 2 •] [Game 3] with colored winner dots
META LINE — "P1 is ON THE DRAW | Seed: 55555"
OPENING HANDS — two-column grid, card pills, colored left border per player
LIFE CHART — SVG line graph, both players, labeled points per turn
CONTROLS — [Expand All] [Collapse All] ↑↓ navigate Enter: toggle
TURNS — collapsible, each with:
  Turn header: turn#, player (colored), life totals, arrow toggle
  Hand: pill badges (gold text on dark)
  Plays: numbered steps with category badges + AI reasoning
  Combat detail: attackers, blockers, damage
  Board state: two-column grid, creature badges with P/T, land lists
RESULT — colored win banner, final life, turn length, remaining board
```

### Category Badges for Plays

| Class | Label | Color | Use |
|-------|-------|-------|-----|
| `cat-land` | LAND | green | Land drops |
| `cat-cast` | CAST | blue | Spell cast |
| `cat-draw` | DRAW | gray | Draw step |
| `cat-cantrip` | DIG | teal | Cantrips |
| `cat-combat` | COMBAT | red | Attacks/blocks |
| `cat-counter` | COUNTER | purple | Counterspells |
| `cat-ability` | ABILITY | orange | Triggers/activations |

### Per-Turn HTML Pattern

```html
<div class="turn bug" data-idx="1">
  <div class="turn-header">
    <div class="left">
      <span class="tnum bug">T2</span>
      <span class="player bug">DECK NAME</span>
      <span class="life">Life: <b>20</b> &nbsp;|&nbsp; Opp: 20</span>
    </div><span class="arrow">▶</span>
  </div>
  <div class="turn-body">
    <div class="section-label">Hand</div>
    <div class="hand-pills">
      <span class="pill">Card Name</span>
    </div>
    <div class="section-label">Plays</div>
    <div class="play">
      <span class="step">1.</span>
      <span class="cat-badge cat-cast">CAST</span>
      <span class="action">Cast: Ragavan (R)</span>
      <span class="reasoning">← T1 threat, generate mana</span>
    </div>
    <div class="section-label">Board State</div>
    <div class="board-grid">
      <div class="board-side bug">
        <h4>P1 — 1 lands</h4>
        <div class="board">
          <span class="creature-badge">Ragavan<span class="pt">2/1</span></span>
        </div>
        <div class="land-list">Stomping Ground</div>
      </div>
      <div class="board-side opp">
        <h4>P2 — 0 lands</h4>
        <div class="board"><span style="color:#484f58">no creatures</span></div>
        <div class="land-list">none</div>
      </div>
    </div>
  </div>
</div>
```

### Required JS (inline in template)

```javascript
// Game tab switching
document.querySelectorAll('.game-tab').forEach((tab, i) => {
  tab.onclick = () => {
    document.querySelectorAll('.game-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.game-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('game-' + i).classList.add('active');
  };
});
// Turn collapse/expand
document.querySelectorAll('.turn-header').forEach(h => {
  h.onclick = () => h.parentElement.classList.toggle('open');
});
// Expand/Collapse All
document.querySelectorAll('.controls button').forEach((btn, i) => {
  btn.onclick = () => {
    const panel = document.querySelector('.game-panel.active');
    panel.querySelectorAll('.turn').forEach(t => {
      i === 0 ? t.classList.add('open') : t.classList.remove('open');
    });
  };
});
// Keyboard navigation
document.addEventListener('keydown', e => {
  const turns = [...document.querySelectorAll('.game-panel.active .turn')];
  const cur = turns.findIndex(t => t.classList.contains('focused'));
  let next = cur;
  if (e.key === 'ArrowDown') next = Math.min(cur + 1, turns.length - 1);
  if (e.key === 'ArrowUp') next = Math.max(cur - 1, 0);
  if (e.key === 'Enter' && cur >= 0) turns[cur].classList.toggle('open');
  if (next !== cur || cur < 0) {
    turns.forEach(t => t.classList.remove('focused'));
    turns[Math.max(next, 0)].classList.add('focused');
    turns[Math.max(next, 0)].scrollIntoView({ block: 'nearest' });
  }
});
```

## Debugging Checklist

When a deck's WR is off, run a replay and check:
1. **Missing cards** — WARNING in output means placeholder (0/0, no abilities)
2. **Creatures not attacking** — 0/0 stats = power/toughness not loaded
3. **Combo not assembling** — key pieces never cast (check hand/plays)
4. **Bad AI** — check reasoning text for nonsensical EV scores

## Seed Reference

| Purpose | Seeds |
|---------|-------|
| Demo | 55555 |
| Matrix | 40000–49999 |
| H2H | 50000–59999 |
| Debug | 80000+ |

## Auto-Trigger After Sims

After every matrix sim or dashboard merge, auto-generate replays for:
1. **Outlier decks** (WR outside expected range) → replay worst + best matchup
2. **G1→match swing ≥20pp** → replay to see SB transformation
3. **0 comebacks** in 10+ matches → diagnose unwinnable states
4. **New deck added** → replay vs T1 field (Boros, Jeskai, Affinity)

Pipeline: identify targets from metagame_data.jsx → `run_meta.py --bo3` → `build_replay.py` → commit logs to `replays/`.

See CLAUDE.md "Post-Sim Replay Generation" section for the full Python script.
