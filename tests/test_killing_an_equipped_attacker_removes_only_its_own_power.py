"""Killing an equipped creature removes only the creature's own power;
the Equipment stays on the battlefield (CR 301.5c) and re-attaches.

# Mechanic the tests name

The lifespan-delta block scorer credited a block that kills the attacker
with the attacker's WHOLE power leaving the opponent's board.  For an
equipped attacker most of that power is the Equipment's, and the
Equipment survives the creature: it is re-attached to the next body for
its equip cost.  Pinnacle Affinity vs Goryo's Vengeance s50000 game 1,
turn 4: Griselbrand (7/7 lifelink, the deck's whole engine, blinked to
stay) blocked and traded with a 1/1 Drone carrying Cranial Plating and
Lavaspur Boots (8/1); both equipment stayed, moved onto Memnite, and
Goryo's lost on turn 6.  The scorer saw "8 power removed for 7"; the
rule sees "1 power removed for 7".

The same split applies to a blocker that dies wearing Equipment: the
defender keeps the Equipment's power.  +1/+1 counters, Auras and the
creature's own scaling die with the creature and are NOT persistent.

Class: every Equipment in every deck (Plating, Boots, Shadowspear,
Bonesplitter, the Swords, Batterskull, Kaldra, Colossus Hammer, …), on
both sides of every block decision and every predicted block.  Card
names below are fixture carriers only.
"""
from __future__ import annotations

import random

from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _put(game, card_db, name, controller, zone="battlefield"):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = False
        game.players[controller].battlefield.append(c)
    else:
        game.players[controller].hand.append(c)
    return c


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.DECLARE_BLOCKERS
    game.active_player = 1
    game.turn_number = 8
    for _ in range(4):
        _put(game, card_db, "Swamp", 0)
        _put(game, card_db, "Island", 1)
    game.players[0].life = 15
    game.players[1].life = 13
    return game


def _equip(game, equipment, creature):
    """Attach without paying: the instance tag the engine uses."""
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")
    equipment.attached_to_id = creature.instance_id


def test_power_from_equipment_is_the_equipments_share_only(card_db):
    game = _game(card_db)
    memnite = _put(game, card_db, "Memnite", 1)
    assert memnite.equipment_power_bonus() == 0
    splitter = _put(game, card_db, "Bonesplitter", 1)          # +2/+0
    _equip(game, splitter, memnite)
    assert memnite.power == 3
    assert memnite.equipment_power_bonus() == 2
    memnite.plus_counters += 2
    assert memnite.power == 5
    assert memnite.equipment_power_bonus() == 2                 # counters are the creature's own


def _trade_delta(game, card_db, attacker, blocker):
    ai = EVPlayer(player_idx=0, deck_name="Goryo's Vengeance", rng=random.Random(0))
    me, opp = game.players[0], game.players[1]
    return ai._score_block_lifespan_delta(
        game, attacker, blocker, my_life=me.life,
        my_power=sum(c.power or 0 for c in me.creatures),
        opp_power=sum(c.power or 0 for c in opp.creatures))


def test_a_trade_into_an_equipped_attacker_scores_below_the_same_trade_into_a_natural_one(card_db):
    """Two attackers with identical 3/1 stats: one is a 1/1 carrying a
    +2/+0 Equipment, the other has its power as its own.  A 1/1 blocker
    trades with either.  Killing the natural one removes 3 power from
    the opponent's board; killing the equipped one removes 1."""
    game = _game(card_db)
    blocker = _put(game, card_db, "Memnite", 0)
    equipped = _put(game, card_db, "Memnite", 1)
    splitter = _put(game, card_db, "Bonesplitter", 1)
    _equip(game, splitter, equipped)
    natural = _put(game, card_db, "Memnite", 1)
    natural.plus_counters += 2
    natural.temp_toughness_mod -= 2                             # 3/1, all its own
    assert (equipped.power, equipped.toughness) == (3, 1)
    assert (natural.power, natural.toughness) == (3, 1)
    d_equipped = _trade_delta(game, card_db, equipped, blocker)
    d_natural = _trade_delta(game, card_db, natural, blocker)
    assert d_equipped < d_natural


def test_the_engine_creature_does_not_trade_with_an_equipment_carrier(card_db):
    """The replayed decision: a 7/7 lifelink engine facing a 1/1 that
    carries seven points of Equipment power.  The trade removes one
    point of the opponent's power for seven of the defender's plus the
    engine's non-combat worth — the block must score below not
    blocking."""
    game = _game(card_db)
    griselbrand = _put(game, card_db, "Griselbrand", 0)
    carrier = _put(game, card_db, "Memnite", 1)
    for _ in range(6):
        _put(game, card_db, "Darksteel Citadel", 1)           # artifacts for the Plating count
    plating = _put(game, card_db, "Cranial Plating", 1)
    boots = _put(game, card_db, "Lavaspur Boots", 1)
    _equip(game, plating, carrier)
    _equip(game, boots, carrier)
    assert carrier.power >= 8 and carrier.toughness == 1
    assert _trade_delta(game, card_db, carrier, griselbrand) < 0


def test_a_blocker_that_dies_wearing_equipment_keeps_the_equipments_power_for_its_side(card_db):
    game = _game(card_db)
    attacker = _put(game, card_db, "Tarmogoyf", 1)
    attacker.temp_power_mod += 4                                 # big enough to kill either blocker
    equipped = _put(game, card_db, "Memnite", 0)
    splitter = _put(game, card_db, "Bonesplitter", 0)
    _equip(game, splitter, equipped)
    natural = _put(game, card_db, "Memnite", 0)
    natural.plus_counters += 2
    natural.temp_toughness_mod -= 2
    assert (equipped.power, equipped.toughness) == (natural.power, natural.toughness)
    # Chumping with the equipped body costs the defender 1 power of board;
    # chumping with the natural body costs 3.
    d_equipped = _trade_delta(game, card_db, attacker, equipped)
    d_natural = _trade_delta(game, card_db, attacker, natural)
    assert d_equipped > d_natural


def test_the_predicted_block_uses_the_same_split(card_db):
    """The turn planner's block prediction (the attacker's model of how
    the defender will block) carries the Equipment share on the
    VirtualCreature and scores the trade the same way."""
    from ai.turn_planner import CombatPlanner, VirtualCreature, _OppLifeSnap
    from ai.turn_planner import extract_virtual_board
    game = _game(card_db)
    _put(game, card_db, "Memnite", 0)
    equipped = _put(game, card_db, "Memnite", 1)
    splitter = _put(game, card_db, "Bonesplitter", 1)
    _equip(game, splitter, equipped)
    board = extract_virtual_board(game, 1)
    v_att = next(c for c in board.my_creatures if c.instance_id == equipped.instance_id)
    assert v_att.power == 3 and v_att.equipment_power == 2
    v_blk = board.opp_creatures[0]
    planner = CombatPlanner()
    natural = v_att.copy()
    natural.equipment_power = 0
    # Attacking side's power total 6 (other attackers on board), so the
    # post-block clock is not saturated at either end.
    d_equipped = planner._predict_block_score(v_att, v_blk, board, 1, 6)
    d_natural = planner._predict_block_score(natural, v_blk, board, 1, 6)
    assert d_equipped < d_natural
