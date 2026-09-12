"""Combat damage is dealt in steps decided per CREATURE, not per attacker
(CR 510.2, 510.4, 702.7b):

- If any creature in combat — attacker OR blocker — has first or double
  strike, there is a first-strike damage step in which exactly those
  creatures deal damage, then a regular step in which every other
  creature still in combat deals damage (double strikers deal in both).
- A creature deals combat damage in a step iff its OWN keywords put it
  there; whether its opponent in the block is in the same step is
  irrelevant. A creature removed in the first-strike step deals nothing
  in the regular step.

The bug: `resolve_combat_damage` split ATTACKERS into the two steps and
the blockers' damage back lived inside each attacker's assignment loop,
gated by `blocker_has_fs == first_strike_step`. So a first-strike
blocker of a vanilla attacker (visited only in the regular step) and a
vanilla blocker of a first-strike attacker (visited only in the
first-strike step) never dealt damage at all. In the 2026-09-12 replays
every creature Domain Zoo controls has first strike under Scion of Draco
+ Leyline of the Guildpact: Scion blocked Ajani and nothing happened
(s60304 L1042-1060), Scion and Kavu blocked two Emissaries with no damage
step (s60301 L578-585), a first-strike Frog attacked into a 3/3 and
survived (s60304 L1253-1272).

Synthetic templates; the mechanic is the two damage steps.
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
        keywords=set(keywords or ()), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="battlefield")
    card._game_state = game
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _fight(attacker_pt, attacker_kw, blocker_pt, blocker_kw):
    game = GameState(rng=random.Random(0))
    a = _creature(game, "Attacker", 1, *attacker_pt, keywords=attacker_kw)
    b = _creature(game, "Blocker", 0, *blocker_pt, keywords=blocker_kw)
    cm = CombatManager()
    cm.declare_attackers(game, [a], active_player=1)
    cm.declare_blockers(game, {a.instance_id: [b.instance_id]})
    cm.resolve_combat_damage(game)
    game.check_state_based_actions()
    return game, a, b


FS = {Keyword.FIRST_STRIKE}
DS = {Keyword.DOUBLE_STRIKE}


def test_a_first_strike_blocker_deals_its_damage_before_a_vanilla_attacker():
    game, a, b = _fight((1, 2), set(), (4, 4), FS)
    assert a.zone != "battlefield", (
        f"a 4/4 first-strike blocker must kill a 1/2 in the first-strike step "
        f"(attacker damage_marked={a.damage_marked}, zone={a.zone})")
    assert b.damage_marked == 0, "the attacker died before the regular step and dealt nothing"


def test_a_vanilla_blocker_deals_regular_damage_to_a_first_strike_attacker_that_survived():
    game, a, b = _fight((2, 3), FS, (3, 3), set())
    assert b.damage_marked == 2, "the first striker dealt its 2 in the first-strike step"
    assert a.zone != "battlefield", (
        f"the 3/3 survived the first-strike step and deals 3 back in the regular "
        f"step (attacker damage_marked={a.damage_marked}, zone={a.zone})")


def test_a_double_striker_deals_damage_in_both_steps_and_a_first_striker_once():
    # 2/5 double strike into a 1/5 blocker: 2 + 2 = 4 damage on the blocker.
    game, a, b = _fight((2, 5), DS, (1, 5), set())
    assert b.damage_marked == 4, f"double strike deals in both steps (got {b.damage_marked})"
    assert a.damage_marked == 1, "the blocker deals its regular damage once"
    # 2/5 first strike into a 1/5 blocker: 2, once.
    game, a, b = _fight((2, 5), FS, (1, 5), set())
    assert b.damage_marked == 2, f"first strike deals once (got {b.damage_marked})"
    assert a.damage_marked == 1


def test_a_blocker_killed_in_the_first_strike_step_deals_nothing():
    game, a, b = _fight((3, 3), FS, (3, 2), set())
    assert b.zone != "battlefield"
    assert a.damage_marked == 0, (
        f"a blocker removed in the first-strike step deals no regular damage "
        f"(attacker damage_marked={a.damage_marked})")


def test_an_unblocked_vanilla_attacker_still_hits_the_player_when_a_first_striker_is_in_combat():
    """The regular step must run for every creature still in combat, not
    only when an attacker without first strike exists."""
    game = GameState(rng=random.Random(0))
    fs = _creature(game, "FS", 1, 2, 2, keywords=FS)
    van = _creature(game, "Vanilla", 1, 3, 3)
    blocker = _creature(game, "Blocker", 0, 1, 1)
    cm = CombatManager()
    cm.declare_attackers(game, [fs, van], active_player=1)
    cm.declare_blockers(game, {fs.instance_id: [blocker.instance_id]})
    life_before = game.players[0].life
    cm.resolve_combat_damage(game)
    game.check_state_based_actions()
    assert game.players[0].life == life_before - 3
    assert blocker.zone != "battlefield"
