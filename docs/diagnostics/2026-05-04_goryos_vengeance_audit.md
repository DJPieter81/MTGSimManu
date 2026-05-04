---
title: Goryo's Vengeance combo audit (Phase K, methodology v1)
status: active
priority: secondary
session: 2026-05-04
depends_on:
  - docs/design/2026-05-04_modern_combo_audit_methodology.md
tags:
  - audit
  - combo
  - phase-k
  - goryos
summary: >
  9-question audit of Goryo's Vengeance (sim WR 9.6%, expected ~45%, |Δ|=35pp).
  6 findings: 1 Class A (Wear // Tear split-card CMC), 2 Class B (only 7
  legendary reanimation targets vs canonical 8-12; no Atraxa, Grand Unifier),
  1 Class C (Thoughtseize/Faithful Mending consistently held at priority
  while combo never assembles — turn-1 disruption never fires), 1 Class E
  (no graveyard-growth heuristic to trigger Faithful Mending self-discard
  before reanimator drawn), 1 Class H (zero MB graveyard hate from opponents
  is good for Goryo, but the deck nevertheless cannot win — root cause is
  internal). Class B is the only same-day-actionable finding; Class C is
  the dominant cause but is a deferred AI fix.
---

# Goryo's Vengeance combo audit

## Context

- Live WR (matrix snapshot, N=30): **9.6%**
- Expected band: 40–55%
- |Δ|: **35pp** — largest absolute gap among the 5 audit targets.
- Reproducibility: vs Boros Energy 0/10, vs Affinity 0/10, vs Dimir 0/10
  (seed 50000, BO3, MB only verbose run; SB does not engage).

## Q1 — Card data (Class A)

Verified `engine/card_database.py` extraction against `ModernAtomic.json`
for all 19 unique non-land cards in the mainboard + sideboard.

**Findings:**

- 18 cards: clean. CMC, supertype (Legendary), oracle keywords match
  printed values. Notably Archon of Cruelty (`SUPERTYPE_CORRECTIONS` in
  `engine/card_database.py:973` already restores the stripped Legendary
  supertype that's missing from MTGJSON), Griselbrand (CMC 8, Legendary,
  flying+lifelink), Goryo's Vengeance (CMC 2, B-cost, "target legendary
  creature card") all match. `tests/test_archon_of_cruelty_is_legendary.py`
  guards the Archon correction.
- **Class A — Wear // Tear (sideboard):** sim CMC = **2**, oracle CMC = **3.0**.
  MTGJSON publishes the *fused* CMC (sum of both halves) on a split card;
  sim records only the front-face CMC. The downstream effect handler
  (`engine/card_effects.py:1309 wear_tear_resolve`) destroys BOTH an
  artifact AND an enchantment for the cost of 2 — i.e. it is pricing the
  fuse cast at the cost of one half. This is generous to the player
  (Class A leaning F), not stingy. Low priority for Goryo's WR; affects
  every deck that runs the card (Boros, Ruby Storm, Domain Zoo,
  Goryo's SB, AzCon SB, Pinnacle Aff SB indirectly).

**Verdict:** 1 Class A finding (cosmetic; player-favourable).

## Q2 — Tier-1 conformance (Class B)

Compared `decks/modern_meta.py "Goryo's Vengeance"` against the modern
metagame archetype canonical list (Reanimator combo; reference: GoryoVengeance
top-8 lists Apr 2026, e.g. MtgGoldfish.com archetype page).

Current 60 mainboard:

```
4 Goryo's Vengeance, 4 Griselbrand, 3 Archon of Cruelty, 4 Solitude,
4 Ephemerate, 4 Faithful Mending, 4 Thoughtseize, 3 Inquisition of Kozilek,
2 Undying Evil, 4 Unburial Rites, 2 Leyline of Sanctity, 22 lands
```

**Findings:**

- **Class B-1 — only 7 legendary reanimation targets.** Canonical Modern
  Goryo's lists run 4 Atraxa, Grand Unifier + 4 Griselbrand + 1–2
  Archon = 9–10 legendary targets. The current list runs 0 Atraxa.
  Atraxa is the deck's strongest target in the post-MH3 metagame (CMC 7
  for a 7/7 flying-vigilance-deathtouch-lifelink that draws ~6 cards on
  ETB) and the reason the archetype is competitive at all. Without
  Atraxa the deck wins via a 7/7 lifelink (Griselbrand) but has no
  card-advantage backup.

  - Decklist edit: **+4 Atraxa, Grand Unifier**, **−4 Solitude** (or
    −2 Solitude / −1 Inquisition / −1 Leyline of Sanctity to keep
    interaction). Solitude is a 5-mana evoke creature that conflicts
    with reanimator's mana plan: T2-T3 mana is reserved for
    Faithful Mending → Goryo's Vengeance.
  - Atraxa is in `ModernAtomic.json` (CMC 7, legendary, has all 4
    keywords; verified in this audit).

