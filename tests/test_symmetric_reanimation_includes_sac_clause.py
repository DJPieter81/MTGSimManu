"""Symmetric-reanimation sacrifice-clause projection.

Rule under test
---------------
A symmetric mass-reanimation spell whose oracle text contains BOTH
"sacrifices all creatures they control" AND "returns ... creature cards
from ... graveyard" must project both clauses in the EV pipeline:

  1. SACRIFICE: opp_power / my_power drop to zero (modulo reanimated)
     because the on-board creatures are sacrificed.
  2. REANIMATE: graveyard creatures return to the battlefield.

Mechanic, not card
------------------
Identified in
`docs/diagnostics/2026-05-10_affinity_85pct_opponent_side_root_cause.md`
(Component B). `_project_spell::is_symmetric_reanimation` correctly adds
the reanimated creatures to my_power / opp_power but does NOT zero out
the existing on-board creatures that the spell sacrifices. When the
opponent has a wide board (Affinity at T4: 2 Construct Tokens,
24 power), the projection misses the entire `opp_power -= 24` swing,
and cascading into a board wipe scores ~7 EV against an Affinity board
that visibly contains the deck's exact win condition.

The fix: when the oracle text indicates "sacrifices all creatures
they control" / "all players sacrifice all creatures", zero out
on-board creature aggregates BEFORE adding the reanimated ones.

Class size: every Living-End-style cascade-reanimator, every
Decree-of-Pain-style mass-wipe-then-reanimate, every future card that
combines symmetric sacrifice with graveyard return.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import EVSnapshot, _project_spell, snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_graveyard(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="graveyard",
    )
    card._game_state = game
    game.players[controller].graveyard.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


class TestSymmetricReanimationCreditsSacrificeClause:
    """A spell whose oracle text contains 'sacrifices all creatures they
    control' AND 'returns ... creature cards from ... graveyard' must
    have its EV projection credit BOTH clauses — the sacrifice removes
    on-board creatures (zeroes pre-existing power), the reanimation
    brings GY creatures back."""

    def test_is_symmetric_reanimation_credits_sac_clause_in_opp_power_swing(
            self, card_db):
        """Synthesize the g1t4d14 fixture (Living End vs Affinity at
        T4: Affinity has 24 opp_power on board across 2 large
        Constructs; Living End's controller has 4 creatures in GY).

        Cast Living End. Pre-fix: projection adds reanimated power
        but doesn't subtract sacrificed power → `projected.opp_power`
        remains ≥ 24 (the on-board Constructs are still credited).

        Post-fix: `projected.opp_power` must reflect that the
        sacrifice clause fired, removing the entire opp on-board
        creature aggregate, before reanimating opp's (empty) graveyard.
        """
        game = GameState(rng=random.Random(0))

        # My side (Living End): 4 creatures in GY ready to reanimate.
        _add_to_graveyard(game, card_db, "Architects of Will", 0)
        _add_to_graveyard(game, card_db, "Street Wraith", 0)
        _add_to_graveyard(game, card_db, "Street Wraith", 0)
        _add_to_graveyard(game, card_db, "Striped Riverwinder", 0)

        # Opp (Affinity): wide on-board creature aggregate, no GY.
        # We model the on-board threat via the snapshot directly — this
        # test exercises _project_spell's snapshot-level projection
        # (it consumes opp_power from the snapshot, not from
        # game.players[opp].battlefield).
        snap_baseline = snapshot_from_game(game, player_idx=0)
        # Override snapshot to reflect Affinity's wide board: 2 Constructs
        # totalling 24 power (16/8 + 8/8 — the g1t4d14 fixture).
        snap = snap_baseline.fast_replace(
            my_life=1, opp_life=20,
            opp_power=24, opp_toughness=16,
            opp_creature_count=2,
            my_power=0, my_toughness=0, my_creature_count=0,
            my_hand_size=4,
        )

        living_end = _add_to_hand(game, card_db, "Living End", 0)
        projected = _project_spell(
            living_end, snap, game=game, player_idx=0)

        # Post-fix: the SACRIFICE clause must zero opp's on-board
        # creature aggregate before any reanimation happens. Opp has 0
        # creatures in their GY (modeled above), so opp_power after Living
        # End must be 0 — they sacrifice their 2 Constructs and
        # reanimate nothing.
        #
        # Pre-fix: projected.opp_power ≥ 24 (sac clause skipped), so
        # this assertion fails.
        assert projected.opp_power < snap.opp_power, (
            f"Symmetric reanimation must credit the 'sacrifice all "
            f"creatures they control' clause as a reduction in opp's "
            f"on-board power. Snapshot opp_power={snap.opp_power}, "
            f"projected opp_power={projected.opp_power}. "
            f"Pre-fix the projection adds reanimated creatures but "
            f"doesn't zero the sacrificed on-board ones, so opp_power "
            f"stays at or above the snapshot value. This is the "
            f"diagnostic's Component B."
        )

        # Tighter: opp has 0 creatures in their GY, so opp_power
        # should reach 0 after the spell resolves.
        assert projected.opp_power == 0, (
            f"Opp has 0 creatures in graveyard but {snap.opp_creature_count} "
            f"on-board; symmetric reanimation must sacrifice all and "
            f"reanimate none → projected opp_power=0. Got "
            f"{projected.opp_power}. The sacrifice clause is being "
            f"skipped."
        )
        assert projected.opp_creature_count == 0, (
            f"Opp creature_count after Living End must be 0 (no GY "
            f"creatures to return, all on-board sacrificed). Got "
            f"{projected.opp_creature_count}."
        )

    def test_my_side_also_sacrifices_then_reanimates(self, card_db):
        """Same rule applies to MY side: pre-existing my_power must be
        zeroed by the sacrifice clause, then reanimated creatures from
        my GY are added back. With 4 GY creatures, the net delta should
        reflect the trade (lose existing board, gain GY)."""
        game = GameState(rng=random.Random(0))

        # My side: 1 small on-board creature, 2 GY creatures.
        # Striped Riverwinder is 5/5, Architects of Will is 3/1.
        # Total reanimated power = 5 + 3 = 8.
        _add_to_graveyard(game, card_db, "Striped Riverwinder", 0)
        _add_to_graveyard(game, card_db, "Architects of Will", 0)

        snap = EVSnapshot(
            my_life=10, opp_life=20,
            my_power=2, opp_power=0,         # I have a 2-power creature
            my_toughness=2, opp_toughness=0,
            my_creature_count=1, opp_creature_count=0,
            my_hand_size=4, opp_hand_size=2,
            my_mana=4, opp_mana=2,
            my_total_lands=4, opp_total_lands=4,
            turn_number=4,
            my_gy_creatures=2,
        )

        living_end = _add_to_hand(game, card_db, "Living End", 0)
        projected = _project_spell(
            living_end, snap, game=game, player_idx=0)

        # Sacrifice clause zeroes my pre-existing 2-power creature,
        # then reanimation adds back the 5/5 + 3/1 from GY.
        # Net my_power == 5 + 3 = 8 (NOT 2 + 8 = 10).
        assert projected.my_power == 8, (
            f"My side: pre-existing 2 power should be sacrificed, "
            f"then GY 5/5 + 3/1 reanimated → my_power=8. Got "
            f"{projected.my_power}. Pre-fix this returns 2+8=10 "
            f"(skips the sac of my on-board creature)."
        )
        # Creature count: lose 1 on-board, gain 2 reanimated = 2.
        assert projected.my_creature_count == 2, (
            f"my_creature_count after Living End: lose 1 on-board, "
            f"gain 2 reanimated = 2. Got {projected.my_creature_count}."
        )

    def test_one_sided_reanimation_does_not_sacrifice_existing_board(
            self, card_db):
        """Regression: one-sided reanimation (Goryo's Vengeance, Persist)
        does NOT have a sacrifice clause. My pre-existing on-board
        creatures must remain after such a spell."""
        game = GameState(rng=random.Random(0))
        # My GY: 1 creature.
        _add_to_graveyard(game, card_db, "Griselbrand", 0)

        snap = EVSnapshot(
            my_life=10, opp_life=20,
            my_power=2, opp_power=0,         # I have a 2-power creature
            my_toughness=2, opp_toughness=0,
            my_creature_count=1, opp_creature_count=0,
            my_hand_size=4, opp_hand_size=2,
            my_mana=4, opp_mana=2,
            my_total_lands=4, opp_total_lands=4,
            turn_number=4,
            my_gy_creatures=1,
        )

        goryo = _add_to_hand(game, card_db, "Goryo's Vengeance", 0)
        projected = _project_spell(goryo, snap, game=game, player_idx=0)

        # Goryo's adds the reanimated creature on top of my existing
        # board. NO sacrifice clause. Existing 2 power must remain.
        assert projected.my_power >= snap.my_power + 2, (
            f"One-sided reanimation (Goryo's) must NOT sacrifice my "
            f"pre-existing on-board creatures. my_power before "
            f"{snap.my_power}, after {projected.my_power}. "
            f"Regression test for the symmetric/one-sided path "
            f"distinction."
        )
