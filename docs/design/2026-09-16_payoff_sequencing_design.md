---
title: Payoff sequencing — a core decision-making design for engine + payoff decks
status: active
priority: primary
session: 2026-09-16
supersedes: []
depends_on:
  - docs/history/audits/2026-09-15_5panel_deep_audit.md
  - docs/diagnostics/2026-09-15_deep_audit_backlog.md
tags: [design, ai-decision-quality, combo, payoff-sequencing, calibration]
summary: >
  One owner of "is the engine live / what sink is reachable / is there a lethal
  line" — ai/assembly_state.py — read by tutor delivery, activation EV, cast EV
  and the attack declarer, replacing four half-derivations. Two refutations
  reshaped it: the traced Toolbox line is a countered X-tutor into open tax mana,
  so the primary sink is the on-board team-counter ACTIVATION class (Leyline of
  Abundance), which the engine must first learn to execute; and every line
  carries a BHI resolution probability so an all-in spell never outranks an
  uncounterable ability. Judge-panel winner (2 of 3 votes) with all three
  refuters' must-fix amendments applied; single-deck movement stated honestly.
---

# Payoff sequencing — design (2026-09-16)

Produced by a judge-panel design workflow (5 readers → 4 designs → 3 judges →
3 adversarial refuters). The winner, *AssemblyState*, was **refuted by all
three lenses** on specific points; every must-fix amendment below is applied,
and where a refutation was itself wrong it is answered in one line. The
synthesis is written by hand from the cached agent results (the synthesis
agents were blocked by an account credit limit), so nothing here is
unverified against the code the agents cited.

## 1. Context — the traced defect, and why two fixes were flat

Creatures Toolbox (21% flat WR, band [30,70]) assembles the Devoted Druid +
Vizier of Remedies unbounded-mana loop, makes 81–88 mana, and passes.
Traced (`toolbox_zoo.txt` G1 T4 :500-575; `toolbox_dimir.txt` G1 T4
:462-571, T6 :809-827):

- **Decision (1), pre-engine, budget 8–10:** the X tutor (Nature's Rhythm)
  is cast at **X=2 for Vizier** although the mass pump (Craterhoof, MV 8) is
  fetchable. Today's picker (`engine/activated_effects.default_tutor_rank`,
  engine-first, no sink term) and the AI callback
  (`ai/activation_ev.choose_tutor_delivery._worth`, engine credit ungated)
  both return Vizier — verified on a rebuilt board.
- **Decision (2), loop live, 81+ mana:** the AI casts a 1-drop at +0.0 and
  passes (`:537-555`). No tutor and no spell sink are in hand; the ONLY
  outlet is two-to-three copies of **Leyline of Abundance** on the
  battlefield, whose "{6}{G}{G}: put a +1/+1 counter on each creature you
  control" parses as `UNCLASSIFIED` and is refused by
  `ActivationManager.can_activate` (`engine/activation.py:46-58` lists only
  `PUT_COUNTER_SELF` / `PUT_COUNTER_TARGET`). Ten activations = +10/+10 →
  34 power into 16 life: **lethal on T4 with permanents already in play,
  spending no tutor, immune to Stubborn Denial.** This state occurred in
  6/6 loop-live turns across four traces.

Why Lane-T (sac-victim guard, +0.2pp) and U2 (sink-existence gate, +0.5pp)
were flat: both changed the **magnitude or availability of a credit** while
the **target pick, the X paid, and the outlet the deck can execute** stayed
the same. U2's sink predicate was true in every Toolbox game (both sinks in
the 60) and gated a number attached to the wrong target. Neither touched the
two decisions that lose the games.

