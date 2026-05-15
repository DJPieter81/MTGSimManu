---
title: Finisher simulator v3 — API spec (library composition + tutor access + multi-turn rollout)
status: active
priority: primary
session: 2026-05-10
depends_on:
  - docs/PHASE_D_DEFERRED.md
  - ai/finisher_simulator.py
  - ai/combo_calc.py
  - ai/combo_evaluator.py
  - ai/bhi.py
tags:
  - phase-d
  - simulator-v3
  - combo
  - design
summary: >
  Specifies the API surface for finisher_simulator v3, the
  successor to the v2 projection in `ai/finisher_simulator.py`.
  v2 collapses chain-fuel scoring to zero whenever no closer is
  in hand, because `expected_damage = 0` masks the *intermediate*
  value of casting fuel that progresses the chain toward a future
  closer (drawn this turn, fetched next turn via Wish, or flashed
  back from the graveyard). v3 closes three gaps: (1) library
  composition modelling — `P(closer | N more draws)` from a
  tag-indexed histogram of the library; (2) tutor-as-finisher-
  access semantics — Wish in hand counts as the closer at +N mana
  cost; (3) multi-turn rollout — simulate 1, 2, 3 turns out and
  pick the turn maximising `damage × survival`. Survival folds in
  `bhi.py`'s counter / removal density signals and the
  opp-pressure tick from `clock.py`. Design-only; an optional
  unwired stub at `ai/finisher_simulator_v3.py` makes the API
  reviewable. Acceptance gate: Storm field N=50 ≥ 44%, and
  Goryo's / Living End / Amulet must not regress > 5pp.
---

# Finisher simulator v3 — API spec

## 1. Background

### 1.1 What v2 gives us

v2 (`ai/finisher_simulator.py`, shipped 04754d2) returns a
`FinisherProjection` with:

* `pattern` ∈ {storm, cascade, reanimation, cycling, none}
* `expected_damage` (this turn)
* `success_probability`
* `mana_floor`, `chain_length`, `closer_name`
* `hold_value` — projected next-turn damage × P(survive opp's
  intervening turn), flat scalar
* `next_turn_damage` — chain damage if cast next turn given +1
  land
* `coverage_ratio` — `expected_damage / opp_life`, clamped
* `closer_in_zone` — flags for hand/sb/library/graveyard

### 1.2 The v2 gap that PR3c hit five times

`docs/PHASE_D_DEFERRED.md` "Path forward" makes the scope
concrete:

> PR3d — simulator v3 (next session) must model **intermediate
> value of casting fuel BEFORE the closer is reached.** The
> current `expected_damage = 0` when no closer is in hand
> collapses every chain-fuel decision to "fire_value = 0", and
> Storm's intent is "build chain THIS turn, find closer NEXT
> turn via Wish/tutor".

In the seed 60600 G3 T4 trace
(`replays/affinity_vs_storm_60600.ndjson`, decisions `g3t4d76`
and `g3t4d78`):

* At storm = 6, opp life = 20, Affinity ahead, Storm holds
  Grapeshot in hand. EV table from the NDJSON `alternatives`
  field at d76:

  | Action            | EV     | Source                                     |
  |-------------------|--------|--------------------------------------------|
  | pass (chosen)     |  0.00  | tiebreaker default                         |
  | Grapeshot         | -5.63  | combo modifier — fuel-in-hand hold         |
  | Manamorphose      | -10.00 | base projection — **no chain credit**      |
  | Desperate Ritual  | -10.07 | base projection — **no chain credit**      |
  | Reckless Impulse  | -10.31 | base projection — **no chain credit**      |

* At d78 (storm = 7, post-Manamorphose), the same shape
  appears: pass at 0.00 dominates Past in Flames at -10.28
  even though PiF + flashback chain is the path that actually
  closes.

The structural blindness is that `expected_damage = 0` masks
the truth that *casting Past in Flames advances the chain
toward a closer that lives in the graveyard or in the
sideboard via Wish.* v2's marginal-delta signal (after − before
at the same single-turn horizon) sees zero everywhere except
on the closer cast itself.

### 1.3 The 39pp panic the deferred path stems from

PR3c's first wire-up regressed Ruby Storm field N=20 from
44.8% → 7.5%, and then 5.3% after the wasted-cast patch
(`docs/PHASE_D_DEFERRED.md` "What broke"). Three iterations of
the v2 hold gate all collapsed Storm:

* `hold_value > fire_value` → Storm holds forever (next turn
  always projects more mana, recursion non-terminating).
* Lethal-gate (only fire when this turn is lethal) → Storm at
  1.2% (almost never reaches lethal-this-turn projection
  because closer is in SB via Wish, not in hand).