- **Class B-2 — only 4 Faithful Mending.** Canonical lists run 4
  Faithful Mending **plus** 4 Lightning Axe / Bone Shards / Thrilling
  Discovery for additional self-discard. Current list only has 4
  Faithful Mending. The mulligan log (verbose Goryo's vs Affinity
  s=50000) shows 2 of 3 mulligans were forced because the 7-card hand
  was missing the enabler bucket. With ~7 self-discard slots vs the
  canonical ~10–12, opening hands frequently lack the discard outlet.
  However — the simulator currently only models *one* self-discard
  card class per archetype, so this finding is shadowed by Class C
  unless a generalised "self-discard outlet" tag is added. Defer.

**Verdict:** 1 actionable Class B finding (B-1: +4 Atraxa, −4 Solitude).

## Q3 — Strategy/preamble interaction (Class C)

Verbose trace (Goryo's vs Affinity s=50000, post-mull-to-5, opening
`Griselbrand, Thoughtseize, Flooded Strand, Marsh Flats, Unburial Rites`):

- **T1:** plays Marsh Flats, cracks for Godless Shrine. Has B available.
  **Does NOT cast Thoughtseize.** Passes priority. (Affinity goes wide.)
- **T2:** plays Flooded Strand → Watery Grave. Now has BB. Still does NOT
  cast Thoughtseize. Passes priority.
- **T3:** draws Thoughtseize (now 2 Thoughtseize in hand). Still passes.
- **T4:** dies to Sojourner's Companion + Cranial Plating attack.

This is the **dominant root cause** of the 9.6% WR. The AI never fires
Thoughtseize because:

1. The gameplan (`decks/gameplans/goryos_vengeance.json`) declares
   `card_priorities.Thoughtseize: 18.0` for the DISRUPT goal AND
   `8.0` for the FILL_RESOURCE goal — but the GoalEngine
   selects FILL_RESOURCE first (no Griselbrand/Archon in graveyard
   yet) and the lower priority loses to "pass" in the EV scoring
   layer.
2. With **no creature in graveyard**, the FILL_RESOURCE goal scores
   Faithful Mending highly, but the 5-card hand has no Faithful
   Mending — so the goal scores no plays high enough to fire.

This is **Class C** — shared `_execute_turn` selects the
FILL_RESOURCE goal, which then has nothing to fire on the 5-card
hand, and the AI defaults to "pass" instead of falling back to the
(also high-priority) DISRUPT goal's Thoughtseize.

**Defer to AI-fix dispatch.** The fix is a goal-fallback rule in
`ai/gameplan.py` or `ai/turn_planner.py`: when the selected goal has
no executable plays this turn, evaluate the *next-priority* goal's
plays before passing. This is generic — affects every deck whose
goal selection mis-prioritises (Storm if no rituals, Living End if
no cycler, Amulet Titan if no Amulet).

## Q4 — Single-deck gates (Class D)

```bash
grep -rn "active_deck ==\|deck_name ==\|deck in (\|self\.deck\.name ==" \
    ai/ engine/ decks/ --include='*.py'
```

**Result:** 0 hits. Modern's AI is fully tag/oracle-driven for deck
identification. **Verdict:** clean.

## Q5 — Heuristic cardinality (Class E)

`ai/bhi.py` (559 lines) — Bayesian opponent inference. Searched for
graveyard-growth or cards-cast proxies that might miscount the deck's
self-discard:

- `ai/combo_evaluator.py:293`: `storm_count = me.spells_cast_this_turn`
  — relevant for Storm/Pinnacle, not Goryo's.
- `ai/scoring_constants.py:895`: "additional cycling EV when graveyard
  creature count < 3" — relevant for Living End cyclers, not Goryo's.
- No graveyard-growth heuristic specifically for reanimator decks.

