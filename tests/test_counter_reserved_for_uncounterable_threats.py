"""Counter triage — reserve counters for threats only counters can answer.

Rule (mechanic-phrased):
    A counterspell against a CREATURE spell should be downgraded when
    the defender holds a flash-speed creature-exile in hand.  The
    creature can be answered post-resolution by the flash exile (a
    1-mana / 2-card commitment), preserving the counter for non-
    creature threats (artifacts, sorceries, planeswalkers) that the
    creature-exile cannot touch.

    Class size: applies to any pairing of (counterspell, flash-speed
    creature-exile) — Counterspell + Solitude / Subtlety / Endurance /
    Grief / Fury / Otawara / Path to Exile / Swords to Plowshares /
    March of Otherworldly Light / Generous Visitor.  >> 10 cards.

This test reproduces the AzCon vs Affinity bottleneck (replays/
azorius_control_vs_affinity_s60120.txt and seed 50001):

    T3 — Affinity casts Sojourner's Companion.  AzCon counters with
    Counterspell, burning the only Plating answer in the deck.
    T4 — Affinity casts Cranial Plating.  AzCon has Solitude in hand
    but no counter.  Solitude exiles a Construct token instead of the
    Plating-bearing Memnite (carriers were not yet attached).  Plating
    resolves, equips Memnite for 10/1, lethal.

The fix preserves the counter at T3 by triaging on flexibility:
since Solitude can answer Sojourner's post-resolution, the counter
stays in hand and is available at T4 for Plating.
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


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


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
# Primary failing test — counter is reserved for non-creature threats
# ─────────────────────────────────────────────────────────────────────


def test_counter_reserved_when_flash_exile_can_handle_creature(card_db):
    """Defender has both Counterspell and Solitude; opp casts a creature
    spell.  Defender should NOT counter — Solitude can exile it post-
    resolution, preserving the counter for non-creature threats.

    Setup mirrors AzCon T3 vs Affinity Sojourner's Companion:
      - Defender has 4 islands + a plains (UU + WW available).
      - Hand: Counterspell, Solitude, Orim's Chant (white pitch fuel).
      - Opp has a creature on board (so Solitude has a valid evoke
        target — `can_cast` requires it for evoke gating).
      - Opp casts a 4-power creature on the stack.

    Expected: decide_response either returns None (pass, plan to use
    Solitude post-resolution) OR returns a non-Counterspell response.
    Currently fails: AzCon fires Counterspell.
    """
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx  # opp's turn — we are responding

    # Defender mana base — 2 islands + 2 plains (cover both Counterspell
    # UU and Solitude evoke pitch path).
    for _ in range(2):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    for _ in range(2):
        _add_to_zone(game, card_db, "Plains", defender_idx, "battlefield")

    # Opp has a creature on board so Solitude has a legal evoke target.
    # (Without this, can_cast filters Solitude out of `instants` because
    # evoke gates on opp.creatures being non-empty.)
    _add_to_zone(game, card_db, "Memnite", attacker_idx, "battlefield")

    # Hand: counter + flash-creature-exile + a white card to pitch for evoke.
    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")
    _add_to_zone(game, card_db, "Solitude", defender_idx, "hand")
    _add_to_zone(game, card_db, "Orim's Chant", defender_idx, "hand")

    # Opp casts Sojourner's Companion (4/4 creature, CMC 7 printed but
    # affinity-reduced in real games — for the test, we just need a
    # creature on the stack with non-trivial threat).
    _put_on_stack(game, card_db, "Sojourner's Companion", attacker_idx)
    item = game.stack.items[-1]

    decider = _make_decider(defender_idx)
    result = decider.decide_response(game, item)

    if result is None:
        # Acceptable: pass on the cast, plan to handle post-resolution
        # with Solitude.  Counterspell is preserved.
        return

    chosen, _targets = result
    assert chosen.name != "Counterspell", (
        "Counter must be reserved when a flash creature-exile is "
        "available to answer the same threat post-resolution. "
        "Burning Counterspell here strands the deck against later "
        "non-creature threats (artifacts, planeswalkers, sorceries) "
        f"that the creature-exile cannot touch.  Chose: {chosen.name}."
    )


# ─────────────────────────────────────────────────────────────────────
# Regression: when Solitude is NOT in hand, Counterspell still fires
# ─────────────────────────────────────────────────────────────────────


def test_counter_still_fires_without_flash_exile(card_db):
    """Without Solitude (or any flash creature-exile) in hand, the
    counter is the only answer to a creature threat — it must still
    fire.  Regression guard: don't break the legacy behaviour.
    """
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx

    for _ in range(2):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    for _ in range(2):
        _add_to_zone(game, card_db, "Plains", defender_idx, "battlefield")

    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    _put_on_stack(game, card_db, "Sojourner's Companion", attacker_idx)
    item = game.stack.items[-1]

    decider = _make_decider(defender_idx)
    result = decider.decide_response(game, item)

    assert result is not None, (
        "Counterspell must still fire when no other answer to the "
        "creature threat exists in hand."
    )
    chosen, _ = result
    assert chosen.name == "Counterspell", (
        f"Expected Counterspell to fire (sole answer); got {chosen.name}."
    )


# ─────────────────────────────────────────────────────────────────────
# Regression: counter still fires against NON-CREATURE threats
# ─────────────────────────────────────────────────────────────────────


def test_counter_fires_against_non_creature_even_with_flash_exile(card_db):
    """Solitude in hand does NOT make us pass on Cranial Plating.
    Solitude cannot exile artifacts; counter is the ONLY answer.

    Regression guard: the new triage must apply only when the flash
    exile can actually answer the stack threat, not blanket-disable
    counters whenever flash exile is in hand.
    """
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx

    for _ in range(2):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    for _ in range(2):
        _add_to_zone(game, card_db, "Plains", defender_idx, "battlefield")

    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")
    _add_to_zone(game, card_db, "Solitude", defender_idx, "hand")
    _add_to_zone(game, card_db, "Orim's Chant", defender_idx, "hand")

    # Cranial Plating is an Equipment artifact — Solitude cannot exile it.
    # Counter is the only answer.
    _put_on_stack(game, card_db, "Cranial Plating", attacker_idx)
    item = game.stack.items[-1]

    decider = _make_decider(defender_idx)
    result = decider.decide_response(game, item)

    assert result is not None, (
        "Cranial Plating is an artifact — Solitude cannot answer it. "
        "Counter must fire."
    )
    chosen, _ = result
    assert chosen.name == "Counterspell", (
        f"Expected Counterspell against an artifact threat (the only "
        f"answer in hand); got {chosen.name}."
    )
