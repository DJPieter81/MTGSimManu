"""A spell that removes a card from a graveyard is aimed at the card whose
removal is worth most to its owner (AI, from CR 608.2c and 701.19).

A spell whose resolution removes its target from the graveyard it was
chosen in -- "exile target card from a graveyard", or "choose target card
in a graveyard ... search its owner's graveyard, hand, and library for any
number of cards with the same name as that card and exile them" -- takes
from the card's owner that card and, for a same-name exile, every card of
its name in the zones it searches. What it takes is worth something only
when the owner would have used it: a keystone their gameplan declares, or
a card still a resource to them in their graveyard (`graveyard_fuel`).

The defect: no pick knew the shape, so the engine's printed-data default
aimed it at the first card in the graveyard. 4/5c Control vs Living End
s50000 game 3: Surgical Extraction exiled four Misty Rainforests while
Living End itself sat in that graveyard. And because the projection sees
nothing in a graveyard, hand or library exile, the cast scored as a loss
and was made only on a turn facing lethal, at that default.

Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
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


def _game(card_db, graveyard, *, opp_deck="Living End",
          library=("Living End", "Shardless Agent", "Misty Rainforest"),
          spell="Surgical Extraction"):
    """Player 0 (active, main phase) holds `spell` with lands to cast it;
    player 1 plays `opp_deck` with `graveyard` (in order) and `library`."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.players[0].deck_name = "4/5c Control"
    game.players[1].deck_name = opp_deck
    for _ in range(4):
        _add(game, card_db, "Swamp", 0, "battlefield")
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Forest", 1, "battlefield")
    for name in graveyard:
        _add(game, card_db, name, 1, "graveyard")
    for name in library:
        _add(game, card_db, name, 1, "library")
    _add(game, card_db, "Forest", 1, "hand")
    caster = _add(game, card_db, spell, 0, "hand")
    return game, caster


def _ai():
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=0, deck_name="4/5c Control",
                    rng=random.Random(0))


def _names(game, ids):
    return [game.get_card_by_id(i).name for i in ids]


# ── The aim ───────────────────────────────────────────────────────────

def test_a_same_name_exile_is_aimed_at_a_keystone_not_a_spent_land(card_db):
    game, surgical = _game(card_db, ["Misty Rainforest", "Living End"])
    assert _names(game, _ai()._choose_targets(game, surgical)) == ["Living End"]


def test_the_aim_is_the_strip_rankings_best_card_worth_aiming_at(card_db):
    """Among the cards worth aiming at, the one the caster most wants out
    of its owner's resources: the hand attack's ranking
    (`score_card_for_opponent_strip`, the owner's declared keystones and
    creature threat)."""
    from ai.ev_evaluator import score_card_for_opponent_strip, snapshot_from_game
    from ai.gameplan import get_gameplan
    from ai.graveyard_removal import worth_aiming_at
    game, surgical = _game(card_db, ["Misty Rainforest", "Street Wraith",
                                     "Living End", "Shardless Agent"])
    snap = snapshot_from_game(game, 0)
    plan = get_gameplan("Living End")
    worthy = [c for c in game.players[1].graveyard
              if worth_aiming_at(game, 0, c)]
    assert "Misty Rainforest" not in [c.name for c in worthy]
    best = max(worthy,
               key=lambda c: score_card_for_opponent_strip(c, snap, plan))
    assert _names(game, _ai()._choose_targets(game, surgical)) == [best.name]


def test_graveyard_fuel_is_worth_aiming_at_without_a_published_gameplan(
        card_db):
    """An opponent whose plan the caster cannot read: a card castable from
    their graveyard (flashback) is still a resource to them; a spent land
    is not."""
    game, surgical = _game(card_db, ["Misty Rainforest", "Faithless Looting"],
                           opp_deck="", library=())
    assert _names(game, _ai()._choose_targets(game, surgical)) == [
        "Faithless Looting"]


def test_a_removal_only_spell_is_not_aimed_at_a_card_worth_nothing(card_db):
    """Nothing worth aiming at: the spell is not cast (the picker's empty
    announcement), even on a turn whose every cast is allowed."""
    game, surgical = _game(card_db, ["Misty Rainforest"])
    assert _ai()._choose_targets(game, surgical) == []


