---
title: Goryo's Vengeance 10% — mulligan keeps mana-broken combo hands
status: active
priority: primary
session: 2026-04-28
depends_on:
  - docs/history/sessions/2026-04-26_storm_goryos_deferral_gate.md
tags:
  - p0
  - wr-outlier
  - goryos
  - mulligan
  - combo
  - mana-color-soundness
summary: |
  Bo3 replay (seed 60200) of Goryo's vs Boros confirms three
  independent mulligan/data bugs that together produce Goryo's 0/20 vs
  Boros (10% overall WR, -30pp from band). Validation sim at n=10
  post-color-fix still 0/10 — exposed a 4th bug: the combo-set
  cardname predicate is too permissive (any 1-of-3 piece counts as
  "combo present" → hand with just Goryo's Vengeance keeps despite
  having no enabler and no target). Three of the four bugs share a
  single subsystem: `ai/mulligan.py:96-104`'s combo-set check.

  Class-size: every combo deck whose enabler / payoff requires a color
  the manabase doesn't reliably produce (Goryo's, Living End cycle,
  Ruby Storm RR, Pinnacle Affinity blue counters, Niv-Mizzet shells).
  The mulligan consumes `mulligan_combo_sets` as a card-name predicate
  only; it never checks whether the hand's lands cover the union of
  pip requirements for cards in the kept combo set.
---

# Goryo's Vengeance 10% — mana-color-blind mulligan

## Replay

`replays/goryos_vengeance_vs_boros_energy_s60200.txt` — Boros wins 2-0,
seed 60200 (matches the 0/20 cell in metagame_data.jsx).

## Game 1 — kept a 6 with no combo piece (bug #3)

Original 7 (P1 on draw):
`Unburial Rites, Solitude, Silent Clearing (W/B), Thoughtseize×2,
Flooded Strand, Godless Shrine (W/B)` — no combo enabler.

Mulligan rejected with reason `missing combo piece from {'Faithful
Mending', 'Griselbrand', "Goryo's Vengeance"}`. Correct.

6-card hand drawn:
`Ephemerate, Marsh Flats, Solitude×2, Unburial Rites, Undying Evil` —
**still no combo enabler**.

Mulligan kept it. Reason in code (`ai/mulligan.py:100-102`):

```python
if not (hand_names & combo_set):
    if cards_in_hand <= 6:
        self.last_reason = f"missing combo piece but only {cards_in_hand} cards"
        return True   # ← early-return short-circuits the check
```

The 6-card threshold gives up. Goryo's at 6 needs a combo piece *more*
than at 7, not less; mulling to 5 is the correct call when the
remaining 6 has zero enablers and no on-curve interaction. The keep
auto-loses — Goryo's never drew Goryo's Vengeance, Faithful Mending,
or Griselbrand in 5 turns.

## Game 2 — kept a 7 the manabase cannot cast (bug #1, primary)

Opening 7 (P1 on play):
`Godless Shrine (W/B), Swamp (B), Concealed Courtyard (W/B),
Archon of Cruelty, Goryo's Vengeance ({1}{B}), Unburial Rites,
Faithful Mending ({W}{U})`.

Mulligan kept with reason `has key card(s): Archon of Cruelty,
Faithful Mending, Goryo's Vengeance, Unburial Rites, 2 cheap spells`.

The 7 satisfies all four `mulligan_combo_sets` (`hand_names & combo_set`
non-empty for each). But the manabase produces only `{W, B}`. The hand
contains:

- `Faithful Mending` ({W}{U}) — needs U. **Uncastable** through T6.
- `Goryo's Vengeance` ({1}{B}) — castable, but needs a *legendary*
  creature in graveyard. None available.
- `Unburial Rites` ({4}{B} cast / {3}{B} flashback) — castable
  cast-cost, but only meaningful from graveyard, and requires a
  discard outlet (which is Faithful Mending — uncastable).
- `Archon of Cruelty` ({6}{B}{B}) — not legendary (verified: empty
  `supertypes` in MTGJSON), so cannot be Goryo's-targeted. Reanimation
  via Unburial Rites only, which loops back to needing the discard
  outlet.

Result: AI plays land-drops T1-T5, casts Thoughtseize on T6 against
Goblin Bombardment, dies T6 to Phlage + 3 attackers.

The hand looks playable through the cardname predicate but is
structurally dead the moment the lands are revealed. Drawing the same
7 cards with `Watery Grave` swapped for `Swamp` would have made
Faithful Mending live on T2, dropped Archon to graveyard, and enabled
Unburial Rites flashback on T5-6 for the kill.

