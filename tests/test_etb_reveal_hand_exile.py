"""An ETB "target opponent reveals their hand, you choose a nonland
card and exile it" trigger must actually strip that card — and the
"until this creature leaves" / "return the exiled card" variants must
give it back when the source leaves.

Rule: hand disruption on a body (CR 603 ETB trigger) is the same
"reveal, choose under a restriction, remove" mechanic as the sorcery
form (Thoughtseize/Inquisition) — it just triggers from the
battlefield instead of the stack. The generic reveal-hand chooser
existed only on the spell/cast path, so from an ETB trigger it was a
silent no-op: the opponent kept every card (audit: Eldrazi Tron vs
Izzet Prowess, s55613 — Thought-Knot Seer cast four times, zero hand
disruption).

Two sub-shapes share one mechanic:
  - permanent exile + a separate "opponent draws" LTB (Thought-Knot
    Seer): the card stays exiled.
  - linked exile "until this creature leaves the battlefield" /
    "return the exiled card to its owner's hand" (Kitesail Freebooter,
    Tidehollow Sculler, Brain Maggot): the card returns on LTB.

Card names are fixture carriers; the rule is the reveal-choose-exile
ETB and its linked return.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.oracle_resolver import resolve_etb_from_oracle, resolve_dies_trigger


def _mk(game, card_db, name, owner, zone):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    return CardInstance(template=t, owner=owner, controller=owner,
                        instance_id=game.next_instance_id(), zone=zone)


def _stock_opp_hand(game, card_db, owner=1):
    """Give the opponent a hand with a land and two nonland cards."""
    hand = [
        _mk(game, card_db, "Memnite", owner, "hand"),       # creature, MV0
        _mk(game, card_db, "Lightning Bolt", owner, "hand"),  # instant, MV1
        _mk(game, card_db, "Island", owner, "hand"),         # land
    ]
    game.players[owner].hand = hand
    return hand


def test_etb_reveal_hand_exiles_a_nonland_card(card_db):
    game = GameState(rng=random.Random(0))
    _stock_opp_hand(game, card_db)
    seer = _mk(game, card_db, "Thought-Knot Seer", 0, "battlefield")
    seer._game_state = game
    game.players[0].battlefield.append(seer)

    before = len(game.players[1].hand)
    fired = resolve_etb_from_oracle(game, seer, 0)

    assert fired, "the ETB reveal-hand-exile branch did not fire"
    assert len(game.players[1].hand) == before - 1, (
        "ETB must exile exactly one card from the opponent's hand"
    )
    # The land is never a legal choice; a nonland card was taken.
    assert any(c.name == "Island" for c in game.players[1].hand), (
        "a land must never be the exiled card"
    )
    assert len(game.players[1].exile) == 1, "the chosen card goes to exile"


def test_permanent_exile_shape_does_not_return_card_on_leave(card_db):
    """Thought-Knot Seer exiles permanently (its LTB lets the opponent
    draw instead) — the exiled card must NOT come back on LTB."""
    game = GameState(rng=random.Random(0))
    _stock_opp_hand(game, card_db)
    seer = _mk(game, card_db, "Thought-Knot Seer", 0, "battlefield")
    seer._game_state = game
    game.players[0].battlefield.append(seer)
    resolve_etb_from_oracle(game, seer, 0)
    exiled_name = game.players[1].exile[0].name

    resolve_dies_trigger(game, seer, 0)

    assert not any(c.name == exiled_name for c in game.players[1].hand), (
        "a permanent-exile ETB must not return the card when it leaves"
    )


def test_linked_exile_returns_card_when_source_leaves(card_db):
    """Kitesail Freebooter / Tidehollow Sculler exile 'until this
    creature leaves the battlefield' — the card returns on LTB."""
    game = GameState(rng=random.Random(0))
    _stock_opp_hand(game, card_db)
    src = card_db.get_card("Kitesail Freebooter") or card_db.get_card("Tidehollow Sculler")
    if src is None:
        pytest.skip("no linked-exile hand-disruptor in this DB")
    body = CardInstance(template=src, owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="battlefield")
    body._game_state = game
    game.players[0].battlefield.append(body)
    resolve_etb_from_oracle(game, body, 0)
    assert len(game.players[1].exile) == 1
    exiled_name = game.players[1].exile[0].name

    resolve_dies_trigger(game, body, 0)

    assert any(c.name == exiled_name for c in game.players[1].hand), (
        "a 'until this creature leaves' exile must return the card to its "
        "owner's hand when the source leaves the battlefield"
    )
    assert not game.players[1].exile, "the card left exile on return"
