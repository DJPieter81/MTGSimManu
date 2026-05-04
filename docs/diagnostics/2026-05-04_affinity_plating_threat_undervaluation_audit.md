---
title: Plating-equipped-creature threat undervaluation audit (Phase L follow-up)
status: active
priority: primary
session: 2026-05-04
depends_on:
  - docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md
tags:
  - audit
  - affinity
  - threat-value
  - phase-l-followup
summary: >
  Tests whether ai/permanent_threat.py and ai/ev_evaluator.py:
  creature_threat_value() under-rate creatures that can be equipped
  with Cranial Plating (or analogous +N/+0 scaling effects). Boros vs
  Affinity replay analysis + isolated threat-value comparisons.
  Findings: PARTIAL — current state is correctly captured (dynamic
  P/T flows through), but POTENTIAL state (unattached equipment that
  can be re-attached next turn) is invisible. Unattached Cranial
  Plating itself returns permanent_threat = 0.00 — opponents are
  actively de-prioritising the lethal artifact.
---

# Plating-equipped-creature threat undervaluation audit

## Pre-flight: what the threat functions actually compute

Two separate functions matter, both used by removal-targeting code paths in
`ai/ev_player.py`:

1. **`ai.permanent_threat.permanent_threat(card, owner, game)`** — used by
   burn-to-permanent and nonland-permanent removal targeting (`ev_player.py`
   lines 1138-1168, 2643-2650). Computes the marginal drop in the owner's
   `position_value` when the card is removed: `V_full - V_partial`.
2. **`ai.ev_evaluator.creature_threat_value(card, snap)`** — used by
   creature-only removal targeting (`ev_player.py` lines 2677-2680). Reads
   `card.power` and `card.toughness` (which are *dynamic* properties — they
   call `_dynamic_base_power`/`_dynamic_base_toughness` and do see the
   currently-attached Plating bonus), then adds oracle-driven virtual-power
   for "for each X" scalers and battle-cry amplifiers.

The first function pops the card off the battlefield and rebuilds the
snapshot, so it sees the dynamic P/T of *other* creatures change too (e.g.
removing a Plating drops the equipped creature's power). The second function
only reads the targeted creature's own P/T.

**Crucially, neither function considers unattached equipment as future
upside on a creature it might be moved to.** The Plating that sits on the
battlefield, currently attached to nothing (or attached to Creature A, but
with `{B}{B}: Attach to target creature you control` available next turn),
contributes only its CURRENT effect. If unattached, that effect is zero.

## Empirical comparison — isolated test

Reproduction harness: `scratch_threat_audit.py` (deletable; not committed).
Setup:

- Affinity (side 0): Memnite (1/1), Cranial Plating (unattached), Mox Opal,
  Springleaf Drum, Darksteel Citadel (artifact land).
- Boros control (side 1): 2× Mountain, Goblin Guide (2/2 haste).

| Quantity (Boros perspective) | Plating UNATTACHED | Plating EQUIPPED to Memnite |
|---|---:|---:|
| Memnite power/toughness | 1/1 | 6/1 |
| `creature_threat_value(Memnite)` | **1.15** | **6.15** |
| `permanent_threat(Memnite)` | -38.00 | 24.67 |
| `permanent_threat(Cranial Plating)` | **0.00** | (n/a — attached) |
| `creature_threat_value(Goblin Guide)` (control) | 8.15 | 8.15 |
| `permanent_threat(Goblin Guide)` (control) | 68.00 | 68.00 |

`opp_artifact_count` from Boros's snapshot = 4 (correctly excludes
Darksteel Citadel via PR-L1).
`opp_artifact_scaling_active` = True (Plating's oracle text contains
"for each artifact"). Yet this scaling-active flag is read by the
`position_value` heuristic via `clock.py:artifact_value`, not by the
creature-targeting path.

### What this reveals

- **Lightning Bolt would target Goblin Guide (8.15) over Memnite (1.15)**
  even though Memnite is the natural Plating recipient. Once Plating
  attaches next turn, Memnite swings as a 6/1 — within 1-shot range of
  Boros's life total combined with anything else.
