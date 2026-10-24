"""A warped card is offered from exile on a later turn (CR 702.185a).

Mechanic under test
-------------------
"If this spell's warp cost was paid, exile the permanent this spell becomes
at the beginning of the next end step. Its owner may cast this card after
the current turn has ended for as long as it remains exiled." The end-step
exile registers that permission (`effect_model.permit_play`, from the next
turn on) and every play gate reads it through `rules_query`: on a later
turn the exiled card is a legal play when its MANA cost is payable (the
warp cost is a cast from the hand only); never the turn it was exiled; and
a card in exile no permission names -- a `_warped` flag alone -- is no play.

Class size: every card with a Warp cost (32 in the current DB) -- the rule
is read from the parsed `template.warp_cost`, never the card's name.
"""

from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _make_card(game, template, owner=0, zone="hand"):
    card = CardInstance(
        template=template, owner=owner, controller=owner,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    getattr(game.players[owner], zone).append(card)
    return card


def _add_mana(game, player_idx, **colors):
    pool = game.players[player_idx].mana_pool
    for color, amount in colors.items():
        pool.add(color.upper(), amount)


@pytest.fixture(scope="module")
def db():
    from engine.card_database import CardDatabase
    return CardDatabase()


@pytest.fixture
def warped(db):
    """Pinnacle Emissary (Warp {U/R}, mana cost {1}{U}{R}) cast for its
    warp cost on turn 5, resolved, and exiled at that turn's end step."""
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.turn_number = 5
    emissary = _make_card(game, db.cards["Pinnacle Emissary"])
    _add_mana(game, 0, U=1)
    assert game.cast_spell(0, emissary)
    game.resolve_stack()
    game.end_of_turn_cleanup()
    assert emissary.zone == "exile"
    game.players[0].mana_pool.empty()
    return game, emissary


class TestWarpedCardFromExile:

    def test_offered_on_a_later_turn_for_its_mana_cost(self, warped):
        game, emissary = warped
        game.turn_number = 7                      # the owner's next turn
        _add_mana(game, 0, U=1, R=1, C=1)         # {1}{U}{R}
        assert emissary in game.get_legal_plays(0)
        assert game.cast_spell(0, emissary)
        assert emissary.zone == "stack"

    def test_not_offered_the_turn_it_was_exiled(self, warped):
        game, emissary = warped
        _add_mana(game, 0, U=1, R=1, C=1)
        assert emissary not in game.get_legal_plays(0)
        assert not game.can_cast(0, emissary)

    def test_the_warp_cost_is_no_option_from_exile(self, warped):
        game, emissary = warped
        game.turn_number = 7
        _add_mana(game, 0, U=1)                   # the warp cost only
        assert emissary not in game.get_legal_plays(0)

    def test_a_card_in_exile_no_permission_names_is_no_play(self, db):
        """A `_warped` flag on an exiled card grants nothing: the rule's
        permission is what lets it be cast."""
        game = GameState(rng=random.Random(0))
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        emissary = _make_card(game, db.cards["Pinnacle Emissary"],
                              zone="exile")
        emissary._warped = True
        _add_mana(game, 0, U=1, R=1, C=1)
        assert emissary not in game.get_legal_plays(0)
        assert not game.can_cast(0, emissary)
