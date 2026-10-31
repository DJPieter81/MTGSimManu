"""A removal spell is never aimed at a permanent whose ward its caster
cannot or would not pay (CR 702.21a: the spell is countered unless the
cost is paid).

The resolution-time decision ("pay the ward, or let the spell be
countered") is `decide_optional_cost` over the ward's OptionalCost. Target
choice asks the same question before casting: a target whose ward is
unaffordable — too little mana left after the spell itself, or less life
than the life part (CR 119.4) — or whose ward the payment decision would
decline, would only get the spell countered, so it is not a target. A
ward the caster would pay does not change the choice.

Rules pinned (decision rule, computed with the engine's own gate and the
same callback the resolution uses). Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.callbacks import DefaultCallbacks
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


class _Pay(DefaultCallbacks):
    def decide_optional_cost(self, game, player_idx, opt) -> bool:
        return True


class _Decline(DefaultCallbacks):
    def decide_optional_cost(self, game, player_idx, opt) -> bool:
        return False


def _put(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = False
        game.players[controller].battlefield.append(c)
    else:
        getattr(game.players[controller], zone).append(c)
    return c


def _scene(card_db, callbacks, caster_life=20, swamps=3, warded="Sire of Seven Deaths"):
    game = GameState(rng=random.Random(0), callbacks=callbacks)
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(swamps):
        _put(game, card_db, "Swamp", 0, "battlefield")
    game.players[0].life = caster_life
    big = _put(game, card_db, warded, 1, "battlefield")
    small = _put(game, card_db, "Grizzly Bears", 1, "battlefield")
    spell = _put(game, card_db, "Murder", 0, "hand")
    from ai.ev_player import EVPlayer
    ai = EVPlayer(player_idx=0, deck_name="Dimir Midrange", rng=random.Random(0))
    return game, ai, spell, big, small


def test_a_ward_the_caster_cannot_pay_in_life_rules_the_target_out(card_db):
    game, ai, spell, big, small = _scene(card_db, _Pay(), caster_life=6)
    assert ai._choose_targets(game, spell) == [small.instance_id]


def test_a_ward_the_payment_decision_would_decline_rules_the_target_out(card_db):
    game, ai, spell, big, small = _scene(card_db, _Decline())
    assert ai._choose_targets(game, spell) == [small.instance_id]


def test_a_ward_the_caster_would_pay_leaves_the_choice_unchanged(card_db):
    game, ai, spell, big, small = _scene(card_db, _Pay())
    assert ai._choose_targets(game, spell) == [big.instance_id]


def test_a_mana_ward_needs_the_mana_left_after_the_spell_itself(card_db):
    # Murder costs 3: three Swamps pay for the spell and leave nothing for
    # a ward of {4}.
    game, ai, spell, big, small = _scene(card_db, _Pay(), swamps=3,
                                         warded="Kappa Cannoneer")
    assert ai._choose_targets(game, spell) == [small.instance_id]


def test_an_instant_speed_response_rules_out_a_ward_it_would_not_pay(card_db):
    # The response path picks through `_pick_best_removal_target`, not the
    # main-phase chooser — the same rule must hold there.
    game, ai, spell, big, small = _scene(card_db, _Decline())
    chosen = ai._pick_best_removal_target(spell, [big, small], game.players[1],
                                          game, 1)
    assert chosen is small