- **Wear // Tear style nonland-permanent removal sees Plating as a 0.0
  threat when unattached.** `permanent_threat` returns the marginal
  position-value drop, and removing an unattached equipment from the
  battlefield doesn't change the dynamic P/T of any creature. The
  artifact-count delta passes through `clock.py:artifact_value`, but
  that's a small board-strength term, not the Plating-as-finisher
  signal.
- The `permanent_threat(Memnite)` value of -38 in the unattached case is a
  separate oddity worth flagging: the marginal-contribution formula goes
  negative because removing a tiny body from a board with strong opp
  artifact-count can swing `position_value` in the controller's favour
  through some interaction term. Investigating that is out of scope for
  this audit but is worth a follow-up issue.

## Replay evidence — Boros vs Affinity

Searched `replays/boros_vs_affinity_*.txt`,
`replays/boros_rarakkyo_vs_affinity_*.txt`,
`replays/affinity_vs_boros_*.txt` for turns where Boros has a Galvanic
Discharge / Lightning Bolt available and Affinity has either Plating in
play (unattached) or about to come down.

### Example A — `replays/boros_vs_affinity_s60100.txt` line 727

T3, Affinity is casting Engineered Explosives (X=2). Affinity board:
Memnite (1/1), Signal Pest (0/1), Mox Opal, Treasure Vault, Darksteel
Citadel. **Cranial Plating is in Affinity's hand and Plating mana is
available next turn.**

Boros responds with Galvanic Discharge:
```
T3 P2: Galvanic Discharge deals 3 to Signal Pest
T3: Signal Pest dies
```

`creature_threat_value(Signal Pest)` ≈ 1.00 + battle-cry virtual_power → 3.0
`creature_threat_value(Memnite)` ≈ 1.15 (vanilla 1/1)

Bolt picked Signal Pest because of the battle-cry amplifier — fair on its
own merits, but Memnite is the natural Plating recipient, and the next
turn Affinity casts Plating (line 738) and equips. Had Boros killed
Memnite, Plating would have had to attach to Signal Pest instead (a 0/1
that already lost to Bolt range anyway).

### Example B — `replays/boros_rarakkyo_vs_affinity_bo3_s61000.txt` lines 324, 990

Two separate Galvanic Discharges target Thought Monitor (3/2, ETB-card-
draw flier). Both turns, Affinity has Plating + 4-5 artifacts in play.
Killing Thought Monitor stops 2 in the air; not killing the Plating
recipient leaves a Construct Token at 7/3 + Plating-attached for 11+ power
on the swing back. Boros's removal allocation does not weight "is this the
Plating target" at all.

### Example C — `replays/boros_vs_affinity_s60100.txt` line 465

T7, Boros has Galvanic Discharge and a board where Affinity has 20/4
Sojourner's Companion (with 2× Plating attached) and Thought Monitor (2/2).
Boros casts Galvanic Discharge → 3 to face. Going face is correct here
because both Affinity creatures are out of 3-damage range — but this
is downstream evidence: by the time Plating is fully assembled, removal is
unable to interact with the Plating target at all.

### Replay-evidence count

Across 6 Boros-vs-Affinity replays surveyed (s60100, s60001, s60200,
s60900, s61000, s50000), the targeting bias appears in **3-4 turns where
Boros had removal in hand and Affinity had Plating either in hand (next-
turn cast) or unattached on board**. Not all of these are "wrong" — Signal
Pest's battle-cry premium is a real signal too — but the targeting code
*never* tilts toward the Plating recipient even when oracle context makes
it the obvious finisher.

## Findings

