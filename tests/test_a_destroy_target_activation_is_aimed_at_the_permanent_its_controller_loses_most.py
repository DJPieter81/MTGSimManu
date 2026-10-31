"""A destroy-target activation is aimed at the permanent its controller
loses most, and activated only when that loss outweighs what activating
costs (CR 602.2b, 701.8a, 702.12b).

An activated "destroy target <type>" ability declares its target as it is
activated (CR 602.2b) and destroys it on resolution unless it is
indestructible (CR 701.8a, 702.12b). The AI's rule is the one it already
follows for damage that destroys (`ai.damage_targets`): of the opposing
permanents the ability may target -- the engine's own admission (its
types, qualifiers and controller; hexproof, protection) -- that the
instruction would destroy and whose ward its controller would pay, the one
whose loss costs its controller most (`permanent_threat`, the removal
scorer's currency). It is activated when that loss outweighs the
activation's own cost terms (a sacrificed source, life paid), as an aimed
damage activation is.

The defect: unit LA made 72 pool activations resolve on the dispatcher's
removal family, but only the 9 aimed at a land were valued; the other 63
("destroy target artifact or enchantment", "... creature", "... permanent")
were never activated.

Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


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


def _equip(equipment, creature):
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")


def _game(card_db, opposing):
    """Player 0 with Cathar Commando ("{1}, Sacrifice this creature:
    Destroy target artifact or enchantment.") and two Plains; player 1
    with `opposing` on the battlefield. Both libraries hold Islands."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    commando = _add(game, card_db, "Cathar Commando", 0)
    for _ in range(2):
        _add(game, card_db, "Plains", 0)
    for _ in range(3):
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    theirs = [_add(game, card_db, n, 1) for n in opposing]
    return game, commando, theirs


def _plating_board(card_db):
    """Cranial Plating on a Memnite among four other artifacts."""
    game, commando, theirs = _game(card_db, [
        "Memnite", "Cranial Plating", "Ornithopter", "Frogmite",
        "Signal Pest"])
    _equip(theirs[1], theirs[0])
    return game, commando, theirs


def _requirements(card):
    ability = card.template.activated_abilities[0]
    return list(card.template.effects.activated(ability.index).targets)


def _candidates(game, card):
    from ai.activation_ev import activation_candidates
    from ai.ev_evaluator import snapshot_from_game
    return [c for c in activation_candidates(
        game, 0, snapshot_from_game(game, 0)) if c[0] is card]


# ── The aim (one owner with the damage aim) ───────────────────────────

def test_the_aim_is_the_admitted_opposing_permanent_its_controller_loses_most(
        card_db):
    from ai.damage_targets import choose_destroy_recipient
    from ai.permanent_threat import permanent_threat
    from engine.target_solver import target_admitted
    game, commando, theirs = _plating_board(card_db)
    reqs = _requirements(commando)
    opp = game.players[1]
    admitted = [c for c in theirs
                if target_admitted(game, 0, reqs, c.instance_id,
                                   source=commando)]
    assert len(admitted) == len(theirs)          # every one is an artifact
    aim = choose_destroy_recipient(game, 0, commando, reqs,
                                   mana_committed=1)
    best = max(admitted, key=lambda c: permanent_threat(c, opp, game))
    assert aim is not None and aim.permanent is best
    assert aim.value == permanent_threat(best, opp, game)


def test_an_indestructible_or_a_friendly_permanent_is_never_the_aim(card_db):
    """CR 702.12b: the instruction does not destroy an indestructible
    permanent; the AI's own artifacts are not candidates."""
    from ai.damage_targets import choose_destroy_recipient
    game, commando, theirs = _game(card_db, ["Darksteel Citadel"])
    _add(game, card_db, "Ornithopter", 0)
    assert choose_destroy_recipient(game, 0, commando,
                                    _requirements(commando),
                                    mana_committed=1) is None


def test_a_type_the_requirement_does_not_name_is_never_the_aim(card_db):
    from ai.damage_targets import choose_destroy_recipient
    game, commando, theirs = _game(card_db, ["Grizzly Bears"])
    assert choose_destroy_recipient(game, 0, commando,
                                    _requirements(commando),
                                    mana_committed=1) is None


# ── The decision ──────────────────────────────────────────────────────

def test_it_is_activated_at_its_aim_when_the_loss_outweighs_the_cost(card_db):
    from ai.damage_targets import choose_destroy_recipient
    game, commando, theirs = _plating_board(card_db)
    aim = choose_destroy_recipient(game, 0, commando,
                                   _requirements(commando), mana_committed=1)
    (cand,) = _candidates(game, commando)
    assert cand[2] == [aim.target_id] and cand[3] > 0.0


def test_it_is_held_when_the_aim_is_worth_less_than_the_sacrificed_source(
        card_db):
    """A lone 0/2 artifact is worth less to its controller than a 3/1 is to
    us: the sacrifice is a net loss, so the ability is not activated."""
    from ai.activation_ev import activation_candidates  # noqa: F401
    from ai.permanent_threat import permanent_threat
    game, commando, theirs = _game(card_db, ["Ornithopter"])
    assert permanent_threat(theirs[0], game.players[1], game) < \
        permanent_threat(commando, game.players[0], game)
    assert not _candidates(game, commando)


def test_the_activation_it_offers_destroys_its_aim(card_db):
    from engine.activation import ActivationManager
    game, commando, theirs = _plating_board(card_db)
    (cand,) = _candidates(game, commando)
    target = game.get_card_by_id(cand[2][0])
    ability = commando.template.activated_abilities[cand[1]]
    assert ActivationManager.activate(game, 0, commando, ability, cand[2])
    while not game.stack.is_empty:
        game.resolve_stack()
    assert target.zone == "graveyard" and commando.zone == "graveyard"
