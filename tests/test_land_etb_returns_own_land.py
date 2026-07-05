"""E1b — a land's 'return a land you control' ETB clause must fire.

Probe evidence (seed 60105, Amulet vs Dimir Bo3): a land whose oracle
reads "When this land enters, return a land you control to its
owner's hand" entered the battlefield and NO return trigger fired —
across the whole Bo3.  The parse string exists in a fetch-priority
heuristic (card_effects.py) but no land-entry path executes the
clause, so the karoo family plays as drawback-free duals and the
land-recursion engine built on replaying them never exists.

Class size: the 10-karoo bounce cycle plus any land printed with the
same ETB shape.  The clause is a structural land property (a sibling
of `enters_tapped`), parsed at DB load into a template flag and
executed on the uniform land-entry hook that already carries the
untap-on-enter (Amulet-pattern) triggers — so every land-entry path
(land drop, fetch, mass land search) fires it identically.

Rule-phrased tests — card names below are carriers for the oracle
shape only.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.land_manager import LandManager


def _put_in_hand(game: GameState, card_db, name: str,
                 player_idx: int = 0) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=player_idx, controller=player_idx,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[player_idx].hand.append(card)
    return card


def _land_on_battlefield(game: GameState, card_db, name: str,
                         player_idx: int = 0) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=player_idx, controller=player_idx,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.tapped = False
    game.players[player_idx].battlefield.append(card)
    return card


def test_template_flag_parsed_from_etb_return_clause(card_db):
    karoo = card_db.get_card("Simic Growth Chamber")
    basic = card_db.get_card("Forest")
    assert karoo.etb_return_land is True
    assert basic.etb_return_land is False


def test_land_etb_return_clause_bounces_a_controlled_land(card_db):
    """Playing a land with the return-a-land ETB clause moves one of
    the controller's other lands from the battlefield to hand."""
    game = GameState(rng=random.Random(0))
    basic = _land_on_battlefield(game, card_db, "Forest")
    karoo = _put_in_hand(game, card_db, "Simic Growth Chamber")

    LandManager.play_land(game, 0, karoo)

    player = game.players[0]
    assert karoo in player.battlefield
    assert basic in player.hand, (
        "the ETB return clause must bounce a controlled land to hand")
    assert basic.zone == "hand"
    assert basic not in player.battlefield


def test_land_etb_return_clause_returns_self_when_only_land(card_db):
    """The clause is mandatory; with no other land, the entering land
    is the only legal choice and returns itself."""
    game = GameState(rng=random.Random(0))
    karoo = _put_in_hand(game, card_db, "Simic Growth Chamber")

    LandManager.play_land(game, 0, karoo)

    player = game.players[0]
    assert karoo in player.hand
    assert karoo.zone == "hand"
    assert not any(c.template.is_land for c in player.battlefield)


def test_mass_land_search_entry_path_fires_return_clause(card_db):
    """Lands put onto the battlefield by search effects (not a land
    drop) must fire the same ETB clause — the hook is the uniform
    land-entry pipeline, not play_land specifically."""
    game = GameState(rng=random.Random(0))
    basic = _land_on_battlefield(game, card_db, "Forest")

    tmpl = card_db.get_card("Simic Growth Chamber")
    karoo = CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="library",
    )
    karoo._game_state = game
    game.players[0].library.append(karoo)

    # Simulate the search-entry path used by mass land search: move to
    # battlefield tapped, then run the shared land-entry static hook.
    game.players[0].library.remove(karoo)
    karoo.zone = "battlefield"
    karoo.tapped = True
    game.players[0].battlefield.append(karoo)
    LandManager.apply_land_etb_static(game, karoo, 0)

    assert basic in game.players[0].hand