**Class E-1 (Goryo's-specific):** there is no heuristic that says
"Faithful Mending becomes higher EV when no reanimation target is in
graveyard yet." The only signal is the FILL_RESOURCE goal's
`resource_zone: graveyard, resource_min_cmc: 5`, but this goal currently
selects without a fallback (see Q3). Defer with Class C.

## Q6 — Rule strictness (Class F)

Re-read oracle text for each win-condition card and engine handler:

- **Goryo's Vengeance:** oracle "Return target legendary creature card
  from your graveyard to the battlefield. It gains haste. Exile it at
  the beginning of the next end step."
  - `engine/card_effects.py` reanimation handler verified to enforce
    `Supertype.LEGENDARY` target restriction and the end-step exile
    trigger. Clean.
- **Unburial Rites:** "Return target creature card from your graveyard
  to the battlefield. … Flashback {3}{W}{B}." Handler returns ANY
  creature card, no legendary qualifier (validated by
  `tests/test_goryos_decklist_unburial_rites.py:test_unburial_rites_can_target_griselbrand`).
- **Faithful Mending:** "Draw two cards, then discard two cards.
  Flashback {3}{U}." Handler verified.

**Verdict:** clean.

## Q7 — Fetch validity (Class G)

Per the Q7 script in this audit's parent doc:

- Marsh Flats x4 → {Plains, Swamp}, valid_targets_in_deck=7 (Hallowed
  Fountain + Godless Shrine + Watery Grave + Plains + Swamp via
  shock-land subtypes). OK.
- Flooded Strand x4 → {Island, Plains}, valid_targets_in_deck=6. OK.

**Verdict:** clean.

## Q8 — Bo1 hate-card density (Class H)

Goryo's-specific MB graveyard hate from opponents (Bo1 default):

| Opponent | MB graveyard-hate cards | Count |
|---|---|---|
| Boros Energy | Surgical Extraction (SB only) | MB=0 |
| Dimir Midrange | Drown in the Loch / Dauthi Voidwalker | MB=3 |
| 4c Omnath | Endurance / Bojuka Bog | MB=2-3 |
| Affinity | Tormod's Crypt / Relic of Progenitus | MB=0 |

Goryo's loses 0/10 to Boros and Affinity neither of which has MB
graveyard hate. **Class H does NOT explain Goryo's underperformance**
(if anything it should overperform vs MB-clean opponents). The bug is
internal (Class C, Q3).

**Verdict:** No actionable Class H finding from Goryo's perspective;
the underperformance is endogenous.

## Q9 — Hand-rolled cantrip resolution (Class I)

```bash
grep -rn "player.draw(1)\|drawCard\|self.hand.append.*deck.pop" \
    decks/ ai/ --include='*.py'
```

**Result:** 0 hits. **Verdict:** clean.

## Summary

| Class | Count | Actionable now? |
|---|---|---|
| A — Card data | 1 (Wear // Tear split-card CMC) | low priority |
| B — Decklist construction | 2 (no Atraxa; only 4 self-discard slots) | **B-1 yes** |
| C — Strategy/preamble | 1 (goal fallback when selected goal has no plays) | defer (AI fix) |
| D — Single-deck gates | 0 | n/a |
| E — Heuristic cardinality | 1 (Faithful Mending EV without grave-growth signal) | defer with C |
| F — Rule strictness | 0 | n/a |
| G — Fetch validity | 0 | n/a |
| H — Bo1 hate density | 0 (does not apply to Goryo's underperformance) | n/a |
| I — Hand-rolled cantrips | 0 | n/a |

**Top finding:** Class C dominates (AI fails to cast Thoughtseize on T1
because the GoalEngine selects FILL_RESOURCE which has no plays on the
mulled-to-5 hand, and falls through to "pass" instead of evaluating
the DISRUPT goal). This is deferred as an AI fix — the methodology
keeps Class C/D/E for a follow-up dispatch.

**Same-day fix-PR candidate:** Class B-1 (+4 Atraxa MB, −4 Solitude or
balanced equivalent) is the single decklist-data edit this audit can
land. Expected lift: 5–15pp (Atraxa as a card-advantage backup target
fixes the "Griselbrand-only" curse but does not address the AI never
casting Thoughtseize in the first place; absent a Class C fix the
ceiling is the % of games where the AI does happen to fill the
graveyard via natural draws).

## Fix-PR list

- **PR-K1 (Class B-1):** `claude/fix-classB-goryos-add-atraxa` — +4
  Atraxa, Grand Unifier MB, −4 Solitude (or balanced cut). Test:
  `tests/test_goryos_decklist_atraxa.py` asserts Atraxa count ≥ 4 and
  total mainboard = 60.

## Deferred (Class C/D/E, document only)

- **C-1 + E-1:** GoalEngine selects FILL_RESOURCE on T1 with empty
  grave; if the goal cannot play a card, fall back to the next-priority
  goal's plays before passing. Deferred to AI-fix dispatch.

- **B-2:** Add a generalised "self-discard outlet" tag in
  `ai/predicates.py` so the mulligan scorer counts Faithless Looting,
  Lightning Axe, Thrilling Discovery, etc. as Faithful-Mending-equivalent
  enablers. Enables expanding the deck's discard slots without
  per-card tuning. Deferred.
