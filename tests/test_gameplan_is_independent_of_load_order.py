"""A deck's gameplan is the same whichever caller loads it first.

`decks.gameplan_loader.load_gameplan` caches by deck name, and a plan
loaded with the decklist derives `always_early` / `reactive_only` when
the JSON omits them. A caller that loaded without the decklist cached a
JSON-only plan, so the plan a process held — and the games it played —
depended on which code path ran first (worker scheduling in a sharded
matrix). Every caller now goes through the one path that supplies the
decklist.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai.gameplan import get_gameplan
from decks import gameplan_loader

GAMEPLANS = Path(__file__).resolve().parents[1] / "decks" / "gameplans"


def _decks_with_derived_fields():
    out = []
    for f in sorted(GAMEPLANS.glob("*.json")):
        d = json.loads(f.read_text())
        if d.get("deck_name") and ("always_early" not in d or "reactive_only" not in d):
            out.append(d["deck_name"])
    return out


@pytest.mark.parametrize("deck", _decks_with_derived_fields())
def test_a_snapshot_first_load_yields_the_same_plan_as_the_goal_engine_load(deck, monkeypatch):
    import random
    from ai.ev_evaluator import snapshot_from_game
    from engine.game_state import GameState
    monkeypatch.setattr(gameplan_loader, "_cache", {})
    reference = get_gameplan(deck)
    monkeypatch.setattr(gameplan_loader, "_cache", {})
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = deck
    snapshot_from_game(game, 0)          # the snapshot path loads first
    after = get_gameplan(deck)
    assert after.always_early == reference.always_early
    assert after.reactive_only == reference.reactive_only


@pytest.mark.parametrize("deck", _decks_with_derived_fields())
def test_a_bare_bulk_load_never_changes_the_plan_a_game_reads(deck, monkeypatch):
    monkeypatch.setattr(gameplan_loader, "_cache", {})
    reference = get_gameplan(deck)
    monkeypatch.setattr(gameplan_loader, "_cache", {})
    gameplan_loader.load_all_gameplans()        # no decklists: JSON-only plans
    after = get_gameplan(deck)
    assert after.always_early == reference.always_early
    assert after.reactive_only == reference.reactive_only
