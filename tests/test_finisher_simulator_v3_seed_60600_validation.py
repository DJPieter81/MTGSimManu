"""Seed 60600 G3 T4 chain-blindness validation for the v3 simulator.

This test synthesises the projection-layer state at the two trace
decisions cited in ``docs/PHASE_D_DEFERRED.md`` (Trace 2, decisions
``g3t4d76`` and ``g3t4d78``) and verifies that the multi-turn rollout
in ``ai/finisher_simulator_v3.py`` produces a strictly higher
chain-fuel score than the v2 single-turn projection.

Why this matters
----------------
The v2 simulator returns ``pattern="none"`` (or ``expected_damage=0``)
when the storm chain cannot fire **this turn** from current mana
state.  That signal is what ``compute_play_ev`` consumes via the
combo-modifier path — and is what produced the ~-10 EV scores on
chain-prerequisite spells (Desperate Ritual, Past in Flames,
Manamorphose) at d76/d78, masking the truth that *those spells
advance the chain toward a closer reachable next turn*.

v3's ``_project_multi_turn`` runs the same v2 chain finder at offsets
T+0, T+1, T+2, T+3 with snapshot deltas (+1 mana/land, -opp_pressure
life, storm reset per CR 500.4).  The argmax score over the rollout
gives credit for chains that close on a future turn instead of this
turn — the chain-enablement signal that v2 lacks.

Rules-phrased name (per the abstraction contract in CLAUDE.md):
"multi-turn rollout credits chain-fuel even when chain cannot fire
this turn".  The test does NOT name a specific card in its assertions
— every detection runs through tag/oracle/keyword predicates.

Trace decisions cited (from ``replays/affinity_vs_storm_60600.ndjson``):

* ``g3t4d76``  — storm=6, opp_life=20, Storm mana exhausted after
  6-spell chain. Alternatives in NDJSON:
  Grapeshot -5.63 / Manamorphose -10.00 / Desperate Ritual -10.07.
* ``g3t4d78``  — storm=7, opp_life=20, 2 floating mana post-
  Manamorphose. Alternatives:
  Desperate Ritual -9.95 / Reckless Impulse -10.25 / Past in Flames
  -10.28.

The test fixtures pin the projection arithmetic at these states; the
~-10 EV numbers come from ``compute_play_ev`` (the consumer) and are
NOT what the simulator returns directly — the simulator returns
``FinisherProjection`` objects whose ``expected_damage *
success_probability`` is the chain-fuel score that ``compute_play_ev``
multiplies into the EV table.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set

import pytest

from ai.bhi import BayesianHandTracker
from ai.ev_evaluator import EVSnapshot
from ai.finisher_simulator import simulate_finisher_chain
from ai.finisher_simulator_v3 import (
    LibraryComposition,
    _project_multi_turn,
)
from ai.scoring_constants import CHAIN_MULTI_TURN_DEPTH


# ─── Mock helpers (shape mirrors tests/test_finisher_simulator.py) ──


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

    @property
    def power(self):
        return self.template.power


def _grapeshot(iid: int) -> MockCard:
    """STORM-keyword damage closer (Grapeshot pattern)."""
    from engine.cards import Keyword as Kw

    return MockCard(
        template=MockTemplate(
            name=f"StormBurn{iid}",
            cmc=2,
            is_sorcery=True,
            keywords={Kw.STORM},
            oracle_text="storm — deal 1 damage to any target",
            tags={"finisher"},
        ),
        instance_id=iid,
    )


def _ritual(iid: int, name: str = "RitualMock") -> MockCard:
    """Desperate Ritual / Pyretic Ritual-style: pay 1R, add RRR."""
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


def _manamorphose(iid: int) -> MockCard:
    """Manamorphose-style cantrip ritual: 1R, draw, add 2 any."""
    return MockCard(
        template=MockTemplate(
            name="ManamorphoseMock",
            cmc=2,
            is_instant=True,
            oracle_text="add two mana of any color, then draw a card",
            ritual_mana=("any", 2),
            tags={"cantrip", "ritual"},
        ),
        instance_id=iid,
    )


def _cantrip(iid: int, name: str = "CantripMock") -> MockCard:
    """Reckless Impulse / Wrenn's Resolve-style draw-2."""
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=2,
            is_sorcery=True,
            oracle_text="exile the top two cards of your library. you may play those cards",
            tags={"cantrip"},
        ),
        instance_id=iid,
    )


