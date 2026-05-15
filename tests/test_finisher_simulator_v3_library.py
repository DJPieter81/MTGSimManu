"""Tests for the v3 library composition module.

Covers the two functions in `ai/finisher_simulator_v3.py` that
PR3c's library-composition stream owns:

* `build_library_composition` — tag/oracle/keyword-bucketed
  histogram of the library.  No card-name keys.
* `p_draw_closer` — hypergeometric P(>=1 closer drawn in N draws).

The other v3 functions (`_tutor_access_contribution`,
`_project_multi_turn`, `_survival_to_offset`,
`simulate_finisher_chain_v3`) are owned by parallel streams and
are exercised in their own test files.

Rules-phrased tests, no card names:

* `test_library_composition_indexes_by_tag_not_card_name` — the
  histogram exposes tag/category buckets only.
* `test_p_draw_closer_hypergeometric_for_small_library` — closed-
  form hypergeometric check.
* `test_p_draw_closer_zero_draws_returns_zero` — zero-draw boundary.
* `test_p_draw_closer_more_draws_than_library_returns_one` —
  ceiling behaviour (request more draws than the library has).
* `test_p_draw_closer_uses_sideboard_when_wish_in_hand` — boundary
  test, currently SKIPPED: `p_draw_closer` operates purely on the
  composition; the SB-when-tutor-in-hand semantic is owned by the
  tutor-access function, not by `p_draw_closer` itself.  The SKIP
  documents the API boundary so the next session doesn't drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import comb
from typing import Optional, Set

import pytest


# ─── Mock helpers (mirror tests/test_finisher_simulator.py) ───────


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
    zone: str = "library"


def _ritual(iid: int = 1, name: str = "RitualMock") -> MockCard:
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=2,
            is_instant=True,
            oracle_text="add three red mana",
            ritual_mana=("R", 3),
            tags={"ritual"},
        ),
        instance_id=iid,
    )


def _cantrip(iid: int = 2, name: str = "CantripMock") -> MockCard:
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=1,
            is_instant=True,
            oracle_text="draw a card",
            tags={"cantrip"},
        ),
        instance_id=iid,
    )


def _tutor(iid: int = 3, name: str = "TutorMock") -> MockCard:
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=3,
            is_sorcery=True,
            oracle_text="search your sideboard for a card",
            tags={"tutor"},
        ),
        instance_id=iid,
    )


def _storm_closer(iid: int = 4, name: str = "StormBurnMock") -> MockCard:
    """Storm-keyword damage closer (Grapeshot pattern)."""
    from engine.cards import Keyword as Kw

    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=2,
            is_sorcery=True,
            oracle_text="storm — deal 1 damage to any target",
            tags={"finisher"},
            keywords={Kw.STORM},
        ),
        instance_id=iid,
    )


def _token_finisher(iid: int = 5, name: str = "TokenFinisherMock") -> MockCard:
    """Empty-the-Warrens-style oracle: 'create … tokens … for each'."""
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=4,
            is_sorcery=True,
            oracle_text=(
                "create two 1/1 red goblin creature tokens for each "
                "spell cast this turn"
            ),
            tags={"finisher"},
        ),
        instance_id=iid,
    )


def _cycling_payoff(iid: int = 6, name: str = "CyclingPayoffMock") -> MockCard:
    """Living-End-style oracle payoff."""
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=5,
            is_sorcery=True,
            oracle_text=(
                "all creature cards in all graveyards return to the "
                "battlefield"
            ),
            tags={"combo"},
        ),
        instance_id=iid,
    )


def _cost_reducer(iid: int = 7, name: str = "ReducerMock") -> MockCard:
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=2,
            is_creature=True,
            power=1,
            toughness=1,
            oracle_text="spells cost 1 less to cast",
            tags={"cost_reducer"},
        ),
        instance_id=iid,
    )


def _flashback(iid: int = 8, name: str = "FlashbackMock") -> MockCard:
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=4,
            is_sorcery=True,
            oracle_text=(
                "each instant and sorcery card in your graveyard gains "
                "flashback until end of turn"
            ),
            tags={"flashback", "combo"},
        ),
        instance_id=iid,
    )


def _land(iid: int = 9, name: str = "MountainMock") -> MockCard:
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=0,
            is_land=True,
            oracle_text="{T}: add R",
            tags=set(),
        ),
        instance_id=iid,
    )


# ─── build_library_composition tests ──────────────────────────────


def test_library_composition_indexes_by_tag_not_card_name():
    """Rule: the histogram bucket keys are tag/category strings,
    never card names.  Reading the composition should NEVER require
    knowing what specific cards live in the library.
    """
    from ai.finisher_simulator_v3 import build_library_composition

    library = [
        _ritual(iid=1, name="UniqueRitualName"),
        _ritual(iid=2, name="OtherRitualName"),
        _cantrip(iid=3, name="UniqueCantripName"),
        _tutor(iid=4, name="UniqueTutorName"),
        _storm_closer(iid=5, name="UniqueStormCloserName"),
        _cost_reducer(iid=6, name="UniqueReducerName"),
        _flashback(iid=7, name="UniqueFlashbackName"),
        _land(iid=8, name="UniqueLandName"),
    ]

    comp = build_library_composition(library)

    # Total includes every card passed in (no zone filtering).
    assert comp.total == len(library)

    # Card-name keys must not appear in any bucket.
    for k in comp.by_tag.keys():
        assert "Unique" not in k, (
            f"by_tag key {k!r} looks like a card name; "
            "histogram must be tag-keyed"
        )
        # Card names from this test:
        assert k not in {
            "UniqueRitualName", "OtherRitualName",
            "UniqueCantripName", "UniqueTutorName",
            "UniqueStormCloserName", "UniqueReducerName",
            "UniqueFlashbackName", "UniqueLandName",
        }

    # Tag-based buckets that match the design doc's enumeration are
    # present and correctly counted.
    assert comp.by_tag.get("ritual", 0) == 2
    assert comp.by_tag.get("cantrip", 0) == 1
    assert comp.by_tag.get("tutor", 0) == 1
    assert comp.by_tag.get("cost_reducer", 0) == 1
    assert comp.by_tag.get("flashback", 0) == 1

    # Closer category is derived from the STORM keyword predicate.
    assert "storm_closer" in comp.closer_categories
    assert comp.closer_count >= 1


def test_library_composition_counts_token_finisher_via_oracle():
    """Rule: the token_finisher closer category is detected by the
    same oracle predicate the v2 simulator uses ('create … tokens …
    for each'), not by a card-name list.
    """
    from ai.finisher_simulator_v3 import build_library_composition

    library = [
        _token_finisher(iid=1, name="FirstFinisherMock"),
        _token_finisher(iid=2, name="SecondFinisherMock"),
        _cantrip(iid=3),
    ]
    comp = build_library_composition(library)

    assert comp.total == 3
    assert "token_finisher" in comp.closer_categories
    # Two of the three cards are token-finisher closers.
    assert comp.closer_count == 2


def test_library_composition_counts_cycling_payoff_via_oracle():
    """Rule: the cycling_payoff closer category fires on the v2
    oracle predicate ('all creature cards … graveyard … battlefield'),
    not on a card-name list.
    """
    from ai.finisher_simulator_v3 import build_library_composition

    library = [
        _cycling_payoff(iid=1, name="FirstCyclingPayoffMock"),
        _cantrip(iid=2),
    ]
    comp = build_library_composition(library)

    assert "cycling_payoff" in comp.closer_categories
    assert comp.closer_count == 1


def test_library_composition_empty_library():
    """Rule: an empty library produces an empty composition with
    zero closers and no closer categories.
    """
    from ai.finisher_simulator_v3 import build_library_composition

    comp = build_library_composition([])
    assert comp.total == 0
    assert comp.closer_count == 0
    assert comp.closer_categories == ()


# ─── p_draw_closer tests ──────────────────────────────────────────


def test_p_draw_closer_hypergeometric_for_small_library():
    """Rule: P(>=1 closer in N draws) matches the closed-form
    hypergeometric formula P = 1 - C(non_closer, N) / C(total, N).
    """
    from ai.finisher_simulator_v3 import (
        LibraryComposition,
        p_draw_closer,
    )

    # 40-card library with 2 closers (Storm's typical fuel : closer
    # ratio).  Probability of drawing >= 1 closer in 7 draws (an
    # opening hand) is computed from the hypergeometric.
    total = 40
    closers = 2
    n_draws = 7
    non_closer = total - closers
    expected = 1.0 - comb(non_closer, n_draws) / comb(total, n_draws)

    comp = LibraryComposition(
        total=total,
        by_tag={"ritual": 6, "cantrip": 8, "storm_closer": closers},
        closer_count=closers,
        closer_categories=("storm_closer",),
    )

    got = p_draw_closer(comp, n_draws)
    assert got == pytest.approx(expected, rel=1e-9, abs=1e-12)


def test_p_draw_closer_zero_closers_returns_zero():
    """Rule: P(draw closer | 0 closers in library) = 0 regardless
    of how many draws are taken.
    """
    from ai.finisher_simulator_v3 import (
        LibraryComposition,
        p_draw_closer,
    )

    comp = LibraryComposition(
        total=40,
        by_tag={"ritual": 6, "cantrip": 34},
        closer_count=0,
        closer_categories=(),
    )

    for n in (1, 3, 7, 40):
        assert p_draw_closer(comp, n) == 0.0


def test_p_draw_closer_zero_draws_returns_zero():
    """Rule: P(draw closer | 0 draws) = 0 — no draws were taken,
    so no closer can be drawn.  Boundary case.
    """
    from ai.finisher_simulator_v3 import (
        LibraryComposition,
        p_draw_closer,
    )

    comp = LibraryComposition(
        total=40,
        by_tag={"storm_closer": 2},
        closer_count=2,
        closer_categories=("storm_closer",),
    )

    assert p_draw_closer(comp, 0) == 0.0


def test_p_draw_closer_more_draws_than_library_returns_one():
    """Rule: if N exceeds the library size AND there's at least one
    closer, P = 1.0 (we must draw the whole library, which contains
    a closer).
    """
    from ai.finisher_simulator_v3 import (
        LibraryComposition,
        p_draw_closer,
    )

    comp = LibraryComposition(
        total=10,
        by_tag={"storm_closer": 1, "cantrip": 9},
        closer_count=1,
        closer_categories=("storm_closer",),
    )

    # 11 draws from a 10-card library — closer is guaranteed.
    assert p_draw_closer(comp, 11) == pytest.approx(1.0)
    # n_draws == total also guarantees the closer.
    assert p_draw_closer(comp, 10) == pytest.approx(1.0)


def test_p_draw_closer_monotone_in_n_draws():
    """Rule: more draws never decrease P(reach closer)."""
    from ai.finisher_simulator_v3 import (
        LibraryComposition,
        p_draw_closer,
    )

    comp = LibraryComposition(
        total=40,
        by_tag={"storm_closer": 2, "cantrip": 38},
        closer_count=2,
        closer_categories=("storm_closer",),
    )

    prev = -1.0
    for n in range(0, 11):
        p = p_draw_closer(comp, n)
        assert p >= prev - 1e-12, f"non-monotone at n={n}"
        prev = p


def test_p_draw_closer_subset_filter_storm_only():
    """Rule: when the caller narrows `closer_categories` to a
    subset, only the categories listed in `composition.by_tag` for
    that subset are counted.

    Setup: library has 1 storm_closer and 1 token_finisher.
    P(storm_closer only) < P(any closer) because the storm count
    is half the total closer count.
    """
    from ai.finisher_simulator_v3 import (
        LibraryComposition,
        p_draw_closer,
    )

    total = 40
    comp = LibraryComposition(
        total=total,
        by_tag={
            "ritual": 6,
            "cantrip": 32,
            "storm_closer": 1,
            "token_finisher": 1,
        },
        closer_count=2,
        closer_categories=("storm_closer", "token_finisher"),
    )

    n = 7
    p_any = p_draw_closer(comp, n)
    p_storm = p_draw_closer(comp, n, closer_categories={"storm_closer"})

    # Strict ordering: narrower category set ⇒ fewer "good" cards ⇒
    # smaller probability.
    assert p_storm < p_any
    # Closed-form check for the narrowed query (1 closer of 40).
    expected_storm = 1.0 - comb(total - 1, n) / comb(total, n)
    assert p_storm == pytest.approx(expected_storm, rel=1e-9, abs=1e-12)


# ─── API boundary documented as SKIP ──────────────────────────────


@pytest.mark.skip(
    reason=(
        "API boundary: `p_draw_closer` projects from the library "
        "histogram only; the 'Wish in hand grants SB access' "
        "semantic is owned by `_tutor_access_contribution` (parallel "
        "stream).  This test is a placeholder so the next session "
        "wires SB → composition through the right entry point rather "
        "than re-purposing `p_draw_closer`."
    )
)
def test_p_draw_closer_uses_sideboard_when_wish_in_hand():
    """Documented boundary: SB access is the tutor function's job."""
    pass
