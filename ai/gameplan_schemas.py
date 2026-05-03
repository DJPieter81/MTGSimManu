"""Type-safe pydantic mirrors of `DeckGameplan` for offline synthesis.

Phase 4 of the abstraction-cleanup pass.  These schemas are the typed
contract that an offline synthesizer (`tools/synth_gameplan.py`)
produces.  Output is serialized to JSON and consumed by
`decks.gameplan_loader.parse_gameplan`, which validates by round-trip.

Design rules (per CLAUDE.md ABSTRACTION CONTRACT):
- The schemas describe DATA, not strategic preference.
- No archetype branches, no card names, no magic constants here —
  the synthesizer puts those into the goals' `card_roles` and into
  optional override fields.
- The pydantic surface here is intentionally a strict subset of the
  full dataclass `DeckGameplan` shape — anything the synthesizer can
  derive (e.g. `mulligan_keys` via Phase 3) is omitted from the
  schema and left for the loader's derivation step.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional, Set

from pydantic import BaseModel, ConfigDict, Field


GoalTypeStr = Literal[
    "DEPLOY_ENGINE", "FILL_RESOURCE", "RAMP",
    "EXECUTE_PAYOFF", "CURVE_OUT", "PUSH_DAMAGE",
    "DISRUPT", "PROTECT", "INTERACT",
    "GRIND_VALUE", "CLOSE_GAME",
]


class SynthesizedGoal(BaseModel):
    """Typed mirror of `ai.gameplan.Goal` for synth output.

    The synthesizer emits one of these per strategic phase. Card-name
    lists live in `card_roles` exclusively — `card_priorities` is the
    legacy hint surface that newer synthesis paths leave empty.
    """
    model_config = ConfigDict(extra="forbid")

    goal_type: GoalTypeStr
    description: str = ""
    card_priorities: Dict[str, float] = Field(default_factory=dict)
    card_roles: Dict[str, List[str]] = Field(default_factory=dict)
    transition_check: Optional[str] = None
    min_turns: int = 0
    min_mana_for_payoff: int = 0
    prefer_cycling: bool = False
    hold_mana: bool = False
    resource_target: int = 0
    resource_zone: str = "graveyard"
    resource_min_cmc: int = 0


class SynthesizedGameplan(BaseModel):
    """Typed mirror of `ai.gameplan.DeckGameplan` for synth output.

    Fields the loader can DERIVE are omitted on purpose — see Phase 3
    (`mulligan_keys` derived from goal `card_roles`).  The synthesizer
    may still emit them when it has a non-obvious override to record;
    in that case the loader's override semantics apply.
    """
    model_config = ConfigDict(extra="forbid")

    deck_name: str
    archetype: Literal["aggro", "midrange", "control", "combo", "tempo", "ramp"] = "midrange"
    archetype_subtype: Optional[str] = None
    goals: List[SynthesizedGoal]
    fallback_goals: Optional[List[SynthesizedGoal]] = None

    # Mulligan ranges — explicit defaults preserved (see DeckGameplan).
    mulligan_min_lands: int = 2
    mulligan_max_lands: int = 4
    mulligan_require_creature_cmc: int = 0
    mulligan_effective_cmc: Dict[str, int] = Field(default_factory=dict)

    # Override surfaces — populated only when the synthesizer wants to
    # disagree with the derivation defaults.  When omitted the loader
    # fills these from goal-driven derivation (Phase 3) or per-archetype
    # defaults (Phase 2).
    mulligan_keys: List[str] = Field(default_factory=list)
    mulligan_combo_sets: List[List[str]] = Field(default_factory=list)
    mulligan_combo_paths: List[Dict[str, List[str]]] = Field(default_factory=list)
    mulligan_cmc_profile: Dict[str, int] = Field(default_factory=dict)

    always_early: List[str] = Field(default_factory=list)
    reactive_only: List[str] = Field(default_factory=list)
    critical_pieces: List[str] = Field(default_factory=list)
    land_priorities: Dict[str, float] = Field(default_factory=dict)

    combo_readiness_check: Optional[Literal["generic_combo_readiness"]] = None


def to_json_dict(plan: SynthesizedGameplan) -> dict:
    """Serialize a `SynthesizedGameplan` to the JSON shape that
    `decks.gameplan_loader._parse_gameplan` consumes.

    The pydantic dump already matches the loader's expected keys for
    every field.  Empty override lists / dicts are written as-is — the
    loader treats them as "not specified" and applies derivation /
    defaults.
    """
    data = plan.model_dump(mode="json", exclude_none=True)
    # Filter empty defaults so the JSON stays minimal — the loader
    # uses dict.get(..., default) so missing keys reach the default.
    for key in (
        "mulligan_keys", "mulligan_combo_sets", "mulligan_combo_paths",
        "mulligan_cmc_profile", "always_early", "reactive_only",
        "critical_pieces", "land_priorities", "mulligan_effective_cmc",
        "fallback_goals",
    ):
        if key in data and not data[key]:
            del data[key]
    return data
