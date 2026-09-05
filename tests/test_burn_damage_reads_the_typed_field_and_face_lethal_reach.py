"""A burn spell's damage comes from its parsed oracle amount, and a burn
spell goes face when the burn castable this turn is lethal together.

# Mechanic the tests name

The decision layer read a burn spell's damage from a card-NAME table
(`decks/card_knowledge_loader.get_burn_damage`); any "deals N damage to
any target" spell outside the table read as 0, skipped the burn branch
entirely, and was aimed like creature removal.  Izzet Prowess vs Domain
Zoo s50000 game 3: Zoo sat at 3, 2, then 1 life from turn 3 to turn 9
while the one-damage burn spell was cast into a 4/4 and twice into a
5/5 — and Zoo won at 1 life.  The typed `direct_damage_data['amount']`
(parse_direct_damage_spell, 79 pool cards) is the amount; the table is
only a fallback for modal / variable shapes it does not cover.

Second rule: lethal is a property of the TURN, not of one spell.  A
burn spell whose own damage is below the opponent's life still goes
face when the burn spells castable this turn add up to lethal.

Class: every direct-damage spell in every deck.  Card names below are
fixture carriers only.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.game_state import GameState, Phase


def _put(game, card_db, name, controller, zone):
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
    elif zone == "library":
        game.players[controller].library.append(c)
    else:
        game.players[controller].hand.append(c)
    return c


def _game(card_db, lands=2, opp_life=20):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(lands):
        _put(game, card_db, "Mountain", 0, "battlefield")
    game.players[1].life = opp_life
    for _ in range(4):
        _put(game, card_db, "Mountain", 1, "library")
    return game


def _ai():
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=0, deck_name="Izzet Prowess",
                    rng=random.Random(0))


def test_burn_damage_is_the_parsed_oracle_amount_with_the_table_as_fallback(card_db):
    from ai.card_classes import burn_damage
    assert burn_damage(card_db.get_card("Lava Dart")) == 1
    assert burn_damage(card_db.get_card("Lightning Bolt")) == 3
    # A modal / variable shape the parser does not type still resolves
    # through the knowledge table.
    assert burn_damage(card_db.get_card("Galvanic Discharge")) == 3
    assert burn_damage(card_db.get_card("Counterspell")) == 0


def test_a_burn_spell_goes_face_when_it_is_lethal_alone(card_db):
    game = _game(card_db, lands=2, opp_life=1)
    dart = _put(game, card_db, "Lava Dart", 0, "hand")
    _put(game, card_db, "Quantum Riddler", 1, "battlefield")
    assert _ai()._choose_targets(game, dart) == [PLAYER_TARGET_OPPONENT]


def test_a_burn_spell_goes_face_when_this_turns_burn_is_lethal_together(card_db):
    game = _game(card_db, lands=2, opp_life=2)
    dart = _put(game, card_db, "Lava Dart", 0, "hand")
    _put(game, card_db, "Lava Dart", 0, "hand")
    _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")  # killable
    assert _ai()._choose_targets(game, dart) == [PLAYER_TARGET_OPPONENT]


def test_damage_based_removal_projects_only_what_its_damage_kills(card_db):
    """The cast-time projection removed the biggest threat off the board
    for any removal-tagged spell; a one-damage spell was credited a 5/5
    kill.  Damage removes only what it kills (toughness ≤ amount)."""
    from ai.ev_evaluator import _project_spell, snapshot_from_game
    game = _game(card_db, lands=2, opp_life=20)
    dart = _put(game, card_db, "Lava Dart", 0, "hand")
    _put(game, card_db, "Quantum Riddler", 1, "battlefield")          # 5/5
    snap = snapshot_from_game(game, 0)
    proj = _project_spell(dart, snap, game=game, player_idx=0)
    assert proj.opp_creature_count == snap.opp_creature_count
    assert proj.opp_power == snap.opp_power
    game2 = _game(card_db, lands=2, opp_life=20)
    dart2 = _put(game2, card_db, "Lava Dart", 0, "hand")
    _put(game2, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")   # 2/1
    snap2 = snapshot_from_game(game2, 0)
    proj2 = _project_spell(dart2, snap2, game=game2, player_idx=0)
    assert proj2.opp_creature_count == snap2.opp_creature_count - 1


def test_burn_out_of_lethal_reach_still_prefers_a_killable_creature(card_db):
    game = _game(card_db, lands=2, opp_life=20)
    dart = _put(game, card_db, "Lava Dart", 0, "hand")
    ragavan = _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")
    assert _ai()._choose_targets(game, dart) == [ragavan.instance_id]