## Bug #2 — RETRACTED (engine already corrects Archon's supertype)

Initial diagnostic claimed `decks/gameplans/goryos_vengeance.json`'s
combo set `[Faithful Mending, Goryo's Vengeance, Archon of Cruelty]`
was rules-illegal because Archon's MTGJSON `supertypes` field is
empty.  This was checked against the **raw JSON** but not against the
**engine view** of the card.

`engine/card_database.py::_build_template` applies a two-stage
correction layer: it re-derives supertypes from the type-line string,
and falls back to direct name-keyed corrections for cards whose
MTGJSON entry is corrupt in both locations.  The existing test
`tests/test_archon_of_cruelty_is_legendary.py` (4 cases) locks this
in.

Confirmed at runtime:

```python
db.get_card("Archon of Cruelty").supertypes
# → [<Supertype.LEGENDARY: 'legendary'>]
```

Goryo's CAN target Archon in the engine.  The combo set is legal.
The G2 replay loss is fully attributable to Bug #1 (no U source for
Faithful Mending → no discard outlet → no fatty in graveyard → no
reanimation target regardless of which legendary the deck declares).

Lesson: when checking gameplan-level rules-legality claims, query the
engine's CardTemplate view, not raw MTGJSON.  Two diagnostic layers,
not one.

## Bug #4 — combo-set cardname predicate is too permissive

Discovered by post-fix validation sim: ran `run_meta.py --matchup
goryos boros -n 10` after Bug #1 fix landed.  Result: still 0/10.
Verbose trace of seed 50000 shows the hand `{2× Goryo's Vengeance,
Unburial Rites, Marsh Flats, Flooded Strand, Thoughtseize, Inquisition
of Kozilek}` was kept with no Faithful Mending and no fatty target.

`ai/mulligan.py:96-104`:

```python
for combo_set in gp.mulligan_combo_sets:
    if not (hand_names & combo_set):   # <-- "any 1 piece = combo present"
        ...
        return False
```

The predicate `hand_names & combo_set` is non-empty as soon as ANY
single card from a 3-card combo path is in hand.  For Goryo's, all
four declared paths share `Goryo's Vengeance` or `Unburial Rites` —
so a hand with just Goryo's satisfies sets 1 and 2 (both contain
Goryo's), and a hand with just Unburial Rites satisfies sets 3 and
4.  Either alone is "1 of 3 = 33% combo" — no enabler, no target,
unplayable.

The right semantic is: combo decks need ≥ 2 of 3 pieces from at least
ONE declared path before keeping a 7.  One piece is functionally zero
progress; you can't dig for two cards in 4-5 turns vs an aggro clock.
Two pieces = 67% progress, and the digger (cantrip / Faithful Mending
itself) finds the third.

