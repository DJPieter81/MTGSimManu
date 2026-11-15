"""A target is the object that was targeted (CR 608.2b, 400.7).

CR 608.2b: as a spell or ability resolves, it checks whether its targets are
still legal. A target that left the zone it was in when it was targeted is
illegal -- and a permanent that left the battlefield and returned is a new
object (CR 400.7), not the one that was targeted. A target that can no
longer be targeted by the source (hexproof gained, CR 702.11b; protection,
702.16b) is illegal too. If every target is illegal, the spell or ability
does not resolve; activated abilities check exactly as spells do.

The engine snapshots each card target's zone, and a permanent's battlefield
entry, when the target is chosen (`StackItem.target_zones` /
`target_entry_seqs`, one helper for casting and activating), and
`ResolutionManager` re-checks them on resolution.
"""
from __future__ import annotations

import random

import pytest

from engine import rules_audit
from engine.cards import CardInstance, Keyword
from engine.game_state import GameState, Phase


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


def _put(game, card_db, idx, name, zone):
    c = CardInstance(template=card_db.get_card(name), owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        game.players[idx].battlefield.append(c)
    elif zone == "hand":
        game.players[idx].hand.append(c)
    return c


def _cast_bolt_at(game, card_db, target):
    from engine.cast_manager import CastManager
    _put(game, card_db, 0, "Mountain", "battlefield")
    bolt = _put(game, card_db, 0, "Lightning Bolt", "hand")
    assert CastManager.cast_spell(game, 0, bolt, [target.instance_id])
    return bolt


def _activate_ping_at(game, card_db, target):
    from engine.activation import ActivationManager
    pinger = _put(game, card_db, 0, "Prodigal Pyromancer", "battlefield")
    ability = pinger.template.activated_abilities[0]
    assert ActivationManager.activate(game, 0, pinger, ability,
                                      [target.instance_id])
    return pinger


def _resolve_all(game):
    while not game.stack.is_empty:
        game.resolve_stack()


def test_casting_and_activating_record_each_card_targets_zone_and_object(card_db):
    game = _game()
    a = _put(game, card_db, 1, "Ragavan, Nimble Pilferer", "battlefield")
    _cast_bolt_at(game, card_db, a)
    _activate_ping_at(game, card_db, a)
    for item in game.stack.items:
        assert item.target_zones == {a.instance_id: "battlefield"}
        assert item.target_entry_seqs == {a.instance_id: a.battlefield_entry_seq}


def test_a_spell_whose_only_target_left_and_returned_does_not_resolve(card_db):
    game = _game()
    target = _put(game, card_db, 1, "Ragavan, Nimble Pilferer", "battlefield")
    _cast_bolt_at(game, card_db, target)
    game._blink_permanent(target, 1)          # a new object (CR 400.7)
    _resolve_all(game)
    game.check_state_based_actions()
    assert target.zone == "battlefield" and target.damage_marked == 0
    assert game.players[1].life == 20
    assert any("fizzles" in line for line in game.log)


def test_a_spell_whose_only_target_gained_hexproof_does_not_resolve(card_db):
    game = _game()
    target = _put(game, card_db, 1, "Ragavan, Nimble Pilferer", "battlefield")
    _cast_bolt_at(game, card_db, target)
    target.temp_keywords.add(Keyword.HEXPROOF)
    _resolve_all(game)
    assert target.damage_marked == 0 and game.players[1].life == 20


def test_an_activated_ability_whose_only_target_left_does_not_resolve(card_db):
    game = _game()
    target = _put(game, card_db, 1, "Ragavan, Nimble Pilferer", "battlefield")
    _activate_ping_at(game, card_db, target)
    game.zone_mgr.move_card(game, target, "battlefield", "graveyard",
                            cause="test: dies in response")
    _resolve_all(game)
    assert game.players[1].life == 20        # not redirected to a player
    assert any("fizzles" in line for line in game.log)


def test_an_activated_ability_whose_target_is_still_there_resolves(card_db):
    game = _game()
    target = _put(game, card_db, 1, "Ragavan, Nimble Pilferer", "battlefield")
    _activate_ping_at(game, card_db, target)
    _resolve_all(game)
    game.check_state_based_actions()
    assert target.zone == "graveyard"        # 1 damage kills a 2/1


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    yield rules_audit
    rules_audit.reset()


def _violations(findings):
    return {f["rule"] for f in findings if f["kind"] == "violation"}


def test_the_resolve_target_audit_sees_a_spell_resolving_against_a_new_object(audit, card_db, monkeypatch):
    """CR 608.2b / 400.7, restated from the raw snapshot: an item whose
    every card target is gone, a new object or untargetable must not
    resolve. Silent when the engine fizzles it; it fires when the fizzle
    check is broken."""
    from engine.spell_resolution import ResolutionManager
    game = _game()
    target = _put(game, card_db, 1, "Ragavan, Nimble Pilferer", "battlefield")
    _cast_bolt_at(game, card_db, target)
    game._blink_permanent(target, 1)
    _resolve_all(game)
    assert "608.2b/resolve_target" not in _violations(rules_audit.drain())
    game = _game()
    target = _put(game, card_db, 1, "Ragavan, Nimble Pilferer", "battlefield")
    _cast_bolt_at(game, card_db, target)
    game._blink_permanent(target, 1)
    monkeypatch.setattr(ResolutionManager, "_spell_fizzles",
                        staticmethod(lambda g, item: False))
    _resolve_all(game)
    assert "608.2b/resolve_target" in _violations(rules_audit.drain())
