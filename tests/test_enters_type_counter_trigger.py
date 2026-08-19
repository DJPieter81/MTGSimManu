"""CR 603 permanent-enters triggered counter, dispatched by card TYPE.

# Mechanic under test

"Whenever this creature or another <type> you control enters, put a
+1/+1 counter on this creature. It can't be blocked this turn." Each
qualifying enter-the-battlefield event (the source's own ETB, or any
other permanent of the named card type entering under the same
controller) puts one +1/+1 counter on the source and, where the clause
states it, makes the source unblockable that turn.

Two invariants:
  1. Fires once per qualifying ETB (repeatable watcher, not a one-shot).
  2. The source's OWN ETB counts EXACTLY once — no double-credit from
     matching both "this creature" and "another <type>".

Parsed once at DB load into `CardTemplate.enters_type_counter`;
`engine.triggers.TriggerManager.trigger_etb` reads the typed field.
Kappa Cannoneer (artifact type) is the fixture carrier; the mechanic is
type-general (any "this or another <card type> you control enters ->
+1/+1" trigger).
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType
from engine.game_state import GameState


def _bf(game, tmpl, controller):
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    return card


def _enter(game, tmpl, controller):
    """Put a permanent on the battlefield and fire its ETB fan-out,
    exactly as resolution does (append THEN trigger_etb)."""
    card = _bf(game, tmpl, controller)
    game.players[controller].battlefield.append(card)
    game.trigger_etb(card, controller)
    return card


class TestEntersTriggeredCounterFiresPerQualifyingPermanentWithoutSelfDoubleCredit:

    def test_self_etb_adds_exactly_one_counter(self, card_db):
        game = GameState(rng=random.Random(0))
        kappa_tmpl = card_db.get_card("Kappa Cannoneer")
        assert kappa_tmpl.enters_type_counter is not None
        assert kappa_tmpl.enters_type_counter["permanent_type"] == "artifact"

        base_power = kappa_tmpl.power
        kappa = _bf(game, kappa_tmpl, 0)
        game.players[0].battlefield.append(kappa)
        game.trigger_etb(kappa, 0)  # its own ETB

        assert kappa.plus_counters == 1, (
            f"self-ETB gave {kappa.plus_counters} counters, expected exactly "
            f"1 (no double-credit)"
        )
        assert kappa.power == base_power + 1

    def test_another_artifact_etb_grows_source(self, card_db):
        game = GameState(rng=random.Random(0))
        kappa = _bf(game, card_db.get_card("Kappa Cannoneer"), 0)
        game.players[0].battlefield.append(kappa)
        game.trigger_etb(kappa, 0)  # self ETB: +1
        assert kappa.plus_counters == 1

        # Another artifact enters -> +1 more.
        _enter(game, card_db.get_card("Ornithopter"), 0)
        assert kappa.plus_counters == 2

        # A second artifact enters -> +1 more (repeatable).
        _enter(game, card_db.get_card("Ornithopter"), 0)
        assert kappa.plus_counters == 3

    def test_nonartifact_etb_does_not_grow_source(self, card_db):
        game = GameState(rng=random.Random(0))
        kappa = _bf(game, card_db.get_card("Kappa Cannoneer"), 0)
        game.players[0].battlefield.append(kappa)
        game.trigger_etb(kappa, 0)
        assert kappa.plus_counters == 1

        # A non-artifact creature enters -> NO counter.
        yp = card_db.get_card("Young Pyromancer")
        assert CardType.ARTIFACT not in yp.card_types
        _enter(game, yp, 0)
        assert kappa.plus_counters == 1, (
            "non-artifact ETB wrongly grew the artifact-typed watcher"
        )

    def test_unblockable_clause_sets_flag(self, card_db):
        game = GameState(rng=random.Random(0))
        kappa = _bf(game, card_db.get_card("Kappa Cannoneer"), 0)
        assert kappa.template.enters_type_counter["unblockable_this_turn"] is True
        game.players[0].battlefield.append(kappa)
        game.trigger_etb(kappa, 0)
        assert getattr(kappa, "cannot_be_blocked_this_turn", False) is True

    def test_opponent_artifact_does_not_grow_source(self, card_db):
        """"...another artifact YOU control..." — an opponent's artifact
        ETB must not grow the watcher."""
        game = GameState(rng=random.Random(0))
        kappa = _bf(game, card_db.get_card("Kappa Cannoneer"), 0)
        game.players[0].battlefield.append(kappa)
        game.trigger_etb(kappa, 0)
        assert kappa.plus_counters == 1

        _enter(game, card_db.get_card("Ornithopter"), 1)  # opponent's artifact
        assert kappa.plus_counters == 1
