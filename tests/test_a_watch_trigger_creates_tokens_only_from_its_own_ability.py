"""A watch trigger creates tokens only from the ability that holds its watch
clause (CR 603.1, 603.2).

"Whenever a[n] [nontoken] <type> you control enters, create <token>": the
trigger and its instruction are one ability, one paragraph of the card's
text, together with the modal bullets that paragraph introduces. A watch
clause inside quotation marks is an ability granted to another object
(CR 113.1a), and one inside parentheses is reminder text (CR 207.2): neither
is the card's own trigger.

The defect: the enter-watcher (`engine/triggers.py`) matched the watch
clause anywhere on the card and the "create … token" anywhere else. 32 of
the 67 pool cards it matches are misread that way.
- Scurry Oak's evolve reminder text ("whenever a creature you control
  enters …") made a Squirrel from its other ability, and each Squirrel's
  entry another.
- Nurturing Presence grants "whenever a creature you control enters …" in
  quotes and separately creates a Spirit. Each token's entry created another
  token, without end.

Card names are fixture carriers: each is one member of its class.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def _tokens(game, controller=0):
    return [c for c in game.players[controller].battlefield
            if getattr(c, "is_token", False)]


def _enter(game, card_db, name, controller=0):
    """The card enters the way it does in play: a land is played, a spell
    is cast and resolves."""
    card = _add(game, card_db, name, controller, "hand")
    if card.template.is_land:
        game.play_land(controller, card)
    else:
        for _ in range(card.template.cmc or 0):
            _add(game, card_db, "Forest", controller)
            _add(game, card_db, "Plains", controller)
        assert game.cast_spell(controller, card, [])
        game.resolve_stack()
    return card


def test_a_watch_clause_in_a_granted_ability_creates_nothing(card_db):
    """Nurturing Presence's "whenever a creature you control enters" is in
    quotes: a creature entering creates no token (and does not loop)."""
    game = _game()
    _add(game, card_db, "Nurturing Presence", 0)
    _enter(game, card_db, "Grizzly Bears")
    assert _tokens(game) == []


def test_a_watch_clause_beside_a_separate_token_ability_creates_nothing(
        card_db):
    """Scurry Oak: "whenever a creature you control enters" is evolve's
    reminder text; its Squirrel belongs to its counters-placed ability."""
    game = _game()
    _add(game, card_db, "Scurry Oak", 0)
    _enter(game, card_db, "Grizzly Bears")
    assert _tokens(game) == []


def test_a_watch_clause_and_its_own_token_instruction_still_create(card_db):
    """Weapons Manufacturing: "whenever a nontoken artifact you control
    enters, create … Munitions" is one ability."""
    game = _game()
    _add(game, card_db, "Weapons Manufacturing", 0)
    _enter(game, card_db, "Ornithopter")
    assert len(_tokens(game)) == 1
