---
title: Multi-card tutor projection — class-size audit (FALSIFIED)
status: falsified
priority: diagnostic
session: 2026-05-10
depends_on:
  - docs/design/2026-05-10_oracle_pattern_projection_blindspot_audit.md
tags:
  - ev-evaluator
  - card-projection
  - tutor
  - class-size
  - abstraction-contract
summary: >
  Tested the hypothesis that `compute_play_ev` under-projects tutors
  whose oracle is "search your library for up to N cards … into your
  hand" (line 1903 onward). Measured the printed Modern pool: 40
  cards match a multi-card-into-hand pattern across all of
  ModernAtomic.json. Cross-referenced `decks/modern_meta.py`'s 16
  active deck lists (mainboard ∪ sideboard): zero hits. The named
  decks the brief identified as multi-card-tutor users (4c Omnath,
  4/5c Control, Azorius Control) actually run only fetch-land
  "search for a land" effects (1-card into play, not into hand) and
  Lórien Revealed (draws 3 — already covered by the literal `draw N`
  branch landed in PR #334). Class size in the *active* pool is
  zero; the abstraction contract's class-size floor (10 cards in
  code paths that legitimately matter) is not met. No code change.
---

# Multi-card tutor projection — class-size audit (FALSIFIED)

## Hypothesis (under test)

