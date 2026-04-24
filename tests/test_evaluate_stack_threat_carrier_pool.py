"""Bug R1 — Carrier-pool synergy missing from threat scoring.

`ai/response.py::evaluate_stack_threat` treats an incoming creature
spell as a stand-alone body. When the opponent already has one or more
Equipment permanents on-board (e.g. Cranial Plating, Nettlecyst), the
marginal impact of adding another carrier to the pool is higher than
the creature's printed P/T — each new carrier opens an additional
attach surface, so the equipment's pump lands on fresh damage every
turn rather than rebinding to the same body.

Fix plan:
  1. When scoring a creature spell, sweep the opponent's battlefield
     for equipment (oracle contains 'equip' and grants +P/+T) whose
     bonus scales with the number of potential carriers.  Add a term
     proportional to `pump_bonus / (current_carrier_count + 1)` to
     express the marginal synergy.
  2. For X-cost / 'for each' creatures (Walking Ballista), derive an
     expected X from `snap.opp_mana` and scale the threat accordingly.

Both extensions are oracle-driven — no hardcoded card names.
"""
from __future__ import annotations

import random

import pytest

from ai.response import ResponseDecider
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _attach_equipment(equipment, creature):
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")


def _put_on_stack(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="stack",
    )
    card._game_state = game
    item = StackItem(
        item_type=StackItemType.SPELL,
        source=card,
        controller=controller,
        targets=[],
    )
    game.stack.items.append(item)
    return item


def _build_opp_plating_board(card_db, *, n_platings: int):
    """Opp controls 2 attackers + n_platings attached + filler artifacts.

    Total artifact count = 2 attackers + n_platings + 4 filler = 6 + n_platings.
    """
    game = GameState(rng=random.Random(0))
    ornithopter = _add_to_battlefield(game, card_db, "Ornithopter", controller=1)
    frogmite = _add_to_battlefield(game, card_db, "Frogmite", controller=1)
    for _ in range(n_platings):
        plating = _add_to_battlefield(game, card_db, "Cranial Plating", controller=1)
        # Attach alternating to ornithopter / frogmite
        if _ % 2 == 0:
            _attach_equipment(plating, ornithopter)
        else:
            _attach_equipment(plating, frogmite)
    # Four filler artifacts so the plating bonus is substantial.
    for _ in range(4):
        _add_to_battlefield(game, card_db, "Memnite", controller=1)
    return game


class TestSojournersCompanionCarrierPoolBonus:
    """An incoming 4/4 artifact creature on a plating-heavy board
    is worth MORE than a vanilla 4/4 — it becomes a new carrier for
    the rebinding equipment."""

    def test_sojourners_threat_higher_with_plating_than_without(self, card_db):
        """Setup:
          opp has 2 attackers (Ornithopter, Frogmite) + 2 Cranial Plating
          (attached) + 4 other artifacts (Memnite × 4) = 8 artifacts total.
          Incoming spell on stack: Sojourner's Companion (4/4 artifact).

        Counterfactual:
          Same board but WITHOUT the 2 Platings (they are replaced by
          2 more Memnites so the artifact count stays equal and only the
          plating presence changes).

        Assertion:
          threat_with_platings >= threat_without_platings + carrier_term.
          Concretely, at least 2 points higher — the plating pump re-
          binds to the new carrier on the owner's next combat.
        """
        # With 2 platings
        game_pump = _build_opp_plating_board(card_db, n_platings=2)
        item_pump = _put_on_stack(
            game_pump, card_db, "Sojourner's Companion", controller=1)
        decider = ResponseDecider(player_idx=0)
        threat_with = decider.evaluate_stack_threat(game_pump, item_pump)

        # Without platings — replace the 2 Platings with 2 more Memnites
        # to keep the artifact count the same.
        game_flat = GameState(rng=random.Random(0))
        _add_to_battlefield(game_flat, card_db, "Ornithopter", controller=1)
        _add_to_battlefield(game_flat, card_db, "Frogmite", controller=1)
        for _ in range(6):  # 4 original memnites + 2 replacing the platings
            _add_to_battlefield(game_flat, card_db, "Memnite", controller=1)
        item_flat = _put_on_stack(
            game_flat, card_db, "Sojourner's Companion", controller=1)
        threat_without = decider.evaluate_stack_threat(game_flat, item_flat)

        delta = threat_with - threat_without
        assert delta >= 2.0, (
            f"Sojourner's Companion threat with 2 Cranial Platings on opp "
            f"board ({threat_with:.2f}) must exceed the same-size artifact "
            f"board without platings ({threat_without:.2f}) by at least "
            f"2.0 points — adding a carrier to a plating-rich pool "
            f"multiplies the pump across more attackers.  Got delta "
            f"{delta:.2f}."
        )


