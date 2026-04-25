"""Living End graveyard mutation race.

Bug: ``_resolve_living_end`` snapshots the graveyard, then for each
creature: removes it from the GY, puts it on the battlefield, fires
its ETB triggers. If a returned creature's ETB mutates the SAME
graveyard (e.g., Endurance's ``target player.graveyard.clear()``),
subsequent iterations fail with ``ValueError: list.remove(x): x not
in list`` because the snapshot references creatures the ETB already
removed.

CR 614 (simultaneous resolution) says all returned creatures enter
"at the same time"; the engine should treat the leave-graveyard step
as bulk before any ETB fires, so an ETB that touches the GY sees a
settled state.

Discovered during Phase 2b matrix run (cascade decks casting Living
End more aggressively triggered the race in production).
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _put_in_graveyard(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="graveyard",
    )
    card._game_state = game
    game.players[controller].graveyard.append(card)
    return card


class TestLivingEndGYMutationRace:
    """Living End + Endurance in same GY must not raise ValueError."""

    def test_endurance_etb_does_not_break_living_end_iteration(self, card_db):
        """Endurance reanimated alongside other creatures, opponent GY
        empty so Endurance's ETB targets the controller's own GY,
        clearing it mid-iteration. Without the fix this raises
        ``ValueError: list.remove(x): x not in list``."""
        game = GameState(rng=random.Random(0))

        # P1 graveyard: Endurance FIRST (iteration order = insertion
        # order for plain lists), then two other creatures that will
        # be the victims of the race when Endurance's ETB clears the
        # GY before they are dequeued.
        endurance = _put_in_graveyard(game, card_db, "Endurance", 0)
        riverwinder = _put_in_graveyard(game, card_db, "Striped Riverwinder", 0)
        architects = _put_in_graveyard(game, card_db, "Architects of Will", 0)

        # P2 graveyard: empty. Endurance's target-selection logic falls
        # back to ``controller`` when opponent.graveyard is empty,
        # which is what triggers the bug.
        assert game.players[1].graveyard == []

        # Should NOT raise.
        game._resolve_living_end(0)

        # All three creatures must end up on the battlefield (CR 614:
        # they all return simultaneously, so the GY-mutating ETB cannot
        # retroactively un-return its companions).
        bf_names = [c.name for c in game.players[0].battlefield]
        assert "Endurance" in bf_names, (
            f"Endurance missing from battlefield: {bf_names}")
        assert "Striped Riverwinder" in bf_names, (
            f"Striped Riverwinder lost to GY mutation race: {bf_names}")
        assert "Architects of Will" in bf_names, (
            f"Architects of Will lost to GY mutation race: {bf_names}")

        # Graveyard must be empty (all three returned, none left
        # stranded by the race).
        assert game.players[0].graveyard == [], (
            f"P1 graveyard should be empty after Living End, got "
            f"{[c.name for c in game.players[0].graveyard]}")

    def test_endurance_last_in_gy_still_safe(self, card_db):
        """Endurance LAST in iteration order: bug does not fire pre-fix
        (its ETB runs after all creatures are already returned), so
        this case must remain green both pre- and post-fix. Guards
        against a fix that breaks the easy ordering."""
        game = GameState(rng=random.Random(0))

        riverwinder = _put_in_graveyard(game, card_db, "Striped Riverwinder", 0)
        architects = _put_in_graveyard(game, card_db, "Architects of Will", 0)
        endurance = _put_in_graveyard(game, card_db, "Endurance", 0)

        game._resolve_living_end(0)

        bf_names = [c.name for c in game.players[0].battlefield]
        assert {"Endurance", "Striped Riverwinder",
                "Architects of Will"}.issubset(set(bf_names)), bf_names
        assert game.players[0].graveyard == []
