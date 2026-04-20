# Sideboard Solver — Proposal

**Status:** Draft, design-only. Awaiting approval before implementation.
**Author:** Claude Code session, 2026-04-19
**Scope:** Replace `engine/sideboard_manager.py` (243 lines of oracle-
free card-name string matching) with an oracle-driven, math-based
value solver.
**Motivates:** Living End vs Boros 10%, Azorius 15%, and similar
outliers — traced not to an AI scoring bug but to sideboard composition
and swap decisions that hand-curated heuristics got wrong.

---

## 1. Problem

`engine/sideboard_manager.py:sideboard()` decides which cards to board
IN/OUT between games in a Bo3 match. It does this with ~40 string-
match rules like:

```python
if any(w in opp_lower for w in ["storm", "living end"]):
    if any(w in card_lower for w in ["flusterstorm", "force of negation"]):
        board_in_priority.append((card_name, count, 8))
```

Problems:

1. **Card-name hardcoding.** `"flusterstorm"`, `"force of negation"`,
   `"wear"`, `"endurance"`, ~40 patterns. Any new SB card needs a new
   rule; any rename breaks silently.
2. **Archetype-name hardcoding.** `"goryo"`, `"living end"`, `"affinity"`
   as string probes against `opponent_deck.lower()`. Breaks on variants
   ("Azorius Control" vs "Azorius Control (WST)" needed separate tuning).
3. **Magic priorities.** Priorities 6–10 have no derivation. Priority 10
   vs 9 is a guess, not a gradient derived from the data.
4. **Meta-blind.** No notion of meta share — Chalice in the SB is worth
   more if the field has 30% artifact decks, less if it has 10%. Current
   logic treats each matchup identically.
5. **Deck-building blind.** Only decides *swaps* from an existing 15-card
   SB. Can't propose changes to the SB composition itself, which is where
   the biggest WR-leaks live (Living End's SB has 3 Foundation Breakers
   that do nothing vs 6 out of 15 meta decks).

The concrete cost, measured: Living End is 10% vs Boros. Pros put
Living End vs Burn at ~30–40%. The delta is the SB gap, not an AI scoring
bug.

## 2. Design goals

- **Oracle-driven.** Match patterns against `card.oracle_text`, not
  card names. New cards with matching oracle text work automatically.
- **Composition of existing primitives.** Every value formula must
  compose from `DeckKnowledge` densities, `clock.py` subsystems,
  `position_value`, or `creature_threat_value`. No new magic constants.
- **Per-matchup AND meta-wide.** The same formula answers "what should
  I board in for this match?" and "what should the 15-card SB contain
  for the weighted meta?" — one function, two use cases.
- **Symmetric reasoning.** `sb_value` computes the card's value. Swap
  decision = compare this card's value to the lowest-value card
  currently in main. No separate priority tier.
- **Auditable.** The output of `plan_sideboard()` is a table of
  `(card, value, action)` rows. A human can read it and verify the
  swap makes sense.

## 3. Mathematical formulation

Let $C$ be a card and $O$ be the opponent's deck (list of templates).
Define:

$$\text{sb\_value}(C, O) = \sum_{\text{clause}\in C.\text{oracle}} w(\text{clause}, O)$$

where $w$ is the expected value of each effect clause given opponent's
board-state distribution. Each clause maps to a pre-existing subsystem
(no new weights):

### 3.1 Removal clauses

| Oracle pattern | Formula | Primitive |
|---|---|---|
| `destroy target creature` | $\bar{t}_O \cdot \rho_{\text{creature}}(O) \cdot r$ | `creature_threat_value` averaged over opp's creatures, × opp's creature density, × residency |
| `destroy target artifact` | $\bar{p}_O \cdot \rho_{\text{artifact}}(O) \cdot r$ | `permanent_threat` avg across opp artifacts |
| `destroy all creatures` / board wipe | $\sum_i \text{cct}(c_i, O)$ × board-clearance-rate | `creature_clock_impact` |
| `target player loses N life` / burn | $N \cdot \text{life\_as\_resource}(O.\text{life})$ | `clock.life_as_resource` |

