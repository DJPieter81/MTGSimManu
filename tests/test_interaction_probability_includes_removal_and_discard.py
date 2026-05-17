"""M7 (W1b-7): `interaction_probability` broadens BHI's narrow
counter-only `counter_probability` into a unified measure of opp's
ability to disrupt our spell — counters + instant-speed removal +
instant-speed discard — all gated by what untapped mana the opp can
actually deploy.

Audit roots (combo F3, midrange F2 from
`docs/history/audits/2026-05-16_5panel_bo3_audit.md`):

* Combo (Storm): when opp is tapped out, the chain currently still
  pays a counter discount because `counter_probability` only ever
  drops when we observe their hand — never when their mana is
  visibly insufficient.  The chain over-defends and fires later than
  the EV-optimal turn.
* Midrange (Dimir): control opp's `counter_probability` is the
  ceiling, but a 1-mana removal spell (Fatal Push) is just as
  disruptive against a creature line.  Removal-disruption was
  invisible.

Structural fix: rename `counter_probability` →
`interaction_probability` and broaden the computation to
``max(P(counter) | counter-mana, P(removal) | removal-mana,
P(discard) | discard-mana)`` — every interaction term gated on the
opp having the mana to use it.  ONE call site in
`ev_evaluator._score_spell` multiplies projected EV by
``(1 - interaction_probability)`` instead of the scattered
``counter_pct`` checks.

Tests pin the mechanic, not the card.  Card names appear only as
fixture data.

Per CLAUDE.md "failing-test-first": this file goes red first; the
green phase ships in the same commit.
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from ai.bhi import BayesianHandTracker
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


REPO_ROOT = Path(__file__).resolve().parents[1]


# ─── Fixtures ──────────────────────────────────────────────────────────


def _make_card(game, card_db, name, controller, zone="library"):
    tmpl = card_db.cards.get(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone=zone,
    )
    card._game_state = game
    return card


def _make_game(card_db, my_deck, opp_deck, opp_library_cards,
               opp_battlefield_cards=()):
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = my_deck
    game.players[1].deck_name = opp_deck
    game.players[1].library = [
        _make_card(game, card_db, n, 1) for n in opp_library_cards
    ]
    game.players[1].hand = []
    game.players[1].battlefield = [
        _make_card(game, card_db, n, 1, zone="battlefield")
        for n in opp_battlefield_cards
    ]
    return game


# Dimir Midrange-style pool — skewed toward removal (Fatal Push x2,
# Counterspell x1) to mirror the real-list distribution (Dimir runs
# 4x Push + 2-3x counters in MB).  This skew makes the broadened
# `interaction_probability` STRICTLY greater than the narrow
# counter-only posterior when both channels are mana-enabled.
# Thoughtseize is the gameplan-declared discard signal already
# wired through `_compute_discard_prior` (cf.
# test_bhi_tracks_discard_probability.py).
DIMIR_LIB = [
    "Thoughtseize",
    "Fatal Push",
    "Fatal Push",
    "Counterspell",
    "Psychic Frog",
    "Murktide Regent",
    "Watery Grave",
    "Polluted Delta",
    "Island",
    "Swamp",
]


# ─── A. Removal contribution to interaction_probability ────────────────


class TestInteractionProbabilityIncludesRemoval:
    """When the opp's pool exposes instant-speed removal AND they have
    the mana to cast it, `interaction_probability` must reflect the
    removal threat — not only the counter threat.

    Mechanic: the broadened measure is ``max(P(counter), P(removal),
    P(discard))`` for the categories the opp can actually deploy.
    """

    def test_interaction_probability_higher_when_opp_has_removal_in_hand(
            self, card_db):
        """Mechanic: pool with both counters and removal -> the
        broadened interaction_probability must be at least as large as
        the counter-only baseline.

        Sets up an opp with sufficient untapped mana to cast either,
        so the gating-by-mana doesn't suppress either branch.
        """
        # Opp battlefield seeded with enough untapped lands that any
        # 1-cost or 2-cost interaction can fire.
        game = _make_game(
            card_db, "Ruby Storm", "Dimir Midrange", DIMIR_LIB,
            opp_battlefield_cards=("Island", "Island", "Swamp"))

        # Deal a 7-card hand to the opp so the priors reflect a real
        # post-mulligan state (with hand_size=0 the prior is 0 by
        # construction — see initialize_from_game).
        for _ in range(7):
            c = game.players[1].library.pop(0)
            c.zone = "hand"
            game.players[1].hand.append(c)

        bhi = BayesianHandTracker(player_idx=0)
        bhi.initialize_from_game(game)

        # The broadened API must exist as a method on the tracker.
        assert hasattr(bhi, "get_interaction_probability"), (
            "BHI must expose get_interaction_probability() — the "
            "structural broadening of the narrow counter posterior.")

        # Read the narrow counter prior directly from the belief
        # field (no separate accessor is required — `.beliefs.p_counter`
        # is the canonical narrow value).
        p_counter_only = bhi.beliefs.p_counter
        p_interaction = bhi.get_interaction_probability(game)

        # When removal is in the pool, the broadened measure must be
        # >= counter-only.  Strict greater is the typical case (Dimir
        # has Fatal Push), but >= is the structurally required floor.
        assert p_interaction >= p_counter_only, (
            f"interaction_probability ({p_interaction}) must be >= "
            f"counter-only ({p_counter_only}) when opp pool exposes "
            f"removal in addition to counters."
        )
        # Strictly greater here — Dimir's pool has Fatal Push, which
        # is real removal threat.
        assert p_interaction > p_counter_only, (
            f"interaction_probability must strictly exceed "
            f"counter-only when removal is present in the pool; got "
            f"interaction={p_interaction}, counter={p_counter_only}."
        )


# ─── B. Tap-out window ─────────────────────────────────────────────────


class TestInteractionProbabilityTapOutWindow:
    """When the opp has zero untapped mana, they cannot deploy any
    interaction at instant speed.  `interaction_probability` must
    therefore be zero — the "tap-out window" the audit identified.

    Combo decks should fire faster into this window; control decks
    should worry less about losing their own counters since the opp
    can't respond either way.
    """

    def test_interaction_probability_zero_when_opp_tapped_out(
            self, card_db):
        """Mechanic: opp has zero untapped mana -> interaction is 0.

        Even if the pool is heavy with counters/removal/discard, none
        of them can fire without mana.  This is the structural
        complement to "mana you can see is mana they have"."""
        # Opp has interaction cards but no lands on the battlefield —
        # nothing untapped.
        game = _make_game(
            card_db, "Ruby Storm", "Dimir Midrange", DIMIR_LIB,
            opp_battlefield_cards=())
        # Deal a real hand so the priors are non-zero by construction.
        for _ in range(7):
            c = game.players[1].library.pop(0)
            c.zone = "hand"
            game.players[1].hand.append(c)

        bhi = BayesianHandTracker(player_idx=0)
        bhi.initialize_from_game(game)

        p_interaction = bhi.get_interaction_probability(game)
        assert p_interaction == 0.0, (
            f"interaction_probability must be 0.0 when opp has zero "
            f"untapped mana; got {p_interaction}.  The tap-out window "
            f"is the audit's primary motivation for this primitive."
        )


# ─── C. Single call site in _score_spell ──────────────────────────────


class TestScoreSpellSingleCallSite:
    """Structural directive: `compute_play_ev` (the EV scoring entry
    point that `_score_spell` wraps) multiplies by
    `(1 - interaction_probability)` at exactly ONE call site — the
    scattered `counter_pct` checks have been consolidated.
    """

    def test_score_spell_calls_interaction_probability_exactly_once(
            self, card_db):
        """Spy on `get_interaction_probability` during a single
        `compute_play_ev` invocation; assert call count == 1.

        The structural unification rules out the previous N-site
        pattern where counter_probability was queried in multiple
        branches of estimate_opponent_response + recovery code.
        """
        from ai.bhi import BayesianHandTracker
        from ai.ev_evaluator import compute_play_ev, snapshot_from_game

        # Build a tiny but realistic game: P0 has a cheap spell to
        # score, P1 has visible mana + a counter/removal-heavy pool.
        game = _make_game(
            card_db, "Ruby Storm", "Dimir Midrange", DIMIR_LIB,
            opp_battlefield_cards=("Island", "Island", "Swamp"))
        # Hand for the opp so priors are positive.
        for _ in range(7):
            c = game.players[1].library.pop(0)
            c.zone = "hand"
            game.players[1].hand.append(c)
        game.active_player = 0
        game.priority_player = 0
        # Give P0 a creature to score (so removal branch is live).
        game.players[0].hand = [_make_card(game, card_db, "Goblin Guide",
                                            0, zone="hand")]
        # P0 needs the mana too.
        game.players[0].battlefield = [
            _make_card(game, card_db, "Mountain", 0, zone="battlefield")
        ]

        bhi = BayesianHandTracker(player_idx=0)
        bhi.initialize_from_game(game)

        spell = game.players[0].hand[0]
        snap = snapshot_from_game(game, player_idx=0)

        # Spy on the broadened method.
        with mock.patch.object(
            bhi, "get_interaction_probability",
            wraps=bhi.get_interaction_probability,
        ) as spy:
            compute_play_ev(spell, snap,
                            archetype="combo", game=game,
                            player_idx=0, bhi=bhi)
            call_count = spy.call_count

        assert call_count == 1, (
            f"compute_play_ev must call get_interaction_probability "
            f"EXACTLY ONCE per scoring; observed {call_count}.  "
            f"Multiple call sites indicate the consolidation "
            f"regressed."
        )


# ─── D. Grep guard — no production `counter_probability` references ──


class TestNoOldNameReferences:
    """The rename is exhaustive across `ai/` production paths.  A
    stray `counter_probability` reference (outside the explicitly
    tagged legacy accessor) would silently fall back to the narrow
    measure and re-introduce the audit defect.
    """

    def test_no_counter_probability_references_remain(self):
        """Grep ai/ for old name; allow lines tagged
        `# ratchet-allow:` (the legacy `get_counter_probability`
        accessor kept for narrow-prior regression tests).
        """
        ai_dir = REPO_ROOT / "ai"
        assert ai_dir.is_dir(), f"expected ai/ at {ai_dir}"

        result = subprocess.run(
            ["grep", "-rn", "counter_probability", str(ai_dir)],
            capture_output=True, text=True,
        )
        matches = [
            line for line in result.stdout.splitlines()
            if line.strip()
            and "ratchet-allow" not in line
        ]
        assert matches == [], (
            f"Found {len(matches)} stray reference(s) to "
            f"`counter_probability` in ai/; the rename to "
            f"`interaction_probability` must be exhaustive.  "
            f"First 5:\n" + "\n".join(matches[:5])
        )