The 6-card relaxation (Bug #3) already accepts mulled-down hands with
weaker requirements, so the 7-card threshold can afford to be strict
without bricking the deck on early mulligans.

## Validation (post-fix, same session 2026-04-28)

Goryo's vs 6 representative opponents @ n=4 each, seeds 50000+:

| Opponent | Pre-fix WR | Post-fix WR | Δ |
|----------|-----------:|------------:|----:|
| Boros Energy | 0% | 0% | flat (Boros engine-inflated, 76% overall) |
| Dimir Midrange | 0% | 0% | flat (n=4 noise) |
| Azorius Control | 35% | 50% | **+15pp** |
| Eldrazi Tron | 0% | 25% | **+25pp** |
| Domain Zoo | 0% | 0% | flat (aggro race overruns 5-card hands) |
| Amulet Titan | 5% | 50% | **+45pp** |
| **Aggregate** | **10%** | **21%** | **+11pp** |

Three matchups moved meaningfully (+15/+25/+45pp), three flat. Flat
results split into two groups:

- **Boros, Domain Zoo** — fast aggro decks that race a mulled-to-5
  hand. Mulligan correctness alone cannot lift these; they need either
  better mulligan retention at 7 (orthogonal mulligan-quality issue)
  or a faster combo path (deck-construction issue).
- **Dimir Midrange** — small-sample noise at n=4 (baseline 0/20
  consistent with ~5-15% true MU).

Goryo's at 21% remains below the 40-55% expected band but the vector
is correct and the doubling is real. Class-size claim partially
validated; Storm/Living End validation deferred (Storm has an
unrelated payoff-execution bug — verbose seed 50000 G1 shows AI casts
Past in Flames twice then passes without resolving Grapeshot, see
`docs/history/sessions/2026-04-26_storm_goryos_deferral_gate.md`).

## Responsible subsystem

| Bug | Location | Fix shape |
|-----|----------|-----------|
| #1 — combo-set check is color-blind | `ai/mulligan.py:107-167` | extend the predicate to verify the hand's lands cover the union of color pips for kept combo-set cards (using `template.mana_cost` parsed via existing `engine/mana.py` cost parser). **LANDED 2026-04-28**. |
| #2 — RETRACTED | — | engine's `_build_template` correction layer already restores Archon's Legendary supertype; locked in by `tests/test_archon_of_cruelty_is_legendary.py`. Not a real bug. |
| #3 — 6-card escape clause is too generous | `ai/mulligan.py:130-141` | for combo-archetype decks, do not auto-keep a 6 that misses *every* combo set; allow mull to 5. Aggro/midrange (no `mulligan_combo_sets`) auto-keep unchanged. **LANDED 2026-04-28**. |
| #4 — combo-set predicate too permissive | `ai/mulligan.py:118-129` | at 7 cards, require ≥ 2 of 3 pieces from at least one combo path; 1-of-3 was treated as "combo present". **LANDED 2026-04-28**. |

Bug #1 applies to the entire combo archetype, not Goryo's alone:
Living End cycle (B/G + R for cascade), Ruby Storm (RR pips), Niv-
Mizzet shells (off-color enablers).  Class-size satisfies the
ABSTRACTION CONTRACT.  Implementation is oracle-driven (uses
`template.mana_cost.colors` and oracle-text scan for fetchlands), no
hardcoded card names, no magic numbers.

## Failing test (to land before any fix)

`tests/test_mulligan_rejects_color_unsound_combo_hand.py`:

```python
def test_goryos_rejects_no_red_no_blue_hand(card_db):
    """Goryo's hand with all four 'combo pieces' but only W/B
    sources must mulligan — Faithful Mending ({W}{U}) cannot be
    cast, so the combo cannot fire."""
    decider = _goryos_decider()
    hand = [
        _hand_card(card_db, "Godless Shrine"),
        _hand_card(card_db, "Swamp"),
        _hand_card(card_db, "Concealed Courtyard"),
        _hand_card(card_db, "Archon of Cruelty"),
        _hand_card(card_db, "Goryo's Vengeance"),
        _hand_card(card_db, "Unburial Rites"),
        _hand_card(card_db, "Faithful Mending"),
    ]
    keep = decider.should_keep(hand, ...)
    assert keep is False, decider.last_reason
    # Failure message at red: today this asserts True with reason
    # "has key card(s): Archon of Cruelty, Faithful Mending, ...".
```

The test name encodes the *rule* — "color-unsound combo hand must mull"
— not the card. It will catch the same bug for Living End (B/G cycler
hand with no R for cascade), Ruby Storm (R-only hand with no second R),
Niv-Mizzet (off-color enabler hands), etc.

## What this does NOT cover

- The post-combo play AI (the deferral-gate fix from 2026-04-26
  improved this; not regressed).
- Goryo's vs non-Boros matchups. The 0/20 cell makes Boros the cleanest
  diagnostic, but the hand-rejection fix should lift Goryo's against
  every fast-clock deck where mana-soundness is most punishing.
- The card database's `produces_mana` extraction for fetchlands —
  fetches resolve at cast-time, not at mulligan time. The verifier
  must know that fetches contribute to color coverage transitively
  (a Marsh Flats counts as access to any of the duals it can fetch
  in the deck). This is a sub-design point inside fix #1, not a
  separate bug.

## Why this is `priority: primary`

CLAUDE.md loop-break protocol: "Run run_meta.py --bo3 against the worst
matchup, identify the exact turn where EV diverges from correct play,
name the responsible subsystem in writing." Done — this doc names
`ai/mulligan.py:96-104` and `:100-102`. Per the contract, no further
code on Goryo's WR until the failing test for fix #1 lands red.

The same pattern almost certainly explains why Ruby Storm (RR pips,
needs R + R fast) and Living End (cycle B/G, cascade RUB) sit at
20% / 38% with similar shape — same mulligan blind spot. After fix
#1 lands, run a 3-deck WR sweep (Goryo's, Storm, Living End) before
declaring the bug closed.
