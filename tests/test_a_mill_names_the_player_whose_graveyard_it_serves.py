"""A mill names the player whose graveyard it serves (AI, from CR 701.17a).

To mill N cards, a player puts the top N cards of their library into
their graveyard (CR 701.17a). The library is hidden and its order random,
so a mill leaves the milled player's expected draws unchanged. What it
changes is their graveyard: each milled card becomes whatever resource it
is there to its owner, graveyard fuel (`ai.predicates`). So a mill that
names its player ("target player mills two cards") is worth to its caster
the fuel it adds to the caster's own graveyard, and costs the caster the
fuel it adds to an opponent's. The caster names the admitted player it is
worth most to, and on a tie keeps the engine's default. A mill that leaves
a player's library empty costs that player the game at their next draw
(CR 121.4, 704.5b), which outweighs any fuel.

The defect: the player owner (`ai.modal.named_player_value`) did not value
a mill, so every targeted mill took the default, the opponent. Once unit
DW performed Thought Scour's mill, Grixis Reanimator milled Domain Zoo,
whose Psychic Frogs exile graveyard cards for flying, instead of its own
library of reanimation targets. The Grixis Reanimator vs Domain Zoo cell
fell 70 -> 20.

The fuel tiers widen with it: a cost that exiles cards from its owner's
graveyard (an activated ability's, an escape's other cards) consumes
graveyard size like delve, and a harmonize card is castable from the
graveyard like a flashback card.

Card names are fixture carriers: 113 pool cards print "target player
mills".
"""
from __future__ import annotations

import random

import pytest

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


def _game(card_db, mine=("Island",) * 6, theirs=("Forest",) * 6,
          callbacks=None):
    """Player 0 with two Islands and a library of `mine`; player 1 with a
    library of `theirs`."""
    game = GameState(rng=random.Random(0), callbacks=callbacks)
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(2):
        _add(game, card_db, "Island", 0, "battlefield")
    for name in mine:
        _add(game, card_db, name, 0, "library")
    for name in theirs:
        _add(game, card_db, name, 1, "library")
    return game


def _ai():
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=0, deck_name="Grixis Reanimator",
                    rng=random.Random(0))


def _mill_spec(card_db):
    from engine.effect_spec import Verb, iter_specs
    host = card_db.get_card("Thought Scour").effects.spell(0)
    return next(s for s in iter_specs(host.specs) if s.verb is Verb.MILL)


_REANIMATION_LIBRARY = ("Archon of Cruelty", "Grizzly Bears", "Island",
                        "Island")


# ── Graveyard fuel: the tiers widen ───────────────────────────────────

def test_a_cost_that_exiles_cards_from_a_graveyard_consumes_its_size(
        card_db):
    """'Exile three cards from your graveyard: ...' spends graveyard size
    as delve does: once its owner reveals it, every card in their
    graveyard is fuel to them."""
    from ai.predicates import graveyard_fuel
    game = _game(card_db)
    spent = [_add(game, card_db, "Forest", 1, "graveyard") for _ in range(3)]
    assert graveyard_fuel(game, 1) == []
    _add(game, card_db, "Psychic Frog", 1, "battlefield")
    assert graveyard_fuel(game, 1) == spent


def test_an_escape_cost_consumes_the_other_cards_in_its_graveyard(card_db):
    from ai.predicates import graveyard_fuel
    game = _game(card_db)
    land = _add(game, card_db, "Forest", 1, "graveyard")
    dust = _add(game, card_db, "Cling to Dust", 1, "graveyard")
    assert graveyard_fuel(game, 1) == [land, dust]


def test_a_harmonize_card_is_castable_from_its_graveyard(card_db):
    from ai.predicates import graveyard_fuel
    game = _game(card_db)
    _add(game, card_db, "Forest", 1, "graveyard")
    ride = _add(game, card_db, "Wild Ride", 1, "graveyard")
    assert graveyard_fuel(game, 1) == [ride]


# ── The share of a library a mill turns into fuel ─────────────────────

def test_the_casters_own_share_is_read_from_its_whole_deck(card_db):
    """The pilot knows its decklist: a reanimation spell still in its hand
    makes the creature cards of its library fuel (two of four)."""
    from ai.predicates import graveyard_fuel_share
    game = _game(card_db, mine=_REANIMATION_LIBRARY)
    assert graveyard_fuel_share(game, 0, 0) == 0.0
    _add(game, card_db, "Persist", 0, "hand")
    assert graveyard_fuel_share(game, 0, 0) == 0.5


