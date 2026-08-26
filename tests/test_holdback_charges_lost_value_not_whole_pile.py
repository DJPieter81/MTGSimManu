"""Holdback charges the response value actually forfeited, not the held pile.

`_holdback_penalty` protects held instant-speed interaction by charging
tap-out plays. The A1 spec scaled that charge with the ENTIRE held pile
(count x mean CMC) whenever at least one response was stranded, on the
rationale that "the second counter still wants the mana on a future turn."
But lands untap every turn: mana held now buys responses only in THIS
turn's response window, so the value at risk is exactly the responses that
no longer fit after the cast — the cheapest-first-packed value delta, not
the pile. Under pile-scaling, an interaction-heavy hand charged every
proactive play the full pile (observed live: a draw spell at EV -51.8, a
3-CMC engine at -24.6), so a reactive deck holding four answers could never
deploy anything and never presented a clock — the generalized form of the
decider losses in docs/diagnostics/2026-08-26_decider_loss_root_cause.md.

Rules under test:
  1. The charge is monotone in the value actually lost: a partial tap-out
     that strands one cheap response charges strictly less than a full
     tap-out that strands the pile. (Pile-scaling charged both the same.)
  2. A reactive deck with an interaction-heavy hand and a castable engine
     deploys the engine rather than passing behind the pile.

Card names are fixture carriers; the mechanic is cheapest-first packed
response value before vs after the cast.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game

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


def _interaction_heavy_state():
    """Reactive deck, seven untapped lands across all colours (so the A5
    colour-stranding amplifier stays quiet), an interaction-heavy hand,
    an opponent with a board and a full grip."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 9
    me, opp = game.players[0], game.players[1]
    me.deck_name = "4/5c Control"
    opp.deck_name = "Domain Zoo"
    me.life = 15
    opp.life = 20
    for n in ["Hallowed Fountain", "Breeding Pool", "Steam Vents",
              "Temple Garden", "Sacred Foundry", "Stomping Ground",
              "Hallowed Fountain"]:
        _add(game, n, 0, "battlefield")
    held = [
        _add(game, "Galvanic Discharge", 0, "hand"),   # 1, instant removal
        _add(game, "Galvanic Discharge", 0, "hand"),   # 1
        _add(game, "Counterspell", 0, "hand"),         # 2, hard counter
        _add(game, "Solitude", 0, "hand"),             # 5, flash removal
    ]
    for _ in range(10):
        _add(game, "Island", 0, "library")
    _add(game, "Doorkeeper Thrull", 1, "battlefield")
    _add(game, "Wild Nacatl", 1, "battlefield")
    for _ in range(4):
        _add(game, "Mountain", 1, "battlefield")
    for n in ["Wild Nacatl", "Territorial Kavu", "Ragavan, Nimble Pilferer",
              "Doorkeeper Thrull"]:
        _add(game, n, 1, "library")
    for _ in range(4):
        _add(game, "Mountain", 1, "hand")
    return game, held


def test_partial_tap_out_charges_strictly_less_than_full_tap_out():
    """Rule 1. With held costs [1, 1, 2, 5] and 7 mana, a 3-mana cast
    still leaves [1, 1, 2] castable (lost value = the 5-drop's slot loses
    nothing... the packing drops from value 4 to value 4); a 7-mana cast
    strands everything. The pile-scaling bug charged both the full pile;
    the charge must instead grow with what is actually lost."""
    game, _held = _interaction_heavy_state()
    ai = EVPlayer(player_idx=0, deck_name="4/5c Control",
                  rng=random.Random(0))
    ai._init_deck_knowledge(game)
    me, opp = game.players[0], game.players[1]
    snap = snapshot_from_game(game, 0)
    p_small = ai._holdback_penalty(me, opp, snap, 5, game=game)
    p_full = ai._holdback_penalty(me, opp, snap, 7, game=game)
    assert p_small > p_full, (
        f"a 5-mana cast (strands part of the pile, {p_small:+.2f}) must "
        f"charge strictly less than a 7-mana tap-out (strands everything, "
        f"{p_full:+.2f}); pile-scaling charged the full pile for both")


def test_proactive_deploy_happens_despite_the_held_pile():
    """Rule 2 — the behavioural pin of the live forensics: with 7 untapped
    lands and an interaction-heavy hand, SOME proactive permanent must be
    deployed (under pile-scaling the AI passed every such turn), and the
    3-CMC engine's own score must clear zero (it was observed at -24.6
    under pile-scaling). Which permanent wins the turn is the scorer's
    judgment — in this fixture flash removal that also presents a body
    legitimately outbids the engine — so the pin is "deployment happens
    and the engine is no longer inverted", not a specific card choice."""
    game, _held = _interaction_heavy_state()
    engine = _add(game, "Teferi, Time Raveler", 0, "hand")
    ai = EVPlayer(player_idx=0, deck_name="4/5c Control",
                  rng=random.Random(0))
    decision = ai.decide_main_phase(game)
    assert decision is not None, (
        "passing with 7 untapped lands, a castable engine, and an "
        "interaction-heavy hand is the durdle failure this test pins")
    action, card, _t = decision
    from engine.cards import CardType
    deployed_permanent = (card.template.is_creature
                          or CardType.PLANESWALKER in card.template.card_types)
    assert action == "cast_spell" and deployed_permanent, (
        f"expected a proactive permanent deploy, got {action} {card.name}")
    engine_score = next(
        (c.ev for c in ai._last_candidates
         if c.card.instance_id == engine.instance_id), None)
    assert engine_score is not None and engine_score > 0, (
        f"the engine must clear zero once holdback charges only lost "
        f"value (got {engine_score})")