* `hold_lethal AND not fire_lethal` → Storm at 0% (never
  satisfies hold_lethal because `next_turn_damage = 0` when no
  closer is in hand).

Every collapse traces back to the same root: v2 has no
representation of "the closer is *findable* — drawn, tutored,
flashed back — within the next 1-3 turns".

### 1.4 What v3 must add

Three capabilities, in dependency order:

1. **Library composition modelling** — `P(closer | N more
   draws, library_state)` derived from a tag/oracle histogram
   of the library. Tag-driven, not card-name-keyed (per the
   abstraction contract).
2. **Tutor-as-finisher-access semantics** — when Wish (or any
   `'tutor'`-tagged card with payoff access) is in hand,
   `closer_in_zone` should treat the SB/library closer as
   reachable, with the chain's mana cost incremented by the
   tutor's CMC and `success_probability` decremented by the
   tutor's resolution risk (BHI counter density).
2.  Note: v2's `_project_storm_with_tutor_access` is a
    test-bench-only sketch of this; v3 makes it the live
    primary path.
3. **Multi-turn rollout** — recursive projection over T+0,
   T+1, T+2, T+3 with `damage × survival` as the objective and
   `bhi.py` + `clock.py` as the survival inputs. Pick the
   turn-offset with the highest score; the AI's hold-vs-fire
   gate becomes "fire on the optimal turn-offset", not "fire
   when fire_lethal beats hold_lethal".

## 2. API surface — `FinisherProjectionV3`

`FinisherProjectionV3` extends v2 with five new fields. v2's
fields are preserved verbatim so existing callers
(`combo_evaluator.card_combo_evaluation`) remain wire-compatible
during migration.

```python
# ai/schemas.py — extension (NOT editing the v2 model in place
# until PR3c lands; v3 ships its own model).

class LibraryComposition(BaseModel):
    """Tag-indexed histogram of the library, plus total size.

    Tags are the same string keys produced by
    `engine/card_database.py`'s tag pass — `'ritual'`,
    `'cantrip'`, `'tutor'`, `'cost_reducer'`, `'flashback'`,
    `'reanimate'`, `'discard'`, `'cycling'`, `'cascade'`. The
    closer category is keyed by *keyword* and *oracle predicate*,
    not card name:

      * `'storm_closer'` — STORM keyword
      * `'token_finisher'` — oracle "create … tokens … for each"
      * `'reanim_target'` — creature with power >= the deck's
        gameplan-declared `reanim_target_power_floor`
      * `'cycling_payoff'` — oracle "all creature cards …
        graveyards … to the battlefield"
      * `'cascade_payoff'` — `'combo'` tag AND not creature
        AND CMC >= cheapest cascade enabler in the deck

    No card-name keys ever live in this histogram. The
    composition is built once per player at game start and
    decremented as cards leave the library.
    """
    total: int = Field(default=0, ge=0)
    by_tag: dict[str, int] = Field(default_factory=dict)
    closer_count: int = Field(default=0, ge=0)
    closer_categories: tuple[str, ...] = Field(default=())
    model_config = ConfigDict(frozen=True)


class TurnOffsetProjection(BaseModel):
    """One node in the multi-turn rollout chain.

    A `FinisherProjectionV3` carries a list of these for offsets
    0..max_depth.  Each node is a complete-by-itself projection
    of "what does the chain look like if we attempt to close on
    THIS turn-offset?", derived from the snapshot delta applied
    over `offset` simulated turns.
    """
    offset: int = Field(ge=0)            # 0 = this turn
    expected_damage: float = Field(ge=0.0)
    closer_reachable_p: float = Field(ge=0.0, le=1.0)  # P(closer in hand by then)
    survival_p: float = Field(ge=0.0, le=1.0)          # P(we're alive by then)
    score: float = Field(ge=0.0)         # damage * survival * closer_reachable
    mana_at_offset: int = Field(ge=0)
    storm_at_offset: int = Field(ge=0)   # CR 500.4 — resets to 0 each turn
    notes: str = ""                      # human-readable explanation
    model_config = ConfigDict(frozen=True)


class FinisherProjectionV3(BaseModel):
    """Projected EV-impact of attempting / building a finisher
    chain over a multi-turn horizon.

    Successor to v2 `FinisherProjection`. Wire-compatible: the v2
    fields below carry the same semantics, so a `combo_evaluator`
    written against v2 reads v3 unchanged. The v3 fields are
    additive.
    """

    # ── v1/v2 fields (verbatim) ──
    pattern: FinisherPattern = "none"
    expected_damage: float = Field(default=0.0, ge=0.0)
    success_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    mana_floor: int = Field(default=0, ge=0)
    chain_length: int = Field(default=0, ge=0)
    closer_name: Optional[str] = None
    hold_value: float = Field(default=0.0, ge=0.0)
    next_turn_damage: float = Field(default=0.0, ge=0.0)
    coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    closer_in_zone: dict[str, bool] = Field(
        default_factory=lambda: {
            'hand': False, 'sb': False,
            'library': False, 'graveyard': False,
        }
    )

    # ── v3 fields ──

    library_composition: LibraryComposition = Field(
        default_factory=LibraryComposition,
    )
    """Snapshot of the player's library at the moment of
    projection. Drives `closer_reachable_p` for each turn
    offset."""

    turn_projections: tuple[TurnOffsetProjection, ...] = Field(
        default=(),
    )
    """Multi-turn rollout — element [i] is the projection for
    turn offset i. Length is bounded by
    `CHAIN_MULTI_TURN_DEPTH` (=3, see scoring_constants.py)."""

    best_turn_offset: int = Field(default=0, ge=0)
    """Turn offset at which `score` is maximised. Caller's
    'fire on this turn' instruction; offset 0 = fire now."""

    tutor_access_chains: tuple[str, ...] = Field(default=())
    """Tutor-card names in hand whose payoff access is reachable
    in SB/library. Generic by tag — no card-name semantics —
    but the resolved tutor name is recorded for trace
    visibility, not used for scoring decisions."""

    p_closer_by_turn: tuple[float, ...] = Field(default=())
    """Cumulative probability the closer is in hand by turn
    offset i (i ∈ 0..max_depth). p_closer_by_turn[0] is the
    "closer in hand right now" indicator (1.0 or 0.0);
    subsequent entries fold in the `P(draw | N more draws)`
    arithmetic."""

    model_config = ConfigDict(frozen=True)
```

