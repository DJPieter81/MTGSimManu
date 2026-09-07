"""A spell's keep-score must be discounted when the hand's OWN lands
cannot reach its CMC.

`_card_keep_score` valued every spell by curve position (`KEEP_SCORE_CMC_
INVERTED_CEIL - cmc`) and tags, with no term reading whether the hand's own
land count could ever pay for it. `_hand_ev_score` already discounts
UNPRODUCTIVE lands beyond the optimal count (`MULLIGAN_EXCESS_LAND_PENALTY`)
— nothing discounted UNCASTABLE spells on the other side of the same
resource.

Observed (2026-09-06, `docs/diagnostics/2026-09-06_wall_clock_deadline_
load_invariance.md` follow-up, Ruby Storm vs Azorius Blink seed 50001):
Blink kept a 1-land 7 — Aang (CMC3), Quantum Riddler (CMC5), 2x Witch
Enchanter (CMC4), Consign to Memory (CMC1) — at score 25.0 against the 24.0
floor. The game never drew a second land; four of the five spells were dead
the entire game. `mulligan_min_lands: 1` (Azorius Blink's own gameplan
data) is what let the hand past the floor gate; the scoring bug is what let
it clear 24.0 despite four spells the hand could never cast.

Class size: `_card_keep_score` runs for every non-land card in every
opening hand, for all 20k+ Modern cards and all 25 registered decks — not
an Azorius Blink special case. `MULLIGAN_SPELL_MANA_GAP_PENALTY` in
`ai/scoring_constants.py` carries the derivation and the calibration.
"""
from __future__ import annotations

from typing import List

import pytest

from ai.gameplan import DeckGameplan, GoalEngine
from ai.mulligan import MulliganDecider
from ai.strategy_profile import ArchetypeStrategy
from ai.scoring_constants import (
    MULLIGAN_MIN_HAND_SCORE_7,
    MULLIGAN_SPELL_MANA_GAP_PENALTY,
)
from engine.cards import CardInstance, CardTemplate, CardType
from engine.mana import ManaCost


def _land(iid: int, *, produces: List[str] = None) -> CardInstance:
    tmpl = CardTemplate(
        name="Synthetic Land", card_types=[CardType.LAND],
        mana_cost=ManaCost(generic=0), supertypes=[], subtypes=[],
        produces_mana=produces or ["W"], enters_tapped=False,
        oracle_text="", tags=set(),
    )
    return CardInstance(template=tmpl, owner=0, controller=0,
                        instance_id=iid, zone="hand")


def _spell(iid: int, cmc: int, tags: set = None, *,
           name: str = None) -> CardInstance:
    tmpl = CardTemplate(
        name=name or f"Synthetic Spell CMC{cmc}",
        card_types=[CardType.SORCERY], mana_cost=ManaCost(generic=cmc),
        supertypes=[], subtypes=[], produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(tags or []),
    )
    return CardInstance(template=tmpl, owner=0, controller=0,
                        instance_id=iid, zone="hand")


def _no_gameplan_decider(archetype: ArchetypeStrategy) -> MulliganDecider:
    return MulliganDecider(archetype, goal_engine=None)


def _tempo_gameplan_decider(*, mulligan_min_lands: int = 1) -> MulliganDecider:
    """A no-combo, no-critical-piece tempo/control deck — every hand falls
    through to the scored gate `_hand_ev_score`, same as Azorius Blink."""
    gp = DeckGameplan(
        deck_name="ManaGapTestTempo", goals=[], mulligan_keys=set(),
        critical_pieces=set(), mulligan_min_lands=mulligan_min_lands,
        mulligan_max_lands=5,
        mulligan_cmc_profile={"cheap": 2, "medium": 3, "premium": 5},
        archetype="tempo",
    )
    return MulliganDecider(ArchetypeStrategy.MIDRANGE, GoalEngine(gp))


def _control_gameplan_decider(*, mulligan_min_lands: int = 2) -> MulliganDecider:
    """Control archetype — `keep_score_counterspell_at_home` is True, so
    counterspell-tagged spells score at the AT_HOME weight, matching the
    documented MULLIGAN_MIN_HAND_SCORE_7 calibration examples exactly."""
    gp = DeckGameplan(
        deck_name="ManaGapTestControl", goals=[], mulligan_keys=set(),
        critical_pieces=set(), mulligan_min_lands=mulligan_min_lands,
        mulligan_max_lands=5,
        mulligan_cmc_profile={"cheap": 2, "medium": 3, "premium": 5},
        archetype="control",
    )
    return MulliganDecider(ArchetypeStrategy.CONTROL, GoalEngine(gp))


