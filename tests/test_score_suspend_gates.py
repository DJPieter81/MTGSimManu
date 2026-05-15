"""Suspend EV gates must not return the 0.0 sentinel when the
"faster route" is illusory or when opp_clock pressure is short.

Diagnostic: docs/diagnostics/2026-05-10_living_end_5pct_root_cause.md
(`ai/ev_player.py::_score_suspend` Gates 1 + 2 return 0.0 in 89 of
96 enumerations across a 15-match Bo3 sweep).

Rule-phrased mechanic (no card names):

  Gate 1 (cascade-card-in-hand short-circuit) — must only fire
  when the cascade card in hand is *castable in current mana
  state*. A cascade card that is uncastable for color or mana
  reasons is not a "faster route"; it is a parallel plan, and
  suspend should score on its own merit.

  Gate 2 (opp_clock hard cutoff) — must replace the hard return
  with a probabilistic discount derived from the same clock
  primitive that `EVSnapshot.urgency_factor` uses, so that a
  suspend on a slow opp_clock still produces a non-zero gradient
  proportional to P(survive_to_resolution_turn).

  Regression: a true Gate 2 trigger (no cascade in hand AND
  opp_clock <= 1 i.e. lethal next turn) should still score 0.0
  because the math collapses — `urgency_factor` at opp_clock <= 1
  is 0.0 by construction.

Class size: every Modern suspend printing — Living End, Ancestral
Vision, Crashing Footfalls, Restore Balance, Wheel of Fate, Lotus
Bloom, Greater Gargadon, future printings. No card names; reads
SUSPEND keyword and oracle-driven cascade flag.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone, untapped=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        if untapped:
            # Shocklands / fastlands etc. ETB tapped under typical setup;
            # tests want a clean "mana available" pre-cast state.
            card.tapped = False
    game.players[controller].battlefield.append(card) \
        if zone == "battlefield" else \
        getattr(game.players[controller], zone).append(card)
    return card


def _setup_living_end_main_phase(game):
    """Bring the game to MAIN1 turn 2 for player 0 (Living End)."""
    game.players[0].deck_name = "Living End"
    game.players[1].deck_name = "Affinity"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 2
    game.players[0].lands_played_this_turn = 1


def _score_suspend(game, card):
    """Construct EVPlayer + snapshot and call _score_suspend directly."""
    player = EVPlayer(player_idx=0, deck_name="Living End",
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    opp = game.players[1]
    return player._score_suspend(card, snap, game, me, opp)


class TestGate1CastabilityCheck:
    """Gate 1 must only short-circuit when the cascade card in hand
    is castable in current mana state. A cascade card stuck behind
    colored-mana requirements does NOT block suspend — suspend is
    the parallel plan, not the strictly-slower plan.
    """

    def test_suspend_ev_positive_when_cascade_in_hand_is_uncastable_for_color(
            self, card_db):
        """BUG mana base (Watery Grave + Breeding Pool + 2 Blooming
        Marsh = UB / UG / BG / BG, no red sources). Demonic Dread
        in hand needs `{1}{B}{R}` — uncastable. Living End suspend
        cost `{2}{B}{B}` is payable from these 4 lands (2 black
        sources). The cascade route does not exist; suspend must
        score above 0.0 so that it competes with cycle / land plays
        on EV magnitude rather than tying with no-progress actions
        at the 0.0 sentinel."""
        game = GameState(rng=random.Random(0))
        # BUG mana, all untapped (manual ETB-untapped for the test).
        for nm in ("Watery Grave", "Breeding Pool",
                   "Blooming Marsh", "Blooming Marsh"):
            _add(game, card_db, nm, controller=0,
                 zone="battlefield", untapped=True)
        # Cascade card in hand but UNCASTABLE (no red source).
        dd = _add(game, card_db, "Demonic Dread", controller=0,
                  zone="hand")
        living_end = _add(game, card_db, "Living End", controller=0,
                          zone="hand")
        # A creature in graveyard so payoff > 0.
        _add(game, card_db, "Memnite", controller=0, zone="graveyard")
        _setup_living_end_main_phase(game)

        # Pre-conditions: Demonic Dread really is uncastable, Living
        # End really is suspend-payable.
        assert not game.can_cast(0, dd), (
            "BUG mana base must not be able to cast Demonic Dread "
            "(red required, no red sources). Test setup is wrong "
            "if this fires.")
        assert game.can_suspend(0, living_end), (
            "4 BUG lands include 2 black sources — Living End "
            "suspend cost {2}{B}{B} must be payable.")

        ev = _score_suspend(game, living_end)
        assert ev > 0.0, (
            f"Living End suspend scored {ev} with an uncastable "
            f"cascade card in hand. Gate 1 must check castability — "
            f"a cascade card that cannot be cast is not a 'faster "
            f"route' and must not short-circuit suspend's payoff "
            f"math. See docs/diagnostics/"
            f"2026-05-10_living_end_5pct_root_cause.md."
        )


class TestGate2ClockGradient:
    """Gate 2 must produce a continuous discount, not a hard 0.0.
    The discount must derive from the existing clock primitive
    (EVSnapshot.urgency_factor / opp_clock) so that suspend retains
    an EV gradient as opp_clock varies — and only fully collapses
    to ~0 when opp_clock is at lethal-next-turn levels.
    """

    def test_suspend_ev_positive_when_resolution_offset_exceeds_opp_clock_but_deck_has_no_alternative_pressure(
            self, card_db):
        """Living End suspend resolution_offset = 4 (3 counters + 1).
        Force opp_clock to be short by lowering my_life so the
        opponent's modest board pressures faster than suspend can
        resolve. The deck has no cascade card in hand and no other
        action — pre-fix Gate 2 returns 0.0, suspend ties with
        passing. Post-fix the discount via urgency_factor gives a
        non-zero payoff because there is still positive probability
        we resolve and stabilise."""
        game = GameState(rng=random.Random(0))
        # 4 swamps untapped — Living End suspend cost {2}{B}{B} payable.
        for _ in range(4):
            _add(game, card_db, "Swamp", controller=0,
                 zone="battlefield", untapped=True)
        # Goblin Guides on opp side: 4 * 2 = 8 power.
        # opp_clock = my_life / opp_power.
        for _ in range(4):
            _add(game, card_db, "Goblin Guide", controller=1,
                 zone="battlefield")
        living_end = _add(game, card_db, "Living End", controller=0,
                          zone="hand")
        # GY creatures so the payoff is non-trivial.
        for _ in range(3):
            _add(game, card_db, "Street Wraith", controller=0,
                 zone="graveyard")
        _setup_living_end_main_phase(game)
        # Lower my_life so opp_clock < resolution_offset (4).
        # my_life 8 / opp_power 8 = 1.0 → snap.opp_clock = 1.0.
        # That hits the regression case below — bump life so we're
        # in the "short but not lethal" band: my_life=16,
        # opp_power=8 → opp_clock = 2.0 < resolution_offset (4).
        game.players[0].life = 16

        snap = snapshot_from_game(game, 0)
        assert snap.opp_power >= 4, (
            f"Test setup needs opp_power large enough to make "
            f"opp_clock < 4. Got opp_power={snap.opp_power}.")
        # opp_clock must be in (1, 4) — Gate 2 fires (resolution_offset
        # > opp_clock) but the urgency_factor discount is non-zero.
        assert 1.0 < snap.opp_clock < 4.0, (
            f"Test needs opp_clock between 1 (lethal-next-turn) "
            f"and 4 (resolution_offset). Got opp_clock="
            f"{snap.opp_clock}.")

        ev = _score_suspend(game, living_end)
        assert ev > 0.0, (
            f"Living End suspend scored {ev} with opp_clock="
            f"{snap.opp_clock:.2f} (< resolution_offset 4). Gate 2 "
            f"must not return the 0.0 sentinel — it must apply a "
            f"continuous discount derived from urgency_factor so "
            f"a non-zero P(survive) preserves an EV gradient."
        )

    def test_suspend_ev_zero_when_no_cascade_and_opp_clock_lethal(
            self, card_db):
        """Regression: when opp_clock <= 1 (lethal next turn) the
        discount collapses to ~0 because urgency_factor(opp_clock=1)
        = 0.0 by construction. Suspend should not return *negative*
        EV either — the floor is 0.0 (no progress) less the
        mana-waste term; we only assert it is non-positive here
        because the gate has correctly recognised we are dying
        before the suspend ever resolves."""
        game = GameState(rng=random.Random(0))
        for _ in range(4):
            _add(game, card_db, "Swamp", controller=0,
                 zone="battlefield", untapped=True)
        # 8 power on opp side, my_life = 5 → opp_clock = ceil(5/8)
        # but the continuous my_life/opp_power = 0.625 → max(1, ...)
        # = 1.0 → urgency_factor = 0.0.
        for _ in range(4):
            _add(game, card_db, "Goblin Guide", controller=1,
                 zone="battlefield")
        living_end = _add(game, card_db, "Living End", controller=0,
                          zone="hand")
        # No cascade card in hand. GY creatures so payoff math is
        # non-trivial — we want to prove the gate collapses payoff
        # because of urgency, not because payoff is zero.
        for _ in range(3):
            _add(game, card_db, "Street Wraith", controller=0,
                 zone="graveyard")
        _setup_living_end_main_phase(game)
        game.players[0].life = 5

        snap = snapshot_from_game(game, 0)
        assert snap.opp_clock <= 1.0 + 1e-9, (
            f"Regression test setup must have opp_clock at the "
            f"lethal-next-turn level. Got opp_clock="
            f"{snap.opp_clock}.")

        ev = _score_suspend(game, living_end)
        # Hard upper bound: ev must be ~0 (regression for the
        # diagnostic's "Gate 2 fires when opp_clock=1" branch).
        # The waste term may push slightly negative, but the
        # payoff×urgency_factor term is 0 by construction.
        assert ev <= 0.0 + 1e-6, (
            f"Suspend scored {ev} with opp_clock={snap.opp_clock}, "
            f"i.e. lethal next turn. The clock-derived discount "
            f"must collapse the payoff to ~0, ensuring we don't "
            f"waste 4 mana on a suspend that will never resolve."
        )
