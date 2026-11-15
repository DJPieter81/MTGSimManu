"""The mana a sacrifice-for-mana ability can produce is mana its controller
can pay with (CR 605.1a, 605.3a, 601.2g-h, 106.4).

A mana ability that sacrifices its source ("{T}, Sacrifice this token: Add
one mana of any color"; "Sacrifice this token: Add {C}") may be activated
while its controller pays a cost, like any mana ability. Its own cost must
be payable: a {T} in it needs the source untapped (CR 107.5) and, on a
creature, under its controller's control since their turn began (CR
302.6). A cost with mana in it is a conversion, not a source: paying it
needs the mana it would make.

The defects: the castability check, the colour check and the AI's mana
estimate counted lands, rocks and dorks only, so a Treasure or an Eldrazi
Spawn never made a spell castable. The payment path spent them only as a
generic shortfall, adding the first colour of the unit, so a Treasure
could never pay a coloured pip; it ignored the {T} in a Treasure's cost and
the {1} in a conversion's.

Class: every source with a sacrifice-for-mana ability -- 207 pool cards
create a Treasure; Eldrazi Spawn and Scion makers; Lotus-style artifacts.
Registered: Ragavan, Fable of the Mirror-Breaker, Beza (Treasure) and
Kozilek's Command (Eldrazi Spawn, Eldrazi Tron). Card names are fixture
carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.mana import ManaCost

_SPAWN = ('Create a 0/1 colorless Eldrazi Spawn creature token with '
          '"Sacrifice this token: Add {C}."')


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone="battlefield", idx=0):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _spawn(game, idx=0):
    from engine.effect_grammar import parse_effects
    [spec] = parse_effects(_SPAWN)
    return game.create_token(idx, "", count=1, token_spec=spec.payload)[0]


def test_a_treasure_counts_toward_what_its_controller_can_cast(card_db):
    game = _game()
    _put(game, card_db, "Island")
    bolt = _put(game, card_db, "Lightning Bolt", zone="hand")
    think = _put(game, card_db, "Think Twice", zone="hand")
    me = game.players[0]
    assert not game.can_cast(0, bolt), "no land makes red"
    assert not game.can_cast(0, think), "one land pays one mana"
    game.create_token(0, "treasure")
    assert me.untapped_mana_capacity() == 2
    assert me.available_mana_estimate == 2
    assert game.can_cast(0, bolt), "the Treasure makes the red"
    assert game.can_cast(0, think), "the Treasure is the second mana"


def test_a_treasure_pays_a_coloured_pip_its_lands_cannot(card_db):
    game = _game()
    island = _put(game, card_db, "Island")
    [treasure] = game.create_token(0, "treasure")
    assert game.tap_lands_for_mana(0, ManaCost(generic=1, red=1))
    assert island.tapped
    assert treasure not in game.players[0].battlefield
    assert game.players[0].mana_pool.total() == 0


def test_a_treasure_alone_pays_a_single_pip_of_any_colour(card_db):
    for pip in ("white", "blue", "black", "red", "green"):
        game = _game()
        [treasure] = game.create_token(0, "treasure")
        assert game.tap_lands_for_mana(0, ManaCost(**{pip: 1})), pip
        assert treasure not in game.players[0].battlefield


def test_lands_pay_before_a_sacrifice_source_is_spent(card_db):
    game = _game()
    for _ in range(2):
        _put(game, card_db, "Island")
    [treasure] = game.create_token(0, "treasure")
    assert game.tap_lands_for_mana(0, ManaCost(generic=1, blue=1))
    assert treasure in game.players[0].battlefield


def test_a_tapped_treasure_cannot_pay_its_tap_cost(card_db):
    game = _game()
    _put(game, card_db, "Island")
    [treasure] = game.create_token(0, "treasure")
    treasure.tapped = True
    me = game.players[0]
    assert me.untapped_mana_capacity() == 1
    assert not game.tap_lands_for_mana(0, ManaCost(generic=2))
    assert treasure in me.battlefield


def test_a_spawn_pays_the_turn_it_arrives(card_db):
    game = _game()
    spawn = _spawn(game)
    assert spawn.has_summoning_sickness
    me = game.players[0]
    assert me.untapped_mana_capacity() == 1
    assert game.tap_lands_for_mana(0, ManaCost(generic=1))
    assert spawn not in me.battlefield


def test_a_sacrifice_ability_that_costs_mana_adds_no_capacity(card_db):
    game = _game()
    _put(game, card_db, "Island")
    star = _put(game, card_db, "Chromatic Star")
    me = game.players[0]
    assert me.untapped_mana_capacity() == 1
    assert not game.tap_lands_for_mana(0, ManaCost(generic=2))
    assert star in me.battlefield


def test_a_source_never_pays_for_its_own_ability(card_db):
    from engine.mana_payment import ManaPayment
    game = _game()
    [treasure] = game.create_token(0, "treasure")
    assert ManaPayment.sacrifice_mana_sources(game, 0) == [
        (treasure, [["W", "U", "B", "R", "G"]])]
    assert ManaPayment.sacrifice_mana_sources(
        game, 0, exclude_instance_id=treasure.instance_id) == []


def _blaze_with_two_spawn(card_db):
    from engine.constants import PLAYER_TARGET_OPPONENT
    game = _game()
    mountain = _put(game, card_db, "Mountain")
    spawns = [_spawn(game), _spawn(game)]
    blaze = _put(game, card_db, "Blaze", zone="hand")
    assert game.cast_spell(0, blaze, targets=[PLAYER_TARGET_OPPONENT])
    return game, mountain, spawns


def test_the_x_of_a_spell_is_paid_from_every_source_its_controller_can_pay_with(
        card_db):
    """X is chosen from the mana its caster can pay and is paid in full:
    the land pays {R}, the two Spawn are sacrificed for X=2 (CR 601.2f-h,
    605.3a) -- never an X of 2 paid by nothing."""
    game, mountain, spawns = _blaze_with_two_spawn(card_db)
    me = game.players[0]
    assert game.stack.top.x_value == 2
    assert mountain.tapped
    assert all(s not in me.battlefield for s in spawns)
    game.resolve_stack()
    assert game.players[1].life == 18


def test_an_x_is_paid_with_every_unit_its_lands_produce(card_db):
    """The three Urza lands together make seven mana (their conditional
    bonus); an X chosen as 7 is paid with all seven, not three of them."""
    game = _game()
    lands = [_put(game, card_db, n) for n in
             ("Urza's Tower", "Urza's Mine", "Urza's Power Plant")]
    serpent = _put(game, card_db, "Stonecoil Serpent", zone="hand")
    paid = getattr(game, "mana_paid_total", 0)
    assert game.cast_spell(0, serpent)
    assert game.stack.top.x_value == 7
    assert all(land.tapped for land in lands)
    assert getattr(game, "mana_paid_total", 0) - paid == 7
    assert game.players[0].mana_pool.total() == 0


# ── Auditor (CR 605.3a, 107.5), restated from the ability's raw cost ─

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_tapped_source_sacrificed_for_mana(card_db, audit,
                                                               monkeypatch):
    from engine import mana_payment
    monkeypatch.setattr(mana_payment, "_sacrifice_cost_payable",
                        lambda perm, ability: True)              # the defect
    game = _game()
    [treasure] = game.create_token(0, "treasure")
    treasure.tapped = True
    assert game.tap_lands_for_mana(0, ManaCost(generic=1))
    assert "605.3a/sacrifice_mana_cost_paid" in _rules(audit)


def test_the_audit_records_an_x_left_unpaid(card_db, audit, monkeypatch):
    from engine.mana_payment import ManaPayment
    real = ManaPayment.tap_lands_for_mana

    def no_generic(game, player_idx, cost, *a, **k):            # the defect
        if cost.generic and not cost.non_generic_pips:
            return False
        return real(game, player_idx, cost, *a, **k)
    monkeypatch.setattr(ManaPayment, "tap_lands_for_mana",
                        staticmethod(no_generic))
    _blaze_with_two_spawn(card_db)
    assert "601.2h/x_cost_paid" in _rules(audit)


def test_the_audit_is_silent_when_the_x_is_paid(card_db, audit):
    _blaze_with_two_spawn(card_db)
    assert "601.2h/x_cost_paid" not in _rules(audit)


def test_the_audit_is_silent_when_each_sacrifice_cost_was_payable(card_db,
                                                                  audit):
    game = _game()
    _put(game, card_db, "Island")
    game.create_token(0, "treasure")
    _spawn(game)
    assert game.tap_lands_for_mana(0, ManaCost(generic=1, red=1))
    assert game.tap_lands_for_mana(0, ManaCost(generic=1))
    assert "605.3a/sacrifice_mana_cost_paid" not in _rules(audit)
