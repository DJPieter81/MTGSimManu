"""Every target a spell requires is chosen as it is cast (CR 601.2c).

As a spell is cast, its caster announces a target for each target the
spell requires. An "up to N" requirement may take fewer, and a mode's
requirement binds only if that mode is chosen (CR 700.2a). One object or
player may be chosen for different instances of the word "target"; within
one instance the targets are distinct. A spell whose required target is
not chosen is not cast.

The defect: the cast check (`CastManager.targets_admitted`) asked only
whether each chosen target was admitted, so an empty target list passed.
A census of 50 seeded games counted 44 of 260 required-target casts with
no target:
- Thoughtseize 28 of 31 (its caster's opponent was assumed at resolution);
- Thought Scour 4 of 4;
- Fatal Push 4 of 19, which resolved doing nothing.
Surgical Extraction was cast at nothing and resolved doing nothing.

The cast procedure now asks the caster for a required target the casting
path left unchosen (`GameCallbacks.choose_cast_targets`, answered by the
caster's own target picker) and refuses the cast, before anything is
paid, when none is chosen.

Card names are fixture carriers: every instant and sorcery with a target
requirement reads the rule.
"""
from __future__ import annotations

import random

import pytest

from engine.callbacks import DefaultCallbacks
from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT, PLAYER_TARGET_SELF
from engine.game_state import GameState, Phase
from engine.target_solver import TargetRequirement


class _Asked(DefaultCallbacks):
    """Answers a cast's target question with a fixed list, recording that
    it was asked."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = 0

    def choose_cast_targets(self, game, player_idx, card, requirements,
                            x_value=0):
        self.asked += 1
        return list(self.answer(game) if callable(self.answer)
                    else self.answer)


def _add(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, callbacks=None):
    game = GameState(rng=random.Random(0), callbacks=callbacks)
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(3):
        _add(game, card_db, "Swamp", 0, "battlefield")
    for _ in range(5):
        _add(game, card_db, "Island", 1, "library")
        _add(game, card_db, "Swamp", 0, "library")
    return game


# ── The legality owner ────────────────────────────────────────────────

def _req(types, count_min=1, count_max=1, optional=False, scope="any"):
    return TargetRequirement(zone="battlefield", types=frozenset(types),
                             owner_scope=scope, is_optional=optional,
                             count_min=count_min, count_max=count_max)


def test_one_object_may_be_chosen_for_different_target_words(card_db):
    from engine.target_solver import required_targets_chosen
    game = _game(card_db)
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    reqs = [_req({"creature"}), _req({"creature"})]
    assert required_targets_chosen(game, 0, reqs, [bears.instance_id])


def test_a_requirement_for_two_targets_takes_two_distinct_ones(card_db):
    from engine.target_solver import required_targets_chosen
    game = _game(card_db)
    a = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    b = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    reqs = [_req({"creature"}, count_min=2, count_max=2)]
    assert not required_targets_chosen(game, 0, reqs, [a.instance_id])
    assert not required_targets_chosen(game, 0, reqs,
                                       [a.instance_id, a.instance_id])
    assert required_targets_chosen(game, 0, reqs,
                                   [a.instance_id, b.instance_id])


def test_an_up_to_requirement_needs_no_target(card_db):
    from engine.target_solver import required_targets_chosen
    game = _game(card_db)
    reqs = [_req({"artifact"}, count_min=0, count_max=2, optional=True)]
    assert required_targets_chosen(game, 0, reqs, [])


def test_a_player_marker_answers_only_a_requirement_admitting_that_player(
        card_db):
    """'Target opponent' is not answered by its caster (CR 115.1)."""
    from engine.target_solver import required_targets_chosen, target_admitted
    game = _game(card_db)
    opponent = [TargetRequirement(zone="any", types=frozenset({"player"}),
                                  owner_scope="opponent")]
    assert required_targets_chosen(game, 0, opponent,
                                   [PLAYER_TARGET_OPPONENT])
    assert not required_targets_chosen(game, 0, opponent,
                                       [PLAYER_TARGET_SELF])
    assert not target_admitted(game, 0, opponent, PLAYER_TARGET_SELF)


# ── The cast ──────────────────────────────────────────────────────────

def test_a_spell_its_caster_aims_at_nothing_is_not_cast(card_db):
    """Asked for the target its cast left unchosen, the caster chooses
    none: the cast is refused, before anything is paid."""
    asked = _Asked([])
    game = _game(card_db, asked)
    _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    push = _add(game, card_db, "Fatal Push", 0, "hand")
    assert not game.cast_spell(0, push, [])
    assert asked.asked == 1
    assert push.zone == "hand" and game.stack.is_empty
    assert not any(land.tapped for land in game.players[0].lands)


def test_the_caster_is_asked_for_a_target_its_cast_left_unchosen(card_db):
    game = _game(card_db)
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    asked = _Asked([bears.instance_id])
    game.callbacks = asked
    push = _add(game, card_db, "Fatal Push", 0, "hand")
    assert game.cast_spell(0, push, [])
    assert asked.asked == 1
    assert game.stack.items[-1].targets == [bears.instance_id]
    game.resolve_stack()
    assert bears.zone == "graveyard"


def test_a_cast_whose_targets_are_chosen_is_not_asked_again(card_db):
    asked = _Asked([])
    game = _game(card_db, asked)
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    push = _add(game, card_db, "Fatal Push", 0, "hand")
    assert game.cast_spell(0, push, [bears.instance_id])
    assert asked.asked == 0


def test_a_spell_with_only_up_to_targets_is_cast_with_none(card_db):
    asked = _Asked([])
    game = _game(card_db, asked)
    force = _add(game, card_db, "Force of Vigor", 0, "hand")
    assert game.cast_spell(0, force, [], free_cast=True)
    assert asked.asked == 0


def test_the_default_announcement_is_a_legal_choice(card_db):
    """Without an AI, the engine's default reads printed data only: the
    opponent's permanent a creature requirement admits."""
    game = _game(card_db)
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    push = _add(game, card_db, "Fatal Push", 0, "hand")
    assert game.cast_spell(0, push, [])
    assert game.stack.items[-1].targets == [bears.instance_id]