The card-draw projection at `ai/ev_evaluator.py:1903` (post-PR-#334)
covers literal `draw N` and impulse-draw `exile top N may play`,
but not multi-card library tutors with oracle text "search your
library for up to N cards, … put them into your hand". Such cards
are projected as 1-card cantrips, under-scoring their EV by N − 1
hand value. Decks running these tutors should win at a lower rate
than they should. Audit doc:
`docs/design/2026-05-10_oracle_pattern_projection_blindspot_audit.md`
identifies this as the fourth row of the audit table (line 72), and
names ~30 candidate cards.

## Measurement

### Step 1 — printed Modern pool (ModernAtomic.json, 21 795 cards)

Pattern: oracle text containing
`search your library for (up to )?(two|three|four|five|six|seven|\d+) cards?` AND
`into your hand`. Augmented to catch conditional phrasings
("instead search …") and split-fate phrasings ("put one into your
hand and the other into your graveyard").

Result: **40 cards** match — Cultivate, Kodama's Reach, Tooth and
Nail, Tiamat, Ranger of Eos, Yavimaya Elder, Gifts Ungiven, Final
Parting, Increasing Ambition, Behold the Beyond, Squadron Hawk,
Plea for Guidance, Fork in the Road, Jarad's Orders, Threats
Undetected, Three Dreams, Realms Uncharted, Verdant Mastery, …

Above the abstraction-contract class-size floor (10) on raw
printed-pool count.

### Step 2 — active deck pool (decks/modern_meta.py, 16 decks)

Cross-referenced the 40-card list against every mainboard /
sideboard entry of every active deck in `MODERN_DECKS`. Zero hits.

```text
Total multi-card tutors in 16 active decks (mainboard ∪ sideboard): 0
```

### Step 3 — named candidate decks

The brief identified 4c Omnath, 4/5c Control, and Azorius Control
as multi-card-tutor users. Inspecting their actual lists for any
`search your library` oracle text:

- **4c Omnath, 4/5c Control:** only fetch-lands (Flooded Strand,
  Misty Rainforest, Windswept Heath). Fetches search for **one**
  land card and put it **onto the battlefield**, not into hand.
  These are not card-draw projections at all — they're handled by
  the mana-base layer of the projection, separate from this code
  path.

- **Azorius Control:** fetch-lands + Lórien Revealed (which draws
  three cards — covered by the literal `draws? three` branch
  landed in PR #334) + Demolition Field (opponent's library).
  Lórien Revealed is the only multi-card hand-fill tutor in the
  active pool, and the *literal-draw* branch already projects it
  correctly. No multi-card "search for up to N → hand" tutor.

- **Mastermind's Acquisition:** named in the brief. Oracle: "Search
  your library for a card …" — singular "a card", not multi.
  Already correct.

- **Bring to Light:** named in the brief. Oracle: "Search your
  library for a creature, instant, or sorcery card with mana value
  ≤ converge … cast that card without paying its mana cost." —
  singular target, and the resolved target is *cast*, not put into
  hand. Different mechanic; not in scope here.

- **Lukka, Wayward Bonder:** not in the printed pool reachable from
  the current ModernAtomic refresh.

## Conclusion

The hypothesised under-projection has no realised cost in the
current sim's deck pool. The 40-card printed population exists,
but none of those cards are run by any of the 16 active decks.
WR delta on a code change here would be zero by construction.

Per `CLAUDE.md` ABSTRACTION CONTRACT bullet 1: "If fewer than 10
[cards in active deck rotation] could legitimately hit this code
path, you are patching." Active-pool class size is zero; this is
**below the patch floor by definition**.

The brief's escape hatch is engaged:

> If during diagnosis you discover that the multi-card-tutor
> population in Modern is too small to matter (< 10 cards in
> actively played decks), STOP and write a `docs/diagnostics/…`
> with `status: falsified`, document the measurement, push the
> diagnostic. Don't ship a speculative code fix.

No code change shipped. This doc records the measurement so the
next session does not re-run the same experiment.

## When this becomes worth re-opening

Re-evaluate when **either** of the following holds:

1. A new deck registered in `decks/modern_meta.py` runs ≥ 4
   copies of any card in the printed-pool list above (Cultivate,
   Kodama's Reach, Gifts Ungiven, Tooth and Nail, Tiamat, etc.).
   Lands ramp decks are the natural future entrants — Amulet
   Titan currently does not run Cultivate / Reach (it runs
   Primeval Titan + bounce-lands), but a future printing or list
   change could bring them in.

2. A new printing introduces a multi-card "search for up to N →
   hand" tutor with text-density relevance to a top-tier deck
   (i.e. a tier-1 archetype's main combo enabler is one of these).

In either case, restart the audit: re-measure the active-pool
class size against the printed-pool list, and only ship a code
fix when active-pool size ≥ 10. The fix shape is documented in
the design doc (parsed extractor mirroring PR #334) and in this
doc's appendix below — implementation is gated on class size, not
on hypothesis quality.

## Appendix — fix shape on file

For when the class size eventually clears the floor, the parsed
extractor would slot into the same block at
`ai/ev_evaluator.py:1903` as a third pattern, mirroring the
impulse-draw extractor landed in PR #334:

```python
# (sketch, NOT applied — gated on class size; see body of doc)
elif 'search your library' in oracle and 'into your hand' in oracle:
    m = _re.search(
        r'search your library for (?:up to )?'
        r'(one|two|three|four|five|six|seven|\d+) cards?',
        oracle)
    if m:
        draws_n = max(draws_n, _parse_oracle_count(m.group(1)))
```

Tuple-index trick (`_ORACLE_NUMERALS`) reused unchanged from
PR #334 — no new bare numeric literals introduced. Test name
already drafted in the design doc:
`test_tutor_projects_card_count_when_multiple_targets.py` (rule-
phrased; mechanic-named; not card-named). Implementation is a ~6
line addition; the long pole is class-size justification, not
code.

## Cross-references

- `docs/design/2026-05-10_oracle_pattern_projection_blindspot_audit.md` — the audit doc this falsifies the fourth row of (line 72, "Tutor 'into your hand' search"). The other three rows (ETB life gain, ritual mana, energy ETB) have non-zero active-pool class size and remain candidates for follow-up; this doc says nothing about them.
- `tools/check_abstraction.py` — class-size enforcement. The fix this doc *would* have shipped does not violate the ratchet (no card-name check, no deck gate), but it also wouldn't move any WR — the fix's principled defence in CI doesn't substitute for the abstraction contract's class-size precondition.
- PR #334 (`95f58c58e7dc886ae1df016b820e56026f72af21`) — the exemplar the fix would have mirrored.
