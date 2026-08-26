"""Activating an ability can never recurse into cost payment or run unbounded.

This is the whole risk of the activated-ability subsystem, so it is pinned
before anything enumerates activations.

The hazard is specific: the engine's existing action caps guard the MAIN-PHASE
loop. An ability activated *during mana payment* would spin below all of them —
a self-untapping mana source paying for its own re-activation has no counter
watching it. Two independent guards close that, and this file asserts both
rather than trusting either:

  * `_paying_mana` — CR 605.3 permits only MANA abilities while paying a cost.
    Raised across the whole of `tap_lands_for_mana`, so the recursive edge does
    not exist rather than being merely bounded.
  * `_activation_depth` vs `ACTIVATION_MAX_DEPTH` — incremented on BOTH the
    push side and the resolution side, because a push-side guard alone misses
    re-entry that begins while an ability is resolving.

Also pinned here: a permanent may not tap itself to pay for its own ability,
and a free repeatable ability is refused (it has no depleting resource, so
nothing terminates the loop).

Rules-phrased; card names are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.activation import ActivationManager
from engine.cards import (ActivatedAbility, ActivationCost,
                          ActivationEffectKind, CardInstance)
from engine.card_database import CardDatabase
from engine.constants import ACTIVATION_MAX_DEPTH
from engine.game_state import GameState, Phase
from engine.mana import ManaCost

_DB = CardDatabase()


def _add(game, name, controller=0, zone="battlefield"):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game(n_lands=4):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    game.players[0].deck_name = "Eldrazi Tron"
    game.players[1].deck_name = "Dimir Midrange"
    for _ in range(n_lands):
        _add(game, "Island")
    return game


def _ability(mana=1, tap=False, kind=ActivationEffectKind.DRAW_N, amount=1,
             unpayable=(), restrictions=(), once=False, mana_ability=False):
    return ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(generic=mana), tap_self=tap,
                            unpayable=tuple(unpayable)),
        effect_text="Draw a card.",
        effect_kind=kind,
        amount=amount,
        restrictions=tuple(restrictions),
        once_each_turn=once,
        is_mana_ability=mana_ability,
    )


def _host(game, ability):
    """A permanent carrying `ability` as its only activated ability."""
    perm = _add(game, "Wall of Omens")
    perm.template.activated_abilities = [ability]
    return perm


# ── the two safety valves ──────────────────────────────────────────────

def test_no_activation_while_a_cost_is_being_paid():
    """CR 605.3 — only mana abilities may be activated to pay a cost."""
    game = _game()
    ab = _ability()
    perm = _host(game, ab)
    assert ActivationManager.can_activate(game, 0, perm, ab), (
        "control: this ability is legal when no payment is in flight")

    game._paying_mana = 1
    assert not ActivationManager.can_activate(game, 0, perm, ab), (
        "an activation attempted DURING cost payment must be refused — this "
        "is the edge that could otherwise recurse into payment and spin "
        "below every existing action counter")


def test_paying_mana_flag_is_raised_and_cleared_by_the_payment_path():
    """The guard is worthless if the real payment path doesn't set it."""
    from engine.mana_payment import ManaPayment

    game = _game()
    seen = {}
    real_inner = ManaPayment._tap_lands_for_mana_inner

    def spy(g, *a, **kw):
        seen['during'] = getattr(g, '_paying_mana', 0)
        return real_inner(g, *a, **kw)

    ManaPayment._tap_lands_for_mana_inner = staticmethod(spy)
    try:
        ManaPayment.tap_lands_for_mana(game, 0, ManaCost(generic=1), "X")
    finally:
        ManaPayment._tap_lands_for_mana_inner = staticmethod(real_inner)

    assert seen.get('during', 0) > 0, (
        "the real payment path must raise _paying_mana, or the CR 605.3 gate "
        "never fires in a live game")
    assert getattr(game, '_paying_mana', 0) == 0, (
        "the flag must be cleared afterwards, even though payment succeeded")


def test_activation_depth_bound_refuses_re_entry():
    game = _game()
    ab = _ability()
    perm = _host(game, ab)
    game._activation_depth = ACTIVATION_MAX_DEPTH
    assert not ActivationManager.can_activate(game, 0, perm, ab), (
        "at the re-entry bound no further activation may begin")


