"""A turn plan reads the spells the player may cast from the graveyard.

A card with flashback -- printed, or granted "until end of turn" (Past in
Flames, CR 702.34a) -- and a card with escape or harmonize may be cast from
its owner's graveyard. The engine lists them among the legal plays
(`GameState` legal-play listing). A turn plan read only the hand and the
exiled cards a permission names (`ai.playable_cards`), so a storm
finisher's hold (`combo_calc.card_combo_modifier`) counted no graveyard
fuel and fired below lethal while flashback spells were still castable.

The defect: Ruby Storm goldfish s70004, turn 8. After Past in Flames,
Grapeshot fired at storm 9 for 10 into 20. Then three Reckless Impulse and
a Wrenn's Resolve were flashed back, which would have made it 14. Card names
are fixture carriers.
"""
from __future__ import annotations

import random

from ai.combo_calc import (ComboAssessment, _build_role_cache,
                           card_combo_modifier)
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase

DECK = "Ruby Storm"


def _add(game, card_db, name, zone, controller=0):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller], zone).append(c)
    return c


def _game(card_db, lands=6, storm=6):
    g = GameState(rng=random.Random(0))
    for _ in range(lands):
        _add(g, card_db, "Mountain", "battlefield")
    for _ in range(20):
        _add(g, card_db, "Mountain", "library")
    g.players[0].deck_name = DECK
    g.players[1].deck_name = "Goldfish"
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    g.turn_number = 8
    g.players[0].lands_played_this_turn = 1
    g.players[0].spells_cast_this_turn = storm
    g._global_storm_count = storm
    return g


def _modifier(game, card):
    player = EVPlayer(player_idx=0, deck_name=DECK, rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    a = ComboAssessment(resource_zone="storm", has_payoff=True,
                        combo_value=80.0,
                        payoff_names={"Grapeshot", "Empty the Warrens"},
                        _role_cache=_build_role_cache(player.goal_engine))
    return card_combo_modifier(card, a, snap, game.players[0], game, 0)


# ── The read path ──────────────────────────────────────────────────────

def test_a_flashback_spell_in_the_graveyard_is_playable(card_db):
    from ai.playable_cards import playable_cards
    g = _game(card_db)
    granted = _add(g, card_db, "Desperate Ritual", "graveyard")
    granted.has_flashback = True
    plain = _add(g, card_db, "Desperate Ritual", "graveyard")
    cards = playable_cards(g, 0)
    assert granted in cards
    assert plain not in cards


def test_an_escape_spell_in_the_graveyard_is_playable(card_db):
    from ai.playable_cards import playable_cards
    g = _game(card_db)
    t = card_db.get_card("Kroxa, Titan of Death's Hunger")
    assert t.escape_cost is not None
    kroxa = _add(g, card_db, "Kroxa, Titan of Death's Hunger", "graveyard")
    assert kroxa in playable_cards(g, 0)


def test_the_hand_itself_is_still_the_hand(card_db):
    from ai.playable_cards import plan_view
    g = _game(card_db)
    _add(g, card_db, "Desperate Ritual", "graveyard").has_flashback = True
    in_hand = _add(g, card_db, "Opt", "hand")
    assert g.players[0].hand == [in_hand]
    assert len(plan_view(g, 0).hand) == 2


# ── The plan: the closer's hold ────────────────────────────────────────

def test_a_closer_below_lethal_holds_while_flashback_fuel_is_castable(
        card_db):
    g = _game(card_db)
    shot = _add(g, card_db, "Grapeshot", "hand")
    for _ in range(2):
        _add(g, card_db, "Desperate Ritual", "graveyard").has_flashback = True
    assert _modifier(g, shot) < 0


def test_graveyard_spells_without_a_permission_are_no_fuel(card_db):
    g = _game(card_db)
    shot = _add(g, card_db, "Grapeshot", "hand")
    held = _modifier(g, shot)
    for _ in range(2):
        _add(g, card_db, "Desperate Ritual", "graveyard")
    assert _modifier(g, shot) == held
