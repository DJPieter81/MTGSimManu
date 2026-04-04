"""
Loads supplemental card knowledge from decks/card_knowledge.json.

This provides AI-relevant metadata that's hard to derive from oracle text:
- threat_value: How threatening a card is (for AI targeting/evaluation)
- burn_damage: Damage a spell deals (for clock calculations)
- requires_target: Whether evoke/ETB needs a valid target
- tags: Additional tags beyond what oracle text parsing provides
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Set

_KNOWLEDGE_FILE = Path(__file__).parent / "card_knowledge.json"
_cache: Optional[Dict[str, dict]] = None


def _load() -> Dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(_KNOWLEDGE_FILE) as f:
            data = json.load(f)
        # Remove metadata keys
        _cache = {k: v for k, v in data.items() if not k.startswith("_")}
    except (FileNotFoundError, json.JSONDecodeError):
        _cache = {}
    return _cache


def get_card_knowledge(card_name: str) -> Optional[dict]:
    """Get knowledge dict for a card, or None if not known."""
    return _load().get(card_name)


def get_extra_tags(card_name: str) -> Set[str]:
    """Get supplemental tags for a card."""
    entry = _load().get(card_name)
    if entry and "tags" in entry:
        return set(entry["tags"])
    return set()


def get_threat_value(card_name: str) -> float:
    """Get the known threat value for a card (0.0 if unknown)."""
    entry = _load().get(card_name)
    if entry and "threat_value" in entry:
        return entry["threat_value"]
    return 0.0


def get_burn_damage(card_name: str) -> int:
    """Get the burn damage for a card (0 if not a burn spell)."""
    entry = _load().get(card_name)
    if entry and "burn_damage" in entry:
        return entry["burn_damage"]
    return 0


def requires_target(card_name: str) -> bool:
    """Check if a card requires a target for evoke/ETB."""
    entry = _load().get(card_name)
    if entry:
        return entry.get("requires_target", False)
    return False


def get_all_with_tag(tag: str) -> Set[str]:
    """Get all card names that have a specific tag."""
    data = _load()
    return {name for name, entry in data.items()
            if tag in entry.get("tags", [])}
