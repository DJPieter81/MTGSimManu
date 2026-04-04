"""
Loads deck gameplans from JSON files in decks/gameplans/.

Each JSON file defines a DeckGameplan with goals, mulligan config,
and card role assignments. This replaces the hardcoded _build_*
functions in ai/gameplan.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Import the dataclasses we're populating
from ai.gameplan import DeckGameplan, Goal, GoalType


_GAMEPLANS_DIR = Path(__file__).parent / "gameplans"

# Cache loaded gameplans
_cache: Dict[str, DeckGameplan] = {}


def _parse_goal(data: Dict[str, Any]) -> Goal:
    """Convert a JSON goal dict to a Goal dataclass."""
    # Convert card_roles values from lists to sets
    card_roles = {}
    for role, cards in data.get("card_roles", {}).items():
        card_roles[role] = set(cards)

    return Goal(
        goal_type=GoalType[data["goal_type"]],
        description=data.get("description", ""),
        card_priorities=data.get("card_priorities", {}),
        card_roles=card_roles,
        transition_check=data.get("transition_check"),
        min_turns=data.get("min_turns", 0),
        prefer_cycling=data.get("prefer_cycling", False),
        hold_mana=data.get("hold_mana", False),
        resource_target=data.get("resource_target", 0),
        resource_zone=data.get("resource_zone", "graveyard"),
        resource_min_cmc=data.get("resource_min_cmc", 0),
    )


def _parse_gameplan(data: Dict[str, Any]) -> DeckGameplan:
    """Convert a JSON gameplan dict to a DeckGameplan dataclass."""
    goals = [_parse_goal(g) for g in data["goals"]]

    fallback_goals = None
    if "fallback_goals" in data:
        fallback_goals = [_parse_goal(g) for g in data["fallback_goals"]]

    # combo_readiness_check is a string reference to a function name
    combo_readiness_check = None
    if data.get("combo_readiness_check") == "generic_combo_readiness":
        from ai.gameplan import generic_combo_readiness
        combo_readiness_check = generic_combo_readiness

    return DeckGameplan(
        deck_name=data["deck_name"],
        goals=goals,
        mulligan_keys=set(data.get("mulligan_keys", [])),
        mulligan_min_lands=data.get("mulligan_min_lands", 2),
        mulligan_max_lands=data.get("mulligan_max_lands", 4),
        mulligan_effective_cmc=data.get("mulligan_effective_cmc", {}),
        mulligan_require_creature_cmc=data.get("mulligan_require_creature_cmc", 0),
        mulligan_combo_sets=[set(s) for s in data.get("mulligan_combo_sets", [])],
        land_priorities=data.get("land_priorities", {}),
        reactive_only=set(data.get("reactive_only", [])),
        always_early=set(data.get("always_early", [])),
        archetype=data.get("archetype", "midrange"),
        combo_readiness_check=combo_readiness_check,
        fallback_goals=fallback_goals,
        critical_pieces=set(data.get("critical_pieces", [])),
    )


def load_gameplan(deck_name: str) -> Optional[DeckGameplan]:
    """Load a gameplan for a deck, using cache if available."""
    if deck_name in _cache:
        return _cache[deck_name]

    # Try to find a matching JSON file
    for json_file in _GAMEPLANS_DIR.glob("*.json"):
        try:
            with open(json_file) as f:
                data = json.load(f)
            if data.get("deck_name") == deck_name:
                plan = _parse_gameplan(data)
                _cache[deck_name] = plan
                return plan
        except (json.JSONDecodeError, KeyError):
            continue

    return None


def load_all_gameplans() -> Dict[str, DeckGameplan]:
    """Load all gameplans from the gameplans directory."""
    plans = {}
    for json_file in sorted(_GAMEPLANS_DIR.glob("*.json")):
        try:
            with open(json_file) as f:
                data = json.load(f)
            name = data.get("deck_name")
            if name:
                plans[name] = _parse_gameplan(data)
        except (json.JSONDecodeError, KeyError):
            continue
    _cache.update(plans)
    return plans


def clear_cache() -> None:
    """Clear the gameplan cache (for testing)."""
    _cache.clear()