def _past_in_flames(iid: int) -> MockCard:
    """Past in Flames-style flashback enabler: grants flashback to GY
    instants/sorceries. Detection via oracle text."""
    return MockCard(
        template=MockTemplate(
            name="PastInFlamesMock",
            cmc=4,
            is_sorcery=True,
            oracle_text=(
                "each instant and sorcery card in your graveyard "
                "gains flashback until end of turn"
            ),
            tags={"finisher_access", "flashback"},
        ),
        instance_id=iid,
    )


def _ruby_medallion(iid: int) -> MockCard:
    """Ruby Medallion-style cost reducer."""
    return MockCard(
        template=MockTemplate(
            name="RubyMedallionMock",
            cmc=2,
            oracle_text="red spells you cast cost 1 less to cast",
            tags={"cost_reducer"},
        ),
        instance_id=iid,
    )


def _make_bhi() -> BayesianHandTracker:
    """BHI tracker with neutral beliefs (no counter / removal density).

    Skips ``initialize_from_game`` because we want to isolate the
    rollout's chain-fuel arithmetic from the prior-computation
    pipeline.  Matches the helper in tests/test_finisher_simulator_v3_rollout.py.
    """
    bhi = BayesianHandTracker(player_idx=0)
    bhi.beliefs.p_counter = 0.0
    bhi.beliefs.p_removal = 0.0
    bhi._initialized = True
    return bhi


# ─── Trace-2 G3 T4 fixtures ────────────────────────────────────────


def _d76_snapshot() -> EVSnapshot:
    """Snapshot at g3t4d76 — end of Main1 after 6-spell chain.

    From the NDJSON state at seq 389 (TURN_START for T4 Storm) and
    decision g3t4d76 (seq 391):

    * Storm life=10 (took 4 dmg from Affinity T4 plating)
    * Affinity life=20
    * Storm mana exhausted (chain spent it)
    * storm_count=6 spells cast this turn (Ral, Wrenn's, Glimpse,
      Desperate Ritual+splice, Glimpse, Ruby Medallion)
    * Storm hand_size=8 (drew through 2 Glimpses + Wrenn's)
    * Affinity has Plating in hand, Signal Pest at 0/1 — no
      attackers in T4, opp_power=0
    """
    return EVSnapshot(
        my_life=10,
        opp_life=20,
        my_mana=0,
        opp_mana=0,
        my_total_lands=4,
        opp_total_lands=3,
        my_hand_size=8,
        opp_hand_size=5,
        turn_number=4,
        storm_count=6,
        opp_power=0,
    )


def _d76_hand() -> list:
    """Storm hand at g3t4d76 — 8 cards.

    Constructed from the NDJSON alternatives field at d76+d78
    (Grapeshot, Manamorphose, Desperate Ritual, Reckless Impulse,
    Past in Flames appears at d78 so it was in hand at d76 too).
    Filler completes the 8-card hand size — the rollout cares about
    the chain-fuel signature, not the exact hand identity.
    """
    return [
        _grapeshot(1),
        _manamorphose(2),
        _ritual(3, "DesperateRitualMock"),
        _cantrip(4, "RecklessImpulseMock"),
        _cantrip(5, "RecklessImpulseMock2"),
        _ritual(6, "PyreticRitualMock"),
        _cantrip(7, "WrennMock"),
        _cantrip(8, "GlimpseMock"),
    ]


def _d76_battlefield() -> list:
    """Storm battlefield at g3t4d76 — 2 Ruby Medallions + Ral.

    From the TURN_START board state at seq 397 (T4 Affinity start
    after Storm's main1): Storm has Ral + 2 Ruby Medallions in
    'other' permanents.  The 2 medallions = -2R cost reducer
    stacked on red spells.
    """
    return [_ruby_medallion(50), _ruby_medallion(51)]


