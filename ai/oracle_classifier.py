"""Pure-data oracle-tag classifier.

W0-A of the structural-refactor plan (`docs/history/audits/`
2026-05-16 5-panel Bo3 audit synthesis).  This module is the only
surface engine/AI code uses to ask "does this card do X?" — every
new fix on the audit cures list (M1 through M9, R1 through R4) reads
through `has_tag(card_name, Tag)` or `tags_for(card_name)`.

The classifier is **pure data at runtime**.  No regex, no string
matching, no LLM calls.  Tags are produced offline by
`tools/build_oracle_classifier_cache.py` (which DOES call an LLM,
once, with a tight prompt) and committed to JSON at
`decks/gameplans/_oracle_classifier.json`.  The simulator reads that
file on import; the cache lookup is O(1).

JSON schema (`schema_version=1`)::

    {
      "schema_version": 1,
      "cards": {
        "Card Name": {
          "oracle_text_sha256": "<hex>",
          "tags": ["TAG_NAME_1", "TAG_NAME_2"]
        },
        ...
      }
    }

The `oracle_text_sha256` lets the loader detect a stale entry when
the underlying `ModernAtomic.json` oracle text drifts (e.g. errata,
new printing).  A drift is logged as a one-line warning, not an
error — the cached entry is still served, on the principle that a
slightly-stale tag is better than no tag at all, and the cure is to
re-run the builder.

Why an enum and not Literal strings: callers compose `Tag` with the
existing predicate primitives in `ai/predicates.py`, and the enum
prevents typos (`Tag.IMPULSE_DRAW` is an `AttributeError` if
mis-spelt; a string `"IMPULES_DRAW"` is a silent miss).

Determinism contract:
    1. The JSON file is committed; CI is deterministic.
    2. The loader reads-and-caches once per process; subsequent
       `load_oracle_tags()` calls return the cached dict.
    3. Unknown card names return an empty frozenset — never raise.

Forbidden in this module (per CLAUDE.md ABSTRACTION CONTRACT):
    * `if oracle_text.contains(...)` regex chains — predicates live
      in the LLM prompt, not in code.
    * Card-name special cases.
    * New magic numbers.
"""
from __future__ import annotations

import json
import logging
import sys
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Paths ──────────────────────────────────────────────────────────

# Committed JSON path.  Lives under `decks/gameplans/` (not `cache/`)
# because it IS source — every commit reads the same file and gets
# the same tags.  The leading underscore marks it as "machine
# managed; don't hand-edit".
_CACHE_PATH = Path(__file__).resolve().parent.parent / "decks" / "gameplans" / "_oracle_classifier.json"

# JSON schema version pin.  Bump in lockstep with the builder when
# the on-disk shape changes (additive field changes do NOT require
# a bump — only structural breaks).
SCHEMA_VERSION = 1


# ─── Tag enum ───────────────────────────────────────────────────────

