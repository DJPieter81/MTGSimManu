"""A hand attack whose caster has a payoff to resolve takes the card that
would stop it first.

"You choose a nonland card" (CR 701.9b): the caster picks, by what each
card does to the caster's own plan. A combo caster (a gameplan with an
EXECUTE_PAYOFF goal) wins by resolving its payoff spells; a card in the
victim's hand that can counter one of them is the card that stops the
win, so it is taken before a threat that only clocks the caster. A card
that counters only spells the payoff is not (a creature-spell counter
against noncreature payoffs) earns nothing. When the caster is about to
die to the board (the attack-imminence rule), the imminent attacker is
still taken first.

The defect (Ruby Storm vs Dimir Midrange s51000 g2, turn 7): Thoughtseize
took Psychic Frog over the Counterspell Dimir held from turn 1, which then
countered Storm's lethal Wish on turn 9. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller], zone).append(c)
    return c


def _game(card_db, victim_hand):
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Dimir Midrange"
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 7
    for _ in range(4):
        _add(game, card_db, "Mountain", 0, "battlefield")
    for name in ("Grapeshot", "Past in Flames", "Mountain"):
        _add(game, card_db, name, 0, "library")
    hand = [_add(game, card_db, n, 1, "hand") for n in victim_hand]
    return game, hand


def _pick(game):
    from ai.discard_advisor import choose_discard
    return choose_discard(game, 1, list(game.players[1].hand), False)


def test_a_combo_casters_hand_attack_takes_the_counter_over_a_threat(card_db):
    game, (frog, counter) = _game(card_db, ["Psychic Frog", "Counterspell"])
    assert _pick(game) is counter


def test_a_counter_that_cannot_stop_the_payoff_earns_nothing(card_db):
    """Essence Scatter counters creature spells only; the payoffs are
    noncreature spells, so the threat is taken."""
    game, (frog, scatter) = _game(card_db, ["Psychic Frog", "Essence Scatter"])
    assert _pick(game) is frog


def test_a_caster_with_no_payoff_to_resolve_keeps_the_threat_ranking(card_db):
    game, (frog, counter) = _game(card_db, ["Psychic Frog", "Counterspell"])
    game.players[0].deck_name = "Dimir Midrange"     # no EXECUTE_PAYOFF goal
    assert _pick(game) is frog