### 2.1 How the v3 fields compose into `card_combo_evaluation`

The current evaluator (`ai/combo_evaluator.py`, lines 281-419) makes
two calls into the projection:

1. `baseline_proj.expected_damage * baseline_proj.success_probability` →
   `fire_value`. v3 replaces this with
   `baseline_proj.turn_projections[best_turn_offset].score`.

2. `baseline_proj.hold_value` → compared against `fire_value` for
   the hold gate. v3 replaces this with: hold ⇔ `best_turn_offset
   > 0 AND turn_projections[0].score < turn_projections[best].score`.

The chain-progress credit branch (lines 389-393) — the patch that
gives positive EV to fuel cards when `expected_damage = 0` but the
chain is reachable — becomes redundant in v3 because intermediate
turns have non-zero `score` whenever closer reachability is non-zero
on a future turn. The branch can be removed in PR3c if the v3
arithmetic covers it (open question 4 below).

## 3. Library composition module

### 3.1 Function signature

```python
# ai/library_composition.py — NEW module

def build_library_composition(
    library: list["CardInstance"],
    *,
    deck_gameplan: Optional[dict] = None,
) -> LibraryComposition:
    """Bucket `library` by tag/oracle predicate.

    No card names enter or leave this function. Closer
    categories (`storm_closer`, `token_finisher`,
    `reanim_target`, `cycling_payoff`, `cascade_payoff`)
    are detected via the same predicates that
    `ai/finisher_simulator.py` already uses (`_has_storm_keyword`,
    `_has_token_finisher_oracle`, `_is_cycling_payoff`,
    `_is_cascade_payoff`, plus a new `_is_reanim_target` that
    reads `reanim_target_power_floor` from the gameplan JSON).

    `deck_gameplan` is an optional pass-through of
    `decks/gameplans/{deck}.json` so per-archetype thresholds
    (e.g. reanimator's minimum target power) are looked up by
    tag, not by deck name. When None, conservative defaults are
    used.
    """
    ...

def p_draw_closer(
    composition: LibraryComposition,
    n_draws: int,
    *,
    closer_categories: Optional[set[str]] = None,
) -> float:
    """P(at least one closer drawn in N more draws).

    Hypergeometric without replacement:
        P = 1 - C(non_closer, n_draws) / C(total, n_draws)

    `closer_categories` defaults to all categories in
    `composition.closer_categories`. Caller can narrow to a
    subset (e.g. only `'storm_closer'` when the player can
    only cast a storm-keyword closer this turn).

    All values derived from `composition` — no card-name
    inspection.
    """
    ...
```

### 3.2 `library_state` shape

Concretely, after calling `build_library_composition` once at game
start (and tracking decrements as cards leave the library), the
state looks like:

