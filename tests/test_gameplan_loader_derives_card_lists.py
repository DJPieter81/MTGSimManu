"""Failing-test contract for `decks.gameplan_loader._derive_*` helpers.

Phase 3 of the abstraction-cleanup pass: card-specific lists in
gameplan JSONs (`mulligan_keys`, `always_early`, `reactive_only`)
should derive from the goals' `card_roles` declarations + decklist /
oracle data rather than being hand-maintained in parallel.

  - `_derive_mulligan_keys` (no decklist needed; goals only)
  - `_derive_always_early` (needs decklist + db: cost_reducer OR
    cmc <= 1 cards that goals reference as engines/enablers/rituals)
  - `_derive_reactive_only` (needs decklist + db: instant/flash
    interaction — counterspell / removal / protection tags)

The contract this test locks in:
  1. A gameplan with `mulligan_keys: []` (or omitted) populates from
     the union of every goal's `enablers` / `payoffs` / `finishers`.
  2. An explicit JSON `mulligan_keys` / `always_early` / `reactive_only`
     list always wins (override semantics — the deck author has the
     final say on edge cases).
  3. `interaction` / other roles do NOT contribute to `mulligan_keys`
     (a control deck's removal suite is not a mulligan key).
  4. `always_early` derives only from cards in the mainboard that are
     either tagged `cost_reducer` or have CMC <= 1, AND are referenced
     in any goal's enablers / engines / rituals role buckets.
  5. `reactive_only` derives only from cards in the mainboard whose
     oracle text mentions instant/flash AND that are tagged with
     counterspell / removal / protection.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from decks.gameplan_loader import (
    _parse_gameplan,
    _derive_mulligan_keys,
    _derive_always_early,
    _derive_reactive_only,
)
from ai.gameplan import Goal, GoalType


class _FakeCard:
    """Minimal CardTemplate stand-in for derivation tests.

    Only exposes the attributes the derivation helpers read:
    `cmc`, `is_cost_reducer`, `tags`, `oracle_text`.  Avoids dragging
    in the full ModernAtomic load just to test pure logic."""

    def __init__(self, cmc=0, is_cost_reducer=False, tags=None, oracle_text=""):
        self.cmc = cmc
        self.is_cost_reducer = is_cost_reducer
        self.tags = set(tags or ())
        self.oracle_text = oracle_text


class _FakeDB:
    """CardDatabase stand-in: dict-of-name → _FakeCard, exposes
    `.get_card(name)` like the real CardDatabase."""

    def __init__(self, cards):
        self._cards = dict(cards)

    def get_card(self, name):
        return self._cards.get(name)


def _minimal_data(mulligan_keys=None) -> dict:
    """Build a minimal gameplan dict with one goal that declares
    enablers, payoffs, finishers, and interaction roles.  The
    interaction role exists to assert it is NOT included in the
    derived mulligan_keys set."""
    data = {
        "deck_name": "TestDerive",
        "goals": [
            {
                "goal_type": "CURVE_OUT",
                "card_priorities": {},
                "card_roles": {
                    "enablers": ["EnablerA", "EnablerB"],
                    "payoffs": ["PayoffA"],
                    "finishers": ["FinisherA"],
                    "interaction": ["RemovalA", "CounterA"],
                },
            }
        ],
    }
    if mulligan_keys is not None:
        data["mulligan_keys"] = mulligan_keys
    return data


def test_derive_mulligan_keys_from_goals_when_json_omits_field():
    """JSON omits `mulligan_keys` → derived from goal roles."""
    gp = _parse_gameplan(_minimal_data(mulligan_keys=None))
    assert gp.mulligan_keys == {
        "EnablerA", "EnablerB", "PayoffA", "FinisherA",
    }, (
        f"Expected derived mulligan_keys from enablers + payoffs + finishers, "
        f"got {gp.mulligan_keys!r}"
    )


def test_derive_mulligan_keys_from_goals_when_json_empty_list():
    """JSON sets `mulligan_keys: []` → derive (treat empty as missing)."""
    gp = _parse_gameplan(_minimal_data(mulligan_keys=[]))
    assert gp.mulligan_keys == {
        "EnablerA", "EnablerB", "PayoffA", "FinisherA",
    }


def test_explicit_mulligan_keys_overrides_derived_set():
    """JSON declares an explicit list → derived set is ignored entirely.
    The deck author may keep a card the goals don't classify as a key
    role (e.g. a sideboard pivot) or exclude one that the goals do
    classify (e.g. a redundant payoff)."""
    explicit = ["CustomKeyA", "CustomKeyB"]
    gp = _parse_gameplan(_minimal_data(mulligan_keys=explicit))
    assert gp.mulligan_keys == set(explicit), (
        f"Expected explicit override {set(explicit)}, got {gp.mulligan_keys!r}"
    )
    # Confirm derived cards are absent — override is total, not additive
    assert "EnablerA" not in gp.mulligan_keys


def test_interaction_role_does_not_contribute_to_mulligan_keys():
    """A control deck's removal/counter suite is in card_roles[interaction]
    but is NOT a mulligan key — the deck wins by interacting on the
    opp's clock, not by drawing interaction in the opener."""
    gp = _parse_gameplan(_minimal_data(mulligan_keys=None))
    assert "RemovalA" not in gp.mulligan_keys
    assert "CounterA" not in gp.mulligan_keys


