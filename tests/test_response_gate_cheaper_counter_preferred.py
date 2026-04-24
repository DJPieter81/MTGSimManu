"""Response gate prefers the cheaper of two castable counters.

Bug R2 — when multiple counterspells in hand are all castable against
the threat, the response decider should pick the one with the lowest
*effective* mana cost (after pitch alternative-cost reductions on the
opponent's turn). Without this, the AI burns its real-mana counter
(Counterspell) when it could have pitched Force of Negation for free.

Failing case:
    - Active player (opp) casts Wrath of God against a defender with
      multiple creatures (high threat value, both counters legal).
    - Defender holds Force of Negation (1UU, pitch on opp turn) AND
      Counterspell (UU). Defender has 2 Islands + a Plains open.
    - decide_response should choose Force of Negation: zero mana
      committed, just pitch a blue card. Choosing Counterspell wastes
      the UU counter and ties up defender's mana.

Regressions covered:
    - Only Counterspell in hand → it still fires.
    - Only Force of Negation + a blue pitch target → it still fires.
    - Force of Negation with no pitch fuel and no mana → does NOT fire
      (can_cast filters it out).
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_zone(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    elif zone == "stack":
        # Caller is responsible for pushing the StackItem.
        pass
    return card


def _make_stack_item(card, controller):
    return StackItem(
        item_type=StackItemType.SPELL,
        source=card,
        controller=controller,
    )


def _build_defender_with_lands(game, card_db, defender_idx, n_islands, n_extra_lands=0):
    """Defender (player 1) gets enough untapped Islands to cast UU counters
    plus optional extra non-blue lands (Plains) so total mana >= 4 to support
    a CMC-3 Force of Negation if it were paid normally.
    """
    for _ in range(n_islands):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    for _ in range(n_extra_lands):
        _add_to_zone(game, card_db, "Plains", defender_idx, "battlefield")


def _add_dummy_creatures(game, card_db, controller, n):
    """Give a player N vanilla creatures so a board wipe carries threat value."""
    for _ in range(n):
        _add_to_zone(game, card_db, "Grizzly Bears", controller, "battlefield")


def _make_response_decider(defender_idx):
    from ai.response import ResponseDecider
    from ai.turn_planner import TurnPlanner
    return ResponseDecider(defender_idx, TurnPlanner(), strategic_logger=None)


# ─────────────────────────────────────────────────────────────────────
# R2 — primary failing test
# ─────────────────────────────────────────────────────────────────────


def test_prefers_force_of_negation_over_counterspell_on_opp_turn(card_db):
    """Both counters legal; FoN is free via pitch → must prefer FoN.

    Setup: opponent casts Wrath of God against a defender with creatures
    on the board (high threat). Defender has UU + extras, holds both
    counters and a blue pitch target. The cheaper *effective* cost is
    Force of Negation (zero mana, exile one blue card on opp turn) so it
    must be chosen over Counterspell (UU committed).
    """
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx  # opponent's turn

    # Defender has UU + extras (more than enough mana for either counter)
    _build_defender_with_lands(game, card_db, defender_idx,
                               n_islands=2, n_extra_lands=2)
    # Defender creatures so Wrath is a real threat
    _add_dummy_creatures(game, card_db, defender_idx, n=2)

    # Hand: both counters + a blue pitch target for Force of Negation.
    # Counterspell is added FIRST so a naive "first castable counter wins"
    # iteration would pick Counterspell. The fix must select on cost.
    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")
    _add_to_zone(game, card_db, "Force of Negation", defender_idx, "hand")
    _add_to_zone(game, card_db, "Consider", defender_idx, "hand")  # blue pitch fuel

    # Opponent casts Wrath of God (noncreature, high threat → both counters legal)
    wrath = _add_to_zone(game, card_db, "Wrath of God", attacker_idx, "stack")
    item = _make_stack_item(wrath, attacker_idx)
    game.stack.push(item)

    decider = _make_response_decider(defender_idx)
    result = decider.decide_response(game, item)

    assert result is not None, "Defender should counter the Wrath"
    chosen, _targets = result
    assert chosen.name == "Force of Negation", (
        f"Expected Force of Negation (free via pitch on opp turn); "
        f"got {chosen.name}. Both counters were castable but the cheaper "
        f"effective cost was Force of Negation."
    )


# ─────────────────────────────────────────────────────────────────────
# Regressions: ensure each counter still fires when it's the only option
# ─────────────────────────────────────────────────────────────────────


def test_counterspell_alone_still_fires(card_db):
    """Only Counterspell in hand → fires (no FoN to compare against)."""
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx

    _build_defender_with_lands(game, card_db, defender_idx,
                               n_islands=2, n_extra_lands=0)
    _add_dummy_creatures(game, card_db, defender_idx, n=2)

    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    wrath = _add_to_zone(game, card_db, "Wrath of God", attacker_idx, "stack")
    item = _make_stack_item(wrath, attacker_idx)
    game.stack.push(item)

    decider = _make_response_decider(defender_idx)
    result = decider.decide_response(game, item)

    assert result is not None, "Counterspell alone must still fire"
    chosen, _ = result
    assert chosen.name == "Counterspell"


def test_force_of_negation_alone_with_pitch_fuel_fires(card_db):
    """Only FoN + a blue pitch target → fires (regression for pitch path)."""
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx

    # No Islands needed — pitch path bypasses mana
    _build_defender_with_lands(game, card_db, defender_idx,
                               n_islands=0, n_extra_lands=0)
    _add_dummy_creatures(game, card_db, defender_idx, n=2)

    _add_to_zone(game, card_db, "Force of Negation", defender_idx, "hand")
    _add_to_zone(game, card_db, "Consider", defender_idx, "hand")  # blue pitch fuel

    wrath = _add_to_zone(game, card_db, "Wrath of God", attacker_idx, "stack")
    item = _make_stack_item(wrath, attacker_idx)
    game.stack.push(item)

    decider = _make_response_decider(defender_idx)
    result = decider.decide_response(game, item)

    assert result is not None, "Force of Negation alone (with pitch fuel) must fire"
    chosen, _ = result
    assert chosen.name == "Force of Negation"


def test_force_of_negation_alone_without_pitch_fuel_does_not_fire(card_db):
    """FoN with no blue pitch target and no mana → can_cast filters it out."""
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx

    _build_defender_with_lands(game, card_db, defender_idx,
                               n_islands=0, n_extra_lands=0)
    _add_dummy_creatures(game, card_db, defender_idx, n=2)

    _add_to_zone(game, card_db, "Force of Negation", defender_idx, "hand")
    # No other blue card, no lands.

    wrath = _add_to_zone(game, card_db, "Wrath of God", attacker_idx, "stack")
    item = _make_stack_item(wrath, attacker_idx)
    game.stack.push(item)

    decider = _make_response_decider(defender_idx)
    result = decider.decide_response(game, item)

    assert result is None, "FoN with no pitch target and no mana must not fire"
