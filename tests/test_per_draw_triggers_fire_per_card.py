"""Per-draw triggers: one trigger per card drawn, and their damage is
checked by state-based actions the next time a player would receive
priority.

Two mechanics, both dispatched by `engine/zone_transfer.py`'s
`TransferKind.DRAW` fan-out:

  1. **Per-card firing (CR 121.2)** — "whenever an opponent draws a
     card" is a trigger per DRAWN CARD, so a draw-N event fires N times,
     with the "except the first one they draw in each of their draw
     steps" exemption applying only to the draw step's turn-based draw
     (CR 504.1). Pinned here as a regression surface: the class was
     reported missing in
     `docs/diagnostics/2026-08-27_dimir_overperformance_root_cause.md`
     ("a draw-7 into a live on-draw source produced ZERO triggers"), and
     an instrumented re-run of that exact match (seed 62000) shows all
     seven triggers firing. The report was inferred from the absence of
     a log line, which the fan-out does not emit.

  2. **The real hole in that evidence — SBA timing (CR 704.3/704.5a)** —
     the seven triggers took the drawing player from 2 to -5 life and
     nothing checked state-based actions, so that player kept taking
     actions and a lifelink attack later in the same turn restored them
     to 2. The loss is performed at the next state-based-action check:
     when the resolution that drew ends, or as the step whose draw it was
     gives priority (CR 704.3) -- never part-way through the draw (CR
     704.4: the draw makes every card). A player at 0 or less loses then,
     and life gained afterwards cannot save them.

Class size: every on-draw source (the "deals N damage"/"loses N life"
per-draw family — Orcish Bowmasters, Underworld Dreams, Sheoldred, Ob
Nixilis, ...) crossed with every multi-card draw effect. Real-DB cards
are fixture carriers; the fan-out dispatches on classifier tags, never
on names.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase

ON_DRAW_SOURCE = "Orcish Bowmasters"   # "except the first one they draw
                                       # in each of their draw steps"
FILLER = "Island"                      # library fodder
LIFELINKER = "Griselbrand"             # 7/7 lifelink — the "saved by
                                       # later life gain" counterfactual


def _make_game():
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "Dimir Midrange"
    game.players[1].deck_name = "Goryo's Vengeance"
    game.active_player = 1
    game.current_phase = Phase.MAIN1
    return game


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


def _setup(card_db, library=12):
    """On-draw source under P0; P1 is the drawing opponent."""
    game = _make_game()
    _add(game, card_db, ON_DRAW_SOURCE, 0, "battlefield")
    for _ in range(library):
        _add(game, card_db, FILLER, 1, "library")
    return game


class TestPerDrawTriggerFiresOncePerCard:

    def test_multi_card_draw_fires_one_trigger_per_card(self, card_db):
        game = _setup(card_db)
        before = game.players[1].life

        game.draw_cards(1, 4)

        assert before - game.players[1].life == 4, (
            f"a 4-card draw fired {before - game.players[1].life} "
            f"triggers instead of 4")

    def test_first_draw_of_the_draw_step_is_exempt_but_the_rest_fire(
            self, card_db):
        """CR 504.1 turn-based draw is the exempt one; extra draws in
        the same draw step still trigger."""
        game = _setup(card_db)
        game.current_phase = Phase.DRAW
        game.players[1].cards_drawn_this_turn = 0
        before = game.players[1].life

        game.draw_cards(1, 3)

        assert before - game.players[1].life == 2, (
            f"draw-step exemption applied to the wrong number of draws: "
            f"{before - game.players[1].life} damage from 3 draws")

    def test_outside_the_draw_step_no_draw_is_exempt(self, card_db):
        game = _setup(card_db)
        game.players[1].cards_drawn_this_turn = 0
        before = game.players[1].life

        game.draw_cards(1, 1)

        assert before - game.players[1].life == 1, (
            "a main-phase draw was treated as the draw step's free draw")


class TestPerDrawTriggerDamageIsCheckedByStateBasedActions:

    def test_player_reduced_to_zero_by_per_draw_triggers_loses_at_the_next_check(
            self, card_db):
        """CR 704.5a, performed the next time a player would receive
        priority (CR 704.3) -- not deferred past it."""
        game = _setup(card_db)
        game.players[1].life = 3

        game.draw_cards(1, 5)
        game.check_state_based_actions()

        assert game.players[1].life <= 0
        assert game.game_over, (
            "player was taken to 0 or less by per-draw triggers and the "
            "game did not end at the next check")
        assert game.winner == 0

    def test_lethal_per_draw_damage_is_not_undone_by_later_life_gain(
            self, card_db):
        """The replayed failure: the drawing player finished the draw at
        negative life, kept acting, and a lifelink swing later in the
        same turn put them back above 0. Once the loss is registered it
        cannot be reversed."""
        game = _setup(card_db)
        game.players[1].life = 3
        _add(game, card_db, LIFELINKER, 1, "battlefield")

        game.draw_cards(1, 5)
        game.check_state_based_actions()
        assert game.game_over, "loss was not registered at the check"
        winner = game.winner

        game.gain_life(1, 7, "lifelink")

        assert game.game_over and game.winner == winner, (
            "a player who had already lost was revived by later life "
            "gain")

    def test_a_draw_makes_every_card_after_the_drawing_player_falls_to_zero(
            self, card_db):
        """CR 704.4: no state-based action is performed part-way through
        the draw; every card is drawn, then the check finds the loss."""
        game = _setup(card_db)
        game.players[1].life = 2

        drawn = game.draw_cards(1, 6)

        assert len(drawn) == 6
        game.check_state_based_actions()
        assert game.game_over and game.winner == 0

    def test_survivable_per_draw_damage_does_not_end_the_game(self, card_db):
        """Guard against an over-eager check: a player who stays above 0
        keeps playing."""
        game = _setup(card_db)
        game.players[1].life = 6

        game.draw_cards(1, 4)
        game.check_state_based_actions()

        assert game.players[1].life == 2
        assert not game.game_over