```python
LibraryComposition(
    total=43,
    by_tag={
        'ritual': 6,
        'cantrip': 8,
        'tutor': 3,
        'cost_reducer': 2,
        'flashback': 1,        # Past in Flames
        'card_advantage': 4,
        'storm_closer': 2,     # 2× Grapeshot
        'token_finisher': 0,
        'reanim_target': 0,
        'cycling_payoff': 0,
        'cascade_payoff': 0,
    },
    closer_count=2,
    closer_categories=('storm_closer',),
)
```

The tag set is **exactly** the set already produced by
`engine/card_database.py`'s tag pass plus the closer categories
synthesised here. **No card-name keys.**

### 3.3 Abstraction-contract compliance

Per `CLAUDE.md` "Hard prohibitions":

* ✗ `if name in {'Grapeshot', 'Empty the Warrens'}: ...` —
  forbidden, would be detected by
  `tools/check_abstraction.py`.
* ✓ `if Kw.STORM in keywords or _has_token_finisher_oracle(t): ...`
  — passes the contract; same predicate the v2 simulator already
  uses.

The closer-category set extends only when a NEW oracle predicate
is needed, not per card. The reanimator-target floor reads from
the gameplan JSON so per-archetype thresholds are configuration,
not source.

## 4. Tutor-as-finisher-access semantics

### 4.1 The rule (mechanic-phrased)

> A tutor card is finisher access if and only if the SB ∪
> library contains a card matching one of the closer-category
> predicates. The tutor's CMC is added to the chain's mana
> cost, and the tutor's resolution risk (P_counter from BHI) is
> multiplied into the chain's success_probability.

This is a **mechanic** — not a Storm-deck rule. Burning Wish,
Living Wish, Demonic Tutor, Glittering Wish, Eladamri's Call,
Sevinne's Reclamation (graveyard tutor + reanimation),
Summoner's Pact (creature tutor + reanim payoff) — any
`'tutor'`-tagged card with a real target shares this code path.

### 4.2 Pseudocode

```python
def _tutor_access_contribution(
    hand: list[CardInstance],
    sideboard: list[CardInstance],
    library_composition: LibraryComposition,
    snap: EVSnapshot,
    bhi_state: BayesianHandTracker,
) -> tuple[Optional[CardInstance], int, float]:
    """Returns (best_tutor, extra_cost, p_resolves) for the
    best tutor-as-finisher access path.

    best_tutor      — CardInstance whose access is most
                      cost-efficient (lowest CMC among tutors
                      with a real SB/library target).
    extra_cost      — tutor.cmc, in mana, added to the chain's
                      mana floor when tutor access is used.
    p_resolves      — P(tutor resolves) = (1 - p_counter) where
                      p_counter is bhi.get_counter_probability().
                      Bounded below by a rules-derived floor
                      (CHAIN_TUTOR_MIN_RESOLVE) so a fully-
                      counter-leaden opponent doesn't zero out
                      the path.

    Returns (None, 0, 0.0) when no tutor-with-access exists.
    """
    tutors_with_access = []
    for card in hand:
        if 'tutor' not in card.template.tags:
            continue
        # Reuse the v2 predicate verbatim — generic by oracle.
        if _tutor_has_payoff(card, sideboard) or \
           library_has_payoff(card, library_composition):
            tutors_with_access.append(card)
    if not tutors_with_access:
        return (None, 0, 0.0)
    best_tutor = min(tutors_with_access, key=lambda c: c.template.cmc or 0)
    p_counter = bhi_state.get_counter_probability()
    p_resolves = max(CHAIN_TUTOR_MIN_RESOLVE, 1.0 - p_counter)
    return (best_tutor, best_tutor.template.cmc or 0, p_resolves)
```

### 4.3 Amortising the +N mana cost across turns

When the tutor is in hand on T+0 but the opponent's pressure
forces the chain to T+2, the tutor cost is paid at the turn the
chain fires — not earlier. The multi-turn rollout (Section 5)
handles this:

* T+0 turn-projection: chain mana cost = `chain_floor + tutor_cmc`
  (tutor cast + closer cast + fuel).
* T+1 turn-projection: same arithmetic, with `mana_at_offset =
  snap.my_mana + 1`. If the tutor cost still exceeds available
  mana, `closer_reachable_p` for that offset is dampened by
  `p_draw_land`.
* T+N turn-projection: same recursive shape.

The tutor's CMC is **never** absorbed into a "the tutor was free"
assumption — every projection accounts for its mana.

### 4.4 The case the v2 sketch already handles

`ai/finisher_simulator.py:456-572` (`_project_storm_with_tutor_access`)
is the test-bench v2 stub for this. v3 promotes that function to
the primary code path, calls it from inside `simulate_finisher_chain`
on every turn offset, and integrates `bhi_state` for the
resolution probability (today the function returns
`CHAIN_EXTRA_RULES_STEP_SUCCESS`, a flat sentinel).

