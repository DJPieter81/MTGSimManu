"""
Loads deck gameplans from JSON files in decks/gameplans/.

Each JSON file defines a DeckGameplan with goals, mulligan config,
and card role assignments. This replaces the hardcoded _build_*
functions in ai/gameplan.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Import the dataclasses we're populating
from ai.gameplan import DeckGameplan, Goal, GoalType


_GAMEPLANS_DIR = Path(__file__).parent / "gameplans"

# Cache loaded gameplans
_cache: Dict[str, DeckGameplan] = {}


# Roles that count as "essential" for mulligan-key derivation. Excludes
# `interaction` (a control deck's removal suite isn't a mulligan key —
# the deck wins by interacting on the opp's clock, not by drawing
# interaction in the opener) and `support` / `engine` synonyms.
_MULLIGAN_KEY_ROLES = ("enablers", "payoffs", "finishers")

# Roles that contribute to `always_early` derivation: cards the deck
# wants to deploy on-curve as plan accelerators (engines, enablers,
# rituals).  `payoffs` / `finishers` / `interaction` are excluded —
# those are mid-/late-game plays whose timing is matchup-dependent.
_ALWAYS_EARLY_ROLES = ("engines", "enablers", "rituals")

# Maximum CMC to count as "always_early" purely on cost.  Cards above
# this threshold only qualify if they are explicitly marked as cost
# reducers (i.e. they accelerate the rest of the plan even at higher
# CMC, e.g. Ruby Medallion at 2 mana).  Set to 1 because 0/1-CMC
# enablers are universally on-curve in turn 1 — a 2-CMC enabler
# competes with a 2-drop threat or removal spell, so deferral can be
# correct.
_ALWAYS_EARLY_MAX_CMC = 1

# Tags that mark a card as `reactive_only`: held up to interact rather
# than cast pro-actively.  Combined with an instant/flash oracle text
# requirement so sorcery-speed sweepers / removal aren't misclassified.
_REACTIVE_ONLY_TAGS = frozenset({"counterspell", "removal", "protection"})

# Word-boundary match for instant or flash in oracle text — same
# convention as `engine.card_database` keyword detection (avoids
# matching "flashback").
_INSTANT_OR_FLASH_RE = re.compile(r"\b(instant|flash)\b", re.IGNORECASE)


def _derive_mulligan_keys(goals: List["Goal"]) -> Set[str]:
    """Derive `mulligan_keys` from the goals' `card_roles` declarations.

    Returns the union of every goal's `enablers` / `payoffs` / `finishers`
    role buckets.  These are the cards the deck *needs* to assemble its
    plan — exactly the set a hand should be evaluated against at
    mulligan time.

    Used when a gameplan JSON omits or empties `mulligan_keys`.  An
    explicit JSON list always overrides the derived set (override
    semantics — the deck author may have a non-obvious mulligan rule).

    Per CLAUDE.md ABSTRACTION CONTRACT: card-specific knowledge lives in
    the goals, not duplicated as a hand-maintained `mulligan_keys` list
    that drifts out of sync with the goal definitions.
    """
    derived: Set[str] = set()
    for goal in goals:
        roles = goal.card_roles or {}
        for role_name in _MULLIGAN_KEY_ROLES:
            cards = roles.get(role_name)
            if cards:
                derived.update(cards)
    return derived


def _derive_always_early(
    goals: List["Goal"],
    decklist: Optional[Dict[str, int]],
    db: Optional[Any],
) -> Set[str]:
    """Derive `always_early` from decklist + oracle data.

    Returns the set of mainboard cards that the deck should always
    deploy on-curve.  A card qualifies when BOTH:

    1. It is referenced in any goal's `engines` / `enablers` / `rituals`
       role bucket — these are the plan-acceleration roles, distinct
       from `payoffs` / `finishers` (whose timing is matchup-dependent
       and lives behind `min_turns` / readiness gates).

    2. It is either tagged `cost_reducer` (the card itself accelerates
       the rest of the plan and should hit the table ASAP, even at
       2-CMC e.g. Ruby Medallion) OR has CMC <= `_ALWAYS_EARLY_MAX_CMC`
       (cheap enablers like Ornithopter / Guide of Souls have no
       opportunity cost — there's nothing else to spend turn 1 on).

    Cards not in `decklist` are filtered out (no cross-deck pollution
    when the same card name appears in multiple gameplans' goals).

    If `decklist` or `db` is None (e.g. a caller that doesn't have
    plumbing) the function returns an empty set, preserving the
    JSON-only behaviour for older call sites.

    Per CLAUDE.md ABSTRACTION CONTRACT: the rule is mechanic-driven
    (cost_reducer tag + CMC + role membership) and applies to every
    Modern card — no card-name lists.
    """
    if not decklist or db is None:
        return set()

    # Union of all "early-play" role buckets across every goal.  A
    # goal that leaves these unset contributes nothing.
    role_cards: Set[str] = set()
    for goal in goals:
        roles = goal.card_roles or {}
        for role_name in _ALWAYS_EARLY_ROLES:
            cards = roles.get(role_name)
            if cards:
                role_cards.update(cards)

    derived: Set[str] = set()
    for name in role_cards:
        if name not in decklist:
            continue  # role-listed but not in this deck's mainboard
        card = db.get_card(name)
        if card is None:
            continue  # db gap (split card half, unparsed) — silently skip
        is_cost_reducer = bool(getattr(card, "is_cost_reducer", False))
        cmc = getattr(card, "cmc", 99)
        if is_cost_reducer or cmc <= _ALWAYS_EARLY_MAX_CMC:
            derived.add(name)
    return derived


def _derive_reactive_only(
    decklist: Optional[Dict[str, int]],
    db: Optional[Any],
) -> Set[str]:
    """Derive `reactive_only` from decklist + oracle data.

    Returns mainboard cards that should be held up rather than cast
    pro-actively.  A card qualifies when BOTH:

    1. Its tags include at least one of {counterspell, removal,
       protection} — i.e. it interacts with the opponent.

    2. Its oracle text contains the word "instant" or "flash"
       (word-boundary matched, so "flashback" doesn't qualify).
       Sorcery-speed removal (e.g. Supreme Verdict) intentionally
       fails this check — the deck plays it on its own turn as a
       board reset, not as a held-up response.

    Cards not in `decklist` are filtered out.  If `decklist` or `db`
    is None the function returns an empty set (JSON-only fallback).

    Per CLAUDE.md ABSTRACTION CONTRACT: the rule applies to every
    Modern card via tags + oracle text; no card-name lists.
    """
    if not decklist or db is None:
        return set()

    derived: Set[str] = set()
    for name in decklist.keys():
        card = db.get_card(name)
        if card is None:
            continue
        tags = getattr(card, "tags", None) or set()
        if not (_REACTIVE_ONLY_TAGS & tags):
            continue
        oracle = getattr(card, "oracle_text", "") or ""
        if not _INSTANT_OR_FLASH_RE.search(oracle):
            continue
        derived.add(name)
    return derived


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
        min_mana_for_payoff=data.get("min_mana_for_payoff", 0),
        prefer_cycling=data.get("prefer_cycling", False),
        hold_mana=data.get("hold_mana", False),
        resource_target=data.get("resource_target", 0),
        resource_zone=data.get("resource_zone", "graveyard"),
        resource_min_cmc=data.get("resource_min_cmc", 0),
    )


def _parse_gameplan(
    data: Dict[str, Any],
    decklist: Optional[Dict[str, int]] = None,
    db: Optional[Any] = None,
) -> DeckGameplan:
    """Convert a JSON gameplan dict to a DeckGameplan dataclass.

    `decklist` and `db` are optional: when supplied, the loader
    derives `always_early` and `reactive_only` from oracle data when
    the JSON omits or empties those fields.  When omitted (older
    callers, tests), derivation is skipped — the JSON-only behaviour
    is preserved.

    Explicit JSON lists always win for all three derived fields
    (`mulligan_keys`, `always_early`, `reactive_only`).
    """
    goals = [_parse_goal(g) for g in data["goals"]]

    fallback_goals = None
    if "fallback_goals" in data:
        fallback_goals = [_parse_goal(g) for g in data["fallback_goals"]]

    # combo_readiness_check is a string reference to a function name
    combo_readiness_check = None
    if data.get("combo_readiness_check") == "generic_combo_readiness":
        from ai.gameplan import generic_combo_readiness
        combo_readiness_check = generic_combo_readiness

    # Mulligan keys: explicit JSON list overrides the derived set.
    # Empty / missing → derive from goals (single source of truth).
    explicit_keys = set(data.get("mulligan_keys", []))
    mulligan_keys = explicit_keys if explicit_keys else _derive_mulligan_keys(goals)

    # always_early: explicit JSON list overrides; empty/missing →
    # derive from decklist + oracle (cost_reducer / low-CMC enablers).
    explicit_early = set(data.get("always_early", []))
    always_early = (
        explicit_early
        if explicit_early
        else _derive_always_early(goals, decklist, db)
    )

    # reactive_only: explicit JSON list overrides; empty/missing →
    # derive from decklist + oracle (instant/flash interaction).
    explicit_reactive = set(data.get("reactive_only", []))
    reactive_only = (
        explicit_reactive
        if explicit_reactive
        else _derive_reactive_only(decklist, db)
    )

    return DeckGameplan(
        deck_name=data["deck_name"],
        goals=goals,
        mulligan_keys=mulligan_keys,
        mulligan_min_lands=data.get("mulligan_min_lands", 2),
        mulligan_max_lands=data.get("mulligan_max_lands", 4),
        mulligan_effective_cmc=data.get("mulligan_effective_cmc", {}),
        mulligan_require_creature_cmc=data.get("mulligan_require_creature_cmc", 0),
        mulligan_combo_sets=[set(s) for s in data.get("mulligan_combo_sets", [])],
        mulligan_combo_paths=[
            # Preserve list-of-strings shape for each bucket — the
            # mulligan decider intersects with hand_names directly.
            {
                bucket_name: list(bucket_cards)
                for bucket_name, bucket_cards in path.items()
            }
            for path in data.get("mulligan_combo_paths", [])
        ],
        land_priorities=data.get("land_priorities", {}),
        reactive_only=reactive_only,
        always_early=always_early,
        archetype=data.get("archetype", "midrange"),
        archetype_subtype=data.get("archetype_subtype"),
        combo_readiness_check=combo_readiness_check,
        fallback_goals=fallback_goals,
        critical_pieces=set(data.get("critical_pieces", [])),
    )


def load_gameplan(
    deck_name: str,
    decklist: Optional[Dict[str, int]] = None,
    db: Optional[Any] = None,
) -> Optional[DeckGameplan]:
    """Load a gameplan for a deck, using cache if available.

    `decklist` and `db` are optional plumbing for `always_early` /
    `reactive_only` derivation.  Callers that have a CardDatabase and
    the deck's mainboard handy should pass them so derivation
    activates when JSON omits those fields; older callers that don't
    can omit them — derivation falls back to empty (preserving
    JSON-only behaviour).

    Cache key includes only `deck_name`; the cache is populated on
    first load and re-used regardless of whether a later call
    supplies decklist/db.  This is fine because explicit JSON
    overrides never change between calls and decklist→derived data
    is stable per deck.
    """
    if deck_name in _cache:
        return _cache[deck_name]

    # Try to find a matching JSON file
    for json_file in _GAMEPLANS_DIR.glob("*.json"):
        try:
            with open(json_file) as f:
                data = json.load(f)
            if data.get("deck_name") == deck_name:
                plan = _parse_gameplan(data, decklist=decklist, db=db)
                _cache[deck_name] = plan
                return plan
        except (json.JSONDecodeError, KeyError):
            continue

    return None


def load_all_gameplans(
    decklists: Optional[Dict[str, Dict[str, int]]] = None,
    db: Optional[Any] = None,
) -> Dict[str, DeckGameplan]:
    """Load all gameplans from the gameplans directory.

    `decklists` is a dict of `{deck_name: mainboard}` used per-plan
    for `always_early` / `reactive_only` derivation when the JSON
    omits those fields.  Omitting it preserves JSON-only behaviour.
    """
    plans = {}
    decklists = decklists or {}
    for json_file in sorted(_GAMEPLANS_DIR.glob("*.json")):
        try:
            with open(json_file) as f:
                data = json.load(f)
            name = data.get("deck_name")
            if name:
                plans[name] = _parse_gameplan(
                    data,
                    decklist=decklists.get(name),
                    db=db,
                )
        except (json.JSONDecodeError, KeyError):
            continue
    _cache.update(plans)
    return plans


def clear_cache() -> None:
    """Clear the gameplan cache (for testing)."""
    _cache.clear()
