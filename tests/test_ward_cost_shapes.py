"""Ward's cost is whatever follows the keyword, wherever the keyword is
printed in the permanent's own keyword list (CR 702.21a).

"Whenever this permanent becomes the target of a spell or ability an
opponent controls, counter it unless that player pays [cost]." The cost
can be mana ("Ward {2}"), life ("Ward—Pay 7 life") or both
("Ward—{2}, Pay 2 life"); a player may pay life only if their life total
is at least the payment (CR 119.4).

Rules pinned:
* ward printed after another keyword ("Flying, ward {2}") is the
  permanent's own ward — the same as a ward line of its own;
* a life part is parsed into `ward_life_cost`, a mana part into
  `ward_cost`; a combined cost sets both, and both are owed;
* cost shapes the engine cannot pay (discard, sacrifice, collect
  evidence) are refused — neither field is set — never half-applied;
* ward granted to ANOTHER object ("Equipped creature ... has ward {1}",
  "Creatures you control have ward {1}") is not the card's own ward;
* an opponent's spell targeting a life-ward permanent is countered
  unless its controller pays the life, which they can only do with at
  least that much life; a combined cost needs both the mana and the
  life.
Card names are fixture carriers only.
"""
from __future__ import annotations

import random

import pytest

from engine.game_state import GameState
from engine.oracle_parser import parse_ward_cost, parse_ward_life_cost


@pytest.mark.parametrize("oracle, mana, life", [
    ("Flying, ward {2}", 2, 0),
    ("Vigilance, ward {3}\nWhenever this creature attacks, draw a card.", 3, 0),
    ("Ward {4}", 4, 0),
    ("Ward—Pay 7 life.", 0, 7),
    ("Reach, first strike\nWard—Pay 3 life.", 0, 3),
    ("Ward—{2}, Pay 2 life.", 2, 2),
])
def test_ward_is_read_from_the_permanents_own_keyword_list(oracle, mana, life):
    assert (parse_ward_cost(oracle), parse_ward_life_cost(oracle)) == (mana, life)


@pytest.mark.parametrize("oracle", [
    "Ward—Discard a card.",
    "Ward—Sacrifice three permanents.",
    "Ward—Collect evidence 4.",
])
def test_a_ward_cost_the_engine_cannot_pay_is_refused_not_half_applied(oracle):
    assert (parse_ward_cost(oracle), parse_ward_life_cost(oracle)) == (0, 0)


@pytest.mark.parametrize("oracle", [
    "Equipped creature gets +1/+0 and has haste and ward {1}.",
    "Creatures you control have ward {1}.",
    "Other creatures you control have hexproof, ward {2}.",
    "{5}{U}: Until end of turn, this land becomes a 7/7 creature with ward {3}.",
])
def test_ward_granted_to_another_object_is_not_the_cards_own(oracle):
    assert (parse_ward_cost(oracle), parse_ward_life_cost(oracle)) == (0, 0)


def test_real_templates_carry_each_ward_shape(card_db):
    skyturtle = card_db.get_card("Colossal Skyturtle")      # "Flying, ward {2}"
    sire = card_db.get_card("Sire of Seven Deaths")         # "Ward—Pay 7 life."
    gisa = card_db.get_card("Gisa, the Hellraiser")         # "Ward—{2}, Pay 2 life."
    assert (skyturtle.ward_cost, skyturtle.ward_life_cost) == (2, 0)
    assert (sire.ward_cost, sire.ward_life_cost) == (0, 7)
    assert (gisa.ward_cost, gisa.ward_life_cost) == (2, 2)


# ─── resolution ──────────────────────────────────────────────────────

from tests.test_ward_framework import (  # noqa: E402  (shared fixtures)
    _AlwaysPayCallbacks, _AssertNotOfferedCallbacks, _land, _push_ward_scenario,
)


def _ward(warded, mana=0, life=0):
    import copy
    warded.template = copy.copy(warded.template)
    warded.template.ward_cost = mana
    warded.template.ward_life_cost = life
    warded.template.oracle_text = "Ward—Pay %d life." % life if not mana else \
        "Ward—{%d}, Pay %d life." % (mana, life)


def test_a_life_ward_counters_unless_the_caster_pays_the_life():
    game = GameState(rng=random.Random(0), callbacks=_AlwaysPayCallbacks())
    warded, removal = _push_ward_scenario(game, ward_cost=0)
    _ward(warded, life=3)
    life_before = game.players[1].life
    game.resolve_stack()
    assert warded not in game.players[0].battlefield, "the caster paid: it resolves"
    assert game.players[1].life == life_before - 3


def test_a_life_ward_counters_when_the_caster_has_too_little_life():
    game = GameState(rng=random.Random(0), callbacks=_AssertNotOfferedCallbacks())
    warded, removal = _push_ward_scenario(game, ward_cost=0)
    _ward(warded, life=7)
    game.players[1].life = 6                       # CR 119.4: cannot pay 7
    game.resolve_stack()
    assert warded in game.players[0].battlefield
    assert removal.zone == "graveyard"
    assert game.players[1].life == 6


def test_a_combined_ward_needs_both_the_mana_and_the_life():
    game = GameState(rng=random.Random(0), callbacks=_AlwaysPayCallbacks())
    _land(game, controller=1, n=2)
    warded, removal = _push_ward_scenario(game, ward_cost=0)
    _ward(warded, mana=2, life=2)
    life_before = game.players[1].life
    game.resolve_stack()
    assert warded not in game.players[0].battlefield
    assert game.players[1].life == life_before - 2
    assert all(l.tapped for l in game.players[1].battlefield
               if l.template.is_land), "the mana part was paid too"


def test_a_combined_ward_counters_without_the_mana_even_with_the_life():
    game = GameState(rng=random.Random(0), callbacks=_AssertNotOfferedCallbacks())
    warded, removal = _push_ward_scenario(game, ward_cost=0)   # no lands
    _ward(warded, mana=2, life=2)
    game.resolve_stack()
    assert warded in game.players[0].battlefield
    assert removal.zone == "graveyard"