## 5. Multi-turn rollout

### 5.1 Algorithm

```python
def _project_multi_turn(
    snap: EVSnapshot,
    hand: list[CardInstance],
    battlefield: list[CardInstance],
    graveyard: list[CardInstance],
    sideboard: list[CardInstance],
    library_composition: LibraryComposition,
    storm_count: int,
    archetype: str,
    bhi_state: BayesianHandTracker,
    max_depth: int = CHAIN_MULTI_TURN_DEPTH,  # =3
) -> tuple[TurnOffsetProjection, ...]:
    """Build the (offset 0, offset 1, ..., offset max_depth)
    chain of TurnOffsetProjection nodes.

    Each offset applies a snapshot delta:
      * +1 land drop  (my_mana += 1, my_total_lands += 1)
      * -opp_pressure life  (clock.py opp_power tick)
      * storm_count -> 0  (CR 500.4)
      * closer_reachable_p folds in
        p_draw_closer(library_composition, n=offset)

    Survival probability folds in:
      * P(survive opp's next turn) from clock.py
      * P(no critical removal) from bhi_state.get_removal_probability
      * P(no counterspell on the closer) — used only when
        closer is targetable (not in graveyard reanim case)

    The score for each offset is:
        damage * survival * closer_reachable
    """
    projections = []
    for offset in range(max_depth + 1):
        # 1. Snapshot delta — pure-function copy.
        future_snap = snap.replace(
            my_mana=snap.my_mana + offset,
            my_total_lands=snap.my_total_lands + offset,
            my_life=max(0, snap.my_life - offset * snap.opp_power),
            turn_number=snap.turn_number + offset,
        )

        # 2. P(closer reachable) — closer in hand now, OR
        #    drawn in the next `offset` turns, OR fetched via
        #    in-hand tutor.
        p_closer_now = 1.0 if any_closer_in_hand(hand) else 0.0
        p_drawn_by_offset = p_draw_closer(library_composition, offset)
        tutor, tutor_cost, tutor_p = _tutor_access_contribution(
            hand, sideboard, library_composition, future_snap, bhi_state,
        )
        # Independent events — closer in hand OR drawn OR tutored
        # (inclusion-exclusion at face value; conservative because
        # tutor + draw correlation is positive).
        p_no_closer = (
            (1.0 - p_closer_now)
            * (1.0 - p_drawn_by_offset)
            * (1.0 - (tutor_p if tutor is not None else 0.0))
        )
        p_closer_reachable = 1.0 - p_no_closer

        # 3. Damage if the chain fires on this offset.
        chain = find_best_chain(
            hand=hand, mana=future_snap.my_mana,
            tutor_cost=tutor_cost if tutor else 0,
            storm_count=0,  # CR 500.4 — fresh count this turn
            medallions=count_reducers(battlefield),
        )
        expected_damage = chain.storm_damage if chain else 0.0

        # 4. Survival — P(we're alive by the start of this offset).
        survival_p = _survival_to_offset(snap, offset, bhi_state)

        # 5. Score = damage × survival × closer_reachable.
        score = expected_damage * survival_p * p_closer_reachable

        projections.append(TurnOffsetProjection(
            offset=offset,
            expected_damage=expected_damage,
            closer_reachable_p=p_closer_reachable,
            survival_p=survival_p,
            score=score,
            mana_at_offset=future_snap.my_mana,
            storm_at_offset=0,
            notes=f"offset={offset} tutor={tutor.template.name if tutor else 'none'}",
        ))

        # 6. Stop early if we'd be dead by this offset.
        if future_snap.my_life <= 0:
            break

    return tuple(projections)
```

### 5.2 Survival input — composition of `bhi.py` and `clock.py`

```python
def _survival_to_offset(
    snap: EVSnapshot,
    offset: int,
    bhi_state: BayesianHandTracker,
) -> float:
    """P(we survive `offset` opp turns).

    Inputs:
      * snap.opp_clock_discrete — turns until opp's clock kills
        us (clock.py, the BHI-aware Bayesian opp clock).
      * bhi_state.get_removal_probability() — P(opp has a
        removal spell that interacts with our chain pieces).

    Composition:
      * P(survive offset turns) =
          max(0, 1 - offset / opp_clock_discrete)
        — clock.py-derived, monotone in offset.
      * Then dampen by removal pressure on chain pieces:
          P_alive = P(survive) * (1 - p_removal * removal_threat_factor)
        where removal_threat_factor reflects how many chain pieces
        opp could realistically remove (bounded by
        CHAIN_REMOVAL_PRESSURE_FLOOR).

    All values derived from existing primitives; no new
    constants beyond CHAIN_REMOVAL_PRESSURE_FLOOR (a rules-
    sentinel — minimum survival in a fully-removal-leaden
    matchup, documented inline in scoring_constants.py).
    """
    opp_clock = max(1.0, snap.opp_clock_discrete)
    base_survival = max(0.0, 1.0 - offset / opp_clock)
    p_removal = bhi_state.get_removal_probability()
    survival = base_survival * (1.0 - p_removal * CHAIN_REMOVAL_PRESSURE_FLOOR)
    return max(0.0, min(1.0, survival))
```

