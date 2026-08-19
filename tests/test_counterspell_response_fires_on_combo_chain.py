"""Counter held against mid-chain non-fuel non-payoff enablers.

Rule (mechanic-phrased, no card names in the rule):

    When the spell on the stack is NEITHER chain fuel (ritual / cantrip /
    draw / card_advantage) NOR the chain payoff (storm_payoff / tutor /
    flashback+combo), but the opponent's chain is MID-CAST (≥
    CHAIN_MIDCAST_MIN_STORM_COUNT spells this turn) AND a payoff is
    reachable (hand / library carries a payoff-accessor card),
    the counter must be RESERVED for the payoff — not burned on the
    non-fuel non-payoff enabler.

    Class size: Ruby Medallion, Goblin Electromancer, Birgi (God of
    Stories), Baral (Chief of Compliance), Primal Amulet, Storm-Kiln
    Artist, Arcane Laboratory, Goblin Chainwhirler, Immolating Gyre,
    Pyromancer's Goggles, Spirebluff Canal (bounce land), and any future
    cost-reducer permanent cast mid-chain — all resolve to tags that
    include 'cost_reducer' but NOT the CHAIN_FUEL_TAGS set (ritual /
    cantrip / draw / card_advantage).  bottleneck_probability returns
    float('nan') for all of these, and the legacy evaluator's
    is_cost_reducer component scores them at ~25+ threat (above the
    counter gate HIGH threshold), causing the counter to fire wastefully.
    >> 10 cards.

    The fix is a NaN-branch guard in ai/response.decide_response that
    detects the active-chain NaN case (opp_combo_chain_active) and holds
    the counter when threat < NEAR_LETHAL_CUTOFF and no pitch counter is
    available.

Audit root cause:
    Ruby Storm vs 4/5c Control / Azorius: control AI had Counterspell
    in hand during Storm's combo turn (spells_cast_this_turn ≥ 2, Ruby
    Medallion on battlefield, Past in Flames in hand).  Storm cast a
    second Ruby Medallion mid-chain.  bottleneck_probability(Medallion)
    = NaN → legacy gate → evaluate_stack_threat returned ~25+ (above
    COUNTER_GATE_HIGH ≈ 1.5) → Counterspell fired on the Medallion.
    Storm then cast Past in Flames + Wish + Grapeshot unchallenged.

Lift-check (another deck that benefits per CLAUDE.md):
    Dimir Midrange vs Ruby Storm / Living End: Dimir's Counterspells
    must be reserved for the storm payoff or cascade finisher, not
    burned on a Goblin Electromancer deploying mid-chain.  Same rule,
    different archetype.
"""
from __future__ import annotations

import random

import pytest

from ai.response import ResponseDecider
from ai.turn_planner import TurnPlanner
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


# ─────────────────────────────────────────────────────────────────────
# Helpers (mirrors test_held_counter_targets_chain_payoff_not_chain_fuel)
# ─────────────────────────────────────────────────────────────────────


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


# ─────────────────────────────────────────────────────────────────────
# PRIMARY FAILING TEST — counter held against mid-chain non-fuel enabler
# ─────────────────────────────────────────────────────────────────────


