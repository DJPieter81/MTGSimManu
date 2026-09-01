"""Every creature blocking an attacker deals its combat damage back to
that attacker — regardless of whether, or in what order, the attacker
assigns its own damage to the blockers.

Rule (CR 509.2 / 510.1c): the attacker chooses how to divide its power
among its blockers, but each blocking creature independently deals its
own combat damage. A blocker the attacker assigns nothing to (because
the attacker's power ran out on earlier blockers) still deals damage.

The bug: the "blocker deals damage back" step lived INSIDE the
attacker's damage-assignment loop, which breaks once the attacker's
power is exhausted. So a small attacker gang-blocked by more bodies than
its power could spread across took damage from only the first few
blockers and survived a lethal gang block (audit: Boros Energy vs 4c
Omnath, s55622 — a 1/2 double-blocked by two 1/1s took 1, not 2, and
lived).

Card names are fixture carriers (synthetic templates); the mechanic is
combat-damage-back from every blocker.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Keyword, ManaCost
from engine.combat_manager import CombatManager
from engine.game_state import GameState


def _creature(game, name, controller, power=2, toughness=2, keywords=None):
    tmpl = CardTemplate(
        name=name, card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=keywords or set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def test_gang_block_beyond_attacker_power_still_deals_full_damage_back():
    game = GameState(rng=random.Random(0))
    # Attacker power 1 (can only assign to one blocker), toughness 2.
    attacker = _creature(game, "Attacker", 1, power=1, toughness=2)
    b1 = _creature(game, "Blocker1", 0, power=1, toughness=1)
    b2 = _creature(game, "Blocker2", 0, power=1, toughness=1)

    cm = CombatManager()
    cm.declare_attackers(game, [attacker], active_player=1)
    cm.declare_blockers(game, {attacker.instance_id: [b1.instance_id, b2.instance_id]})
    cm.resolve_combat_damage(game)
    game.check_state_based_actions()

    # Both blockers deal 1 back → 2 damage ≥ toughness 2 → the attacker dies.
    dead = attacker.zone != "battlefield"
    assert dead, (
        "an attacker gang-blocked by more power than it can assign to must "
        "still take every blocker's damage back and die when that is lethal "
        f"(damage_marked={attacker.damage_marked}, zone={attacker.zone})"
    )


def test_single_block_unaffected():
    """Regression: a normal single block still deals damage back once."""
    game = GameState(rng=random.Random(0))
    attacker = _creature(game, "Attacker", 1, power=2, toughness=3)
    blocker = _creature(game, "Blocker", 0, power=2, toughness=2)
    cm = CombatManager()
    cm.declare_attackers(game, [attacker], active_player=1)
    cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})
    cm.resolve_combat_damage(game)
    game.check_state_based_actions()
    assert attacker.damage_marked == 2, (
        f"single blocker deals its 2 power back once (got {attacker.damage_marked})"
    )
