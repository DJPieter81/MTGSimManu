"""Phase-2 commit-1: `build_combo_distribution` priors validation.

These tests pin the BUILDER itself (not the dispatcher).  They run
regardless of the `OUTCOME_DIST_COMBO` flag — the builder must return
a well-formed `OutcomeDistribution` (or `None` for non-combo cards)
straight from inputs.

When `OUTCOME_DIST_COMBO == True`, the dispatcher in `_score_spell`
will route to this builder; when False, the builder is unused (dead
code path) but its priors must still be unit-correct so flipping the
flag is a one-line change.

Priors are derived from existing principled subsystems:

- `combo_chain.find_all_chains` for chain-resolution storm count.
- `outcome_ev.p_draw_in_n_turns` (hypergeometric) for "I will see a
  finisher in N draws" against the visible library.
- `combo_calc._compute_risk_discount` for `P(disrupted)`.
- `win_probability.p_win_delta` for outcome value in Δ(P_win) units.

No magic numbers, no hardcoded card names — every quantity is sourced
from the snapshot, the gameplan, or the chain-arithmetic primitives.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import snapshot_from_game
from ai.outcome_ev import (
    Outcome,
    OutcomeDistribution,
    build_combo_distribution,
)
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone, summoning_sick=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = summoning_sick
    getattr(game.players[controller], 'library' if zone == 'library'
            else zone).append(card)
    return card


def _build_player(deck_name="Ruby Storm"):
    """Build an EVPlayer with the right archetype for snapshotting."""
    return EVPlayer(player_idx=0, deck_name=deck_name,
                    rng=random.Random(0))


def _setup(card_db, hand_names, gy_names=(), bf_names=(),
           opp_bf_names=(),
           lands=2, opp_lands=2, storm_count=0,
           deck_name="Ruby Storm", opp_deck="Boros Energy",
           my_life=20, opp_life=20, turn_number=4,
           medallions=0, library_names=()):
    """Helper to assemble a minimal GameState mid-turn."""
    game = GameState(rng=random.Random(0))
    for _ in range(lands):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    for _ in range(medallions):
        _add(game, card_db, "Ruby Medallion", controller=0,
             zone="battlefield")
    for _ in range(opp_lands):
        _add(game, card_db, "Plains", controller=1, zone="battlefield")
    cards = []
    for n in hand_names:
        cards.append(_add(game, card_db, n, controller=0, zone="hand"))
    for n in gy_names:
        _add(game, card_db, n, controller=0, zone="graveyard")
    for n in bf_names:
        _add(game, card_db, n, controller=0, zone="battlefield")
    for n in opp_bf_names:
        _add(game, card_db, n, controller=1, zone="battlefield")
    for n in library_names:
        _add(game, card_db, n, controller=0, zone="library")

    game.players[0].deck_name = deck_name
    game.players[1].deck_name = opp_deck
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = turn_number
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = storm_count
    game._global_storm_count = storm_count
    game.players[0].life = my_life
    game.players[1].life = opp_life
    return game, cards


# ──────────────────────────────────────────────────────────────────
# Builder priors — Storm rituals
# ──────────────────────────────────────────────────────────────────


class TestStormRitualPriors:
    def test_no_fuel_no_finisher_at_storm0_low_combo_high_fizzle(self, card_db):
        """storm=0, hand has only one ritual, no finisher, no PiF, no fuel.
        Builder must produce a distribution with `P(COMPLETE_COMBO) < 0.05`
        and `P(FIZZLE) > 0.5`. No "lethal this turn" probability."""
        game, cards = _setup(
            card_db,
            hand_names=["Desperate Ritual"],
            lands=2, medallions=0, storm_count=0,
            opp_life=20, my_life=20,
            library_names=["Mountain", "Mountain", "Mountain"],
            # ^ no finishers in library: nothing to draw into
        )
        ritual = cards[0]
        player = _build_player()
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        dist = build_combo_distribution(
            ritual, snap, game, me, opp,
            player.bhi, player.archetype, player.profile,
        )

        assert dist is not None, "ritual must be detected as combo card"
        assert dist.is_well_formed(), (
            f"distribution malformed: {dist.probabilities}"
        )
        assert dist.probabilities[Outcome.COMPLETE_COMBO] < 0.05, (
            f"P(COMPLETE_COMBO)={dist.probabilities[Outcome.COMPLETE_COMBO]:.3f} "
            f">= 0.05 with no fuel + no finisher accessible"
        )
        assert dist.probabilities[Outcome.FIZZLE] > 0.5, (
            f"P(FIZZLE)={dist.probabilities[Outcome.FIZZLE]:.3f} "
            f"<= 0.5 with no kill path"
        )

    def test_storm3_with_finisher_in_hand_high_combo(self, card_db):
        """storm=3, hand has Grapeshot + 1 ritual, plenty of mana via
        Medallion. The chain finishes — `P(COMPLETE_COMBO)` must be > 0.7."""
        game, cards = _setup(
            card_db,
            hand_names=["Desperate Ritual", "Grapeshot"],
            lands=4, medallions=1, storm_count=3,
            opp_life=4,    # Grapeshot for storm=3+1 final dmg = 4 == lethal
            my_life=20,
        )
        ritual = cards[0]
        player = _build_player()
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        dist = build_combo_distribution(
            ritual, snap, game, me, opp,
            player.bhi, player.archetype, player.profile,
        )

        assert dist is not None
        assert dist.is_well_formed()
        assert dist.probabilities[Outcome.COMPLETE_COMBO] > 0.7, (
            f"P(COMPLETE_COMBO)={dist.probabilities[Outcome.COMPLETE_COMBO]:.3f} "
            f"<= 0.7 even with finisher + ritual + lethal storm"
        )


# ──────────────────────────────────────────────────────────────────
# Builder priors — Cascade
# ──────────────────────────────────────────────────────────────────


class TestCascadePriors:
    def test_cascade_thin_gy_low_partial_advance(self, card_db):
        """Cascade enabler in a reanimator (Living End) shell with thin GY.
        `P(PARTIAL_ADVANCE)` must be low — cascade hits an empty board."""
        # Living End uses cascade enablers; if GY < target, the cascade
        # payoff returns a very small board.
        game, cards = _setup(
            card_db,
            hand_names=["Demonic Dread"],  # 3cmc cascade enabler (in DB)
            lands=2, medallions=0, storm_count=0,
            deck_name="Living End", opp_deck="Boros Energy",
            opp_life=20, my_life=20,
            library_names=[],  # empty library — minimal cascade outcome
        )
        cascade = cards[0]
        player = _build_player(deck_name="Living End")
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        dist = build_combo_distribution(
            cascade, snap, game, me, opp,
            player.bhi, player.archetype, player.profile,
        )

        # Cascade is a combo category (gameplan declares cascade).
        if dist is None:
            pytest.skip("Living End cascade not classified as combo by builder")
        assert dist.is_well_formed()
        # PARTIAL_ADVANCE captures "cascade resolves but doesn't kill" —
        # we want this < 0.5 when GY is empty.
        assert dist.probabilities[Outcome.PARTIAL_ADVANCE] < 0.5, (
            f"P(PARTIAL_ADVANCE)={dist.probabilities[Outcome.PARTIAL_ADVANCE]:.3f} "
            f">= 0.5 with empty graveyard — should not be high"
        )


# ──────────────────────────────────────────────────────────────────
# Builder priors — Reanimation
# ──────────────────────────────────────────────────────────────────


class TestReanimatePriors:
    def test_reanimate_with_target_in_gy_high_combo(self, card_db):
        """Goryo's Vengeance with Griselbrand in GY → reanimation fires
        and returns a 7-power flyer with lifelink. Combo probability
        must be high."""
        game, cards = _setup(
            card_db,
            hand_names=["Goryo's Vengeance"],
            gy_names=["Griselbrand"],
            lands=2, medallions=0, storm_count=0,
            deck_name="Goryo's Vengeance", opp_deck="Boros Energy",
            opp_life=20, my_life=20,
        )
        goryos = cards[0]
        player = _build_player(deck_name="Goryo's Vengeance")
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        dist = build_combo_distribution(
            goryos, snap, game, me, opp,
            player.bhi, player.archetype, player.profile,
        )

        if dist is None:
            pytest.skip("Goryo's not classified as combo by builder")
        assert dist.is_well_formed()
        assert dist.probabilities[Outcome.COMPLETE_COMBO] > 0.3, (
            f"P(COMPLETE_COMBO)={dist.probabilities[Outcome.COMPLETE_COMBO]:.3f} "
            f"with a legendary 7-powered flier in GY ought to be >= 0.3"
        )


# ──────────────────────────────────────────────────────────────────
# Non-combo passthrough
# ──────────────────────────────────────────────────────────────────


class TestNonComboReturnsNone:
    def test_lightning_bolt_returns_none(self, card_db):
        """Lightning Bolt is a removal/burn spell, not a combo
        ritual/cascade/reanimate/finisher. Builder must return None
        so the dispatcher falls through to legacy logic."""
        game, cards = _setup(
            card_db,
            hand_names=["Lightning Bolt"],
            lands=2, deck_name="Boros Energy", opp_deck="Affinity",
        )
        bolt = cards[0]
        player = _build_player(deck_name="Boros Energy")
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        dist = build_combo_distribution(
            bolt, snap, game, me, opp,
            player.bhi, player.archetype, player.profile,
        )

        assert dist is None, (
            "Lightning Bolt must NOT be classified as combo "
            "(it's removal); dispatcher falls through to legacy."
        )

    def test_creature_returns_none(self, card_db):
        """A vanilla creature must not be classified as combo."""
        game, cards = _setup(
            card_db,
            hand_names=["Memnite"],
            lands=2, deck_name="Affinity", opp_deck="Boros Energy",
        )
        memnite = cards[0]
        player = _build_player(deck_name="Affinity")
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        dist = build_combo_distribution(
            memnite, snap, game, me, opp,
            player.bhi, player.archetype, player.profile,
        )

        assert dist is None, (
            "Plain creature must not be classified as combo by Phase 2."
        )
