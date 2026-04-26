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

from pydantic import BaseModel, ConfigDict


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
