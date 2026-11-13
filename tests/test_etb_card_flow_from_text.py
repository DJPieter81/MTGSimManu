"""An enter trigger's card-flow effect resolves from the card's own text
(CR 603.2, 603.6a; surveil CR 701.42; regrowth CR 603.3d, 608.2b).

"When ~ enters, surveil N" and "When ~ enters, [you may] return target card
from your graveyard to your hand" are triggered abilities their permanent
has because its text says so (CR 113.1), whoever tagged it: the effect
grammar types the host (TRIGGERED, SELF_ENTERS) and its SURVEIL or MOVE
spec, and the enter-trigger carrier resolves it through the effect
dispatcher's card-flow family (owners: `GameState.surveil`, the zone
funnel). A classifier tag neither adds nor removes the trigger.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


def _put(game, card_db, idx, name, zone="battlefield"):
    c = CardInstance(template=card_db.get_card(name), owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone if zone != "battlefield" else "battlefield").append(c)
    return c


def _library(game, card_db, n, idx=0):
    for _ in range(n):
        _put(game, card_db, idx, "Island", zone="library")


def _enters(game, card_db, name, idx=0):
    from engine.oracle_resolver import resolve_etb_from_oracle
    card = _put(game, card_db, idx, name)
    return card, resolve_etb_from_oracle(game, card, idx)


@pytest.mark.parametrize("name,n", [("Broodspinner", 2), ("Cephalid Inkmage", 3)])
def test_an_enter_trigger_surveils_as_printed_with_no_tag(card_db, name, n):
    game = _game()
    _library(game, card_db, 5)
    _, handled = _enters(game, card_db, name)
    assert handled
    assert len(game.players[0].graveyard) == n
    assert len(game.players[0].library) == 5 - n


def test_a_surveil_land_surveils_exactly_as_before(card_db):
    game = _game()
    _library(game, card_db, 3)
    _, handled = _enters(game, card_db, "Underground Mortuary")
    assert handled
    assert len(game.players[0].graveyard) == 1


def test_a_classifier_tag_alone_never_makes_a_permanent_surveil(card_db, monkeypatch):
    """A tag with no matching printed trigger changes nothing: a permanent
    whose enter trigger does something else does not surveil, however it
    is tagged."""
    import ai.oracle_classifier as oc
    monkeypatch.setattr(oc, "tags_for",
                        lambda name: frozenset({oc.Tag.ETB_SURVEIL_N}))
    game = _game()
    _library(game, card_db, 3)
    _enters(game, card_db, "Thraben Inspector")
    assert len(game.players[0].library) == 3
    assert game.players[0].graveyard == []


def test_the_surveil_executor_is_the_card_flow_familys(card_db):
    """The dispatcher's card-flow family owns SURVEIL and MOVE; damage ETBs
    stay on their own path (the carrier takes card-flow hosts only)."""
    from engine.effect_executors import FAMILIES
    from engine.effect_spec import Verb
    assert {Verb.SURVEIL, Verb.MOVE} <= FAMILIES["card_flow"]


# ── Regrowth: "When ~ enters, [you may] return target <card> from your
#    graveyard to your hand" (CR 603.3d, 608.2b). The engine enumerates the
#    legal cards (target_solver); the controller picks them (choose_cards)
#    and answers "you may" (choose_optional_effect) -- A35; nothing in the
#    engine scores the pick. ──

def _graveyard(game, card_db, names, idx=0):
    return [_put(game, card_db, idx, n, zone="graveyard") for n in names]


def _callbacks(**answers):
    from engine.callbacks import DefaultCallbacks
    return type("_Scripted", (DefaultCallbacks,), answers)()


@pytest.mark.parametrize("name", ["Gravedigger", "Timeless Witness",
                                  "Eternal Witness"])
def test_an_enter_trigger_returns_a_legal_graveyard_card_as_printed(card_db, name):
    """One card, of a type the target names, from the controller's own
    graveyard -- an opponent's card is never a legal target."""
    game = _game()
    bolt, bears = _graveyard(game, card_db, ["Lightning Bolt", "Grizzly Bears"])
    _put(game, card_db, 1, "Grizzly Bears", zone="graveyard")
    _, handled = _enters(game, card_db, name)
    assert handled
    hand = game.players[0].hand
    assert len(hand) == 1 and hand[0] in (bolt, bears)
    if name == "Gravedigger":            # "target creature card"
        assert hand[0] is bears
    assert hand[0].zone == "hand"
    assert len(game.players[1].graveyard) == 1


@pytest.mark.parametrize("accept", [True, False])
def test_you_may_return_is_the_controllers_choice(card_db, accept):
    game = _game()
    game.callbacks = _callbacks(
        choose_optional_effect=lambda self, ctx, spec: accept)
    (bears,) = _graveyard(game, card_db, ["Grizzly Bears"])
    _enters(game, card_db, "Gravedigger")
    assert (game.players[0].hand == [bears]) is accept
    assert (bears.zone == "graveyard") is not accept


