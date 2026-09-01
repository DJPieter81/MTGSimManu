"""A planeswalker entering the battlefield must not perform an effect
that is actually one of its loyalty abilities.

Rule (CR 606 / 306): a planeswalker has no enter-the-battlefield bounce;
returning a permanent to hand is its minus loyalty ability, activated at
most once per turn by choice. A registry ETB handler that re-implements
that bounce makes the planeswalker bounce TWICE the turn it lands (once
on ETB, once when the AI activates the real ability) — and a bounced
token, appended straight to hand, wrongly persists as a card instead of
ceasing to exist (CR 111.7). The codebase had removed this exact fake
ETB before (documented as causing a double-bounce); it regressed
(audit: Dimir vs Azorius, s55630 — Teferi, Time Raveler emptied a
two-creature board with a single -3 and put a token into hand).

Card names are fixture carriers; the rule is "a planeswalker ETB does
not fire its own loyalty-ability effect."
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardType
from engine.game_state import GameState


def _mk(game, card_db, name, owner, controller, zone):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=owner, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    return c


def test_planeswalker_etb_does_not_bounce_a_permanent(card_db):
    game = GameState(rng=random.Random(0))
    # Opponent (P1) controls a creature.
    victim = _mk(game, card_db, "Grizzly Bears", 1, 1, "battlefield")
    victim.enter_battlefield()
    game.players[1].battlefield.append(victim)

    # A bounce-on-minus planeswalker enters under P0.
    pw = _mk(game, card_db, "Teferi, Time Raveler", 0, 0, "battlefield")
    assert CardType.PLANESWALKER in pw.template.card_types
    game.players[0].battlefield.append(pw)
    hand_before = len(game.players[0].hand)

    game._handle_permanent_etb(pw, 0)

    assert any(c.instance_id == victim.instance_id
               for c in game.players[1].battlefield), (
        "the opponent's permanent must stay on the battlefield — a "
        "planeswalker has no ETB bounce; that is its loyalty ability")
    assert not any(c.instance_id == victim.instance_id
                   for c in game.players[1].hand), (
        "no phantom-ETB bounce should have returned it to hand")
    assert len(game.players[0].hand) == hand_before, (
        "no phantom-ETB card draw should occur on the planeswalker entering")