**CONFIRMED, partial.** The hypothesis as originally framed ("printed P/T
ignored when Plating attached") is *not* the bug — `card.power` IS dynamic
and the threat functions do see equipped P/T correctly. The actual
bias is one level removed:

1. **Unattached equipment with `+N/+0`-style scaling is invisible to
   threat valuation.** `permanent_threat(unattached Cranial Plating) =
   0.00` because removing it doesn't drop position_value when no creature
   is currently equipped. This is a sound *marginal-contribution* answer
   but the wrong *strategic* answer — Plating is a finisher whose value
   is in its option-to-attach.
2. **Creature-targeting code does not look at the controller's
   equipment-pile when ranking creature targets.** A 1/1 vanilla Memnite
   on a board with unattached Plating + 4 artifacts is a 6/1 next turn,
   but `creature_threat_value(Memnite)` reads only Memnite's own oracle
   text and current P/T. Generalises to any "+N/+M for each X" scaling
   equipment (Nettlecyst is the next candidate; Sigarda's Aid future-
   proofing too).

## Recommendation — fix-PR sketch (separate dispatch)

A fix should live in `ai/ev_evaluator.py:creature_threat_value` and (more
ambitiously) in `ai/permanent_threat.py`. Pseudocode for the
creature-side amendment:

```python
def creature_threat_value(card, snap):
    # ... existing virtual_power computation ...

    # Equipment-ceiling premium: if the controller has unattached or
    # cheaply-movable equipment with +N/+0-style scaling, raise the
    # threat ceiling for any creature that could legally be equipped.
    # The premium is gated by the relevant equip-cost being affordable
    # and by oracle-text detection of the scaling pattern. Class size
    # check: the same rule must lift Nettlecyst, Argentum Armor (no
    # scaling), Sigarda's Aid + any equipment, and any future +N/+0
    # equipment from a new set — not just Cranial Plating.
    ceiling_bonus = max(_equipment_ceiling_bonus(card, snap), 0)
    p_effective = (card.power or 0) + virtual_power + ceiling_bonus

    # ...feed p_effective through creature_clock_impact as before...
```

Detection rule (oracle-driven, no card names):

- Walk `controller.battlefield` for permanents with `Equipment` in
  `subtypes` AND oracle text matching `r'\+\d+/\+\d+\s+for\s+each'`.
- For each, parse the +A/+B and the scaling key from oracle. The bonus
  it would grant `card` if equipped = A × `_get_artifact_count` (or
  the relevant counter).
- Discount by feasibility: if the equipment is currently attached
  elsewhere, multiply by a re-attach probability term (e.g. the
  ratio `1 / (equip_cost + 1)`); if unattached, full bonus.

This mechanic also dovetails with the Phase L finding that the
`engine_disruption_value` premium currently applies only to
combo-archetype declared engines — Plating-class equipment fits a
similar "the artifact is itself the win condition" semantic and could
be lifted via the same hook.

### Failing-test phrasing (rule, not card)

The test must name the *mechanic*, e.g.
`tests/test_unattached_equipment_raises_recipient_threat.py::
test_plus_N_per_artifact_equipment_ceiling_lifts_recipient_threat`.

The contract this fix needs to honour:

1. Class size — applies to ALL Equipment with a `+N/+M for each X`
   oracle pattern: Cranial Plating, Nettlecyst, Sword of Body and Mind
   (no scaling, doesn't trigger), and any future printing.
2. Knowledge location — detection lives in oracle text + `subtypes`,
   not card-name conditionals.
3. Generalisation check — the same fix should also help any other deck
   that runs scaling equipment (Pinnacle Affinity is the obvious second
   beneficiary; Hammer Time uses Colossus Hammer which has a flat
   `+10/+10` pattern, also covered).
4. Magic-numbers check — the discount-for-currently-attached factor
   needs a rule-name in `ai/scoring_constants.py` with an inline
   justification.

## Verification done in this branch

- Read `ai/permanent_threat.py` end-to-end (117 lines).
- Read `engine/cards.py:_dynamic_base_power` / `_dynamic_base_toughness`
  (lines 344-447) and confirmed Plating P/T flows through dynamic
  recompute when attached.
- Read `ai/ev_evaluator.py:creature_threat_value` (lines 383-436) and
  `ai/ev_evaluator.py:snapshot_from_game` (around lines 285-316) for
  artifact-count handling.
- Read `ai/ev_player.py` removal-targeting paths at lines 1138-1168,
  2626-2680.
- Built isolated harness using the real `CardDatabase` to load Memnite,
  Cranial Plating, Mox Opal, Springleaf Drum, Darksteel Citadel,
  Goblin Guide, Mountain. Confirmed numeric values above.
- Surveyed 6 Boros-vs-Affinity replays for actual targeting events
  with Plating in context.

No source-code changes in this branch. Fix-PR will be a separate
dispatch with the failing test specified above.