**The refuter's decisive finding:** even the intended fix for decision (1) —
Rhythm at X=8 → Craterhoof (whose ETB pump, haste and 23-power alpha all work
in the engine) — **is countered on the traced board.** X=8 spends all 10
mana; `ai/response.py:532-540` treats a tax counter as live when the payer
has 0 capacity, and the Zoo AI fires Stubborn Denial ("threat 100.0 vs cost
1 … Worth countering"). At X=2 with 8 open it passes — exactly trace :531.
`toolbox_dimir.txt:809-827` shows the same: today's picker already goes X=8
once Vizier is gone, into Counterspell. **An all-in spell line into open
counter mana is the wrong line; the ability line is the right one.**

Siblings: Amulet Titan's ramp tutor has the same shape but **no typed sink**,
so it is not a mover for this design (its only lever, a RAMP goal transition,
is deferred — §7). Storm is deliberately untouched.

## 2. Mechanism — one owner, four readers

### 2.1 `ai/assembly_state.py` (new; pure query layer, no weights)

`assemble(game, player_idx, snap) -> AssemblyState` (pydantic, the
`EVSnapshot` idiom in `ai/schemas.py`):

| field | source (reused, never re-derived) |
|---|---|
| `engine_live` | `bool(ActivationManager.unbounded_mana_engines(game, idx, ignore_summoning_sickness=True))` (`engine/activation.py:426`) |
| `engine_spins_now` | same, sickness respected |
| `mana` | `snap.my_mana` — already folds `LOOP_SHORTCUT_MANA` per live loop (`player_state.py:294-301`; verified 88 with one loop) |
| `completers` | hand ∪ tutor-reachable cards with `would_complete_unbounded_engine` (`:365`); **empty when `engine_live`** |
| `sinks: list[SinkAccess]` | every sink in hand / battlefield / library with how it can be deployed this turn (§2.3) and its `p_resolves` (§2.5) |
| `best_line: Optional[LethalLine]` | the access maximising `p_resolves × win_swing` among those whose projected damage ≥ opp life (§2.4) |
| `payoff_affordable` | min effective cost over gameplan `card_roles` payoffs ∪ sinks ≤ mana (read by the deferred RAMP transition only) |

**Threading (no module-level cache):** `assemble()` runs once per
`EVPlayer.decide_main_phase` iteration and is stored on the instance beside
`_assess_value`; `compute_play_ev` gains `assembly: Optional[AssemblyState]
= None`, threaded exactly as `bhi`/`p_interaction` are
(`ai/ev_evaluator.py:3282`); the delivery callback receives it via a
`state=` kwarg from the picker. An `id(snap)` dictionary is unsound
(`EVSnapshot` is frozen; `fast_replace` mints objects per TurnPlanner
ordering and ids are reused) and is not used.

### 2.2 Sink identity — `is_mana_sink(template_or_ability) -> Optional[SinkShape]`

Exactly the **mana-scaling** shapes, from typed fields only:

- `x_damage` — `deals_targeted_damage ∧ x_cost_data` (Walking Ballista,
  Fireball, Comet Storm, Crackle with Power …).
- `team_pump` — `team_pump_data` (Craterhoof, End-Raze Forerunners, the
  Overrun sorceries — ~30 in the resolver's own census).
- `team_counters` — **new typed activated-ability kind `PUT_COUNTER_TEAM`**
  ("{cost}: put a +1/+1 counter on each creature you control": Leyline of
  Abundance, Gavony Township, Mikaeus the Lunarch, Ajani-shape activations —
  a CR 122 counter-placement class). Prerequisite engine unit (§5 U1).

`has_scaling_token_finisher` is **not** a sink: its scaling variable is
storm/discard, not mana (Seasoned Pyromancer parses True), so it stays owned
by the Storm chain. `ai/combo_calc.unbounded_mana_sink_reachable` becomes a
wrapper over `is_mana_sink` (its pinned tests keep passing; a new pin says a
token maker scaling on something other than mana is not a sink).

### 2.3 Access enumeration — `SinkAccess.via`

`'cast'` (sink in hand), `'activate'` (sink on the battlefield),
`('tutor_cast', tutor, X)` for an `x_creature_tutor_data` spell whose spec
admits the sink (`tutor_target_matches`), `('tutor_activate', perm, idx, X)`
for a battlefield tutor `can_activate` accepts, `'tutor_to_hand'` (adds the
sink's own cost). Guards: CR 704.5f — a to-battlefield delivery of an
`x_damage` permanent is not an access unless `spec['enters_with_x_counters']`
(it would enter 0/0; verified false for the Rhythm/GSZ specs, so the Ballista
shape is reachable only to hand or by cast). The enumeration reads
`eligible_tutor_targets` / `tutor_target_matches` directly and never calls
the X pickers (no recursion).

**Cast-time truth (amendment A3):** `cast_manager` moves the tutor to the
stack *before* X sizing, so a zone-enumerated `best_line` inside the
callback would never see the tutor being resolved. `_worth` therefore
computes `delivers_lethal` **explicitly** from
`(source, X, mana_after = capacity − X·mult − source.cmc, candidate)`, never
from `state.best_line`. A test pins that the AI-side `_gate_x_tutor_payoff`
pick and the cast-time `pick_creature_tutor_x_value` pick are the same
`(X, target)` on the same board.

### 2.4 Damage projection — `sink_damage(shape, template, mana_left, board)`

The only place a sink is priced in damage.

- `x_damage`: `X = CastManager.affordable_x(...)` (§2.6); damage = X per
  point, or per counter converted by the permanent's `DAMAGE_ANY_TARGET`
  remove-a-counter ability; on-board sink: its `plus_counters`. **Ping-line
  feasibility:** `k = min(counters, ceil(opp_life/amount),
  MAX_ACTIONS_* − actions_used)` so a line never projects a counter-stack
  kill the runner cap (`engine/game_runner.py:1472-1479`) cannot execute; the
  k-fold ping is scored as ONE candidate projecting `opp_life − amount·k`
  (targets `[]` for `target_solver`).
- `team_pump`: mirrors `engine/oracle_resolver._resolve_team_pump`
  (`:287-300`) — fixed / creature_count / greatest_power; attackers =
  untapped non-sick power>0 creatures plus the entering body iff haste.
- `team_counters`: `k = mana_left // ability.cost.mana.cmc` activations →
  each attacker +k/+k.
- **Payment-aware attacker exclusion:** the mana creatures the access's
  payment will tap (shortfall = cost − non-creature capacity; lowest-power
  `tap_mana_units>0` creatures first — the existing tapping order) cannot
  attack; project on what is left. On the Zoo T4 board X=8 taps Devoted
  Druid and Delighted Halfling, so the real attacker set is three bodies,
  not five.
- `attack_reach(attackers, through_blocks)`: one combat fold.
  `through_blocks=False` is `decide_attackers`' current unblocked sum
  (`ai/ev_player.py:3122-3133`); `through_blocks=True` subtracts the k largest
  attacker powers for k untapped opposing creatures without trample
  (CR 509.1a) and Σ blocker toughness with trample (CR 702.19b). Follow-up
  (from KLS): delegate the through-blocks verdict to
  `CombatPlanner._simulate_combat` on a `VirtualBoard` copy once
  `VirtualCreature.summoning_sick` is split from `is_tapped`
  (`ai/turn_planner.py:1244` conflates them).

### 2.5 Line ordering — BHI-aware, not a boolean (amendment A2)

Every `SinkAccess` carries `p_resolves`: for `'cast'` / `'tutor_cast'`,
`1 − p_interaction` (the single BHI query the Storm branch already uses),
**additionally routed through the tax-counter branch when
`post_cast_mana < effective_counter_tax` of any soft counter in the
opponent's BHI posterior** (payer-side primitive exists:
`project_counter_tax_payment`); for `'activate'` / `'tutor_activate'`,
`p_resolves = 1` against spell counters. `best_line` maximises
`p_resolves × win_swing(snap)` — so "X=8 at p≈0 into a live tax counter"
loses to "X=2 keeping the tax payable", and the uncounterable Leyline line
beats both. A lexicographic boolean could not express this; the winner's
original tuple was refuted on exactly this point.

`win_swing(snap) = max(0, WIN − position_value(snap))` is **lifted into
`ai/clock.py` beside `position_value`** (one owner of "what a projected kill
is worth"; the existing bare `100.0` at `ai/ev_evaluator.py:3339` calls it).
`position_value` itself is untouched in every form (§6).

### 2.6 X sizing — one owner (amendment: drop `x_value_for_capacity`)

`CastManager.affordable_x` (`engine/cast_manager.py:255-270`) already
documents itself as the one formula `(capacity − cmc) // mult`. *Verified
2026-09-24 (§5 U0):* the inline copy at `:1828-1829` reads capacity AFTER
the base cost has been paid, so it is already net of the fixed pips (the
"engine 10 vs AI 8" reading compared a pre-payment number with a
post-payment one) and additionally net of any applied cost reduction;
replacing it after payment would subtract the base twice. The AI copy at
`ai/ev_player.py:1059` is the same formula as `affordable_x`. The rule is
pinned (`tests/test_x_cost_paid_never_exceeds_capacity_minus_pips.py`, CR
601.2h) and `assembly_state` sizes X through `affordable_x` (pre-payment,
the correct owner for a projection).

### 2.7 Engine-completion credit — `engine_completion_credit(candidate, spending)`

Mana units, derived with no new number from `LOOP_SHORTCUT_MANA`'s own
justification (`engine/constants.py:43-50`: one allowance already exceeds
any sink's need, so a second live loop's marginal mana is zero):

- `0` when `engine_live` (structural fix for "fetch a redundant
  self-untapper" — a new instance id registers as a new engine via
  `bool(after − before)` at `activation.py:399`, which the boolean credit
  cannot see);
- `LOOP_SHORTCUT_MANA − cmc` when `¬engine_live ∧ would_complete ∧` a sink is
  reachable at **post-engine mana** over hand ∪ battlefield ∪ library
  counting any tutor still available next turn;
- when the only access is the tutor being spent: **not 0** but
  `p_draw_in_n_turns(len(library), K_sinks, snap.opp_clock_discrete) ×
  (LOOP_SHORTCUT_MANA − cmc)` (exact hypergeometric, existing primitive
  `ai/outcome_ev.py:89`). A hard 0 here is the same cliff shape that made U2
  a no-op and would make Toolbox stop assembling on the Dimir T4 board
  (budget 3, Craterhoof unreachable) — that board is the design's negative
  control: *enabler still fetched, credit draw-discounted, X unchanged*.

### 2.8 The four readers

1. **Tutor delivery** — `engine/cast_manager.pick_creature_tutor_x_value:185`
   and `engine/activated_effects.pick_activated_tutor_x:229` replace
   `max(affordable, key=default_tutor_rank)` with the existing
   `choose_tutor_delivery` wrapper (`activated_effects.py:107-122`, which
   already falls back to `default_tutor_rank` with no callback). Iterate
   distinct candidate cmcs ≤ budget, memoise per affordable-set (a handful of
   calls, not 80). `_worth` returns a **three-tier order**
   `(delivers_lethal_line, engine_becomes_live, value)` where
   `engine_becomes_live = would_complete ∧ ¬engine_live`, the sink term lives
   **only in `value`** (`engine_completion_credit·per_mana + creature_threat_value(c, snap)`,
   clock units — never a per-mana ratio), and `delivers_lethal_line` is the
   explicit cast-time computation of §2.3 weighted by §2.5. This keeps
   `tests/test_tutor_credit_for_engine_completing_piece.py` and
   `tests/test_unbounded_untap_mana_engine_shortcut.py::test_tutor_delivery_ranks_the_engine_completing_piece_first`
   **green unedited** (the winner's original key broke all three). The
   ungated `LOOP_SHORTCUT_MANA` branch at `ai/activation_ev.py:176-182` is
   deleted in the same diff. When there is no line and no completer the
   picker **falls through to `default_tutor_rank`**, so Amulet Titan's GSZ
   delivery is byte-identical (its anchors are protected without measuring
   it as a mover).
2. **Cast / activation EV** — `compute_play_ev`'s win-swing credit
   (`ai/ev_evaluator.py:3327-3354`) gains a tag-free second trigger:
   `assembly.best_line and card is best_line.first_step`, placed **outside**
   the `_uses_combo_chain_scoring` gate (that gate is true only for
   archetype `combo`, which would silently exclude an Overrun shell or a Tron
   Ballista line). `_gate_x_tutor_payoff` (`:1078-1086`) and
   `activation_candidates` (tutor `:896-897`, `DAMAGE_ANY_TARGET` `:604-607`,
   and the new `PUT_COUNTER_TEAM` branch) read `engine_completion_credit` and
   add `p_resolves × win_swing` when `(perm, ability_index)` is the line's
   first step — so the first Leyline activation is scored as the line it
   starts, not as one counter against `PLAY_VALUE_FLOOR`. In
   `_gate_x_tutor_payoff`'s hold clause, `accelerates |=
   would_complete_unbounded_engine(target)` (an engine completion is
   acceleration; without it, routing the picker through the callback makes
   `top` the payoff and the hold withholds the Dimir T4 enabler fetch).
3. **Attack** — `decide_attackers`' lethal test reads
   `attack_reach(valid, through_blocks=False)` — a pure refactor pin so the
   projector and the declarer cannot drift. The existing keep-home rule
   (`noncombat_opportunity_cost`, engine-membership term) already parks loop
   pieces.
4. **Goal choice** — DEPLOY_ENGINE may advance on `engine_live`; the RAMP
   transition is **deferred** (§7) because it changes Amulet/E-Tron goal
   timing with no Toolbox benefit (the 08-30 anchor rule).

## 3. Goal decomposition (FWM) — the node this design owns

Root supervisory goal: **Convert Assembled Engine Into Lethal Line**
(result: the deck's mana engine, once live, is converted to opponent life ≤ 0
within the turn a line exists; scope in: registered decks with an unbounded
loop or a typed sink; scope out: Storm chains, non-mana loops such as
Broodscale's counter/sacrifice loop). Children, ordered by result dependency,
each a leaf with one owner and one rule-phrased test:

| id | function (Verb + Qualified Object) | type | owner |
|---|---|---|---|
| 1.1 | Recognise Engine Assembly State | TOOL | `assemble()` ← `ActivationManager` rules queries |
| 1.2 | Identify Reachable Mana Sinks | TOOL | `is_mana_sink` + access enumeration |
| 1.3 | Project Sink Damage Line | TOOL | `sink_damage`, `attack_reach` |
| 1.4 | Price Line Resolution Probability | TOOL (BHI) | `p_resolves` via the single `p_interaction` + tax branch |
| 1.5 | Select Payoff Delivery Target | TOOL | `choose_tutor_delivery._worth` (three-tier order) |
| 1.6 | Credit Line First Step | TOOL | `compute_play_ev` win-swing trigger (one site) |
| 1.7 | Declare Lethal Attack | TOOL | `decide_attackers` ← `attack_reach` |

Completeness: remove 1.2 → no line can exist; remove 1.4 → the countered
X=8 line is chosen (the traced loss); remove 1.5 → X is priced for a target
that is not delivered; remove 1.6 → the line exists but is never the best
play (the +0.0 pass). Every leaf is deterministic code — this subsystem is
the sim-time spine the agentic architecture wraps; no leaf needs a model.

## 4. Data contract

Sinks and tutors come from typed `CardTemplate` / `ActivatedAbility` fields
(`x_cost_data`, `deals_targeted_damage`, `team_pump_data`,
`x_creature_tutor_data`, `tutor_data`, the new `put_counter_data
scope='team'`); payoffs from `decks/gameplans/*.json card_roles`. **No new
JSON key is required.** An optional `card_roles.sinks` bucket (free-form
buckets already flow through `gameplan_loader._parse_goal`) may declare
non-typed outlets, but a JSON-declared sink contributes **only to sink
existence / `payoff_affordable`, never to `sink_damage` or `best_line`** (no
typed shape → no projection → no weight). Data-only follow-up: move Walking
Ballista out of `always_early` in `creatures_toolbox.json` into the
EXECUTE_PAYOFF payoffs bucket so the sink is not cast at X=1 as a body while
an engine is assemblable. No card or deck name appears in any proposed `.py`
logic; names below are test-fixture carriers.

## 5. Implementation units (ordered; each failing-test-first)

Amendment applied: the flat-by-construction primitive is **not** its own
commit on this lane (it would be the third no-movement commit and halt the
lane before the movers ship). U0 and U1 are engine rules-correctness units
that stand on their own; U2+U3 land as **one measured commit**.

- **U0 — one owner of X sizing.** *Verified-before-build, 2026-09-24: not a
  defect.* The refuters read the inline budget in `CastManager.cast_spell`
  (`:1828`) as dividing raw capacity; it does not over-budget in effect,
  because the base cost is paid (lands tapped) BEFORE the X block reads
  `untapped_mana_capacity()`, so the budget it sees is already net of the
  fixed pips — and, unlike `affordable_x`'s printed-cmc formula, net of any
  cost reduction actually applied. Replacing it with `affordable_x` after
  payment would subtract the base twice. The rule is pinned instead:
  `tests/test_x_cost_paid_never_exceeds_capacity_minus_pips.py` (CR 601.2h;
  green on the unchanged engine — 3 lands, {X}{G}, X ≤ 2 with a 3-drop
  reachable only at X=3). The AI-side copy (`ai/ev_player.py:1059`) is the
  same formula as `affordable_x` (`snap.my_mana − cmc`).
- **U1 — `PUT_COUNTER_TEAM` is an executable activated-ability class.**
  `engine/oracle_parser.py` (`put_counter_data` gains `scope='team'`),
  `engine/cards.py` (kind), `engine/activation.py` (`RESOLVABLE_EFFECT_KINDS`),
  `engine/activated_effects.py` (resolve through
  `CardInstance.add_plus_counters` — single-owner does not grow); auditor
  invariant `122/team_counter_placed`. Tests: *a "put a +1/+1 counter on each
  creature you control" activation is legal to activate and places one
  counter on every creature its controller controls*; *it is refused with
  insufficient mana*. Rules-correctness for a class the census had as
  UNCLASSIFIED; measured field is expected flat (the AI does not yet value
  it) — it is an engine unit, not a lane swing.
- **U2 — `ai/assembly_state.py`** (§2.1–2.7, incl. `win_swing` lifted to
  `ai/clock.py`, `is_mana_sink` wrapper in `combo_calc`, `attack_reach` pin in
  `decide_attackers`); add `"ai/assembly_state.py": 0` to
  `tools/magic_numbers_baseline.json` and keep it at 0. Tests:
  *an X-damage sink projects the X the cast path would size*; *a mass-pump
  sink projects the pumped attack of the untapped team through blocks after
  payment-tapping*; *a mana-scaled team-counter activation on the battlefield
  with a live engine projects a lethal line*; *a zero-toughness X sink
  delivered to the battlefield without entry counters is not an access*;
  *a token maker that scales with something other than mana is not a sink*;
  *a live engine has no completers and no second allowance*; *an engine piece
  keeps a draw-discounted credit when its sink is only in the library*; *the
  best line maximises resolution-weighted win swing, not damage*; *an all-in X
  tutor whose payment leaves the opponent's tax counter live does not outrank
  a fetch that keeps the tax payable*; *the attack declarer and the line
  projector read the same reach function*.
- **U3 — the four readers** (§2.8). Tests: *an X tutor with budget for a
  lethal mass-pump payoff delivers the payoff, not the enabler, when the line
  resolves*; *the X charged is the X at which the delivery callback
  delivers*; *the AI-side gate and the cast-time picker agree on (X, target)*;
  *with the loop live a second self-untapper is not fetched over the
  payoff*; *without a line or a completer the picker delivers exactly what
  default_tutor_rank delivers*; *the first step of a lethal line is credited
  the win swing outside the combo-chain gate*; *a sub-lethal line earns no
  win swing*; *Storm chain credit is unchanged*. U2's and U3's fixtures
  **wire the runner's AI callbacks** (`engine/game_runner.py:282-288`) or set
  `game.callbacks` explicitly — a bare `GameState` uses `DefaultCallbacks`
  and would pin the fallback path (the existing
  `tests/test_x_tutor_payoff_aware_selection.py` fixtures are updated the
  same way before any "stays green" claim).

**Execution status (2026-09-24):** U0 verified-before-build (pinned), U1
shipped `af51c42`, U2+U3 shipped as one commit `53c95a5` — replay gate
passed (Toolbox wins s60500 G1 on T4 through the team-counter ability
line), Creatures Toolbox field 21.2 → 28.3 same-seed. Deferred from U2+U3
and recorded in the tracker: the BHI tax-counter branch of `p_resolves`
(no soft-counter posterior API yet), the picker rewrite through
`choose_tutor_delivery`, `tutor_to_hand` accesses, `payoff_affordable`.

Anchor: U0/U1 expected no flips (rules units; any flip is replayed
anchor-exact and accepted only as rules-correct). U2+U3: every
`test_wr_baseline_anchor.py` flip must show a `lethal_line` decision event in
the diverging game's log tail, or it is a regression, not a refresh.

## 6. Risks and non-goals

- **Falsified clock-cliff — structurally different.** `ai/clock.position_value`
  is untouched in every form; the only new credit is the existing `win_swing`
  fired when a projected line reaches opp_life ≤ 0 (the 100 sentinel that
  already exists) — never a slope change on a non-lethal board. Jeskai Blink
  (the deck that fix moved) is the canary guard.
- **Over-reach onto non-combo decks.** The trigger is a mechanic, so any deck
  holding an X-damage spell or a team pump gets it (an aggro deck firing an X
  burn at exact lethal is correct play). Inertness observable: emit a
  `lethal_line` reason in the decision NDJSON and require **zero events** for
  every registered deck without an `x_damage` / `team_pump` /
  `team_counters` card in its 60 (Boros, Zoo, Prowess, Dimir, Storm, Jeskai).
- **Optimistic projection.** Blocks are modelled, instant-speed removal and
  fogs are not; the cast side is BHI-discounted, the activation side is
  discounted only by the tax branch. Mitigation: boolean-gated on lethality,
  so an over-projection costs one line, not every turn's valuation.
- **Wall-clock.** The callback builds a snapshot per call; memoise per
  affordable-set and thread the caller's state; time a Toolbox-vs-Zoo and a
  Zoo-vs-E-Tron Bo3 pre/post U2 before any field number is read
  (`GAME_TIMEOUT_SECONDS` truncation would silently corrupt results).
- **Single-deck lane, stated plainly.** Typed-sink census over every
  registered deck: only Creatures Toolbox holds a sink in its 60 (Eldrazi
  Tron's Ballista is sideboard-only — post-board signal at most; Amulet Titan
  has a tutor but no sink, and its GSZ delivery is unchanged by design).
  Generalisation is by **class** (§7), not by a second registered mover.
- **Loop-break exposure.** This is the third structural unit on the
  unbounded-mana lane. It qualifies because it is replay-named and changes
  the *decision* (target, X, outlet), not a magnitude. If the measured field
  is flat, the lane halts and the halt doc is the replay gate below.
- **Not covered.** Broodscale's counter/sacrifice loop is not a mana engine
  and has no mana sink (`engine_live=False`, nothing changes). The Duskwatch
  to-hand digger stays UNCLASSIFIED (separate unit).

## 7. Generalisation

By class, with zero code change per new printing: every `ActivationManager`
loop (Druid+Vizier / Solemnity, Kinnan+Monolith, Pili-Pala+Grand Architect,
Freed-from-the-Real auras); every X-damage sink; every ETB/spell team pump;
every team-counter activation (Gavony Township, Mikaeus, Ajani-shape); every
X creature tutor and activated tutor. A new Overrun variant or X-burn is
classified at DB load and enters the line search. Deferred to a separate
lane (they alter Amulet/E-Tron goal timing with no Toolbox benefit): the
S-2 finisher lockout generalised from STORM-keyword finishers to
`best_line` (with the Dimir T4 state as its negative control — no line at
budget 3), and a RAMP goal transition (`ramp_goal_ready(goal, me, state)`
consumed by both `gameplan.py:570-572` and `check_transition`, one owner).

## 8. Measurement and the replay gate

Pre-conditions: `MTG_LLM_DECISION_SCORER_OFFLINE=1`, quiet box, Bo3, same
seeds (50000 grid step 500, n=20), nothing pending on the measured path.

1. **Replay gate before any field run** (amendment A6): on
   `--bo3 "Creatures Toolbox" "Domain Zoo" -s 60500` G1 T4 the AI must EITHER
   resolve a lethal line through the on-board team-counter activations then
   alpha (uncounterable by Denial) OR withhold the X tutor at X=2 with the
   tax counter left dead — **not** "X=8 + alpha", which the rebuilt board
   shows is countered. Second gate: `toolbox_dimir.txt` T6 (`:809-827`) —
   the AI must not all-in a lethal-line tutor into UU open with a BHI-inferred
   hard counter unless `p_resolves × win_swing` still beats the alternatives.
   Negative control: Dimir T4 — enabler still fetched, credit discounted, X
   unchanged.
2. **Decision counts on the s60500 replays** (before the field): (a) tutor
   resolutions delivering a typed sink with the engine live; (b) main-phase
   passes with the loop live and a reachable sink unused (must fall to 0 for
   ability sinks); (c) X paid when a lethal-now payoff is fetchable, split by
   whether the opponent had open mana plus a BHI-inferred counter; and the
   share of lines that were ability-based vs spell-based. If the spell-based
   share is countered at the traced rate (2/2), the movement estimate is
   revised **before** the field run.
3. **Movers** (same-seed n=20 field, pre vs post U2+U3): Creatures Toolbox
   field (21.5 → ≥ +2.2pp), its cells vs Domain Zoo / Dimir / Boros.
   **Guards:** Domain Zoo, Boros Energy, Ruby Storm, Jeskai Blink fields
   (flat); `--matchup boros affinity -n 20`; the anchor rule above; all
   ratchets; both CI chunks one at a time.
4. **Decision rule:** ≥ +2.2pp with guards flat → lane counter resets; flat
   → third flat unit, lane HALTS per CLAUDE.md, and the halt doc records which
   of the two traced decisions did not flip and why (the tracker's own
   caveat: the sink turn pre-empted by a sweeper).

## 9. Why this moves where Lane-T and U2 did not

Both prior fixes changed a credit's magnitude and left the **target pick, the
X paid, and the executable outlet** unchanged — the two decisions that lose
the traced games were never in scope. This design changes all three: the
engine learns to execute the outlet that is actually on the battlefield
(U1), the line search prefers the uncounterable ability line over an all-in
spell into open tax mana (U2 §2.5), the tutor's delivery and X are the ones
the line values (U3 reader 1), and the line's first step becomes the
highest-EV play once it exists (U3 reader 2). Decision (2) — 81 mana, Leylines
on board, pass — becomes ten activations and an alpha strike on T4; decision
(1) becomes either the payoff at a resolving X or a held tutor with the tax
kept dead. Expected movement is bounded by how often those states occur
before a sweeper ends the engine, which is exactly why the replay gate and
the decision counts are read before any field number.
