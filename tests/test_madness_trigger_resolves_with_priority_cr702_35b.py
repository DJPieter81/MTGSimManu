"""Madness is a triggered ability: the cast waits for its trigger to resolve.

CR 702.35a: "Madness [cost]" means "If you would discard this card, discard
it, but exile it instead of putting it into your graveyard" and "When this
card is exiled this way, its owner may cast it by paying [cost] rather than
paying its mana cost. If that player doesn't, they put this card into their
graveyard."

So the cast is not part of the discard. The discard exiles the card; the
"when ... exiled this way" ability triggers, and is put on the stack the
next time a player would receive priority (CR 603.3) -- after the ability or
spell that made the player discard has finished resolving (CR 608.2), above
a spell or ability whose cost was the discard (CR 601.2i, 602.2), and, in
the cleanup step, with players receiving priority and another cleanup step
following (CR 514.3a). The card is cast while that trigger resolves (CR
608.2g). Nothing else lets a spell be cast in the middle of a resolution.

The defect (rules audit 701.9a/discarded, Hollow One vs Grixis Reanimator
s58000 T4): an opponent's trigger -- "sacrifices ..., discards a card, and
loses 3 life" -- made a player discard a madness card, and the discard
funnel cast it on the spot, in the middle of that trigger's resolution.

Class: every card with a madness cost (47 in the pool,
`CardTemplate.madness_cost`). Card names are fixture carriers.
"""
from __future__ import annotations

import functools
import random

import pytest

from engine.callbacks import DefaultCallbacks
from engine.cards import CardInstance, CardTemplate, CardType, Color
from engine.constants import MAX_HAND_SIZE
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.stack import StackItem, StackItemType

_RULE = "702.35b/madness_cast_on_trigger_resolution"

_REMINDER = (" (If you discard this card, discard it into exile. When you do, "
             "cast it for its madness cost or put it into your graveyard.)")


# ── Fixtures ──────────────────────────────────────────────────────────

def _game(active: int = 0, callbacks=None) -> GameState:
    game = GameState(rng=random.Random(0), callbacks=callbacks)
    game.active_player = active
    game.current_phase = Phase.MAIN1
    game.turn_number = 3
    return game


def _template(name: str, *, madness: ManaCost = None, cycling=None,
              mana_cost: ManaCost = None) -> CardTemplate:
    oracle = []
    if cycling is not None:
        oracle.append("Cycling {0}")
    if madness is not None:
        oracle.append("Madness {0}" + _REMINDER)
    kwargs = {}
    if madness is not None:
        kwargs["madness_cost"] = madness
    if cycling is not None:
        kwargs["cycling_cost_data"] = cycling
    return CardTemplate(
        name=name, card_types=[CardType.CREATURE],
        mana_cost=mana_cost or ManaCost(generic=2, red=1),
        supertypes=[], subtypes=["Lizard"], power=1, toughness=1,
        loyalty=None, keywords=set(), abilities=[],
        color_identity={Color.RED}, produces_mana=[], enters_tapped=False,
        oracle_text="\n".join(oracle), tags=set(), **kwargs)


def _madness_card(game, owner=0, **kw) -> CardInstance:
    return _put(game, _template("Synthetic Madness Creature",
                                madness=ManaCost(), **kw), owner, "hand")


def _filler(game, owner=0, zone="library") -> CardInstance:
    return _put(game, _template("Synthetic Filler",
                                mana_cost=ManaCost(generic=1)), owner, zone)


def _put(game, template, owner, zone) -> CardInstance:
    card = CardInstance(template=template, owner=owner, controller=owner,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[owner], zone).append(card)
    return card


def _real(game, card_db, name, owner, zone) -> CardInstance:
    return _put(game, card_db.get_card(name), owner, zone)


def _drive(game) -> None:
    while not game.stack.is_empty:
        game.resolve_stack()


def _is_madness_trigger(item, card) -> bool:
    return (item.item_type is StackItemType.TRIGGERED_ABILITY
            and item.source is card and item.description == "madness")


class _Decline(DefaultCallbacks):
    def decide_offered_cast(self, game, player_idx, card) -> bool:
        return False


@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f for f in audit.drain() if f["rule"] == _RULE]


# ── The discard exiles; the trigger casts ─────────────────────────────

def test_a_madness_discard_leaves_the_card_in_exile_under_its_trigger():
    game = _game()
    card = _madness_card(game)
    game.discard_card(0, card, cause="discard")
    assert card.zone == "exile" and card in game.players[0].exile
    assert len(game.stack.items) == 1
    assert _is_madness_trigger(game.stack.top, card)
    assert game.stack.top.controller == card.owner
    assert not getattr(card, "_madness_pending", False), (
        "the card is castable from exile only while its trigger resolves")


