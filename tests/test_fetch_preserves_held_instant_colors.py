"""Bundle 3 — A5: fetch target choice must preserve the colors of
held instants/flash so they remain castable on the opponent's turn.

Diagnosis (Affinity-session consolidated findings, A5):
`ai/mana_planner.py:ManaNeeds` (lines 33-57) tracks
`needed_colors` / `missing_colors` / `payoff_missing_colors` but
NOT the colors of instants the player holds. Without this signal,
`choose_fetch_target` can crack a fetch into Sacred Foundry (R/W)
when the player holds a Counterspell (U), removing the U source
the held interaction needs.

Fix: add `held_instant_colors: Set[str]` to ManaNeeds (populated
in `analyze_mana_needs`) and bias fetch scoring toward sources
that PRESERVE those colors. Engine `tap_lands_for_mana` is also
extended to honour an optional `held_instant_colors` preference
(passed in by the AI; engine remains neutral when not supplied).

Regression anchor: when no held instants are present, the fetch
choice must remain unchanged — no blanket color preference.
"""
from __future__ import annotations

import random

import pytest

from ai.mana_planner import (
    ManaNeeds, analyze_mana_needs, choose_fetch_target,
)
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    if zone == "library":
        game.players[controller].library.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


class TestManaNeedsHeldInstantColorsField:
    """Foundation — ManaNeeds must expose `held_instant_colors`."""

    def test_field_exists(self):
        needs = ManaNeeds()
        assert hasattr(needs, 'held_instant_colors'), (
            "ManaNeeds must expose `held_instant_colors: Set[str]` "
            "so fetch and tap-order decisions can preserve held "
            "interaction's colors."
        )

    def test_populated_from_held_counterspell(self, card_db):
        """When the player holds a UU Counterspell, analyze_mana_needs
        must record U in held_instant_colors."""
        game = GameState(rng=random.Random(0))
        _add(game, card_db, "Island", controller=0, zone="battlefield")
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

        needs = analyze_mana_needs(game, 0)
        assert 'U' in needs.held_instant_colors, (
            f"Counterspell {{UU}} in hand → held_instant_colors must "
            f"contain 'U', got {needs.held_instant_colors}"
        )


class TestChooseFetchPreservesHeldColorsA5:
    """choose_fetch_target must prefer the dual that preserves the
    color of a held instant when otherwise indifferent."""

    def test_fetch_grabs_hallowed_fountain_over_sacred_foundry(
            self, card_db):
        """Player holds Counterspell (U). The fetch can grab either:
            - Hallowed Fountain (W/U) — preserves U for the counter
            - Sacred Foundry (R/W)    — kills U availability
        Fix must pick Hallowed Fountain."""
        game = GameState(rng=random.Random(0))

        # Existing battlefield: 1 Plains so that W is already covered
        # (so the fetch decision isn't dominated by needing a basic
        # color the deck currently lacks).
        _add(game, card_db, "Plains", controller=0, zone="battlefield")
        # Held instant: Counterspell ({UU}) — flags U as a held color.
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

        # Library candidates: both shocks fetchable by an Arid Mesa-
        # style Plains/Mountain fetch (or any fetch that can find both).
        # Test the helper directly with explicit fetch_colors so we
        # don't depend on a particular fetch land template.
        hf = _add(game, card_db, "Hallowed Fountain", controller=0,
                  zone="library")
        _add(game, card_db, "Sacred Foundry", controller=0,
             zone="library")

        needs = analyze_mana_needs(game, 0)

        # Fetch can find any of W/U/R basics-or-shocks
        target = choose_fetch_target(
            game.players[0].library,
            fetch_colors=['W', 'U', 'R'],
            needs=needs,
        )
        assert target is not None, "choose_fetch_target returned None"
        assert target.template.name == "Hallowed Fountain", (
            f"Expected Hallowed Fountain (preserves held U for the "
            f"Counterspell in hand), got {target.template.name}. "
            f"choose_fetch_target must bias toward sources that "
            f"preserve held_instant_colors."
        )

    def test_no_held_instants_no_color_preference_change(
            self, card_db):
        """Regression anchor — without held instants, the fetch
        choice is driven by needed_colors only; we don't blanket-
        prefer one shock over another."""
        game = GameState(rng=random.Random(0))
        _add(game, card_db, "Plains", controller=0, zone="battlefield")
        # No held instants
        # Library: only Sacred Foundry → must be picked, no error.
        sf = _add(game, card_db, "Sacred Foundry", controller=0,
                  zone="library")

        needs = analyze_mana_needs(game, 0)
        # held_instant_colors must be empty
        assert not needs.held_instant_colors, (
            f"No held instants → held_instant_colors should be empty, "
            f"got {needs.held_instant_colors}"
        )

        target = choose_fetch_target(
            game.players[0].library,
            fetch_colors=['W', 'R'],
            needs=needs,
        )
        assert target is sf, (
            f"With no held interaction the fetch must still pick the "
            f"sole available target (Sacred Foundry); got "
            f"{target.template.name if target else 'None'}."
        )
