#!/usr/bin/env python3
"""Gameplan consistency — every card a gameplan names must be in its deck.

Why this exists: `decks/gameplans/<slug>.json` is hand-authored per deck,
while `decks/modern_meta.py` MODERN_DECKS is refreshed from tournament
lists. Nothing tied the two together, so a decklist refresh silently left
gameplans naming cards the deck no longer plays. The consequences are
invisible at runtime — no exception, just wrong AI behaviour:

  * `ai/discard_advisor._declared_keystones` protects only gameplan-named
    cards (critical_pieces / mulligan_keys / always_early) when bottoming
    on a mulligan — phantom names protect nothing, so the deck's REAL
    payoffs get bottomed.
  * `card_priorities` / `card_roles` give gameplan weight only to named
    cards — the real payoffs score as unknown filler.

Precedent: `affinity.json` named Cranial Plating, Memnite, Ornithopter,
Springleaf Drum, Nettlecyst, Signal Pest, Frogmite, Sojourner's Companion
and Thought Monitor — none in the Affinity list at the time (only Mox
Opal overlapped). Ten other gameplans had smaller drift (banned or cut
cards still declared as payoffs / reactive_only / land_priorities).

This is a whole CLASS of silent breakage, so it is a check, not a fix.

Rules enforced (hard gate — no baseline, the count must be zero):
  1. Every gameplan's `deck_name` must match a MODERN_DECKS entry (an
     orphaned gameplan is never loaded — `load_gameplan` matches on the
     `deck_name` field, not the filename).
  2. Every card name a gameplan references must be in that deck's
     mainboard ∪ sideboard (sideboard counts: post-board goals and Wish
     targets legitimately name SB cards).
  3. Every field in a gameplan must be classified below as either
     card-bearing or non-card. An unknown field fails the check so a new
     card-bearing schema field cannot silently escape the walk.

Matching is exact-string, the same rule the loader and the AI use — a
gameplan name that differs from the decklist name by case, punctuation or
a split-card face is exactly the drift this check exists to catch.

Usage:
    python tools/check_gameplan_consistency.py
    python tools/check_gameplan_consistency.py --list   # per-deck references
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
GAMEPLANS_DIR = ROOT / "decks" / "gameplans"

# ---------------------------------------------------------------------------
# Gameplan schema classification.
#
# Every key that can appear in a gameplan JSON is listed in exactly one of
# the groups below. `collect_card_refs` walks the card-bearing groups and
# reports any key it does not recognise — so when a new field is added to
# `decks/gameplan_loader._parse_gameplan`, this table must be extended in
# the same change (the check fails loudly until it is).
# ---------------------------------------------------------------------------

# Top-level: list of card names.
TOP_CARD_LISTS: frozenset = frozenset({
    "mulligan_keys",
    "always_early",
    "reactive_only",
    "critical_pieces",
})

# Top-level: dict keyed by card name (value = weight).
TOP_CARD_DICTS: frozenset = frozenset({
    "land_priorities",
})

# Top-level: list of lists of card names (each inner list is one combo set).
TOP_CARD_LIST_OF_LISTS: frozenset = frozenset({
    "mulligan_combo_sets",
})

# Top-level: list of dicts, each `{role_name: [card, ...]}`.
TOP_CARD_ROLE_PATHS: frozenset = frozenset({
    "mulligan_combo_paths",
})

# Top-level: list of goal objects (see GOAL_* below).
TOP_GOAL_LISTS: frozenset = frozenset({
    "goals",
    "fallback_goals",
})

# Top-level: scalars / config that never carry a card name.
TOP_NON_CARD: frozenset = frozenset({
    "deck_name",
    "archetype",
    "archetype_subtype",
    "strategy_tags",
    "combo_readiness_check",
    "mulligan_min_lands",
    "mulligan_max_lands",
    "mulligan_require_creature_cmc",
    "mulligan_cmc_profile",
    "_mulligan_combo_paths_doc",
})

# Goal object: dict keyed by card name (value = priority).
GOAL_CARD_DICTS: frozenset = frozenset({
    "card_priorities",
})

# Goal object: dict of `{role_name: [card, ...]}`.
GOAL_CARD_ROLE_DICTS: frozenset = frozenset({
    "card_roles",
})

# Goal object: scalars that never carry a card name.
GOAL_NON_CARD: frozenset = frozenset({
    "goal_type",
    "description",
    "resource_target",
    "resource_zone",
    "resource_min_cmc",
    "min_turns",
    "hold_mana",
    "min_mana_for_payoff",
    "prefer_cycling",
})


# A reference is (json_path, card_name) so a violation can say where.
CardRef = Tuple[str, str]


def _list_refs(path: str, value: object, out: List[CardRef],
               problems: List[str]) -> None:
    if not isinstance(value, list):
        problems.append(f"{path}: expected a list of card names")
        return
    for i, name in enumerate(value):
        if isinstance(name, str):
            out.append((f"{path}[{i}]", name))
        else:
            problems.append(f"{path}[{i}]: expected a card-name string")


def _dict_key_refs(path: str, value: object, out: List[CardRef],
                   problems: List[str]) -> None:
    if not isinstance(value, dict):
        problems.append(f"{path}: expected a dict keyed by card name")
        return
    for name in value:
        out.append((f"{path}[{name!r}]", name))


def _role_dict_refs(path: str, value: object, out: List[CardRef],
                    problems: List[str]) -> None:
    if not isinstance(value, dict):
        problems.append(f"{path}: expected {{role: [cards]}}")
        return
    for role, cards in value.items():
        _list_refs(f"{path}.{role}", cards, out, problems)


def _goal_refs(path: str, goal: object, out: List[CardRef],
               problems: List[str]) -> None:
    if not isinstance(goal, dict):
        problems.append(f"{path}: expected a goal object")
        return
    for key, value in goal.items():
        gpath = f"{path}.{key}"
        if key in GOAL_CARD_DICTS:
            _dict_key_refs(gpath, value, out, problems)
        elif key in GOAL_CARD_ROLE_DICTS:
            _role_dict_refs(gpath, value, out, problems)
        elif key in GOAL_NON_CARD:
            continue
        else:
            problems.append(
                f"{gpath}: unclassified goal field — add it to "
                f"GOAL_CARD_DICTS / GOAL_CARD_ROLE_DICTS / GOAL_NON_CARD "
                f"in tools/check_gameplan_consistency.py"
            )


def collect_card_refs(data: dict) -> Tuple[List[CardRef], List[str]]:
    """Walk one gameplan dict.

    Returns `(refs, problems)`: every `(json_path, card_name)` the gameplan
    references, and any structural problems (unclassified fields, wrong
    shapes). Pure function of the JSON — no deck lookup here.
    """
    refs: List[CardRef] = []
    problems: List[str] = []
    for key, value in data.items():
        if key in TOP_CARD_LISTS:
            _list_refs(key, value, refs, problems)
        elif key in TOP_CARD_DICTS:
            _dict_key_refs(key, value, refs, problems)
        elif key in TOP_CARD_LIST_OF_LISTS:
            if not isinstance(value, list):
                problems.append(f"{key}: expected a list of card-name lists")
                continue
            for i, inner in enumerate(value):
                _list_refs(f"{key}[{i}]", inner, refs, problems)
        elif key in TOP_CARD_ROLE_PATHS:
            if not isinstance(value, list):
                problems.append(f"{key}: expected a list of role dicts")
                continue
            for i, inner in enumerate(value):
                _role_dict_refs(f"{key}[{i}]", inner, refs, problems)
        elif key in TOP_GOAL_LISTS:
            if not isinstance(value, list):
                problems.append(f"{key}: expected a list of goals")
                continue
            for i, goal in enumerate(value):
                _goal_refs(f"{key}[{i}]", goal, refs, problems)
        elif key in TOP_NON_CARD:
            continue
        else:
            problems.append(
                f"{key}: unclassified top-level field — add it to one of the "
                f"TOP_* groups in tools/check_gameplan_consistency.py"
            )
    return refs, problems


def deck_card_pool(deck: Dict[str, Dict[str, int]]) -> Set[str]:
    """Mainboard ∪ sideboard card names for one MODERN_DECKS entry."""
    pool: Set[str] = set()
    for zone in ("mainboard", "sideboard"):
        pool.update(deck.get(zone, {}).keys())
    return pool


def check_gameplan(deck_name: str, data: dict,
                   decks: Dict[str, Dict[str, Dict[str, int]]]) -> List[str]:
    """Violations for one gameplan against the registry. Empty = clean."""
    violations: List[str] = []
    refs, problems = collect_card_refs(data)
    for problem in problems:
        violations.append(f"{deck_name}: gameplan structure — {problem}")
    if deck_name not in decks:
        violations.append(
            f"{deck_name}: gameplan deck_name matches no MODERN_DECKS entry "
            f"(orphaned — load_gameplan will never return it)"
        )
        return violations
    pool = deck_card_pool(decks[deck_name])
    seen: Set[str] = set()
    for path, name in refs:
        if name in pool or name in seen:
            continue
        seen.add(name)
        violations.append(
            f"{deck_name}: gameplan card {name!r} not in list (at {path})"
        )
    return violations


def gameplan_files() -> Iterable[Path]:
    """Per-deck gameplan JSONs. Underscore-prefixed files are shared
    registries (`_matchup_roles.json`, `_oracle_classifier.json`), not
    deck gameplans, and are skipped — same convention as the loader,
    which matches on the `deck_name` field those files do not carry."""
    for p in sorted(GAMEPLANS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        yield p


def load_decks() -> Dict[str, Dict[str, Dict[str, int]]]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from decks.modern_meta import MODERN_DECKS  # noqa: WPS433 — lazy on purpose
    return MODERN_DECKS


def run(decks: Dict[str, Dict[str, Dict[str, int]]],
        files: Iterable[Path]) -> List[str]:
    violations: List[str] = []
    for path in files:
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            violations.append(f"{path.name}: invalid JSON — {exc}")
            continue
        deck_name = data.get("deck_name")
        if not isinstance(deck_name, str) or not deck_name:
            violations.append(f"{path.name}: gameplan has no deck_name")
            continue
        violations.extend(check_gameplan(deck_name, data, decks))
    return violations


def main(argv: List[str]) -> int:
    decks = load_decks()
    files = list(gameplan_files())

    if "--list" in argv:
        for path in files:
            data = json.loads(path.read_text())
            deck_name = data.get("deck_name", "?")
            refs, _ = collect_card_refs(data)
            names = sorted({n for _, n in refs})
            pool = deck_card_pool(decks.get(deck_name, {}))
            missing = [n for n in names if n not in pool]
            mark = "!" if missing or deck_name not in decks else " "
            print(f"{mark} {deck_name} ({path.name}): "
                  f"{len(names)} referenced, {len(missing)} missing")
            for n in names:
                print(f"      {'!' if n in missing else ' '} {n}")
        return 0

    violations = run(decks, files)
    if violations:
        print(
            "GAMEPLAN CONSISTENCY VIOLATION: gameplans reference cards "
            "their deck does not play:",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nFix the gameplan JSON in decks/gameplans/ so it names the "
            "deck's ACTUAL cards (decks/modern_meta.py MODERN_DECKS, "
            "mainboard or sideboard). Replace a cut card with the real card "
            "that fills its role, or drop the reference if nothing does. "
            "Do not edit the decklist to satisfy the gameplan.",
            file=sys.stderr,
        )
        # stdout copy so CI annotations that only surface stdout still
        # show the offending names (same convention as check_doc_hygiene).
        print("GAMEPLAN CONSISTENCY: violations (stdout copy):")
        for v in violations:
            print(f"  {v}")
        return 1

    print(f"gameplan consistency OK: {len(files)} gameplans checked against "
          f"{len(decks)} decks")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