### 5.3 Picking `best_turn_offset`

```python
best = max(projections, key=lambda p: p.score)
best_turn_offset = best.offset
```

The hold-vs-fire gate becomes a single comparison: `fire ⇔
best_turn_offset == 0`. There is no separate "hold for next
turn" branch — the rollout has already evaluated every offset
under the same objective.

### 5.4 Why this is non-trivial

The PR3c collapse came from an unbounded recursion: `hold_value
> fire_value` is a self-perpetuating predicate when next-turn
mana always grows. v3 closes this by making `best_turn_offset`
finite-horizon (max_depth = 3) AND making survival monotonically
decreasing in offset. By construction:

* offset=0 score = `dmg * P(closer in hand) * 1.0`
* offset=1 score = `dmg' * P(closer in hand or drawn) * P(survive 1 turn)`
* offset=k score is bounded above by survival decay × closer
  reachability ceiling.

The optimal offset is finite because survival → 0 as offset
grows (modulo opp_clock = ∞ no-clock matches, where the chain
score plateaus and the AI fires anyway because there's no
cost to more turns).

## 6. Test plan

Tests phrase the *rule* not the card. One test per chain pattern
× per new field, plus integration tests that name the gameplay
mechanic.

### 6.1 Library composition tests

```
tests/test_finisher_simulator_v3_library.py
  * test_library_composition_buckets_storm_fuel_by_tag
    — rule: cards with `'ritual'` / `'cantrip'` /
      `'storm_closer'` tags appear in the right buckets;
      no card-name lookups required.
  * test_p_draw_closer_zero_when_zero_closers_in_library
    — rule: P(draw closer | 5 draws, 0 closers in library)
      = 0.0.
  * test_p_draw_closer_monotone_in_n_draws
    — rule: more draws never decrease reachability probability.
  * test_p_draw_closer_hypergeometric_two_closers_in_forty
    — rule: closed-form check against the hypergeometric
      formula for a known small library.
```

### 6.2 Tutor-as-finisher-access tests

```
tests/test_finisher_simulator_v3_tutor.py
  * test_tutor_with_sb_storm_closer_marks_closer_in_zone_sb
    — rule: a tutor in hand and a STORM-keyword card in SB
      sets `closer_in_zone['sb'] = True` and gives the
      projection a non-zero `expected_damage`.
  * test_tutor_with_no_payoff_returns_no_access
    — rule: a tutor with no SB/library target makes no
      contribution (current v2 behaviour preserved).
  * test_tutor_resolution_probability_dampened_by_bhi_counters
    — rule: when bhi.get_counter_probability() = 0.5, the
      tutor's success_probability is dampened to <= 0.5
      (not zeroed — bounded below by
      CHAIN_TUTOR_MIN_RESOLVE).
  * test_tutor_extra_mana_cost_amortised_across_offsets
    — rule: the tutor's CMC is added to the chain's mana
      floor at the turn-offset the chain fires, not earlier.
```

### 6.3 Multi-turn rollout tests — one per chain pattern

