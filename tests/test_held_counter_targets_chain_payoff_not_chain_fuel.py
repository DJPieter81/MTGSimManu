"""Counter held against chain-fuel when the chain payoff is coming.

Rule (mechanic-phrased, no card names in the rule):

    When the spell on the stack is CHAIN FUEL (cantrip / ritual that
    extends an opponent's combo chain) AND the opponent's chain state
    indicates a payoff is reachable (their hand or library carries the
    chain-payoff signature inferred via BHI) AND we hold exactly one
    counter on the chain-defending stack, the counter must be RESERVED
    for the chain payoff — not burned on the fuel.  The fuel resolves;
    the chain grows by one; the counter remains in hand to answer the
    payoff (Past in Flames / Wish / Grapeshot / Empty the Warrens /
    any other STORM_PAYOFF-tagged finisher).

    Class size: this rule applies to every combo deck the opp may
    bring (Storm, Living End, Goryo's Vengeance, Cascade, Amulet
    Titan, Through the Breach, any future Modern combo).  >> 10
    cards from the universe.  Symmetric across counter-density
    decks (Azorius Control, Dimir Midrange, Jeskai Control,
    4/5c Control, Izzet Murktide).  ~30 of 16×16 matchups directly
    affected.

Audit smoking gun (Bo3 panel 2026-05-16, G2 T4):
    Azorius vs Ruby Storm, Storm cast Wrenn's Resolve (cantrip, a
    4-of in the deck) on T4.  Azorius fired Counterspell.  Storm
    chained Past in Flames + Wish + Grapeshot on the next turn for
    lethal.  The counter was burned on a 1-mana cantrip; the
    payoff resolved unchallenged.

Lift-check (one OTHER deck per CLAUDE.md):
    Dimir Midrange vs Living End / Cascade — Dimir's 2 Counterspells
    must be held for the actual Living End / Crashing Footfalls
    payoff, not burned on Shardless Agent / Crashing Footfalls
    cascade-fuel cards.  Same rule, different mechanic family.
"""
from __future__ import annotations

import random

import pytest

from ai.response import ResponseDecider
from ai.turn_planner import TurnPlanner
from engine.card_database import CardDatabase
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


# ─────────────────────────────────────────────────────────────────────
# Primary rule — counter HELD against chain-fuel when payoff coming
# ─────────────────────────────────────────────────────────────────────


def test_counter_held_against_chain_fuel_when_payoff_coming(card_db):
    """Reproduces the audit's G2T4 smoking gun.

    Setup mirrors Azorius vs Ruby Storm:
      - Defender (Azorius) has UU available (2 Islands).
      - Defender hand: exactly one Counterspell.
      - Attacker (Storm) is in mid-chain on opp's turn (active=opp):
        opp.spells_cast_this_turn == 2 (one ritual already resolved
        plus the cantrip being cast), Ruby Medallion in play (a
        cost-reducer permanent that screams "combo chain in flight"),
        deck_name = 'Ruby Storm' (the gameplan archetype = 'combo'
        is what BHI reads).
      - Attacker hand: contains Past in Flames + Wish — both
        STORM_PAYOFF-class finishers that BHI's prior on the opp
        library / hand surfaces as "payoff reachable."
      - Attacker stack item: 'Wrenn's Resolve' — pure CHAIN_FUEL
        (cantrip), no payoff value of its own.

    Expected: decide_response returns None — the counter is held.
    Pre-fix: decide_response burns Counterspell on Wrenn's Resolve
    because evaluate_stack_threat scores the cantrip via its
    card-draw component, exceeding the LOW gate at cheap-counter
    cost.

    Rule under test: when stack item is chain-fuel AND opp chain
    state signals payoff reachable, the counter is held.
    """
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    game.priority_player = defender_idx
    game.turn_number = 4

    # Defender mana base — 2 Islands (UU for Counterspell).
    for _ in range(2):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")

    # Attacker mana base + cost reducer — mid-chain shape.
    for _ in range(3):
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "battlefield")
    _add_to_zone(
        game, card_db, "Ruby Medallion", attacker_idx, "battlefield"
    )

    # Defender has exactly one Counterspell — burning it on fuel
    # strands the deck against the payoff.
    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    # Attacker hand carries the chain payoff (Past in Flames,
    # Wish) so BHI's payoff-in-hand prior is non-zero.
    _add_to_zone(game, card_db, "Past in Flames", attacker_idx, "hand")
    _add_to_zone(game, card_db, "Wish", attacker_idx, "hand")
    # Plus two more cards to make a believable 4-card hand.
    _add_to_zone(game, card_db, "Mountain", attacker_idx, "hand")
    _add_to_zone(game, card_db, "Manamorphose", attacker_idx, "hand")

    # Attacker library carries Grapeshot (STORM_PAYOFF) so the
    # tutor (Wish) target is reachable.
    for _ in range(20):
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "library")
    _add_to_zone(game, card_db, "Grapeshot", attacker_idx, "library")

    # Mid-chain state — spells already cast this turn signals chain
    # in flight.  The rule reads `spells_cast_this_turn > 0` AS the
    # primary chain-in-flight signal (alongside the chain-fuel
    # permanent on the battlefield).
    game.players[attacker_idx].spells_cast_this_turn = 2
    game.players[attacker_idx].deck_name = "Ruby Storm"
    game.players[defender_idx].deck_name = "Azorius Control"

    # Stack item — Wrenn's Resolve, a pure CHAIN_FUEL cantrip.
    _put_on_stack(game, card_db, "Wrenn's Resolve", attacker_idx)
    item = game.stack.items[-1]

    decider = _make_decider(defender_idx)
    # Mark the opp archetype on the decider so BHI / projection
    # treat opp as 'combo' — mirrors what the live game runner does
    # (ev_player.py:_get_opp_profile lookup).
    decider.opp_archetype = "combo"

    result = decider.decide_response(game, item)

    assert result is None, (
        "Counter must be HELD against chain fuel when opp's chain "
        "state signals payoff reachable.  Burning Counterspell on a "
        "1-mana cantrip strands the deck against the upcoming Past "
        "in Flames / Wish / Grapeshot chain (audit G2T4 smoking gun). "
        f"Got: {result[0].name if result else 'None'}."
    )


