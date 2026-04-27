"""Type-safe decision-layer schemas (Pydantic).

Every option, choice, cost, and effect that flows through the AI's
decision pipeline is one of these models.  They double as runtime
validation AND as the contract that PR3 authoring agents (oracle
parser, gameplan synthesizer, etc.) will produce as structured output.

Design rule: schemas describe DATA, not strategic preference.  No
archetype branches, no card names, no magic constants live here —
those belong in the choice lists callers build and in the EV
evaluator the kernel calls.
"""
from __future__ import annotations
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# Discriminated kinds — extend as new mechanics are added.  The
# engine knows how to charge each cost kind; the AI projects each
# effect kind onto an EVSnapshot delta.
CostKind = Literal[
    "life", "mana", "discard", "sacrifice",
    "tap_creatures", "exile_from_gy",
]
EffectKind = Literal[
    "etb_untapped", "produce_mana", "draw_cards",
    "deal_damage", "search_library", "reanimate",
    "exile_target", "counter_target", "scry_then_draw",
]
ChoiceSource = Literal[
    "cast", "target", "optional_cost",
    "block", "attack", "activate", "mulligan",
]

# Finisher pattern — derived from oracle/keyword/tag detection.  Each
# pattern corresponds to a different "how does this deck close out a
# game" mechanism and dictates which fields of `FinisherProjection`
# are meaningful.  Adding a new pattern is purely additive: extend
# this Literal and teach `simulate_finisher_chain` to recognize it.
FinisherPattern = Literal[
    "storm",         # ritual + cantrip → STORM-keyword closer (Grapeshot)
    "cascade",       # cascade trigger casts free spell from library
    "reanimation",   # discard outlet + reanimator → big creature
    "cycling",       # cycle to fill GY → cycling-payoff closer
    "none",          # no chain reachable from current state
]


class CostDescriptor(BaseModel):
    """Typed cost paid as part of an OptionalCost or activated ability."""
    kind: CostKind
    amount: int = 0
    color: Optional[str] = None
    target_filter: Optional[str] = None

    model_config = ConfigDict(frozen=True)


class EffectDescriptor(BaseModel):
    """Typed effect produced by paying a cost."""
    kind: EffectKind
    magnitude: int = 0
    target_zone: Optional[str] = None
    target_filter: Optional[str] = None
    colors: Optional[tuple[str, ...]] = None  # produced colors for mana effects

    model_config = ConfigDict(frozen=True)


class OptionalCost(BaseModel):
    """One optional payment the engine offers the AI.

    Discovered by oracle parsing — never hand-named per card.  Both
    `apply_to_game` (engine resolution) and `apply_to_snap` (AI
    projection) are produced by the same parser so they cannot drift.
    """
    name: str
    cost: CostDescriptor
    effect: EffectDescriptor
    apply_to_game: Callable[..., Any]
    apply_to_snap: Callable[..., Any]

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)


class Choice(BaseModel):
    """One option presented to `best_choice()`.

    `apply` mutates an EVSnapshot copy in place and returns the
    mutated snapshot — the kernel scores each option by calling
    `evaluate_board` on the result.
    """
    name: str
    apply: Callable[..., Any]
    rationale: str = ""
    source: ChoiceSource

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)


class FinisherProjection(BaseModel):
    """Projected EV-impact of attempting a finisher chain.

    Produced by `ai.finisher_simulator.simulate_finisher_chain`.  A
    *projection* — a pure-function summary of what the chain would
    look like if cast from the current snapshot.  No game-state
    mutation happens to compute it.

    Field semantics by pattern:

    * `pattern` — the detected closing mechanism, or "none" when no
      chain is reachable from the current state.
    * `expected_damage` — projected damage dealt to the opponent if
      the chain fires.  Storm: storm count of the closer.
      Reanimation: combat power of the reanimated creature.  Cascade
      / cycling: 0 unless the hit deals direct damage (typical
      payoffs are board swings, not burn finishers).
    * `success_probability` — P(chain finds a finisher).  1.0 when
      the closer is in hand; lower when the chain depends on a
      tutor / cascade / draw to reach the closer.
    * `mana_floor` — minimum mana required to make the chain viable.
      Storm: cmc of the cheapest closer (or tutor cmc when only a
      tutor is available).  Reanimation: cmc of the reanimator
      spell.  Cycling: cycling cost of the cheapest cycler.
      Cascade: cmc of the cheapest cascade enabler.
    * `chain_length` — projected number of spells cast (including
      the closer).  Storm uses the chain finder's `storm_count`;
      other patterns use a simple step count (cycle + payoff = 2,
      enabler + cascade-cast = 2, outlet + reanimator = 2).
    * `closer_name` — name of the finisher card the simulator
      expects to close on, or None if no closer is reachable.

    All numeric values come from `combo_chain.find_all_chains`
    arithmetic, oracle text, or rules constants documented inline
    in `ai/finisher_simulator.py`.  No tuning weights live here.

    Simulator v2 fields (PR3b):

    * `hold_value` — projected EV of NOT casting any finisher this
      turn — i.e. holding the chain pieces and developing instead.
      Computed as `next_turn_damage` debited by the
      opp-pressure-cost of letting opp untap once.  When
      `hold_value > expected_damage * success_probability`, the AI
      should hold the chain rather than fire it.
    * `next_turn_damage` — projected damage of the chain if cast
      next turn given an additional land drop and one drawn card.
      Approximated by re-running the chain finder with mana + 1.
    * `coverage_ratio` — `expected_damage / opp_life`, clamped to
      [0, 1].  When > 0.5 mid-chain, additional fuel investments
      become catastrophic if the closer doesn't land — the
      "stranded chain" risk grows nonlinearly.
    * `closer_in_zone` — which zones contain a viable closer card.
      Keys: 'hand', 'sb', 'library', 'graveyard'.  Used by the
      tutor-as-finisher gate: a tutor with no closer in any zone
      is dead.
    """
    pattern: FinisherPattern = "none"
    expected_damage: float = Field(default=0.0, ge=0.0)
    success_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    mana_floor: int = Field(default=0, ge=0)
    chain_length: int = Field(default=0, ge=0)
    closer_name: Optional[str] = None

    # ── v2 fields ──
    hold_value: float = Field(default=0.0, ge=0.0)
    next_turn_damage: float = Field(default=0.0, ge=0.0)
    coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    closer_in_zone: dict[str, bool] = Field(
        default_factory=lambda: {
            'hand': False, 'sb': False,
            'library': False, 'graveyard': False,
        }
    )

    model_config = ConfigDict(frozen=True)
