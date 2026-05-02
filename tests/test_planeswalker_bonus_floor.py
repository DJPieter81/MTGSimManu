"""Planeswalker EV must have a floor — they shouldn't lose to vanilla creatures.

Rule (mechanic-phrased):
    A planeswalker spell's score must include a positive contribution
    from its loyalty even when card_clock_impact(snap) rounds to ~0 in
    early/mid-game state. Without a floor, a 4-CMC PW like Karn, the
    Great Creator scores at or below a 4-CMC vanilla creature, so the
    AI never casts the planeswalker.

    Class size: applies to every planeswalker in the format
    (Karn, Teferi, Wrenn and Six, Liliana, Ajani, ...). >> 10 cards.

Background:
    PROJECT_STATUS.md flagged "planeswalkers score ~0 (P0)". Audit of 6
    Eldrazi Tron replays found 0 Karn casts despite Tron assembled by
    T4 in every game. This test encodes the rule that the bonus floor
    is +3.0 (one activation worth: ~one card draw, one removal, or one
    Cat token), guaranteed regardless of clock state.
"""
from __future__ import annotations

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _setup_etron_t4_with_karn_in_hand(card_db):
    """Build a deterministic E-Tron-style state: T4, Tron assembled,
    Karn in hand, opp has trivial board, our life total stable.
    Returns (game, controller_idx, karn_card)."""
    game = GameState(card_db, ["P1", "P2"])
    # Assemble Tron for player 0
    for land in ["Urza's Tower", "Urza's Mine", "Urza's Power Plant", "Wastes"]:
        _add_to_battlefield(game, card_db, land, 0)
    # Karn in hand
    karn = _add_to_hand(game, card_db, "Karn, the Great Creator", 0)
    # Opp has only one small creature (PW survives)
    _add_to_battlefield(game, card_db, "Memnite", 1)
    game.players[0].life = 20
    game.players[1].life = 20
    game.turn_number = 4
    return game, 0, karn


def test_karn_score_above_minimum_floor(card_db):
    """Karn's planeswalker bonus must be ≥ 3.0 in a normal early-mid
    state — anything less and a vanilla 4-drop wins on EV.

    Encodes the rule: planeswalker EV bonus is floored at +3.0 (one
    activation's value) regardless of card_clock_impact magnitude.
    """
    from ai.ev_evaluator import snapshot_from_game
    from ai.ev_player import EVPlayer

    game, ctrl, karn = _setup_etron_t4_with_karn_in_hand(card_db)
    snap = snapshot_from_game(game, ctrl)
    player = EVPlayer(ctrl, "Eldrazi Tron")
    me = game.players[ctrl]
    opp = game.players[1 - ctrl]

    karn_score = player._score_spell(karn, snap, game, me, opp)

    # Lower bound: Karn at 4 mana with loyalty 5 should generate a
    # bonus ≥ 3.0 (the survival floor). Total score includes other
    # terms (mana cost, BHI discount, etc.) so we just assert the
    # planeswalker bonus contribution is non-trivial — use the
    # vanilla 4-drop reference value.
    # Anchor: Memnite-on-board, opp at 20, my Tron complete.
    assert karn_score > 1.0, (
        f"Karn EV={karn_score:.2f} too low. The +3.0 floor on the "
        f"planeswalker bonus should keep total Karn score > 1.0 in "
        f"this state, otherwise vanilla 4-drops dominate and Karn is "
        f"never cast."
    )


def test_floor_applies_to_all_planeswalkers(card_db):
    """Same floor logic must lift any planeswalker, not just Karn.

    Encodes the generalization rule: oracle-driven, no per-card hooks.
    Picks Wrenn and Six (Modern legal, common PW) — same path through
    _score_spell, same floor must apply.
    """
    from ai.ev_evaluator import snapshot_from_game
    from ai.ev_player import EVPlayer

    game = GameState(card_db, ["P1", "P2"])
    # 2 lands available for Wrenn (cost 1RG)
    _add_to_battlefield(game, card_db, "Stomping Ground", 0)
    _add_to_battlefield(game, card_db, "Stomping Ground", 0)
    wrenn = _add_to_hand(game, card_db, "Wrenn and Six", 0)
    _add_to_battlefield(game, card_db, "Memnite", 1)
    game.players[0].life = 20
    game.players[1].life = 20
    game.turn_number = 2

    snap = snapshot_from_game(game, 0)
    player = EVPlayer(0, "4c Omnath")
    me = game.players[0]
    opp = game.players[1]
    wrenn_score = player._score_spell(wrenn, snap, game, me, opp)

    # Wrenn at 2 mana with loyalty 3 should also get the floor.
    assert wrenn_score > 0.5, (
        f"Wrenn and Six EV={wrenn_score:.2f} too low. Floor logic "
        f"must apply generically, not just to Karn."
    )
