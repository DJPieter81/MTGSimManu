"""Tranche 4a: counter costs become PAYABLE activation costs.

Tranches 1-3 parsed "Put a -1/-1 counter on this creature" and "Remove a
charge counter from this artifact" but dumped them in
`ActivationCost.unpayable` as `put_counter` / `remove_counter`, making the
whole permanent visible-but-refused. Measured DB-wide before this tranche:
158 parsed activated abilities carried a counter cost item (147
remove-counter, 10 put-counter, 1 mixed). This tranche is a payer ADDITION
in the tranche-2 shape (`life`, `sacrifice_self`) — the parser's
classification work is reused, nothing is re-parsed.

Rules pinned:
  * CR 118.x / 601.2h — a cost is payable only if it CAN be paid: a REMOVE
    cost needs that many counters of that kind on the permanent right now.
    A PUT cost is always payable (nothing has to already exist).
  * CR 602.2b — the counter is paid at ACTIVATION time; the ability on the
    stack resolves independently of it.
  * Counters are stored ONCE, in the instance's existing counter fields
    (`plus_counters` / `minus_counters` / `loyalty_counters` /
    `other_counters`). The payer reads and writes those, never a parallel
    ledger.
  * No-free-repeatable (rule 9): a REMOVE cost always depletes the counter
    supply. A PUT cost depletes only when the counter itself is a
    resource — a -1/-1 counter on a creature shrinks toughness toward the
    SBA that ends the loop; a neutral counter (charge/page/oil) on an
    otherwise-free ability depletes nothing and stays refused.
  * A counter kind whose P/T semantics the instance model cannot hold
    (-0/-1, +2/+2) is refused rather than approximated.

Rules-phrased; card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from engine.activation import ActivationManager
from engine.cards import (ActivatedAbility, ActivationCost,
                          ActivationEffectKind, CardInstance)
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import parse_activation_cost

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


def _game(n_islands=4, n_hand=3):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.players[0].deck_name = "Eldrazi Tron"
    game.players[1].deck_name = "Dimir Midrange"
    for _ in range(n_islands):
        _add(game, "Island")
    for _ in range(n_hand):
        _add(game, "Island", 0, "hand")
    for _ in range(8):
        _add(game, "Island", 0, "library")
    return game


def _ability(mana=0, tap=False, put_kind=None, put_n=0,
             rem_kind=None, rem_n=0,
             kind=ActivationEffectKind.DRAW_N, amount=1):
    return ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(generic=mana), tap_self=tap,
                            put_counter_kind=put_kind,
                            put_counter_amount=put_n,
                            remove_counter_kind=rem_kind,
                            remove_counter_amount=rem_n),
        effect_text="Draw a card.", effect_kind=kind, amount=amount)


def _host(game, ability, name="Wall of Omens"):
    """Attach one synthetic ability to a fixture carrier. The template is
    COPIED first: templates are shared DB objects and mutating one would
    leak into every other test in the session."""
    perm = _add(game, name)
    perm.template = copy.copy(perm.template)
    perm.template.activated_abilities = [ability]
    return perm


# ── parsing: counter cost items become structured, not unpayable ──────

def test_remove_counter_cost_parses_kind_and_amount():
    cost = parse_activation_cost(
        "{1}, {T}, Remove a page counter from this artifact")
    assert cost is not None
    assert cost.remove_counter_kind == "page"
    assert cost.remove_counter_amount == 1
    assert cost.tap_self is True and cost.mana.cmc == 1
    assert cost.unpayable == (), (
        f"a self-referential remove-counter cost is tranche-4 payable, got "
        f"{cost.unpayable}")


def test_put_counter_cost_parses_kind_and_amount():
    cost = parse_activation_cost("Put a -1/-1 counter on this creature")
    assert cost is not None
    assert cost.put_counter_kind == "-1/-1"
    assert cost.put_counter_amount == 1
    assert cost.unpayable == ()


def test_multi_counter_amount_parses_as_a_count():
    cost = parse_activation_cost(
        "Remove two +1/+1 counters from this creature")
    assert cost is not None
    assert cost.remove_counter_kind == "+1/+1"
    assert cost.remove_counter_amount == 2
    assert cost.unpayable == ()


def test_pt_counter_kinds_the_instance_model_cannot_hold_stay_unpayable():
    """+1/+1 and -1/-1 are the only P/T counter kinds the instance model
    represents. A -0/-1 counter would have to shrink toughness WITHOUT
    shrinking power — refused, never mapped onto -1/-1."""
    for phrase in ("Put a -0/-1 counter on this creature",
                   "Remove a +2/+2 counter from this creature"):
        cost = parse_activation_cost(phrase)
        assert cost is not None and cost.unpayable, (
            f"{phrase!r} must stay visible-but-refused, got {cost}")
        assert cost.put_counter_kind is None
        assert cost.remove_counter_kind is None


def test_counter_costs_on_other_permanents_stay_unpayable():
    """This tranche pays counters on the SOURCE only. A counter moved off
    another permanent needs a victim choice (the sacrifice-another shape),
    and an unbounded 'any number'/'all' count is a different cost shape —
    both refused rather than approximated."""
    for phrase in ("Remove a +1/+1 counter from a creature you control",
                   "Remove two +1/+1 counters from among creatures you control",
                   "Remove any number of charge counters from this artifact",
                   "Remove all +1/+1 counters from this creature",
                   "Remove a counter from this creature"):
        cost = parse_activation_cost(phrase)
        assert cost is not None and cost.unpayable, (
            f"{phrase!r} must stay visible-but-refused, got {cost}")
        assert cost.remove_counter_kind is None


# ── legality ──────────────────────────────────────────────────────────

def test_remove_counter_refused_when_the_counters_are_not_there():
    """CR 118.x — you cannot pay what you do not have."""
    game = _game()
    ab = _ability(rem_kind="+1/+1", rem_n=1)
    perm = _host(game, ab)
    assert perm.counter_count("+1/+1") == 0
    assert not ActivationManager.can_activate(game, 0, perm, ab)
    perm.adjust_counters("+1/+1", 1)
    assert ActivationManager.can_activate(game, 0, perm, ab), (
        "one counter exactly covers a one-counter cost")


def test_remove_counter_amount_must_be_fully_covered():
    game = _game()
    ab = _ability(rem_kind="+1/+1", rem_n=2)
    perm = _host(game, ab)
    perm.adjust_counters("+1/+1", 1)
    assert not ActivationManager.can_activate(game, 0, perm, ab), (
        "a partially-covered cost is not payable")
    perm.adjust_counters("+1/+1", 1)
    assert ActivationManager.can_activate(game, 0, perm, ab)


def test_put_counter_is_always_payable():
    """Nothing has to pre-exist for a PUT cost — the counter is created."""
    game = _game()
    ab = _ability(mana=1, put_kind="-1/-1", put_n=1)
    perm = _host(game, ab)
    assert ActivationManager.can_activate(game, 0, perm, ab)


def test_removing_a_counter_satisfies_the_no_free_repeatable_rule():
    """A remove-counter cost depletes a finite supply, so a zero-mana,
    no-tap ability carrying it terminates the way life/sacrifice do."""
    game = _game()
    ab = _ability(rem_kind="+1/+1", rem_n=1)
    perm = _host(game, ab)
    perm.adjust_counters("+1/+1", 3)
    assert ActivationManager.can_activate(game, 0, perm, ab)


def test_putting_a_minus_counter_on_a_creature_depletes_its_toughness():
    """A -1/-1 counter cost shrinks the source toward the zero-toughness
    SBA — that is the resource that terminates the loop."""
    game = _game()
    ab = _ability(put_kind="-1/-1", put_n=1)
    perm = _host(game, ab)  # a creature host
    assert ActivationManager.can_activate(game, 0, perm, ab)


def test_putting_a_neutral_counter_for_free_stays_refused():
    """A charge/page/oil counter depletes nothing. A zero-mana, no-tap
    ability that only adds one is a free repeatable with no terminating
    resource — refused, exactly like a bare zero-cost ability."""
    game = _game()
    ab = _ability(put_kind="page", put_n=1)
    perm = _host(game, ab)
    assert not ActivationManager.can_activate(game, 0, perm, ab)
    priced = _ability(mana=2, tap=True, put_kind="page", put_n=1)
    priced_host = _host(game, priced, name="Wall of Omens")
    assert ActivationManager.can_activate(game, 0, priced_host, priced), (
        "the same cost is fine once a real cost item prices it")


def test_putting_a_minus_counter_on_a_noncreature_stays_refused():
    """-1/-1 counters on a non-creature deplete nothing (no toughness to
    shrink), so they do not license a free repeatable."""
    game = _game()
    ab = _ability(put_kind="-1/-1", put_n=1)
    perm = _host(game, ab, name="Mazemind Tome")  # an artifact
    assert not ActivationManager.can_activate(game, 0, perm, ab)


# ── payment ───────────────────────────────────────────────────────────

def test_removed_counters_leave_the_instance_at_activation_time():
    game = _game()
    ab = _ability(rem_kind="+1/+1", rem_n=2)
    perm = _host(game, ab)
    perm.adjust_counters("+1/+1", 3)
    hand = len(game.players[0].hand)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    assert perm.plus_counters == 1, (
        "the counters are paid as a COST at activation time, through the "
        "instance's own counter fields")
    game.resolve_stack()
    assert len(game.players[0].hand) == hand + 1


def test_put_counters_arrive_on_the_instance_at_activation_time():
    game = _game()
    ab = _ability(put_kind="-1/-1", put_n=1)
    perm = _host(game, ab)
    tough_before = perm.toughness
    assert ActivationManager.activate(game, 0, perm, ab, [])
    assert perm.minus_counters == 1
    assert perm.toughness == tough_before - 1, (
        "a -1/-1 counter must move the instance's real P/T, not a shadow "
        "ledger — that is what makes the SBA terminate the loop")


def test_named_counter_kinds_use_the_generic_counter_map():
    game = _game()
    ab = _ability(mana=1, rem_kind="page", rem_n=1)
    perm = _host(game, ab)
    perm.adjust_counters("page", 2)
    assert perm.other_counters["page"] == 2
    assert ActivationManager.activate(game, 0, perm, ab, [])
    assert perm.other_counters["page"] == 1


def test_activation_is_refused_whole_when_counters_are_missing():
    """Half-paid costs are forbidden: with the counters absent, activate()
    refuses before charging the mana half."""
    game = _game()
    ab = _ability(mana=1, rem_kind="+1/+1", rem_n=1)
    perm = _host(game, ab)
    untapped_before = len(game.players[0].untapped_lands)
    assert not ActivationManager.activate(game, 0, perm, ab, [])
    assert len(game.players[0].untapped_lands) == untapped_before
    assert game.stack.is_empty


# ── end-to-end on real DB shapes ──────────────────────────────────────

def test_counter_removal_damage_outlet_parses_payable_from_the_db():
    """'Remove a +1/+1 counter from this creature: It deals 1 damage to any
    target' — the largest counter-cost class (24 abilities DB-wide)."""
    t = _DB.get_card("Walking Ballista")
    dmg = [a for a in (t.activated_abilities or [])
           if a.effect_kind is ActivationEffectKind.DAMAGE_ANY_TARGET]
    assert dmg, f"fixture premise: got {t.activated_abilities}"
    ab = dmg[0]
    assert ab.cost.remove_counter_kind == "+1/+1"
    assert ab.cost.remove_counter_amount == 1
    assert ab.cost.unpayable == (), f"got {ab.cost.unpayable}"


