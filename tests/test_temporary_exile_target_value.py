"""Choosing the target of an "exile until the next end step" effect.

The exiled permanent comes back as a new object before its controller's
next turn (CR 400.7), so the exile removes only what changes across that
return. For a planeswalker that is its loyalty above printed — it returns
at printed loyalty (CR 306.5b) — less any combat damage already headed at
it, which the exile cancels (CR 506.4).

Rules pinned:
* a walker at printed loyalty that the attacker is attacking is never the
  exile's target (the exile would undo the attack and remove nothing);
* a walker grown above printed loyalty and not under attack is worth the
  share of it the reset removes;
* combat damage aimed at the walker is subtracted from that share.
Card names are fixture carriers only.
"""
from __future__ import annotations

import random

import pytest

from ai.temporary_exile import temporary_exile_share
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.combat_manager import CombatManager
from engine.game_state import GameState


def _perm(game, controller, *, types, power=None, toughness=None, loyalty=None, name="P"):
    tmpl = CardTemplate(
        name=name, card_types=types, mana_cost=ManaCost(generic=3),
        supertypes=[], subtypes=[], power=power, toughness=toughness, loyalty=loyalty,
        keywords=set(), abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text="", tags=set())
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.summoning_sick = False
    if loyalty is not None:
        c.loyalty_counters = loyalty
    game.players[controller].battlefield.append(c)
    return c


def _attack(game, attacker, walker):
    CombatManager().declare_attackers(game, [attacker], active_player=attacker.controller,
                                      attack_targets={attacker.instance_id: walker})


def test_a_walker_at_printed_loyalty_under_attack_is_worth_nothing_to_exile():
    game = GameState(rng=random.Random(0))
    walker = _perm(game, 1, types=[CardType.PLANESWALKER], loyalty=4, name="Walker")
    bear = _perm(game, 0, types=[CardType.CREATURE], power=2, toughness=2, name="Bear")
    _attack(game, bear, walker)
    assert bear.attacked_planeswalker is walker
    assert temporary_exile_share(walker, game, 0) == 0


def test_a_grown_walker_not_under_attack_is_worth_the_share_its_reset_removes():
    game = GameState(rng=random.Random(0))
    walker = _perm(game, 1, types=[CardType.PLANESWALKER], loyalty=4, name="Walker")
    walker.loyalty_counters = 7
    assert temporary_exile_share(walker, game, 0) == pytest.approx(3 / 7)


def test_damage_already_aimed_at_a_walker_is_subtracted_from_the_exile_share():
    game = GameState(rng=random.Random(0))
    walker = _perm(game, 1, types=[CardType.PLANESWALKER], loyalty=4, name="Walker")
    walker.loyalty_counters = 7
    bear = _perm(game, 0, types=[CardType.CREATURE], power=2, toughness=2, name="Bear")
    _attack(game, bear, walker)
    assert temporary_exile_share(walker, game, 0) == pytest.approx(1 / 7)


def test_a_nonwalker_keeps_its_whole_value_as_an_exile_target():
    game = GameState(rng=random.Random(0))
    bear = _perm(game, 1, types=[CardType.CREATURE], power=2, toughness=2, name="Bear")
    assert temporary_exile_share(bear, game, 0) == 1


def test_the_attack_trigger_exile_skips_the_walker_its_source_attacks(card_db):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    game = GameState(rng=random.Random(0))
    wt = card_db.get_card("Teferi, Time Raveler")      # a walker with real value
    walker = CardInstance(template=wt, owner=1, controller=1,
                          instance_id=game.next_instance_id(), zone="battlefield")
    walker._game_state = game
    walker.loyalty_counters = wt.loyalty
    game.players[1].battlefield.append(walker)
    t = card_db.get_card("Phelia, Exuberant Shepherd")
    src = CardInstance(template=t, owner=0, controller=0,
                       instance_id=game.next_instance_id(), zone="battlefield")
    src._game_state = game
    src.summoning_sick = False
    game.players[0].battlefield.append(src)
    src.attacking = True
    src.attacked_planeswalker = walker
    EFFECT_REGISTRY.execute(t.name, EffectTiming.ATTACK, game, src, 0)
    assert walker.zone == "battlefield", "the walker stays to take the attack"


def test_a_nonwalker_the_threat_primitive_scores_zero_is_still_exiled(card_db):
    """The exile declines only targets whose exile removes nothing (share
    0). A permanent the threat primitive happens to score 0 is still a
    target, as before this rule."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    game = GameState(rng=random.Random(0))
    inert = _perm(game, 1, types=[CardType.ENCHANTMENT], name="Engine")
    t = card_db.get_card("Phelia, Exuberant Shepherd")
    src = CardInstance(template=t, owner=0, controller=0,
                       instance_id=game.next_instance_id(), zone="battlefield")
    src._game_state = game
    game.players[0].battlefield.append(src)
    EFFECT_REGISTRY.execute(t.name, EffectTiming.ATTACK, game, src, 0)
    assert inert.zone == "exile"
