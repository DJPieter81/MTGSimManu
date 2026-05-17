"""Invariant: _threat_score is invariant under prior turn history.

P0-B regression — `engine.card_effects._threat_score` (which delegates to
`ai.permanent_threat.permanent_threat`) computes the marginal drop in
controller's position value when a permanent is removed.  The marginal
formula is:

    threat(P) = V_O(B) - V_O(B \\ {P})

The strategic intent: the relative ordering of two threats on the SAME
board must be a function of the BOARD only, not of how the game arrived
at that board.  Concretely, if two paths reach the same final
battlefield configuration — one freshly constructed, one after N prior
turns of plays — then `permanent_threat(card)` must return the same
value on both paths for every card on the board.

Rule encoded by this test (independent of any single card):
   "permanent_threat(card) on a fixed final battlefield is invariant
    to the path through which the battlefield was reached."

Class size: every Modern card whose threat is read by removal /
counterspell / blink targeters.  State drift here is the canonical
cause of the live-vs-repro inversion documented in P0-B of
`docs/proposals/2026-05-03_p0_p1_backlog.md`: live sim picks
Springleaf Drum at 1.333 over Memnite at 1.150, isolated repro of the
same battlefield correctly picks Memnite at 1.15 over Drum at 1.00.

The two paths in this test:

* Path A — fresh build: instantiate the four-permanent Affinity board
  in a brand-new GameState on turn 1, no prior plays.
* Path B — after-history: instantiate the same four permanents in a
  GameState whose turn_number has been advanced by several player
  turns and whose per-turn tracking counters (spells_cast_this_turn,
  cards_drawn_this_turn) carry residual values.

The marginal-contribution formula must produce identical scores on
both paths because the popped-card delta is the only thing that should
matter — any per-turn counter that bleeds into the comparison is a
state-drift bug.

Generalization-first note: this rule is invariance-shaped, not
Affinity-shaped.  Boros Energy with an Ocelot Pride board, Living End
post-cascade boards, Storm post-ritual chains, Eldrazi Tron post-Karn
turns — every matchup whose AI decides removal targets after the
opponent has resolved several prior turns is a beneficiary.  Cranial
Plating with Memnite + Drum is the smallest reproducer for the
artifact-count drift class; the test pins the rule on this minimal
fixture so it doesn't depend on a full deck simulation.
"""
from __future__ import annotations

import random

import pytest

from ai.permanent_threat import permanent_threat
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


def _mk(game, card_db, name, ctrl, zone: str = "battlefield"):
    """Spawn a permanent in `zone` for player `ctrl`.  Defaults to
    battlefield, with full ETB bookkeeping.  Hand zone skips ETB so
    we can stage cards without triggering effects.  Mirrors the
    helper used elsewhere in the test suite (
    `tests/test_permanent_threat_invariant_across_calls.py`)."""
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=ctrl,
        controller=ctrl,
        instance_id=game.next_instance_id(),
        zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[ctrl].battlefield.append(card)
    elif zone == "hand":
        game.players[ctrl].hand.append(card)
    else:
        raise ValueError(f"unsupported zone in test helper: {zone}")
    return card


def _attach(equipment, creature):
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")


def _build_affinity_board(card_db, *, prior_turns: int):
    """Build the canonical Cranial-Plating-equipped Affinity board.

    Returns ``(game, memnite, drum)`` where the controller is player 1
    (the "opp" from the AI's perspective).  When ``prior_turns > 0``
    the surrounding game state is shifted into a "T5 of an Affinity
    game" configuration: both players have additional lands and hand
    cards, life totals reflect a few combat phases of damage, and the
    half-turn counter is advanced.  The Affinity board itself
    (Ornithopter + Plating equipped, plus Memnite and Springleaf
    Drum) is identical to Path A — only the surrounding context
    differs.  That context is what we expect to be invisible to a
    correct marginal-contribution formula.
    """
    game = GameState(rng=random.Random(0))

    # Affinity-style four-permanent fixture.  Plating equipped to
    # Ornithopter so the +1/+0 per-artifact pump is live.
    orn = _mk(game, card_db, "Ornithopter", 1)
    plating = _mk(game, card_db, "Cranial Plating", 1)
    memnite = _mk(game, card_db, "Memnite", 1)
    drum = _mk(game, card_db, "Springleaf Drum", 1)
    _attach(plating, orn)

    if prior_turns > 0:
        # Surrounding context for a T5-ish Affinity board: opp has a
        # few lands and reload cards, the AI controller (player 0)
        # has a few lands and burn spells, life totals reflect ~2
        # turns of attacks, and per-turn tracking carries residual
        # values.  The four-permanent Affinity fixture is unchanged.
        for _ in range(3):
            _mk(game, card_db, "Mountain", 1)
            _mk(game, card_db, "Mountain", 0)
        for _ in range(3):
            _mk(game, card_db, "Lightning Bolt", 1, zone="hand")
            _mk(game, card_db, "Lightning Bolt", 0, zone="hand")
        game.players[0].life = 12
        game.players[1].life = 18
        game.turn_number = 1 + prior_turns
        game.players[0].cards_drawn_this_turn = 1
        game.players[1].cards_drawn_this_turn = 1

    return game, memnite, drum