# ── The AI announces the player it aims at ────────────────────────────

def test_the_ai_announces_the_player_its_hand_attack_aims_at(card_db):
    """A player target is the caster's announcement too: the picker names
    the opponent explicitly, the player the spell resolved against when
    it was cast at nothing."""
    from ai.ev_player import EVPlayer
    game = _game(card_db)
    _add(game, card_db, "Grizzly Bears", 1, "hand")
    seize = _add(game, card_db, "Thoughtseize", 0, "hand")
    ai = EVPlayer(player_idx=0, deck_name="Dimir Midrange",
                  rng=random.Random(0))
    assert ai._choose_targets(game, seize) == [PLAYER_TARGET_OPPONENT]


def test_the_ai_is_asked_through_the_runner_callbacks(card_db):
    """The runner's callbacks answer with the caster's own picker."""
    from ai.ev_player import EVPlayer
    from engine.game_runner import AICallbacks
    callbacks = AICallbacks()
    game = _game(card_db, callbacks)
    ai = EVPlayer(player_idx=0, deck_name="Dimir Midrange",
                  rng=random.Random(0))
    callbacks.bind_players([ai])
    _add(game, card_db, "Grizzly Bears", 1, "hand")
    seize = _add(game, card_db, "Thoughtseize", 0, "hand")
    assert game.cast_spell(0, seize, [], free_cast=True)
    assert game.stack.items[-1].targets == [PLAYER_TARGET_OPPONENT]


# ── The auditor (CR 601.2c, restated from the requirements' raw fields) ─

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _required_findings(audit):
    return [f for f in audit.drain()
            if f["rule"] == "601.2c/required_targets_chosen"]


def test_the_audit_records_a_spell_cast_at_nothing(card_db, audit,
                                                   monkeypatch):
    from engine.cast_manager import CastManager
    monkeypatch.setattr(CastManager, "announced_targets",     # the defect
                        staticmethod(lambda g, p, c, t: list(t or [])))
    game = _game(card_db, _Asked([]))
    _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    push = _add(game, card_db, "Fatal Push", 0, "hand")
    assert game.cast_spell(0, push, [])
    assert _required_findings(audit)