class TestNonCreatureDoesNotGetCarrierBonus:
    """Regression: the carrier-pool term fires only for creature spells.
    A non-creature spell cannot carry equipment, so adding it to the
    stack must not pick up the carrier synergy bonus."""

    def test_instant_spell_not_boosted_by_plating_pool(self, card_db):
        """Same plating-rich opp board, but the incoming spell is
        Lightning Bolt (instant, not a creature).  Threat should equal
        (within tolerance) the threat on a flat board — no carrier
        bonus because Bolt never joins the carrier pool."""
        game_pump = _build_opp_plating_board(card_db, n_platings=2)
        item_pump = _put_on_stack(
            game_pump, card_db, "Lightning Bolt", controller=1)
        decider = ResponseDecider(player_idx=0)
        threat_with = decider.evaluate_stack_threat(game_pump, item_pump)

        game_flat = GameState(rng=random.Random(0))
        _add_to_battlefield(game_flat, card_db, "Ornithopter", controller=1)
        _add_to_battlefield(game_flat, card_db, "Frogmite", controller=1)
        for _ in range(6):
            _add_to_battlefield(game_flat, card_db, "Memnite", controller=1)
        item_flat = _put_on_stack(
            game_flat, card_db, "Lightning Bolt", controller=1)
        threat_without = decider.evaluate_stack_threat(game_flat, item_flat)

        delta = abs(threat_with - threat_without)
        assert delta < 0.5, (
            f"Lightning Bolt is not a creature — it can never become a "
            f"plating carrier.  Threat with platings ({threat_with:.2f}) "
            f"must not differ from threat without ({threat_without:.2f}) "
            f"by more than noise (< 0.5).  Got delta {delta:.2f}."
        )


class TestWalkingBallistaXScalesWithOppMana:
    """Walking Ballista enters with X +1/+1 counters. Its oracle contains
    `{X}` — threat must scale with an expected X derived from opp's
    available mana, not score as a vanilla 0/0."""

    def test_walking_ballista_threat_scales_with_opp_mana(self, card_db):
        """Opp has ~5 mana available; incoming Walking Ballista should
        project as a ~5/5-ish threat.  Compare against the same incoming
        on a board where opp has ~1 mana — the low-mana case must have
        a strictly smaller threat, because expected X is smaller."""
        # Build a game where opp has ample lands → large opp_mana.
        game_big = GameState(rng=random.Random(0))
        for _ in range(5):
            _add_to_battlefield(game_big, card_db, "Island", controller=1)
        item_big = _put_on_stack(
            game_big, card_db, "Walking Ballista", controller=1)
        decider = ResponseDecider(player_idx=0)
        threat_big = decider.evaluate_stack_threat(game_big, item_big)

        # Low-mana counterfactual: opp has 1 land.
        game_small = GameState(rng=random.Random(0))
        _add_to_battlefield(game_small, card_db, "Island", controller=1)
        item_small = _put_on_stack(
            game_small, card_db, "Walking Ballista", controller=1)
        threat_small = decider.evaluate_stack_threat(game_small, item_small)

        assert threat_big > threat_small + 1.0, (
            f"Walking Ballista threat must scale with expected X (opp "
            f"mana).  With 5 lands threat={threat_big:.2f}; with 1 land "
            f"threat={threat_small:.2f}.  The high-mana case should be "
            f"strictly greater by at least 1 clock-unit."
        )