def test_a_removal_with_a_rider_keeps_the_default_aim_for_its_rider(card_db):
    """'Exile target card from a graveyard. Draw a card.' is worth its
    draw whatever it exiles: with nothing worth aiming at it is aimed by
    the engine's printed default, not held."""
    game, cremate = _game(card_db, ["Misty Rainforest"], spell="Cremate")
    assert _names(game, _ai()._choose_targets(game, cremate)) == [
        "Misty Rainforest"]


def test_the_ai_aims_at_its_own_cards_never(card_db):
    from ai.graveyard_removal import worth_aiming_at
    game, surgical = _game(card_db, [])
    mine = _add(game, card_db, "Living End", 0, "graveyard")
    assert not worth_aiming_at(game, 0, mine)
    assert _ai()._choose_targets(game, surgical) == []


def test_a_storm_keystone_is_aimed_at_over_a_spent_ritual(card_db):
    """The rule is the owner's, not one deck's: against a storm deck the
    flashback engine it declares a keystone, not the ritual it already
    spent."""
    game, surgical = _game(card_db, ["Pyretic Ritual", "Past in Flames"],
                           opp_deck="Ruby Storm", library=())
    assert _names(game, _ai()._choose_targets(game, surgical)) == [
        "Past in Flames"]


# ── The value and the timing ──────────────────────────────────────────

def test_a_card_worth_aiming_at_is_a_reason_to_cast_this_turn(card_db):
    """Its owner uses that card on their own turn: removing it now is
    worth more than next turn, so the cast is not deferred."""
    from ai.ev_evaluator import _enumerate_this_turn_signals, snapshot_from_game
    game, surgical = _game(card_db, ["Misty Rainforest", "Living End"])
    signals = _enumerate_this_turn_signals(
        surgical, snapshot_from_game(game, 0), game, 0, "control")
    assert "graveyard_removal" in signals
    game, surgical = _game(card_db, ["Misty Rainforest"])
    signals = _enumerate_this_turn_signals(
        surgical, snapshot_from_game(game, 0), game, 0, "control")
    assert "graveyard_removal" not in signals


def test_the_value_counts_each_graveyard_copy_that_is_fuel(card_db):
    """In the hand-denial currency: each graveyard copy still a resource
    to its owner is worth its denied value; a copy that is not, nothing."""
    from ai.ev_evaluator import snapshot_from_game
    from ai.graveyard_removal import removal_shape, removal_value
    from ai.hand_denial import _denied_value
    shape = removal_shape(card_db.get_card("Surgical Extraction"))
    one, _ = _game(card_db, ["Faithless Looting"], opp_deck="", library=())
    two, _ = _game(card_db, ["Faithless Looting", "Faithless Looting"],
                   opp_deck="", library=())
    snap = snapshot_from_game(one, 0)
    v1 = removal_value(one, one.players[1].graveyard[0], shape, snap)
    v2 = removal_value(two, two.players[1].graveyard[0], shape,
                       snapshot_from_game(two, 0))
    assert v1 == _denied_value(one.players[1].graveyard[0], snap) > 0
    assert v2 == 2 * v1
    land, _ = _game(card_db, ["Misty Rainforest"], opp_deck="", library=())
    assert removal_value(land, land.players[1].graveyard[0], shape,
                         snapshot_from_game(land, 0)) == 0


def test_the_value_counts_the_hidden_copies_by_the_chance_they_are_in_hand(
        card_db):
    """The owner's hand and library are one hidden pool (the BHI premise):
    each pool copy is worth its denied value times hand size over pool
    size -- never read from which zone it actually sits in."""
    from ai.ev_evaluator import snapshot_from_game
    from ai.graveyard_removal import removal_shape, removal_value
    from ai.hand_denial import _denied_value
    shape = removal_shape(card_db.get_card("Surgical Extraction"))
    game, _ = _game(card_db, ["Misty Rainforest"], opp_deck="",
                    library=("Misty Rainforest", "Misty Rainforest", "Forest"))
    snap = snapshot_from_game(game, 0)
    opp = game.players[1]
    target = opp.graveyard[0]
    p_hand = len(opp.hand) / (len(opp.hand) + len(opp.library))
    assert removal_value(game, target, shape, snap) == \
        2 * p_hand * _denied_value(target, snap)


