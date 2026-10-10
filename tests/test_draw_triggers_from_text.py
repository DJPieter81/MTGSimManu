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


# ── Resolution: the draw carrier (CR 603.2, 603.3d) ─────────────────────

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game(active=0, phase=Phase.MAIN1):
    game = GameState(rng=random.Random(0))
    game.current_phase = phase
    game.active_player = active
    for p in game.players:
        p.life = 20
    return game


def _put(game, card_db, idx, name, zone="battlefield"):
    c = CardInstance(template=card_db.get_card(name), owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
    getattr(game.players[idx], zone).append(c)
    return c


def _draw(game, card_db, idx, n=1):
    for _ in range(n):
        _put(game, card_db, idx, "Island", zone="library")
    return game.draw_cards(idx, n)


def _lives(game):
    return [p.life for p in game.players]


def test_an_opponent_draw_trigger_resolves_on_each_opponent_draw_only(card_db):
    """Fate Unraveler (untagged): "whenever an opponent draws a card, ~
    deals 1 damage to that player"."""
    game = _game()
    _put(game, card_db, 0, "Fate Unraveler")
    _draw(game, card_db, 1, 2)
    assert _lives(game) == [20, 18]
    _draw(game, card_db, 0)
    assert _lives(game) == [20, 18]


@pytest.mark.parametrize("name,after", [("Psychosis Crawler", [20, 19]),
                                        ("Horizon Chimera", [21, 20])])
def test_a_your_draw_trigger_resolves_on_its_controllers_draws_only(card_db, name, after):
    game = _game()
    _put(game, card_db, 0, name)
    _draw(game, card_db, 1)
    assert _lives(game) == [20, 20]
    _draw(game, card_db, 0)
    assert _lives(game) == after


def test_the_nth_card_of_the_turn_triggers_on_that_draw_only(card_db):
    """Kang: "whenever you draw your second card each turn, each opponent
    loses 1 life and you gain 1 life"."""
    game = _game()
    _put(game, card_db, 0, "Kang, Temporal Tyrant")
    _draw(game, card_db, 0)
    assert _lives(game) == [20, 20]
    _draw(game, card_db, 0)
    assert _lives(game) == [21, 19]
    _draw(game, card_db, 0)
    assert _lives(game) == [21, 19]


def test_a_draw_trigger_without_the_exemption_fires_on_the_draw_step_draw(card_db):
    """Underworld Dreams prints no "except the first one ..." clause, so
    the opponent's draw-step draw triggers it."""
    game = _game(active=1, phase=Phase.DRAW)
    _put(game, card_db, 0, "Underworld Dreams")
    _draw(game, card_db, 1)
    assert _lives(game) == [20, 19]


def test_a_targeted_draw_trigger_aims_where_its_controller_chooses(card_db):
    """Niv-Mizzet, Parun: "whenever you draw a card, ~ deals 1 damage to
    any target" -- the target is chosen as the trigger is put on the stack
    (CR 603.3d): by default the opponent's face, else the controller's pick
    out of the legal choices."""
    from engine.callbacks import DefaultCallbacks
    game = _game()
    _put(game, card_db, 0, "Niv-Mizzet, Parun")
    bears = _put(game, card_db, 1, "Grizzly Bears")
    _draw(game, card_db, 0)
    assert _lives(game) == [20, 19] and bears.damage_marked == 0

    class _AtTheBears(DefaultCallbacks):
        def choose_trigger_targets(self, game, player_idx, source, spec, req,
                                   players, permanents):
            assert bears in permanents and 1 in players
            return [bears]
    game.callbacks = _AtTheBears()
    _draw(game, card_db, 0)
    assert _lives(game) == [20, 19]
    assert bears.damage_marked == 1 or bears.zone == "graveyard"


def test_bowmasters_draw_trigger_deals_its_damage_and_amasses(card_db):
    """Orcish Bowmasters: "... whenever an opponent draws a card except
    the first one they draw in each of their draw steps, ~ deals 1 damage
    to any target. Then amass Orcs 1." By default the damage goes to the
    opponent's face."""
    game = _game()
    _put(game, card_db, 0, "Orcish Bowmasters")
    _draw(game, card_db, 1)
    assert _lives(game) == [20, 19]
    armies = [c for c in game.players[0].battlefield
              if "Army" in (c.template.subtypes or [])]
    assert len(armies) == 1 and armies[0].plus_counters == 1


def test_bowmasters_skips_the_first_card_of_the_drawers_own_draw_step(card_db):
    game = _game(active=1, phase=Phase.DRAW)
    _put(game, card_db, 0, "Orcish Bowmasters")
    _draw(game, card_db, 1)
    assert _lives(game) == [20, 20]
    _draw(game, card_db, 1)
    assert _lives(game) == [20, 19]


def test_the_ai_aims_a_draw_triggers_damage_with_its_damage_aim(card_db):
    """The AI picks the target as its damage aim does
    (`ai.damage_targets.choose_damage_recipient`): the opposing permanent
    the damage destroys that is worth most, else the face."""
    from ai.damage_targets import choose_damage_recipient
    from engine.game_runner import AICallbacks
    game = _game()
    game.callbacks = AICallbacks()
    bow = _put(game, card_db, 0, "Orcish Bowmasters")
    rager = _put(game, card_db, 1, "Dragon's Rage Channeler")
    host = next(h for h in bow.template.effects.front()
                if h.trigger is not None and h.trigger.draw is not None)
    aim = choose_damage_recipient(game, 0, bow, 1, host.targets[0],
                                  mana_committed=0)
    _draw(game, card_db, 1)
    if aim.permanent is rager:
        assert rager.zone == "graveyard" and _lives(game) == [20, 20]
    else:
        assert rager.zone == "battlefield" and _lives(game) == [20, 19]