# ─────────────────────────────────────────────────────────────────────
# Secondary rule — counter FIRES on the actual STORM_PAYOFF
# ─────────────────────────────────────────────────────────────────────


def test_counter_fires_on_storm_payoff(card_db):
    """Same chain-in-flight state, but now opp casts the actual
    storm-payoff card (Grapeshot).  The counter MUST fire — the
    payoff is the bottleneck of the chain; countering it ends the
    threat.

    Regression guard: the held-counter rule above must not trigger
    when the stack item IS the payoff.  STORM_PAYOFF tag is the
    primary discriminator between fuel and payoff.
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
    _add_to_zone(
        game, card_db, "Ruby Medallion", attacker_idx, "battlefield"
    )

    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    # Attacker hand has fuel only — payoff is on the stack.
    _add_to_zone(game, card_db, "Manamorphose", attacker_idx, "hand")
    _add_to_zone(game, card_db, "Mountain", attacker_idx, "hand")

    # Mid-chain — storm count high (this is what makes Grapeshot
    # lethal).
    game.players[attacker_idx].spells_cast_this_turn = 6
    game.players[attacker_idx].deck_name = "Ruby Storm"
    game.players[defender_idx].deck_name = "Azorius Control"

    # Grapeshot — the STORM_PAYOFF spell.
    _put_on_stack(game, card_db, "Grapeshot", attacker_idx)
    item = game.stack.items[-1]

    decider = _make_decider(defender_idx)
    decider.opp_archetype = "combo"
    result = decider.decide_response(game, item)

    assert result is not None, (
        "Counter must FIRE on the storm payoff (Grapeshot).  The "
        "payoff is the bottleneck of the chain — countering it ends "
        "the threat.  Holding the counter here loses the game."
    )
    chosen, targets = result
    assert chosen.name == "Counterspell", (
        f"Expected Counterspell on Grapeshot; got {chosen.name}."
    )
    assert item.source.instance_id in targets, (
        "Counterspell must target the Grapeshot stack item."
    )


# ─────────────────────────────────────────────────────────────────────
# Regression — default behaviour preserved when no chain state
# ─────────────────────────────────────────────────────────────────────


def test_counter_fires_when_no_chain_state(card_db):
    """Aggro opp casts a creature; storm_count=0, no chain-fuel
    permanent in play, archetype=aggro.  Chain-aware logic must NOT
    fire — fall back to legacy behaviour (counter fires on the threat
    if it clears the gate).

    Regression anchor: the chain-aware suppression must trigger ONLY
    when the chain-state signal is positive.  Otherwise it would
    bleed into every counterspell decision and break the existing
    counter-vs-threat balance documented in
    test_counter_fires_on_cost_reduced_threat.py and the field WR
    against aggro decks.
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

    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    # Attacker hand has no payoff; archetype is aggro.
    _add_to_zone(game, card_db, "Mountain", attacker_idx, "hand")

    # No chain state — fresh turn, no fuel permanent.
    game.players[attacker_idx].spells_cast_this_turn = 0
    game.players[attacker_idx].deck_name = "Domain Zoo"
    game.players[defender_idx].deck_name = "Azorius Control"

    # Aggro casts a real threat (Goblin Guide-class creature).
    _put_on_stack(game, card_db, "Monastery Swiftspear", attacker_idx)
    item = game.stack.items[-1]

    decider = _make_decider(defender_idx)
    decider.opp_archetype = "aggro"
    result = decider.decide_response(game, item)

    # The chain-aware suppression must not block this counter.  The
    # legacy decision (whether to fire or not) depends on the
    # triage / gate logic which is exercised by other tests; this
    # test only asserts that the chain-aware rule does NOT short-
    # circuit to None.  Either fires Counterspell or returns None
    # for a non-chain reason (post-resolution removal triage) —
    # both are valid; what is INVALID is "held because of chain
    # state when there is no chain."  We assert that by checking the
    # decision was NOT recorded as a chain-fuel hold.
    if result is None:
        # If the decider returned None it must be for a non-chain
        # reason.  The `last_decision` summary names the reason.
        reason = (decider.last_decision or {}).get(
            "chosen", {}
        ).get("reason", "")
        assert "chain" not in reason.lower(), (
            "Aggro-vs-aggro decision must not be classified as a "
            "chain-fuel hold.  Reason returned: " + reason
        )