def test_the_controller_chooses_which_card_returns(card_db):
    asked = []

    def cheapest(self, ctx, spec, pool, n):
        asked.append(sorted(c.name for c in pool))
        return sorted(pool, key=lambda c: c.template.cmc or 0)[:n]
    game = _game()
    game.callbacks = _callbacks(choose_cards=cheapest)
    bolt, _bears = _graveyard(game, card_db, ["Lightning Bolt", "Grizzly Bears"])
    _enters(game, card_db, "Timeless Witness")
    assert asked == [["Grizzly Bears", "Lightning Bolt"]]
    assert game.players[0].hand == [bolt]


def test_a_mandatory_target_is_chosen_even_if_the_controller_picks_none(card_db):
    """CR 601.2c, 603.3d: a required target is chosen when a legal one
    exists, and without "you may" the card is returned."""
    game = _game()
    game.callbacks = _callbacks(choose_cards=lambda self, ctx, spec, pool, n: [])
    _graveyard(game, card_db, ["Lightning Bolt"])
    _enters(game, card_db, "Timeless Witness")
    assert [c.name for c in game.players[0].hand] == ["Lightning Bolt"]


def test_a_chosen_card_no_longer_in_the_graveyard_is_not_returned(card_db):
    """CR 608.2b: on resolution a chosen target that has left the
    graveyard is illegal and is not moved; one still there is."""
    from engine import effect_resolver as er
    from engine.effect_spec import EventHint, HostKind
    game = _game()
    bolt, bears = _graveyard(game, card_db, ["Lightning Bolt", "Grizzly Bears"])
    witness = _put(game, card_db, 0, "Timeless Witness")
    host = next(h for h in witness.template.effects.front()
                if h.kind is HostKind.TRIGGERED
                and EventHint.SELF_ENTERS in h.trigger.event_hints)

    def resolve(card):
        return er.resolve_ability(game, er.handle_of(witness), 0, host,
                                  ((er.handle_of(card),),), family="card_flow",
                                  source_object=witness)
    assert resolve(bears)
    assert game.players[0].hand == [bears]
    chosen = er.handle_of(bolt)
    game.zone_mgr.move_card(game, bolt, "graveyard", "exile")
    assert not er.resolve_ability(game, er.handle_of(witness), 0, host,
                                  ((chosen,),), family="card_flow",
                                  source_object=witness)
    assert bolt.zone == "exile" and game.players[0].hand == [bears]


def test_a_classifier_tag_alone_never_regrows(card_db, monkeypatch):
    import ai.oracle_classifier as oc
    monkeypatch.setattr(oc, "tags_for", lambda name: frozenset(
        {oc.Tag.ETB_RETURN_FROM_GY_TO_HAND}))
    game = _game()
    _graveyard(game, card_db, ["Grizzly Bears"])
    _enters(game, card_db, "Thraben Inspector")
    assert game.players[0].hand == []
    assert len(game.players[0].graveyard) == 1


@pytest.mark.parametrize("name", ["Timeless Witness", "Eternal Witness"])
def test_the_ai_leaves_a_card_that_works_from_the_graveyard(card_db, name):
    """A card that serves its plan from the graveyard (a flashback spell,
    the graveyard safe) stays there while another legal card can come
    back (`discard_advisor.serves_plan_from_graveyard`); it comes back only
    to fill a required target -- never for an optional one."""
    from engine.game_runner import AICallbacks
    game = _game()
    game.callbacks = AICallbacks()
    rites, bolt = _graveyard(game, card_db, ["Unburial Rites", "Lightning Bolt"])
    _enters(game, card_db, name)
    assert game.players[0].hand == [bolt]

    game = _game()
    game.callbacks = AICallbacks()
    (rites,) = _graveyard(game, card_db, ["Unburial Rites"])
    _enters(game, card_db, name)
    required = name == "Timeless Witness"        # no "you may"
    assert game.players[0].hand == ([rites] if required else [])


@pytest.mark.parametrize("name", ["Gravedigger", "Timeless Witness"])
def test_the_ai_returns_its_hand_delivery_choice(card_db, name):
    """The AI takes an optional card to its own hand and picks it with its
    plan-aware hand-delivery ranking -- the one a library tutor delivers
    by (`ai.activation_ev.choose_tutor_delivery`)."""
    from ai.activation_ev import choose_tutor_delivery
    from engine.game_runner import AICallbacks
    game = _game()
    game.callbacks = AICallbacks()
    pool = _graveyard(game, card_db, ["Lightning Bolt", "Grizzly Bears",
                                      "Mountain"])
    legal = [c for c in pool if name != "Gravedigger" or c.template.is_creature]
    expected = choose_tutor_delivery(game, 0, legal, source=None)
    _enters(game, card_db, name)
    assert game.players[0].hand == [expected]
