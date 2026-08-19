"""Energy counters persist across turns (CR 122.5).

Unlike mana, energy is a long-term resource: paying energy ({E}) does not
auto-drain at end of turn, and reset_turn_tracking() must not touch
energy_counters. This file pins that invariant so accidental additions to
the turn-reset list are caught immediately.

Fixture: PlayerState directly — no card-name dependency.
"""
from __future__ import annotations

from engine.player_state import PlayerState


def _make_player() -> PlayerState:
    p = PlayerState(player_idx=0, life=20)
    return p


def test_energy_counters_persist_through_reset_turn_tracking():
    """reset_turn_tracking must not clear energy_counters (CR 122.5)."""
    p = _make_player()
    p.energy_counters = 5
    p.reset_turn_tracking()
    assert p.energy_counters == 5, (
        "energy_counters must survive reset_turn_tracking — "
        "energy is a permanent resource, not a per-turn one"
    )


def test_spend_energy_decrements_correctly():
    """spend_energy(n) subtracts n from energy_counters and returns True."""
    p = _make_player()
    p.energy_counters = 4
    result = p.spend_energy(3)
    assert result is True
    assert p.energy_counters == 1


def test_spend_energy_fails_when_insufficient():
    """spend_energy fails and does not decrement when energy is insufficient."""
    p = _make_player()
    p.energy_counters = 2
    result = p.spend_energy(3)
    assert result is False
    assert p.energy_counters == 2, "failed spend must not mutate the counter"
