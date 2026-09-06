"""Outside an emergency, a creature is not thrown in front of an attacker
when what it gives up by dying (its non-combat worth: mana production,
unbounded-engine membership, activated abilities, equipment ceiling)
exceeds what the block saves.

The lifespan-delta block scorer compared block vs no-block post-states in
which a dead blocker cost exactly its power — so a zero-power mana
creature, and the half of a just-assembled unbounded mana engine, chump-
blocked a 2/1 at 16 life (Creatures Toolbox vs Boros s50000 G2 T4/T5).
The blocker's non-combat worth is `ai.clock.opportunity_cost` minus its
combat term, charged to the block post-state as virtual life, the scale
`life_as_resource` already converts to survival turns.

Card names are fixture carriers only.
"""
from __future__ import annotations

import random

from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _bf(game, card_db, name, controller=0):
    t = card_db.get_card(name)
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "Creatures Toolbox"
    game.players[1].deck_name = "Boros Energy"
    game.players[0].life = 16
    game.players[1].life = 22
    game.turn_number = 4
    game.active_player = 1
    game.current_phase = Phase.COMBAT_DECLARE_BLOCKERS if hasattr(
        Phase, "COMBAT_DECLARE_BLOCKERS") else Phase.MAIN1
    return game


def test_engine_half_is_not_chumped_outside_emergency(card_db):
    game = _game(card_db)
    druid = _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Vizier of Remedies")
    _bf(game, card_db, "Fiend Artisan")
    attacker = _bf(game, card_db, "Grizzly Bears", controller=1)
    ai = EVPlayer(player_idx=0, deck_name="Creatures Toolbox",
                  rng=random.Random(0))
    blocks = ai.decide_blockers(game, [attacker])
    chosen = {bid for ids in blocks.values() for bid in ids}
    assert druid.instance_id not in chosen, blocks


def test_mana_creature_is_a_worse_chump_than_an_equal_body(card_db):
    """Two 0/2s, one taps for mana: when a block is taken, the body with
    nothing else to give is the one spent."""
    game = _game(card_db)
    game.players[0].life = 5
    druid = _bf(game, card_db, "Devoted Druid")
    thopter = _bf(game, card_db, "Ornithopter")
    attacker = _bf(game, card_db, "Grizzly Bears", controller=1)
    ai = EVPlayer(player_idx=0, deck_name="Creatures Toolbox",
                  rng=random.Random(0))
    blocks = ai.decide_blockers(game, [attacker])
    chosen = {bid for ids in blocks.values() for bid in ids}
    assert thopter.instance_id in chosen and druid.instance_id not in chosen, blocks


def test_a_valueless_body_still_chumps_when_the_block_helps(card_db):
    """Control: the change prices non-combat worth, it does not ban
    zero-power blocks — a 0/2 with nothing else to give still blocks."""
    game = _game(card_db)
    game.players[0].life = 5
    thopter = _bf(game, card_db, "Ornithopter")
    attacker = _bf(game, card_db, "Grizzly Bears", controller=1)
    ai = EVPlayer(player_idx=0, deck_name="Creatures Toolbox",
                  rng=random.Random(0))
    blocks = ai.decide_blockers(game, [attacker])
    chosen = {bid for ids in blocks.values() for bid in ids}
    assert thopter.instance_id in chosen, blocks


def test_second_blocker_for_a_kill_is_the_cheapest_adequate_one(card_db):
    """When a first blocker cannot kill the attacker alone, the added
    second blocker is chosen by what it gives up, not by list order —
    an engine half is not spent to finish a 1/2."""
    game = _game(card_db)
    game.players[0].life = 6
    _bf(game, card_db, "Devoted Druid")
    vizier = _bf(game, card_db, "Vizier of Remedies")   # 2/1 — dies to 1 dmg
    thopter = _bf(game, card_db, "Ornithopter")         # 0/2
    bear = _bf(game, card_db, "Grizzly Bears")          # 2/2 — kills a 1/2 alone
    attacker = _bf(game, card_db, "Guide of Souls", controller=1)  # 1/2
    ai = EVPlayer(player_idx=0, deck_name="Creatures Toolbox",
                  rng=random.Random(0))
    blocks = ai.decide_blockers(game, [attacker])
    chosen = {bid for ids in blocks.values() for bid in ids}
    assert vizier.instance_id not in chosen, blocks


def test_all_in_attack_keeps_home_a_creature_worth_more_than_its_damage(card_db):
    """Racing sends everything with combat value — but a creature whose
    non-combat worth (an engine half) exceeds the damage it would add is
    not thrown into blockers to add two points. Power and non-combat worth
    are both life-point units, so the comparison needs no conversion."""
    game = _game(card_db)
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.players[0].life = 9
    game.players[1].life = 2   # the vanilla body alone is on-board lethal
    _bf(game, card_db, "Devoted Druid")
    vizier = _bf(game, card_db, "Vizier of Remedies")   # 2/1 engine half
    bear = _bf(game, card_db, "Grizzly Bears")          # 2/2 vanilla
    _bf(game, card_db, "Ranger-Captain of Eos", controller=1)  # 3/3 blocker
    ai = EVPlayer(player_idx=0, deck_name="Creatures Toolbox",
                  rng=random.Random(0))
    attackers = ai.decide_attackers(game)
    names = {c.name for c in attackers}
    assert "Vizier of Remedies" not in names, names
    assert "Grizzly Bears" in names, names