def test_an_opponents_share_is_estimated_from_what_they_revealed(card_db):
    """An opponent's library and hand are hidden: their share is read from
    the cards they revealed (battlefield and graveyard), the viewer's
    sample of their deck."""
    from ai.predicates import graveyard_fuel_share
    game = _game(card_db, theirs=("Grizzly Bears",) * 6)
    _add(game, card_db, "Persist", 1, "hand")
    assert graveyard_fuel_share(game, 0, 1) == 0.0, "nothing revealed"
    _add(game, card_db, "Forest", 1, "battlefield")
    _add(game, card_db, "Faithless Looting", 1, "graveyard")
    assert graveyard_fuel_share(game, 0, 1) == 0.5
    _add(game, card_db, "Psychic Frog", 1, "battlefield")
    assert graveyard_fuel_share(game, 0, 1) == 1.0


# ── What naming a player is worth ─────────────────────────────────────

def test_a_mill_is_worth_the_fuel_it_adds_to_the_named_players_graveyard(
        card_db):
    from ai.modal import named_player_value
    game = _game(card_db, mine=_REANIMATION_LIBRARY)
    _add(game, card_db, "Persist", 0, "hand")
    _add(game, card_db, "Psychic Frog", 1, "battlefield")
    spec = _mill_spec(card_db)
    assert named_player_value(game, 0, spec, 0) == 2 * 0.5
    assert named_player_value(game, 0, spec, 1) == -2 * 1.0


def test_a_reanimation_pilot_mills_itself(card_db):
    game = _game(card_db, mine=_REANIMATION_LIBRARY)
    _add(game, card_db, "Persist", 0, "hand")
    scour = _add(game, card_db, "Thought Scour", 0, "hand")
    assert _ai()._choose_targets(game, scour) == [PLAYER_TARGET_SELF]


def test_a_caster_with_no_graveyard_plan_does_not_fuel_an_opponents(
        card_db):
    game = _game(card_db)
    _add(game, card_db, "Psychic Frog", 1, "battlefield")
    scour = _add(game, card_db, "Thought Scour", 0, "hand")
    assert _ai()._choose_targets(game, scour) == [PLAYER_TARGET_SELF]


def test_with_no_fuel_on_either_side_the_default_opponent_is_named(card_db):
    game = _game(card_db)
    scour = _add(game, card_db, "Thought Scour", 0, "hand")
    assert _ai()._choose_targets(game, scour) == [PLAYER_TARGET_OPPONENT]


@pytest.mark.parametrize("mine, theirs", [
    (_REANIMATION_LIBRARY, ("Forest", "Forest")),
    (("Archon of Cruelty", "Grizzly Bears"), ("Forest",) * 6),
], ids=["decks_the_opponent", "never_decks_itself"])
def test_a_mill_that_empties_a_library_decks_its_player(card_db, mine,
                                                        theirs):
    """Whatever the fuel: a library the mill empties loses its player the
    game at their next draw. The caster's own library is fuel in both
    cases, yet it names the opponent it can deck, and never decks
    itself."""
    game = _game(card_db, mine=mine, theirs=theirs)
    _add(game, card_db, "Persist", 0, "hand")
    scour = _add(game, card_db, "Thought Scour", 0, "hand")
    assert _ai()._choose_targets(game, scour) == [PLAYER_TARGET_OPPONENT]


def test_the_named_player_mills_when_the_spell_resolves(card_db):
    """Through the runner's callbacks and the real stack: the caster's
    own library loses the two milled cards and the one drawn."""
    from engine.game_runner import AICallbacks
    callbacks = AICallbacks()
    game = _game(card_db, mine=_REANIMATION_LIBRARY, callbacks=callbacks)
    callbacks.bind_players([_ai()])
    _add(game, card_db, "Persist", 0, "hand")
    scour = _add(game, card_db, "Thought Scour", 0, "hand")
    assert game.cast_spell(0, scour, [])
    assert game.stack.items[-1].targets == [PLAYER_TARGET_SELF]
    while not game.stack.is_empty:
        game.resolve_stack()
    assert len(game.players[0].library) == 1
    assert len(game.players[0].graveyard) == 3
    assert len(game.players[1].library) == 6
