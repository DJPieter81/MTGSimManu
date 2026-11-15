"""Flipping a coin is an instruction; "if you win / lose the flip" reads its
outcome (CR 705).

CR 705.1: a player flips a coin and wins or loses the flip, each outcome
equally likely; the engine's seeded rng decides, so the same seed gives
the same flips. An ability that flips a coin and then says "if you win the
flip, <A>. If you lose the flip, <B>" performs A exactly on a win and B
exactly on a loss (CR 608.2c), in printed order, with "you may" and "if you
do" sequenced around them as everywhere else.

The defect: the effect grammar recognised "flip a coin" and refused it, and
typed no condition for the outcome, so no flip resolved through the one
dispatcher. The one flip card in a registered deck was performed by a
legacy shape-specific handler, which also flipped on the opponent's turn
(the trigger says "during your turn") and counted every spell cast this
turn as extra loyalty (the back face counts instant and sorcery spells).

Class (`tools/class_census.py "flip a coin"`): 29 pool cards print it;
the plain "flip a coin" and its two outcome conditions are typed, and the
multi-coin, "until you lose a flip", "for each", heads/tails and
counted-wins forms stay refused. Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.effect_spec import ConditionKind, Stage, Verb, iter_specs
from engine.game_state import GameState, Phase
from engine.mana import ManaCost

RAL = "Ral, Monsoon Mage // Ral, Leyline Prodigy"


class _Rigged(random.Random):
    """A flip that always comes out `outcome`; every other draw seeded."""

    def __init__(self, outcome, seed=0):
        super().__init__(seed)
        self.outcome = outcome

    def choice(self, seq):
        if "win" in seq and "lose" in seq:
            return self.outcome
        return super().choice(seq)


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


def _game(card_db, rng):
    game = GameState(rng=rng)
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for p in (0, 1):
        for _ in range(8):
            _add(game, card_db, "Mountain", p)
        for _ in range(6):
            _add(game, card_db, "Swamp", p, "library")
    return game


def _cast(game, card_db, name, controller=0, targets=None):
    spell = _add(game, card_db, name, controller, "hand")
    assert game.cast_spell(controller, spell, targets or [])
    return spell


def _bolt(game, card_db, controller=0):
    return _cast(game, card_db, "Lightning Bolt", controller,
                 targets=[PLAYER_TARGET_OPPONENT])


def _probe(game, oracle, controller=0):
    tmpl = CardTemplate(
        name="Flip Probe", card_types=[CardType.ENCHANTMENT],
        mana_cost=ManaCost(), supertypes=[], subtypes=[], power=None,
        toughness=None, loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text=oracle, tags=set())
    card = CardInstance(template=tmpl, owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(),
                        zone="battlefield")
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


_GAMBLE = ("Whenever you cast an instant or sorcery spell, flip a coin. If "
           "you win the flip, you gain 3 life. If you lose the flip, Flip "
           "Probe deals 1 damage to you.")


def _hosts(text):
    from engine.effect_grammar.link import parse_face_hosts
    return parse_face_hosts(text)


# ── The grammar ────────────────────────────────────────────────────────

def test_flip_a_coin_is_a_flip_instruction_whose_outcomes_its_conditions_read():
    (host,) = _hosts(_GAMBLE.replace("Flip Probe", "~"))
    flip, won, lost = list(iter_specs(host.specs))
    assert flip.verb is Verb.FLIP
    assert (won.condition.kind, won.condition.pred) == (ConditionKind.FLIP,
                                                        "won")
    assert (lost.condition.kind, lost.condition.pred) == (ConditionKind.FLIP,
                                                          "lost")
    assert won.condition.ref.index == lost.condition.ref.index == flip.seq


@pytest.mark.parametrize("text", [
    "flip a coin until you lose a flip. create a 1/1 goblin creature token "
    "for each flip you won.",
    "flip a coin for each creature you control.",
    "flip two coins.",
    "flip a coin. if you win one or more flips, draw a card.",
    "flip a coin. if it comes up heads, draw a card.",
])
def test_the_forms_a_single_flip_does_not_cover_stay_refused(text):
    from engine.effect_resolver import can_execute
    (host,) = _hosts(text)
    assert not can_execute(host)
    assert any(s.verb is Verb.UNMODELLED for s in iter_specs(host.specs))


def test_flips_repeated_until_a_loss_are_a_recognised_unsupported_action():
    (host,) = _hosts("flip a coin until you lose a flip.")
    (s,) = host.specs
    assert s.verb is Verb.UNMODELLED
    assert s.payload.stage is Stage.RECOGNIZED_UNSUPPORTED


# ── Resolution ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("outcome, life, probe_damage", [
    ("win", 23, 0),          # the won branch: +3; the lost branch is not
    ("lose", 19, 0),         # the lost branch: 1 to you, none to the probe
])
def test_the_won_branch_runs_on_a_win_and_the_lost_branch_on_a_loss(
        card_db, outcome, life, probe_damage):
    game = _game(card_db, _Rigged(outcome))
    probe = _probe(game, _GAMBLE)
    _bolt(game, card_db)
    assert game.players[0].life == life
    assert probe.damage_marked == probe_damage


def test_the_same_seed_gives_the_same_flips(card_db):
    def flips(seed):
        game = _game(card_db, random.Random(seed))
        _probe(game, _GAMBLE)
        for _ in range(6):
            _bolt(game, card_db)
        return [line for line in game.log if "coin flip" in line]
    first = flips(7)
    assert len(first) == 6
    assert flips(7) == first


def test_a_during_your_turn_cast_trigger_does_not_flip_on_another_turn(
        card_db):
    game = _game(card_db, _Rigged("lose"))
    _probe(game, _GAMBLE.replace("sorcery spell,", "sorcery spell during "
                                 "your turn,"))
    game.active_player = 1                      # the opponent's turn
    _bolt(game, card_db)
    assert game.players[0].life == 20
    assert not [line for line in game.log if "coin flip" in line]
    game.active_player = 0
    _bolt(game, card_db)
    assert game.players[0].life == 19


# ── A real member of the class, through the cast carrier ───────────────

def _ral(game, card_db):
    return _add(game, card_db, RAL, 0)


def test_a_lost_flip_deals_the_damage_to_the_triggers_controller(card_db):
    game = _game(card_db, _Rigged("lose"))
    ral = _ral(game, card_db)
    _bolt(game, card_db)
    assert game.players[0].life == 19
    assert ral.damage_marked == 0
    assert ral.zone == "battlefield" and not ral.is_transformed


def test_a_won_flip_exiles_the_permanent_and_returns_it_transformed(card_db):
    """"You may exile ~. If you do, return ~ to the battlefield
    transformed": a new back-face object (CR 400.7, 712) entering with its
    printed loyalty plus one counter for each instant and sorcery spell
    its controller cast this turn (CR 614.1c) -- the creature spell cast
    first is not one of them."""
    game = _game(card_db, _Rigged("win"))
    ral = _ral(game, card_db)
    entry = ral.battlefield_entry_seq
    _cast(game, card_db, "Memnite")             # a creature spell
    _bolt(game, card_db)
    assert ral.zone == "battlefield" and ral.is_transformed
    assert ral.battlefield_entry_seq == entry + 1
    assert ral.loyalty_counters == ral.template.back_face_loyalty + 1


def test_lost_flips_kill_the_controller_not_the_permanent(card_db):
    game = _game(card_db, _Rigged("lose"))
    ral = _ral(game, card_db)
    game.players[0].life = 2
    for _ in range(3):
        if game.game_over:
            break
        _bolt(game, card_db)
    game.check_state_based_actions()
    assert ral.zone == "battlefield"
    assert game.players[0].life <= 0
    assert game.game_over


def test_the_legacy_coin_flip_handler_is_gone():
    from engine import oracle_resolver
    from engine.cards import CardTemplate as T
    assert not hasattr(oracle_resolver, "_handle_coin_flip_transform")
    assert "has_coin_flip" not in T.__dataclass_fields__


# ── The auditors ───────────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit, rule):
    return [f for f in audit.drain()
            if f["rule"] == rule and f["kind"] == "violation"]


def test_the_flip_auditor_sees_a_branch_performed_on_the_other_outcome(
        card_db, audit, monkeypatch):
    """Re-create the defect: every flip condition reads as holding."""
    from engine import effect_resolver
    monkeypatch.setitem(effect_resolver.CONDITION_EVALUATORS,
                        ConditionKind.FLIP, lambda ctx, cond: True)
    game = _game(card_db, _Rigged("lose"))
    _probe(game, _GAMBLE)
    _bolt(game, card_db)
    assert _violations(audit, "705.1/flip_outcome_gates")


def test_the_flip_auditor_is_silent_when_the_outcome_gates_its_branches(
        card_db, audit):
    for outcome in ("win", "lose"):
        game = _game(card_db, _Rigged(outcome))
        _probe(game, _GAMBLE)
        _bolt(game, card_db)
    assert not _violations(audit, "705.1/flip_outcome_gates")