def test_counter_held_against_mid_chain_nonfuel_enabler_when_payoff_reachable(card_db):
    """Counter HELD when a non-fuel non-payoff permanent is cast mid-combo-chain.

    Rule under test: when bottleneck_probability returns NaN (stack spell
    is neither CHAIN_FUEL nor payoff_accessor) but the opponent's chain is
    mid-cast (spells_cast_this_turn >= CHAIN_MIDCAST_MIN_STORM_COUNT) AND
    payoff is reachable (opp hand/library carries a payoff_accessor card),
    the counter must be held for the payoff.

    Pre-fix: decide_response fires Counterspell on the cost-reducer
    permanent because evaluate_stack_threat assigns ~25+ via the
    is_cost_reducer component, exceeding COUNTER_GATE_HIGH ≈ 1.5.

    Post-fix: the NaN mid-chain guard detects opp_combo_chain_active and
    holds the counter (returns None) when threat < NEAR_LETHAL_CUTOFF.

    Setup:
      - Defender (control): 1 Counterspell in hand, 2 Islands untapped.
      - Attacker (storm): Ruby Medallion on battlefield (chain-in-flight),
        spells_cast_this_turn = 2 (mid-cast), Past in Flames in hand
        (payoff_accessor: flashback+combo tags → payoff reachable).
      - Stack: Ruby Medallion (2nd copy) — cost_reducer tag, NOT in
        CHAIN_FUEL_TAGS, NOT payoff_accessor → bp = NaN.
    """
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    game.priority_player = defender_idx
    game.turn_number = 4

    # Defender mana: 2 Islands → UU available for Counterspell.
    for _ in range(2):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")

    # Attacker mana base + deployed cost reducer (chain-in-flight signal).
    for _ in range(3):
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "battlefield")
    _add_to_zone(game, card_db, "Ruby Medallion", attacker_idx, "battlefield")

    # Defender: exactly one Counterspell — burning it on the enabler
    # strands the deck against the upcoming Past in Flames.
    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    # Attacker hand: Past in Flames makes payoff_reachable=True.
    # Past in Flames has flashback+combo tags → is_chain_payoff_accessor.
    _add_to_zone(game, card_db, "Past in Flames", attacker_idx, "hand")
    _add_to_zone(game, card_db, "Manamorphose", attacker_idx, "hand")

    # Library: Grapeshot (storm_payoff) confirms a finisher is reachable.
    for _ in range(20):
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "library")
    _add_to_zone(game, card_db, "Grapeshot", attacker_idx, "library")

    # Mid-chain state: 2 spells already cast this turn.
    game.players[attacker_idx].spells_cast_this_turn = 2
    game.players[attacker_idx].deck_name = "Ruby Storm"
    game.players[defender_idx].deck_name = "Azorius Control"

    # Stack item: Ruby Medallion (cost_reducer tag, NOT ritual/cantrip/draw →
    # not chain fuel; NOT storm_payoff/tutor/flashback+combo → not payoff
    # accessor).  bottleneck_probability must return float('nan') for this.
    _put_on_stack(game, card_db, "Ruby Medallion", attacker_idx)
    item = game.stack.items[-1]

    decider = _make_decider(defender_idx)
    decider.opp_archetype = "combo"

    result = decider.decide_response(game, item)

    assert result is None, (
        "Counter must be HELD against a non-fuel non-payoff mid-chain "
        "enabler (cost-reducer class) when opp chain is mid-cast and "
        "payoff is reachable.  bottleneck_probability returns NaN for "
        "Ruby Medallion; the NaN mid-chain guard must detect "
        "opp_combo_chain_active and hold the counter for the upcoming "
        "Past in Flames payoff.  "
        f"Got: {result[0].name if result else 'None'}."
    )


# ─────────────────────────────────────────────────────────────────────
# REGRESSION — counter still fires on the actual payoff accessor
# ─────────────────────────────────────────────────────────────────────


def test_counter_fires_on_payoff_accessor_not_suppressed_by_nan_guard(card_db):
    """Counter fires on the chain payoff even when NaN guard is present.

    Regression guard: the NaN mid-chain guard (new in this diff) must
    not suppress counters when the stack item IS a payoff_accessor.
    Past in Flames has flashback+combo tags → is_chain_payoff_accessor
    → bottleneck_probability = 1.0 → threat = LETHAL_THREAT → counter
    fires.  The NaN guard is never reached for bp == 1.0 spells.

    Rule: NaN hold applies ONLY when bp is NaN (non-fuel non-payoff).
    Payoff-accessor spells (bp == 1.0) must still draw the counter.
    """
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    game.priority_player = defender_idx
    game.turn_number = 4

    for _ in range(2):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")

    for _ in range(3):
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "battlefield")
    _add_to_zone(game, card_db, "Ruby Medallion", attacker_idx, "battlefield")

    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    # Attacker hand has fuel + Grapeshot (payoff in hand, not on stack).
    _add_to_zone(game, card_db, "Manamorphose", attacker_idx, "hand")
    _add_to_zone(game, card_db, "Grapeshot", attacker_idx, "hand")
    for _ in range(20):
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "library")

    game.players[attacker_idx].spells_cast_this_turn = 2
    game.players[attacker_idx].deck_name = "Ruby Storm"
    game.players[defender_idx].deck_name = "Azorius Control"

    # Stack item: Past in Flames — flashback+combo tags → payoff_accessor
    # → bottleneck_probability = 1.0 → threat lifted to LETHAL_THREAT.
    _put_on_stack(game, card_db, "Past in Flames", attacker_idx)
    item = game.stack.items[-1]

    decider = _make_decider(defender_idx)
    decider.opp_archetype = "combo"

    result = decider.decide_response(game, item)

    assert result is not None, (
        "Counter must FIRE on Past in Flames (payoff_accessor: "
        "flashback+combo → bottleneck_probability=1.0 → "
        "threat=LETHAL_THREAT).  The NaN mid-chain guard must not "
        "suppress counters against bp==1.0 payoff spells."
    )
    chosen, _ = result
    assert "counterspell" in chosen.template.tags, (
        f"Expected a counterspell on Past in Flames; got {chosen.name} "
        f"with tags {chosen.template.tags}."
    )