def test_resolution_side_also_increments_the_depth_guard():
    """A push-side guard alone misses re-entry that begins during RESOLUTION."""
    from engine.activated_effects import resolve_activated_ability

    game = _game()
    perm = _add(game, "Wall of Omens")
    depth_seen = {}
    real_draw = game.draw_cards

    def spy_draw(idx, n):
        depth_seen['during'] = getattr(game, '_activation_depth', 0)
        return real_draw(idx, n)

    game.draw_cards = spy_draw
    resolve_activated_ability(game, perm, 0, None, ability=_ability())
    assert depth_seen.get('during', 0) > 0, (
        "the depth guard must be raised while the effect is RESOLVING, not "
        "only while it is being pushed")
    assert getattr(game, '_activation_depth', 0) == 0, (
        "and released afterwards")


# ── cost-side protections ──────────────────────────────────────────────

def test_permanent_cannot_tap_itself_to_pay_for_its_own_ability():
    from engine.mana_payment import ManaPayment

    game = _game(n_lands=0)
    rock = _add(game, "Talisman of Impulse")
    assert not rock.tapped
    paid = ManaPayment.tap_lands_for_mana(
        game, 0, ManaCost(generic=1), None,
        exclude_instance_id=rock.instance_id)
    assert not paid, (
        "the only mana source on board is the ability's own source; excluding "
        "it must make the cost unpayable rather than tapping itself")
    assert not rock.tapped, "and it must not have been tapped"


def test_free_repeatable_ability_is_refused():
    game = _game()
    ab = _ability(mana=0, tap=False)
    perm = _host(game, ab)
    assert not ActivationManager.can_activate(game, 0, perm, ab), (
        "a zero-cost ability with no tap has no depleting resource, so "
        "nothing would terminate a loop of activations")


def test_capacity_precondition_refuses_an_unaffordable_ability():
    game = _game(n_lands=1)
    ab = _ability(mana=5)
    perm = _host(game, ab)
    assert not ActivationManager.can_activate(game, 0, perm, ab), (
        "refusing up-front is what keeps payment atomic: both mutating "
        "branches inside the solver are gated on a shortfall")


# ── refusal conditions from the schema ─────────────────────────────────

def test_unpayable_cost_item_is_refused():
    game = _game()
    ab = _ability(unpayable=("sacrifice_self",))
    perm = _host(game, ab)
    assert not ActivationManager.can_activate(game, 0, perm, ab)


def test_sibling_with_an_unpayable_cost_disables_the_whole_permanent():
    """A half-payable engine is worse than an unusable one."""
    game = _game()
    good = _ability()
    bad = _ability(unpayable=("pay_life",))
    bad.index = 1
    perm = _add(game, "Wall of Omens")
    perm.template.activated_abilities = [good, bad]
    assert not ActivationManager.can_activate(game, 0, perm, good), (
        "the AI must not pay for the cheap half of a combo it can never "
        "finish")


def test_unrepresentable_restriction_is_refused():
    game = _game()
    ab = _ability(restrictions=("Activate only if you control a Dragon.",))
    perm = _host(game, ab)
    assert not ActivationManager.can_activate(game, 0, perm, ab)


def test_mana_ability_is_not_activatable_as_a_play():
    game = _game()
    ab = _ability(mana_ability=True)
    perm = _host(game, ab)
    assert not ActivationManager.can_activate(game, 0, perm, ab), (
        "mana abilities are produced by the payment path (CR 605)")


def test_once_each_turn_ledger_blocks_the_second_activation():
    game = _game()
    ab = _ability(once=True)
    perm = _host(game, ab)
    assert ActivationManager.can_activate(game, 0, perm, ab)
    perm.activations_this_turn[ab.index] = 1
    assert not ActivationManager.can_activate(game, 0, perm, ab)


def test_tap_cost_refused_on_a_summoning_sick_creature():
    """CR 302.6."""
    game = _game()
    ab = _ability(mana=0, tap=True)
    perm = _add(game, "Wall of Omens")
    perm.template.activated_abilities = [ab]
    perm.summoning_sick = True
    assert not ActivationManager.can_activate(game, 0, perm, ab)
    perm.summoning_sick = False
    assert ActivationManager.can_activate(game, 0, perm, ab)


def test_pump_is_refused_on_a_non_creature_permanent():
    """Cleanup iterates player.creatures, so a pump elsewhere never expires."""
    game = _game()
    ab = _ability(kind=ActivationEffectKind.PUMP_SELF_UEOT)
    ab.power_mod, ab.toughness_mod = 2, 2
    perm = _add(game, "Talisman of Impulse")  # artifact, not a creature
    perm.template.activated_abilities = [ab]
    assert not ActivationManager.can_activate(game, 0, perm, ab)
