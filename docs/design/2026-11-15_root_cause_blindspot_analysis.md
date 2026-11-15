---
title: Root-cause analysis and our blind spots — the defect classes our checks cannot see
status: active
priority: primary
session: 2026-11-15
supersedes: []
superseded_by: []
depends_on:
  - docs/design/rules-foundation-sweep-tracker.md
  - CLAUDE.md
tags:
  - root-cause-analysis
  - blind-spots
  - process
  - ci
summary: |
  Every defect found from 2026-11-08 to 2026-11-15 is classified by WHY our
  checks missed it, not only by what it broke. Six blind-spot classes cover
  all of them. The largest is a second code path answering, with a cheap
  proxy, a question the engine already has one owner for. The second is a
  legacy or name-keyed path that implements part of a card and silently
  drops the rest. Tests, ratchets, the rules auditor and field runs passed
  on every one of these defects. They were found by independent adversarial
  review and by outlier-driven field diagnosis. The protocol: every defect
  gets a root cause AND a blind-spot class AND a generic detector for the
  class (or a recorded reason there is none), and the detector is swept
  over the whole codebase for siblings before the unit closes.
---

# Root-cause analysis and our blind spots

## Why this document

The simulator is mature enough that the remaining defects are not where we look. In this stretch:
- **Every defect passed every existing check:** the unit tests, the abstraction, magic-number, single-owner and zone ratchets, 0 rules-auditor violations in every field run, and green CI.
- **They were found by** two adversarial review rounds (independent agents told to refute a unit) and two outlier-driven field diagnoses (independent agents replaying the most lopsided cells).

So fixing each root cause is not enough. Each one points at a **blind spot**: a class of defect our process cannot see. A blind spot, once named, gets a generic detector, and the detector finds the siblings.

## The defects and their classes (2026-11-08 to 2026-11-15)

| Defect | Found by | Blind-spot class |
|---|---|---|
| Summoner's Pact's name-keyed handler dropped "pay … or lose the game" | goldfish log (the human read it) | B2 partial implementation |
| Five token spells dropped their delayed exile (Feral Lightning …) | host harness, on the grammar change | B2 partial implementation |
| Relic of Progenitus's activated ability registered as an enter effect | field diagnosis (Tron) | B2 partial implementation |
| Eldrazi Temple's mana restriction deliberately unenforced | field diagnosis (Tron) | B2 partial implementation |
| Shock payment: castability judged by colour sets, not pips | adversarial review (PE) | B1 proxy for an owner (`can_cast`) |
| Pay-or-else offer: payability judged by mana total | adversarial review (LG) | B1 proxy for an owner (`mana_payable`) |
| Removal guards counted creatures, not legal targets | field diagnosis (Zoo) | B1 proxy for an owner (`can_be_targeted`) |
| Land colours read from `produces_mana` (Crumbling Vestige) | my own trace while fixing | B1 proxy for an owner (`land_mana_units`) |
| Delayed returns ran `trigger_etb`, not the enter owner | field diagnosis (Zoo) | B1 proxy for an owner (`_handle_permanent_etb`) |
| Urza's Saga wrote zones by hand | sweep while fixing EN | B1 proxy for an owner (the zone funnel) |
| "Each opponent discards": the victim's choice went through the caster's ranking | adversarial review (HA) | B3 choice by the wrong chooser |
| Bounce-land return chosen by the engine (tapped, then fewest mana) | field diagnosis (Tron) | B3 choice in the wrong layer |
| Hand attack ranked by a type key ("counters first") | adversarial review (HA) | B4 valuation not in one currency |
| Shock life priced against board power only, not burn in hand | adversarial review (PE) | B4 valuation not in one currency |
| Hollow One's Vengevine has no green source in its list | field diagnosis (Tron) | B5 data validity |
| Every one of the above passed tests, ratchets, auditor and CI | — | B6 measurement blind spot |

## The six blind-spot classes

### B1. A proxy for an owned question (the largest class)
The engine owns questions in one place each: castability (`GameState.can_cast`), payability (`CastManager.mana_payable`), targetability (`target_solver.can_be_targeted`), the colours a source taps for (`ManaPayment.land_mana_units`), what happens on entering (`_handle_permanent_etb`), and zone changes (the zone funnel). A second path that answers the same question with a cheaper proxy is right on the common board and wrong on the edge, and the edge is where the games are decided:
- a colour set instead of pips;
- a creature count instead of legal targets;
- a mana total instead of colours;
- printed data instead of what a land actually taps for;
- a template queue instead of the enter owner.

**Why we missed it:** `check_single_owner` pins only three owned questions (targets, damage and life, counters). The proxies sit outside its view.

**Generic detector:** name every owned question with its owner and its known proxies, and ratchet the proxies.

| Proxy | Count today | Owner |
|---|---|---|
| a creature count used as "has a target" (`opp_creature_count == 0`, `not opp.creatures`) | 4 | `can_be_targeted` |
| `produces_mana` read in `ai/` | 35 | `land_mana_units` for tap colours |
| `available_mana_estimate` compared against a cost in `engine/` | 2 | `mana_payable` |
| `trigger_etb` called outside the enter owner | 1 (the game-start Leyline, legitimate) | `_handle_permanent_etb` |

### B2. A partial implementation behind a name-keyed or legacy path
A card resolved by a name-keyed handler (69 remain) or a legacy branch does what the handler author wrote, not what the card says. Clauses the handler never mentions are silently dropped: a delayed trigger, a restriction, an activation turned into an enter effect. Nothing compares the handler's behaviour with the card's oracle.