class TestUnreachableCurveHandIsDiscounted:
    """A one-land hand whose spells all sit above that single land is not
    keepable on curve-position value alone — reproduces the Blink case."""

    def test_one_land_hand_with_only_high_cmc_spells_mulls(self):
        """1 land + CMC 3/5/4/4/1 spells: every high-CMC card needs land
        draws the hand cannot guarantee, so the hand falls below the floor.

        Pre-fix: land_score=6, spell_score ignores the gap and clears 24
        (the real Blink hand scored 25.0). Post-fix: each spell whose CMC
        exceeds the 1 land in hand loses MULLIGAN_SPELL_MANA_GAP_PENALTY
        per point of gap, driving the total well under the floor.
        """
        hand = [
            _land(1),
            _spell(2, cmc=3, name="Synthetic Aang"),
            _spell(3, cmc=5, name="Synthetic Riddler"),
            _spell(4, cmc=4, name="Synthetic Witch A"),
            _spell(5, cmc=4, name="Synthetic Witch B"),
            _spell(6, cmc=1, tags={"counterspell"}, name="Synthetic Consign"),
        ]
        decider = _tempo_gameplan_decider(mulligan_min_lands=1)
        keep = decider.decide(hand, cards_in_hand=6)
        assert keep is False, (
            f"A 1-land hand whose spells sit at CMC 3/5/4/4 was kept. "
            f"Reason: '{decider.last_reason}'. Four of five spells cannot "
            f"be cast off the hand's own land — this is the reproduced "
            f"Azorius Blink defect (2026-09-06 diagnostic)."
        )

    def test_gap_penalty_scales_with_the_shortfall_not_flat(self):
        """A spell's discount is proportional to its own CMC-minus-lands
        gap: CMC5 on 1 land loses more than CMC3 on 1 land."""
        hand = [_land(1)]
        decider = _no_gameplan_decider(ArchetypeStrategy.CONTROL)
        near = decider._card_keep_score(_spell(9, cmc=3), hand)
        far = decider._card_keep_score(_spell(9, cmc=5), hand)
        assert far < near, (
            f"CMC5 on 1 land ({far}) should score below CMC3 on 1 land "
            f"({near}) — the gap penalty must scale with the shortfall.")
        # Exact accounting: base curve term is max(0, 5-cmc); the gap term
        # is MULLIGAN_SPELL_MANA_GAP_PENALTY * max(0, cmc - lands_in_hand).
        assert near == pytest.approx(max(0, 5 - 3) - MULLIGAN_SPELL_MANA_GAP_PENALTY * (3 - 1))
        assert far == pytest.approx(max(0, 5 - 5) - MULLIGAN_SPELL_MANA_GAP_PENALTY * (5 - 1))


class TestReachableSpellsAreNotPenalized:
    """A spell whose CMC is already covered by the hand's own lands pays
    no gap penalty — the discount is about UNREACHABLE mana, not CMC alone."""

    def test_spell_at_or_below_land_count_scores_unchanged(self):
        """CMC == lands in hand (gap 0) and CMC < lands in hand (already
        slack) must match the pre-fix curve-only score exactly."""
        hand_3_lands = [_land(1), _land(2), _land(3)]
        decider = _no_gameplan_decider(ArchetypeStrategy.CONTROL)
        at_par = decider._card_keep_score(_spell(9, cmc=3), hand_3_lands)
        under = decider._card_keep_score(_spell(9, cmc=2), hand_3_lands)
        assert at_par == pytest.approx(max(0, 5 - 3))
        assert under == pytest.approx(max(0, 5 - 2))

    def test_modest_single_card_gap_does_not_crater_a_strong_hand(self):
        """A 3-land hand with one CMC-4 bomb among cheap spells still
        keeps — one point of gap on one card is not the Blink pattern
        of every card being unreachable."""
        hand = [
            _land(1), _land(2), _land(3),
            _spell(4, cmc=1, tags={"removal"}),
            _spell(5, cmc=2, tags={"threat"}),
            _spell(6, cmc=4, tags={"threat"}, name="Synthetic Bomb"),
        ]
        decider = _no_gameplan_decider(ArchetypeStrategy.MIDRANGE)
        keep = decider.decide(hand, cards_in_hand=6)
        assert keep is True, (
            f"A 3-land hand with cheap development plus one reachable-soon "
            f"CMC-4 bomb (gap=1) was over-mulliganed. "
            f"Reason: '{decider.last_reason}'. A single point of mana gap "
            f"on one card must not crater an otherwise strong hand.")


class TestExistingScoredGateCalibrationUnaffected:
    """Regression guards: the documented MULLIGAN_MIN_HAND_SCORE_7
    calibration examples (ai/scoring_constants.py) stay keep/mull-correct
    — every spell in them sits within reach of the hand's own lands."""

    def test_three_lands_four_cmc4_counterspells_still_keeps(self):
        """3 lands + 4x CMC-4 counterspells: gap=1 each (4 points total),
        comfortably inside the documented 34-point total's margin over
        the 24.0 floor."""
        hand = [
            _land(1), _land(2), _land(3),
            _spell(4, cmc=4, tags={"counterspell"}, name="Spell Alpha"),
            _spell(5, cmc=4, tags={"counterspell"}, name="Spell Beta"),
            _spell(6, cmc=4, tags={"counterspell"}, name="Spell Gamma"),
            _spell(7, cmc=4, tags={"counterspell"}, name="Spell Delta"),
        ]
        decider = _control_gameplan_decider(mulligan_min_lands=2)
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep is True, (
            f"3 lands + 4 CMC-4 counterspells (gap=1 each) should still "
            f"clear the floor after the mana-gap discount. "
            f"Reason: '{decider.last_reason}'.")

    def test_five_land_two_cmc2_spells_still_keeps(self):
        """5 lands + 2x CMC-2 spells: no gap (CMC below land count)."""
        hand = [
            _land(1), _land(2), _land(3), _land(4), _land(5),
            _spell(6, cmc=2, tags={"threat"}),
            _spell(7, cmc=2, tags={"removal"}),
        ]
        decider = _no_gameplan_decider(ArchetypeStrategy.MIDRANGE)
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep is True, (
            f"5 lands + 2 role-tagged CMC-2 spells (no gap) must still "
            f"keep. Reason: '{decider.last_reason}'.")
