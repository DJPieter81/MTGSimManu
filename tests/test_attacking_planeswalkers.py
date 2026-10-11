"""Creatures can attack planeswalkers (CR 506.1 / 508.1b / 506.4).

The attacking player chooses, for each attacking creature, whether it attacks
the defending player or a planeswalker that player controls. Unblocked
damage (and trample excess, CR 702.19c) goes to the chosen defender; damage
to a planeswalker removes loyalty (CR 120.3c, through `engine/damage.py`).
If the attacked planeswalker has left the battlefield, an unblocked creature
attacking it deals no combat damage (CR 506.4).

Before this every attacker hit the player: no planeswalker in any deck could
be attacked. Replay: Azorius Control's Solitudes never touched Eldrazi Tron's
Ugin (+3 life a turn) and the locked game ran to the turn cap.

Class: every planeswalker. The choice is AI (`ai/attack_targets.py`); the
rule is engine (`engine/combat_manager.py`). Card names are fixture carriers.
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
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="battlefield")
    card._game_state = game
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _planeswalker(game, card_db, controller, loyalty=4,
                  name="Teferi, Time Raveler"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller, instance_id=game.next_instance_id(),
                        zone="battlefield")
    card._game_state = game
    card.enter_battlefield()
    card.loyalty_counters = loyalty
    game.players[controller].battlefield.append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    return game


def test_an_unblocked_attacker_assigned_to_a_planeswalker_removes_loyalty_not_life(card_db):
    game = _game()
    atk = _creature(game, "Attacker", 0, power=3, toughness=3)
    pw = _planeswalker(game, card_db, 1, loyalty=5)
    life = game.players[1].life
    cm = CombatManager()
    cm.declare_attackers(game, [atk], 0, attack_targets={atk.instance_id: pw})
    cm.declare_blockers(game, {})
    cm.resolve_combat_damage(game)
    assert pw.loyalty_counters == 2
    assert game.players[1].life == life


def test_trample_excess_goes_to_the_attacked_planeswalker(card_db):
    game = _game()
    atk = _creature(game, "Trampler", 0, power=5, toughness=5,
                    keywords={Keyword.TRAMPLE})
    blocker = _creature(game, "Blocker", 1, power=1, toughness=2)
    pw = _planeswalker(game, card_db, 1, loyalty=5)
    life = game.players[1].life
    cm = CombatManager()
    cm.declare_attackers(game, [atk], 0, attack_targets={atk.instance_id: pw})
    cm.declare_blockers(game, {atk.instance_id: [blocker.instance_id]})
    cm.resolve_combat_damage(game)
    assert pw.loyalty_counters == 2       # 5 power − 2 lethal to the blocker
    assert game.players[1].life == life


def test_an_attacker_whose_planeswalker_left_deals_no_combat_damage(card_db):
    game = _game()
    atk = _creature(game, "Attacker", 0, power=3, toughness=3)
    pw = _planeswalker(game, card_db, 1, loyalty=5)
    life = game.players[1].life
    cm = CombatManager()
    cm.declare_attackers(game, [atk], 0, attack_targets={atk.instance_id: pw})
    game.players[1].battlefield.remove(pw)
    pw.zone = "graveyard"
    cm.declare_blockers(game, {})
    cm.resolve_combat_damage(game)
    assert game.players[1].life == life


def test_attack_targets_default_to_the_player(card_db):
    game = _game()
    atk = _creature(game, "Attacker", 0, power=3, toughness=3)
    pw = _planeswalker(game, card_db, 1, loyalty=5)
    life = game.players[1].life
    cm = CombatManager()
    cm.declare_attackers(game, [atk], 0)
    cm.declare_blockers(game, {})
    cm.resolve_combat_damage(game)
    assert game.players[1].life == life - 3
    assert pw.loyalty_counters == 5


# ── AI choice (ai/attack_targets.py) ──────────────────────────────────

def test_an_on_board_planeswalker_has_positive_worth(card_db):
    # The battlefield snapshot carries creatures, artifacts and enchantments
    # but not loyalty; permanent_threat credits the remaining loyalty pool,
    # so removing a planeswalker is a real loss for its owner (it read 0.0).
    from ai.permanent_threat import permanent_threat
    game = _game()
    pw = _planeswalker(game, card_db, 1, loyalty=4, name="Ugin, Eye of the Storms")
    assert permanent_threat(pw, game.players[1], game) > 0.0


def test_ai_attacks_a_planeswalker_exactly_when_killing_it_beats_the_face_damage(card_db):
    from ai.attack_targets import choose_attack_targets
    from ai.damage_targets import face_damage_value
    from ai.permanent_threat import permanent_threat
    for life in (20, 60):
        game = _game()
        small = _creature(game, "Small", 0, power=2, toughness=2)
        big = _creature(game, "Big", 0, power=4, toughness=4)
        pw = _planeswalker(game, card_db, 1, loyalty=2,
                           name="Ugin, Eye of the Storms")
        game.players[1].life = life
        worth = permanent_threat(pw, game.players[1], game)
        face = face_damage_value(game, 1, small.power)
        targets = choose_attack_targets(game, 0, [small, big])
        if worth > face:
            # The smallest group whose power reaches the loyalty is sent.
            assert targets == {small.instance_id: pw}
        else:
            assert targets == {}


def test_ai_goes_face_when_the_attack_is_lethal(card_db):
    from ai.attack_targets import choose_attack_targets
    game = _game()
    a = _creature(game, "A", 0, power=3, toughness=3)
    b = _creature(game, "B", 0, power=3, toughness=3)
    _planeswalker(game, card_db, 1, loyalty=2, name="Ugin, Eye of the Storms")
    game.players[1].life = 5
    assert choose_attack_targets(game, 0, [a, b]) == {}


def test_ai_with_no_opposing_planeswalker_assigns_nothing():
    from ai.attack_targets import choose_attack_targets
    game = _game()
    a = _creature(game, "A", 0, power=3, toughness=3)
    assert choose_attack_targets(game, 0, [a]) == {}
