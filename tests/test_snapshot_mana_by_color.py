"""Bundle 3 — A2: EVSnapshot must expose per-color untapped mana.

Diagnosis (Affinity-session consolidated findings, A2):
`ai/ev_evaluator.py::EVSnapshot.my_mana` is a scalar `int` — the
total count of untapped lands. It is the foundation other holdback
logic (A5) needs to ask "do I still have a U source after this
play?". Without per-color tracking we cannot reason about whether
a held instant remains castable post-tap-out.

Fix: add `my_mana_by_color: Dict[str, int]` covering W/U/B/R/G/C.
Populate in `snapshot_from_game()` by iterating untapped lands and
calling `_effective_produces_mana(player_idx, land)` (Leyline of
the Guildpact aware) — multi-colored lands contribute one to EACH
color they can produce (best-case availability for any single
color, since you tap the land for one color at a time).
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import EVSnapshot, snapshot_from_game
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


class TestSnapshotManaByColorA2:
    """snapshot_from_game must populate `my_mana_by_color` for every
    color the player's untapped lands can produce."""

    def test_field_exists_on_evsnapshot(self):
        snap = EVSnapshot()
        assert hasattr(snap, 'my_mana_by_color'), (
            "EVSnapshot must expose `my_mana_by_color: Dict[str, int]` "
            "as the foundation for color-aware holdback (A5)."
        )
        assert isinstance(snap.my_mana_by_color, dict), (
            "my_mana_by_color must be a dict, got "
            f"{type(snap.my_mana_by_color).__name__}"
        )

    def test_basics_and_dual_lands_populate_per_color(self, card_db):
        """2 Islands + 1 Steam Vents (U/R) + 1 Plains, all untapped:
            U = 3 (2 Islands + Steam Vents)
            R = 1 (Steam Vents)
            W = 1 (Plains)"""
        game = GameState(rng=random.Random(0))

        _add(game, card_db, "Island", controller=0, zone="battlefield")
        _add(game, card_db, "Island", controller=0, zone="battlefield")
        _add(game, card_db, "Steam Vents", controller=0, zone="battlefield")
        _add(game, card_db, "Plains", controller=0, zone="battlefield")

        snap = snapshot_from_game(game, 0)

        assert snap.my_mana_by_color.get("U", 0) == 3, (
            f"Expected U=3 (2 Islands + Steam Vents), "
            f"got U={snap.my_mana_by_color.get('U', 0)} "
            f"in {snap.my_mana_by_color}"
        )
        assert snap.my_mana_by_color.get("W", 0) == 1, (
            f"Expected W=1 (Plains), "
            f"got W={snap.my_mana_by_color.get('W', 0)}"
        )
        assert snap.my_mana_by_color.get("R", 0) == 1, (
            f"Expected R=1 (Steam Vents), "
            f"got R={snap.my_mana_by_color.get('R', 0)}"
        )

    def test_tapped_lands_excluded(self, card_db):
        """Only UNTAPPED lands count — tapped lands produce no mana
        right now."""
        game = GameState(rng=random.Random(0))

        l1 = _add(game, card_db, "Island", controller=0, zone="battlefield")
        l2 = _add(game, card_db, "Island", controller=0, zone="battlefield")
        l1.tapped = True  # used this turn
        # l2 still untapped

        snap = snapshot_from_game(game, 0)
        assert snap.my_mana_by_color.get("U", 0) == 1, (
            f"Tapped Island must not contribute to my_mana_by_color; "
            f"expected U=1, got U={snap.my_mana_by_color.get('U', 0)}"
        )
