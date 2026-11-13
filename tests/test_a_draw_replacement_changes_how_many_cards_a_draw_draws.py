"""A draw replacement changes how many cards a draw draws (CR 614.1a,
614.5, 616.1, 121.2).

"If you would draw a card, draw two cards instead" replaces each single draw
(CR 121.2: drawing N cards is N draws). "If you would draw one or more
cards, you draw that many cards plus one instead" replaces the whole draw
once. Each replacement applies once to an event (CR 614.5); two of them
both apply (CR 616.1). The cards a replacement draws are still draws: a
draw limit counts them (CR 101.2).

The engine's rule:
- The replacement is typed at load (`CardTemplate.draw_replacement`).
- It is a static rule effect while its permanent is on the battlefield
  (`ModKind.REPLACE_DRAW`, `rules_query.draw_replacements`).
- The draw owner (`GameState.draw_cards`) applies it.
- Shapes the owner does not apply (dredge, reveal, exile, a "max speed"
  condition) are not typed.

The defect: no draw replacement was applied anywhere. Quantum Riddler (six
registered decks x4) drew one card where the rules give two.

Card names are fixture carriers: each is one member of its class.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase

RIDDLER = "Quantum Riddler"


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, *, in_play=(), hand=0, opp_in_play=()):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for name in in_play:
        _add(game, card_db, name, 0)
    for name in opp_in_play:
        _add(game, card_db, name, 1)
    for _ in range(hand):
        _add(game, card_db, "Island", 0, "hand")
    for p in (0, 1):
        for _ in range(20):
            _add(game, card_db, "Island", p, "library")
    return game


def _draws(game, n, player=0):
    return len(game.draw_cards(player, n))


# ── "One or more cards ... that many plus one" (once per draw) ─────────

def test_one_or_fewer_cards_in_hand_draws_one_more(card_db):
    game = _game(card_db, in_play=[RIDDLER], hand=1)
    assert _draws(game, 1) == 2


def test_two_cards_in_hand_draw_as_printed(card_db):
    game = _game(card_db, in_play=[RIDDLER], hand=2)
    assert _draws(game, 1) == 1


def test_a_draw_of_several_cards_is_replaced_once(card_db):
    game = _game(card_db, in_play=[RIDDLER], hand=0)
    assert _draws(game, 3) == 4


def test_two_replacements_each_apply_once(card_db):
    game = _game(card_db, in_play=[RIDDLER, RIDDLER], hand=0)
    assert _draws(game, 1) == 3


def test_an_opponents_replacement_does_not_touch_your_draws(card_db):
    game = _game(card_db, opp_in_play=[RIDDLER], hand=0)
    assert _draws(game, 1) == 1


# ── "A card ... two cards" (each single draw) ──────────────────────────

def test_each_single_draw_is_replaced(card_db):
    game = _game(card_db, in_play=["Thought Reflection"], hand=3)
    assert _draws(game, 2) == 4


def test_the_first_draw_in_the_draw_step_is_not_replaced(card_db):
    """Alhammarret's Archive: "except the first one you draw in each of
    your draw steps"."""
    game = _game(card_db, in_play=["Alhammarret's Archive"], hand=3)
    game.current_phase = Phase.DRAW
    assert _draws(game, 1) == 1
    assert _draws(game, 1) == 2
    game.current_phase = Phase.MAIN1
    assert _draws(game, 1) == 2


def test_an_empty_hand_condition_is_read_at_each_draw(card_db):
    """Phial of Galadriel: only the draw made with no cards in hand is
    replaced."""
    game = _game(card_db, in_play=["Phial of Galadriel"], hand=0)
    assert _draws(game, 3) == 4


def test_a_replacement_draw_still_counts_against_a_draw_limit(card_db):
    """Spirit of the Labyrinth: each player can't draw more than one card
    each turn."""
    game = _game(card_db, in_play=[RIDDLER, "Spirit of the Labyrinth"],
                 hand=0)
    assert _draws(game, 1) == 1


def test_a_shape_the_owner_does_not_apply_is_not_typed(card_db):
    for name in ("Life from the Loam", "Enduring Renewal",
                 "Vnwxt, Verbose Host"):
        assert card_db.get_card(name).draw_replacement is None


# ── The auditor (CR 614.1a) ────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "614.1a/draw_replaced"]


def test_the_audit_records_a_draw_the_replacement_did_not_change(
        card_db, audit, monkeypatch):
    from engine import rules_query
    game = _game(card_db, in_play=[RIDDLER], hand=0)
    monkeypatch.setattr(rules_query, "draw_replacements",
                        lambda g, p: [])                  # the defect
    game.draw_cards(0, 1)
    assert _findings(audit)


def test_the_audit_is_silent_when_the_replacement_applies(card_db, audit):
    game = _game(card_db, in_play=[RIDDLER], hand=0)
    game.draw_cards(0, 1)
    assert not _findings(audit)
