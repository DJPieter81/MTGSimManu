"""Cheap hard-paid counter held through chain-fuel when payoff unresolved.

Rule (mechanic-phrased, no card names in the rule):

    The chain-fuel hold in `decide_response` (M2) reserves held
    counters for the opponent's chain PAYOFF when the spell on the
    stack is chain FUEL and a payoff is still reachable in the
    opponent's pool.  The only exemption from the hold is a genuine
    PITCH counter — one castable via a mana-free alternative cost
    ("exile a card ... rather than pay") on the opponent's turn.
    Burning a pitch counter costs only the pitched card, so its
    opportunity cost against the future payoff is near zero.

    A cheap HARD-PAID counter (printed CMC at or below the pitch
    threshold, but paid with real mana) is NOT exempt.  It carries
    the same card-opportunity cost as any other counter, and — being
    a conditional/cheap counter — it is typically at its BEST against
    the payoff, when the combo player is tapped out mid-chain.  The
    exemption must therefore be classified by MECHANIC (pitch
    alternative-cost path), not by effective-cost VALUE.

    Class size: every ≤1-CMC hard-paid counter in Modern (the
    "counter unless controller pays" family, the creature-counter
    family, the copy-target family — well over 10 printings) held by
    every counter-density deck vs every combo deck.  Micro-audit A4
    Wave-2 (2026-05-17): defender burned a 1-mana conditional
    counter on a 4-of chain cantrip with no 'chain-fuel hold'
    reasoning recorded, because the cost-based pitch exemption
    matched its printed CMC.

Lift-check (one OTHER deck per CLAUDE.md): the same fix holds for
any cheap-counter deck vs any chain deck — e.g. a tempo deck's
1-mana counters vs a cascade deck's fuel spells.  Symmetric across
counter-holding archetypes; no deck-specific knowledge involved.
"""
from __future__ import annotations

import random

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


def _make_decider(defender_idx):
    return ResponseDecider(
        defender_idx, TurnPlanner(), strategic_logger=None
    )


def _mid_chain_game(card_db, *, payoff_reachable: bool):
    """Build the audit's M1 G1 T6 shape: combo opp mid-chain, fuel
    cantrip on the stack, defender holding a cheap conditional
    counter AND a hard counter with mana for either.

    When ``payoff_reachable`` is False, the opponent's hand/library
    carry no chain-payoff accessor — the fuel on the stack IS the
    bottleneck of whatever chain remains.
    """
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    game.priority_player = defender_idx
    game.turn_number = 6

    # Defender mana — UU up (enough for either counter).
    for _ in range(2):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")

    # Attacker mana base + cost reducer — mid-chain shape.
    for _ in range(3):
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "battlefield")
    _add_to_zone(game, card_db, "Ruby Medallion", attacker_idx, "battlefield")

    # Defender holds a cheap conditional counter (1-CMC hard-paid)
    # AND a hard counter.  The cheap one is the audit's leak: its
    # printed CMC matches the pitch-counter cost threshold.
    _add_to_zone(game, card_db, "Spell Pierce", defender_idx, "hand")
    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    if payoff_reachable:
        # Payoff inferable in opp hand (BHI-visible) and reachable
        # in library — chain-payoff accessors present.
        _add_to_zone(game, card_db, "Past in Flames", attacker_idx, "hand")
        _add_to_zone(game, card_db, "Wish", attacker_idx, "hand")
        _add_to_zone(game, card_db, "Grapeshot", attacker_idx, "library")
    else:
        # Fuel-only pool: no payoff accessor anywhere — the cantrip
        # on the stack is the only way the chain finds a payoff.
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "hand")
    _add_to_zone(game, card_db, "Manamorphose", attacker_idx, "hand")
    for _ in range(20):
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "library")

    # Mid-chain state: ≥1 ritual already cast this turn.
    game.players[attacker_idx].spells_cast_this_turn = 2
    game.players[attacker_idx].deck_name = "Ruby Storm"
    game.players[defender_idx].deck_name = "Dimir Midrange"

    # Fuel-tagged cantrip on the stack.
    _put_on_stack(game, card_db, "Wrenn's Resolve", attacker_idx)
    item = game.stack.items[-1]
    return game, item, defender_idx


# ─────────────────────────────────────────────────────────────────────
# Primary rule — cheap hard-paid counter is NOT exempt from the hold
# ─────────────────────────────────────────────────────────────────────