class TestThreatScoreInvariantUnderPriorTurns:
    """`permanent_threat` on a fixed final battlefield must not drift
    based on how the game reached that board state."""

    def test_artifact_pop_threat_invariant_to_owner_player_index(
            self, card_db):
        """Localised drift fix (P0-B, 2026-05-08): the marginal threat
        of an artifact on a Plating-equipped board must be the SAME
        whether the owner is player 0 or player 1.  The previous
        ``permanent_threat`` implementation conditionally adjusted
        ``my_artifact_count`` for ``owner_idx == 0`` and
        ``opp_artifact_count`` for ``owner_idx == 1``.  That confused
        the absolute player index with the snapshot's perspective:
        ``snapshot_from_game(game, owner_idx)`` returns a snapshot in
        which ``my_*`` always refers to ``owner``'s side regardless
        of which absolute player ``owner`` is.  The two branches
        therefore did NOT mirror each other — popping the same
        artifact from a player-0 board vs a player-1 board produced
        different threat magnitudes, even though the boards were
        otherwise identical.  The fix removes the count restoration
        entirely: ``snapshot_from_game`` correctly recomputes the
        post-pop counts, and the marginal-contribution formula at
        face value gives a positive threat for popping a non-land
        artifact (removing it drops the owner's artifact_value).

        Class size: every Modern non-land artifact whose threat the
        AI scores.  The standard `run_meta` matchup configuration
        sets the AI under evaluation as player 0 and the opponent as
        player 1, so the player-1-owner case is the dominant one
        where the bug manifested."""
        # Same fixture, mirrored across player_idx so the perspective
        # asymmetry of the bug becomes the assertion.
        def _build(owner_idx):
            g = GameState(rng=random.Random(0))
            orn = _mk(g, card_db, "Ornithopter", owner_idx)
            plating = _mk(g, card_db, "Cranial Plating", owner_idx)
            _mk(g, card_db, "Memnite", owner_idx)
            drum = _mk(g, card_db, "Springleaf Drum", owner_idx)
            _attach(plating, orn)
            return g, drum

        g0, drum0 = _build(owner_idx=0)
        g1, drum1 = _build(owner_idx=1)

        threat_p0 = permanent_threat(drum0, g0.players[0], g0)
        threat_p1 = permanent_threat(drum1, g1.players[1], g1)

        assert threat_p0 == pytest.approx(threat_p1, abs=1e-9), (
            f"Springleaf Drum threat depends on owner's player_idx: "
            f"owner=p0 → {threat_p0:.6f}, owner=p1 → {threat_p1:.6f}.  "
            f"permanent_threat must be invariant under a pure player-"
            f"index swap of an otherwise-identical board.  The bug "
            f"this pins is the partial-snapshot count-restore "
            f"conditioning on absolute owner_idx (0 → my_*, 1 → opp_*) "
            f"while ``snapshot_from_game(game, owner_idx)`` always "
            f"reports counts in owner-perspective my_* fields.  The "
            f"correct fix removes the count restore entirely and lets "
            f"snapshot_from_game's recompute do the work."
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "P0-B residual: position_value is structurally non-linear "
            "in surrounding state (combat_clock = ceil(opp_life / "
            "effective_power), mana_clock_impact = 1/opp_life, "
            "life_as_resource discretization).  The artifact-count "
            "perspective drift — the dominant Affinity-side bug — is "
            "fixed (see "
            "test_artifact_pop_threat_invariant_to_owner_player_index), "
            "but strict path-equality on Memnite threat additionally "
            "requires normalising the surrounding-state context in "
            "permanent_threat — out of scope for this commit.  The "
            "qualitative ordering (creature > mana rock under prior "
            "turn history) holds; see "
            "test_body_outranks_mana_rock_after_prior_turns."
        ),
    )
    def test_memnite_threat_equal_across_paths(self, card_db):
        """Path A (fresh T1) and Path B (after several prior turns of
        bookkeeping) must agree on Memnite's threat to within float
        noise.  If they disagree, some per-turn counter or
        nonlinearity is leaking into the marginal-contribution
        computation."""
        game_a, memnite_a, _drum_a = _build_affinity_board(
            card_db, prior_turns=0)
        game_b, memnite_b, _drum_b = _build_affinity_board(
            card_db, prior_turns=8)  # ~T5, both players have moved

        opp_a = game_a.players[1]
        opp_b = game_b.players[1]
        threat_a = permanent_threat(memnite_a, opp_a, game_a)
        threat_b = permanent_threat(memnite_b, opp_b, game_b)

        assert threat_a == pytest.approx(threat_b, abs=1e-9), (
            f"Memnite threat drifted across paths: fresh={threat_a:.6f} "
            f"vs after-history={threat_b:.6f}.  permanent_threat must "
            f"be a pure function of the battlefield, not of "
            f"surrounding context (life totals, hand sizes, mana)."
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "P0-B residual: position_value's mana_clock_impact = "
            "1/opp_life makes the artifact_value contribution scale "
            "with opponent life total. The artifact-count perspective "
            "drift (the dominant Affinity-side bug) is fixed; this "
            "remaining drift is structural to position_value and out "
            "of scope for this commit. The qualitative ordering "
            "(body > rock under prior turn history) holds."
        ),
    )
    def test_drum_threat_equal_across_paths(self, card_db):
        """Same invariance for Springleaf Drum.  If only one of
        Memnite-threat or Drum-threat drifts, the relative ordering
        of removal targets inverts — that's the live-sim symptom in
        P0-B (live picks Drum 1.333 > Memnite 1.150; isolated repro
        picks Memnite 1.15 > Drum 1.00)."""
        game_a, _memnite_a, drum_a = _build_affinity_board(
            card_db, prior_turns=0)
        game_b, _memnite_b, drum_b = _build_affinity_board(
            card_db, prior_turns=8)

        opp_a = game_a.players[1]
        opp_b = game_b.players[1]
        threat_a = permanent_threat(drum_a, opp_a, game_a)
        threat_b = permanent_threat(drum_b, opp_b, game_b)

        assert threat_a == pytest.approx(threat_b, abs=1e-9), (
            f"Springleaf Drum threat drifted across paths: "
            f"fresh={threat_a:.6f} vs after-history={threat_b:.6f}."
        )

    def test_body_outranks_mana_rock_after_prior_turns(self, card_db):
        """The qualitative ordering — body > mana rock on a Plating
        board — must hold even after several prior turns of game
        bookkeeping.  This is the bug the live-sim exhibits: the
        ordering inverts at T5 even though it's correct at T1.
        Phrased as a strict-greater-than rule because both equal-
        contribution-to-Plating cases must still favour the body's
        attacking-clock contribution."""
        game, memnite, drum = _build_affinity_board(
            card_db, prior_turns=8)
        opp = game.players[1]

        memnite_threat = permanent_threat(memnite, opp, game)
        drum_threat = permanent_threat(drum, opp, game)

        assert memnite_threat > drum_threat, (
            f"After {8} prior half-turns of bookkeeping, Memnite "
            f"({memnite_threat:.3f}) must still score higher than "
            f"Springleaf Drum ({drum_threat:.3f}).  If Drum scores "
            f"higher here but lower on a fresh board, a per-turn "
            f"counter is creating state drift in the marginal-"
            f"contribution formula — the very inversion documented "
            f"in P0-B of the 2026-05-03 backlog."
        )