```
tests/test_finisher_simulator_v3_storm.py
  * test_storm_chain_picks_offset_2_when_offset_0_misses_closer
    — rule: when no closer is in hand but P(draw closer | 2)
      > P(draw | 0), best_turn_offset is 2.
  * test_storm_chain_picks_offset_0_when_lethal_in_hand
    — rule: when closer is in hand and chain reaches lethal,
      best_turn_offset is 0 regardless of survival.
  * test_storm_chain_holds_when_offset_1_lethal_offset_0_subletha
    — rule: when offset 1 reaches lethal but offset 0 doesn't,
      best_turn_offset > 0 (the v2 hold gate, generalised).
  * test_storm_chain_seed_60600_g3t4d76_fuel_scores_positive
    — rule: at the exact game state from
      `replays/affinity_vs_storm_60600.ndjson:g3t4d76`, the
      chain-fuel cards (Manamorphose, Desperate Ritual,
      Reckless Impulse) score *positive* EV (chain progress)
      rather than -10 (current v2 baseline).

tests/test_finisher_simulator_v3_cascade.py
  * test_cascade_payoff_in_library_marks_offset_0_reachable
    — rule: cascade enabler in hand + payoff in library
      sets offset 0 closer_reachable_p = 1.0 (deck
      construction guarantees the hit).
  * test_cascade_no_enabler_returns_pattern_none
    — rule: cycling cards without a cascade enabler in hand
      AND no payoff in hand yield pattern = none across all
      offsets.

tests/test_finisher_simulator_v3_reanimation.py
  * test_reanimation_target_in_gy_picks_offset_0
    — rule: a creature in GY plus a reanimator in hand
      maximises score at offset 0.
  * test_reanimation_no_outlet_no_gy_target_picks_later_offset_when_outlet_drawable
    — rule: when the only path to a GY creature is a
      drawn discard outlet, best_turn_offset > 0 with
      closer_reachable_p folding in P(draw outlet).

tests/test_finisher_simulator_v3_cycling.py
  * test_cycling_payoff_in_hand_picks_offset_0
    — rule: cycling card + payoff in hand fires on offset 0.
  * test_cycling_to_fill_gy_picks_offset_1_when_payoff_via_cascade
    — rule: when the payoff is reached via a cascade
      enabler that needs another turn's mana, best offset
      moves to 1.
```

### 6.4 Survival composition tests

```
tests/test_finisher_simulator_v3_survival.py
  * test_survival_monotone_in_offset
    — rule: survival_p[i+1] <= survival_p[i] for every i.
  * test_survival_nonzero_when_no_clock
    — rule: when opp_clock = NO_CLOCK_DEFAULT, survival is
      capped at 1.0 (no decay).
  * test_survival_floored_above_zero_in_max_removal_density
    — rule: even with bhi.get_removal_probability() = 1.0,
      survival_p stays >= a documented floor; never zero
      so chains in heavy-removal matchups still get scored.
```

### 6.5 Acceptance regression tests

```
tests/test_finisher_simulator_v3_acceptance.py
  * test_storm_field_n50_holds_baseline
    — rule: Storm field N=50 win rate is >= 44%
      (`run_meta.py --field storm -n 50 --bo3`).
  * test_goryos_does_not_regress_more_than_5pp
    — rule: |WR(v3) - WR(v2)| < 5 for Goryo's Vengeance.
  * test_living_end_does_not_regress_more_than_5pp
  * test_amulet_does_not_regress_more_than_5pp
```

These last four are slow integration tests, gated behind a
`@pytest.mark.slow` marker so the fast suite stays fast; CI runs
them on the merge gate.

## 7. Acceptance gate for migration

Restated from `docs/PHASE_D_DEFERRED.md` "Path forward":

> PR3c — migration:
>   * Re-attempt the wire-up using the v2 API
>   * Storm field N=50 must hold ≥ 44%
>   * Goryo's / Living End / Amulet must not regress > 5pp
>   * Delete `card_combo_modifier`
>   * Delete the 4 stale tests in `test_combo_calc.py`

v3's API supports this gate without deck-specific patches because:

* **Storm vs fuel-without-closer.** v3's library composition +
  multi-turn rollout makes "build chain T4, find closer T5"
  legible. The seed 60600 d76/d78 trace fixes itself when
  `turn_projections[1].score > turn_projections[0].score`.
* **Goryo's vs no-target-in-GY.** v3's reanimation projection
  with `closer_reachable_p` folding in `P(discard outlet | N)`
  preserves the live signal.
* **Living End vs cycling-to-fill.** v3's cycling pattern with a
  cascade-fed payoff projects `closer_reachable_p` from library
  composition rather than a binary in-hand flag.
* **Amulet Titan.** Not a chain finisher per se, but its
  Primeval Titan game-end pattern composes via the reanimation
  predicate's "creature with high power" bucket — projection
  stays in-bounds.

Each acceptance criterion is mapped to a generic mechanic, not a
deck name. No `if archetype == "storm"` lives in v3.

## 8. Open questions

1. **Joint independence assumption for `p_closer_reachable`.**
   Section 5.1 step 2 treats "closer in hand", "drawn",
   "tutored" as independent events. They aren't — drawing a
   tutor and drawing a closer are positively correlated. The
   inclusion-exclusion approximation is conservative (it
   under-estimates `p_closer_reachable`), so it errs on the
   side of holding the chain. Is this acceptable for v1 of
   v3, or should we ship a proper joint distribution? Joint
   distribution requires a per-deck draw simulator; the
   conservative approximation is significantly cheaper.