def test_cheap_hard_paid_counter_held_through_chain_fuel(card_db):
    """Micro-audit A4 (M1 G1 T6): defender fired a 1-mana hard-paid
    conditional counter on a 4-of chain cantrip while the payoff was
    still reachable.  The chain-fuel hold (bottleneck_probability ==
    0.0) must apply to hard-paid counters of ANY printed cost — only
    genuine pitch counters (mana-free alternative cost) are exempt.

    Pre-fix: the exemption compared `_effective_counter_cost` (which
    returns printed CMC for hard-paid counters) against the pitch
    cost threshold, so any 1-CMC hard counter unlocked the entire
    firing flow.
    """
    game, item, defender_idx = _mid_chain_game(card_db, payoff_reachable=True)

    decider = _make_decider(defender_idx)
    decider.opp_archetype = "combo"
    result = decider.decide_response(game, item)

    assert result is None, (
        "Cheap hard-paid counter must be HELD against chain fuel when "
        "the opp's chain payoff is still reachable.  A 1-mana "
        "conditional counter is at its best against the payoff (opp "
        "tapped out mid-chain); burning it on a cantrip strands the "
        "defender against the payoff.  "
        f"Got: {result[0].name if result else 'None'}."
    )
    reason = (decider.last_decision or {}).get("chosen", {}).get("reason", "")
    assert "chain" in reason.lower(), (
        "The hold must be recorded as a chain-fuel hold so replay "
        "audits can verify the reasoning (A4 found NO such reasoning "
        f"in the NDJSON).  Reason recorded: {reason!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# Negative rule — fuel that IS the bottleneck may be countered
# ─────────────────────────────────────────────────────────────────────


def test_counter_may_fire_on_fuel_when_fuel_is_the_bottleneck(card_db):
    """Same mid-chain shape, but the opp pool contains NO payoff
    accessor (hand and library are fuel/land only).  The fuel spell
    on the stack is then the only route to a payoff — countering it
    is legitimate.  bottleneck_probability returns NaN (no
    payoff-reachable chain state), so the legacy threat gate decides;
    the decision must NOT be recorded as a chain-fuel hold.
    """
    game, item, defender_idx = _mid_chain_game(card_db, payoff_reachable=False)

    decider = _make_decider(defender_idx)
    decider.opp_archetype = "combo"
    result = decider.decide_response(game, item)

    if result is None:
        reason = (decider.last_decision or {}).get(
            "chosen", {}
        ).get("reason", "")
        assert "chain" not in reason.lower(), (
            "With no payoff reachable anywhere in the opp pool, the "
            "chain-fuel hold must not trigger — the fuel spell is the "
            f"bottleneck.  Reason recorded: {reason!r}"
        )


# ─────────────────────────────────────────────────────────────────────
# Exemption pin — genuine pitch counters keep their free firing path
# ─────────────────────────────────────────────────────────────────────


def test_pitch_counter_retains_free_firing_path_on_chain_fuel(card_db):
    """A genuine pitch counter (mana-free alternative cost on the
    opponent's turn) keeps its documented exemption from the
    chain-fuel hold: its opportunity cost is one pitched card and no
    mana, so trading it for fuel does not strand the defender's mana
    against the payoff.

    Pin: the hold must not short-circuit to a chain-fuel pass when
    the defender's only counters include a castable pitch counter.
    (Whether the pitch counter then FIRES is the legacy gate's call —
    this test only pins that the chain-hold exemption is classified
    by the pitch mechanic, not by printed cost.)
    """
    game, item, defender_idx = _mid_chain_game(card_db, payoff_reachable=True)
    defender = game.players[defender_idx]
    # Swap the cheap hard counter for a genuine pitch counter + a
    # blue card to pitch.  Keep the hard counter in hand.
    defender.hand = [c for c in defender.hand if c.template.cmc != 1]
    _add_to_zone(game, card_db, "Force of Negation", defender_idx, "hand")
    _add_to_zone(game, card_db, "Consider", defender_idx, "hand")

    decider = _make_decider(defender_idx)
    decider.opp_archetype = "combo"
    decider.decide_response(game, item)

    reason = (decider.last_decision or {}).get("chosen", {}).get("reason", "")
    assert "chain-fuel hold" not in reason.lower(), (
        "A castable pitch counter exempts the decision from the "
        "chain-fuel short-circuit (documented near-zero opportunity "
        f"cost).  Reason recorded: {reason!r}"
    )
