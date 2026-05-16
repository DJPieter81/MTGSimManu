"""Oracle classifier — pure-data loader for LLM-pre-populated tags.

This module is the runtime read side of the structural classifier
introduced for the 2026-05-16 audit (W0-A in
``/root/.claude/plans/create-some-extremely-verbose-validated-crane.md``).
It loads ``decks/gameplans/_oracle_classifier.json`` and exposes the
tag-membership query that engine/AI code uses INSTEAD OF regex-matching
oracle text inline.

The classifier tag IS the dispatch — no oracle-text parse happens at
runtime once a card is classified. The JSON cache is generated offline
by ``tools/build_oracle_classifier_cache.py`` (LLM-driven, deterministic
via SHA-256 keyed cache), committed to the repo, and read here.

Why a separate module (not a constant in ``ai/predicates.py``):
- The classifier cache file may grow to thousands of entries (one per
  Modern-legal card with at least one tag). A dedicated module keeps
  the load-and-query path testable in isolation.
- Tag membership is a public concept queried from both ``engine/`` and
  ``ai/``; importing ``ai/predicates.py`` from engine code is fine, but
  the classifier is also useful in pure-engine paths (cast-time
  legality, see ``engine/cast_manager.py``) where AI imports would
  bring scoring-layer transitive deps.

Until the W0-A LLM-cache build script runs the full Modern set, the
JSON file holds only seed entries (Teferi-TR and the closest
functional analogues for R4). The ``Tag`` enum is the single source of
truth for tag names; entries not in the JSON fall back to
``frozenset()`` (no claims).

Forward-compatibility: when ``tools/build_oracle_classifier_cache.py``
expands the cache to the full Modern set, no call-site code changes —
the same ``tags_for(card)`` query returns a larger frozenset.
"""
from __future__ import annotations

import json
import os
from enum import Enum
from typing import Dict, FrozenSet, Optional


class Tag(str, Enum):
    """Structural classifier tags asserted about a card's oracle text.

    Each tag corresponds to a *mechanic* (not a card name). Engine and
    AI code dispatch on tag membership instead of inline oracle-text
    regex. The full taxonomy lives in the Wave-0 plan; this enum grows
    incrementally with each fix that needs a new tag.

    R4 (W1a-4) seeds ``SORCERY_SPEED_LOCKOUT`` for the Teferi-TR family
    of static abilities. Future fixes (W1a-1 IMPULSE_DRAW, W1a-3
    ETB_SURVEIL_N, ...) extend this enum and the JSON cache together.
    """

    SORCERY_SPEED_LOCKOUT = "SORCERY_SPEED_LOCKOUT"


# Path to the committed cache file. Resolved relative to the project
# root (parent of this module's package directory).
_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "decks", "gameplans", "_oracle_classifier.json",
)


_LOADED_TAGS: Optional[Dict[str, FrozenSet[Tag]]] = None


def _parse_tag(s: str) -> Optional[Tag]:
    """Convert a string from the JSON cache to a ``Tag`` enum.

    Unknown tag strings (e.g., one that exists in a newer cache than
    this code knows about) return ``None`` and are filtered out. This
    keeps backward compatibility when the cache is bumped ahead of
    the code.
    """
    try:
        return Tag(s)
    except ValueError:
        return None


def load_oracle_tags() -> Dict[str, FrozenSet[Tag]]:
    """Return ``{card_name: frozenset(Tag, ...)}`` for every card with
    at least one classifier tag in the cache.

    Cached after the first call — the JSON is read once per process.
    Cards absent from the cache map to ``frozenset()`` via
    ``tags_for``; this function only returns the explicitly-classified
    cards.
    """
    global _LOADED_TAGS
    if _LOADED_TAGS is not None:
        return _LOADED_TAGS

    try:
        with open(_CACHE_PATH) as f:
            blob = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _LOADED_TAGS = {}
        return _LOADED_TAGS

    out: Dict[str, FrozenSet[Tag]] = {}
    for name, tag_strs in (blob.get("tags_by_card", {}) or {}).items():
        parsed = frozenset(t for t in (_parse_tag(s) for s in tag_strs) if t)
        if parsed:
            out[name] = parsed
    _LOADED_TAGS = out
    return _LOADED_TAGS


def tags_for(card_name: str) -> FrozenSet[Tag]:
    """Return the classifier tags for a card by name.

    Cards not in the cache return ``frozenset()`` — they are
    *unclaimed*, NOT *negative*. Engine code that wants a positive
    membership test should use ``Tag.X in tags_for(name)``, which
    returns ``False`` for unclaimed cards (the safe default).
    """
    return load_oracle_tags().get(card_name, frozenset())


def _reset_for_tests() -> None:
    """Test-only: clear the module-level cache so a test that mutates
    the on-disk JSON sees the change on next read."""
    global _LOADED_TAGS
    _LOADED_TAGS = None