def test_the_cast_is_scored_with_what_it_takes(card_db):
    """The board projection sees no exile from a graveyard, hand or
    library; the spell's score carries the value of what it takes from
    the card it is aimed at."""
    from ai.ev_evaluator import snapshot_from_game
    from ai.graveyard_removal import removal_shape, removal_value
    ai = _ai()
    shape = removal_shape(card_db.get_card("Surgical Extraction"))
    fuel, s1 = _game(card_db, ["Faithless Looting", "Faithless Looting"],
                     opp_deck="", library=())
    land, s2 = _game(card_db, ["Misty Rainforest"], opp_deck="", library=())
    v = removal_value(fuel, fuel.players[1].graveyard[0], shape,
                      snapshot_from_game(fuel, 0))
    ev_fuel = ai._score_spell(s1, snapshot_from_game(fuel, 0), fuel,
                              *fuel.players)
    ev_land = ai._score_spell(s2, snapshot_from_game(land, 0), land,
                              *land.players)
    assert v > 0
    assert ev_fuel > ev_land


# ── The escape cost: what the payment spends (CR 702.138a) ────────────

def _escape_game(card_db, own_graveyard):
    """Player 0 (Grixis-style reanimator) holds Cling to Dust in its
    graveyard with `own_graveyard` beside it, a reanimation spell in hand
    (its deck can return creatures), and lands for the escape cost; the
    opponent's graveyard holds a creature."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(4):
        _add(game, card_db, "Swamp", 0, "battlefield")
        _add(game, card_db, "Island", 0, "library")
    _add(game, card_db, "Unearth", 0, "hand")
    cling = _add(game, card_db, "Cling to Dust", 0, "graveyard")
    for name in own_graveyard:
        _add(game, card_db, name, 0, "graveyard")
    _add(game, card_db, "Grizzly Bears", 1, "graveyard")
    return game, cling


def test_an_escape_cast_spends_what_its_exiled_cards_are_worth_to_it(
        card_db):
    """The payment takes the cards that serve the caster least
    (`pick_cost_exile`); each still a resource there (a creature its deck
    can reanimate) costs its denied value, a card that serves nothing
    there costs nothing."""
    from ai.ev_evaluator import snapshot_from_game
    from ai.graveyard_removal import own_cost_exile_value
    from ai.hand_denial import _denied_value
    lands, cling = _escape_game(card_db, ["Swamp"] * 5)
    assert own_cost_exile_value(lands, 0, cling,
                                snapshot_from_game(lands, 0)) == 0
    game, cling = _escape_game(card_db, ["Swamp"] * 4 + ["Archon of Cruelty"])
    snap = snapshot_from_game(game, 0)
    archon = next(c for c in game.players[0].graveyard
                  if c.name == "Archon of Cruelty")
    assert own_cost_exile_value(game, 0, cling, snap) == \
        _denied_value(archon, snap) > 0


def test_the_escape_cast_is_scored_net_of_its_cost(card_db):
    """The same removal is worth less by exactly the cost: the score with
    a reanimation target in the payment is lower than with lands only."""
    from ai.ev_evaluator import snapshot_from_game
    from ai.graveyard_removal import own_cost_exile_value
    ai = _ai()
    lands, c1 = _escape_game(card_db, ["Swamp"] * 5)
    archon, c2 = _escape_game(card_db, ["Swamp"] * 4 + ["Archon of Cruelty"])
    ev_lands = ai._score_spell(c1, snapshot_from_game(lands, 0), lands,
                               *lands.players)
    ev_archon = ai._score_spell(c2, snapshot_from_game(archon, 0), archon,
                                *archon.players)
    cost = own_cost_exile_value(archon, 0, c2, snapshot_from_game(archon, 0))
    assert cost > 0
    assert ev_archon < ev_lands
