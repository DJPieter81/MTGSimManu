"""LE-E1: Grafdigger's Cage continuous-effect gate.

Oracle text:
    "Creature cards in graveyards and libraries can't enter the
     battlefield. Players can't cast spells from graveyards or
     libraries."

The engine must honour BOTH clauses as continuous effects while Cage is
on any player's battlefield:

1. ``_resolve_living_end`` must NOT return graveyard creatures to the
   battlefield; they stay in the graveyard.
2. ``_resolve_living_end`` must STILL exile the creatures that are on
   the battlefield (the first clause of Cage only gates enter-from-GY;
   it does not block leaves-the-battlefield actions).
3. ``can_cast`` must reject casting a spell whose zone is ``graveyard``
   or ``library`` (flashback/escape/etc.) while Cage is active.

Diagnostic: docs/diagnostics/2026-04-24_living_end_consolidated_findings.md
(LE-E1).

The gate is oracle-driven — it matches the continuous-effect oracle
pattern "creature cards in graveyards" + "can't enter the battlefield",
so any functional reprint (e.g., a future hate card with identical
text) also gets gated without code changes.
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


def _put_on_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


class TestLivingEndGatedByGrafdiggersCage:
    """Living End on a board containing Cage must not reanimate."""

    def test_cage_blocks_living_end_graveyard_return(self, card_db):
        """With Cage in play on the opponent, Living End exiles the
        battlefield creatures but leaves the graveyards untouched."""
        game = GameState(rng=random.Random(0))

        # P2 controls Grafdigger's Cage
        _put_on_battlefield(game, card_db, "Grafdigger's Cage", 1)

        # P1 puts 3 creatures in its graveyard to target with LE
        gy_creatures = [
            _put_in_graveyard(game, card_db, "Striped Riverwinder", 0),
            _put_in_graveyard(game, card_db, "Architects of Will", 0),
            _put_in_graveyard(game, card_db, "Curator of Mysteries", 0),
        ]

        # Resolve Living End controlled by P1
        game._resolve_living_end(0)

        # Creatures must still be in GY (Cage blocks the ETB from GY)
        p1_gy_names = [c.name for c in game.players[0].graveyard]
        for creature in gy_creatures:
            assert creature in game.players[0].graveyard, (
                f"{creature.name} entered the battlefield despite "
                f"Grafdigger's Cage. GY: {p1_gy_names}, "
                f"BF: {[c.name for c in game.players[0].battlefield]}"
            )
            assert creature not in game.players[0].battlefield

        # Log must cite Grafdigger's Cage (rule cite)
        cage_log = [l for l in game.log if "Grafdigger" in l]
        assert cage_log, (
            f"No Grafdigger's Cage rule cite in log. Log tail: "
            f"{game.log[-10:]}"
        )

    def test_no_cage_allows_living_end(self, card_db):
        """Regression: Living End resolves normally when no Cage is in
        play — creatures come back to the battlefield."""
        game = GameState(rng=random.Random(0))

        gy_creatures = [
            _put_in_graveyard(game, card_db, "Striped Riverwinder", 0),
            _put_in_graveyard(game, card_db, "Architects of Will", 0),
            _put_in_graveyard(game, card_db, "Curator of Mysteries", 0),
        ]

        game._resolve_living_end(0)

        # All creatures should be on battlefield, GY now empty
        bf_names = [c.name for c in game.players[0].battlefield]
        for creature in gy_creatures:
            assert creature in game.players[0].battlefield, (
                f"{creature.name} did NOT return to battlefield "
                f"without Cage. GY: "
                f"{[c.name for c in game.players[0].graveyard]}, "
                f"BF: {bf_names}"
            )
            assert creature not in game.players[0].graveyard

    def test_cage_blocks_own_living_end_too(self, card_db):
        """Cage is symmetric — if the Living End controller owns Cage,
        their own graveyard stays on lockdown as well."""
        game = GameState(rng=random.Random(0))

        # P1 (the LE controller) owns the Cage
        _put_on_battlefield(game, card_db, "Grafdigger's Cage", 0)

        gy_creatures = [
            _put_in_graveyard(game, card_db, "Striped Riverwinder", 0),
        ]

        game._resolve_living_end(0)

        for creature in gy_creatures:
            assert creature in game.players[0].graveyard, (
                f"{creature.name} entered the battlefield despite "
                f"controller's own Grafdigger's Cage."
            )


class TestCastFromGraveyardGatedByGrafdiggersCage:
    """Cage's second clause: players can't cast spells from graveyards
    or libraries. can_cast must reject such casts while Cage is in
    play."""

    def test_cant_cast_from_graveyard_with_cage(self, card_db):
        """Any card with zone=='graveyard' must fail can_cast when Cage
        is in play. We exercise the flashback path (has_flashback on the
        instance, as granted by Past in Flames in gameplay) since no
        creature with intrinsic flashback exists in the current DB; the
        Cage gate is the same for any graveyard-cast route."""
        game = GameState(rng=random.Random(0))

        # P2 controls Cage
        _put_on_battlefield(game, card_db, "Grafdigger's Cage", 1)

        card = _put_in_graveyard(game, card_db, "Faithful Mending", 0)
        card.has_flashback = True

        # Faithful Mending flashback cost = 1WU. Mix of Islands and
        # Plains covers the colour requirement with plenty of generic.
        for land_name in ("Island", "Island", "Island",
                          "Plains", "Plains", "Plains"):
            land_tmpl = card_db.get_card(land_name)
            land = CardInstance(
                template=land_tmpl,
                owner=0,
                controller=0,
                instance_id=game.next_instance_id(),
                zone="battlefield",
            )
            land._game_state = game
            land.enter_battlefield()
            game.players[0].battlefield.append(land)

        # can_cast from graveyard with Cage → False
        assert game.can_cast(0, card) is False, (
            f"can_cast returned True for {card.name} from GY with "
            f"Grafdigger's Cage in play on opponent."
        )

    def test_can_cast_from_graveyard_without_cage(self, card_db):
        """Regression: same setup without Cage → can_cast returns True
        (flashback spell is legal)."""
        game = GameState(rng=random.Random(0))

        card = _put_in_graveyard(game, card_db, "Faithful Mending", 0)
        card.has_flashback = True

        for land_name in ("Island", "Island", "Island",
                          "Plains", "Plains", "Plains"):
            land_tmpl = card_db.get_card(land_name)
            land = CardInstance(
                template=land_tmpl,
                owner=0,
                controller=0,
                instance_id=game.next_instance_id(),
                zone="battlefield",
            )
            land._game_state = game
            land.enter_battlefield()
            game.players[0].battlefield.append(land)

        assert game.can_cast(0, card) is True, (
            f"can_cast returned False for {card.name} from GY without "
            f"Cage — flashback should be legal."
        )