$\bar{t}_O$ = mean `creature_threat_value` over creatures in $O$'s deck.
$\rho_{\text{creature}}(O)$ = fraction of $O$'s library that is a creature
(already computed in `DeckKnowledge`).
$r$ = `PERMANENT_VALUE_WINDOW = 2.0` turns, the canonical residency
constant (`ai/ev_evaluator.py`).

### 3.2 Counter clauses

| Oracle pattern | Formula |
|---|---|
| `counter target spell` (no cost restriction) | $\rho_{\text{spell}}(O) \cdot \overline{\text{spell\_value}}(O)$ |
| `counter target noncreature spell` | $\rho_{\text{noncreature spell}}(O) \cdot \overline{\text{spell\_value}}(O)$ |
| `counter … unless … pays {N}` (Spell Pierce) | $\rho_{\text{cheap spell}}(O) \cdot \overline{\text{spell\_value}}$ discounted by opp's mean mana availability |

$\overline{\text{spell\_value}}$ is the mean `estimate_spell_ev` across
opp's spell templates, computed once at deck-knowledge-build time.

### 3.3 Protection / hexproof / lifegain

| Oracle pattern | Formula |
|---|---|
| `protection from red` | $\rho_{\text{red damage}}(O) \cdot \text{cct}(C)$ × residency — the damage this body absorbs |
| `gain N life` | $N \cdot (\text{opp.avg\_dpt} / O.\text{life})$ — scales with opp's damage-per-turn |
| `hexproof` on body $C$ | $\rho_{\text{targeted removal}}(O) \cdot \text{cct}(C)$ — value of not dying |

`opp.avg_dpt` = mean power of opp's creatures × meta density — already
derivable from deck composition.

### 3.4 Graveyard / combo hate

| Oracle pattern | Formula |
|---|---|
| `exile target player's graveyard` | $\text{combo\_reliance}(O) \cdot \text{combo\_impact}$ |
| `cards in graveyards can't be cast` (Leyline of the Void) | ditto, but persistent across turns |

$\text{combo\_reliance}(O)$ derives from opp's `FILL_RESOURCE`
goal's `resource_zone == "graveyard"` in their gameplan JSON. If opp's
gameplan doesn't read from graveyard, the hate is worth 0.

### 3.5 Chalice-style hate (already implemented)

The Chalice X solver (engine/game_state.py, commit b18d758) is the
first math-based hate-card primitive. It picks X to maximise
$\text{opp\_count}(X) − \text{my\_count}(X)$. Same structure applies
to any "counter spells with mana value X" effect.

### 3.6 Neutral — cards that don't scale with matchup

Cards that work equally vs every opponent (generic threats, cantrips,
ramp) get a **baseline** value = `estimate_spell_ev` against a "neutral
midrange opponent". This is the `v_generic(C)` column. The matchup-
specific value is computed by comparison.

**Swap decision:** card $C_{\text{sb}}$ in SB swaps in for card
$C_{\text{main}}$ in main iff

$$\text{sb\_value}(C_{\text{sb}}, O) - v_{\text{generic}}(C_{\text{sb}})
\;>\; \text{sb\_value}(C_{\text{main}}, O) - v_{\text{generic}}(C_{\text{main}})$$

i.e. swap only when the SB card's *matchup delta* exceeds the main card's
*matchup delta*. This preserves generically-strong cards (Counterspell,
Lightning Bolt) while swapping in specialists (Kor Firewalker vs Burn,
Relic of Progenitus vs GY decks).

## 4. Architecture

### 4.1 New module: `ai/sideboard_solver.py`

```python
def sb_value(card: CardInstance, opp_templates: List[CardTemplate],
             opp_gameplan: Optional[DeckGameplan] = None) -> float:
    """Expected value of `card` against an opponent running
    `opp_templates`. Pure function; no game-state dependency.

    Composition:
      value = Σ clause_value(clause, opp) over oracle clauses
    Every clause_value hands off to an existing subsystem (clock,
    creature_threat_value, permanent_threat, deck_knowledge,
    life_as_resource).
    """
    ...

def plan_sideboard(my_main: Dict[str, int], my_sb: Dict[str, int],
                    opp_deck_name: str,
                    card_db: CardDatabase
                    ) -> Tuple[Dict[str, int], Dict[str, int], List[str]]:
    """Compute the per-matchup swap plan.
    Returns (new_main, new_sb, rationale_log).
    """
    ...

def optimal_sideboard(card_pool: Dict[str, int], meta: Dict[str, float],
                       card_db: CardDatabase
                       ) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Given a card pool and the weighted meta, solve for the 60+15
    split that maximises Σ meta_share × expected_wr(matchup).
    Used for deck-building, not per-game swaps.
    """
    ...
```

