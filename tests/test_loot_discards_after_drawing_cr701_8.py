"""A "draw N, then discard M" effect discards after it draws (CR 701.8).

The generic spell resolver typed the loot shape as a plain draw: Faithless
Looting resolved as "draw two" and kept both cards; Burning Inquiry ("each
player draws three cards, then discards three cards at random") resolved
to nothing at all. The discard half is the whole point of the class for the
decks that run it — a card whose cost falls per card discarded this turn,
madness, graveyard recursion — and the draw-only reading is free card
advantage for everyone else. Observed 2026-09-09 (Domain Zoo vs Hollow One
s50000): Hollow One cycled a Hollow One for two mana on the turn Burning
Inquiry should have made it free, and never once discarded to a loot.

Rules pinned:

1. The shape is typed once at load: draw count, discard count, whether
   the discard is at random, and whether every player loots.
2. Resolution draws, then discards — the controller's choice through the
   discard callback, or at random from the game's RNG — and every discard
   goes through the discard funnel, so the per-turn discard counter
   advances and a madness card is offered its madness cast.
3. "Each player" loots every player, the controller first.

Class: 36 instants and sorceries, 4 "each player" shapes, 145 permanents
carrying the ability. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    elif zone == "library":
        game.players[controller].library.append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(8):
        _add(game, card_db_holder[0], "Forest", 0, "library")
        _add(game, card_db_holder[0], "Island", 1, "library")
    return game


card_db_holder = [None]


def _resolve_all(game):
    while not game.stack.is_empty:
        game.resolve_stack()
        game.check_state_based_actions()


# ─── 1. The shape is typed once ───────────────────────────────────────


def test_the_loot_shape_is_typed_with_its_counts_scope_and_randomness(card_db):
    from engine.oracle_parser import parse_loot_effect
    assert parse_loot_effect("Draw two cards, then discard two cards.") == {
        "draw": 2, "discard": 2, "random": False, "each_player": False}
    assert parse_loot_effect(
        "Each player draws three cards, then discards three cards at random."
    ) == {"draw": 3, "discard": 3, "random": True, "each_player": True}
    assert parse_loot_effect("Draw a card, then discard a card.") == {
        "draw": 1, "discard": 1, "random": False, "each_player": False}
    assert parse_loot_effect("Draw two cards.") is None
    assert card_db.get_card("Faithless Looting").loot_data == {
        "draw": 2, "discard": 2, "random": False, "each_player": False}


# ─── 2. Resolution draws, then discards through the funnel ───────────


def test_a_loot_spell_discards_after_it_draws_and_the_discard_counts(card_db):
    card_db_holder[0] = card_db
    game = _game()
    _add(game, card_db, "Mountain", 0, "battlefield")
    spell = _add(game, card_db, "Faithless Looting", 0, "hand")
    for n in ("Grizzly Bears", "Colossal Dreadmaw", "Memnite"):
        _add(game, card_db, n, 0, "hand")
    me = game.players[0]
    hand_before, gy_before = len(me.hand), len(me.graveyard)
    assert CastManager.cast_spell(game, 0, spell, [])
    _resolve_all(game)
    assert len(me.hand) == hand_before - 1, (
        "draw two then discard two leaves the hand one smaller (the spell)")
    assert len(me.graveyard) == gy_before + 3, "the spell plus two discards"
    assert me.cards_discarded_or_cycled_this_turn == 2, (
        "each discard must advance the per-turn counter (Hollow One's cost)")


def test_a_madness_card_discarded_to_a_loot_is_offered_its_madness_cast(card_db):
    card_db_holder[0] = card_db
    game = _game()
    _add(game, card_db, "Mountain", 0, "battlefield")
    spell = _add(game, card_db, "Faithless Looting", 0, "hand")
    rootwalla = _add(game, card_db, "Blazing Rootwalla", 0, "hand")   # madness {0}
    _add(game, card_db, "Memnite", 0, "hand")
    _add(game, card_db, "Memnite", 0, "hand")
    assert CastManager.cast_spell(game, 0, spell, [])
    _resolve_all(game)
    # The default chooser discards the highest-cost card first — the
    # one-mana madness creature ahead of the free ones — so it IS
    # discarded, and the funnel must offer its {0} madness cast.
    assert rootwalla in game.players[0].battlefield, (
        "a madness card discarded to a loot was binned instead of being "
        "offered its madness cast through the discard funnel")


# ─── 3. "Each player" loots every player ─────────────────────────────


def test_an_each_player_random_loot_makes_every_player_draw_and_discard(card_db):
    card_db_holder[0] = card_db
    game = _game()
    _add(game, card_db, "Mountain", 0, "battlefield")
    spell = _add(game, card_db, "Burning Inquiry", 0, "hand")
    for n in ("Grizzly Bears", "Memnite", "Watchwolf"):
        _add(game, card_db, n, 0, "hand")
    for n in ("Grizzly Bears", "Memnite"):
        _add(game, card_db, n, 1, "hand")
    h0 = [len(p.hand) for p in game.players]
    assert CastManager.cast_spell(game, 0, spell, [])
    _resolve_all(game)
    assert len(game.players[0].hand) == h0[0] - 1, "caster: +3 -3, minus the spell"
    assert len(game.players[1].hand) == h0[1], "opponent: +3 -3"
    assert [p.cards_discarded_or_cycled_this_turn for p in game.players] == [3, 3]
    assert len(game.players[1].graveyard) == 3
