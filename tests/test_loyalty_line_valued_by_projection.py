"""A planeswalker's loyalty line is valued by what it does to the board,
on the same scale as a spell (CR 606): the line's typed clause is projected
through the spell projector as an ability (no card, no mana), plus the
change in the walker's own future activations at its new loyalty. The
keyword-matched integer table and "always activate the ultimate" are gone.

Rules pinned:
* every loyalty line carries its typed clause (dispatch kinds unchanged);
* a line that kills the opponent's best creature outranks a dig when a
  creature is there, and not when the board is empty;
* a line's effect value equals the same clause's projected value as a
  free ability, and a minus pays for the walker's lost future activations;
* an ultimate is chosen by value, not merely because it is affordable;
* an activation whose every line is worth less than holding is declined.
Oracle strings are fixture carriers only.
"""
from __future__ import annotations

import random

from ai.ev_evaluator import _project_spell, evaluate_board, snapshot_from_game
from ai.pw_ability import PW_DECLINE, choose_pw_ability, loyalty_line_value
from engine.cards import CardInstance, LoyaltyEffectKind
from engine.game_state import GameState, Phase
from tests.conftest import typed_walker

_BURN_OR_DIG = ("[+1]: Look at the top two cards of your library. Put one into "
                "your hand and the other into your graveyard.\n"
                "[−2]: Fixture Walker deals 3 damage to target creature.")


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.active_player = 0
    return game


def _put(game, card_db, name, controller):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _choose(game, pw):
    return choose_pw_ability(pw, pw.template.loyalty_abilities, game.players[0],
                             game.players[1], game, player_idx=0)


def test_every_loyalty_line_carries_its_typed_clause(card_db):
    t = card_db.get_card("Karn, the Great Creator")
    for ability in t.loyalty_abilities.values():
        assert ability.clause is not None
    # classified kinds keep their dispatch; only UNCLASSIFIED became CLAUSE
    assert any(a.effect_kind is not LoyaltyEffectKind.CLAUSE
               for w in card_db.cards.values()
               for a in (w.loyalty_abilities or {}).values())


def test_a_kill_line_outranks_a_dig_when_a_threat_is_there(card_db):
    game = _game()
    _put(game, card_db, "Tarmogoyf", 1)
    _put(game, card_db, "Grizzly Bears", 1)
    pw = typed_walker(card_db, game, 0, _BURN_OR_DIG, loyalty=4)
    assert _choose(game, pw) == "minus"


def test_the_dig_wins_on_an_empty_board(card_db):
    game = _game()
    pw = typed_walker(card_db, game, 0, _BURN_OR_DIG, loyalty=4)
    assert _choose(game, pw) == "plus"


def test_a_lines_effect_value_is_its_clause_projected_as_a_free_ability(card_db):
    game = _game()
    _put(game, card_db, "Grizzly Bears", 1)
    pw = typed_walker(card_db, game, 0, _BURN_OR_DIG, loyalty=4)
    minus = pw.template.loyalty_abilities["minus"]
    snap = snapshot_from_game(game, 0)
    probe = CardInstance(template=minus.clause, owner=0, controller=0,
                         instance_id=0, zone="stack")
    effect_only = (evaluate_board(_project_spell(probe, snap, None, game, 0, as_ability=True))
                   - evaluate_board(snap))
    total = loyalty_line_value(pw, minus, game, 0)
    # total = effect + residency change; a minus lowers the walker's pool
    assert total <= effect_only
    assert effect_only > 0


def test_a_minus_that_kills_the_walker_forfeits_its_whole_pool(card_db):
    from ai.ev_evaluator import expected_future_value
    game = _game()
    _put(game, card_db, "Grizzly Bears", 1)
    pw = typed_walker(card_db, game, 0, _BURN_OR_DIG, loyalty=2)
    minus = pw.template.loyalty_abilities["minus"]
    snap = snapshot_from_game(game, 0)
    assert expected_future_value(pw, snap, loyalty=0) == 0.0
    assert expected_future_value(pw, snap, loyalty=2) > 0.0
    probe = CardInstance(template=minus.clause, owner=0, controller=0,
                         instance_id=0, zone="stack")
    effect_only = (evaluate_board(_project_spell(probe, snap, None, game, 0, as_ability=True))
                   - evaluate_board(snap))
    assert loyalty_line_value(pw, minus, game, 0) < effect_only


def test_an_ultimate_is_chosen_by_value_not_affordability(card_db):
    game = _game()
    text = ("[+1]: Draw a card.\n"
            "[−6]: Fixture Walker deals 1 damage to target creature.")
    pw = typed_walker(card_db, game, 0, text, loyalty=7)
    assert _choose(game, pw) == "plus"


def test_an_activation_worth_less_than_holding_is_declined(card_db):
    game = _game()
    text = "[−2]: Fixture Walker deals 3 damage to target creature."
    pw = typed_walker(card_db, game, 0, text, loyalty=3)
    assert _choose(game, pw) == PW_DECLINE