def test_named_counter_removal_draw_parses_payable_from_the_db():
    """'{1}, {T}, Remove a page counter from this artifact: Draw a card' —
    the named-counter class routed through `other_counters`."""
    t = _DB.get_card("Tome of Legends")
    draws = [a for a in (t.activated_abilities or [])
             if a.effect_kind is ActivationEffectKind.DRAW_N]
    assert draws, f"fixture premise: got {t.activated_abilities}"
    ab = draws[0]
    assert ab.cost.remove_counter_kind == "page"
    assert ab.cost.tap_self is True
    assert ab.cost.unpayable == ()


def test_counter_removal_outlet_runs_end_to_end_from_the_db():
    """Engine end-to-end: the counter is charged, the damage resolves, and
    the finite counter supply is what ends the repetition."""
    game = _game()
    ballista = _add(game, "Walking Ballista")
    ballista.adjust_counters("+1/+1", 2)
    ab = [a for a in ballista.template.activated_abilities
          if a.effect_kind is ActivationEffectKind.DAMAGE_ANY_TARGET][0]
    opp_life = game.players[1].life
    fired = 0
    while ActivationManager.can_activate(game, 0, ballista, ab) and fired < 20:
        assert ActivationManager.activate(game, 0, ballista, ab, [])
        game.resolve_stack()
        game.check_state_based_actions()
        fired += 1
    assert fired == 2, (
        f"exactly as many activations as counters — the cost is the bound "
        f"(fired {fired})")
    assert game.players[1].life < opp_life