### 4.2 Integration points

- `engine/sideboard_manager.py:sideboard()` — delegate to
  `plan_sideboard` when opp deck knowledge is available. Fall back to
  current logic with a deprecation log line.
- Post-test: deprecate the fallback, delete the 243-line module.
- `run_meta.py:run_bo3()` — already calls `sideboard()`; no change.
- New CLI: `python run_meta.py --plan-sb "Living End" "Boros Energy"`
  prints the proposed swap table for audit.

### 4.3 Data dependencies

- `DeckKnowledge` (existing) — densities by card class
- `decks/gameplans/*.json` (existing) — opp's `resource_zone` for
  GY-hate evaluation
- `decks/modern_meta.py:METAGAME_SHARES` (existing) — for
  `optimal_sideboard`
- `CardDatabase` (existing) — template lookup

No new data files. No new constants except declared rules constants
already in use (`PERMANENT_VALUE_WINDOW = 2.0`).

## 5. Phased rollout

**Phase 1 — Core solver (~2h).** Implement `sb_value` and
`plan_sideboard`. Unit tests on canonical matchups:

- `sb_value(Kor Firewalker, Boros)` > `sb_value(Kor Firewalker, Amulet Titan)`
- `sb_value(Relic of Progenitus, Living End)` > 0
- `sb_value(Relic of Progenitus, Zoo)` ≈ 0
- `sb_value(Force of Negation, Storm)` > `sb_value(Force of Negation, Boros)`

**Phase 2 — A/B test (~30m).** Run 16×16 N=20 twice:
(a) current `sideboard_manager.sideboard()`
(b) new `plan_sideboard()`
Measure aggregate WR delta. Acceptance: ≥5pp improvement on outlier
matchups (Living End vs Boros, Azorius vs aggro) without regression
elsewhere.

**Phase 3 — Switchover (~30m).** Replace `sideboard_manager` delegate
with hard call to `plan_sideboard`. Archive old module under
`docs/history/`. Regenerate dashboards.

**Phase 4 — Deck-building solver (~1h, optional).** Implement
`optimal_sideboard` and audit existing SBs — find decks where ≥3 SB
slots are dominated by an alternative card in the deck pool. Report
to user; do NOT auto-apply decklist changes (those are user-approved).

## 6. Validation

- 178/178 existing tests stay green.
- New `tests/invariants/test_sb_value.py`:
  - **Oracle parity:** two SB cards with identical oracle text get
    identical `sb_value` against the same opponent.
  - **Meta-share scaling:** `optimal_sideboard` puts more anti-artifact
    hate in the SB when Affinity's meta share is 30% vs 10%.
  - **GY hate vs non-GY deck:** Leyline of the Void is not swapped in
    vs Boros.
  - **Swap symmetry:** a card that's not in the SB never enters main.
- 16×16 A/B: 5pp improvement on outliers, ±3pp on stable matchups.

## 7. Out of scope

- Changing mainboard decklists based on `optimal_sideboard` output.
  That's a user decision, and mainboards are tied to competitive
  tournament data (source of truth in `decks/modern_meta.py`).
- Opponent's sideboard awareness — we'd need BHI for their SB,
  which is a larger project.
- Re-architecting `StrategyProfile` weights. Those are orthogonal.
- Per-game-state dynamic sideboarding (swapping mid-match). Current
  rules don't allow it; out of scope forever.

## 8. Risks

- **Formula blind spots.** A card class we don't have a clause for
  defaults to `v_generic`, i.e. unchanged from baseline. Risk: regressions
  on matchups where the old heuristic hit a specialist card our
  formula misses. Mitigation: phase-2 A/B measures this; Phase 3 only
  ships if aggregate delta ≥ 0.
- **Performance.** `sb_value` evaluated on 60+15 = 75 cards × 16 opp
  decks = 1200 evaluations per matchup baseline. Each is ~1–10ms;
  total ~1–10s per matrix build. Acceptable.
