"""A creature put into a graveyard from the battlefield dies, whatever puts
it there (CR 700.4).

"Dies" means "is put into a graveyard from the battlefield": destruction,
lethal damage, a sacrifice paid as a cost, a sacrifice an effect demands
(evoke, an edict, annihilator) -- each is a death, so the creature's own
"when this dies" abilities and every "whenever a creature dies" observer
trigger, and it counts as a creature that died this turn. A permanent that
is not a creature does not die.

The death owner (`PermanentEffects._creature_dies`) ran the death effects
only for the paths that called it; a sacrifice moved through the zone funnel
directly, so a creature sacrificed for Goblin Bombardment, an evoked
creature's sacrifice and an annihilator sacrifice triggered nothing and
counted for nothing.

Card names are fixture carriers: hundreds of pool cards sacrifice creatures.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game(active=0):
    g = GameState(rng=random.Random(0))
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = active
    g.turn_number = 5
    return g


def _put(game, card_db, name, zone, idx=0):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def test_a_creature_moved_to_the_graveyard_through_the_zone_funnel_dies(card_db):
    """The death count and a "whenever a creature you control dies"
    observer (drain 1) see the move."""
    game = _game()
    _put(game, card_db, "Bastion of Remembrance", "battlefield")
    bears = _put(game, card_db, "Grizzly Bears", "battlefield")
    lives = [p.life for p in game.players]
    game.zone_mgr.move_card(game, bears, "battlefield", "graveyard",
                            cause="sacrifice")
    assert bears.zone == "graveyard"
    assert game.players[0].creatures_died_this_turn == 1
    assert [p.life for p in game.players] == [lives[0] + 1, lives[1] - 1]


def test_a_creature_sacrificed_to_pay_a_cost_dies(card_db):
    """Undying (CR 702.93a) is a dies trigger: the creature sacrificed for
    the activation returns with a +1/+1 counter."""
    from engine.activation import ActivationManager
    game = _game()
    bomb = _put(game, card_db, "Goblin Bombardment", "battlefield")
    wolf = _put(game, card_db, "Young Wolf", "battlefield")
    ability = bomb.template.activated_abilities[0]
    assert ActivationManager.activate(game, 0, bomb, ability, targets=[-1])
    assert wolf.zone == "battlefield" and wolf.plus_counters == 1
    assert wolf.battlefield_entry_seq == 2          # a new object


def test_a_permanent_that_is_not_a_creature_does_not_die(card_db):
    game = _game()
    _put(game, card_db, "Bastion of Remembrance", "battlefield")
    land = _put(game, card_db, "Mountain", "battlefield")
    lives = [p.life for p in game.players]
    game.zone_mgr.move_card(game, land, "battlefield", "graveyard")
    assert land.zone == "graveyard"
    assert game.players[0].creatures_died_this_turn == 0
    assert [p.life for p in game.players] == lives


def test_a_death_is_processed_once(card_db):
    """The death owner performs the move; it is not handed back to itself."""
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield")
    game._creature_dies(bears)
    assert bears.zone == "graveyard"
    assert game.players[0].creatures_died_this_turn == 1
    assert sum(1 for line in game.log if line.endswith("Grizzly Bears dies")) == 1


# ── Auditor (CR 700.4) ─────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def test_the_audit_records_a_creature_reaching_a_graveyard_without_dying(
        card_db, audit, monkeypatch):
    from engine.zone_manager import ZoneManager
    monkeypatch.setattr(ZoneManager, "_dies",
                        staticmethod(lambda card, from_zone, to_zone: False))
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield")
    game.zone_mgr.move_card(game, bears, "battlefield", "graveyard")
    assert "700.4/dies" in [f["rule"] for f in audit.drain()]


def test_the_audit_is_silent_when_the_creature_dies(card_db, audit):
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield")
    land = _put(game, card_db, "Mountain", "battlefield")
    game.zone_mgr.move_card(game, bears, "battlefield", "graveyard")
    game.zone_mgr.move_card(game, land, "battlefield", "graveyard")
    assert "700.4/dies" not in [f["rule"] for f in audit.drain()]