def test_derive_helper_unioned_across_multiple_goals():
    """A multi-goal gameplan unions card_roles across every goal."""
    goals = [
        Goal(
            goal_type=GoalType.CURVE_OUT,
            description="",
            card_roles={"enablers": {"E1"}, "payoffs": {"P1"}},
        ),
        Goal(
            goal_type=GoalType.PUSH_DAMAGE,
            description="",
            card_roles={"finishers": {"F1"}, "payoffs": {"P2"}},
        ),
    ]
    derived = _derive_mulligan_keys(goals)
    assert derived == {"E1", "P1", "F1", "P2"}


def test_existing_gameplan_jsons_still_load_without_change():
    """Sanity: every shipped gameplan JSON still loads, and the
    explicit-override path means their mulligan_keys are unchanged
    (those JSONs all declare the field explicitly)."""
    from decks.gameplan_loader import load_all_gameplans, clear_cache
    clear_cache()
    plans = load_all_gameplans()
    assert len(plans) >= 16, f"Expected ≥16 gameplans, got {len(plans)}"
    # Sample-check: Boros Energy keeps its explicit mulligan_keys
    boros = plans.get("Boros Energy")
    assert boros is not None
    assert "Guide of Souls" in boros.mulligan_keys


# ─────────────────────────────────────────────────────────────────────
# `_derive_always_early` — needs decklist + db
# ─────────────────────────────────────────────────────────────────────


def _early_goals():
    """Two goals together exercising the engine/enabler/ritual roles
    that the always_early helper consults."""
    return [
        Goal(
            goal_type=GoalType.DEPLOY_ENGINE,
            description="",
            card_roles={
                "engines": {"FastMana", "EngineRock"},
                "enablers": {"OneDropA", "OneDropB"},
            },
        ),
        Goal(
            goal_type=GoalType.EXECUTE_PAYOFF,
            description="",
            card_roles={
                "rituals": {"RitualA"},
                "payoffs": {"FinisherA"},  # NOT included — payoffs are
                                            # combo finishers, not early plays
            },
        ),
    ]


def test_always_early_derives_cost_reducer_referenced_as_engine():
    """A cost_reducer card in the deck and tagged as an engine in any
    goal is an always_early play — it accelerates the rest of the
    plan, so casting it on curve is always correct."""
    decklist = {"FastMana": 4, "EngineRock": 4, "FinisherA": 2}
    db = _FakeDB({
        "FastMana": _FakeCard(cmc=2, is_cost_reducer=True, tags={"cost_reducer"}),
        "EngineRock": _FakeCard(cmc=3, tags={"mana_source"}),  # NOT a cost_reducer
        "FinisherA": _FakeCard(cmc=4, tags={"combo"}),
    })
    derived = _derive_always_early(_early_goals(), decklist, db)
    assert "FastMana" in derived, (
        f"Expected cost_reducer engine in always_early, got {derived!r}"
    )
    # FinisherA is not engine/enabler/ritual, never always_early
    assert "FinisherA" not in derived
    # EngineRock is cmc=3 and not a cost_reducer → not always_early
    assert "EngineRock" not in derived


def test_always_early_derives_low_cmc_enablers():
    """1-CMC cards referenced as enablers/engines/rituals are always
    correct on-curve plays.  CMC > 1 cards that aren't cost_reducers
    are excluded — those compete with on-curve threats."""
    decklist = {"OneDropA": 4, "OneDropB": 4, "EngineRock": 4, "RitualA": 4}
    db = _FakeDB({
        "OneDropA": _FakeCard(cmc=1, tags={"creature", "early_play"}),
        "OneDropB": _FakeCard(cmc=0, tags={"creature"}),
        "EngineRock": _FakeCard(cmc=3, tags={"mana_source"}),  # too expensive
        "RitualA": _FakeCard(cmc=1, tags={"ritual", "mana_source"}),
    })
    derived = _derive_always_early(_early_goals(), decklist, db)
    assert "OneDropA" in derived
    assert "OneDropB" in derived
    assert "RitualA" in derived
    assert "EngineRock" not in derived  # cmc=3, not a cost_reducer