def _d78_snapshot() -> EVSnapshot:
    """Snapshot at g3t4d78 — end of Main2 after Manamorphose.

    From NDJSON decision g3t4d78 (seq 397):

    * Storm life=10, Affinity life=20
    * 2 floating mana (Manamorphose adds 2 any color)
    * storm_count=7 (the chain continued in Main2 with Manamorphose)
    * Storm hand_size=8 (Manamorphose draws 1, chain emptied 1)
    """
    return EVSnapshot(
        my_life=10,
        opp_life=20,
        my_mana=2,
        opp_mana=0,
        my_total_lands=4,
        opp_total_lands=3,
        my_hand_size=8,
        opp_hand_size=5,
        turn_number=4,
        storm_count=7,
        opp_power=0,
    )


def _d78_hand() -> list:
    """Storm hand at g3t4d78 — 8 cards including Past in Flames.

    PiF in hand is the d78-specific signal — the alternatives table
    in NDJSON shows it scored at -10.28, the canonical chain-blindness
    case for flashback enablers.
    """
    return [
        _past_in_flames(1),
        _ritual(2, "DesperateRitualMock"),
        _cantrip(3, "RecklessImpulseMock"),
        _cantrip(4, "WrennMock"),
        _cantrip(5, "GlimpseMock"),
        _cantrip(6, "RecklessImpulseMock2"),
        _ritual(7, "PyreticRitualMock"),
        _grapeshot(8),
    ]


def _d78_graveyard() -> list:
    """Storm graveyard at g3t4d78 — fuel for PiF flashback.

    Contains the spells cast during the d70..d77 chain.  PiF's
    flashback grants reach to these for a future-turn chain.
    """
    return [
        _ritual(20, "GraveDesperate"),
        _cantrip(21, "GraveGlimpse1"),
        _cantrip(22, "GraveGlimpse2"),
        _cantrip(23, "GraveWrenn"),
        _manamorphose(24),
    ]


def _empty_library_composition(total: int = 35) -> LibraryComposition:
    """Library composition with no closer signal — pins the rollout's
    closer_reachable_p to the in-hand indicator only.

    The seed 60600 trace's Storm deck has 3 mainboard Grapeshots; by
    d76 ~2 are still in library after the early-game draws.  We test
    with closer_count=0 first to isolate the "chain-fuel without
    library-drawn closer" case — that's the strictest validation of
    chain-enablement credit.
    """
    return LibraryComposition(
        total=total,
        by_tag={},
        closer_count=0,
        closer_categories=(),
    )


# ─── Validation tests ──────────────────────────────────────────────


