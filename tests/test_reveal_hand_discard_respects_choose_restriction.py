"""A "reveal hand, you choose a card, that player discards it" effect
must honor the restriction its own choose-clause states.

The class ("Target player reveals their hand. You choose a [restricted]
card from it. That player discards that card.") is large in Modern —
Inquisition of Kozilek, Despise, Divest, Duress, Distress, Harsh
Scrutiny, ... — and the restriction differs per card:

    Inquisition of Kozilek: "a nonland card ... with mana value 3 or less"
    Despise:                "a creature or planeswalker card"
    Divest:                 "an artifact or creature card"
    Duress:                 "a noncreature, nonland card"
    Thoughtseize:           "a nonland card"          (no restriction)

The generic resolver picked ``max(nonlands, key=cmc)`` — the single
highest-mana-value nonland — honoring NEITHER the mana-value cap NOR
the card-type restriction. Inquisition of Kozilek (Goryo's Vengeance
mainboard) could therefore discard a 6-mana bomb its own text forbids
it from taking (cap is mana value 3), and Duress/Despise/Divest could
take a card type they may not.

Same over-match/under-filter class as the mana-value-capped
reanimation bug — the resolver reads the card's whole shape but drops
the clause's stated restriction. Card names below are fixture carriers
for the oracle shapes; the rule under test is the mechanic.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState


def _hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _resolve(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    spell = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="stack",
    )
    spell._game_state = game
    from engine.oracle_resolver import resolve_spell_from_oracle
    return resolve_spell_from_oracle(game, spell, controller)


def test_mana_value_capped_discard_cannot_take_a_card_above_the_cap(card_db):
    """Inquisition of Kozilek ('a nonland card with mana value 3 or
    less') must not discard a 6-mana card when a legal in-cap target
    exists."""
    game = GameState(rng=random.Random(0))
    # Opponent (player 1) hand: a big bomb the cap forbids + a legal target.
    big = _hand(game, card_db, "Emrakul, the Aeons Torn", 1)   # MV 15
    legal = _hand(game, card_db, "Ragavan, Nimble Pilferer", 1)  # MV 1

    _resolve(game, card_db, "Inquisition of Kozilek", 0)

    discarded = [c.name for c in game.players[1].graveyard]
    assert "Emrakul, the Aeons Torn" not in discarded, (
        f"Inquisition (cap mana value 3) discarded an above-cap card. "
        f"Graveyard: {discarded}"
    )
    assert "Ragavan, Nimble Pilferer" in discarded, (
        f"Inquisition had a legal in-cap target and took nothing / the "
        f"wrong card. Graveyard: {discarded}"
    )
    assert big in game.players[1].hand, (
        "the above-cap card must remain in hand"
    )


def test_mana_value_capped_discard_is_a_no_op_when_only_above_cap_cards_exist(card_db):
    game = GameState(rng=random.Random(0))
    big = _hand(game, card_db, "Emrakul, the Aeons Torn", 1)  # MV 15, only card

    _resolve(game, card_db, "Inquisition of Kozilek", 0)

    assert big in game.players[1].hand, (
        "no legal in-cap target existed; nothing may be discarded"
    )
    assert game.players[1].graveyard == []


def test_type_restricted_discard_only_takes_an_allowed_type(card_db):
    """Duress ('a noncreature, nonland card') must not take a creature
    even when the creature is the highest-mana-value nonland-ish pick."""
    game = GameState(rng=random.Random(0))
    creature = _hand(game, card_db, "Griselbrand", 1)          # MV 8 creature
    spell = _hand(game, card_db, "Lightning Bolt", 1)          # MV 1 instant

    _resolve(game, card_db, "Duress", 0)

    discarded = [c.name for c in game.players[1].graveyard]
    assert "Griselbrand" not in discarded, (
        f"Duress (noncreature only) discarded a creature. GY: {discarded}"
    )
    assert "Lightning Bolt" in discarded, (
        f"Duress must take the noncreature spell. GY: {discarded}"
    )
    assert creature in game.players[1].hand


def test_unrestricted_discard_still_takes_the_highest_value_card(card_db):
    """Regression anchor: Thoughtseize / a plain 'nonland card' discard
    keeps the existing best-card heuristic (highest mana value)."""
    game = GameState(rng=random.Random(0))
    _hand(game, card_db, "Lightning Bolt", 1)         # MV 1
    bomb = _hand(game, card_db, "Griselbrand", 1)      # MV 8

    _resolve(game, card_db, "Thoughtseize", 0)

    discarded = [c.name for c in game.players[1].graveyard]
    assert "Griselbrand" in discarded, (
        f"unrestricted discard must still take the highest-value card. "
        f"GY: {discarded}"
    )