def test_always_early_filters_to_mainboard():
    """Cards not in the decklist are excluded — derivation is per-deck,
    not a generic Modern set."""
    decklist = {"OneDropA": 4}  # OneDropB not in decklist
    db = _FakeDB({
        "OneDropA": _FakeCard(cmc=1, tags={"creature"}),
        "OneDropB": _FakeCard(cmc=0, tags={"creature"}),
    })
    derived = _derive_always_early(_early_goals(), decklist, db)
    assert "OneDropA" in derived
    assert "OneDropB" not in derived


def test_always_early_handles_missing_db_card_gracefully():
    """Cards referenced in goals/decklist but missing from the db
    (e.g. unparsed split cards, db gaps) are silently skipped — no
    crash."""
    decklist = {"OneDropA": 4, "MysteryCard": 4}
    db = _FakeDB({"OneDropA": _FakeCard(cmc=1, tags={"creature"})})
    # MysteryCard isn't in db.get_card() — should not crash
    derived = _derive_always_early(_early_goals(), decklist, db)
    assert "OneDropA" in derived
    assert "MysteryCard" not in derived


def test_always_early_returns_empty_when_no_decklist():
    """If decklist or db is None (e.g. tests calling load_gameplan
    without plumbing), derivation returns empty — preserving the
    original JSON-only behaviour."""
    derived = _derive_always_early(_early_goals(), None, None)
    assert derived == set()


def test_explicit_always_early_overrides_derived(tmp_path):
    """JSON declares an explicit always_early list → derived set is
    ignored entirely."""
    data = {
        "deck_name": "TestDerive",
        "goals": [
            {
                "goal_type": "DEPLOY_ENGINE",
                "card_roles": {"enablers": ["DerivedX"]},
            }
        ],
        "always_early": ["AuthorPickedY"],
        "mulligan_keys": ["k"],  # avoid mulligan_keys derivation interfering
    }
    decklist = {"DerivedX": 4, "AuthorPickedY": 4}
    db = _FakeDB({
        "DerivedX": _FakeCard(cmc=1),
        "AuthorPickedY": _FakeCard(cmc=4),  # cmc=4 → would NOT derive
    })
    gp = _parse_gameplan(data, decklist=decklist, db=db)
    assert gp.always_early == {"AuthorPickedY"}, (
        f"Explicit override must beat derivation, got {gp.always_early!r}"
    )
    assert "DerivedX" not in gp.always_early


def test_empty_always_early_triggers_derivation(tmp_path):
    """JSON sets `always_early: []` → derive (treat empty as missing)."""
    data = {
        "deck_name": "TestDerive",
        "goals": [
            {
                "goal_type": "DEPLOY_ENGINE",
                "card_roles": {"enablers": ["OneDropA"]},
            }
        ],
        "always_early": [],
        "mulligan_keys": ["k"],
    }
    decklist = {"OneDropA": 4}
    db = _FakeDB({"OneDropA": _FakeCard(cmc=1)})
    gp = _parse_gameplan(data, decklist=decklist, db=db)
    assert "OneDropA" in gp.always_early


# ─────────────────────────────────────────────────────────────────────
# `_derive_reactive_only` — needs decklist + db
# ─────────────────────────────────────────────────────────────────────


def test_reactive_only_derives_instant_speed_counterspell():
    """An instant counterspell in the deck → reactive_only by default
    (it should be held up rather than cast pre-emptively)."""
    decklist = {"CounterA": 4}
    db = _FakeDB({
        "CounterA": _FakeCard(
            cmc=2,
            tags={"counterspell", "interaction", "instant_speed"},
            oracle_text="instant. counter target spell.",
        ),
    })
    derived = _derive_reactive_only(decklist, db)
    assert "CounterA" in derived


def test_reactive_only_derives_flash_removal():
    """A creature with flash that has a removal effect (an ETB-kill
    creature) is reactive_only — the flash makes it a tempo response,
    not a curve play."""
    decklist = {"FlashKiller": 4}
    db = _FakeDB({
        "FlashKiller": _FakeCard(
            cmc=3,
            tags={"creature", "removal", "destroy_target_creature"},
            oracle_text="flash. when this creature enters, destroy target creature.",
        ),
    })
    derived = _derive_reactive_only(decklist, db)
    assert "FlashKiller" in derived