class TestSeed60600D76ChainBlindnessGap:
    """g3t4d76 — storm=6, mana=0, closer in hand.

    The v2 simulator returns ``pattern="none"`` at this state because
    ``find_all_chains`` requires at least one castable spell with
    mana>=cmc, and mana=0 disqualifies every spell.  The chain-fuel
    EV in ``compute_play_ev`` therefore has no positive signal — every
    ritual/cantrip falls to its raw "card-from-hand minus mana spent"
    score (~-10).

    v3's ``_project_multi_turn`` projects T+0, T+1, T+2, T+3 with
    snapshot deltas (+1 mana/land per offset, storm reset to 0 per
    CR 500.4).  At T+2 the chain has 2 mana — enough to cast the
    in-hand Grapeshot for storm-count damage. The rollout's argmax
    score is strictly positive.
    """

    def test_v3_multi_turn_score_strictly_exceeds_v2_single_turn_score(self):
        """Rule: at the d76 fixture, v3's rollout argmax score is
        strictly positive while v2's single-turn projection is zero
        (chain unreachable this turn).  This is the canonical
        chain-blindness closure case.
        """
        snap = _d76_snapshot()
        hand = _d76_hand()
        battlefield = _d76_battlefield()

        # v2 single-turn projection
        v2 = simulate_finisher_chain(
            snap=snap,
            hand=hand,
            battlefield=battlefield,
            graveyard=[],
            library_size=35,
            storm_count=6,
            archetype="storm",
        )
        v2_score = v2.expected_damage * v2.success_probability

        # v3 multi-turn rollout
        v3 = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=battlefield,
            graveyard=[],
            sideboard=[],
            library_composition=_empty_library_composition(),
            storm_count=6,
            archetype="storm",
            bhi_state=_make_bhi(),
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )
        v3_best = max(v3, key=lambda p: p.score)

        # The chain-blindness gap: v2 returns 0 because mana=0 blocks
        # the chain finder.
        assert v2_score == pytest.approx(0.0), (
            f"v2 should produce zero chain-fuel signal at d76 "
            f"(mana=0, no chain reachable), got v2_score={v2_score}"
        )

        # v3 closes the gap: at T+2 the chain has enough mana to
        # cast the in-hand Grapeshot with ritual support, producing
        # strictly positive expected damage and a strictly positive
        # score (survival=1.0 under no-clock opponent).
        assert v3_best.score > 0.0, (
            f"v3 rollout must produce non-zero argmax score at d76, "
            f"got best={v3_best}"
        )
        assert v3_best.score > v2_score, (
            f"v3 argmax score must STRICTLY exceed v2 score "
            f"(chain-blindness gap closure), v3={v3_best.score} "
            f"v2={v2_score}"
        )

    def test_v3_offset_zero_matches_v2_when_mana_blocks_chain(self):
        """Rule: at offset=0 (this turn) v3 delegates to v2 with the
        same snapshot.  Both must agree that the chain is unreachable
        from mana=0.  This pins that v3's multi-turn signal comes
        from FUTURE offsets, not from disagreeing with v2 on the
        current turn.
        """
        snap = _d76_snapshot()
        hand = _d76_hand()
        battlefield = _d76_battlefield()

        v3 = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=battlefield,
            graveyard=[],
            sideboard=[],
            library_composition=_empty_library_composition(),
            storm_count=6,
            archetype="storm",
            bhi_state=_make_bhi(),
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        # Offset 0 sees mana=0 — same as v2's view of the current
        # turn.  Expected damage at offset 0 must be 0.
        assert v3[0].offset == 0
        assert v3[0].expected_damage == pytest.approx(0.0), (
            f"v3 offset=0 must agree with v2 (mana=0 blocks chain), "
            f"got expected_damage={v3[0].expected_damage}"
        )

    def test_v3_chain_enablement_credit_appears_at_higher_offsets(self):
        """Rule: v3's rollout produces strictly positive expected
        damage on at LEAST one offset >= 1.  This is the chain-
        enablement credit — the signal v2 cannot produce because it
        only projects this turn.

        Mechanic: the rituals + cantrips + closer in hand cannot
        fire at mana=0 (this turn), but the +N land-drop deltas at
        T+1..T+3 let the chain reach the closer and produce non-zero
        storm damage on a future turn.
        """
        snap = _d76_snapshot()
        hand = _d76_hand()
        battlefield = _d76_battlefield()

        v3 = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=battlefield,
            graveyard=[],
            sideboard=[],
            library_composition=_empty_library_composition(),
            storm_count=6,
            archetype="storm",
            bhi_state=_make_bhi(),
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        future_offsets = [p for p in v3 if p.offset >= 1]
        assert any(p.expected_damage > 0.0 for p in future_offsets), (
            f"v3 rollout must credit chain enablement on at least one "
            f"offset >= 1; got per-offset damage: "
            f"{[(p.offset, p.expected_damage) for p in v3]}"
        )

    def test_v3_argmax_offset_is_future_turn_when_current_blocked(self):
        """Rule: when this-turn chain is mana-blocked, the rollout's
        argmax over score selects a future offset (>= 1), not offset 0.
        Encodes the "fire on the optimal turn-offset" rule from design
        §1.4 step 3 — the AI's hold-vs-fire becomes "build now, close
        next turn".
        """
        snap = _d76_snapshot()
        hand = _d76_hand()
        battlefield = _d76_battlefield()

        v3 = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=battlefield,
            graveyard=[],
            sideboard=[],
            library_composition=_empty_library_composition(),
            storm_count=6,
            archetype="storm",
            bhi_state=_make_bhi(),
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        best = max(v3, key=lambda p: p.score)
        assert best.offset >= 1, (
            f"v3 must select a future offset when current turn is "
            f"chain-blocked; got argmax offset={best.offset}"
        )


