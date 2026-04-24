"""Regression tests for LE-E2 — suspend counter tracking + upkeep resolution.

Diagnostic: docs/diagnostics/2026-04-24_living_end_consolidated_findings.md

LE-E2 context: prior to this fix, the engine detected cards with CMC 0 + the
SUSPEND keyword and blocked them from being hand-cast (cast_manager.py:91-93),
but never implemented the suspend mechanic itself:
  1. No way to pay the suspend cost and exile the card with time counters.
  2. No upkeep trigger decrementing the counters.
  3. No resolution path when the last counter is removed (cast for free).

This gap made Violent Outburst (suspend-cascade) completely non-functional
for Living End. Rift Bolt (suspend 1—{R}, CMC 3) is used as the test vehicle
because Violent Outburst is not present in ModernAtomic.json; the mechanic
is generic and not card-specific.

Tests:
- can_suspend / suspend_card flow (exile, counter placement).
- Upkeep decrement moves the counters down; when zero, spell casts free.
- Non-suspend cards are completely unaffected.
"""
import random
import pytest

from engine.cards import CardInstance, Keyword
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from engine.callbacks import DefaultCallbacks


@pytest.fixture(scope="module")
def db():
    return CardDatabase()


def _fresh_game(db):
    game = GameState(rng=random.Random(42), callbacks=DefaultCallbacks())
    # Minimal setup: just two players, some instance IDs available.
    # No decks — we construct CardInstances manually.
    return game


