"""Track H (2026-07-05 calibration wave, handoff from Track G's
diagnostic, finding 2) — gameplan land priorities reach in-game
sequencing.

Mechanic under test: ``gameplan.land_priorities`` is per-deck DATA
declaring which lands matter to the deck's engine.  It was consumed
only by mulligan bottoming (ai/gameplan.py) — the in-game land-drop
scorer ``_score_land`` had no gameplan hook, so engine-land
sequencing could not be data-tuned.  The hook must order otherwise
comparable land drops by the declared priority, with the scale small
enough that priorities re-order lands rather than outrank spells.

Synthetic gameplan + synthetic lands — no real card names.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState, Phase


def _land_template(name: str) -> CardTemplate:
    return CardTemplate(
        name=name,
        card_types=[CardType.LAND],
        mana_cost=ManaCost(),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(),
        produces_mana=["C"],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )


def _instance(game: GameState, tmpl: CardTemplate, controller: int,
              zone: str) -> CardInstance:
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "hand":
        game.players[controller].hand.append(card)
    elif zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    return card


def test_gameplan_declared_land_priority_orders_in_game_land_drops():
    """Two mechanically identical lands in hand; the gameplan declares
    a priority for one.  ``_score_land`` must rank the declared land
    strictly higher — the per-deck data reaches in-game sequencing."""
    from ai.ev_player import EVPlayer

    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "SyntheticDeck"
    game.players[1].deck_name = "SyntheticOpponent"
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 2

    me = game.players[0]
    engine_land = _instance(game, _land_template("Synthetic Engine Land"),
                            0, "hand")
    plain_land = _instance(game, _land_template("Synthetic Plain Land"),
                           0, "hand")

    ai = EVPlayer(0, "SyntheticDeck", rng=random.Random(0))

    # Install a synthetic gameplan carrying only land_priorities —
    # the hook must read the DATA, not any deck identity.
    from ai.gameplan import DeckGameplan, GoalEngine
    plan = DeckGameplan(deck_name="SyntheticDeck", goals=[])
    plan.land_priorities = {"Synthetic Engine Land": 10.0}
    ai.goal_engine = GoalEngine(plan)

    ev_engine = ai._score_land(engine_land, me, [], game)
    ev_plain = ai._score_land(plain_land, me, [], game)
    assert ev_engine > ev_plain, (
        f"gameplan-declared land priority must order in-game land "
        f"drops (engine={ev_engine:.2f} vs plain={ev_plain:.2f})"
    )
