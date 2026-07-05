"""E3 (2026-07-05 probe) — planeswalker loyalty-ability choice.

Two rules, phrased on the mechanic (no card names):

1. **Defensive minus at a failing race** — when an opposing attacker
   would drop the controller into the panic zone (or kill the walker),
   a minus ability that neutralizes that attacker (kill or bounce) is
   preferred over a loyalty-positive ability with no board impact,
   even when the neutralized attacker is cheap.

2. **Suicide guard** — an ability that drops the walker to zero
   loyalty (killing it) is never chosen for a play with no
   attacker-neutralizing board impact while a surviving line exists
   and the race is not failing.  s60104 G2 T8 pattern: a walker at
   exactly-lethal-to-itself loyalty was minused away to bounce one
   noncreature permanent the opponent could simply replay.

Layering (per CLAUDE.md "Engine layer enforces rules; AI layer makes
choices"): the CHOICE lives in `ai/pw_ability.py::choose_pw_ability`;
`engine/game_runner.py` only delegates and then enforces the loyalty
rules of whatever the AI picked.
"""
from __future__ import annotations

import random

import pytest

from ai.clock import LifePhase, life_phase
from ai.ev_evaluator import snapshot_from_game
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    else:
        game.players[controller].library.append(card)
    return card


class _FakeWalker:
    """Duck-typed planeswalker instance — the chooser only reads
    loyalty_counters (loyalty legality itself is enforced by the
    engine after the choice)."""

    def __init__(self, loyalty: int):
        self.loyalty_counters = loyalty


def test_defensive_minus_preferred_when_attacker_would_panic_controller(
        card_db):
    """Rule 1.  Controller at low life vs a cheap attacker whose next
    hit drops them into the panic zone.  The walker offers:

      plus  (+1): pure card selection, no board impact
      minus (-2): bounce the attacking creature

    The bounce target is CHEAP (cmc < 2) — pre-fix the bounce branch
    scores it below the card-draw plus and the AI durdles while dying.
    At a failing race the attacker-neutralizing minus must win.
    """
    from ai.pw_ability import choose_pw_ability

    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "control_side"
    game.players[1].deck_name = "aggro_side"
    game.players[0].life = 4
    game.players[1].life = 20
    game.turn_number = 6
    game.active_player = 0
    game.current_phase = Phase.MAIN1

    # Opposing cheap attacker — enough power that the race is failing.
    _add(game, card_db, "Frogmite", 1, "battlefield")
    _add(game, card_db, "Memnite", 1, "battlefield")

    snap = snapshot_from_game(game, 0)
    assert life_phase(snap) in (LifePhase.PANIC, LifePhase.LETHAL), (
        f"fixture invariant: the race must be failing, got "
        f"{life_phase(snap)}"
    )

    pw = _FakeWalker(loyalty=4)
    pw_data = {
        "plus": (1, "Look at the top two cards of your library. "
                    "Put one into your hand."),
        "minus": (-2, "Return target creature to its owner's hand."),
    }

    choice = choose_pw_ability(pw, pw_data, game.players[0],
                               game.players[1], game, player_idx=0)
    assert choice == "minus", (
        f"at a failing race, the attacker-neutralizing minus must be "
        f"preferred over no-board-impact card selection, got {choice!r}"
    )


def test_never_suicides_walker_for_no_impact_bounce_when_race_not_failing(
        card_db):
    """Rule 2.  Controller comfortable (no panic).  Walker at exactly
    the loyalty its minus costs — activating the minus kills it.  The
    minus bounces an opposing NONCREATURE permanent (replayable next
    turn, neutralizes no attacker); the plus is loyalty-positive
    utility.  The chooser must keep the walker alive.
    """
    from ai.pw_ability import choose_pw_ability

    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "control_side"
    game.players[1].deck_name = "artifact_side"
    game.players[0].life = 20
    game.players[1].life = 20
    game.turn_number = 6
    game.active_player = 0
    game.current_phase = Phase.MAIN1

    # Opponent has a mid-cmc noncreature permanent (a juicy bounce
    # target by cmc) and NO creatures — nothing threatens the walker
    # or the controller.
    _add(game, card_db, "Isochron Scepter", 1, "battlefield")
    _add(game, card_db, "Nettlecyst", 1, "battlefield")

    snap = snapshot_from_game(game, 0)
    assert life_phase(snap) not in (LifePhase.PANIC, LifePhase.LETHAL), (
        "fixture invariant: race must NOT be failing"
    )

    pw = _FakeWalker(loyalty=3)
    pw_data = {
        "plus": (1, "You may cast sorcery spells as though they had "
                    "flash."),
        "minus": (-3, "Return up to one target artifact, creature, or "
                      "enchantment to its owner's hand. Draw a card."),
    }

    choice = choose_pw_ability(pw, pw_data, game.players[0],
                               game.players[1], game, player_idx=0)
    assert choice == "plus", (
        f"a minus that kills the walker for a replayable noncreature "
        f"bounce (no attacker neutralized, race not failing) must lose "
        f"to the surviving loyalty-positive line, got {choice!r} "
        f"(s60104 G2 T8: fresh walker suicided at 3 loyalty for a "
        f"single artifact bounce)"
    )


def test_engine_delegates_pw_choice_to_ai_layer():
    """Layering contract: `engine/game_runner.py` must not own the
    scoring — `_choose_pw_ability` delegates to `ai.pw_ability`.
    Engine enforces legality; AI ranks."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "engine" / "game_runner.py").read_text()
    assert "ai.pw_ability" in src or "from ai import pw_ability" in src, (
        "engine/game_runner.py must delegate planeswalker ability "
        "choice to ai/pw_ability.py (E3 layering violation: strategic "
        "scoring in the engine layer)"
    )
