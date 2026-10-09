"""A printed restriction on when players may cast binds every cast, a free
or alternative one included (CR 101.2, 307.1; a free cast is still a cast,
CR 601.2).

"Each opponent can cast spells only any time they could cast a sorcery"
stops a cast made outside its caster's main phase with an empty stack,
whatever the route: an imprinted copy fired in an opponent's upkeep, a
rebound recast at upkeep, a suspended card's last-counter cast, a madness
cast during a discard's resolution. Each route then does what its rules say
when the spell is not cast:
* a rebound card stays exiled (CR 702.88a);
* a suspended card stays exiled (CR 702.62a);
* a madness card goes to its owner's graveyard (CR 702.35a);
* a cascade hit goes to the bottom with the rest (CR 702.85a);
* a copy that cannot be cast is not paid for.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game(active=0, phase=Phase.UPKEEP):
    game = GameState(rng=random.Random(0))
    game.active_player = active
    game.current_phase = phase
    return game


def _put(game, card_db, name, controller, zone, tapped=False):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = tapped
        game.players[controller].battlefield.append(c)
    else:
        getattr(game.players[controller], zone).append(c)
    return c


def _restricted_by_opponent(game, card_db, restricted=0):
    """The opponent of `restricted` controls a printed sorcery-timing
    restriction."""
    return _put(game, card_db, "Teferi, Time Raveler", 1 - restricted,
                "battlefield")


def test_an_imprinted_copy_is_neither_cast_nor_paid_for_against_a_sorcery_timing_restriction(card_db):
    from engine.game_runner import GameRunner
    game = _game(active=1)                    # the opponent's upkeep
    _restricted_by_opponent(game, card_db)
    for _ in range(3):
        _put(game, card_db, "Plains", 0, "battlefield")
    scepter = _put(game, card_db, "Isochron Scepter", 0, "battlefield")
    chant = _put(game, card_db, "Orim's Chant", 0, "exile")
    chant.instance_tags.add("on_scepter")
    scepter.instance_tags.add("imprint:Orim's Chant")
    GameRunner(card_db)._process_imprint_copy_activations(
        game, 0, timing="opp_upkeep")
    assert not any("silences" in line for line in game.log), game.log[-3:]
    assert not scepter.tapped
    assert all(not land.tapped for land in game.players[0].lands)


def test_a_rebound_recast_against_a_sorcery_timing_restriction_stays_exiled(card_db):
    from engine.game_runner import GameRunner
    game = _game(active=0)                    # its controller's upkeep
    _restricted_by_opponent(game, card_db)
    _put(game, card_db, "Memnite", 0, "battlefield")
    rc = _put(game, card_db, "Ephemerate", 0, "exile")
    rc._rebound_controller = 0
    game._rebound_cards = [rc]
    GameRunner(card_db)._process_rebound_recasts(game, 0, ai=None)
    assert rc in game.players[0].exile and rc.zone == "exile"
    assert game.stack.is_empty


def test_a_suspended_card_that_cannot_be_cast_stays_exiled(card_db):
    game = _game(active=0)
    _restricted_by_opponent(game, card_db)
    _put(game, card_db, "Mountain", 0, "battlefield")
    bolt = _put(game, card_db, "Rift Bolt", 0, "hand")
    game.current_phase = Phase.MAIN1
    assert game.suspend_card(0, bolt)
    game.current_phase = Phase.UPKEEP
    life = game.players[1].life
    game.tick_suspend_upkeep(0)
    assert bolt in game.players[0].exile and bolt.zone == "exile"
    assert game.players[1].life == life


def test_a_madness_cast_against_a_sorcery_timing_restriction_goes_to_the_graveyard(card_db):
    game = _game(active=1, phase=Phase.MAIN1)  # discarded on the opponent's turn
    _restricted_by_opponent(game, card_db)
    rootwalla = _put(game, card_db, "Blazing Rootwalla", 0, "hand")
    game.discard_card(0, rootwalla, cause="forced discard")
    while not game.stack.is_empty:
        game.resolve_stack()
    assert rootwalla in game.players[0].graveyard
    assert rootwalla not in game.players[0].battlefield


def test_a_cascade_hit_that_cannot_be_cast_goes_to_the_bottom_with_the_rest(card_db):
    """A cascade hit its caster may not cast (here: a "can't cast spells
    this turn") is put on the bottom of the library, never into a hand."""
    from engine.stack import StackItem, StackItemType
    game = _game(active=0, phase=Phase.MAIN1)
    me = game.players[0]
    for _ in range(3):
        _put(game, card_db, "Mountain", 0, "library")
    hit = _put(game, card_db, "Lightning Bolt", 0, "library")
    me.library.remove(hit)
    me.library.insert(0, hit)                 # the top card
    elf = _put(game, card_db, "Bloodbraid Elf", 0, "hand")
    me.hand.remove(elf)
    me.silenced_this_turn = True
    game._handle_cascade(StackItem(item_type=StackItemType.SPELL, source=elf,
                                   controller=0))
    assert hit not in me.hand
    assert hit in me.library and hit.zone == "library"
