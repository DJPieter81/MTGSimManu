"""A player gets the counters an instruction gives them (CR 122.1, 107.14,
122.1f).

"When this creature enters, you get {E}{E}{E}": as the trigger resolves,
its controller gets three energy counters ({E} is an energy counter a
player has, CR 107.14). "At the beginning of your upkeep, you get
{E}{E}{E}": at that step, not as the permanent enters. "Each player gets
a poison counter": each player (CR 122.1f). An activated ability's
counters come when it is activated and resolves, never on entry.

The defect: no executor gave a player counters.
- The legacy entry path gave the first "you get {E}" of a card's text as
  the permanent entered, whatever ability printed it (an activated
  ability, an upkeep trigger, one mode of a modal trigger), and skipped
  every "When ~ enters, you get {E}" that is an enter trigger.
- A cast-trigger regex read Cyclops Superconductor's prowess as a cast
  trigger and its enter energy as the trigger's: three energy for every
  noncreature spell.
- Nothing gave a poison counter.

Class: 114 typed player-counter instructions in the pool (94 energy, 20
poison): 61 enter, 18 spell, 6 activated, 5 attack, 3 combat damage, 2
step and others. Registered: Static Prison (Boros Energy x1), whose enter
trigger exiles and gives energy (with unit UP); Guide of Souls' enter
watcher keeps its legacy pass (no carrier for another creature
entering); Galvanic Discharge keeps its handler. Card names are fixture
carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game(phase=Phase.MAIN1):
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = phase
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    c = CardInstance(template=tmpl, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _cast(game, card_db, name, lands, idx=0):
    for land in lands:
        _put(game, card_db, land, "battlefield", idx)
    card = _put(game, card_db, name, "hand", idx)
    assert game.cast_spell(idx, card, targets=[])
    while game.stack.items:
        game.resolve_stack()
    return card


# ── The rule ───────────────────────────────────────────────────────────

def test_an_enter_trigger_gives_its_controller_the_counters_it_names(card_db):
    # "When this creature enters, you get {E}{E}{E}."
    game = _game()
    _cast(game, card_db, "Aether Theorist", ["Island", "Island"])
    assert game.players[0].energy_counters == 3


def test_a_step_trigger_gives_them_at_its_step_not_on_entry(card_db):
    # "At the beginning of your upkeep, you get {E}{E}{E}."
    from engine.turn_clock import Clock, ClockEvent, emit
    game = _game()
    _cast(game, card_db, "Aethersquall Ancient", ["Island"] * 7)
    assert game.players[0].energy_counters == 0
    game.current_phase = Phase.UPKEEP
    emit(game, ClockEvent(Clock.STEP_BEGINS, 0, step="upkeep"))
    assert game.players[0].energy_counters == 3


def test_an_activated_abilitys_counters_are_not_given_on_entry(card_db):
    # "{T}: You get {E}."
    game = _game()
    _cast(game, card_db, "Consulate Turret", ["Wastes"] * 3)
    assert game.players[0].energy_counters == 0


def test_a_cast_trigger_that_gives_no_counters_gives_none(card_db):
    """Prowess is no energy grant: a noncreature spell cast under it gives
    its controller no energy."""
    game = _game()
    _put(game, card_db, "Cyclops Superconductor", "battlefield", 0)
    before = game.players[0].energy_counters
    _cast(game, card_db, "Lightning Bolt", ["Mountain"])
    assert game.players[0].energy_counters == before


def test_each_player_named_gets_a_poison_counter(card_db):
    # "When this creature enters, each player gets a poison counter."
    game = _game()
    _cast(game, card_db, "Ichor Rats", ["Swamp"] * 3)
    assert [p.poison_counters for p in game.players] == [1, 1]


def test_an_attack_trigger_gives_its_counters_once(card_db):
    # "Whenever this creature attacks, you get {E}{E}."
    from engine.combat_manager import CombatManager
    game = _game(Phase.DECLARE_ATTACKERS)
    miner = _put(game, card_db, "Aethergeode Miner", "battlefield", 0)
    CombatManager().declare_attackers(game, [miner], 0)
    assert game.players[0].energy_counters == 2


def test_an_enter_trigger_with_an_exile_gives_its_counters_once(card_db):
    """Static Prison: the exile and the energy of one enter trigger, the
    energy given once."""
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast(game, card_db, "Static Prison", ["Plains"] * 3)
    assert bears.zone == "exile"
    assert game.players[0].energy_counters == 2


# ── Auditor (CR 122.1) ─────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f["rule"] for f in audit.drain() if f["kind"] == "violation"]


def test_the_audit_records_counters_not_given(card_db, audit, monkeypatch):
    from engine.permanent_effects import PermanentEffects
    monkeypatch.setattr(PermanentEffects, "give_player_counters",   # defect
                        staticmethod(lambda *a, **k: None), raising=False)
    game = _game()
    _cast(game, card_db, "Aether Theorist", ["Island", "Island"])
    assert "122.1/player_counters" in _violations(audit)


def test_the_audit_is_silent_when_the_counters_are_given(card_db, audit):
    game = _game()
    _cast(game, card_db, "Aether Theorist", ["Island", "Island"])
    assert "122.1/player_counters" not in _violations(audit)