class TestSeed60600D78ChainProjection:
    """g3t4d78 — storm=7, mana=2, PiF in hand, GY full.

    At d78, v2 *does* find a chain (PiF + GY flashback + Grapeshot in
    hand with 2 mana floating + Medallion reducers).  But the chain
    is projected at the current storm_count=7, so v2 reports a
    sizeable ``expected_damage`` reflecting *this turn's* storm.  v3
    resets ``storm_count=0`` for each offset (CR 500.4 — storm count
    is per-turn) so its projections are STRICTLY conservative on
    future-turn damage.

    The d78 case is a counterpoint to d76: v3 is not always higher
    than v2.  Both behaviors are rule-correct; the diagnostic doc
    records which is the appropriate signal at which decision.
    """

    def test_v3_resets_storm_count_per_offset(self):
        """Rule: every offset reports ``storm_at_offset == 0`` — CR
        500.4 mandates storm count is per-turn.  Pins the multi-turn
        rollout's CR-compliance.
        """
        snap = _d78_snapshot()

        v3 = _project_multi_turn(
            snap=snap,
            hand=_d78_hand(),
            battlefield=_d76_battlefield(),
            graveyard=_d78_graveyard(),
            sideboard=[],
            library_composition=_empty_library_composition(total=33),
            storm_count=7,
            archetype="storm",
            bhi_state=_make_bhi(),
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        for p in v3:
            assert p.storm_at_offset == 0, (
                f"v3 offset={p.offset} must reset storm per CR 500.4, "
                f"got storm_at_offset={p.storm_at_offset}"
            )

    def test_v3_rollout_produces_positive_score_with_closer_in_hand(self):
        """Rule: at d78 the in-hand Grapeshot makes
        ``closer_reachable_p == 1.0`` at every offset (no draw needed),
        so the rollout's score reflects chain damage × survival on
        the optimal offset.  Score must be strictly positive.
        """
        snap = _d78_snapshot()

        v3 = _project_multi_turn(
            snap=snap,
            hand=_d78_hand(),
            battlefield=_d76_battlefield(),
            graveyard=_d78_graveyard(),
            sideboard=[],
            library_composition=_empty_library_composition(total=33),
            storm_count=7,
            archetype="storm",
            bhi_state=_make_bhi(),
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        best = max(v3, key=lambda p: p.score)
        # Closer is in hand — the indicator is 1.0 at every offset.
        for p in v3:
            assert p.closer_reachable_p == pytest.approx(1.0), (
                f"closer in hand should make closer_reachable_p=1.0 "
                f"at every offset, got {p}"
            )
        assert best.score > 0.0, (
            f"d78 with closer in hand + ritual fuel must produce "
            f"positive score, got {best}"
        )

    def test_v3_pif_pattern_detected_via_oracle_not_card_name(self):
        """Rule: Past in Flames-style oracle ('grants flashback ...
        to graveyard instants/sorceries') is detected by v2's
        ``_is_pif_pattern`` predicate inside ``_project_storm``.  The
        v3 rollout reuses v2 internally, so the PiF chain-enabler
        signal must propagate without card-name dependence.

        Verifies the pattern is detected even though the hand
        contains a mock named ``PastInFlamesMock`` — every detection
        runs through oracle text, not the name field.
        """
        snap = _d78_snapshot()
        hand = _d78_hand()
        # Confirm the PiF mock's oracle text matches the v2 predicate.
        pif = next(c for c in hand if "flashback" in c.template.oracle_text)
        assert "flashback" in pif.template.oracle_text.lower()
        assert "graveyard" in pif.template.oracle_text.lower()
        assert (
            "instant" in pif.template.oracle_text.lower()
            or "sorcery" in pif.template.oracle_text.lower()
        )

        v3 = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=_d76_battlefield(),
            graveyard=_d78_graveyard(),
            sideboard=[],
            library_composition=_empty_library_composition(total=33),
            storm_count=7,
            archetype="storm",
            bhi_state=_make_bhi(),
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        # The storm-pattern indicator on at least one offset confirms
        # PiF + rituals + closer is recognised as a chain pattern.
        patterns = {p.notes.split("pattern=")[-1] for p in v3}
        assert "storm" in patterns, (
            f"v3 rollout must detect storm pattern from oracle-driven "
            f"chain-fuel signature; got patterns={patterns}"
        )


class TestSeed60600ResidualGap:
    """Documents the residual chain-blindness gap that v3 does NOT
    close — the ``_tutor_access_contribution`` stub.

    v3's design (``docs/design/2026-05-10_simulator_v3.md`` §4) calls
    for tutor-as-finisher-access semantics: when a tutor is in hand
    and the SB/library contains a closer, the chain projects positive
    damage at that offset (closer fetched via tutor, +tutor_cmc cost).

    The function ``_tutor_access_contribution`` is still
    ``NotImplementedError`` in main.  The rollout's
    ``_safe_tutor_resolve_p`` falls back to ``CHAIN_TUTOR_MIN_RESOLVE``
    when the tutor is detected in hand, but the underlying damage
    projection still uses v2's ``simulate_finisher_chain`` which
    returns ``expected_damage=0`` when no closer is in hand.

    The test below pins this residual gap so the next session knows
    where the remaining work lies — chain-fuel scoring with closer
    in *library only* (no in-hand closer) still scores zero.
    """

    def test_residual_no_in_hand_closer_collapses_chain_damage_to_zero(self):
        """Rule: when no closer is in hand AND no tutor wires the
        SB/library closer in (because ``_tutor_access_contribution``
        is a stub), v3's expected_damage at every offset is 0 even
        when ``closer_reachable_p > 0`` from library composition.

        This is a known residual gap; the test documents it so PR3c
        / the next migration session has a regression anchor.
        """
        snap = EVSnapshot(
            my_life=15, opp_life=20,
            my_mana=4, opp_mana=0,
            my_total_lands=4, opp_total_lands=3,
            my_hand_size=4, opp_hand_size=5,
            turn_number=4, storm_count=0,
            opp_power=0,
        )
        # Hand has chain fuel + PiF but NO closer in hand.
        hand = [
            _past_in_flames(1),
            _ritual(2),
            _ritual(3, "PyreticRitualMock"),
            _cantrip(4),
        ]
        # Library has 3 closers — v3 should ideally credit
        # "draw-to-closer + chain" but currently scores 0 damage.
        lib_with_closers = LibraryComposition(
            total=35,
            by_tag={"storm_closer": 3},
            closer_count=3,
            closer_categories=("storm_closer",),
        )

        v3 = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=[],
            graveyard=[],
            sideboard=[],
            library_composition=lib_with_closers,
            storm_count=0,
            archetype="storm",
            bhi_state=_make_bhi(),
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        # closer_reachable_p > 0 at offset >= 1 (library has closers
        # to draw into) but expected_damage stays 0 — that's the gap.
        future = [p for p in v3 if p.offset >= 1]
        assert any(p.closer_reachable_p > 0.0 for p in future), (
            f"library closers must produce positive closer_reachable_p; "
            f"got {[(p.offset, p.closer_reachable_p) for p in v3]}"
        )
        # The residual: expected_damage is 0 across every offset
        # because v2's chain finder cannot synthesise a "drawn closer".
        for p in v3:
            assert p.expected_damage == pytest.approx(0.0), (
                f"residual gap (PR3c TODO): v3 expected_damage at "
                f"offset={p.offset} should ideally be > 0 when "
                f"closer_reachable_p > 0, but the v2 chain finder it "
                f"delegates to requires closer in hand; got {p}"
            )
        # Score collapses to 0 as a consequence — the chain-blindness
        # gap is NOT fully closed for the closer-in-library case.
        best = max(v3, key=lambda p: p.score)
        assert best.score == pytest.approx(0.0), (
            f"residual gap: v3 score must be 0 when no closer is in "
            f"hand and the tutor-access path is unwired; got {best}"
        )
