"""Loyalty-ability and land damage go through the damage owner.

CR 120.3: damage to a creature is marked on it; it is destroyed by
state-based actions (CR 704.5g), not by the effect that dealt it. Damage
to a player makes that player lose that much life (CR 120.3a), which counts
as life lost this turn. CR 115.4 / 702.11b: an effect picks its target
among what its source may target.

A planeswalker's "deals N damage" line picks its recipient on resolution
(it chose none when activated): a killable opposing creature its source may
target, by the shared resolution-time picker
(`target_solver.pick_resolution_target`), else the opponent. A land that
deals damage to its controller for mana does it through
`engine.damage.deal_damage` like every other damage.
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


def _put(game, card_db, idx, name, zone="battlefield"):
    c = CardInstance(template=card_db.get_card(name), owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        game.players[idx].battlefield.append(c)
    return c


def _damage_line(card_db, walker):
    from engine.cards import LoyaltyEffectKind
    t = card_db.get_card(walker)
    return next(a for a in t.loyalty_abilities.values()
                if a.effect_kind is LoyaltyEffectKind.DAMAGE)


def _activate(game, walker_card, ability):
    from engine.planeswalker_manager import PlaneswalkerManager
    PlaneswalkerManager._resolve(game, walker_card.controller, walker_card,
                                 ability)


def test_a_loyalty_ability_damage_goes_through_the_damage_owner_and_death_waits_for_state_based_actions(card_db, monkeypatch):
    from engine import damage
    calls = []
    real = damage.deal_damage
    monkeypatch.setattr(damage, "deal_damage",
                        lambda src, tgt, n, **k: (calls.append((src, tgt, n)),
                                                  real(src, tgt, n, **k))[1])
    game = _game()
    walker = _put(game, card_db, 0, "Wrenn and Six")
    ability = _damage_line(card_db, "Wrenn and Six")
    pest = _put(game, card_db, 1, "Ragavan, Nimble Pilferer")   # a 2/1
    _activate(game, walker, ability)
    assert calls == [(walker, pest, 1)]
    assert pest.zone == "battlefield" and pest.damage_marked == 1
    game.check_state_based_actions()
    assert pest.zone == "graveyard"


def test_a_loyalty_damage_pick_never_takes_a_creature_its_source_may_not_target(card_db):
    game = _game()
    walker = _put(game, card_db, 0, "Wrenn and Six")
    ability = _damage_line(card_db, "Wrenn and Six")
    shielded = _put(game, card_db, 1, "Ragavan, Nimble Pilferer")
    shielded.temp_keywords.add(Keyword.HEXPROOF)
    _activate(game, walker, ability)
    assert shielded.damage_marked == 0
    assert game.players[1].life == 19


def test_a_loyalty_damage_with_nothing_to_kill_goes_to_the_opponent_as_life_lost(card_db):
    game = _game()
    walker = _put(game, card_db, 0, "Wrenn and Six")
    _activate(game, walker, _damage_line(card_db, "Wrenn and Six"))
    assert (game.players[1].life, game.players[1].life_lost_this_turn) == (19, 1)


def test_damage_a_land_deals_its_controller_for_mana_is_life_lost_this_turn(card_db):
    from engine.mana import ManaCost
    game = _game()
    _put(game, card_db, 0, "Shivan Reef")
    assert game.tap_lands_for_mana(0, ManaCost(red=1))
    assert (game.players[0].life, game.players[0].life_lost_this_turn) == (19, 1)


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    yield rules_audit
    rules_audit.reset()


def test_the_loyalty_damage_audit_sees_a_recipient_its_source_may_not_target(audit, card_db, monkeypatch):
    """CR 115.4 / 702.11b, restated at the seam: the creature a loyalty
    damage line picked on resolution is one its source may target.
    Silent when the pick is legal; it fires when the picker is broken
    back to ignoring hexproof."""
    from engine import target_solver
    game = _game()
    walker = _put(game, card_db, 0, "Wrenn and Six")
    ability = _damage_line(card_db, "Wrenn and Six")
    shielded = _put(game, card_db, 1, "Ragavan, Nimble Pilferer")
    shielded.temp_keywords.add(Keyword.HEXPROOF)
    _activate(game, walker, ability)
    assert "115.4/loyalty_damage_target" not in {
        f["rule"] for f in rules_audit.drain() if f["kind"] == "violation"}
    monkeypatch.setattr(target_solver, "pick_resolution_target",
                        lambda game, controller, source, candidates, **kw:
                        max(candidates, key=kw.get("key")) if candidates else None)
    _activate(game, walker, ability)
    assert "115.4/loyalty_damage_target" in {
        f["rule"] for f in rules_audit.drain() if f["kind"] == "violation"}