def test_the_madness_cast_is_made_while_its_trigger_resolves(monkeypatch):
    game = _game()
    card = _madness_card(game)
    seen = []
    real_cast = GameState.cast_spell

    def spy(self, player_idx, c, *a, **kw):
        seen.append(getattr(self, "_resolving_item", None))
        return real_cast(self, player_idx, c, *a, **kw)

    monkeypatch.setattr(GameState, "cast_spell", spy)
    game.discard_card(0, card, cause="discard")
    assert seen == []                       # nothing cast by the discard
    game.resolve_stack()                    # the trigger resolves
    assert len(seen) == 1 and _is_madness_trigger(seen[0], card)
    assert card.zone == "stack"             # the spell waits for priority
    _drive(game)
    assert card in game.players[0].battlefield


def test_a_declined_madness_trigger_puts_the_card_into_the_graveyard():
    game = _game(callbacks=_Decline())
    card = _madness_card(game)
    game.discard_card(0, card, cause="discard")
    assert card.zone == "exile"
    _drive(game)
    assert card in game.players[0].graveyard and card.zone == "graveyard"
    assert card not in game.players[0].exile
    assert not getattr(card, "_madness_pending", False)


def test_an_unpayable_madness_trigger_puts_the_card_into_the_graveyard():
    game = _game()
    card = _put(game, _template("Synthetic Madness Creature",
                                madness=ManaCost(red=1)), 0, "hand")
    game.discard_card(0, card, cause="discard")
    assert card.zone == "exile"
    _drive(game)
    assert card in game.players[0].graveyard


def test_a_card_that_left_exile_before_its_madness_trigger_resolves_stays_put():
    """CR 400.7: the card that left exile is a new object the trigger no
    longer names -- it is neither cast nor put into the graveyard."""
    game = _game()
    card = _madness_card(game)
    game.discard_card(0, card, cause="discard")
    game.zone_mgr.move_card(game, card, "exile", "hand", cause="test")
    _drive(game)
    assert card.zone == "hand" and card in game.players[0].hand
    # Known gap shared with `effect_resolver.Handle`: a card that leaves
    # exile and returns to it is not yet told apart (no generic zone-entry
    # ordinal on CardInstance), so that case is not pinned here.


# ── A discard during another resolution ───────────────────────────────

def test_a_madness_card_discarded_by_a_resolving_ability_waits_for_it_to_finish():
    """The resolving ability makes its controller's opponent discard, then
    goes on (a life loss); the madness card is not cast before the ability
    has finished (CR 608.2), only once its own trigger resolves."""
    game = _game(active=1)
    card = _madness_card(game, owner=0)
    host = _filler(game, owner=1, zone="battlefield")
    during = {}

    def effect(g, source, controller, targets):
        g.discard_card(0, card, cause="forced discard")
        during["zone"] = card.zone
        during["spells"] = [i for i in g.stack.items
                            if i.item_type is StackItemType.SPELL]
        g.players[0].life -= 3   # the instruction after the discard

    game.stack.push(StackItem(item_type=StackItemType.TRIGGERED_ABILITY,
                              source=host, controller=1, effect=effect,
                              description="discard trigger"))
    game.resolve_stack()
    assert during == {"zone": "exile", "spells": []}
    assert _is_madness_trigger(game.stack.top, card)
    _drive(game)
    assert card in game.players[0].battlefield


def test_a_madness_card_discarded_to_a_loot_spell_is_cast_after_the_spell(card_db):
    game = _game()
    _real(game, card_db, "Mountain", 0, "battlefield")
    looting = _real(game, card_db, "Faithless Looting", 0, "hand")
    rootwalla = _real(game, card_db, "Blazing Rootwalla", 0, "hand")
    _real(game, card_db, "Memnite", 0, "hand")
    for _ in range(3):
        _real(game, card_db, "Memnite", 0, "library")
    assert game.cast_spell(0, looting, [])
    game.resolve_stack()                     # the loot resolves
    assert looting.zone == "graveyard"
    assert rootwalla.zone == "exile"
    assert any(_is_madness_trigger(i, rootwalla) for i in game.stack.items)
    _drive(game)
    assert rootwalla in game.players[0].battlefield


# ── A discard as a cost ───────────────────────────────────────────────

def test_a_madness_card_cycled_triggers_above_the_cycling_ability():
    """Cycling is an activated ability whose cost discards the card (CR
    702.29a); the madness trigger goes on the stack above it and resolves
    first, so the madness spell resolves before the cycling draw."""
    game = _game()
    me = game.players[0]
    drawn = _filler(game)
    card = _madness_card(game, cycling={"mana": 0, "life": 0,
                                        "colors": set()})
    assert game.activate_cycling(0, card)
    kinds = [i.item_type for i in game.stack.items]
    assert kinds == [StackItemType.ACTIVATED_ABILITY,
                     StackItemType.TRIGGERED_ABILITY]
    assert _is_madness_trigger(game.stack.top, card)
    assert drawn in me.library                 # nothing drawn yet
    game.resolve_stack()                       # madness trigger: cast
    assert card.zone == "stack" and game.stack.top.source is card
    game.resolve_stack()                       # the madness spell
    assert card in me.battlefield and drawn in me.library
    game.resolve_stack()                       # the cycling ability
    assert drawn in me.hand and game.stack.is_empty


