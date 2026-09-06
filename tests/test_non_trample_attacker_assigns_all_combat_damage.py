"""A non-trample attacker must assign ALL its combat damage to its
blockers — the excess over a blocker's lethal is piled onto a blocker,
not silently discarded.

Rule (CR 510.1c): a blocked creature without trample cannot hold back
damage; once it has assigned lethal to each blocker it must assign the
rest to a blocker (conventionally the last). The engine capped each
blocker's assignment at lethal and dropped the leftover unless the
attacker had trample — so a big attacker into a small blocker
under-reported the damage it dealt, and lifelink (which gains life equal
to damage dealt) under-gained (audit: Goryo's vs Domain Zoo, s55641 — a
7/7 lifelink Atraxa into a lone 6-toughness blocker gained 6, not 7).

Card names are fixture carriers (synthetic templates); the mechanic is
non-trample overflow assignment / lifelink magnitude.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Keyword, ManaCost
from engine.combat_manager import CombatManager
from engine.game_state import GameState


def _creature(game, name, controller, power, toughness, keywords=None):
    tmpl = CardTemplate(
        name=name, card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=keywords or set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="battlefield")
    card._game_state = game
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def test_lifelink_attacker_gains_full_power_over_a_small_blocker():
    game = GameState(rng=random.Random(0))
    game.players[1].life = 20
    # 7/7 lifelink attacker for P1, no trample; single 6-toughness blocker.
    attacker = _creature(game, "BigLifelinker", 1, 7, 7, {Keyword.LIFELINK})
    blocker = _creature(game, "SmallWall", 0, 0, 6)

    life_before = game.players[1].life
    cm = CombatManager()
    cm.declare_attackers(game, [attacker], active_player=1)
    cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})
    cm.resolve_combat_damage(game)

    gained = game.players[1].life - life_before
    assert gained == 7, (
        "a non-trample lifelink attacker deals its full power to the blocker "
        f"(assigning the 1 excess over lethal too); gained {gained}, expected 7")


def test_gang_block_overflow_dealt_when_power_exceeds_total_toughness():
    """Power 7 into two 2-toughness blockers: 4 is lethal to both, the
    remaining 3 is still assigned (piled on a blocker) — total 7 dealt."""
    game = GameState(rng=random.Random(0))
    game.players[1].life = 20
    attacker = _creature(game, "BigLifelinker", 1, 7, 7, {Keyword.LIFELINK})
    b1 = _creature(game, "Wall1", 0, 0, 2)
    b2 = _creature(game, "Wall2", 0, 0, 2)
    life_before = game.players[1].life
    cm = CombatManager()
    cm.declare_attackers(game, [attacker], active_player=1)
    cm.declare_blockers(game, {attacker.instance_id: [b1.instance_id, b2.instance_id]})
    cm.resolve_combat_damage(game)
    gained = game.players[1].life - life_before
    assert gained == 7, (
        f"all 7 combat damage must be assigned to blockers (gained {gained})")