def test_the_audit_is_silent_when_every_required_target_is_chosen(
        card_db, audit):
    game = _game(card_db)
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    push = _add(game, card_db, "Fatal Push", 0, "hand")
    assert game.cast_spell(0, push, [bears.instance_id])
    seize = _add(game, card_db, "Thoughtseize", 0, "hand")
    assert game.cast_spell(0, seize, [PLAYER_TARGET_OPPONENT],
                           free_cast=True)
    assert not _required_findings(audit)


# ── The AI does not cast a spell it found no target for ───────────────

def test_a_spell_that_requires_a_target_is_not_worth_casting_without_one(
        card_db):
    from ai.modal import worth_casting_without_targets
    game = _game(card_db)
    push = _add(game, card_db, "Fatal Push", 0, "hand")
    force = _add(game, card_db, "Force of Vigor", 0, "hand")
    assert not worth_casting_without_targets(game, push, 0)
    assert worth_casting_without_targets(game, force, 0), \
        "an 'up to' spell may be cast with no target"


def test_a_response_that_finds_no_target_casts_no_targeted_spell(card_db):
    """The response planner picked removal against a creature spell on the
    stack, found no creature on the battlefield within the spell's reach,
    and cast the removal spell at nothing (the census: Fatal Push 4 of 19,
    March of Otherworldly Light 2 of 19, Erode 1 of 7 response casts)."""
    from ai.response import ResponseDecider
    from ai.turn_planner import TurnPlanner
    from engine.stack import StackItem, StackItemType
    game = _game(card_db)
    game.active_player = 1
    _add(game, card_db, "Devourer of Destiny", 1, "battlefield")  # out of reach
    push = _add(game, card_db, "Fatal Push", 0, "hand")
    threat = CardInstance(template=card_db.get_card("Primeval Titan"), owner=1,
                          controller=1, instance_id=game.next_instance_id(),
                          zone="stack")
    threat._game_state = game
    item = StackItem(item_type=StackItemType.SPELL, source=threat,
                     controller=1)
    game.stack.push(item)
    decider = ResponseDecider(0, TurnPlanner(), strategic_logger=None)
    result = decider.decide_response(game, item)
    assert result is None or result[0] is not push or result[1], result


def _ai(seat=0):
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=seat, deck_name="Dimir Midrange",
                    rng=random.Random(0))


def test_a_player_slot_names_the_player_its_instruction_is_worth_most_to(
        card_db):
    """The one owner of what a player-only slot names
    (`pick_trigger_targets`, read by triggers and modes too): tokens are
    created for their caster; a sacrifice is the opponent's."""
    game = _game(card_db)
    _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    salvation = _add(game, card_db, "Dark Salvation", 0, "hand")
    edict = _add(game, card_db, "Diabolic Edict", 0, "hand")
    ai = _ai()
    assert PLAYER_TARGET_SELF in ai._choose_targets(game, salvation)
    assert ai._choose_targets(game, edict) == [PLAYER_TARGET_OPPONENT]


def test_a_spell_still_lacking_a_required_object_is_not_aimed(card_db):
    """'Put a +1/+1 counter on each creature target player controls.
    Target creature gains ...': with no creature its caster would aim at,
    the picker names no player either -- the spell is not cast, rather
    than aimed and refused."""
    game = _game(card_db)
    offense = _add(game, card_db, "Practiced Offense", 0, "hand")
    assert _ai()._choose_targets(game, offense) == []
    mine = _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    aimed = _ai()._choose_targets(game, offense)
    assert mine.instance_id in aimed and len(aimed) == 2


def test_a_spell_whose_shape_no_pick_knows_is_announced_at_the_default(
        card_db):
    """'Cascade. Target creature can't block this turn': no pick knows the
    shape, so its caster announces the engine's printed-data default -- a
    legal creature, an opponent's first -- rather than not casting it."""
    game = _game(card_db)
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    dread = _add(game, card_db, "Demonic Dread", 0, "hand")
    assert _ai()._choose_targets(game, dread) == [bears.instance_id]
