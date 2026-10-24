"""A permanent that enters the battlefield is a new object (CR 400.7), and
a planeswalker enters with loyalty counters equal to its printed loyalty
(CR 306.5b) — whatever path put it there, not only casting it.

Rules pinned:
* a planeswalker put onto the battlefield from exile through the zone
  funnel has its printed loyalty, so it does not die to SBA 704.5i;
* a permanent exiled until the next end step returns without the counters
  it had (a damaged planeswalker returns at printed loyalty; a creature
  returns without +1/+1 counters);
* the delayed return runs through the zone funnel: the exiled card sits
  in its owner's exile while away.
Card names are fixture carriers only (one real member of the delayed-
return class resolved through its registered handlers).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState


def _walker(game, controller, loyalty=4):
    tmpl = CardTemplate(
        name="Walker", card_types=[CardType.PLANESWALKER], mana_cost=ManaCost(generic=3),
        supertypes=[], subtypes=[], power=None, toughness=None, loyalty=loyalty,
        keywords=set(), abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text="", tags=set())
    return CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="exile")


def test_a_planeswalker_put_onto_the_battlefield_enters_with_printed_loyalty():
    game = GameState(rng=random.Random(0))
    pw = _walker(game, 0, loyalty=4)
    game.players[0].exile.append(pw)
    game.zone_mgr.move_card(game, pw, "exile", "battlefield", cause="test")
    assert pw.loyalty_counters == 4
    game.check_state_based_actions()
    assert pw.zone == "battlefield", "SBA 704.5i must not see a 0-loyalty walker"


def _phelia_exiles(game, card_db, target, attacker_controller=0):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    t = card_db.get_card("Phelia, Exuberant Shepherd")
    phelia = CardInstance(template=t, owner=attacker_controller,
                          controller=attacker_controller,
                          instance_id=game.next_instance_id(), zone="battlefield")
    phelia._game_state = game
    game.players[attacker_controller].battlefield.append(phelia)
    EFFECT_REGISTRY.execute(t.name, EffectTiming.ATTACK, game, phelia, attacker_controller)
    return phelia


def test_a_planeswalker_exiled_until_end_step_returns_with_printed_loyalty(card_db):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    game = GameState(rng=random.Random(0))
    t = card_db.get_card("Teferi, Time Raveler")   # printed loyalty 4
    pw = CardInstance(template=t, owner=1, controller=1,
                      instance_id=game.next_instance_id(), zone="battlefield")
    pw._game_state = game
    game.players[1].battlefield.append(pw)
    pw.loyalty_counters = 7          # grown above printed, so the exile picks it
    phelia = _phelia_exiles(game, card_db, pw)
    assert pw.zone == "exile" and pw in game.players[1].exile
    EFFECT_REGISTRY.execute(phelia.template.name, EffectTiming.END_STEP, game, phelia, 0)
    assert pw.zone == "battlefield"
    assert pw.loyalty_counters == t.loyalty, "a returned walker is a new object at printed loyalty"
