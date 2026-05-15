"""Tests for the tutor-as-finisher-access function in v3.

Covers ``_tutor_access_contribution`` in
``ai/finisher_simulator_v3.py`` per the rule from
``docs/design/2026-05-10_simulator_v3.md`` §4:

    A tutor card is finisher access if and only if SB ∪ library
    contains a card matching one of the closer-category predicates.
    The tutor's CMC is added to the chain's mana cost, and the
    tutor's resolution risk (P_counter from BHI) is multiplied
    into the chain's success_probability.

Tests phrase the MECHANIC, not the card.  A Wish swapped for any
other ``'tutor'``-tagged card with the same SB/library state must
produce the same result — that is the structural rule v3 enforces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set

import pytest

from ai.bhi import BayesianHandTracker
from ai.ev_evaluator import EVSnapshot
from ai.finisher_simulator_v3 import (
    LibraryComposition,
    _tutor_access_contribution,
    build_library_composition,
)
from ai.scoring_constants import CHAIN_TUTOR_MIN_RESOLVE


# ─── Mock helpers (shape mirrors tests/test_finisher_simulator_v3_rollout.py) ──


@dataclass
class MockTemplate:
    name: str = "Test"
    cmc: int = 1
    is_instant: bool = False
    is_sorcery: bool = False
    is_creature: bool = False
    is_land: bool = False
    oracle_text: str = ""
    tags: Set[str] = field(default_factory=set)
    keywords: Set = field(default_factory=set)
    color_identity: Set = field(default_factory=set)
    power: Optional[int] = None
    toughness: Optional[int] = None
    ritual_mana: Optional[tuple] = None
    cycling_cost_data: Optional[dict] = None
    is_cascade: bool = False
    is_arcane: bool = False
    splice_cost: Optional[int] = None


@dataclass
class MockCard:
    template: MockTemplate = field(default_factory=MockTemplate)
    instance_id: int = 0
    zone: str = "hand"

    @property
    def name(self):
        return self.template.name


def _make_snap(*, my_mana: int = 4, opp_life: int = 20) -> EVSnapshot:
    return EVSnapshot(
        my_life=20,
        opp_life=opp_life,
        my_mana=my_mana,
        opp_mana=0,
        my_total_lands=my_mana,
        opp_total_lands=0,
        my_hand_size=4,
        opp_hand_size=4,
        turn_number=4,
        storm_count=0,
        opp_power=0,
    )


def _make_bhi(*, p_counter: float = 0.0) -> BayesianHandTracker:
    bhi = BayesianHandTracker(player_idx=0)
    bhi.beliefs.p_counter = p_counter
    bhi.beliefs.p_removal = 0.0
    bhi._initialized = True
    return bhi


# ─── Card mocks — generic by tag/keyword/oracle (no card-name semantics) ───


def _wish_like_tutor(iid: int = 1, name: str = "WishMock", cmc: int = 3) -> MockCard:
    """A 'tutor'-tagged card with no special oracle hooks.

    Stands in for Wish, Burning Wish, Living Wish, Glittering Wish,
    Demonic Tutor, Eladamri's Call, Summoner's Pact — any card the
    engine tags as ``'tutor'``.
    """
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=cmc,
            is_sorcery=True,
            tags={"tutor"},
        ),
        instance_id=iid,
    )


def _storm_closer(iid: int = 100, name: str = "StormBurnSB") -> MockCard:
    """A STORM-keyword closer (mocks Grapeshot pattern)."""
    from engine.cards import Keyword as Kw

    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=2,
            is_sorcery=True,
            keywords={Kw.STORM},
            oracle_text="storm — deal 1 damage to any target",
            tags={"finisher"},
        ),
        instance_id=iid,
    )


def _token_finisher(iid: int = 101) -> MockCard:
    """Empty-the-Warrens-pattern token finisher.

    Detected via the oracle predicate `_has_token_finisher_oracle_v3`:
    'create … tokens … for each'.
    """
    return MockCard(
        template=MockTemplate(
            name="TokenFinisherMock",
            cmc=4,
            is_sorcery=True,
            oracle_text=(
                "create two 1/1 red Goblin creature tokens "
                "for each spell cast this turn"
            ),
            tags={"finisher"},
        ),
        instance_id=iid,
    )


def _ritual(iid: int = 2) -> MockCard:
    return MockCard(
        template=MockTemplate(
            name="PyreticRitualMock",
            cmc=2,
            is_instant=True,
            oracle_text="add three red mana",
            ritual_mana=("R", 3),
            tags={"ritual"},
        ),
        instance_id=iid,
    )


def _cantrip(iid: int = 3) -> MockCard:
    return MockCard(
        template=MockTemplate(
            name="CantripMock",
            cmc=1,
            is_instant=True,
            oracle_text="draw a card.",
            tags={"cantrip"},
        ),
        instance_id=iid,
    )


def _empty_library_composition() -> LibraryComposition:
    """A composition where the library has NO closers in any category."""
    return LibraryComposition(
        total=40,
        by_tag={},
        closer_count=0,
        closer_categories=(),
    )


def _library_with_storm_closer(extra_total: int = 39) -> LibraryComposition:
    """Composition synthesised to represent a library that contains a
    single STORM-keyword closer."""
    library = [_storm_closer(iid=999)]
    library += [_ritual(iid=2000 + i) for i in range(extra_total)]
    return build_library_composition(library)


# ─── Tests ─────────────────────────────────────────────────────────────


class TestTutorAccessContribution:
    """The mechanic: tutor in hand + closer reachable in SB/library."""

    def test_tutor_access_returns_closer_in_zone_sb_when_wish_in_hand_and_closer_in_sb(
        self,
    ):
        """Rule: a 'tutor'-tagged card in hand, plus a STORM-keyword
        closer in the sideboard, returns the tutor as the best access
        path with a positive resolve probability.

        The closer being in SB (not in hand, not in library) is the
        canonical Ruby-Storm-vs-mainboard-hate fixture from the
        seed 60600 trace.
        """
        snap = _make_snap()
        hand = [_wish_like_tutor(iid=1, cmc=3), _ritual(2)]
        sideboard = [_storm_closer(iid=10)]
        composition = _empty_library_composition()
        bhi = _make_bhi(p_counter=0.0)

        best_tutor, extra_cost, p_resolves = _tutor_access_contribution(
            hand=hand,
            sideboard=sideboard,
            library_composition=composition,
            snap=snap,
            bhi_state=bhi,
        )

        # The tutor is the in-hand 'tutor'-tagged card.
        assert best_tutor is not None
        assert "tutor" in best_tutor.template.tags
        # Extra mana cost is the tutor's CMC.
        assert extra_cost == 3
        # No counter density -> resolves close to 1.0, floored at
        # CHAIN_TUTOR_MIN_RESOLVE.
        assert p_resolves >= CHAIN_TUTOR_MIN_RESOLVE
        assert p_resolves == pytest.approx(1.0)

    def test_tutor_access_adds_amortised_mana_cost_across_lookahead_horizon(self):
        """Rule: the tutor's CMC is returned as the chain's extra
        mana cost — the same number, regardless of the EVSnapshot's
        turn / mana state.  Amortisation in the multi-turn rollout
        composes by accounting for the cost at the firing turn (see
        design §4.3); this function's contract is to *report* the
        cost so the rollout can integrate it across offsets.
        """
        sideboard = [_storm_closer(iid=10)]
        composition = _empty_library_composition()
        bhi = _make_bhi(p_counter=0.0)

        # Three different EVSnapshots simulating turn-0, turn-1,
        # turn-2 of the rollout.  Each shares the same tutor in hand.
        snap_t0 = _make_snap(my_mana=4)
        snap_t1 = snap_t0.replace(
            my_mana=snap_t0.my_mana + 1,
            my_total_lands=snap_t0.my_total_lands + 1,
            turn_number=snap_t0.turn_number + 1,
        )
        snap_t2 = snap_t1.replace(
            my_mana=snap_t1.my_mana + 1,
            my_total_lands=snap_t1.my_total_lands + 1,
            turn_number=snap_t1.turn_number + 1,
        )

        hand = [_wish_like_tutor(iid=1, cmc=3)]

        results = [
            _tutor_access_contribution(
                hand=hand,
                sideboard=sideboard,
                library_composition=composition,
                snap=snap,
                bhi_state=bhi,
            )
            for snap in (snap_t0, snap_t1, snap_t2)
        ]
        # Same tutor returned at every offset; same CMC reported.
        for tutor, cost, _ in results:
            assert tutor is not None
            assert cost == 3

    def test_tutor_access_returns_empty_when_no_tutor_in_hand(self):
        """Rule: a hand with no 'tutor'-tagged cards returns
        (None, 0, 0.0) regardless of SB / library contents."""
        snap = _make_snap()
        hand = [_ritual(1), _cantrip(2)]  # no tutor in hand
        sideboard = [_storm_closer(iid=10)]
        composition = _library_with_storm_closer()
        bhi = _make_bhi(p_counter=0.0)

        best_tutor, extra_cost, p_resolves = _tutor_access_contribution(
            hand=hand,
            sideboard=sideboard,
            library_composition=composition,
            snap=snap,
            bhi_state=bhi,
        )

        assert best_tutor is None
        assert extra_cost == 0
        assert p_resolves == 0.0

    def test_tutor_access_returns_empty_when_tutor_has_no_payoff_target_in_sb_or_library(
        self,
    ):
        """Rule: a 'tutor'-tagged card in hand with NO closer
        reachable (empty SB, library has no closer category) returns
        (None, 0, 0.0) — preserves v2 ``_tutor_has_payoff_access``
        behaviour generalised to v3's tag/oracle/keyword predicates.
        """
        snap = _make_snap()
        hand = [_wish_like_tutor(iid=1, cmc=3)]
        sideboard = []  # nothing to fetch
        composition = _empty_library_composition()  # library has no closer
        bhi = _make_bhi(p_counter=0.0)

        best_tutor, extra_cost, p_resolves = _tutor_access_contribution(
            hand=hand,
            sideboard=sideboard,
            library_composition=composition,
            snap=snap,
            bhi_state=bhi,
        )

        assert best_tutor is None
        assert extra_cost == 0
        assert p_resolves == 0.0

    def test_tutor_access_uses_tag_predicate_not_card_name(self):
        """Rule: swap the Wish-like tutor for any other
        ``'tutor'``-tagged card with the same CMC and same SB state,
        and the function returns the same (tutor, extra_cost,
        p_resolves) shape — i.e. detection is by tag, not by name.

        This is the abstraction-contract gate: no `card.name == "X"`
        branches.  Two distinct names with identical tag/CMC/oracle
        shape produce identical results.
        """
        snap = _make_snap()
        sideboard = [_storm_closer(iid=10)]
        composition = _empty_library_composition()
        bhi = _make_bhi(p_counter=0.0)

        # Two different names, same tag/CMC.  Could be Wish vs
        # Burning Wish vs Demonic Tutor — the function MUST treat
        # them identically.
        hand_wish = [_wish_like_tutor(iid=1, name="WishMock", cmc=3)]
        hand_other = [_wish_like_tutor(iid=1, name="DemonicTutorMock", cmc=3)]

        r_wish = _tutor_access_contribution(
            hand=hand_wish,
            sideboard=sideboard,
            library_composition=composition,
            snap=snap,
            bhi_state=bhi,
        )
        r_other = _tutor_access_contribution(
            hand=hand_other,
            sideboard=sideboard,
            library_composition=composition,
            snap=snap,
            bhi_state=bhi,
        )

        # Same shape — only `name` field differs, which is metadata.
        assert (r_wish[0] is not None) == (r_other[0] is not None)
        assert r_wish[1] == r_other[1]
        assert r_wish[2] == pytest.approx(r_other[2])


class TestTutorAccessResolveProbability:
    """The mechanic: tutor's p_resolves is gated by BHI counter
    density and floored at CHAIN_TUTOR_MIN_RESOLVE."""

    def test_tutor_access_resolve_p_dampened_by_bhi_counter_density(self):
        """Rule: when BHI's p_counter is 0.5, p_resolves becomes
        ``max(CHAIN_TUTOR_MIN_RESOLVE, 1 - 0.5) = 0.5``.
        """
        snap = _make_snap()
        hand = [_wish_like_tutor(iid=1, cmc=3)]
        sideboard = [_storm_closer(iid=10)]
        composition = _empty_library_composition()
        bhi = _make_bhi(p_counter=0.5)

        _, _, p_resolves = _tutor_access_contribution(
            hand=hand,
            sideboard=sideboard,
            library_composition=composition,
            snap=snap,
            bhi_state=bhi,
        )
        # 1 - 0.5 = 0.5, which equals the floor — both arms agree.
        assert p_resolves == pytest.approx(0.5)

    def test_tutor_access_resolve_p_floored_at_min_resolve_under_max_counter_density(
        self,
    ):
        """Rule: with BHI's p_counter = 1.0 (full counter density),
        p_resolves is still at LEAST CHAIN_TUTOR_MIN_RESOLVE.  A
        fully counter-leaden opponent does not zero out the path."""
        snap = _make_snap()
        hand = [_wish_like_tutor(iid=1, cmc=3)]
        sideboard = [_storm_closer(iid=10)]
        composition = _empty_library_composition()
        bhi = _make_bhi(p_counter=1.0)

        _, _, p_resolves = _tutor_access_contribution(
            hand=hand,
            sideboard=sideboard,
            library_composition=composition,
            snap=snap,
            bhi_state=bhi,
        )
        # 1 - 1.0 = 0.0, but floor lifts it to CHAIN_TUTOR_MIN_RESOLVE.
        assert p_resolves == pytest.approx(CHAIN_TUTOR_MIN_RESOLVE)


class TestTutorAccessTargetDiscovery:
    """The mechanic: a tutor's target is reachable when the closer
    lives in SB OR library."""

    def test_tutor_access_reachable_via_library_closer_when_sb_empty(self):
        """Rule: when SB has no closer but the library composition
        declares closers > 0, the tutor still counts as having
        finisher access.  Mirrors v2's `_has_storm_finisher` scan of
        SB ∪ library.
        """
        snap = _make_snap()
        hand = [_wish_like_tutor(iid=1, cmc=3)]
        sideboard = []  # empty
        composition = _library_with_storm_closer()
        bhi = _make_bhi(p_counter=0.0)

        best_tutor, extra_cost, p_resolves = _tutor_access_contribution(
            hand=hand,
            sideboard=sideboard,
            library_composition=composition,
            snap=snap,
            bhi_state=bhi,
        )

        assert best_tutor is not None
        assert extra_cost == 3
        assert p_resolves > 0.0

    def test_tutor_access_reachable_via_token_finisher_oracle_in_sb(self):
        """Rule: a non-STORM-keyword token finisher (Empty-the-Warrens
        pattern) in SB still counts as a real target.  The closer
        predicate set is keyword OR oracle, not keyword alone.
        """
        snap = _make_snap()
        hand = [_wish_like_tutor(iid=1, cmc=3)]
        sideboard = [_token_finisher(iid=10)]
        composition = _empty_library_composition()
        bhi = _make_bhi(p_counter=0.0)

        best_tutor, extra_cost, p_resolves = _tutor_access_contribution(
            hand=hand,
            sideboard=sideboard,
            library_composition=composition,
            snap=snap,
            bhi_state=bhi,
        )

        assert best_tutor is not None
        assert extra_cost == 3
        assert p_resolves > 0.0

    def test_tutor_access_picks_cheapest_tutor_when_multiple_tutors_in_hand(self):
        """Rule: when several 'tutor'-tagged cards are in hand, the
        cheapest one (lowest CMC) is returned — minimising the
        chain's extra mana cost.  Mirrors design §4.2's ``min(..., key=cmc)``.
        """
        snap = _make_snap()
        hand = [
            _wish_like_tutor(iid=1, name="ExpensiveTutorMock", cmc=4),
            _wish_like_tutor(iid=2, name="CheapTutorMock", cmc=2),
        ]
        sideboard = [_storm_closer(iid=10)]
        composition = _empty_library_composition()
        bhi = _make_bhi(p_counter=0.0)

        best_tutor, extra_cost, _ = _tutor_access_contribution(
            hand=hand,
            sideboard=sideboard,
            library_composition=composition,
            snap=snap,
            bhi_state=bhi,
        )

        assert best_tutor is not None
        assert extra_cost == 2  # cheapest tutor wins
