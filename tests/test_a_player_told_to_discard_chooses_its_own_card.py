"""A player an instruction tells to discard chooses the card it discards
(CR 701.9b: "the player discarding chooses", unless the instruction says
otherwise).

The defect (adversarial review round 2): the discard executor passed
`self_discard=False` for every discarder but the instruction's controller,
so "each opponent discards a card" routed the opponent's own choice
through the CASTER's strip ranking -- the opponent threw away its best
card (the card the caster would have taken) instead of its least useful
one. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.game_state import GameState, Phase
from engine.mana import ManaCost


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


def _probe(game, oracle, controller=0):
    tmpl = CardTemplate(
        name="Discard Probe", card_types=[CardType.ENCHANTMENT],
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


def _game(card_db):
    from engine.game_runner import AICallbacks
    game = GameState(rng=random.Random(0))
    game.callbacks = AICallbacks()
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Dimir Midrange"
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    for p in (0, 1):
        for _ in range(4):
            _add(game, card_db, "Mountain" if p == 0 else "Island", p)
        for _ in range(5):
            _add(game, card_db, "Swamp", p, "library")
    return game


def test_an_opponent_told_to_discard_keeps_its_best_card(card_db):
    game = _game(card_db)
    _probe(game, "Whenever you cast an instant or sorcery spell, each "
                 "opponent discards a card.")
    keep = _add(game, card_db, "Counterspell", 1, "hand")
    for _ in range(4):
        _add(game, card_db, "Island", 1, "hand")      # a flooded hand
    bolt = _add(game, card_db, "Lightning Bolt", 0, "hand")
    assert game.cast_spell(0, bolt, [PLAYER_TARGET_OPPONENT])
    game.resolve_stack()
    gy = [c.name for c in game.players[1].graveyard]
    assert gy == ["Island"], gy
    assert keep in game.players[1].hand


# ── The auditor ────────────────────────────────────────────────────────

import pytest  # noqa: E402


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    from engine import rules_audit
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit, rule):
    return [f for f in audit.drain()
            if f["rule"] == rule and f["kind"] == "violation"]


def _resolve_each_opponent_discards(card_db):
    game = _game(card_db)
    _probe(game, "Whenever you cast an instant or sorcery spell, each "
                 "opponent discards a card.")
    _add(game, card_db, "Counterspell", 1, "hand")
    _add(game, card_db, "Island", 1, "hand")
    bolt = _add(game, card_db, "Lightning Bolt", 0, "hand")
    assert game.cast_spell(0, bolt, [PLAYER_TARGET_OPPONENT])
    game.resolve_stack()


def test_the_discard_chooser_auditor_sees_an_opponents_choice_made_for_it(
        card_db, audit, monkeypatch):
    """Re-create the defect: only the controller chooses its own discard."""
    from engine import effect_executors as ex
    monkeypatch.setattr(ex, "_discarder_chooses",
                        lambda ctx, s, player: player == ctx.controller)
    _resolve_each_opponent_discards(card_db)
    assert _violations(audit, "701.9b/discarder_chooses")


def test_the_discard_chooser_auditor_is_silent_when_the_discarder_chooses(
        card_db, audit):
    _resolve_each_opponent_discards(card_db)
    assert not _violations(audit, "701.9b/discarder_chooses")
