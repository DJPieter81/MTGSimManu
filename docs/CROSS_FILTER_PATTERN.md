# Cross-Filter Pattern for MTGSim Showcases

Implemented in Legacy showcase (`mtgsimclaude_showcase.html`). Apply same pattern to Modern.

## Architecture

One global `selectDeck(name)` function drives all views. Every clickable element calls it. One `clearSelection()` resets everything. Click empty space on any Chart.js chart = clear.

## Rules: Highlight vs Swap

| Chart type | On deck select | Reason |
|------------|---------------|--------|
| WR bar chart | **Highlight** — gold border on selected, dim others to 5% opacity | Same dimension, different emphasis |
| Turn length by archetype | **Highlight** — gold border on matching archetype bars, dim others | Same dimension, archetype grouping |
| Matchup spread | **Swap** — replace data with selected deck's per-opponent WRs | Different data per deck |
| Win resolution doughnut | **Swap** — replace data with selected deck's damage/combo/timeout split | Different data per deck |
| Heatmap | **Structural** — gold CSS highlight on row + column | Not a Chart.js chart |
| Deck cloud | **Structural** — gold background on selected chip, show profile card | Not a Chart.js chart |

**Rule of thumb:** If clicking changes WHICH data you're looking at → swap. If clicking changes WHAT'S emphasized in the same data → highlight.

## Data needed

```javascript
// Per-deck win resolution (compute from sim)
const deckRes = {
  'Boros Energy': {d: 85.2, c: 3.1, t: 11.7},  // damage%, combo%, timeout%
  'Domain Zoo':  {d: 78.4, c: 8.2, t: 13.4},
  // ... for each heatmap deck
};

// Deck-to-archetype mapping (for turn length highlighting)
const deckArch = {
  'Boros Energy': 'aggro',
  'Domain Zoo': 'aggro', 
  'Amulet Titan': 'combo',
  // ...
};

// Archetype-to-turn-length-bar mapping
const archMatch = {
  'Combo v Aggro': ['combo','aggro'],
  'Aggro v Aggro': ['aggro'],
  'Combo v Midrange': ['combo','midrange'],
  // ...
};
```

## Chart.js onClick wiring

```javascript
// Store chart instances as globals
const wrChart = new Chart(..., {
  options: {
    onClick: (e, els) => {
      if (els.length) selectDeck(labels[els[0].index]);
      else clearSelection();
    }
  }
});

// Same pattern for spread, doughnut, turn length
```

## selectDeck(name) implementation

```javascript
let selectedDeck = null;
function selectDeck(name) {
  selectedDeck = name;
  
  // 1. WR chart: HIGHLIGHT
  wrChart.data.datasets[0].borderWidth = labels.map((_, i) => i === idx ? 3 : 1.5);
  wrChart.data.datasets[0].borderColor = labels.map((_, i) => i === idx ? GOLD : defaultBorder[i]);
  wrChart.data.datasets[0].backgroundColor = labels.map((_, i) => 
    i === idx ? 'rgba(184,148,30,.2)' : defaultBg[i].replace(/[\d.]+\)$/, '.05)')
  );
  wrChart.update();

  // 2. Spread: SWAP to deck's per-opponent WRs
  spreadChart.data.datasets[0].data = heatmapRow[deckIndex];
  spreadChart.update();

  // 3. Doughnut: SWAP to deck's win resolution
  resChart.data.datasets[0].data = [deckRes[name].d, deckRes[name].c, deckRes[name].t];
  resChart.update();

  // 4. Turn length: HIGHLIGHT matching archetype
  const arch = deckArch[name];
  turnChart.data.datasets[0].backgroundColor = archTypes.map((at, i) => {
    const a = archMatch[at] || [];
    return a.includes(arch) ? defaultTurnBg[i] : (defaultTurnBg[i] + '18');
  });
  turnChart.update();

  // 5. Heatmap: CSS gold highlight on row/col
  // 6. Deck cloud: CSS selected class on chip
}

function clearSelection() {
  // Reset all charts to defaults
  // Remove all CSS highlights
}
```

## CSS for structural highlights

```css
.deck-chip.selected { border-color: var(--gold); background: var(--gold-bg) !important; }
.heatmap td.hl-row { box-shadow: inset 0 2px 0 var(--gold), inset 0 -2px 0 var(--gold); }
.heatmap td.hl-col { box-shadow: inset 2px 0 0 var(--gold), inset -2px 0 0 var(--gold); }
.heatmap td.hl-row.hl-col { box-shadow: inset 0 0 0 2px var(--gold); }
.heatmap .dl.hl-label { color: var(--gold); font-weight: 700; }
```

## Doughnut click → select best deck for that win type

```javascript
// Click "Combo kill" segment → select deck with highest combo %
onClick: (e, els) => {
  if (!els.length) { clearSelection(); return; }
  const key = ['d','c','t'][els[0].index];
  let best = '', bestV = 0;
  for (const [d, v] of Object.entries(deckRes)) {
    if (v[key] > bestV) { bestV = v[key]; best = d; }
  }
  if (best) selectDeck(best);
}
```

## Key gotchas from Legacy implementation

1. **No scrollIntoView** in selectDeck — it jumps the page
2. **Declare heatmap data (hD, hV) before chart init** — Chart.js runs immediately, forward refs crash
3. **Store default colors** as arrays (`wrBgDef`, `wrBrDef`) — needed for clearSelection reset
4. **Click empty space = clear** — add `else clearSelection()` to every chart's onClick
5. **Fuzzy name matching** — deck names differ between charts (e.g. "UR Del" vs "UR Delver"), use startsWith matching

## GitHub Pages links

Update product card links to:
```
https://djpieter81.github.io/MTGSimManu/results/...
```
Not htmlpreview.github.io (chokes on large files).
