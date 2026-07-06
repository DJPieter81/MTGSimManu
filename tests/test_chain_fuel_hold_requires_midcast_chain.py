"""Chain-fuel hold applies only while the opponent's chain is mid-cast.

Rule (mechanic-phrased, no card names in the rule):

    The chain-fuel hold (`bottleneck_probability` == 0.0) reserves a
    defender's counters for the opponent's chain PAYOFF.  That trade
    is EV-sound only while the chain is actually MID-CAST this turn —
    the opponent has committed mana to at least one prior spell and
    the payoff is imminent, so a counter spent on fuel strands the
    defender against the payoff.

    Outside a mid-cast chain (a development turn: the spell on the
    stack is the opponent's first cast of the turn), fuel is ordinary
    sorcery-speed card advantage / mana development.  The hold must
    NOT apply; legacy threat evaluation prices the response.  A hold
    keyed on the opponent's ARCHETYPE (or on a cost-reducer merely
    being on the battlefield) extends the triage rule into a
    full-game interaction lockout: the defender can never counter any
    engine piece, draw engine, or ritual on any turn of the game,
    which is the decision-level root cause of the 2026-07-05 combo
    calibration overshoot (field WR 70.8% vs band [40-55], reactive
    decks' rows at 80-95%).

    The payoff arm (bp == 1.0) is storm-count independent: a
    payoff-access spell may be countered whenever chain state is
    inferable, including as the first cast of the turn.

Class size: every fuel-tagged spell of every chain deck (storm,
cascade, ritual-combo — hundreds of printings) responded to by every
counter-holding deck.  Lift-check: the same rule restores dev-turn
counterplay for any reactive deck vs any chain archetype, not just
the audited pairing.
"""
from __future__ import annotations

import math
import random

from ai.combo_calc import bottleneck_probability
from ai.response import ResponseDecider
from ai.turn_planner import TurnPlanner
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


def _add_to_zone(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    elif zone == "library":
        game.players[controller].library.append(card)
    return card


def _put_on_stack(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="stack",
    )
    card._game_state = game
    item = StackItem(
        item_type=StackItemType.SPELL,
        source=card,
        controller=controller,
        targets=[],
    )
    game.stack.push(item)
    return item


def _game(card_db, *, stack_card, spells_cast_this_turn,
          with_cost_reducer=True):
    """Combo-archetype opponent, payoff reachable in pool, defender
    holding a hard counter with mana up.  ``spells_cast_this_turn``
    uses production semantics: it INCLUDES the spell on the stack
    (cast completes on push — engine/cast_manager.py increments at
    push time), so 1 == "first cast of the turn" and >= 2 == mid-cast.
    """
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    game.priority_player = defender_idx
    game.turn_number = 3

    for _ in range(2):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    for _ in range(2):
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "battlefield")
    if with_cost_reducer:
        _add_to_zone(game, card_db, "Ruby Medallion", attacker_idx,
                     "battlefield")

    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    # Payoff reachable: accessor in hand, direct payoff in library.
    _add_to_zone(game, card_db, "Wish", attacker_idx, "hand")
    _add_to_zone(game, card_db, "Grapeshot", attacker_idx, "library")
    for _ in range(20):
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "library")

    game.players[attacker_idx].spells_cast_this_turn = spells_cast_this_turn
    game.players[attacker_idx].deck_name = "Ruby Storm"
    game.players[defender_idx].deck_name = "Azorius Control"

    item = _put_on_stack(game, card_db, stack_card, attacker_idx)
    return game, item, defender_idx


def _decide(game, item, defender_idx):
    decider = ResponseDecider(defender_idx, TurnPlanner(),
                              strategic_logger=None)
    decider.opp_archetype = "combo"
    result = decider.decide_response(game, item)
    reason = (decider.last_decision or {}).get("chosen", {}).get("reason", "")
    return result, reason


# ─────────────────────────────────────────────────────────────────────
# Primary rule — development-turn fuel is NOT chain-held
# ─────────────────────────────────────────────────────────────────────