- **Parser fragility.** Oracle-text regex matching can misfire (e.g.
  "for each artifact" matched reminder text in a known bug). Mitigate
  by reusing the tightened regexes already in `cards.py`
  `_dynamic_base_power`.

## 9. Supersession

This proposal, if implemented, supersedes:

- The card-name-matching approach in `engine/sideboard_manager.py`.
- Any future hand-curated SB slot recommendations (e.g. "add 3 Kor
  Firewalker to Living End"). Those become `optimal_sideboard` output.

## 10. Open questions

1. **Granularity of `opp_gameplan` input.** Should `sb_value` read opp's
   JSON gameplan for goal-specific signals, or only deck composition?
   JSON gives richer signal (Living End's resource_zone="graveyard" says
   GY hate is strong); composition alone misses this. Recommendation:
   pass both, gameplan optional.
2. **SB size invariance.** Current logic caps swaps at 5 (7 for artifact
   matchups). Should the solver respect a cap, or swap as many as the
   delta justifies? Recommendation: no explicit cap; swap until marginal
   delta is negative. Natural self-limiting.
3. **Integration with mulligan.** A better SB changes mulligan priority
   ("keep if hand has anti-Burn tech"). Phase 5 future work — out of
   scope for this proposal.

---

## Appendix A — Phase 2 A/B results (2026-04-19)

Both backends run at 16×16 N=20, same codebase (post-critical-pieces
protection + GY-hate regex tightening).

**Per-deck field WR deltas:**

| Deck | Old | New | Δpp |
|---|---|---|---|
| Azorius Control | 9.7% | 16.7% | **+7.0** |
| Eldrazi Tron | 67.7% | 73.0% | **+5.3** |
| Amulet Titan | 42.3% | 46.7% | +4.3 |
| Dimir Midrange | 61.0% | 65.0% | +4.0 |
| Izzet Prowess | 46.3% | 48.0% | +1.7 |
| Jeskai Blink | 60.3% | 61.7% | +1.3 |
| 4/5c Control | 33.0% | 32.7% | −0.3 |
| Domain Zoo | 68.0% | 67.0% | −1.0 |
| Pinnacle Affinity | 62.3% | 61.0% | −1.3 |
| Living End | 24.7% | 22.3% | −2.3 |
| 4c Omnath | 51.0% | 48.7% | −2.3 |
| Azorius Control (WST) | 35.7% | 33.0% | −2.7 |
| Affinity | 89.7% | 86.7% | −3.0 |
| Ruby Storm | 42.0% | 39.0% | −3.0 |
| Boros Energy | 71.3% | 68.0% | −3.3 |
| Goryo's Vengeance | 35.0% | 30.7% | **−4.3** |

**Aggregate:** +0.1pp (neutral).

**Matchup volatility:** ±25–35pp swings on specific pairings
(Amulet-vs-Prowess ±35, Affinity-vs-PinnacleAff ±30). The solver is
making large SB changes that don't always land — high variance signals
underfitting in the value formulas.

**Acceptance criteria check:**
- "≥5pp improvement on outlier matchups" — Azorius **+7pp ✓**, Tron
  **+5pp ✓** (marginal).
- "without regression elsewhere" — Goryo's **−4pp ✗** (over the ±3
  tolerance).
- Aggregate ≥ 0 — **+0.1pp ✓** (but only barely).

**Decision: DO NOT ship.** Solver stays opt-in under `SB_SOLVER=new`
while the formulas iterate. Phase 3 gated on a future run that meets
all three criteria.

**Suggested Phase 2.5 refinements (before re-testing):**

