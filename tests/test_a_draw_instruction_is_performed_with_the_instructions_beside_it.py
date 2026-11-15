"""A draw instruction is performed by the one draw owner, with the
instructions printed beside it, in printed order (CR 121.1, 608.2c).

A spell's or ability's "draw N cards" draws through `GameState.draw_cards`,
which counts the draw and fans out its triggers ("whenever you draw a
card", CR 603.2). An instruction printed before it -- "mill two cards,
then draw a card" -- is performed first.

The defect: plain draws stayed with legacy carriers that read the draw
alone.
- Thought Scour's legacy draw clause dropped "target player mills two
  cards" (Grixis Reanimator, three main deck copies).
- An enter trigger's "mill two cards, then draw two cards" milled and
  never drew.
- A "{T}: Draw a card" activation drew outside the draw owner, so the
  source's own "whenever you draw a card" never fired.

Card names are fixture carriers: unit DW switches every card-flow host
whose draws are plain (1281 new-path pairs, each proven or recorded with
its reason by tools/host_resolution_equivalence.py).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT, PLAYER_TARGET_SELF
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(10):
        _add(game, card_db, "Island", 1, "library")
        _add(game, card_db, "Swamp", 0, "library")
    return game


def _resolve(game):
    while not game.stack.is_empty:
        game.resolve_stack()


def _zones(player):
    return len(player.library), len(player.graveyard), len(player.hand)


def test_a_spell_mills_the_chosen_player_then_draws_for_its_caster(card_db):
    game = _game(card_db)
    scour = _add(game, card_db, "Thought Scour", 0, "hand")
    assert game.cast_spell(0, scour, [PLAYER_TARGET_SELF], free_cast=True)
    _resolve(game)
    assert _zones(game.players[0]) == (7, 3, 1), \
        "two milled, one drawn; the spell itself in the graveyard"
    game = _game(card_db)
    scour = _add(game, card_db, "Thought Scour", 0, "hand")
    assert game.cast_spell(0, scour, [PLAYER_TARGET_OPPONENT],
                           free_cast=True)
    _resolve(game)
    assert _zones(game.players[1]) == (8, 2, 0)
    assert _zones(game.players[0]) == (9, 1, 1)


def test_an_enter_trigger_mills_then_draws(card_db):
    """'When ~ enters, mill two cards, then draw two cards.'"""
    game = _game(card_db)
    well = _add(game, card_db, "The Everflowing Well // The Myriad Pools",
                0, "hand")
    assert game.cast_spell(0, well, [], free_cast=True)
    _resolve(game)
    assert well.zone == "battlefield"
    assert _zones(game.players[0]) == (6, 2, 2)


def test_a_draw_activation_draws_through_the_draw_owner(card_db):
    """'{T}: Draw a card' on a source that says 'Whenever you draw a card,
    ~ deals 1 damage to any target': the draw's own trigger fires."""
    from engine.activated_effects import resolve_activated_ability
    game = _game(card_db)
    niv = _add(game, card_db, "Niv-Mizzet, the Firemind", 0, "battlefield")
    ability = next(a for a in niv.template.activated_abilities
                   if "draw" in a.effect_text.lower())
    life = game.players[1].life
    resolve_activated_ability(game, niv, 0, [], ability=ability)
    _resolve(game)
    assert len(game.players[0].hand) == 1
    assert game.players[1].life == life - 1


def test_a_draw_activation_the_legacy_path_left_unperformed_draws(card_db):
    """'Pay 7 life: Draw seven cards.' The legacy activation path left this
    unclassified activation unperformed."""
    from engine.activated_effects import resolve_activated_ability
    game = _game(card_db)
    gris = _add(game, card_db, "Griselbrand", 0, "battlefield")
    ability = next(a for a in gris.template.activated_abilities
                   if "draw" in a.effect_text.lower())
    assert resolve_activated_ability(game, gris, 0, [], ability=ability)
    assert len(game.players[0].hand) == 7


def test_a_draw_beside_a_keyword_that_targets_still_draws(card_db):
    """'Draw two cards. Awaken 4' cast with the awaken keyword's target:
    the target list is the keyword host's, not the draw's, so no carrier
    may place it on the draw (`effect_carrier.places_legacy_targets`); the
    draw resolves all the same."""
    game = _game(card_db)
    land = _add(game, card_db, "Island", 0, "battlefield")
    spell = _add(game, card_db, "Coastal Discovery", 0, "hand")
    assert game.cast_spell(0, spell, [land.instance_id], free_cast=True)
    _resolve(game)
    assert len(game.players[0].hand) == 2
