"""CR 704.5h — lethal marked damage destroys a creature UNLESS it has
indestructible. CR 704.5g — toughness 0 or less puts the creature into
the graveyard regardless (indestructible only prevents *destruction*,
and 704.5g isn't a destroy effect).

The discrete 704.5h check (with the indestructible exemption) existed
only in the dead SBAManager loop (engine/sba_manager.py). The live path
folded lethality into CardInstance.is_dead, which had NO indestructible
check — so indestructible creatures wrongly died to marked lethal
damage. Probe was RED; fixed at the single owning site (is_dead).

The 704.5i deathtouch check must respect the same exemption — a
deathtouch-marked indestructible creature also survives.

See docs/proposals/resolver_sba_unification.md §5.3.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Keyword
from engine.damage import deal_damage
from engine.game_state import GameState
from engine.mana import ManaCost


def _fresh_game() -> GameState:
    return GameState(rng=random.Random(0))


def _creature(game: GameState, name: str, controller: int, *,
              power: int = 2, toughness: int = 2,
              keywords=None) -> CardInstance:
    tmpl = CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(keywords or ()), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def test_indestructible_creature_survives_lethal_marked_damage():
    """704.5h: damage >= toughness does not destroy an indestructible
    creature."""
    game = _fresh_game()
    wall = _creature(game, "Adamant Wall", 0, power=2, toughness=2,
                     keywords={Keyword.INDESTRUCTIBLE})
    wall.damage_marked = 3  # lethal for a 2-toughness creature

    game.check_state_based_actions()

    assert wall.zone == "battlefield"
    assert wall in game.players[0].battlefield


def test_nonindestructible_creature_still_dies_to_lethal_marked_damage():
    """Negative control: without indestructible, the same marked damage
    kills (704.5h)."""
    game = _fresh_game()
    body = _creature(game, "Plain Body", 0, power=2, toughness=2)
    body.damage_marked = 3

    game.check_state_based_actions()

    assert body.zone == "graveyard"
    assert body in game.players[0].graveyard


def test_indestructible_creature_still_dies_to_zero_toughness():
    """704.5g: toughness <= 0 (from -X/-X effects) is not a destroy
    effect — indestructible does not save the creature."""
    game = _fresh_game()
    wall = _creature(game, "Adamant Wall", 0, power=2, toughness=2,
                     keywords={Keyword.INDESTRUCTIBLE})
    wall.temp_toughness_mod = -2  # toughness → 0

    assert wall.toughness <= 0
    game.check_state_based_actions()

    assert wall.zone == "graveyard"
    assert wall in game.players[0].graveyard


def test_indestructible_creature_survives_deathtouch_marked_damage():
    """704.5i is a destroy effect too — the deathtouch check must
    respect indestructible."""
    game = _fresh_game()
    source = _creature(game, "Venom Source", 1,
                       keywords={Keyword.DEATHTOUCH})
    wall = _creature(game, "Adamant Wall", 0, power=2, toughness=4,
                     keywords={Keyword.INDESTRUCTIBLE})

    deal_damage(source, wall, 1)
    game.check_state_based_actions()

    assert wall.zone == "battlefield"
    assert wall in game.players[0].battlefield
    # Marker cleared so the SBA pass reaches a fixpoint.
    assert wall._deathtouch_damage == 0
