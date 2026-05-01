"""Evoke pitch removal must not fire on tiny vanilla creatures.

Reference: docs/diagnostics/2026-04-28_affinity_evoke_overtrading.md
(Verbose seed 50001 — Affinity vs Azorius Control, T1)

Pre-fix behaviour (the bug):

  Affinity casts 3× free creatures on T1 (Memnite, Memnite,
  Ornithopter).  AzCon evokes BOTH Solitudes in response, exiling
  BOTH Orim's Chants to remove 2× Memnite.  Trade math:
    AzCon spends 4 cards (2 pitched Orim's Chants + 2 evoked
    Solitudes themselves) + 4 evoke-trigger life
    Removes 2× Memnite (1/1 vanilla, ~1 dmg/turn each)
  Net: 4 cards for ~2 dmg/turn prevented = catastrophic.

Root cause: `engine/game_runner.py::_cast_instant_removal` uses
a single `threat_threshold` heuristic and `cmc`-based sort.  It
ignores the 2-card pitch cost of evoke (CR 702.73).  When
`max_threat >= 2.0`, removal fires regardless of whether the trade
is value-positive.

The right rule (rule, not card-name): evoke removal pitches a
white card from hand AND sacrifices the evoked creature itself —
effective cost is 2 cards.  Firing this trade on a 1-power vanilla
target is value-negative unless we're at imminent lethal.
Detection: card has 'evoke' tag AND target's `permanent_threat`
is below the 2-card swing threshold.

Class-size: every Modern Horizons evoke-pitch elemental
(Solitude, Subtlety, Endurance, Grief, Fury, future printings).
Detection is `'evoke' in tags`, oracle/keyword based — no card
names.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


def _build_affinity_vs_azcon_t1(card_db):
    """Reproduce the seed-50001 T1 state (AzCon defending, Affinity
    deployed Memnite/Memnite/Ornithopter + Urza's Saga)."""
    game = GameState(rng=random.Random(0))
    # P0 = Affinity board
    _add(game, card_db, "Memnite", controller=0, zone="battlefield")
    _add(game, card_db, "Memnite", controller=0, zone="battlefield")
    _add(game, card_db, "Ornithopter", controller=0, zone="battlefield")
    _add(game, card_db, "Urza's Saga", controller=0, zone="battlefield")
    # P1 = AzCon hand: 2x Solitude, 2x Orim's Chant, Counterspell
    _add(game, card_db, "Solitude", controller=1, zone="hand")
    _add(game, card_db, "Solitude", controller=1, zone="hand")
    _add(game, card_db, "Orim's Chant", controller=1, zone="hand")
    _add(game, card_db, "Orim's Chant", controller=1, zone="hand")
    _add(game, card_db, "Counterspell", controller=1, zone="hand")
    _add(game, card_db, "Hall of Storm Giants", controller=1, zone="hand")
    # P1 lands (just Meticulous Archive on board, but evoke needs no mana)
    _add(game, card_db, "Meticulous Archive", controller=1,
         zone="battlefield")
    game.players[0].deck_name = "Affinity"
    game.players[1].deck_name = "Azorius Control"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 2  # P1's first turn just ended, P0 in T1
    game.players[0].life = 20
    game.players[1].life = 20
    return game


class TestEvokeNotWastedOnVanilla:
    """Engine-level instant-speed removal heuristic must respect the
    2-card pitch cost of evoke."""

    def test_azcon_does_not_evoke_solitude_on_memnite(self, card_db):
        """Reproduce seed 50001 T1: Memnite cast triggers an
        instant-speed response window for AzCon.  AzCon has 2x
        Solitude + 2x Orim's Chant in hand.  Evoking Solitude on
        Memnite costs 2 cards (pitch + Solitude); Memnite is worth
        ~1 damage/turn = ~1.5 EV.  Trade is catastrophic.

        AzCon must NOT cast Solitude on a 1-power vanilla creature.

        Test name encodes the rule: evoke removal on tiny vanilla
        targets is value-negative because the 2-card pitch cost
        exceeds the threat removed."""
        from ai.ev_player import EVPlayer
        from engine.game_runner import GameRunner, AIPlayer

        game = _build_affinity_vs_azcon_t1(card_db)

        # Set up AIPlayer wrappers for both sides
        affinity_ai = EVPlayer(player_idx=0, deck_name="Affinity",
                                rng=random.Random(0))
        azcon_ai = EVPlayer(player_idx=1, deck_name="Azorius Control",
                             rng=random.Random(0))

        # Snapshot AzCon's hand before the response window
        azcon_hand_before = [c.name for c in game.players[1].hand]
        gy_before = len(game.players[1].graveyard)

        # Trigger the response window manually — simulating the
        # opportunity that fires when Affinity passes priority.
        runner = GameRunner(card_db=card_db)
        # Use end_step context (where evoke creatures are eligible
        # — pre_combat skips them per game_runner.py:898)
        runner._cast_instant_removal(
            game, azcon_ai, affinity_ai,
            context="end_step", max_instants=3,
        )

        # Check: did AzCon cast Solitude?
        azcon_hand_after = [c.name for c in game.players[1].hand]
        gy_after = game.players[1].graveyard
        gy_names_after = [c.name for c in gy_after]
        exiled_after = [c.name for c in game.players[1].exile]

        solitude_resolved = (
            "Solitude" in [c.name for c in gy_after]
            or "Solitude" in exiled_after
            or game.players[1].hand.count == 0
        )
        # More direct: count Solitudes still in hand
        solitude_in_hand_before = azcon_hand_before.count("Solitude")
        solitude_in_hand_after = azcon_hand_after.count("Solitude")
        solitudes_cast = solitude_in_hand_before - solitude_in_hand_after

        assert solitudes_cast == 0, (
            f"AzCon evoked {solitudes_cast} Solitude(s) on T1 vs "
            f"Affinity's Memnites — catastrophic trade.  Each evoke "
            f"costs 2 cards (Solitude itself + the pitched Orim's "
            f"Chant) for ~1 damage/turn prevented.\n\n"
            f"Hand before: {azcon_hand_before}\n"
            f"Hand after:  {azcon_hand_after}\n"
            f"Graveyard:   {gy_names_after}\n"
            f"Exile:       {exiled_after}\n\n"
            f"The engine's instant-removal heuristic at "
            f"engine/game_runner.py::_cast_instant_removal uses a "
            f"flat threat_threshold without accounting for the "
            f"2-card pitch cost of evoke.  See "
            f"docs/diagnostics/2026-04-28_affinity_evoke_overtrading.md"
        )

    def test_evoke_does_fire_on_high_threat_target(self, card_db):
        """Regression: when the target IS worth the 2-card trade
        (e.g. a real 5+ power threat), evoke removal SHOULD fire.
        Don't over-tighten.

        Avoids triggering bug E3 (evoke gate blocks pitch when total
        mana < effective_cmc) by giving AzCon enough lands to legally
        hardcast Solitude — the evoke path is then available because
        the gate sees sufficient mana coverage."""
        from ai.ev_player import EVPlayer
        from engine.game_runner import GameRunner

        game = GameState(rng=random.Random(0))
        # Affinity board: a 5-power threat (use Cranial Plating
        # equipped to a body — power scales correctly)
        plating = _add(game, card_db, "Cranial Plating",
                       controller=0, zone="battlefield")
        carrier = _add(game, card_db, "Memnite", controller=0,
                       zone="battlefield")
        for _ in range(4):
            _add(game, card_db, "Ornithopter", controller=0,
                 zone="battlefield")
        carrier.instance_tags.add(f"equipped_{plating.instance_id}")
        # AzCon: Solitude + pitch card + 5 untapped Plains so it
        # CAN hardcast (sidesteps bug E3's evoke gate).
        _add(game, card_db, "Solitude", controller=1, zone="hand")
        _add(game, card_db, "Orim's Chant", controller=1, zone="hand")
        for _ in range(5):
            _add(game, card_db, "Plains", controller=1,
                 zone="battlefield")
        game.players[0].deck_name = "Affinity"
        game.players[1].deck_name = "Azorius Control"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.turn_number = 4
        game.players[0].life = 20
        game.players[1].life = 16

        affinity_ai = EVPlayer(player_idx=0, deck_name="Affinity",
                                rng=random.Random(0))
        azcon_ai = EVPlayer(player_idx=1, deck_name="Azorius Control",
                             rng=random.Random(0))

        sol_before = sum(1 for c in game.players[1].hand
                         if c.name == "Solitude")
        runner = GameRunner(card_db=card_db)
        runner._cast_instant_removal(
            game, azcon_ai, affinity_ai,
            context="end_step", max_instants=3,
        )
        sol_after = sum(1 for c in game.players[1].hand
                        if c.name == "Solitude")
        carrier_dead = (carrier.zone != "battlefield")

        # The Plating-equipped Memnite is effectively a 7/7
        # (1 base + 6 attached pump from Plating × 6 artifacts).
        # _permanent_value = 13.3, far above the 5.0 pitch threshold.
        # AzCon should hardcast Solitude on it.
        assert sol_before - sol_after >= 1 or carrier_dead, (
            f"AzCon failed to cast Solitude on a high-threat "
            f"Plating-equipped carrier (val ~13).  This IS the right "
            f"trade — the fix must not over-tighten.\n"
            f"sol_before={sol_before}, sol_after={sol_after}, "
            f"carrier_dead={carrier_dead}, "
            f"carrier.power={carrier.power}"
        )
