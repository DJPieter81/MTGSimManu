"""Kicker (CR 702.33) kicked EFFECTS — the payoff resolves only when the
spell was kicked, and the AI kicks only when the payoff is worth its cost.

Dispatched in K1 commit 3: the combat-prevention Fog rider (Orim's Chant)
and the library-dig "put N instead" count-modifier (Consult the Star
Charts). Sowing Mycospawn's when-cast "exile target land" needs targeted
land-exile plumbing the generic resolver lacks — a recorded lead, so the
AI is not offered that kick.

Card names are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.spell_resolution import ResolutionManager


def _game():
    g = GameState(rng=random.Random(0))
    g.current_phase = Phase.MAIN1
    g.active_player = 0
    return g


def _land(g, card_db, name, controller=0):
    l = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=g.next_instance_id(),
                     zone="battlefield")
    l._game_state = g; l.tapped = False
    g.players[controller].battlefield.append(l)
    return l


def _in_hand(g, card_db, name):
    c = CardInstance(template=card_db.get_card(name), owner=0, controller=0,
                     instance_id=g.next_instance_id(), zone="hand")
    c._game_state = g
    g.players[0].hand.append(c)
    return c


# ─── the kicked effects resolve ──────────────────────────────────────


def test_orims_chant_kicked_prevents_all_attacks_this_turn(card_db):
    g = _game()
    _land(g, card_db, "Plains"); _land(g, card_db, "Plains")  # {W} base + {W} kicker
    oc = _in_hand(g, card_db, "Orim's Chant")
    g.callbacks.should_kick = lambda gg, p, c: 1
    assert g.cast_spell(0, oc, targets=[-2])  # target the opponent (player)
    ResolutionManager.resolve_stack(g)
    assert oc._kick_count == 1
    assert g.players[0].cannot_attack_this_turn
    assert g.players[1].cannot_attack_this_turn


def test_orims_chant_unkicked_does_not_fog(card_db):
    g = _game()
    _land(g, card_db, "Plains")  # only base affordable
    oc = _in_hand(g, card_db, "Orim's Chant")
    g.callbacks.should_kick = lambda gg, p, c: 1  # wants to, but can't afford
    assert g.cast_spell(0, oc, targets=[-2])
    ResolutionManager.resolve_stack(g)
    assert oc._kick_count == 0
    assert not g.players[0].cannot_attack_this_turn


def test_consult_kicked_puts_two_cards_into_hand_instead_of_one(card_db):
    g = _game()
    for _ in range(8):
        _land(g, card_db, "Island")
    for _ in range(6):
        c = CardInstance(template=card_db.get_card("Lightning Bolt"), owner=0,
                         controller=0, instance_id=g.next_instance_id(), zone="library")
        c._game_state = g
        g.players[0].library.append(c)
    consult = _in_hand(g, card_db, "Consult the Star Charts")
    g.callbacks.should_kick = lambda gg, p, c: 1
    hand_before = len(g.players[0].hand) - 1  # minus the Consult about to leave
    assert g.cast_spell(0, consult)
    ResolutionManager.resolve_stack(g)
    assert consult._kick_count == 1
    assert len(g.players[0].hand) - hand_before == 2, "kicked Consult draws two"


def test_consult_unkicked_puts_one(card_db):
    g = _game()
    for _ in range(8):
        _land(g, card_db, "Island")
    for _ in range(6):
        c = CardInstance(template=card_db.get_card("Lightning Bolt"), owner=0,
                         controller=0, instance_id=g.next_instance_id(), zone="library")
        c._game_state = g
        g.players[0].library.append(c)
    consult = _in_hand(g, card_db, "Consult the Star Charts")
    g.callbacks.should_kick = lambda gg, p, c: 0
    hand_before = len(g.players[0].hand) - 1
    assert g.cast_spell(0, consult)
    ResolutionManager.resolve_stack(g)
    assert len(g.players[0].hand) - hand_before == 1


# ─── the AI kick decision ────────────────────────────────────────────


def test_ai_kicks_a_fog_only_when_the_opponent_has_a_board(card_db):
    from ai.board_eval import evaluate_action, Action, ActionType
    g = _game()
    _land(g, card_db, "Plains"); _land(g, card_db, "Plains")
    oc = _in_hand(g, card_db, "Orim's Chant")
    # No opposing creatures → Fog buys nothing → don't kick.
    assert evaluate_action(g, 0, Action(ActionType.KICK, {'card': oc})) <= 0
    # Give the opponent an attacker → kicking the Fog is worth it.
    opp = CardInstance(template=card_db.get_card("Grizzly Bears"), owner=1,
                       controller=1, instance_id=g.next_instance_id(), zone="battlefield")
    opp._game_state = g; opp.summoning_sick = False
    g.players[1].battlefield.append(opp)
    assert evaluate_action(g, 0, Action(ActionType.KICK, {'card': oc})) > 0


def test_ai_does_not_kick_when_only_the_base_is_affordable(card_db):
    from ai.board_eval import evaluate_action, Action, ActionType
    g = _game()
    _land(g, card_db, "Plains")  # one land: base only
    oc = _in_hand(g, card_db, "Orim's Chant")
    opp = CardInstance(template=card_db.get_card("Grizzly Bears"), owner=1,
                       controller=1, instance_id=g.next_instance_id(), zone="battlefield")
    opp._game_state = g; opp.summoning_sick = False
    g.players[1].battlefield.append(opp)
    assert evaluate_action(g, 0, Action(ActionType.KICK, {'card': oc})) <= 0