def test_first_cast_of_turn_fuel_is_not_chain_held(card_db):
    """The opponent's first cast of the turn (spells_cast_this_turn
    == 1: the stack spell itself) is a development cast, not a
    mid-cast chain.  bottleneck_probability must defer to legacy
    threat evaluation (NaN), not return the 0.0 hold.

    Pre-fix: the archetype arm of the in-flight predicate returned
    the hold for every fuel spell a combo deck ever cast.
    """
    game, item, defender_idx = _game(
        card_db, stack_card="Wrenn's Resolve", spells_cast_this_turn=1)
    bp = bottleneck_probability(item, game, defender_idx)
    assert math.isnan(bp), (
        "Development-turn fuel (first cast of the turn) must defer to "
        f"legacy threat evaluation (NaN); got bp={bp!r}"
    )


def test_development_turn_fuel_response_not_starved(card_db):
    """Decision level: on a development turn the defender's response
    to a fuel-tagged draw engine must NOT be the chain-fuel hold —
    the legacy gate decides (and with a hard counter + mana up it
    fires, denying the chain deck its velocity).

    Pre-fix (calibration overshoot smoking gun): the reason recorded
    was 'chain-fuel hold' on every fuel spell of every turn, so
    reactive decks cast 0-4 pieces of interaction per MATCH.
    """
    game, item, defender_idx = _game(
        card_db, stack_card="Wrenn's Resolve", spells_cast_this_turn=1)
    result, reason = _decide(game, item, defender_idx)
    assert "chain-fuel hold" not in reason, (
        "Development-turn fuel must not trigger the chain-fuel hold; "
        f"reason recorded: {reason!r}"
    )
    assert result is not None, (
        "With a hard counter and mana up, legacy threat evaluation "
        "fires on a development-turn draw engine (deny chain velocity); "
        "got a pass instead"
    )


def test_cost_reducer_presence_alone_does_not_hold_fuel(card_db):
    """A cost-reducer on the opponent's battlefield is a structural
    chain SIGNAL, but it does not make every later turn a combo turn.
    Fuel cast as the first spell of a turn behind a cost reducer must
    still defer to legacy evaluation.
    """
    game, item, defender_idx = _game(
        card_db, stack_card="Reckless Impulse", spells_cast_this_turn=1,
        with_cost_reducer=True)
    bp = bottleneck_probability(item, game, defender_idx)
    assert math.isnan(bp), (
        "Cost-reducer presence alone must not convert development fuel "
        f"into a chain-fuel hold; got bp={bp!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# Preserved behaviour — mid-cast hold and payoff release
# ─────────────────────────────────────────────────────────────────────


def test_midcast_fuel_still_held(card_db):
    """M2's intended triage is preserved: fuel on the stack with at
    least one PRIOR cast this turn (spells_cast_this_turn >= 2 in
    production semantics) is held while a payoff is reachable.
    """
    game, item, defender_idx = _game(
        card_db, stack_card="Wrenn's Resolve", spells_cast_this_turn=3)
    bp = bottleneck_probability(item, game, defender_idx)
    assert bp == 0.0, f"Mid-cast fuel must still be held; got bp={bp!r}"

    result, reason = _decide(game, item, defender_idx)
    assert result is None and "chain" in reason.lower(), (
        "Mid-cast fuel must still record the chain-fuel hold; "
        f"got result={result!r} reason={reason!r}"
    )


def test_payoff_accessor_counterable_even_as_first_cast(card_db):
    """The payoff arm is storm-count independent: a payoff-access
    spell cast as the FIRST spell of the turn still returns bp == 1.0
    (archetype / cost-reducer arms keep chain state inferable), so
    the counter fires on the bottleneck.
    """
    game, item, defender_idx = _game(
        card_db, stack_card="Wish", spells_cast_this_turn=1)
    bp = bottleneck_probability(item, game, defender_idx)
    assert bp == 1.0, (
        f"Payoff accessor must stay counterable at any storm count; "
        f"got bp={bp!r}"
    )
