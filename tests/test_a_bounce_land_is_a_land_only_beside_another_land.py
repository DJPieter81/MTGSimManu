"""A bounce land is a land only beside another land (AI, from its own text).

"When this land enters, return a land you control to its owner's hand"
(`template.etb_return_land`) returns the bounce land itself when it is the
only land its controller controls. So a bounce land stays on the
battlefield only beside another land. For the keep and the land drop:
- a hand's bounce lands count as lands only when the hand also holds a
  land that is no bounce land;
- a bounce land put onto an empty battlefield returns itself, which only
  spends the land drop, so it is not played there.

The defect: the keep counted every land option, and the land drop scored
a bounce land as a land wherever it went. In Living End vs Amulet Titan
s57500 game 3, Amulet kept two Gruul Turfs as its only lands (with Amulet
of Vigor, two Spelunking, Primeval Titan and Summoner's Pact) and played
one into a self-return on every turn: 9 times in the match. Found
replaying unit SP's one auditor violation.

Card names are fixture carriers: 11 pool lands bounce a land as they
enter; Amulet Titan runs 7 of them.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _card(card_db, name, iid, zone="hand"):
    return CardInstance(template=card_db.get_card(name), owner=0,
                        controller=0, instance_id=iid, zone=zone)


def _hand(card_db, names):
    return [_card(card_db, n, i + 1) for i, n in enumerate(names)]


# ── The count (one owner) ─────────────────────────────────────────────

def test_bounce_lands_alone_are_no_lands_that_stay(card_db):
    from ai.predicates import staying_lands
    hand = _hand(card_db, ["Gruul Turf", "Simic Growth Chamber", "Spelunking"])
    assert staying_lands(hand) == []


def test_a_bounce_land_beside_a_land_stays(card_db):
    from ai.predicates import staying_lands
    hand = _hand(card_db, ["Gruul Turf", "Forest", "Spelunking"])
    assert [c.name for c in staying_lands(hand)] == ["Gruul Turf", "Forest"]


def test_a_land_on_the_battlefield_keeps_a_bounce_land(card_db):
    from ai.predicates import staying_lands
    hand = _hand(card_db, ["Gruul Turf"])
    board = [_card(card_db, "Forest", 9, zone="battlefield")]
    assert len(staying_lands(hand, board)) == 1


# ── The keep ──────────────────────────────────────────────────────────

def _decider(deck):
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=0, deck_name=deck,
                    rng=random.Random(0))._mulligan_decider


def test_a_hand_whose_only_lands_are_bounce_lands_is_mulliganed(card_db):
    """The kept hand of s57500 game 3."""
    hand = _hand(card_db, ["Gruul Turf", "Amulet of Vigor", "Primeval Titan",
                           "Spelunking", "Gruul Turf", "Spelunking",
                           "Summoner's Pact"])
    assert _decider("Amulet Titan").decide(hand, 7) is False


def test_the_same_hand_with_a_land_beside_its_bounce_land_is_kept(card_db):
    hand = _hand(card_db, ["Gruul Turf", "Amulet of Vigor", "Primeval Titan",
                           "Spelunking", "Forest", "Spelunking",
                           "Summoner's Pact"])
    assert _decider("Amulet Titan").decide(hand, 7) is True


# ── The land drop ─────────────────────────────────────────────────────

def _add(game, card_db, name, zone):
    card = CardInstance(template=card_db.get_card(name), owner=0,
                        controller=0, instance_id=game.next_instance_id(),
                        zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[0], zone).append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def _decide(game):
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=0, deck_name="Amulet Titan",
                    rng=random.Random(0)).decide_main_phase(game)


def test_a_bounce_land_is_not_played_onto_an_empty_battlefield(card_db):
    game = _game()
    _add(game, card_db, "Amulet of Vigor", "battlefield")
    _add(game, card_db, "Gruul Turf", "hand")
    decision = _decide(game)
    assert decision is None or decision[0] != "play_land"


def test_the_land_drop_takes_the_land_that_stays(card_db):
    """With an untap watcher out the bounce land scores above a basic,
    but onto an empty battlefield it would return itself."""
    game = _game()
    _add(game, card_db, "Amulet of Vigor", "battlefield")
    _add(game, card_db, "Gruul Turf", "hand")
    forest = _add(game, card_db, "Forest", "hand")
    decision = _decide(game)
    assert decision is not None and decision[0] == "play_land"
    assert decision[1] is forest


def test_a_bounce_land_is_played_beside_a_land(card_db):
    game = _game()
    _add(game, card_db, "Amulet of Vigor", "battlefield")
    _add(game, card_db, "Forest", "battlefield")
    turf = _add(game, card_db, "Gruul Turf", "hand")
    decision = _decide(game)
    assert decision is not None and decision[0] == "play_land"
    assert decision[1] is turf
