# Block Strategy Audit — Boros vs Affinity Batch

**Scope:** 6 Bo3 replays (seeds 63500, 64000, 64500, 65000, 65500, 66000), 14 games.
**Method:** parsed every `[BLOCK]` / `[BLOCK-EMERGENCY]` event, classified by
outcome (kills? survives?), threat profile, and life context. Script:
`tools/audit_blocks.py`.

## Summary — 66 blocks, only 11% unambiguously good

| Bucket | Count | % |
|--------|-------|---|
| **C. Chump into scaled attacker (P≥10)** — delays 1 turn, attacker re-swings same/higher next | **26** | **39%** |
| **A. Premature chump** (no lethal threat, blocker dies for ≤1 dmg) | **16** | **24%** |
| F. 1-for-1 trade | 7 | 11% |
| G. Favorable trade (good) | 7 | 11% |
| D. Emergency chump (genuine near-lethal defense) | 5 | 8% |
| E. Named blocker traded into token (bad value) | 3 | 5% |
| H. Tarpit block (survives, doesn't kill) | 2 | 3% |

**Structural finding:** 63% of all blocks (A+C+E buckets) are net-negative —
they trade a permanent for less than one full turn of equivalent life
protection, or for protection that will be undone next turn because the
damage source isn't neutralized.

---

## Root causes (architectural, not card-specific)

### Root cause #1 — Emergency trigger has no "will blocking stabilize?" check

Current test in `ai/ev_player.py::decide_blockers`:

```python
emergency = (total_incoming >= me.life
             or (me.life - total_incoming <= 5 and total_incoming >= 3)
             or biggest_attacker_power >= me.life // 2)   # ← this one fires non-emergencies
```

The third clause fires when Boros is at 20 life vs a 10-power attacker. That
isn't an emergency — a single 10-damage swing goes 20→10, and we get a
turn to respond.

**Evidence:** bucket A & D mixed at 20+ life:
- s65000 T5 Boros: blocks Memnite + 2× Construct Token at **life=20** — not emergency,
  just the biggest-attacker-≥-life/2 clause firing.
- s66000 T4 Boros: blocks Sojourner's Companion (4/4) with Elemental+Pyromancer at
  **life=17** — 4 damage is not an emergency.
- s63500 T4 Boros: Ajani chumps 4/4 at **life=20**.

### Root cause #2 — No projection across multiple turns

When the attacker's power comes from equipment/auras (Cranial Plating), chumping
once does **not** solve the problem: next turn plating reattaches to any
remaining creature. The blocker is gone; the attacker is unchanged.

**Evidence:**
- **s63500 T5→T8 Boros**: Warrior Tokens chump Sojourner's Companion four turns
  in a row (14/4, 26/4, 24/4, 26/4). Four permanents lost, attacker never dies.
- **s64500 T7→T9 Boros**: Guide→Pyromancer→Guide chump Construct Token (10/10,
  10/10, 11/11). Three permanents lost, attacker never dies.
- **s66000 T4→T7 Boros**: Five separate chumps vs Construct Token scaling
  10→12→14→26→26. Never addressed the plating.

### Root cause #3 — Emergency path keeps adding blockers until arithmetic "stabilizes", ignores board equity

Current emergency loop assigns blockers one by one until
`remaining < me.life and (me.life - remaining > 5 or remaining == 0)`.
It never asks: "is my board *after* blocking worth more than the damage I
would otherwise take?"

**Evidence:**
- **s65500 T5 Boros**: commits **five** blockers (Elemental, Elemental, Guide of
  Souls, Ajani, Pyromancer) at life=14. After emergency completes, Boros has
  no blockers left. Against a sustained Affinity board, this loses the next
  turn outright.
- **s65000 T5 Boros**: commits Cat Token + Ragavan + Ajani at **life=20** vs
  Affinity at life=5. Boros should be *racing* — Ragavan swings for 2/1
  on offense. Instead it blocks and dies.

### Root cause #4 — Named/high-value blockers chumped ahead of cheap fodder

The emergency path picks the "smallest" blocker by `creature_value`. That value
function already includes ETB/token-maker bonuses — but apparently not enough,
because premium pieces are still being thrown away.

**Evidence:**
- **s64000 T7 Boros**: **Phlage, Titan of Fire's Fury** (6/6) chumps Germ Token
  (6/6). Both die. Phlage is a key finisher with escape cost; trading it
  for a token is catastrophic.
- **s65000 T6 Boros**: Phlage chumps Construct Token (8/7) — Phlage dies (6 vs 7).
- **s65500 T7 Boros**: Phlage chumps Germ Token (10/10) — Phlage dies.
- **s64500 T8 Boros**: Seasoned Pyromancer chumps Construct Token (10/10) — dies.
- **s65000 T2/T3 Boros**: Ocelot Pride (token engine) chumps Frogmite (2/2) at
  life=20. Premature + burns the engine.

### Root cause #5 — No "unsalvageable, race instead" branch

If the opponent's clock is so fast that blocking only delays by one turn and
we don't have a board-reset in hand, the correct play is to stop blocking and
push damage. The AI never considers this.

**Evidence:** Multiple games where Boros chumps every turn for 3–4 turns, dying
on the same turn as if they hadn't blocked at all — but with zero board presence
left (so also no offensive clock). Net expected value worse than not blocking.

---

## Smoking-gun cases (one per root cause)

| Seed | Turn | What happened | Root cause |
|------|------|---------------|------------|
| s63500 T5-T8 | Warrior Token chumps Sojourner 14/4 → 26/4 → 24/4 → 26/4 | #2 (no forward projection) |
| s65000 T5 | Triple block at **life=20** vs **opp life=5** (should be racing) | #1 + #3 (bogus emergency + over-commit) |
| s65500 T5 | 5 blockers committed in one emergency; empty board next turn | #3 (stabilization arithmetic ignores residual) |
| s64000 T7 | Phlage chumps Germ Token (both 6/6) — premier finisher traded for token | #4 (value blind) |
| s65000 T2 | Ocelot Pride (engine) chumps Frogmite at **life=20** | #1 + #4 (bogus emergency + engine burn) |
| s66000 T4-T7 | Same Construct Token plating-stacked, 5 permanents chumped over 4 turns | #2 + #5 (no projection + should race) |

---

## Proposed holistic fixes (order of importance)

### Fix A — Tighten the "biggest attacker ≥ life/2" emergency clause

Replace a single absolute threshold with a **two-turn projection**:

```
emergency = (incoming_this_turn >= my_life   # this-turn lethal
             OR incoming_this_turn + opp_unblocked_next_turn_estimate >= my_life)
```

That drops every "block a 10-power at 20 life for no reason" case and
preserves the "4 damage at 8 life" case where next-turn lethal is real.

### Fix B — Plating-aware attacker-power normalisation

Before sorting attackers by power, compute an `equipped_bonus` from
permanents the opponent controls. When the attacker's effective power
is dominated by removable equipment/auras, treat blocking it as a
*stall*, not a solution — only chump if we have a board-reset plan
(Wrath in hand or graveyard reset available).

Detection is oracle-driven: `"equipped creature gets +"` / `"enchanted creature gets +"`
clauses on opponent's permanents, mapped to their attached creature.

### Fix C — Portfolio ceiling on emergency blocks

Cap total sacrificed value per emergency to `0.5 × total_incoming × life_fraction`.
If assigning another blocker pushes total blocker-value above the cap,
stop blocking and accept the remaining damage. Prevents the s65500 T5
5-blocker over-commit.

### Fix D — "Race instead" branch

Before entering the block loop, compute our offensive clock (untapped power
+ burn in hand vs opp life). If our clock-to-kill ≤ opp's clock-to-kill
(adjusted for post-block board), **don't block** — apply face pressure.

This is essentially the existing `my_untapped_power >= opp.life` check
extended to "close enough to win first" rather than "lethal on board now".

### Fix E — Protect key pieces explicitly

Cards with `"escape"` cost, planeswalkers, or oracle text matching
`"whenever ... attacks"` (battle cry) are *engines*, not fodder. They
should never be chump-blockers unless they also kill the attacker.

The existing code has a battle-cry filter in the non-emergency path
but the emergency path ignores it. Extend the filter to both.

---

## Testing plan

For each fix, a minimal unit test:

1. **Fix A**: board with my_life=20, biggest attacker=10 power, no other incoming.
   Assert `decide_blockers` returns `{}` (no block).
2. **Fix B**: opponent controls Cranial Plating equipping a 2/2 becoming 8/2.
   Assert AI does not chump unless it also has Wrath/board-reset.
3. **Fix C**: 4 attackers totalling 12 damage, my_life=15.
   Assert at most 1-2 blockers committed (not 4+).
4. **Fix D**: my_untapped_power=10, opp_life=10, incoming=5, my_life=20.
   Assert `decide_blockers` returns `{}` (race).
5. **Fix E**: Phlage on my side, Germ Token attacks.
   Assert Phlage is not chosen as a chump if a token blocker is available.

---

## Data provenance

All 66 block events with full context are listed in `/tmp/block_audit.md`
(regenerate with `python tools/audit_blocks.py`). Raw logs in
`replays/boros_vs_affinity_trace_s{63500,64000,64500,65000,65500,66000}.txt`.

## Scope note

This audit is Boros-vs-Affinity-only. The equipment/plating issue generalizes
to any "+X buff permanent" — not just Cranial Plating. Before applying Fix B,
cross-check against a deck that uses auras (e.g. Heroic style) or other
equipment (Shadowspear, Colossus Hammer) to confirm the oracle-driven
detection catches them all without false positives.