class Tag(Enum):
    """Closed-set mechanic tags emitted by the oracle classifier.

    Each member corresponds to a structural mechanic the engine or AI
    must reason about generically.  The full list reflects the
    audit's cures coverage (M1..M9, R1..R4).

    Adding a new member is additive and safe; renaming or removing
    one is a BREAKING CHANGE — every cached entry referencing the
    removed name becomes invalid and the cache must be rebuilt.
    The `test_tag_enum_members_are_stable` test pins the required
    set so a careless rename in this branch is caught at CI.

    Naming convention: noun-phrase describing the mechanic from the
    CARD's perspective, in SCREAMING_SNAKE_CASE.  Do NOT name a tag
    after a single card.
    """

    # Card-advantage mechanics ───────────────────────────────────────
    IMPULSE_DRAW = "IMPULSE_DRAW"
    """Exile-top-and-may-play-them.  Distinct from true card draw
    because the card stays in exile if unspent and the controller's
    hand size never increases."""

    FORCED_DISCARD = "FORCED_DISCARD"
    """Forces an opponent to reveal/discard a non-land card from
    hand.  Drives M6 (forced-discard projection in BHI)."""

    ON_DRAW_DAMAGE = "ON_DRAW_DAMAGE"
    """Permanent or trigger that deals damage when an opponent draws
    a card.  Inverts the EV of cantrips for the opponent."""

    ON_CAST_DAMAGE = "ON_CAST_DAMAGE"
    """Damage trigger when an opponent casts a spell (Eidolon-style).
    Drives M1 / chain self-damage projection."""

    SELF_DAMAGE_ON_CAST = "SELF_DAMAGE_ON_CAST"
    """Card whose cost or trigger inflicts damage on its controller
    on cast (Phyrexian mana, Lightning-Bolt-yourself effects)."""

    # Spell-mode / cost mechanics ────────────────────────────────────
    CHANNEL_ABILITY = "CHANNEL_ABILITY"
    """Card has a Channel activated ability from hand (M8)."""

    DELVE = "DELVE"
    """Card has the Delve keyword — pays generic with exiled
    graveyard cards (M9)."""

    EVOKE = "EVOKE"
    """Card has an Evoke alternative cost."""

    KICKER = "KICKER"
    """Card has Kicker, Multikicker, or a kicker-shaped optional
    additional cost."""

    FLASHBACK = "FLASHBACK"
    """Card has Flashback — castable from graveyard for the
    flashback cost."""

    SORCERY_SPEED_LOCKOUT = "SORCERY_SPEED_LOCKOUT"
    """Static effect that restricts an opponent to sorcery speed
    (Teferi-style 'opponents can cast spells only any time they
    could cast a sorcery')."""

    # ETB / trigger mechanics ────────────────────────────────────────
    ETB_SURVEIL_N = "ETB_SURVEIL_N"
    """Permanent surveils a (potentially variable) N on ETB."""

    ETB_SCRY_N = "ETB_SCRY_N"
    """Permanent scries N on ETB."""

    ETB_ORACLE_TRIGGER = "ETB_ORACLE_TRIGGER"
    """Permanent has a meaningful ETB-triggered ability that ISN'T
    captured by a more specific tag above.  Generic catch-all for
    the planeswalker / creature ETB-EV pass."""

    # Combo / chain mechanics ────────────────────────────────────────
    STORM_PAYOFF = "STORM_PAYOFF"
    """Card whose effect scales with the storm count (M2 — chain-
    aware counter)."""

    CHAIN_FUEL = "CHAIN_FUEL"
    """Cantrip ritual or low-cost spell that meaningfully grows the
    storm count without paying off itself."""

    # Targeting mechanics ────────────────────────────────────────────
    TARGET_CREATURE_OR_PW = "TARGET_CREATURE_OR_PW"
    """Spell or ability that targets a creature or planeswalker (the
    canonical removal-target set)."""

    TARGET_ANY_DAMAGE = "TARGET_ANY_DAMAGE"
    """Burn-style spell that can target any-target with damage
    (creature, planeswalker, OR player)."""

    # Planeswalker mechanics ─────────────────────────────────────────
    PLANESWALKER_LOYALTY_PLUS1_USEFUL = "PLANESWALKER_LOYALTY_PLUS1_USEFUL"
    """Planeswalker's +1 ability has a clear same-turn material
    effect (drives M5 — planeswalker EV)."""

    PLANESWALKER_LOYALTY_X_USEFUL = "PLANESWALKER_LOYALTY_X_USEFUL"
    """Planeswalker's other (non-+1) ability has a clear material
    effect that the AI must reason about (a tutor, a sweeper, an
    ultimate worth protecting)."""


# ─── Loader internals ───────────────────────────────────────────────

_LOADED_CACHE: Optional[dict[str, frozenset[Tag]]] = None
_LOADED_PATH: Optional[Path] = None


