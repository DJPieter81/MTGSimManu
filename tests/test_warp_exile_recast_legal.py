"""Tests for Warp-exile recast availability in legal plays (CR 702.Warp).

Mechanic under test
-------------------
A creature cast via Warp is exiled at the beginning of the next end step.
"You may cast it from exile on a later turn." means on a later turn the card
in exile (with _warped=True) must appear as a legal play when the Warp
prerequisites are met (controller has an artifact, can pay the Warp cost).

Class size: any card with a Warp cost (33 cards in the current DB) — so this
fix must use the _warped flag + template.warp_cost, not the card's name.

Bug that this test pins: get_legal_plays only scanned hand/graveyard; warped
cards in exile were never offered as legal plays, so the engine never re-cast
them. The AI couldn't use Warp as a recurring source of tokens/bodies.
"""

from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType
from engine.game_state import GameState, Phase


def _make_card(game, template, owner=0, zone="hand"):
    card = CardInstance(
        template=template, owner=owner, controller=owner,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    return card


def _add_to_zone(game, card, player_idx, zone):
    p = game.players[player_idx]
    card.zone = zone
    if zone == "hand":
        p.hand.append(card)
    elif zone == "battlefield":
        p.battlefield.append(card)
    elif zone == "exile":
        p.exile.append(card)


def _add_mana(game, player_idx, **colors):
    pool = game.players[player_idx].mana_pool
    for color, amount in colors.items():
        pool.add(color.upper(), amount)


@pytest.fixture(scope="module")
def db():
    from engine.card_database import CardDatabase
    return CardDatabase()


@pytest.fixture
def fresh_game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


class TestWarpExileRecastAppearsInLegalPlays:
    """Warp-exiled cards must appear in get_legal_plays when prerequisites met.

    The mechanic: any creature with warp_cost that ends up in exile via the
    Warp exile trigger (card._warped is True) may be cast again. The
    get_legal_plays function must scan player.exile for such cards so the AI
    sees them as available plays.
    """

    def test_warped_exile_card_appears_in_legal_plays(self, fresh_game, db):
        """A warp-exiled creature is offered as a legal play on the next turn.

        Setup: Pinnacle Emissary in exile with _warped=True (simulates the
        state after end-of-turn exile on the previous turn). Player controls
        an artifact (satisfies the Warp has-artifact requirement) and has
        enough mana to pay the Warp cost.

        Expected: get_legal_plays includes the exile card.
        """
        game = fresh_game
        t_emissary = db.cards["Pinnacle Emissary"]
        t_artifact = db.cards["Mox Opal"]

        # Pinnacle Emissary in exile with _warped=True
        emissary = _make_card(game, t_emissary, zone="exile")
        emissary._warped = True
        _add_to_zone(game, emissary, 0, "exile")

        # Artifact on battlefield (Warp prerequisite)
        artifact = _make_card(game, t_artifact, zone="battlefield")
        _add_to_zone(game, artifact, 0, "battlefield")

        # Enough mana to pay Warp cost (cmc=1)
        _add_mana(game, 0, U=1)

        legal = game.get_legal_plays(0)
        legal_names = [c.name for c in legal]
        assert "Pinnacle Emissary" in legal_names, (
            "Warp-exiled Pinnacle Emissary must appear in get_legal_plays "
            f"when has-artifact + mana prerequisites are met. Got: {legal_names}"
        )

    def test_warped_exile_card_absent_without_artifact(self, fresh_game, db):
        """Without an artifact, the warp-exiled card must not be a legal play."""
        game = fresh_game
        t_emissary = db.cards["Pinnacle Emissary"]

        emissary = _make_card(game, t_emissary, zone="exile")
        emissary._warped = True
        _add_to_zone(game, emissary, 0, "exile")

        # No artifact on battlefield; has mana but Warp needs an artifact
        _add_mana(game, 0, U=2)

        legal = game.get_legal_plays(0)
        assert "Pinnacle Emissary" not in [c.name for c in legal], (
            "Warp requires controlling an artifact; without one the exile card "
            "must not appear in legal plays."
        )

    def test_warped_exile_card_absent_without_mana(self, fresh_game, db):
        """Without enough mana, the warp-exiled card must not be a legal play."""
        game = fresh_game
        t_emissary = db.cards["Pinnacle Emissary"]
        t_artifact = db.cards["Mox Opal"]

        emissary = _make_card(game, t_emissary, zone="exile")
        emissary._warped = True
        _add_to_zone(game, emissary, 0, "exile")

        artifact = _make_card(game, t_artifact, zone="battlefield")
        _add_to_zone(game, artifact, 0, "battlefield")

        # No mana — cannot pay Warp cost
        legal = game.get_legal_plays(0)
        assert "Pinnacle Emissary" not in [c.name for c in legal], (
            "Warp requires paying the Warp cost; without mana the exile card "
            "must not appear in legal plays."
        )

    def test_non_warped_exile_card_not_in_legal_plays(self, fresh_game, db):
        """An exile card without _warped=True must not appear as a legal play
        via the Warp path (it might be reachable via other mechanics, but not
        this one)."""
        game = fresh_game
        t_emissary = db.cards["Pinnacle Emissary"]
        t_artifact = db.cards["Mox Opal"]

        # In exile but NOT warped (e.g. exiled by opponent's Prismatic Ending)
        emissary = _make_card(game, t_emissary, zone="exile")
        # _warped NOT set
        _add_to_zone(game, emissary, 0, "exile")

        artifact = _make_card(game, t_artifact, zone="battlefield")
        _add_to_zone(game, artifact, 0, "battlefield")

        _add_mana(game, 0, U=2)

        legal = game.get_legal_plays(0)
        # May appear via other paths (e.g. if the card has flash + exile casting
        # ability), but NOT via the Warp path. The key check: if Pinnacle
        # Emissary appears, its can_cast must return False for exile without
        # _warped — we check can_cast directly.
        assert not game.can_cast(0, emissary), (
            "can_cast must return False for exile card without _warped flag"
        )
