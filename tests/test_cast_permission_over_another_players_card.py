"""A permission to cast another player's exiled card (CR 108.3, 400.3,
601.2a, 108.4).

"Exile the top card of that player's library. Until end of turn, you may
cast that card": the card stays in its OWNER's exile -- it is never put
into the caster's hand -- and the permitted player may cast it from there
with the normal timing and cost. Cast, it is the caster's spell: a
permanent enters under the caster's control, an instant or sorcery goes
to its owner's graveyard. "Cast" never covers a land.

The engine put Ragavan, Nimble Pilferer's exiled card into the attacker's
hand, flagged to go back to exile at end of turn, and changed its
controller: a land could be played from it, and it counted as a card in
hand. Ragavan is a fixture carrier; the rule is the permission's.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _permit_cast(game, card, player=0):
    from engine.effect_model import THIS_TURN, permit_play
    game.continuous_effects.register_effect(
        permit_play(player, [card.instance_id], "cast", THIS_TURN))


def test_a_permitted_player_casts_another_players_exiled_spell(card_db):
    game = _game()
    _put(game, card_db, "Mountain", "battlefield", 0)
    bolt = _put(game, card_db, "Lightning Bolt", "exile", 1)   # theirs
    assert not game.can_cast(0, bolt)
    _permit_cast(game, bolt)
    assert bolt in game.get_legal_plays(0)
    assert game.cast_spell(0, bolt, targets=[-2])
    assert bolt not in game.players[1].exile and bolt.zone == "stack"
    game.resolve_stack()
    assert bolt in game.players[1].graveyard            # its owner's (400.3)
    assert bolt not in game.players[0].graveyard


def test_a_permanent_cast_from_another_players_exile_is_the_casters(card_db):
    game = _game()
    for _ in range(2):
        _put(game, card_db, "Forest", "battlefield", 0)
    bears = _put(game, card_db, "Grizzly Bears", "exile", 1)
    _permit_cast(game, bears)
    assert game.cast_spell(0, bears)
    game.resolve_stack()
    assert bears in game.players[0].battlefield
    assert bears.controller == 0 and bears.owner == 1


def test_another_players_exiled_spell_pays_its_cost_less_the_same_reductions(
        card_db):
    """CR 601.2f: a reduction applies wherever the spell is cast from, and
    the payment charges the cost `can_cast` counted -- the spell is in its
    owner's exile, not the caster's."""
    game = _game()
    _put(game, card_db, "Ruby Medallion", "battlefield", 0)
    mountain = _put(game, card_db, "Mountain", "battlefield", 0)
    spell = _put(game, card_db, "Wrenn's Resolve", "exile", 1)   # {1}{R}
    _permit_cast(game, spell)
    assert game.can_cast(0, spell)
    assert game.cast_spell(0, spell)
    assert mountain.tapped and spell.zone == "stack"


def test_the_ai_plan_sees_a_card_it_may_cast_from_another_players_exile(
        card_db):
    """The turn plan's playable list reads the permission's one read path,
    whoever owns the exiled card."""
    from ai.playable_cards import playable_cards
    game = _game()
    bolt = _put(game, card_db, "Lightning Bolt", "exile", 1)
    _permit_cast(game, bolt)
    assert game.players[0].exile == []
    assert playable_cards(game, 0) == [bolt]


def test_a_cast_permission_never_plays_another_players_land(card_db):
    game = _game()
    land = _put(game, card_db, "Mountain", "exile", 1)
    _permit_cast(game, land)
    assert land not in game.get_legal_plays(0)
    game.play_land(0, land)
    assert land.zone == "exile" and land in game.players[1].exile


def test_combat_damage_exiles_their_top_card_with_a_cast_permission(card_db):
    """Ragavan's trigger: the defending player's top card goes to THEIR
    exile, castable by the attacker this turn; it is never put into the
    attacker's hand and its controller does not change."""
    from engine.combat_manager import CombatManager
    from engine import rules_query
    game = _game()
    ragavan = _put(game, card_db, "Ragavan, Nimble Pilferer", "battlefield", 0)
    top = _put(game, card_db, "Lightning Bolt", "library", 1)
    _put(game, card_db, "Island", "library", 1)
    cm = CombatManager()
    cm.declare_attackers(game, [ragavan], 0)
    cm.resolve_combat_damage(game)
    assert top in game.players[1].exile and top.zone == "exile"
    assert top not in game.players[0].hand
    assert top.controller == 1
    assert rules_query.permitted_cards(game, 0) == [top]
