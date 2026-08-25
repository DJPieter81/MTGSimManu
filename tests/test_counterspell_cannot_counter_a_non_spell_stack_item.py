"""A counterspell may only counter a SPELL, never an ability on the stack.

CR 111 / 701.5: "counter target spell" answers spells. A triggered or activated
ability on the stack is not a spell and cannot be countered by one (answering
abilities requires a card that says so — Stifle, Disallow).

Live bug this pins: `ai/response.py`'s counterspell gate read
`stack_item.source.template.is_spell`, and `CardTemplate.is_spell` is defined
as `not self.is_land`. That property describes the SOURCE CARD, not the stack
object, so any non-land source passed the gate — including a permanent that is
merely the SOURCE of a triggered ability. The engine's only `decide_response`
call site passes `game.stack.top`, which can already be a TRIGGERED_ABILITY, so
this was reachable in live games and not merely a future hazard.

The stack object's own `item_type` is the correct discriminator.

Rule under test: a counterspell is offered against a spell on the stack and
withheld against a non-spell stack item. Mechanic-driven (stack item type), no
card names asserted.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.stack import StackItem, StackItemType
from ai.ev_player import EVPlayer

_DB = CardDatabase()


def _add(game, name, controller, zone):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game_with_counter_in_hand():
    game = GameState(rng=random.Random(0))
    game.active_player = 1
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    me, opp = game.players[0], game.players[1]
    me.deck_name = "4/5c Control"
    opp.deck_name = "Domain Zoo"
    me.life = 20
    opp.life = 20
    # Basic Islands, NOT shocklands: a shockland enters tapped, which made an
    # earlier revision of this fixture unable to cast the counter at all — the
    # negative cases then passed vacuously. The control case below is what
    # catches that.
    for _ in range(5):
        _add(game, "Island", 0, "battlefield")
    counter = _add(game, "Counterspell", 0, "hand")
    # NOTE: castability is asserted per-test AFTER the stack item exists —
    # a counterspell targets "target spell", so CR 601.2c correctly refuses it
    # against an empty stack.
    assert "counterspell" in counter.template.tags, (
        "fixture premise: the held card is tagged as a counterspell")
    return game, counter


def _decide(game, item):
    ai = EVPlayer(player_idx=0, deck_name="4/5c Control", rng=random.Random(0))
    return ai.decide_response(game, item)


def test_counterspell_is_offered_against_a_spell_on_the_stack():
    """Control case — the gate must still work for real spells."""
    game, _counter = _game_with_counter_in_hand()
    threat = _add(game, "Territorial Kavu", 1, "hand")
    threat.zone = "stack"
    game.players[1].hand.remove(threat)
    item = StackItem(item_type=StackItemType.SPELL, source=threat,
                     controller=1)
    game.stack.items.append(item)
    assert game.can_cast(0, _counter), (
        "fixture premise: with a real spell on the stack the counter IS "
        "castable — otherwise the negative cases prove nothing")
    assert _decide(game, item) is not None, (
        "a counterspell must be offered against a creature spell on the "
        "stack — otherwise this test cannot detect the negative case")


def test_counterspell_is_not_offered_against_a_triggered_ability():
    game, _counter = _game_with_counter_in_hand()
    # A permanent on the battlefield is the SOURCE of a triggered ability.
    # Its template.is_spell is True (it is not a land), which is exactly the
    # trap: the SOURCE is spell-like, the STACK OBJECT is not a spell.
    source = _add(game, "Territorial Kavu", 1, "battlefield")
    assert source.template.is_spell, (
        "fixture premise: the source card's is_spell is True, so only the "
        "stack item's own type can distinguish this case")
    item = StackItem(item_type=StackItemType.TRIGGERED_ABILITY,
                     source=source, controller=1)
    game.stack.items.append(item)
    assert not game.can_cast(0, _counter), (
        "the ENGINE must also refuse: a triggered ability is not a legal "
        "target for 'target spell' (CR 601.2c + CR 111)")
    assert _decide(game, item) is None, (
        "a counterspell must NOT be offered against a triggered ability "
        "(CR 701.5 — it counters spells, not abilities)")


def test_counterspell_is_not_offered_against_an_activated_ability():
    game, _counter = _game_with_counter_in_hand()
    source = _add(game, "Territorial Kavu", 1, "battlefield")
    item = StackItem(item_type=StackItemType.ACTIVATED_ABILITY,
                     source=source, controller=1)
    game.stack.items.append(item)
    assert not game.can_cast(0, _counter), (
        "the ENGINE must also refuse an activated ability as a spell target")
    assert _decide(game, item) is None, (
        "a counterspell must NOT be offered against an activated ability")
