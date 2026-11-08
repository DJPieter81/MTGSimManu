"""An effect that says a player loses (or wins) the game ends it (CR
104.2b, 104.3c); "pay <cost>. If you don't, <instruction>" is a choice
its controller makes as it resolves (CR 118.12, 608.2c).

CR 104.3c: if an effect says a player loses the game, that player loses.
CR 104.2b: if an effect says a player wins the game, that player wins.
CR 104.4a: if every player loses at once, the game is a draw.

The defect: the effect grammar recognised "lose the game" and "win the
game" and refused both, and typed no executor for "pay <cost>" with an
"if you don't" branch. A pact ("<effect>. At the beginning of your next
upkeep, pay <cost>. If you don't, you lose the game.") therefore resolved
through a name-keyed handler that performed the search and dropped the
upkeep trigger: its caster never paid and never lost, so a pact was a free
spell.

Class (`tools/class_census.py "(you|that player|target player|each
opponent|its controller) (lose|loses|win|wins) the game"`): 57 pool cards.
Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.delayed_triggers import DelayedTriggerStep
from engine.effect_spec import Verb, iter_specs
from engine.game_state import GameState, Phase

PACT = "Summoner's Pact"


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


def _game(card_db, forests):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 1
    for _ in range(forests):
        _add(game, card_db, "Forest", 0)
    for name in ("Primeval Titan", "Forest", "Forest", "Forest"):
        _add(game, card_db, name, 0, "library")
    for _ in range(4):
        _add(game, card_db, "Island", 1, "library")
    return game


def _cast(game, card_db, name, controller=0):
    spell = _add(game, card_db, name, controller, "hand")
    assert game.cast_spell(controller, spell, [])
    game.resolve_stack()
    return spell


def _next_own_upkeep(game, player=0):
    for p in game.players[player].battlefield:
        p.tapped = False
    game.turn_number += 2
    game.active_player = player
    game.current_phase = Phase.UPKEEP
    game.fire_delayed_triggers(DelayedTriggerStep.UPKEEP)


def _hosts(text):
    from engine.effect_grammar.link import parse_face_hosts
    return parse_face_hosts(text)


# ── The grammar ────────────────────────────────────────────────────────

def test_lose_the_game_and_win_the_game_are_player_instructions():
    (lose,) = _hosts("you lose the game.")
    (win,) = _hosts("target opponent loses the game.")
    (won,) = _hosts("you win the game.")
    assert [s.verb for s in lose.specs] == [Verb.LOSE_GAME]
    assert [s.verb for s in win.specs] == [Verb.LOSE_GAME]
    assert [s.verb for s in won.specs] == [Verb.WIN_GAME]


def test_a_pacts_spell_and_its_upkeep_trigger_are_executable(card_db):
    from engine.effect_carrier import spell_family
    from engine.effect_resolver import can_execute
    tmpl = card_db.get_card(PACT)
    host = tmpl.effects.spell(0)
    assert can_execute(host) and spell_family(tmpl) is not None
    trigger = next(s for s in iter_specs(host.specs)
                   if s.verb is Verb.CREATE_TRIGGER)
    (pay,) = trigger.payload.host.specs
    assert [s.verb for s in pay.otherwise] == [Verb.LOSE_GAME]


def test_a_static_that_says_a_player_cant_lose_is_no_instruction():
    """'You can't lose the game' is a rule-changing static (CR 104.3c's
    exception), not an instruction to lose."""
    hosts = _hosts("you can't lose the game and your opponents can't win "
                   "the game.")
    assert not any(s.verb in (Verb.LOSE_GAME, Verb.WIN_GAME)
                   for h in hosts for s in iter_specs(h.specs))


# ── Resolution ─────────────────────────────────────────────────────────

def test_a_loss_is_the_other_players_win_and_losing_together_is_a_draw():
    game = GameState(rng=random.Random(0))
    game.end_game(losers=(0,))
    assert game.game_over and game.winner == 1
    game = GameState(rng=random.Random(0))
    game.end_game(winners=(0,))
    assert game.game_over and game.winner == 0
    game = GameState(rng=random.Random(0))
    game.end_game(losers=(0, 1))                 # CR 104.4a
    assert game.game_over and game.winner is None


def test_a_game_already_over_is_not_ended_again():
    game = GameState(rng=random.Random(0))
    game.end_game(losers=(1,))
    game.end_game(losers=(0,))
    assert game.winner == 0


def test_a_delayed_upkeep_cost_its_controller_cannot_pay_loses_the_game(
        card_db):
    game = _game(card_db, 0)
    _cast(game, card_db, PACT)
    assert any(c.name == "Primeval Titan" for c in game.players[0].hand)
    assert not game.game_over
    _next_own_upkeep(game)
    assert game.game_over and game.winner == 1


def test_a_delayed_upkeep_cost_its_controller_can_pay_is_paid(card_db):
    from engine.game_runner import AICallbacks
    game = _game(card_db, 4)
    game.callbacks = AICallbacks()
    _cast(game, card_db, PACT)
    _next_own_upkeep(game)
    assert not game.game_over
    assert sum(1 for c in game.players[0].battlefield if c.tapped) == 4


def test_the_delayed_cost_waits_for_its_controllers_own_upkeep(card_db):
    game = _game(card_db, 0)
    _cast(game, card_db, PACT)
    game.turn_number += 1
    game.active_player = 1                       # the opponent's upkeep
    game.fire_delayed_triggers(DelayedTriggerStep.UPKEEP)
    assert not game.game_over


def test_the_search_still_finds_a_card_of_the_printed_kind(card_db):
    game = _game(card_db, 4)
    _cast(game, card_db, PACT)
    found = [c for c in game.players[0].hand if c.name == "Primeval Titan"]
    assert len(found) == 1
    assert all(c.name != "Primeval Titan" for c in game.players[0].library)


# ── The auditor ────────────────────────────────────────────────────────

import pytest  # noqa: E402


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    from engine import rules_audit
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit, rule):
    return [f for f in audit.drain()
            if f["rule"] == rule and f["kind"] == "violation"]


def test_the_game_end_auditor_sees_a_loss_that_did_not_end_the_game(
        card_db, audit, monkeypatch):
    """Re-create the defect: the game-end owner does nothing."""
    monkeypatch.setattr(GameState, "end_game", lambda self, **kw: None)
    game = _game(card_db, 0)
    _cast(game, card_db, PACT)
    _next_own_upkeep(game)
    assert not game.game_over
    assert _violations(audit, "104.3c/effect_ends_the_game")


def test_the_game_end_auditor_is_silent_when_the_loss_ends_the_game(
        card_db, audit):
    game = _game(card_db, 0)
    _cast(game, card_db, PACT)
    _next_own_upkeep(game)
    assert game.game_over
    assert not _violations(audit, "104.3c/effect_ends_the_game")


# ── The caster's choice ────────────────────────────────────────────────

def _ai():
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=0, deck_name="Amulet Titan",
                    rng=random.Random(0))


def _pact_in_hand(card_db, forests):
    game = _game(card_db, forests)
    game.players[0].deck_name = "Amulet Titan"
    game.players[1].deck_name = "Goldfish"
    game.players[0].lands_played_this_turn = 1
    pact = _add(game, card_db, PACT, 0, "hand")
    return game, pact


def test_a_spell_whose_deferred_cost_will_be_unpayable_is_not_cast(card_db):
    from ai.deferred_costs import deferred_costs_payable
    game, pact = _pact_in_hand(card_db, 1)
    assert not deferred_costs_payable(game, 0, pact)
    chosen = _ai().decide_main_phase(game)
    assert not (chosen and chosen[1] is pact)


def test_the_deferred_cost_is_read_against_the_board_untapped_at_that_upkeep(
        card_db):
    """Tapped lands untap before the upkeep (CR 502.3): four tapped Forests
    pay {2}{G}{G} then."""
    from ai.deferred_costs import deferred_costs_payable
    game, pact = _pact_in_hand(card_db, 4)
    for c in game.players[0].battlefield:
        c.tapped = True
    assert deferred_costs_payable(game, 0, pact)
    assert all(c.tapped for c in game.players[0].battlefield)


def test_a_spell_with_no_deferred_cost_is_unaffected(card_db):
    from ai.deferred_costs import deferred_costs_payable, deferred_loss_costs
    game = _game(card_db, 0)
    titan = _add(game, card_db, "Primeval Titan", 0, "hand")
    assert deferred_loss_costs(titan.template) == []
    assert deferred_costs_payable(game, 0, titan)


def test_a_second_deferred_cost_is_payable_only_beside_the_one_pending(
        card_db):
    """Two pacts are paid at the same upkeep from the same board: four
    Forests pay one {2}{G}{G}, not two."""
    from ai.deferred_costs import deferred_costs_payable
    game, pact = _pact_in_hand(card_db, 4)
    assert deferred_costs_payable(game, 0, pact)
    _cast(game, card_db, PACT)                   # one pact now pending
    second = _add(game, card_db, PACT, 0, "hand")
    assert not deferred_costs_payable(game, 0, second)