def test_a_madness_card_discarded_for_a_spells_additional_cost_triggers_above_it(
        card_db):
    """A discard paid as a spell's additional cost (CR 601.2h): the madness
    trigger waits until the spell is cast and goes on the stack above it
    (CR 601.2i, 603.3), so it resolves before the spell."""
    game = _game()
    for _ in range(2):
        _real(game, card_db, "Mountain", 0, "battlefield")
    reunion = _real(game, card_db, "Cathartic Reunion", 0, "hand")
    rootwalla = _real(game, card_db, "Blazing Rootwalla", 0, "hand")
    _real(game, card_db, "Memnite", 0, "hand")
    for _ in range(3):
        _real(game, card_db, "Memnite", 0, "library")
    assert game.cast_spell(0, reunion, [])
    assert rootwalla.zone == "exile"
    assert game.stack.items[0].source is reunion
    assert _is_madness_trigger(game.stack.top, rootwalla)
    game.resolve_stack()                       # the trigger: cast
    assert game.stack.top.source is rootwalla
    game.resolve_stack()                       # the madness creature
    assert rootwalla in game.players[0].battlefield
    assert reunion.zone == "stack"             # Reunion has not resolved
    _drive(game)
    assert reunion.zone == "graveyard"


def test_a_cycled_madness_card_in_the_main_phase_resolves_before_the_next_decision(
        card_db):
    from engine.game_runner import AICallbacks, GameRunner
    from tests.test_a_spell_cast_by_any_route_is_offered_before_it_resolves \
        import _Seat
    game = _game(callbacks=AICallbacks())
    game.priority_player = 0
    _filler(game)
    card = _madness_card(game, cycling={"mana": 0, "life": 0,
                                        "colors": set()})
    seats = [_Seat(0, [("cycle", card, None)]), _Seat(1)]
    game.callbacks.bind_players(seats)
    GameRunner(card_db)._execute_main_phase(game, seats[0], seats[1])
    assert seats[0].stack_at_decision == [[], []]
    assert seats[1].offers == [("Synthetic Madness Creature", "stack")]
    assert card in game.players[0].battlefield


# ── The cleanup step (CR 514.3a) ──────────────────────────────────────

def test_a_cleanup_discard_puts_the_madness_trigger_on_the_stack_and_cleanup_repeats(
        card_db):
    from engine.game_runner import AICallbacks, GameRunner
    from tests.test_a_spell_cast_by_any_route_is_offered_before_it_resolves \
        import _Seat
    game = _game(callbacks=AICallbacks())
    game.current_phase = Phase.CLEANUP
    seats = [_Seat(0), _Seat(1)]
    game.callbacks.bind_players(seats)
    for _ in range(MAX_HAND_SIZE):
        _filler(game, zone="hand")
    card = _put(game, _template("Synthetic Madness Creature",
                                madness=ManaCost(),
                                mana_cost=ManaCost(generic=6, red=1)),
                0, "hand")
    assert game.cleanup_step() is False        # CR 514.3a: priority
    assert card.zone == "exile"
    assert [_is_madness_trigger(i, card) for i in game.stack.items] == [True]
    GameRunner(card_db)._cleanup_step(game)    # trigger, spell, cleanup
    assert card in game.players[0].battlefield
    assert seats[1].offers == [("Synthetic Madness Creature", "stack")]
    assert game.stack.is_empty
    assert len(game.players[0].hand) == MAX_HAND_SIZE


# ── Rules audit ───────────────────────────────────────────────────────

def _inline_madness_discard(game, player_idx, card, cause="discard"):
    """The defect: the madness cast made inside the discard itself."""
    game.zone_mgr.move_card(game, card, "hand", "exile", cause=cause)
    card._madness_pending = True
    try:
        cast = bool(game.can_cast(player_idx, card)
                    and game.cast_spell(player_idx, card))
    finally:
        card._madness_pending = False
    if cast:
        return "stack"
    game.zone_mgr.move_card(game, card, "exile", "graveyard", cause=cause)
    return "graveyard"


def test_the_auditor_flags_a_madness_cast_made_inside_the_discard(
        monkeypatch, audit):
    from engine.discard_manager import DiscardManager
    monkeypatch.setattr(DiscardManager, "discard_card",
                        staticmethod(_inline_madness_discard))
    game = _game()
    card = _madness_card(game)
    game.discard_card(0, card, cause="discard")
    _drive(game)
    assert card in game.players[0].battlefield
    assert len(_violations(audit)) == 1


def test_the_auditor_is_silent_when_the_trigger_makes_the_madness_cast(audit):
    game = _game()
    card = _madness_card(game)
    game.discard_card(0, card, cause="discard")
    _drive(game)
    assert card in game.players[0].battlefield
    assert _violations(audit) == []