def _make_instance(game, template, player_idx, zone="hand"):
    card = CardInstance(
        template=template,
        owner=player_idx, controller=player_idx,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    return card


def _add_mountain(game, player_idx, db):
    """Drop a basic Mountain onto the battlefield for mana."""
    mt = db.get_card("Mountain")
    land = _make_instance(game, mt, player_idx, zone="battlefield")
    game.players[player_idx].battlefield.append(land)
    return land


def _add_island(game, player_idx, db):
    """Drop a basic Island onto the battlefield for mana."""
    island = db.get_card("Island")
    land = _make_instance(game, island, 0, zone="battlefield")
    game.players[player_idx].battlefield.append(land)
    return land


class TestSuspendMechanic:
    """LE-E2: suspend a card, watch counters decrement, see it cast free."""

    def test_can_suspend_requires_suspend_keyword(self, db):
        """Non-suspend cards must not be suspendable (sanity check)."""
        game = _fresh_game(db)
        bolt = db.get_card("Lightning Bolt")  # no SUSPEND keyword
        inst = _make_instance(game, bolt, 0, zone="hand")
        game.players[0].hand.append(inst)
        _add_mountain(game, 0, db)
        assert not game.can_suspend(0, inst)

    def test_suspend_rift_bolt_exiles_with_one_counter(self, db):
        """Paying the suspend cost must move the card to exile with N counters.

        Rift Bolt: "Suspend 1—{R}" means N=1 time counter.
        """
        game = _fresh_game(db)
        bolt = db.get_card("Rift Bolt")
        assert Keyword.SUSPEND in bolt.keywords
        inst = _make_instance(game, bolt, 0, zone="hand")
        game.players[0].hand.append(inst)
        _add_mountain(game, 0, db)

        assert game.can_suspend(0, inst), "Should be suspend-castable with R available"

        ok = game.suspend_card(0, inst)
        assert ok, "suspend_card should succeed"

        assert inst.zone == "exile"
        assert inst in game.players[0].exile
        assert inst not in game.players[0].hand
        assert inst.suspended is True
        assert inst.suspend_counters == 1, (
            f"Expected 1 counter (Suspend 1), got {inst.suspend_counters}")

    def test_upkeep_removes_counter_and_casts_when_zero(self, db):
        """At each of the caster's upkeeps, remove a counter. When the
        last counter is removed, the spell is cast for free.

        Rift Bolt (Suspend 1) → after 1 upkeep, should resolve and deal 3
        damage to the target (here, the opponent).
        """
        game = _fresh_game(db)
        bolt = db.get_card("Rift Bolt")
        inst = _make_instance(game, bolt, 0, zone="hand")
        game.players[0].hand.append(inst)
        _add_mountain(game, 0, db)

        # Suspend it
        assert game.suspend_card(0, inst)
        assert inst.suspend_counters == 1

        # Simulate one upkeep of the suspending player
        opp_life_before = game.players[1].life
        game.active_player = 0  # player 0's upkeep
        game.current_phase = Phase.UPKEEP
        game.tick_suspend_upkeep(0)

        # Counter decremented; last removed → spell resolves (cast for free)
        # After resolution, Rift Bolt should be in graveyard, not exile.
        assert inst.suspend_counters == 0
        assert inst.suspended is False
        assert inst.zone == "graveyard", (
            f"Rift Bolt should resolve to graveyard, zone={inst.zone}")
        # Deals 3 to a target. No explicit target was picked; the engine
        # default for "any target" burn is opponent — so opp life drops.
        assert game.players[1].life == opp_life_before - 3, (
            f"Expected opp life {opp_life_before-3}, got {game.players[1].life}")

    def test_upkeep_multi_counter_decrements_only_by_one(self, db):
        """Ancestral Vision is Suspend 4. After one upkeep, 3 counters
        should remain and the card must NOT yet cast.
        """
        game = _fresh_game(db)
        av = db.get_card("Ancestral Vision")
        assert Keyword.SUSPEND in av.keywords
        inst = _make_instance(game, av, 0, zone="hand")
        game.players[0].hand.append(inst)
        # Need {U} for suspend cost
        land = _add_island(game, 0, db)

        assert game.can_suspend(0, inst)
        assert game.suspend_card(0, inst)
        assert inst.suspend_counters == 4

        # Untap land so the next-upkeep logic doesn't get tangled.
        land.tapped = False
        game.active_player = 0
        game.current_phase = Phase.UPKEEP
        game.tick_suspend_upkeep(0)

        # One counter removed → 3 remain, still in exile, still suspended.
        assert inst.suspend_counters == 3
        assert inst.suspended is True
        assert inst.zone == "exile"

    def test_upkeep_only_ticks_suspended_player(self, db):
        """Opponent's upkeep must NOT decrement MY suspended cards."""
        game = _fresh_game(db)
        bolt = db.get_card("Rift Bolt")
        inst = _make_instance(game, bolt, 0, zone="hand")
        game.players[0].hand.append(inst)
        _add_mountain(game, 0, db)
        assert game.suspend_card(0, inst)
        assert inst.suspend_counters == 1

        # Simulate opponent (player 1) upkeep: my counter stays at 1.
        game.active_player = 1
        game.current_phase = Phase.UPKEEP
        game.tick_suspend_upkeep(1)

        assert inst.suspend_counters == 1, (
            "Opponent's upkeep must not tick MY suspend counters")
        assert inst.zone == "exile"
        assert inst.suspended is True


class TestSuspendRegression:
    """LE-E2: non-suspend flows must be unchanged."""

    def test_lightning_bolt_cast_normally_unaffected(self, db):
        """A card without SUSPEND, cast from hand normally, proceeds as before.

        Regression guard: the suspend patch must not touch the regular cast
        path (no new zone checks, no counter mutation on non-suspend cards).
        """
        game = _fresh_game(db)
        bolt = db.get_card("Lightning Bolt")
        inst = _make_instance(game, bolt, 0, zone="hand")
        game.players[0].hand.append(inst)
        _add_mountain(game, 0, db)
        game.active_player = 0
        game.current_phase = Phase.MAIN1

        # New CardInstance field default: suspend fields must be initialized
        assert inst.suspend_counters == 0
        assert inst.suspended is False

        # Cast Lightning Bolt normally
        ok = game.cast_spell(0, inst)
        assert ok, "Lightning Bolt should cast normally"
        # Resolve stack
        while not game.stack.is_empty:
            game.resolve_stack()
        assert inst.zone == "graveyard"
        assert inst.suspended is False  # still unaffected
        assert inst.suspend_counters == 0
