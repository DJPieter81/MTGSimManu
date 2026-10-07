"""A spell or ability resolves against a target that is legal and that met
ward — the one chosen when it was put on the stack, or, when nothing was
chosen then, a pick made on resolution that obeys the same rules.

* CR 601.2c / 608.2b: the target chosen as the spell was cast is the one
  it affects; a handler must not re-pick a different "best" permanent.
* CR 702.11d / 702.16b: a pick made on resolution never chooses a
  permanent the source may not target (hexproof, protection).
* CR 702.21a: a pick made on resolution meets the target's ward; a ward
  its controller would not get past is not a target at all.
* Resolution-time "deal N damage to any target" picks follow the same
  rules and deal their damage through the damage owner.
Card names are fixture carriers (real handlers resolved through the
registry; synthetic targets).
"""
from __future__ import annotations

import random

from engine.card_effects import EFFECT_REGISTRY, EffectTiming
from engine.cards import CardInstance, CardTemplate, CardType, Color, Keyword, ManaCost
from engine.game_state import GameState


def _perm(game, controller, *, types, name, cmc=2, power=None, toughness=None,
          colors=(), keywords=(), ward=0, ward_life=0, oracle=""):
    tmpl = CardTemplate(
        name=name, card_types=types, mana_cost=ManaCost(generic=cmc),
        supertypes=[], subtypes=[], power=power, toughness=toughness, loyalty=None,
        keywords=set(keywords), abilities=[], color_identity=set(colors),
        produces_mana=[], enters_tapped=False, oracle_text=oracle, tags=set(),
        ward_cost=ward, ward_life_cost=ward_life)
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _spell(game, card_db, name, controller=0):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone="stack")
    c._game_state = game
    return c


def test_a_resolving_spell_affects_the_target_chosen_when_it_was_cast(card_db):
    game = GameState(rng=random.Random(0))
    big = _perm(game, 1, types=[CardType.ARTIFACT], name="Big Rock", cmc=6)
    small = _perm(game, 1, types=[CardType.ARTIFACT], name="Small Rock", cmc=1)
    cmd = _spell(game, card_db, "Kolaghan's Command")
    EFFECT_REGISTRY.execute(cmd.template.name, EffectTiming.SPELL_RESOLVE, game, cmd, 0,
                            targets=[small.instance_id])
    assert small.zone == "graveyard" and big.zone == "battlefield"


def test_a_resolution_pick_never_chooses_a_permanent_it_may_not_target(card_db):
    game = GameState(rng=random.Random(0))
    shielded = _perm(game, 1, types=[CardType.CREATURE], name="Shielded", cmc=5,
                     power=5, toughness=5, colors=[Color.RED], keywords=[Keyword.HEXPROOF])
    plain = _perm(game, 1, types=[CardType.CREATURE], name="Plain", cmc=1,
                  power=1, toughness=1, colors=[Color.BLACK])
    purge = _spell(game, card_db, "Celestial Purge")
    EFFECT_REGISTRY.execute(purge.template.name, EffectTiming.SPELL_RESOLVE, game, purge, 0)
    assert shielded.zone == "battlefield"
    assert plain.zone == "exile"


def test_a_resolution_pick_skips_a_ward_its_controller_cannot_pay(card_db):
    game = GameState(rng=random.Random(0))
    game.players[0].life = 3
    warded = _perm(game, 1, types=[CardType.ARTIFACT], name="Warded Rock", cmc=6,
                   ward_life=7)
    cmd = _spell(game, card_db, "Kolaghan's Command")
    EFFECT_REGISTRY.execute(cmd.template.name, EffectTiming.SPELL_RESOLVE, game, cmd, 0)
    assert warded.zone == "battlefield"


def test_an_etb_trigger_affects_the_target_chosen_when_it_was_put_on_the_stack(card_db):
    game = GameState(rng=random.Random(0))
    big = _perm(game, 1, types=[CardType.CREATURE], name="Big", cmc=6, power=6, toughness=6)
    small = _perm(game, 1, types=[CardType.CREATURE], name="Small", cmc=1, power=1, toughness=1)
    sol = _spell(game, card_db, "Solitude")
    sol.zone = "battlefield"
    game.players[0].battlefield.append(sol)
    EFFECT_REGISTRY.execute(sol.template.name, EffectTiming.ETB, game, sol, 0,
                            targets=[small.instance_id])
    assert small.zone == "exile" and big.zone == "battlefield"


def test_a_resolution_damage_pick_skips_hexproof_and_deals_damage_through_the_owner():
    from engine.oracle_resolver import resolve_any_target_damage
    game = GameState(rng=random.Random(0))
    pinger = _perm(game, 0, types=[CardType.CREATURE], name="Pinger", power=1, toughness=1)
    shielded = _perm(game, 1, types=[CardType.CREATURE], name="Shielded", power=3,
                     toughness=1, keywords=[Keyword.HEXPROOF])
    life = game.players[1].life
    resolve_any_target_damage(game, pinger, 0, 1)
    assert shielded.damage_marked == 0
    assert game.players[1].life == life - 1, "no legal creature: the damage goes face"