2. **Caching strategy for `LibraryComposition`.** The
   composition is invariant within a player's library between
   draws. Should we bind it to `Player.library_composition`
   and update on every draw / mill / scry, or rebuild on demand
   per projection? The former is faster but leaks v3 state into
   the engine; the latter is pure but recomputes a lot. Lean
   towards on-demand with a per-snapshot cache (the
   `combo_evaluator._BASELINE_CACHE` pattern).

3. **Reanimation target floor — is gameplan JSON enough?**
   The `reanim_target_power_floor` lives in the gameplan JSON
   today. For decks without a declared floor (most decks
   shouldn't have one — they're not reanimator), what's the
   default? Proposed: a creature is a `reanim_target` iff its
   power × P(reanimator in hand) > some clock-derived
   threshold. Open whether that arithmetic should live in
   `library_composition.py` or `gameplan.py`.

4. **Chain-progress credit — does v3 subsume it?**
   `combo_evaluator.py:389-393` patches `chain_credit = 0`
   states by giving fuel cards a small positive credit. With
   v3's `turn_projections[i > 0].score > 0` for reachable
   chains, the patch may become unreachable code. Verify in
   the migration commit that removing the patch doesn't
   regress Storm.

5. **Cascade pattern's `expected_damage = 0`.** Cascade
   payoffs (Living End, Crashing Footfalls) deal damage via
   the resulting board, not via the projection arithmetic.
   v2 sidesteps this by setting `expected_damage = 0`. v3
   inherits the issue. Should the cascade projection be
   merged with `clock.py`'s post-board-state delta to give a
   non-zero `expected_damage`? Out of scope for v3 — flag for
   PR4.

6. **Tutor counter-density — whose counters count?**
   `bhi_state.get_counter_probability()` returns a single
   number; in reality a tutor cast on T+0 faces opp's T+0
   open-mana counter density, which differs from the closer's
   T+0 counter density (closer is cast same turn → opp must
   hold counter mana through tutor + closer). v3 simplifies
   to one P. Is that good enough, or do we need per-cast
   decomposition? Defer until v3 ships and we see the live
   trace.

7. **Optional code stub vs full implementation.** This doc
   ships an `ai/finisher_simulator_v3.py` stub with typed
   signatures and docstrings but no implementation. PR3c
   should wire the stub. Open: should PR3c be one-PR-with-
   migration or two-PR (implement-then-wire)? Lean towards
   two — easier to bisect a regression.

## 9. Cross-references

* `docs/PHASE_D_DEFERRED.md` — the failure mode that motivates
  v3 (39pp Storm regression, three iterations of the v2 hold
  gate that all collapsed Storm).
* `replays/affinity_vs_storm_60600.ndjson` — the canonical
  trace, decisions `g3t4d76` and `g3t4d78`. v3 must produce
  positive EV for chain-fuel at these decision points.
* `ai/finisher_simulator.py` — the v2 implementation. v3
  preserves its public surface, extends with new fields,
  promotes the `_project_storm_with_tutor_access` test bench
  to the primary path.
* `ai/combo_calc.py:603-911` — the live `card_combo_modifier`
  whose ~310 LOC of patches v3 + a clean `combo_evaluator.py`
  rewrite must replace. Particular hot-spots:
  * Lines 670-695: tutor-as-finisher-access branch — v3
    promotes this to a first-class projection input.
  * Lines 710-734: cost-reducer chain improvement — v3
    handles via the chain finder's medallion arithmetic;
    no separate code path needed.
  * Lines 741-808 (storm=0 ritual gate) and 818-859
    (storm>=1 ritual gate) — v3 covers both via the
    multi-turn rollout: the gates become "score is higher
    on a later offset" rather than hard sentinels.
* `ai/combo_evaluator.py` — the v2-targeted migration sketch.
  Will be rewritten against v3 in PR3c (open question 7).
* `ai/bhi.py` — counter / removal density inputs for v3's
  survival composition. No new BHI signal needed; the
  existing `get_counter_probability` and
  `get_removal_probability` cover the use case.
* `decks/gameplans/ruby_storm.json` — Storm's per-archetype
  config. v3 reads no new fields here; the `closer_categories`
  set is derived from oracle/keyword detection.
* `tools/check_abstraction.py` — the CI ratchet. v3 must
  ship without increasing the baseline counts. The new
  module `ai/library_composition.py` ships with zero card-
  name conditionals by construction.

## 10. Code stub note

A test-bench stub at `ai/finisher_simulator_v3.py` ships with
this design doc to make the API concrete enough to review.
The stub is **NOT WIRED**: nothing imports it, nothing in
`ai/combo_evaluator.py` is changed. Implementation is PR3c
work. The stub exists so reviewers can read typed signatures
and docstrings rather than re-deriving them from this doc.

If review consensus prefers the design doc alone, the stub is
deletable in one commit with no callers affected.
