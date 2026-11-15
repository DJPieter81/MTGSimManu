"""A permanent an effect puts onto the battlefield triggers its enter
abilities, wherever it came from (CR 603.6a: an ability that triggers
"when <this> enters" triggers each time it enters).

The defect (Domain Zoo diagnosis 2026-11-15, 4c Omnath s51500 g1 t8):
Phelia's delayed "return that card to the battlefield" moved the card and
then called `trigger_etb`, which only queues template abilities, never the
card's own enter effect. A Solitude blinked by Phelia returned and exiled
nothing. Every entry now runs through the one enter owner,
`GameState._handle_permanent_etb`, as the immediate blink path already
does. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_runner import AICallbacks, GameRunner
from engine.game_state import GameState, Phase

_DB = CardDatabase()


def _bf(game, name, controller):
    tmpl = _DB.get_card(name)
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def test_a_permanent_returned_by_a_delayed_blink_runs_its_enter_effect():
    game = GameState(rng=random.Random(0))
    game.callbacks = AICallbacks()
    game.current_phase = Phase.END_STEP
    game.active_player = 0
    game.players[0].deck_name = "Jeskai Blink"
    game.players[1].deck_name = "Domain Zoo"
    phelia = _bf(game, "Phelia, Exuberant Shepherd", 0)
    solitude = _bf(game, "Solitude", 0)
    threat = _bf(game, "Grizzly Bears", 1)
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    EFFECT_REGISTRY.execute("Phelia, Exuberant Shepherd", EffectTiming.ATTACK,
                            game, phelia, 0, targets=[solitude.instance_id])
    assert solitude in game.players[0].exile
    GameRunner(_DB, rng=random.Random(0))._process_end_step_returns(game, 0)
    assert solitude in game.players[0].battlefield
    assert threat not in game.players[1].battlefield, (
        "Solitude's enter ability must trigger on its return")


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


def _blink_and_return():
    game = GameState(rng=random.Random(0))
    game.callbacks = AICallbacks()
    game.current_phase = Phase.END_STEP
    game.active_player = 0
    game.players[0].deck_name = "Jeskai Blink"
    game.players[1].deck_name = "Domain Zoo"
    phelia = _bf(game, "Phelia, Exuberant Shepherd", 0)
    solitude = _bf(game, "Solitude", 0)
    _bf(game, "Grizzly Bears", 1)
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    EFFECT_REGISTRY.execute("Phelia, Exuberant Shepherd", EffectTiming.ATTACK,
                            game, phelia, 0, targets=[solitude.instance_id])
    GameRunner(_DB, rng=random.Random(0))._process_end_step_returns(game, 0)


def test_the_enter_auditor_sees_a_return_that_skipped_the_enter_owner(
        audit, monkeypatch):
    """Re-create the defect: the return calls the template-only queue."""
    from engine.game_state import GameState as GS
    monkeypatch.setattr(GS, "_handle_permanent_etb",
                        lambda self, card, controller, item=None:
                        self.trigger_etb(card, controller))
    _blink_and_return()
    assert _violations(audit, "603.6a/entered_permanent_ran_enter")


def test_the_enter_auditor_is_silent_when_the_enter_owner_ran(audit):
    _blink_and_return()
    assert not _violations(audit, "603.6a/entered_permanent_ran_enter")
