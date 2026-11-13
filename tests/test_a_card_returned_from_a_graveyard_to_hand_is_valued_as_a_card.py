"""A card returned from its owner's graveyard to their hand is valued as one
more card in that hand (AI, from CR 400.7 and 608.2b).

"[You may] return [up to N] target <card> from your graveyard to your
hand" appears as:
- a spell's instruction (Raise Dead);
- an enter trigger (Eternal Witness);
- a loyalty line (Wrenn and Six's +1).

The engine performs it (`engine.effect_executors.graveyard_to_hand`, and
the loyalty RETURN_TO_HAND resolver). Each card it returns is one more card
in its controller's hand.

The AI's rule (`ai.regrowth`): the cast projection and a loyalty line's
value count the cards the instruction returns:
- the instruction's count;
- no more than the cards its requirement admits in that graveyard now.

Each counts on the snapshot's card term (`my_hand_size`), the term a drawn
card counts on. Nothing to return is worth nothing.

The defect: neither the projection nor the loyalty value counted the card.
Wrenn and Six's +1, with a fetchland to return, was worth 0. After unit LY
valued the −1 by its aim, the AI pinged a 31-life opponent for 1 instead of
returning the land (digest: Pinnacle Affinity vs 4/5c Control s52500, T7).
One more card does not flip that spot: there the card term is worth 0.125
and 1 damage 0.143. A land card its controller can still play this turn is
worth the land it becomes, not an average card. That is a lead, not this
unit.

Card names are fixture carriers: each is one member of the class.
"""
from __future__ import annotations

import random

import pytest

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


def _game(card_db, opp_life=20):
    from engine.game_runner import AICallbacks
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.players[0].deck_name = "4c Omnath"
    game.callbacks = AICallbacks()
    for p in (0, 1):
        for _ in range(4):
            _add(game, card_db, "Island", p, "library")
    for _ in range(4):
        _add(game, card_db, "Forest", 0)
    game.players[1].life = opp_life
    return game


def _card_value(game):
    """What one more card in the controller's hand is worth on the board."""
    from ai.ev_evaluator import evaluate_board, snapshot_from_game
    snap = snapshot_from_game(game, 0)
    more = snap.model_copy(update={"my_hand_size": snap.my_hand_size + 1})
    return evaluate_board(more) - evaluate_board(snap)


# ── The count (one owner) ─────────────────────────────────────────────

def test_the_count_is_the_cards_the_requirement_admits_up_to_its_count(card_db):
    from ai.regrowth import cards_returned_on_cast
    game = _game(card_db)
    raise_dead = card_db.get_card("Raise Dead")       # target creature card
    assert cards_returned_on_cast(game, 0, raise_dead) == 0
    _add(game, card_db, "Forest", 0, "graveyard")
    assert cards_returned_on_cast(game, 0, raise_dead) == 0
    _add(game, card_db, "Grizzly Bears", 0, "graveyard")
    _add(game, card_db, "Memnite", 0, "graveyard")
    assert cards_returned_on_cast(game, 0, raise_dead) == 1


# ── The cast projection ───────────────────────────────────────────────

@pytest.mark.parametrize("name", ["Raise Dead", "Eternal Witness"])
def test_a_cast_that_returns_a_card_projects_it_into_the_hand(card_db, name):
    """A spell's instruction and an enter trigger alike: one more card in
    hand than the same cast with nothing to return."""
    from ai.ev_evaluator import _project_spell, snapshot_from_game
    hands = []
    for buried in (False, True):
        game = _game(card_db)
        if buried:
            _add(game, card_db, "Grizzly Bears", 0, "graveyard")
        card = _add(game, card_db, name, 0, "hand")
        snap = snapshot_from_game(game, 0)
        hands.append(_project_spell(card, snap, None, game, 0).my_hand_size
                     - snap.my_hand_size)
    assert hands[1] - hands[0] == 1


# ── A loyalty line ────────────────────────────────────────────────────

def _six(card_db, buried, opp_life=20):
    game = _game(card_db, opp_life)
    six = _add(game, card_db, "Wrenn and Six", 0)
    six.loyalty_counters = 3
    if buried:
        _add(game, card_db, "Flooded Strand", 0, "graveyard")
    return game, six


def test_a_line_that_returns_a_card_is_worth_one_more_card_in_hand(card_db):
    from ai.pw_ability import loyalty_line_value
    values = []
    for buried in (True, False):
        game, six = _six(card_db, buried)
        values.append(loyalty_line_value(
            six, six.template.loyalty_abilities["plus"], game, 0))
    game, _ = _six(card_db, True)
    assert values[0] - values[1] == pytest.approx(_card_value(game))
