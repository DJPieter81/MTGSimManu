"""Cost reductions are continuous effects (CR 601.2f / 611): a permanent's
"<spells> cost {N} less" is a static COST_DELTA effect of that permanent,
and a resolved "until your next turn, <spells> cost {N} less" is a stored
one — both read through the one path (`rules_query.cost_delta`) and one
matcher.

Rules pinned:
* the reducer rule is typed once at load (`CardTemplate.cost_reduction_rule`)
  — the cost path no longer re-reads oracle text per call;
* a static reduction lasts exactly while its source is on the battlefield;
* a resolved reduction lasts until its controller's next turn;
* reductions apply only to the spell class their rule names.
Card names are fixture carriers only.
"""
from __future__ import annotations

import random

from engine import rules_query
from engine.cards import CardInstance
from engine.effect_model import DurationKind, ModKind, cost_delta_effect, until_your_next_turn
from engine.game_state import GameState


def _put(game, card_db, name, controller, zone):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
    getattr(game.players[controller], zone).append(c)
    return c


def test_the_reducer_rule_is_typed_at_load(card_db):
    rules = card_db.get_card("Ruby Medallion").cost_reduction_rules
    assert rules == ({'amount': 1, 'qualities': (('red',),), 'who': 'you'},)


def test_a_static_reduction_is_an_effect_of_its_source(card_db):
    game = GameState(rng=random.Random(0))
    bolt = card_db.get_card("Lightning Bolt")
    medallion = _put(game, card_db, "Ruby Medallion", 0, "battlefield")
    assert rules_query.cost_delta(game, 0, bolt) == 1
    assert any(e.modification.kind is ModKind.COST_DELTA
               and e.duration.kind is DurationKind.WHILE_SOURCE_ON_BATTLEFIELD
               for e in game.continuous_effects.rule_effects(game))
    game.players[0].battlefield.remove(medallion)
    assert rules_query.cost_delta(game, 0, bolt) == 0


def test_the_cost_path_does_not_reread_oracle_text(card_db):
    game = GameState(rng=random.Random(0))
    bolt = card_db.get_card("Lightning Bolt")
    medallion = _put(game, card_db, "Ruby Medallion", 0, "battlefield")
    import copy
    medallion.template = copy.copy(medallion.template)
    medallion.template.oracle_text = ""        # typed field is the source of truth
    assert rules_query.cost_delta(game, 0, bolt) == 1


def test_a_resolved_reduction_lasts_until_its_controllers_next_turn(card_db):
    game = GameState(rng=random.Random(0))
    bolt = card_db.get_card("Lightning Bolt")
    game.continuous_effects.register_effect(cost_delta_effect(
        0, {'amount': 1, 'qualities': (('instant',), ('sorcery',)),
            'who': 'you'},
        until_your_next_turn(0)))
    game.cleanup_step(); game.active_player = 1; game.untap_step(1)
    assert rules_query.cost_delta(game, 0, bolt) == 1
    game.cleanup_step(); game.active_player = 0; game.untap_step(0)
    assert rules_query.cost_delta(game, 0, bolt) == 0


def test_a_reduction_applies_only_to_its_spell_class(card_db):
    game = GameState(rng=random.Random(0))
    game.continuous_effects.register_effect(cost_delta_effect(
        0, {'amount': 1, 'qualities': (('instant',), ('sorcery',)),
            'who': 'you'},
        until_your_next_turn(0)))
    assert rules_query.cost_delta(game, 0, card_db.get_card("Lightning Bolt")) == 1
    assert rules_query.cost_delta(game, 0, card_db.get_card("Grizzly Bears")) == 0
