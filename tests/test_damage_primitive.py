"""engine.damage.deal_damage — single source of truth for damage
routing.

# Mechanic the test names

Damage from any source must route uniformly:

- creature target: damage stacks on `damage_marked`; state-based
  actions kill it if `damage_marked >= toughness` or it has lethal /
  deathtouch annotation (deathtouch handled at SBA layer, not here).
- planeswalker target: damage is removed from `loyalty_counters`; the
  PW dies (SBA layer) when loyalty hits 0.
- player target: damage is removed from `life` and tracked on the
  source's controller for damage_dealt_this_turn book-keeping.

Centralising this routing in `engine.damage.deal_damage` lets the
target enumeration on the AI side (M10) treat creatures, planeswalkers
and face symmetrically — the same primitive backs all three.

# Class size

Every direct-damage card in Modern + every combat-damage step + every
ETB/attack-trigger that pings ("deals N damage to ..."). ~200 unique
prints from the printed pool, comfortably above the abstraction floor.

# Generic by oracle

The primitive dispatches on `CardType` of the target permanent, not
on the source spell's name. No card-name conditionals.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardType
from engine.damage import deal_damage
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller, loyalty=None):
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
    card.summoning_sick = False
    if CardType.PLANESWALKER in tmpl.card_types:
        card.loyalty_counters = (loyalty if loyalty is not None
                                  else (tmpl.loyalty or 0))
    game.players[controller].battlefield.append(card)
    return card


class TestDealDamagePrimitive:
    """`engine.damage.deal_damage(game, target, amount, source_controller)`
    is the single damage-routing primitive."""

    def test_damage_to_creature_stacks_on_damage_marked(self, card_db):
        """A non-lethal damage hit accumulates on `damage_marked`."""
        game = GameState(rng=random.Random(0))
        # Ornithopter is 0/2 — 1 damage is non-lethal and the
        # damage_marked counter must persist past SBA.
        c = _add_to_battlefield(game, card_db, "Ornithopter",
                                  controller=1)
        assert c.damage_marked == 0
        deal_damage(game, c, 1, source_controller=0)
        assert c.damage_marked == 1, (
            f"1 damage on a 0/2 should leave damage_marked=1, "
            f"got {c.damage_marked}")
        assert c.zone == "battlefield", (
            f"0/2 with 1 damage survives SBA; zone={c.zone}")

    def test_damage_to_creature_kills_when_lethal(self, card_db):
        """A creature dies (leaves battlefield) when damage >= toughness."""
        game = GameState(rng=random.Random(0))
        c = _add_to_battlefield(game, card_db, "Memnite", controller=1)
        deal_damage(game, c, 1, source_controller=0)
        # SBA should resolve via the damage call itself (or the
        # creature is_dead check)
        assert c.zone != "battlefield" or c.is_dead, (
            f"Memnite (1/1) should die from 1 damage. "
            f"zone={c.zone}, damage_marked={c.damage_marked}")

    def test_damage_to_planeswalker_reduces_loyalty(self, card_db):
        """A planeswalker target loses loyalty counters equal to damage."""
        game = GameState(rng=random.Random(0))
        pw = _add_to_battlefield(game, card_db, "Teferi, Time Raveler",
                                  controller=1, loyalty=4)
        deal_damage(game, pw, 2, source_controller=0)
        assert pw.loyalty_counters == 2, (
            f"2 damage to Teferi (4 loyalty) should leave 2 loyalty. "
            f"Got loyalty_counters={pw.loyalty_counters}.")

    def test_damage_to_planeswalker_kills_when_loyalty_zero(self, card_db):
        """A planeswalker dies when its loyalty reaches 0."""
        game = GameState(rng=random.Random(0))
        pw = _add_to_battlefield(game, card_db, "Teferi, Time Raveler",
                                  controller=1, loyalty=3)
        deal_damage(game, pw, 3, source_controller=0)
        assert pw.loyalty_counters == 0
        # SBA should pull it off the battlefield
        assert pw.zone != "battlefield" or pw not in game.players[1].battlefield, (
            f"Teferi at 0 loyalty should die. zone={pw.zone}")

    def test_damage_to_player_reduces_life(self, card_db):
        """Damage to a player target (face) reduces life."""
        game = GameState(rng=random.Random(0))
        game.players[1].life = 20
        deal_damage(game, game.players[1], 3, source_controller=0)
        assert game.players[1].life == 17, (
            f"3 damage to player should leave life 17. "
            f"Got life={game.players[1].life}.")