1. **Goryo's regression diagnosis.** Goryo's −4pp suggests the solver
   swaps out something core. Check whether `critical_pieces` covers
   every reanimator chain card (Faithful Mending, Unmarked Grave,
   Archon of Cruelty, Persist, Unburial Rites, Goryo's Vengeance).
2. **Boros / Storm / Affinity ~−3pp.** All three are fast decks where
   swapping ANY mainboard card for a specialist hate piece slows the
   deck. The solver needs a "tempo cost" term — swapping a 2-CMC
   creature for a 3-CMC hate piece costs half a tempo turn, roughly
   `(new_cmc - old_cmc) × mana_clock_impact × 20 × residency`. Subtract
   from `sb_value`.
3. **High-variance pairings.** Amulet-vs-Prowess at ±35pp suggests the
   solver is making a single categorical swap (Blood Moon in, removal
   out?) that dominates. Cap per-matchup swap count at 4 initially, or
   require swap deltas to compound rather than single large moves.
4. **Threshold gate.** Only commit a swap when
   `sb_value(C_sb, O) − sb_value(C_main, O) > ε`, where ε is mana-unit
   × 0.5 (half a mana-turn). Prevents marginal swaps.

## Appendix B — Phase 2.5 results (tempo-cost + ε gate)

Added to `plan_sideboard`:
- `tempo_cost = (sb_cmc − main_cmc) × mana_unit × PERMANENT_VALUE_WINDOW`
- `net_gain = sb_val − tempo_cost − main_val`
- swap committed only when `net_gain > ε = mana_unit × 0.5`

**Per-deck field WR deltas (vs Phase 2 legacy baseline):**

| Deck | Old | New | Δpp | Phase 2 Δpp |
|---|---|---|---|---|
| Azorius Control | 9.7% | 15.3% | **+5.7** | +7.0 |
| Eldrazi Tron | 67.7% | 72.0% | +4.3 | +5.3 |
| Living End | 24.7% | 28.0% | +3.3 | −2.3 |
| 4c Omnath | 51.0% | 53.3% | +2.3 | −2.3 |
| 4/5c Control | 33.0% | 35.0% | +2.0 | −0.3 |
| Ruby Storm | 42.0% | 43.7% | **+1.7** | **−3.0** |
| Boros Energy | 71.3% | 71.3% | **0.0** | **−3.3** |
| Amulet Titan | 42.3% | 42.3% | 0.0 | +4.3 |
| Domain Zoo | 68.0% | 67.7% | −0.3 | −1.0 |
| Affinity | 89.7% | 89.0% | −0.7 | −3.0 |
| Izzet Prowess | 46.3% | 45.3% | −1.0 | +1.7 |
| Pinnacle Affinity | 62.3% | 60.7% | −1.7 | −1.3 |
| Goryo's Vengeance | 35.0% | 32.0% | −3.0 | −4.3 |
| Dimir Midrange | 61.0% | 58.0% | **−3.0** | **+4.0** |
| Azorius (WST) | 35.7% | 31.3% | −4.3 | −2.7 |
| Jeskai Blink | 60.3% | 55.0% | **−5.3** | +1.3 |

**Aggregate:** 0.0pp (still neutral).

**Hypothesis confirmed — tempo-cost helps fast decks:**
- Boros, Storm, Affinity: all moved from ~−3pp toward 0pp. The
  tempo-cost term correctly protected their cheap cards from being
  swapped out for higher-CMC hate pieces.

**New regression pattern — mid-range/control decks:**
- Jeskai Blink: +1.3pp → −5.3pp (−6.6pp relative shift)
- Dimir: +4.0pp → −3.0pp (−7.0pp relative)
- WST: −2.7pp → −4.3pp (−1.6pp relative)

Tempo-cost is over-applied for decks that actually *want* to swap
cheap-into-expensive in certain matchups (control decks welcome
high-CMC finishers like Sheoldred). The cost penalty is uniform
across archetypes when it should scale with deck speed.

**Still-high matchup volatility:** ±30pp swings on
Jeskai-vs-PinnacleAff, Amulet-vs-LivingEnd, Goryo's-vs-4/5c, etc.
Epsilon gate (half a mana-unit) wasn't large enough to suppress
these. Tried 1.0, 2.0 mana-unit — aggressive gates reduced the
winner-deltas too, net effect negative.

**Phase 3 switchover: still NOT ready.** Need:
1. **Archetype-scaled tempo cost:** `tempo_cost × speed_factor` where
   `speed_factor` derives from the deck's own avg CMC or
   `strategy_profile.curve_out` weight.
2. **Value floor for deck-coherent swaps:** reject swaps that leave
   the deck below some minimum count of interaction / threats / etc.
   Deck-role taxonomy derivable from gameplan JSON.
3. **Bigger A/B sample:** at N=20, ±30pp matchup swings could be
   10% of the variance. N=50 or N=100 matrix would tighten the
   confidence bounds and reveal whether the mid-range regressions are
   statistically significant.

Solver stays opt-in via `SB_SOLVER=new`. Phase 3 gated on one of the
three refinements above.

## Appendix C — Phase 2.6 (archetype-scaled tempo) results

Replaced Phase 2.5's uniform `(sb_cmc − main_cmc) × ...` with an
archetype-scaled cost anchored to the deck's own avg CMC:

    cmc_floor = max(main_cmc, my_avg_cmc)
    tempo_cost = max(0, sb_cmc − cmc_floor) × mana_unit × residency

For Boros (avg CMC ≈1.8), a 3-CMC SB swap costs 1.2 mana-units ×
residency. For Azorius (avg CMC ≈3.0), the same swap costs 0 — the
curve already hosts 3-CMC cards.

**Per-deck deltas vs legacy (Phase 2.6 absolute):**

| Deck | Legacy | P2.6 | Δpp | P2.5 Δpp | P2 Δpp |
|---|---|---|---|---|---|
| Eldrazi Tron | 67.7% | 73.7% | **+6.0** | +4.3 | +5.3 |
| Ruby Storm | 42.0% | 46.3% | +4.3 | +1.7 | −3.0 |
| Azorius Control | 9.7% | 13.0% | +3.3 | +5.7 | +7.0 |
| Amulet Titan | 42.3% | 45.7% | +3.3 | 0.0 | +4.3 |
| Izzet Prowess | 46.3% | 48.7% | +2.3 | −1.0 | +1.7 |
| Dimir Midrange | 61.0% | 63.0% | **+2.0** | **−3.0** | +4.0 |
| Pinnacle Affinity | 62.3% | 63.7% | +1.3 | −1.7 | −1.3 |
| Boros Energy | 71.3% | 71.3% | 0.0 | 0.0 | −3.3 |
| Living End | 24.7% | 24.7% | 0.0 | +3.3 | −2.3 |
| Domain Zoo | 68.0% | 67.3% | −0.7 | −0.3 | −1.0 |
| 4/5c Control | 33.0% | 32.3% | −0.7 | +2.0 | −0.3 |
| Jeskai Blink | 60.3% | 58.0% | **−2.3** | **−5.3** | +1.3 |
| Azorius (WST) | 35.7% | 32.7% | −3.0 | −4.3 | −2.7 |
| Affinity | 89.7% | 85.3% | −4.3 | −0.7 | −3.0 |
| 4c Omnath | 51.0% | 46.3% | −4.7 | +2.3 | −2.3 |
| Goryo's Vengeance | 35.0% | 28.0% | **−7.0** | −3.0 | −4.3 |

**Aggregate: −0.2pp** (marginal loss vs legacy).

**Progression summary:**
- Phase 2 (no tempo): **+0.1pp** aggregate
- Phase 2.5 (uniform tempo + ε): **0.0pp**
- Phase 2.6 (archetype-scaled tempo + ε): **−0.2pp**

Adding tempo cost at either granularity didn't yield a net
improvement. Each refinement trades one deck's regression for
another's. Positive signal remains on specific outlier decks:
Azorius Control reliably gains +3 to +7pp across all variants, and
Eldrazi Tron +5 to +6pp.

**Working hypotheses (unvalidated):**
1. **Too few clause types.** Only 5 clauses cover ~20% of SB-relevant
   oracle patterns. Missing: cost reducers, ramp, life-gain wide,
   creature-type-specific effects, symmetric hate (Blood Moon),
   planeswalkers, aura removal.
2. **Volatility from N=20.** ±25–35pp matchup swings persist through
   all phases; an N=50 or N=100 baseline would show whether these
   are statistical noise or real structural effects.
3. **Deck coherence missing.** The solver doesn't know that Dimir's
   deck wants ≥4 counterspells and ≥6 removal cards; it may swap
   them out if the marginal value favours hate pieces.

**Decision: session-end hold.** Solver remains opt-in via
`SB_SOLVER=new`. Further refinement requires either a richer clause
catalogue (fundamentally more work) or a different valuation
framework (e.g. simulate-delta A/B on a per-card basis, which is
expensive but more accurate).

The proposal's value is not negated — the architecture is sound and
the Azorius/Tron gains are repeatable. It's just not yet complete
enough to displace the legacy heuristics across the full meta.
