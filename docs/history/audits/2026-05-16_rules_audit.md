---
title: Rules-engine audit — 2026-05-16
status: active
priority: secondary
session: 2026-05-16
depends_on:
  - docs/history/audits/2026-04-26_storm_pro_audit.md
  - CLAUDE.md
tags: [audit, rules-engine, comprehensive-rules]
summary: >
  Engine rules-fidelity audit across four Bo3 replays. Five structural
  findings; the worst (impulse-draw mis-routed through draw_cards()) is
  causally outcome-decisive — Storm self-kills against Bowmasters in
  audit_storm_vs_dimir_s60101 G1.
---

## Executive summary

- **Overall rules-fidelity grade: 6/10.** Stack/phase plumbing, mulligan,
  fetchland life-loss, storm-count, splice-onto-Arcane, Ral coin-flip,
  Escape, and X-cost board wipes resolve correctly. The serious gaps are
  in (a) the "exile-top-N, may play this turn" family being routed through
  the normal `draw_cards()` path, (b) one card-named handler whose hard
  code drifted from current Oracle, (c) silent ETB triggers on
  modal-DFC/triome lands, (d) a kicker-spell whose resolution is silent,
  and (e) a permission flag (Teferi, Time Raveler's static "opponents
  cast only as sorcery") that is never enforced.

- **Top 3 systemic rules issues (lifted to mechanism):**
  1. **Draw-trigger amplification on impulse-draw.** Reckless Impulse,
     Wrenn's Resolve, Glimpse the Impossible, March of Reckless Joy
     (X-cost) and similar are documented in
     `engine/oracle_resolver.py:431-463` as deliberately *approximated*
     via `game.draw_cards(...)`. This makes every such spell trigger
     "whenever an opponent draws" effects (Orcish Bowmasters,
     Sheoldred, Underworld Dreams), in violation of CR 121.1 / 117
     (draw is a specific action; impulse-draw is *not* a draw).
     **Outcome-decisive in audit_storm_vs_dimir_s60101 G1 T4** (Storm
     self-kills 10 → 0 against two Bowmasters).
  2. **Per-card handler in engine has stale Oracle text.** Galvanic
     Discharge's effect (`engine/card_effects.py:586-629`) targets the
     *opponent*, but current Oracle restricts targeting to "creature or
     planeswalker". Visible in
     `audit_boros_vs_azorius_s60103.txt:454` ("deals 3 to opponent"),
     and the energy-spent ceiling (5) hides a different bug: at
     resolution it spends *all* stored energy up to 5, so a Boros board
     with 1 stored energy from Guide of Souls deals 4 damage rather
     than 1+0 = 1 (`audit_boros_vs_azorius_s60103.txt:687-688`,
     `:763`).
  3. **Land ETB triggers are silently skipped on ETB-tapped lands
     with `when this land enters, surveil 1`.** Meticulous Archive,
     Elegant Parlor, Thundering Falls, Hedge Maze, Lush Portico and
     others print the same ability; surveil only fires in
     `engine/cast_manager.py:1198-1205` for *spell-cast* triggers on
     creatures with surveil keyword in their text. The land-ETB
     trigger path is missing entirely.

- **Top 3 areas the engine handles correctly:**
  1. **Storm count under CR 702.40** — counted across both players'
     spells, splice does not double-count, counter-and-cast Wrenn's
     Resolve still counts as cast. Verified by manual count against
     every "Storm copies: N" line in all four replays.
  2. **Escape costs.** Phlage's escape pays `{R}{R}{W}{W}` + exile 5,
     and the "When Phlage enters, sacrifice it unless it escaped"
     trigger correctly does **not** fire after Escape
     (`audit_boros_vs_azorius_s60103.txt:823-828`).
  3. **Fetchland + shockland life payment** (CR 305.9 special
     action + 305.7 replacement). Bloodstained Mire → Sacred Foundry
     untapped correctly costs 1 (fetch) + 2 (shock) = 3 life. Verified
     across all four replays.

## Per-match findings

### Match 1 — Azorius Control vs Ruby Storm (seed 60100, Storm won 2-0)

**File:** `/home/user/MTGSimManu/replays/audit_azorius_vs_storm_s60100.txt`
**NDJSON:** `/home/user/MTGSimManu/replays/audit_azorius_vs_storm_s60100.ndjson`

#### Finding A1 — Impulse-draw mis-routed through draw_cards()

- **Decision/event:** G1 T9 (text lines 593-670), NDJSON `seq:~340` —
  `T9 P2: Glimpse the Impossible → draw 3 (Reckless Impulse, Sunbaked
  Canyon, Ruby Medallion)`. Reckless Impulse and Glimpse the
  Impossible both log as `→ draw N`.
- **Symptom:** Storm's impulse-draw spells route through
  `engine/game_state.py:219` `draw_cards()` rather than an exile-and-
  may-play resolver. In this game Azorius had no Bowmasters in play so
  no damage triggered, but the cards land in Storm's hand and persist
  past end-of-turn — Oracle restricts Reckless Impulse to "play those
  cards until end of your next turn" (CR 117.10 / 608.2k).
- **Rule violated:** CR 121.1 ("To *draw a card*, a player puts the
  top card of their library into their hand"). Impulse-draw is an
  exile-and-grant-permission effect; it is not a draw. CR 608.2k
  governs the "may play" window. Bowmasters' Oracle: "whenever an
  opponent **draws** a card" — by CR 121.1c, an effect that puts
  exiled cards into hand is not drawing.
- **Mechanism (generic):** Any spell whose Oracle says *"Exile the
  top N cards of your library. … you may play those cards…"*
  (Reckless Impulse, Wrenn's Resolve, Glimpse the Impossible, March
  of Reckless Joy, Light Up the Stage, Valakut Awakening's
  face-down side, Experimental Augury, Bonecrusher Giant's
  Stomp-recursion via Brainstone, etc.) must NOT be routed through
  `draw_cards()`. They must exile-into-a-zoned-stash, mark the cards
  with a "playable_until=eot+1" timestamp, and at the end-of-turn step
  remove the permission.
- **Class size:** ~30 cards in Modern alone match the predicate
  `"exile the top" ∧ ("you may play those cards" ∨ "you may play
  that card")`. The list grows every set (Plumb the Forbidden,
  Light Up the Stage, etc.).
- **Subsystem owner:** `engine/oracle_resolver.py:428-471` is the
  documented approximation point; `engine/game_state.py:219-269`
  is the trigger fan-out for draws.
- **Failing test (rule-phrased):**
  `tests/test_impulse_draw_does_not_trigger_opponent_draw_clauses.py::
  test_impulse_draw_does_not_fire_bowmasters` — set up Storm with
  Reckless Impulse and opponent with Orcish Bowmasters; cast Reckless
  Impulse; assert Storm's life unchanged.
- **Lift-check:** Living End's cascade target Living End reanimates
  graveyard creatures; Goryo's Vengeance reanimates from graveyard —
  neither is "draw". Same predicate-driven dispatch should split
  these out cleanly. **Other decks that benefit when fixed:**
  *Izzet Prowess* (Light Up the Stage same family), *Boros Energy*
  (rare Bauble/Storm gates), and any deck running Plumb the
  Forbidden or Augury. Storm-side test required by CLAUDE.md
  generalisation rule, and the bug also lands a Ravenous Rats /
  Sheoldred-style "whenever you draw" misfire that affects future
  Dimir/Esper deckbuilds. *This is the single most impactful fix in
  the audit.*

#### Finding A2 — Land-ETB surveil never fires

- **Decision/event:** G1 T6 (line 331)
  `T6 P1: Play Thundering Falls (enters tapped)`. G1 T8 (line 480)
  `T8 P1: Play Meticulous Archive (enters tapped)`. G1 T4 (line 249)
  `T4 P2: Play Elegant Parlor (enters tapped)`. No surveil line
  follows any of these.
- **Symptom:** Lands with the surveil-1 ETB trigger never fire it.
- **Rule violated:** CR 603 (triggered abilities) and CR 701.40
  (surveil). When a permanent enters with a triggered ability, it
  goes on the stack at the next opportunity.
- **Mechanism (generic):** The engine resolves surveil only for
  *creature* spell-cast triggers (`cast_manager.py:1198-1205`); it
  has no land-ETB hook. A general "permanent ETB triggers from
  Oracle text" pass is missing for the land subset.
- **Class size:** All "Surveil dual cycle" lands (Meticulous
  Archive, Lush Portico, Hedge Maze, Underground Mortuary, Raucous
  Theater, Commercial District, Undercity Sewers, Elegant Parlor,
  Thundering Falls, Shadowy Backstreet) plus Triomes that surveil,
  plus Watery Grave / Fatal Push targets — order of 30+ cards
  Modern-legal.
- **Subsystem owner:** `engine/land_manager.py` (land play) +
  `engine/oracle_resolver.py` (ETB-on-permanent generic resolver).
- **Failing test (rule-phrased):**
  `tests/test_land_etb_surveil_triggers.py::
  test_etb_surveil_one_puts_top_to_graveyard_or_returns`.
- **Lift-check:** Every Standard/Modern surveil-land player benefits
  (Azorius Control, Dimir Midrange, 4c Omnath, Jeskai Blink). Same
  hook lights up `When ~ enters, [scry/draw/lose-life/gain-life]`
  for triome-style cards — same predicate class.

#### Finding A3 — Consult the Star Charts resolves silently (kicker not modeled)

- **Decision/event:** G1 T8 line 482-485 — `Cast Consult the Star
  Charts (5U) (X=5)` then `Resolve Consult the Star Charts` with
  no card-into-hand log line.
- **Symptom:** The cost-notation `(5U) (X=5)` is wrong — Consult is
  not an X-spell; its Oracle says "Look at the top X cards… **where
  X is the number of lands you control**". `engine/oracle_parser.py:
  219-246` (`parse_x_cost`) keys on the substring " X " in oracle
  text and falsely tags Consult as an X-spell, so the cast-time
  cost computation routes through the X-cost branch
  (`cast_manager.py:937-1040`). The resolution then has no effect
  registered, so the spell does nothing.
- **Rule violated:** CR 107.3 (X in mana costs) — the X in the
  body of an instruction is *not* an X in the mana cost. Kicker
  (CR 702.33) is also not modeled here.
- **Mechanism (generic):** `parse_x_cost` is over-broad — it
  shouldn't fire on "X" tokens that appear only in a non-cost
  context (e.g., "look at the top X cards … where X is the number
  of [board signal]"). The {X} in the mana cost string is the
  authoritative signal.
- **Class size:** Consult the Star Charts, Briber's Purse,
  Mirage Mirror's quantum sibling, Ancient Excavation, future
  Standard cards in that mold. Likely 8-15 Modern-legal cards.
- **Subsystem owner:** `engine/oracle_parser.py` (`parse_x_cost`)
  + the missing kicker pipeline.
- **Failing test (rule-phrased):**
  `tests/test_parse_x_cost_excludes_oracle_body_x.py::
  test_x_in_oracle_body_not_in_mana_cost_returns_none`.
- **Lift-check:** Frees the resolver to handle Kicker correctly
  (Consult, Plumb the Forbidden, Sunscourge Champion, eventually
  any kicker spell). Cleans up `oracle_parser.parse_x_cost`'s
  false positives.

### Match 2 — Ruby Storm vs Dimir Midrange (seed 60101, Dimir won 2-1)

**File:** `/home/user/MTGSimManu/replays/audit_storm_vs_dimir_s60101.txt`
**NDJSON:** `/home/user/MTGSimManu/replays/audit_storm_vs_dimir_s60101.ndjson`

#### Finding B1 — Outcome-decisive instance of Finding A1

- **Decision/event:** G1 T4 — text lines 240-254. NDJSON `seq` range
  in Main1 of game 1 turn 4 (decision_ids around `g1t4d12`-`g1t4d20`).
- **Symptom:** Storm enters T4 at 10 life. Casts Ral (1R) → Desperate
  Ritual → Reckless Impulse `→ draw 2 (Pyretic Ritual, Ral)` →
  Pyretic Ritual → Glimpse the Impossible `→ draw 3 (Ruby Medallion,
  Desperate Ritual, Bloodstained Mire)` → `P1 loses: life total 0`.
  Storm took 10 damage in the chain, all of it from Bowmasters
  triggers attached to impulse-"draws". Dimir had **two** Bowmasters
  in play (one from T2, one from T3). Reckless Impulse "drew" 2 ×
  2 Bowmasters = 4 damage; Glimpse "drew" 3 × 2 Bowmasters = 6
  damage. Total 10. Storm dies as a direct consequence of
  Finding A1.
- **Rule violated:** CR 121.1 (definition of draw) and Bowmasters'
  Oracle: "whenever an opponent draws a card except the first one
  they draw in each of their draw steps". Impulse-draw is not a
  draw.
- **Mechanism (generic):** Same as Finding A1.
- **Class size:** Same as A1; this match supplies the smoking-gun
  measurement (10 self-damage from 5 impulse-draws against 2
  Bowmasters in a single turn).
- **Subsystem owner:** Same as A1
  (`engine/oracle_resolver.py:428-471`).
- **Failing test (rule-phrased):**
  `tests/test_impulse_draw_does_not_trigger_opponent_draw_clauses.
  py::test_glimpse_the_impossible_with_two_bowmasters_deals_zero_
  self_damage` (same suite as A1, distinct case asserting that the
  Storm/Glimpse/2× Bowmasters scenario lets Storm finish T4 with
  ≥1 life).
- **Lift-check:** Lifts *all* Modern decks containing impulse-draw
  vs Bowmasters/Sheoldred — Storm, Izzet Prowess, Boros Energy
  (Stage/Bauble), Dimir Mirror, Jeskai Blink.

### Match 3 — Dimir Midrange vs Boros Energy (seed 60102, Boros won 2-0)

**File:** `/home/user/MTGSimManu/replays/audit_dimir_vs_boros_s60102.txt`
**NDJSON:** `/home/user/MTGSimManu/replays/audit_dimir_vs_boros_s60102.ndjson`

#### Finding C1 — Galvanic Discharge per-card handler is stale & illegal-target

- **Decision/event:** Not present in this specific text (Boros did
  not cast Galvanic Discharge here). Cross-match evidence:
  `audit_boros_vs_azorius_s60103.txt:322-327` and `:683-688` and
  `:759-763` — `Galvanic Discharge deals 3 to opponent` /
  `deals 4 to opponent` from a single energy point on Guide of
  Souls.
- **Symptom:** `engine/card_effects.py:586-629` has a per-card
  handler whose code targets `opp.life` directly (line 626) on the
  fallback path. The current printed Oracle is:
  > "Choose target **creature or planeswalker**. You get {E}{E}{E},
  > then you may pay any amount of {E}. Galvanic Discharge deals
  > that much damage to **that permanent**."
  The handler's `if target_creature is None: opp.life -= damage`
  branch lets the spell target a player, which is illegal. The
  energy-pay logic (`energy_to_spend = min(player.energy_counters,
  5)`) also dips into pre-existing energy and ignores the
  decision-time choice, so a single Guide-of-Souls energy point
  combined with the spell's own +3 stores 4 energy and pays 4,
  dealing 4 damage in a context where the printed effect should
  have produced 0-4.
- **Rule violated:** CR 115.1 (a spell with "target X" requires a
  legal target of that type). CR 117.2 (cost choice at cast time,
  not at resolution).
- **Mechanism (generic):** Per-card hardcoded handlers in
  `engine/card_effects.py` are a card-name-driven escape hatch
  forbidden by the abstraction contract. Galvanic Discharge's
  current text is the *generic* pattern `Target X. You get
  {E}^n, then pay any amount of {E}. ~ deals that much damage to
  that permanent.` — already covered by the existing target-solver
  patterns (`engine/target_solver.py:155` "target creature or
  planeswalker"). The handler should be deleted, and replaced
  with an Oracle-driven "deal X damage where X = energy paid"
  effect that drops out of generic energy/damage primitives.
- **Class size:** Cards with the "you get {E}{E}{E}, then pay any
  amount of {E}, deal that much damage" template are about a dozen
  (Galvanic Discharge, Static Discharge, Galvanic Iteration variant,
  energy-gain instants from MH3 / FDN). More importantly, removing
  *every* per-card handler from `engine/card_effects.py` (presently
  ~90 entries) is a long-running project that the abstraction
  contract is explicitly here to drive.
- **Subsystem owner:** `engine/card_effects.py:586-629` (deletion);
  `engine/oracle_resolver.py` (generic energy-damage handler).
- **Failing test (rule-phrased):**
  `tests/test_target_creature_or_planeswalker_rejects_player_
  target.py::test_galvanic_discharge_cannot_target_player`. Class-of-
  one in card name, but rule-phrased: the test asserts that any
  effect whose Oracle says "target creature or planeswalker"
  refuses a player-id in its `targets` array.
- **Lift-check:** Same pattern lifts Bonecrusher Giant's Stomp
  (target creature or PW), Lightning Helix when used against a
  PW, Galvanic Iteration's copies, and prevents future regressions
  for any card with the same target predicate.

#### Finding C2 — Ragavan exiles "land" and labels it "may cast"

- **Decision/event:** G1 T3 line 203-204, T4 line 259-260, T5 line
  328-329, T6 line 393-394 — `Ragavan, Nimble Pilferer exiles
  Polluted Delta from top of P1's library` / `may cast Polluted
  Delta this turn`.
- **Symptom:** Ragavan's Oracle says "Until end of turn, you may
  cast that card." Lands can't be cast (CR 117.1a). The engine
  correctly fails to cast (`returned to exile (uncast)`) but
  emits the misleading `may cast Land` log line *and* leaves the
  land in exile permanently (which is rules-correct — Ragavan
  doesn't return un-cast cards to the library).
- **Rule violated:** Cosmetic-only. The end-state is rules-correct;
  the log is misleading.
- **Mechanism (generic):** `Ragavan-style "exile, may cast"`
  effects should suppress the "may cast" message and switch to a
  "no cast permitted (land/uncastable)" message when the exiled
  card has no mana cost.
- **Class size:** Ragavan, Daxos of Meletis, Thief of Sanity,
  Knockout Blow, Goblin Cratermaker's flip-side, every "exile and
  may cast" variant. About 20 Modern-legal cards.
- **Subsystem owner:** Whichever module emits the "may cast" log
  line (likely `engine/card_effects.py` Ragavan handler) — but
  this is a *logging-only* fix.
- **Failing test (rule-phrased):**
  `tests/test_exile_may_cast_skips_land.py::
  test_exile_may_cast_does_not_offer_cast_for_lands`.
- **Lift-check:** Daxos of Meletis, Thief of Sanity, Wrenn and Six
  emblem etc. Low priority — cosmetic.

### Match 4 — Boros Energy vs Azorius Control (seed 60103, Boros won 2-0)

**File:** `/home/user/MTGSimManu/replays/audit_boros_vs_azorius_s60103.txt`
**NDJSON:** `/home/user/MTGSimManu/replays/audit_boros_vs_azorius_s60103.ndjson`

#### Finding D1 — Galvanic Discharge in action: 3 damage to face from 1 stored energy

- **Decision/event:** G1 T5 lines 322-327 — Storm-the-deck is not
  in this match; Boros is paying Galvanic Discharge for 3 face
  damage on a 7-life Azorius pilot with only one stored
  energy-counter on Guide of Souls. G1 T7 line 451-454: 3 to
  opponent again. G2 T4 line 685-688: 4 damage from same
  configuration. G2 T5 line 759-763: 4 damage face from 1 energy.
- **Symptom:** Per-card handler in `engine/card_effects.py:586-629`
  generates +3 energy on resolve, then `energy_to_spend =
  min(player.energy_counters, 5)`. With 1 pre-stored energy, this
  spends 4 (1 pre + 3 self-generated) and deals 4. Per Oracle, the
  caster *chooses* at cast time how much energy to pay, and pays
  it as a cost in the casting flow — not at resolve. The handler
  also targets opponent (illegal — Oracle restricts to "creature
  or planeswalker").
- **Rule violated:** CR 117.2 (cost-choice timing), CR 115 (legal
  targets).
- **Mechanism (generic):** Same as C1.
- **Class size:** Same as C1.
- **Subsystem owner:** Same as C1.
- **Failing test (rule-phrased):** Same suite as C1.
- **Lift-check:** Same as C1.

#### Finding D2 — Teferi, Time Raveler's static "opponents cast only as sorcery" is not enforced

- **Decision/event:** G1 T6-T9 — Teferi Time Raveler resolves
  on T3 P1 (line 158) and Azorius minus-3's once on T4 and once
  on T6, but no opponent-side cast on Storm's turns is blocked
  or restricted. In this match no opponent instant-on-Azorius-
  turn cast occurred so this is *latent* rather than triggered,
  but its companion match
  (`audit_storm_vs_dimir_s60101.ndjson`, G3 T6 P1 Counterspell)
  shows Dimir casting Counterspell *during Storm's main phase* at
  line 967, against a Reckless Impulse, with no Teferi on either
  side — fine for that match. The latent failure is the static
  ability's enforcement.
- **Symptom:** Teferi's static text is logged as a `[+1]` event
  ("Until your next turn, you may cast sorcery spells as though
  they had flash") and a `[-3]` event. The static "Each opponent
  can cast spells only any time they could cast a sorcery" is
  never modeled — searches of `engine/` for `as_though_sorcery`,
  `can_cast_only_at_sorcery_speed`, `opponent_cast_speed`,
  `sorcery_speed_only`, etc. find nothing.
- **Rule violated:** CR 117.1c — a permanent's static ability
  modifies what's legal. Teferi Time Raveler restricts opponents'
  cast timing.
- **Mechanism (generic):** Static "X can cast Y only at sorcery
  speed" abilities need a registry in the priority handler. The
  predicate is Oracle-text-driven: any permanent with text
  matching `r"opponents? can('?t| cannot| can) cast .*only any time
  they could cast a sorcery"` should flip a per-player flag the
  priority manager consults.
- **Class size:** Teferi, Time Raveler; Grand Abolisher; Conqueror's
  Flail; Vryn Wingmare's "spells cost {1} more" cousin pattern;
  Drannith Magistrate; about 15 Modern-relevant cards.
- **Subsystem owner:** Priority manager (likely
  `engine/cast_manager.py` `can_cast`) + a static-effect registry
  rebuilt each turn.
- **Failing test (rule-phrased):**
  `tests/test_sorcery_speed_lockout_static_abilities.py::
  test_opponent_cannot_cast_instant_when_teferi_static_active`.
- **Lift-check:** Lifts Azorius Control (Teferi TR vs Storm),
  Mono-W humans (Grand Abolisher), Equipment decks (Conqueror's
  Flail). Same registry catches future cards in this template.

## Cross-match patterns

### Pattern 1 — Impulse-draw as draw (A1 = B1)

Two of the four matches contain the bug; one (G1 of Storm vs Dimir) is
outcome-decisive. The fix is the same single mechanism: split the
impulse-draw path out of `draw_cards()`. **Highest-priority finding in
the audit.**

### Pattern 2 — Per-card handlers in `engine/card_effects.py` go
stale (C1 = D1 = D2-flag)

The `@EFFECT_REGISTRY.register("Card Name", ...)` decorator pattern in
`engine/card_effects.py` is itself an abstraction-contract risk: any
Oracle update to a hardcoded card breaks silently. The Galvanic
Discharge code references an old Oracle ("deal that much damage to that
permanent" *is* the new wording; the handler treats it as "deal that
much damage to any target" using legacy semantics). **The right fix is
to delete `engine/card_effects.py` per-card handlers** wherever the
Oracle pattern is generic, and route through `oracle_resolver.py`.

### Pattern 3 — ETB triggers from Oracle text are inconsistently fired
(A2)

Triome and surveil-dual lands' "When this land enters, surveil 1"
trigger silently no-ops. The same module that handles creature-ETB
triggers (`engine/oracle_resolver.py`) does not iterate over Land
permanents on land-play.

## Recommended fixes (ranked, all structural)

### Fix 1 — Split impulse-draw out of `draw_cards()` *(highest impact)*

- **Change:** In `engine/oracle_resolver.py:428-471`, replace the
  approximation branch (lines 448-463) with a dedicated `def
  resolve_impulse_draw(...)` that exiles the top N cards into a new
  `player.impulse_zone[card_id]` tracking dict, records the
  end-step boundary, and grants permission to play. The play
  pipeline (`engine/cast_manager.py`) must consult this dict to
  authorise the spell-from-exile path.
- **Test (rule-phrased):**
  `tests/test_impulse_draw_does_not_trigger_opponent_draw_clauses.py`
  — same suite from A1/B1, plus the converse (`Sheoldred,
  Drown_in_the_Loch_with_draws`, `Underworld Dreams`).
- **Lift-check:** Storm, Izzet Prowess, Boros Energy (Stage/Bauble),
  4c Omnath (Bauble triggers), Affinity (Thoughtcast variant).
- **Class size:** ~30 cards.
- **Why structural:** No card-name dispatch; the predicate is the
  Oracle phrase `"exile the top N" + ("you may play those cards"
  ∨ "you may play that card")`. Already present in the engine's
  current approximation comment — we just need to honour it.
- **6-point contract check:** ✓ no `card.name ==`, ✓ no deck-gate,
  ✓ no new magic numbers, ✓ failing test names a mechanic, ✓
  lift-check covers ≥4 decks, ✓ change in `engine/` only.

### Fix 2 — Delete Galvanic Discharge per-card handler, route through Oracle

- **Change:** Delete
  `@EFFECT_REGISTRY.register("Galvanic Discharge", …)` in
  `engine/card_effects.py:586-629`. Move the energy-and-damage
  logic into `engine/oracle_resolver.py` keyed on Oracle text
  pattern `"You get {E}+ then you may pay any amount of {E}.
  ~ deals that much damage to target (creature|creature or
  planeswalker)"`. Reuse the existing target solver for "target
  creature or planeswalker" (`target_solver.py:155`).
- **Test (rule-phrased):**
  `tests/test_target_creature_or_planeswalker_rejects_player_target.py
  ::test_galvanic_discharge_cannot_target_player`.
- **Lift-check:** Same predicate lifts Static Discharge,
  generalized energy-damage cards. Also stops Lightning Helix
  hard-coded handlers from drifting in the same way.
- **Class size:** ~12 cards immediate; ~90 per-card handlers
  total in the file that need similar treatment over time (long
  arc).
- **6-point contract check:** ✓ all six. The fix *reduces*
  abstraction-baseline by 1 in `tools/abstraction_baseline.json`.

### Fix 3 — Land-ETB triggers from Oracle text

- **Change:** In `engine/land_manager.py` after a land enters,
  invoke the Oracle-ETB resolver path used for creatures
  (`engine/oracle_resolver.py:resolve_etb_trigger`). The
  permanent-type filter currently bails on lands.
- **Test (rule-phrased):**
  `tests/test_land_etb_oracle_triggers_fire.py::
  test_etb_surveil_one_on_meticulous_archive_puts_top_to_graveyard`.
- **Lift-check:** Surveil-dual cycle (Meticulous Archive et al.),
  Triomes (when they ETB tapped); also lights up Cabal Coffers /
  Castle Locthwain etc. when they enter.
- **Class size:** ~30 Modern-legal lands.
- **6-point contract check:** ✓ all six.

### Fix 4 — Static "sorcery-speed only for opponents" registry

- **Change:** Introduce a per-game `priority_restrictions[
  player_idx] = set(...)` rebuilt on each priority pass from
  battlefield permanents whose Oracle matches the predicate
  `r"each opponent can cast spells only any time they could cast a
  sorcery"`. `engine/cast_manager.py:can_cast` consults this when
  the active player is not the spell-controller and the spell is
  not normally castable at sorcery speed (i.e., no Flash).
- **Test (rule-phrased):**
  `tests/test_sorcery_speed_lockout_static_abilities.py::
  test_opponent_cannot_cast_instant_when_teferi_time_raveler_is_in_play`.
- **Lift-check:** Teferi TR, Grand Abolisher, Drannith Magistrate
  (similar pattern — restricts spell types).
- **Class size:** ~15 Modern-legal cards.
- **6-point contract check:** ✓ all six.

### Fix 5 — `parse_x_cost` excludes Oracle-body X

- **Change:** In `engine/oracle_parser.py:219-246`, tighten the
  predicate so the only signal for X-cost is `{X}` in the mana
  cost string. Remove the `' x '` / `'x '` fallback on Oracle
  text.
- **Test (rule-phrased):**
  `tests/test_parse_x_cost_excludes_oracle_body_x.py::
  test_consult_the_star_charts_is_not_an_x_spell`.
- **Lift-check:** Consult the Star Charts, Briber's Purse, Ancient
  Excavation, any future "look at top X cards" non-X-cost spell.
- **Class size:** ~8 cards.
- **6-point contract check:** ✓ all six.

## Unresolved — needs root-cause investigation

- **Consult the Star Charts resolves silently.** Fix 5 resolves the
  X-cost mis-tag but Consult still has no resolution effect
  registered; the look-at-N-and-keep-K pattern needs a generic
  resolver (`engine/oracle_resolver.py:438-445` only handles
  Sleight-of-Hand's keep-1 case). Generic pattern:
  `"look at the top N cards… put up to M of them into your hand…"`.
  Class size ~6 cards.

- **Hall of Storm Giants' creature-land animation.** Not observed
  in these four replays (the relevant `{5}{U}` activation is never
  used by the AI). Cannot confirm or deny rules fidelity from
  current evidence.

- **`Past in Flames` self-flashback.** PiF goes to graveyard after
  resolving; PiF is an instant/sorcery, so PiF *itself* gains
  flashback for `{4}{R}` (mana cost). I see no usage in the four
  replays. Likely a missing capability but not visible as a bug.

## Patches I refused to write — why these are symptoms, not causes

- **"Just hardcode Reckless Impulse / Wrenn's Resolve / Glimpse to
  skip Bowmasters triggers."** The mechanism is `draw_cards()` →
  Bowmasters. Patching the three cards individually leaves March of
  Reckless Joy, Light Up the Stage, and every future impulse-draw
  spell broken. The fix is the routing change in Fix 1.

- **"Hardcode Galvanic Discharge target to be creature."** Card-name
  patch in `engine/card_effects.py` is the original sin. Fix is to
  *delete* the per-card handler (Fix 2).

- **"Increase Storm's max life or special-case Bowmasters damage."**
  Would mask Fix 1 and break correctness for any future card whose
  ETB fires from a draw. Refused.

- **"Add a `surveil_on_etb=True` field to land templates manually."**
  Card-name-equivalent patch; refused. Oracle predicate is the
  correct dispatch.

- **"Per-card handler for Consult the Star Charts."** Same anti-
  pattern. Fix is Fix 5 + the generic look-at-N-keep-K resolver.
