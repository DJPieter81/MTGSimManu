"""An optional loyalty activation must be declinable (CR 606.3).

Deep-audit Phase 2, control panel Finding 1 (+ the Phase-1 planeswalker
convergence): a planeswalker whose only EXECUTABLE slot is a loyalty-minus is
forced to fire it every turn — even when its targeted effect has no legal
target and only a value rider (e.g. "draw a card") remains. Teferi, Time
Raveler's +1 ("cast sorceries as though they had flash") is UNCLASSIFIED, so the
engine filters it out and offers only the −3 bounce; at 4 loyalty the suicide
guard does not fire (it survives at 1), and into an empty board the bounce
whiffs — the walker ticks 4→1 for a single card and forfeits the answer it
needed later.

Rule (no card names): decline a loyalty-negative activation whose targeted
primary effect has no legal target (only a rider remains), unless the race is
failing (PANIC/LETHAL, where the walker is spent freely).
"""
from __future__ import annotations

import random

from ai.clock import LifePhase, life_phase
from ai.ev_evaluator import snapshot_from_game
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _walker_from(card_db, game, pw_data, loyalty):
    """The same printed lines as a typed walker (what a loaded card carries)."""
    from tests.conftest import typed_walker
    text = "\n".join(
        f"[{'+' if cost > 0 else ('−' if cost < 0 else '')}{abs(cost)}]: {desc}"
        for cost, desc in pw_data.values())
    return typed_walker(card_db, game, 0, text, loyalty=loyalty)


class _FakeWalker:
    def __init__(self, loyalty: int):
        self.loyalty_counters = loyalty


_TEFERI_MINUS = (-3, "Return up to one target artifact, creature, or "
                     "planeswalker to its owner's hand. Draw a card.")


def _quiet_game():
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "control_side"
    game.players[1].deck_name = "opp_side"
    game.players[0].life = 20
    game.players[1].life = 20
    game.turn_number = 6
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def test_minus_loyalty_ability_declined_when_its_targeted_effect_has_no_legal_target(card_db):
    from ai.pw_ability import choose_pw_ability, PW_DECLINE
    game = _quiet_game()  # empty opposing board — the bounce has no target
    snap = snapshot_from_game(game, 0)
    assert life_phase(snap) not in (LifePhase.PANIC, LifePhase.LETHAL)
    pw = _FakeWalker(loyalty=4)  # -3 leaves 1: NOT a suicide
    pw_data = {"minus": _TEFERI_MINUS}  # +1 is unclassified → engine filtered it
    pw = _walker_from(card_db, game, pw_data, pw.loyalty_counters)
    choice = choose_pw_ability(pw, pw.template.loyalty_abilities,
                               game.players[0], game.players[1], game, player_idx=0)
    assert choice == PW_DECLINE, (
        f"a loyalty-negative bounce with no legal target and a safe race must "
        f"be declined (hold loyalty), got {choice!r}")


def test_walker_with_only_executable_minus_is_not_forced_to_tick_down(card_db):
    from ai.pw_ability import choose_pw_ability
    game = _quiet_game()
    pw = _FakeWalker(loyalty=4)
    pw_data = {"minus": _TEFERI_MINUS}
    pw = _walker_from(card_db, game, pw_data, pw.loyalty_counters)
    choice = choose_pw_ability(pw, pw.template.loyalty_abilities,
                               game.players[0], game.players[1], game, player_idx=0)
    assert choice != "minus", "must not tick down into a whiff"


def test_minus_activated_when_its_bounce_has_a_real_target(card_db):
    """S4 restatement: the minus is activated when what it bounces is worth
    the loyalty it spends — here the opponent's only creature, a 5/5 clock.
    (Before S4 any legal target counted; a bare replayable two-drop does not
    pay for three loyalty — see the next test.)"""
    from ai.pw_ability import choose_pw_ability, PW_DECLINE
    game = _quiet_game()
    _add(game, card_db, "Gurmag Angler", 1, "battlefield")  # a real threat
    pw = _FakeWalker(loyalty=4)
    pw_data = {"minus": _TEFERI_MINUS}
    pw = _walker_from(card_db, game, pw_data, pw.loyalty_counters)
    choice = choose_pw_ability(pw, pw.template.loyalty_abilities,
                               game.players[0], game.players[1], game, player_idx=0)
    assert choice == "minus", (
        f"with a threat worth the loyalty the minus is real value — activate, "
        f"got {choice!r}")
    assert choice != PW_DECLINE


def test_a_replayable_bounce_worth_less_than_its_loyalty_is_held(card_db):
    """A bare two-mana artifact the opponent replays next turn does not pay
    for three loyalty (three future activations): hold the walker."""
    from ai.pw_ability import choose_pw_ability, PW_DECLINE
    game = _quiet_game()
    _add(game, card_db, "Isochron Scepter", 1, "battlefield")
    for _ in range(5):
        _add(game, card_db, "Island", 1, "battlefield")
    pw = _walker_from(card_db, game, {"minus": _TEFERI_MINUS}, 4)
    choice = choose_pw_ability(pw, pw.template.loyalty_abilities,
                               game.players[0], game.players[1], game, player_idx=0)
    assert choice == PW_DECLINE


def test_a_loyalty_positive_line_is_never_declined(card_db):
    from ai.pw_ability import choose_pw_ability, PW_DECLINE
    game = _quiet_game()
    pw = _FakeWalker(loyalty=3)
    pw_data = {"plus": (1, "Look at the top two cards of your library. "
                           "Put one into your hand.")}
    pw = _walker_from(card_db, game, pw_data, pw.loyalty_counters)
    choice = choose_pw_ability(pw, pw.template.loyalty_abilities,
                               game.players[0], game.players[1], game, player_idx=0)
    assert choice == "plus" and choice != PW_DECLINE