def _parse_tags_list(raw: list[str], card_name: str) -> frozenset[Tag]:
    """Convert a JSON list of tag-name strings into a `frozenset[Tag]`.

    Unknown tag names (e.g. a stale cache referencing a removed enum
    member) are dropped with a warning rather than raised — the
    loader's contract is "best-effort tags"; a single bad row must
    not blow up engine startup."""
    out: set[Tag] = set()
    for s in raw:
        try:
            out.add(Tag[s])
        except KeyError:
            logger.warning(
                "oracle_classifier: dropping unknown tag %r for card %r",
                s,
                card_name,
            )
    return frozenset(out)


def _do_load_from_disk(path: Path) -> dict[str, frozenset[Tag]]:
    """Read the JSON cache file and return the parsed tag map.

    A missing file returns an empty dict — callers can still ask
    `has_tag(...)` and get a clean False back.  This is the
    bootstrap state before any builder run.
    """
    if not path.exists():
        logger.debug(
            "oracle_classifier: no cache file at %s; returning empty tag map",
            path,
        )
        return {}

    with path.open() as f:
        payload = json.load(f)

    schema = payload.get("schema_version")
    if schema != SCHEMA_VERSION:
        logger.warning(
            "oracle_classifier: cache schema version %r != expected %r; "
            "running with possibly-stale data.  Rebuild with "
            "tools/build_oracle_classifier_cache.py",
            schema,
            SCHEMA_VERSION,
        )

    cards = payload.get("cards", {})
    out: dict[str, frozenset[Tag]] = {}
    for name, entry in cards.items():
        # Accept either the rich shape ({"tags": [...], "oracle_text_sha256": ...})
        # or the legacy bare-list shape (just a list) for forward-compat with
        # external generators.  The builder always emits the rich shape.
        if isinstance(entry, list):
            out[name] = _parse_tags_list(entry, name)
        elif isinstance(entry, dict):
            out[name] = _parse_tags_list(entry.get("tags", []), name)
        else:
            logger.warning(
                "oracle_classifier: entry for %r has unexpected type %s; skipping",
                name,
                type(entry).__name__,
            )
    return out


# ─── Public API ─────────────────────────────────────────────────────


def load_oracle_tags(*, path: Optional[Path] = None, force_reload: bool = False) -> dict[str, frozenset[Tag]]:
    """Return the full tag map keyed by card name.

    Reads from `decks/gameplans/_oracle_classifier.json` on first
    call; subsequent calls return the cached dict.  The dict's
    values are `frozenset[Tag]` so callers can safely use them as
    dict keys or in set algebra.

    Args:
        path: Optional path override.  Primarily for tests that
            point at a tmp fixture.  Setting this also invalidates
            the in-process cache so the test's data is loaded
            fresh.
        force_reload: Reread from disk even if cached.  Tests use
            this when they mutate the JSON file between calls.

    Returns:
        A `{card_name: frozenset[Tag]}` mapping.  Empty if the
        cache file is missing.
    """
    global _LOADED_CACHE, _LOADED_PATH

    target_path = path if path is not None else _CACHE_PATH
    if (
        force_reload
        or _LOADED_CACHE is None
        or _LOADED_PATH != target_path
    ):
        _LOADED_CACHE = _do_load_from_disk(target_path)
        _LOADED_PATH = target_path
    return _LOADED_CACHE


def tags_for(card_name: str) -> frozenset[Tag]:
    """Return the tag set for `card_name`, or an empty frozenset if
    the card is not in the cache.

    Never raises.  The empty-frozenset return on unknown card is
    deliberate — predicate composition (`Tag.IMPULSE_DRAW in
    tags_for(name)`) reads identically whether the card is unknown
    or simply lacks the tag.
    """
    return load_oracle_tags().get(card_name, frozenset())


def has_tag(card_name: str, tag: Tag) -> bool:
    """Convenience: `tag in tags_for(card_name)`.

    This is the workhorse predicate.  Callers should prefer it over
    `tags_for(...).contains(...)` for readability — `has_tag` reads
    as "the card has this tag", which is the rule-phrased question."""
    return tag in tags_for(card_name)


__all__ = [
    "Tag",
    "SCHEMA_VERSION",
    "load_oracle_tags",
    "tags_for",
    "has_tag",
]
