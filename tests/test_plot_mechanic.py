"""Plot — deferred-cast-from-exile (CR 702.170).

Rule: a card with "Plot {cost}" may, at sorcery speed, have its plot cost paid
and be exiled from hand; it is then cast for FREE as a sorcery on a LATER turn
(never the same turn). A generic mechanic in the warp/suspend family — parsed
once into ``CardTemplate.plot_cost`` and dispatched off the typed field, no card
names. 31 DB cards have plot; Slickshot Show-Off (plot {1}{R}) is the fixture
carrier for the real-DB integration, and the mechanic under test is "pay plot
cost, exile from hand, cast free on a later turn", not any specific card.
"""
import random

import pytest

from engine.oracle_parser import parse_plot_cost
from engine.game_state import GameState, Phase


class TestParsePlotCost:
    def test_plot_cost_parsed(self):
        cost = parse_plot_cost(
            "Plot {1}{R} (You may pay {1}{R} and exile this card from your hand. "
            "Cast it as a sorcery on a later turn without paying its mana cost.)")
        assert cost is not None
        assert cost.cmc == 2
        assert cost.red == 1
        assert cost.generic == 1

    def test_no_plot_returns_none(self):
        assert parse_plot_cost("Flying, haste\nWhenever you cast a spell, ...") is None


def _make(game, template, owner=0, zone="hand"):
    from engine.cards import CardInstance
    c = CardInstance(template=template, owner=owner, controller=owner,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    return c


@pytest.fixture(scope="module")
def db():
    from engine.card_database import CardDatabase
    return CardDatabase()


@pytest.fixture
def game():
    g = GameState(rng=random.Random(0))
    g.current_phase = Phase.MAIN1
    g.active_player = 0
    g.turn_number = 3
    return g


def _plot_setup(game, db):
    """Slickshot Show-Off in hand + two untapped Mountains for its {1}{R} plot."""
    spell = _make(game, db.get_card("Slickshot Show-Off"))
    game.players[0].hand.append(spell)
    mountains = []
    for _ in range(2):
        m = _make(game, db.get_card("Mountain"), zone="battlefield")
        m.tapped = False
        game.players[0].battlefield.append(m)
        mountains.append(m)
    return spell, mountains


def test_plot_exiles_card_from_hand_and_pays_its_cost(game, db):
    spell, mountains = _plot_setup(game, db)
    assert game.can_plot(0, spell)
    assert game.plot_card(0, spell) is True
    assert spell not in game.players[0].hand
    assert spell in game.players[0].exile
    assert spell.zone == "exile"
    assert getattr(spell, "_plotted", False) is True
    assert spell._plotted_turn == game.turn_number
    # The plot cost was paid — both Mountains are now tapped.
    assert all(m.tapped for m in mountains), "plot cost {1}{R} must tap both lands"


def test_plotted_card_is_not_castable_the_same_turn(game, db):
    spell, _ = _plot_setup(game, db)
    assert game.plot_card(0, spell) is True
    assert game.can_cast_plotted(0, spell) is False, (
        "a card plotted this turn cannot be cast until a LATER turn (CR 702.170)")


def test_plotted_card_is_cast_free_on_a_later_turn(game, db):
    spell, mountains = _plot_setup(game, db)
    assert game.plot_card(0, spell) is True

    # A later turn: untap lands and advance the turn counter.
    for m in mountains:
        m.tapped = False
    game.turn_number += 1

    assert game.can_cast_plotted(0, spell) is True
    assert game.cast_plotted(0, spell) is True
    # It left exile and the plotted flag is cleared.
    assert spell not in game.players[0].exile
    assert getattr(spell, "_plotted", False) is False
    # The cast was FREE — no mana was spent, so the lands remain untapped.
    assert all(not m.tapped for m in mountains), "plotted cast must be free"