**Why we missed it:** the effect grammar census counts what the grammar refuses, not what a legacy handler omits.

**Generic detector:** a clause-coverage census for registered-deck cards. Every oracle clause of every card in a registered deck is executed by the dispatcher, owned by a typed field, or listed as a known gap with its reason. A clause none of the three accounts for is a finding.

### B3. A choice made by the wrong chooser or in the wrong layer
The rules say who chooses: the caster picks for a hand attack, the discarder picks for "each opponent discards", the controller picks which land a bounce land returns. The engine made a strategic pick itself (bounce land), or passed the right chooser the wrong role (`self_discard=False` for the discarder).

**Why we missed it:** "engine enforces, AI chooses" is a convention with no detector.

**Generic detector:** every `max` / `min` / `sort(key=…)` over candidates in `engine/` (66 today) is either a rules-forced ordering, annotated, or a callback to the chooser the rules name. Ratchet the unannotated count.

### B4. A valuation not in one currency
Lexicographic type keys ("counters first") and hand-set weights (a counter worth 6 against a keystone worth 100) are right on the case that motivated them and wrong on the next. Adversarial review refuted both.

**Generic detector:** none mechanical. The protocol is that every AI comparison is stated in the one currency (position value or turns to win), and a strategy-lens reviewer checks that before the unit closes.

### B5. Data validity
A decklist whose spells its own mana base cannot cast (Hollow One's Vengevine with no green source). A gameplan naming cards the deck does not play (already checked by `check_gameplan_consistency`). Expected bands that may be optimistic.

**Generic detector:** a deck colour-feasibility check. Every nonland spell's coloured pips can be produced by some subset of the deck's lands. Run it in CI next to the gameplan consistency check.

### B6. A measurement blind spot
Our checks only see what they were told to look for:
- the auditor checks the invariants we wrote;
- unit tests encode their author's reading of the rule;
- a field run at n=20 cannot see a 1-point fix;
- a replay read by the person who wrote the fix confirms the fix's hypothesis.

Every defect above passed all four.

**What found them:**
- independent adversarial review: two lenses (comprehensive rules and strategy), given the diff and tests but not the author's conclusions, told to refute;
- outlier-driven field diagnosis: the most lopsided cells, replayed by an independent agent that must cite log lines and say "rules error or decision error".

**Generic detector:** make both standing steps, not occasional ones (see the protocol below).

## The protocol (standing, from 2026-11-15)

For every defect, before the unit closes, record five things in its tracker section:

1. **Symptom:** card level, with the log line.
2. **Root cause:** the mechanism, with no card name.
3. **Blind-spot class:** B1–B6, or a new class with its definition, and the question "why did every existing check pass?".
4. **Generic detector:** the ratchet, census or invariant that catches the class, or the recorded reason none can.
5. **Sweep:** run the detector over the whole codebase. Fix every sibling, or list it with the reason it is left.

And two standing steps:
- **Adversarial review per unit:** two independent reviewers (rules lens, strategy lens) on every rules or AI unit, told to refute. A refutation becomes a failing test and a fix, or a recorded correction.
- **Outlier-driven diagnosis per field run:** after each field run, the most out-of-band decks (both directions) get an independent replay diagnosis that must cite log lines and classify each cause. A cause found this way enters this ledger like any other.

## Ledger (counts per class; update with each unit)

| Class | Count this stretch | Detector status |
|---|---|---|
| B1 proxy for an owned question | 6, plus 2 siblings found by the sweep | **ratchet built** (`tools/check_owner_proxies.py`, in CI); sweep done for target_count, mana_total_pay and enter_queue; tap_colours (31 reads in ai/) baselined, sweep open |
| B2 partial implementation | 4 | clause-coverage census to build |
| B3 wrong chooser or layer | 2 | engine-pick ratchet to build |
| B4 valuation not in one currency | 2 | strategy-lens review (standing) |
| B5 data validity | 1 | colour-feasibility check to build |
| B6 measurement blind spot | all of the above | adversarial review and outlier diagnosis (standing) |

**Build order for the detectors:**
1. B1 proxy ratchet (cheap greps over the codebase);
2. B5 colour-feasibility check (cheap; one known finding);
3. B2 clause-coverage census (the largest payoff: 69 name-keyed handlers);
4. B3 engine-pick ratchet.

## Sweep log

**2026-11-15, B1 owner-proxy sweep** (`tools/check_owner_proxies.py --list`):

| Category | What the sweep found | Outcome |
|---|---|---|
| target_count | 4 hits, all legitimate: a blocker count in combat, a board wipe that does not target, "nothing at all to remove" with legal targets gated after it, the HX guard's "no creature at all" | annotated; baseline 0 |
| mana_total_pay | **2 real siblings.** The counter-tax offer (Mana Leak, Spell Pierce …) and the ward-tax offer judged payment by untapped sources only, so a player with floating mana was never asked | routed through `CastManager.mana_payable` (`optional_costs._generic_mana_payable`); test red first; baseline 0 |
| enter_queue | 1 hit, legitimate: a Leyline that begins the game on the battlefield does not enter (CR 103.6) | annotated; baseline 0 |
| tap_colours | 31 reads of `produces_mana` in ai/ | baselined at 31; open sweep (many are colour-identity reads for planning, which are legitimate) |
