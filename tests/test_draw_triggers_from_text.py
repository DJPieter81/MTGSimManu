"""A draw-triggered ability is read from the card's own text (CR 121.1,
603.2).

"Whenever <player> draws a card" triggers on the draw its text names: who
draws, relative to the ability's controller; which card of the turn ("your
second card each turn"); and, when printed, not on the first card the
drawer draws in each of their draw steps (Orcish Bowmasters). The effect
grammar types that event on the trigger head (`EventHint.DRAW`,
`TriggerHead.draw`); a head whose rider it cannot read is typed as a draw
but not bound, so nothing resolves it as an unconditional draw trigger.
"""
from __future__ import annotations

import pytest


def _draw_heads(card_db, name):
    from engine.effect_spec import EventHint
    t = card_db.get_card(name)
    return [h.trigger for h in t.effects.walk(include_sub=False)
            if h.trigger is not None
            and EventHint.DRAW in h.trigger.event_hints]


def _typed(card_db, name):
    return [(d.drawer, d.nth, d.except_first_in_draw_step)
            for d in (h.draw for h in _draw_heads(card_db, name))
            if d is not None]


@pytest.mark.parametrize("name,typed", [
    ("Underworld Dreams", [("opponent", None, False)]),
    ("Sheoldred, the Apocalypse", [("you", None, False),
                                   ("opponent", None, False)]),
    ("Spiteful Visions", [("player", None, False)]),
    ("Psychosis Crawler", [("you", None, False)]),
    ("Irencrag Pyromancer", [("you", 2, False)]),
    ("Faerie Mastermind", [("opponent", 2, False)]),
    ("Sneaky Snacker", [("you", 3, False)]),
    ("Orcish Bowmasters", [("opponent", None, True)]),
])
def test_a_draw_trigger_head_types_who_draws_which_card_and_the_draw_step_exemption(
        card_db, name, typed):
    assert _typed(card_db, name) == typed


@pytest.mark.parametrize("name", [
    "The Watcher in the Water",          # "... during an opponent's turn"
    "Lady Octopus, Inspired Inventor",   # "your first or second card"
    "Psychic Possession",                # "enchanted opponent"
])
def test_a_draw_head_with_a_rider_it_cannot_read_is_a_draw_but_unbound(card_db, name):
    heads = _draw_heads(card_db, name)
    assert heads and all(h.draw is None for h in heads)


def test_a_draw_step_beginning_is_a_step_trigger_not_a_draw(card_db):
    from engine.effect_spec import EventHint
    t = card_db.get_card("Avaricious Dragon")
    hints = {e for h in t.effects.walk(include_sub=False)
             if h.trigger is not None for e in h.trigger.event_hints}
    assert EventHint.BEGINNING_OF in hints and EventHint.DRAW not in hints
