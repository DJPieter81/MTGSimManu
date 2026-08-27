"""A land-sacrifice tutor must be payoff-justified, not fired as blind ramp.

The Scapeshift shape ("sacrifice any number of lands ... search your library
for that many lands") converts an untapped mana base into fetched lands whose
bounce-land ETBs return co-entrants: without an untapped-entry watcher the
conservative retained yield is ceil(N/2) TAPPED lands (each karoo's ETB
bounces one co-entrant; worst-case LIFO alternation). Cast with no payoff to
deploy, the "ramp" spell therefore HALVES the caster's own mana base at the
moment it should be deploying its threat.

Live bug this pins (docs/diagnostics/2026-08-26_amulet_titan_rediagnosis.md):
6 of 12 walked losses began with a no-payoff land-sacrifice cast — including
one game where the deck's payoff sat in hand from turn 1 and became
permanently uncastable the moment the tutor resolved (4 lands -> 2). The only
existing gate was the fizzle floor (land count >= 4), which encodes castability,
not worth.

Rules under test (mechanic-phrased; card names below are fixture carriers):
  1. Withheld when no payoff-role card is in hand — blind ramp that shrinks
     the mana base buys nothing.
  2. Withheld when the payoff in hand is NOT castable from the conservative
     post-resolution board (no untapped-entry watcher: ceil(N/2) tapped).
  3. Fires when an untapped-entry watcher is in play and the fetched count
     covers the payoff's cost — the tutor is then genuine payoff acceleration.
  4. The tutor never counts ITSELF as the payoff that justifies it (the
     gameplan lists the tutor in its payoff roles; self-justification would
     reopen the blind-ramp hole).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from ai.scoring_constants import PATIENCE_GATE_REJECT_SENTINEL

_DB = CardDatabase()


def _add(game, name, controller, zone, tapped=False):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = tapped
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game(n_forests=6, with_watcher=False):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    me, opp = game.players[0], game.players[1]
    me.deck_name = "Amulet Titan"
    opp.deck_name = "Boros Energy"
    me.life = 15
    opp.life = 20
    for _ in range(n_forests):
        _add(game, "Forest", 0, "battlefield")
    if with_watcher:
        _add(game, "Amulet of Vigor", 0, "battlefield")
    tutor = _add(game, "Scapeshift", 0, "hand")
    # library must hold lands for the fetch to be live
    for _ in range(10):
        _add(game, "Forest", 0, "library")
    for _ in range(2):
        _add(game, "Gruul Turf", 0, "library")
    _add(game, "Ocelot Pride", 1, "battlefield")
    for _ in range(3):
        _add(game, "Mountain", 1, "battlefield")
    return game, tutor


def _score(game, tutor):
    ai = EVPlayer(player_idx=0, deck_name="Amulet Titan",
                  rng=random.Random(0))
    ai._init_deck_knowledge(game)
    if ai.goal_engine:
        ai.goal_engine.check_transition(game, 0)
    snap = snapshot_from_game(game, 0)
    return ai._score_spell(tutor, snap, game,
                           game.players[0], game.players[1])


def test_land_sacrifice_tutor_withheld_without_a_payoff_in_hand():
    """Rule 1: six lands, no payoff anywhere in hand — the tutor must be
    clamped to the patience-reject band."""
    game, tutor = _game(n_forests=6)
    ev = _score(game, tutor)
    assert ev <= PATIENCE_GATE_REJECT_SENTINEL, (
        f"a no-payoff land-sacrifice tutor scored {ev:.2f} — blind ramp "
        f"that halves the mana base must sit in the reject band")


def test_land_sacrifice_tutor_withheld_when_retention_cannot_cover_the_payoff():
    """Rule 2: the payoff is in hand, but with no untapped-entry watcher the
    conservative post-resolution board (ceil(N/2) tapped lands) cannot cast
    it — the tutor makes the payoff LESS castable and must be withheld."""
    game, tutor = _game(n_forests=4)
    _add(game, "Primeval Titan", 0, "hand")  # 6 mana; ceil(4/2)=2 covers nothing
    ev = _score(game, tutor)
    assert ev <= PATIENCE_GATE_REJECT_SENTINEL, (
        f"tutor scored {ev:.2f} while shrinking the board below its own "
        f"payoff's cost — the observed hand-locked-Titan loss")


def test_land_sacrifice_tutor_fires_with_watcher_and_payoff_coverage():
    """Rule 3: untapped-entry watcher in play, seven lands to convert, the
    six-cost payoff in hand — the tutor is genuine acceleration and must
    NOT be clamped."""
    game, tutor = _game(n_forests=7, with_watcher=True)
    _add(game, "Primeval Titan", 0, "hand")
    ev = _score(game, tutor)
    assert ev > PATIENCE_GATE_REJECT_SENTINEL, (
        f"tutor scored {ev:.2f} despite watcher + castable payoff — the "
        f"gate must not strangle the deck's actual combo line")


def test_land_sacrifice_tutor_does_not_count_itself_as_the_payoff():
    """Rule 4: a second copy of the tutor in hand is not a payoff — the
    gameplan lists the tutor in its payoff roles, and self-justification
    would reopen the blind-ramp hole."""
    game, tutor = _game(n_forests=6)
    _add(game, "Scapeshift", 0, "hand")
    ev = _score(game, tutor)
    assert ev <= PATIENCE_GATE_REJECT_SENTINEL, (
        f"tutor scored {ev:.2f} justified only by its own twin — the "
        f"payoff check must exclude the land-sacrifice-tutor shape")
