"""Crediting completion of an unbounded mana engine requires a reachable sink.

Deep-audit Phase 2, ramp panel Finding 1: a tutor that completes a self-untap
infinite-mana loop (Devoted Druid + Vizier) is credited LOOP_SHORTCUT_MANA (~80
mana of value) with no check that any MANA SINK exists to convert that mana to a
win — so Creatures Toolbox fetched the enabler, made 81 mana, and passed with no
payoff on board (toolbox_zoo.txt:525-555). The credit is dead without a sink.

`ai.combo_calc.unbounded_mana_sink_reachable` gates the credit: a sink is a
payoff whose output scales with mana — an X-cost damage spell/permanent, a
mass-pump overrun, or a scaling-token finisher — reachable in hand, on the
battlefield, or in the library. Card names here are fixture carriers only; the
predicate reads typed CardTemplate fields.
"""
from __future__ import annotations

import random

from ai.combo_calc import unbounded_mana_sink_reachable
from engine.cards import CardInstance
from engine.game_state import GameState


class _Me:
    """Minimal duck-typed player: the predicate reads only the three zones."""
    def __init__(self, hand=(), battlefield=(), library=()):
        self.hand = list(hand)
        self.battlefield = list(battlefield)
        self.library = list(library)


def _card(game, card_db, name, zone="library"):
    c = CardInstance(template=card_db.get_card(name), owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    return c


def test_no_sink_reachable_is_false(card_db):
    g = GameState(rng=random.Random(0))
    me = _Me(battlefield=[_card(g, card_db, "Devoted Druid", "battlefield")],
             library=[_card(g, card_db, "Vizier of Remedies"),
                      _card(g, card_db, "Eternal Witness")])
    assert unbounded_mana_sink_reachable(me) is False


def test_mass_pump_overrun_in_library_is_a_sink(card_db):
    g = GameState(rng=random.Random(0))
    me = _Me(library=[_card(g, card_db, "Craterhoof Behemoth")])  # team_pump_data
    assert unbounded_mana_sink_reachable(me) is True


def test_x_cost_damage_in_hand_is_a_sink(card_db):
    g = GameState(rng=random.Random(0))
    me = _Me(hand=[_card(g, card_db, "Walking Ballista", "hand")])  # X-dmg
    assert unbounded_mana_sink_reachable(me) is True


def test_fixed_burn_is_not_a_sink(card_db):
    # Lightning Bolt deals a fixed 3 — more mana does not scale it, so
    # completing an infinite-mana engine converts to nothing through it.
    g = GameState(rng=random.Random(0))
    me = _Me(hand=[_card(g, card_db, "Lightning Bolt", "hand")])
    assert unbounded_mana_sink_reachable(me) is False