def test_reactive_only_excludes_sorcery_speed_removal():
    """Sorcery-speed removal (e.g. Supreme Verdict) is NOT reactive_only
    — the deck plays it on its own turn as a board-state reset, not as
    a held-up response.  It has the `removal` tag but no instant/flash."""
    decklist = {"SorceryWipe": 4}
    db = _FakeDB({
        "SorceryWipe": _FakeCard(
            cmc=4,
            tags={"removal", "board_wipe", "destroy_all_creatures"},
            oracle_text="sorcery. destroy all creatures.",
        ),
    })
    derived = _derive_reactive_only(decklist, db)
    assert "SorceryWipe" not in derived


def test_reactive_only_excludes_non_interaction_instants():
    """An instant cantrip (e.g. Consider) has `instant_speed` but no
    counterspell/removal/protection tag → not reactive_only.  Cantrips
    can be cast pro-actively to fix the next draw."""
    decklist = {"CantripA": 4}
    db = _FakeDB({
        "CantripA": _FakeCard(
            cmc=1,
            tags={"cantrip", "instant_speed"},
            oracle_text="instant. surveil 1, then draw a card.",
        ),
    })
    derived = _derive_reactive_only(decklist, db)
    assert "CantripA" not in derived


def test_reactive_only_filters_to_mainboard():
    """Cards not in the decklist are excluded."""
    decklist = {"CounterA": 4}
    db = _FakeDB({
        "CounterA": _FakeCard(
            cmc=2, tags={"counterspell", "instant_speed"},
            oracle_text="instant. counter target spell.",
        ),
        "CounterB": _FakeCard(
            cmc=2, tags={"counterspell", "instant_speed"},
            oracle_text="instant. counter target spell.",
        ),
    })
    derived = _derive_reactive_only(decklist, db)
    assert "CounterA" in derived
    assert "CounterB" not in derived


def test_reactive_only_returns_empty_when_no_decklist():
    """Without decklist plumbing → empty set, preserving JSON-only
    behaviour."""
    derived = _derive_reactive_only(None, None)
    assert derived == set()


def test_explicit_reactive_only_overrides_derived(tmp_path):
    """JSON declares an explicit reactive_only list → derived set is
    ignored.  This lets a deck author keep e.g. Engineered Explosives
    as reactive_only even though it has no flash/instant."""
    data = {
        "deck_name": "TestDerive",
        "goals": [{"goal_type": "INTERACT", "card_roles": {}}],
        "reactive_only": ["AuthorPickedY"],
        "mulligan_keys": ["k"],
    }
    decklist = {"InstantCounter": 4, "AuthorPickedY": 4}
    db = _FakeDB({
        "InstantCounter": _FakeCard(
            cmc=2, tags={"counterspell", "instant_speed"},
            oracle_text="instant. counter target spell.",
        ),
        "AuthorPickedY": _FakeCard(cmc=0, tags={"removal"},
                                    oracle_text="sorcery."),
    })
    gp = _parse_gameplan(data, decklist=decklist, db=db)
    assert gp.reactive_only == {"AuthorPickedY"}
    assert "InstantCounter" not in gp.reactive_only


def test_empty_reactive_only_triggers_derivation(tmp_path):
    """JSON sets `reactive_only: []` → derive."""
    data = {
        "deck_name": "TestDerive",
        "goals": [{"goal_type": "INTERACT", "card_roles": {}}],
        "reactive_only": [],
        "mulligan_keys": ["k"],
    }
    decklist = {"InstantCounter": 4}
    db = _FakeDB({
        "InstantCounter": _FakeCard(
            cmc=2, tags={"counterspell", "instant_speed"},
            oracle_text="instant. counter target spell.",
        ),
    })
    gp = _parse_gameplan(data, decklist=decklist, db=db)
    assert "InstantCounter" in gp.reactive_only


# ─────────────────────────────────────────────────────────────────────
# Backwards-compat: shipped gameplans still load without decklist
# ─────────────────────────────────────────────────────────────────────


def test_load_gameplan_without_decklist_still_works():
    """`load_gameplan(name)` (no decklist) preserves the existing
    JSON-only behaviour: explicit always_early / reactive_only fields
    are honored, missing ones default to empty (not crash)."""
    from decks.gameplan_loader import load_gameplan, clear_cache
    clear_cache()
    boros = load_gameplan("Boros Energy")
    assert boros is not None
    assert boros.always_early == {
        "Guide of Souls", "Ocelot Pride", "Ragavan, Nimble Pilferer",
    }, f"Boros explicit always_early should be honored, got {boros.always_early!r}"
