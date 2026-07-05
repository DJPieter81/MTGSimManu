"""Regression tests for engine-land gameplan declarations (Track G,
Pinnacle Affinity post-Saga-fidelity calibration).

Diagnostic: docs/diagnostics/2026-07-05_pinnacle_saga_gameplan.md

Rule under test — an ability-granting engine land (Urza's Saga: chapter
II grants "{2}, {T}: Create a Construct token") is worth more than a
generic land in the same manabase, and the gameplan must say so via the
sanctioned data hooks:

1. `land_priorities` must rank the engine land above generic lands so
   mulligan bottoming (ai/gameplan.py card_keep_score) keeps it when
   shipping cards back. Precedent: amulet_titan.json declares
   Urza's Saga at 3.0.
2. The deck whose primary token line runs through the granted ability
   (Pinnacle Affinity) must declare the engine land in a goal's
   `engines` role so role-derived consumers (BHI opponent model,
   discard advisor) see the keystone.
3. A manabase where the engine land is a 4-of among ~15 lands must not
   auto-mull every 4-land hand: `mulligan_max_lands` >= 4 (a "4-land"
   hand there statistically contains a threat-land).

Test names describe the mechanic (engine-land valuation in gameplan
data), not a card-specific behaviour; Urza's Saga is the data instance.
"""
import json
import os

GAMEPLANS_DIR = os.path.join(os.path.dirname(__file__), "..", "decks", "gameplans")

ENGINE_LAND = "Urza's Saga"


def _load(name):
    with open(os.path.join(GAMEPLANS_DIR, name)) as f:
        return json.load(f)


def test_engine_land_has_land_priority_in_saga_decks():
    """Every gameplan whose deck runs the ability-granting engine land
    must declare a positive land_priorities entry for it, so mulligan
    bottoming prefers it over generic lands."""
    for gameplan_file in ("pinnacle_affinity.json", "affinity.json"):
        data = _load(gameplan_file)
        priorities = data.get("land_priorities", {})
        assert priorities.get(ENGINE_LAND, 0.0) > 0.0, (
            f"{gameplan_file}: engine land '{ENGINE_LAND}' has no "
            f"positive land_priorities entry — mulligan bottoming will "
            f"ship it back like a generic land. See "
            f"docs/diagnostics/2026-07-05_pinnacle_saga_gameplan.md."
        )


def test_engine_land_priority_outranks_other_declared_lands():
    """Within each declaring gameplan, the engine land must be the
    top-priority land: its two granted activations + tutor chapter make
    it strictly better than any single-mode land in the same base."""
    for gameplan_file in ("pinnacle_affinity.json", "affinity.json"):
        data = _load(gameplan_file)
        priorities = data.get("land_priorities", {})
        saga = priorities.get(ENGINE_LAND, 0.0)
        others = [v for k, v in priorities.items() if k != ENGINE_LAND]
        assert all(saga >= v for v in others), (
            f"{gameplan_file}: engine land priority {saga} is outranked "
            f"by another land in land_priorities={priorities}."
        )


def test_granted_ability_deck_declares_engine_land_role():
    """The deck whose token line runs through the granted ability must
    list the engine land in a goal's `engines` card_roles bucket."""
    data = _load("pinnacle_affinity.json")
    engine_buckets = [
        set(goal.get("card_roles", {}).get("engines", []))
        for goal in data["goals"]
    ]
    assert any(ENGINE_LAND in bucket for bucket in engine_buckets), (
        f"pinnacle_affinity.json: '{ENGINE_LAND}' is missing from every "
        f"goal's engines role — role-derived consumers (BHI, discard "
        f"advisor) cannot see the deck's keystone."
    )


def test_threat_land_manabase_keeps_four_land_hands():
    """A manabase running the engine land as a 4-of among ~15 lands must
    allow 4-land keeps: mulligan_max_lands >= 4."""
    data = _load("pinnacle_affinity.json")
    assert data.get("mulligan_max_lands", 4) >= 4, (
        f"pinnacle_affinity.json: mulligan_max_lands="
        f"{data.get('mulligan_max_lands')} mulls every 4-land hand, but "
        f"4 of ~15 lands are the threat-land itself."
    )


def test_gameplans_still_load_through_loader():
    """The edited gameplans must round-trip through the real loader."""
    from ai.gameplan import create_goal_engine

    for deck in ("Pinnacle Affinity", "Affinity"):
        engine = create_goal_engine(deck)
        assert engine is not None and engine.gameplan is not None
        assert engine.gameplan.land_priorities.get(ENGINE_LAND, 0.0) > 0.0
