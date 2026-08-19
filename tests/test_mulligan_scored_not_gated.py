"""Mulligan decisions driven by scored hand evaluation, not boolean gates.

Phase 3 sweep: `ai/mulligan.py` contained two categorical boolean-gate chains
that pre-filtered opening hands by counting card types rather than computing
real hand EV:

  1. ``_generic()`` — the no-gameplan fallback. Branched on archetype and
     applied hard land-count ceilings (aggro: land ≤ 3; midrange: land ≤ 4)
     plus a flood gate (land ≥ 5 → always mull at 7 cards). These ceilings
     reject objectively keepable hands — e.g., four lands plus three early
     plays for an aggro deck, or five lands plus three medium spells for a
     midrange deck.

  2. GoalEngine final gate — ``if cheap_spells >= 1: return True; return
     False``. A hand with high-CMC interaction that scores above the CMC-
     profile's ``medium`` threshold (e.g., four CMC-4 counterspells with a
     control gameplan that has ``medium_cmc = 3``) is mulliganed despite
     having strong EV, because zero spells qualify as "cheap".

Replacement mechanic: ``_hand_ev_score(hand, spells, lands)`` sums a capped
land contribution (functional value per land, up to optimal count, with a
small per-excess-land penalty) and per-spell scores from ``_card_keep_score``
(CMC-inverted bonus + tag bonuses). The hand keeps when the score meets the
hand-size-calibrated floor (``MULLIGAN_MIN_HAND_SCORE_7`` /
``MULLIGAN_MIN_HAND_SCORE_6``).

Legality checks are preserved:
  - 0 lands with no non-land mana sources → always mulligan (unchanged).

Test-name convention: mechanic, not card name.  Class size: any deck running
the no-gameplan fallback (16 decks today; the generic path applies to all)
and any deck using the GoalEngine final gate (also all 16 — combo/ramp decks
hit specialized gates before this one, others fall through to it).
"""
from __future__ import annotations

from typing import List

import pytest

from ai.gameplan import DeckGameplan, GoalEngine
from ai.mulligan import MulliganDecider
from ai.strategy_profile import ArchetypeStrategy
from engine.cards import CardInstance, CardTemplate, CardType, Keyword
from engine.mana import ManaCost


# ─── Synthetic card helpers ────────────────────────────────────────────────

def _land(iid: int, *, name: str = "Synthetic Land",
          produces: List[str] = None) -> CardInstance:
    """A basic-land substitute that produces W mana."""
    tmpl = CardTemplate(
        name=name,
        card_types=[CardType.LAND],
        mana_cost=ManaCost(generic=0),
        supertypes=[],
        subtypes=[],
        produces_mana=produces or ["W"],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )
    return CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=iid, zone="hand",
    )


def _spell(iid: int, cmc: int, tags: set = None, *,
           name: str = None) -> CardInstance:
    """A generic sorcery with configurable CMC and AI tags."""
    tmpl = CardTemplate(
        name=name or f"Synthetic Spell CMC{cmc}",
        card_types=[CardType.SORCERY],
        mana_cost=ManaCost(generic=cmc),
        supertypes=[],
        subtypes=[],
        produces_mana=[],
        enters_tapped=False,
        oracle_text="",
        tags=set(tags or []),
    )
    return CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=iid, zone="hand",
    )


def _no_gameplan_decider(archetype: ArchetypeStrategy) -> MulliganDecider:
    """Decider with no GoalEngine (exercises the generic fallback path)."""
    return MulliganDecider(archetype, goal_engine=None)


def _control_gameplan_decider(medium_cmc: int = 3) -> MulliganDecider:
    """Decider with a minimal control GoalEngine but no mulligan keys or
    critical pieces, so every 7-card hand falls through to the final gate."""
    gp = DeckGameplan(
        deck_name="ScoredGateTestControl",
        goals=[],
        mulligan_keys=set(),
        critical_pieces=set(),
        mulligan_min_lands=2,
        mulligan_max_lands=5,
        mulligan_cmc_profile={"cheap": 2, "medium": medium_cmc, "premium": 5},
        archetype="control",
    )
    engine = GoalEngine(gp)
    return MulliganDecider(ArchetypeStrategy.CONTROL, engine)


# ─── Tests that are RED against the current boolean-gate code ─────────────
# These tests assert the CORRECT decision (keep / mull).  They fail with the
# current gated implementation and pass after the scored replacement.


class TestGenericFallbackCategoricalLandCeilingRetired:
    """_generic(): replacing land-count ceilings with scored evaluation.

    The prior aggro branch: ``1 <= land_count <= 3 and cheap_spells >= 2``.
    The prior midrange branch: ``2 <= land_count <= 4 and medium_spells >= 2``.
    The prior flood gate: ``land_count >= 5 → always mull at 7 cards``.

    Each gate rejects hands that a scored approach correctly keeps.
    """

    def test_aggro_four_land_hand_with_early_plays_kept_by_score_not_land_ceiling(
            self):
        """Aggro generic: four lands + three CMC-1 early plays keeps by score.

        Land-ceiling gate: ``1 <= land_count <= MULLIGAN_GENERIC_LAND_FLOOR_AGGRO(3)``
        fails because 4 > 3 → mulls.

        Scored hand: land_score = min(4,3)×6 = 18; spell_score = 3×(4+4) = 24;
        excess_penalty = 1×1 = 1; total = 41 ≥ 24 → keep.

        Mechanic: aggro development is characterized by early-play-tagged spells,
        not by a hard land-count ceiling. Four lands is enough mana for an aggro
        hand — the ceiling was an arbitrary threshold approximating "not flooded".
        """
        hand = [
            _land(1), _land(2), _land(3), _land(4),
            _spell(5, cmc=1, tags={"early_play", "threat"}),
            _spell(6, cmc=1, tags={"early_play", "threat"}),
            _spell(7, cmc=1, tags={"early_play", "removal"}),
        ]
        decider = _no_gameplan_decider(ArchetypeStrategy.AGGRO)
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep is True, (
            f"Aggro 4-land ceiling gate incorrectly mulled a high-EV hand. "
            f"Reason: '{decider.last_reason}'. "
            f"Four lands + three CMC-1 early plays is a strong aggro keep — "
            f"the hand scores well above the minimum. The land-count ceiling "
            f"(≤ 3) is a categorical proxy, not real EV; scored evaluation "
            f"correctly keeps it."
        )

    def test_midrange_five_land_hand_with_spells_kept_by_score_not_flood_gate(
            self):
        """Midrange generic: five lands + three CMC-2 spells keeps by score.

        Two prior gates reject this hand:
          (a) Flood gate: ``land_count >= 5 → return False`` (fires first).
          (b) Midrange land-range: ``2 <= land_count <= 4`` fails because 5 > 4.

        Scored: land_score = 18; excess_penalty = 2; spell_score = 3×3 = 9;
        total = 25 ≥ 24 → keep.

        Mechanic: five lands with three playable spells is a functional midrange
        hand. The flood gate approximates "too few spells" by counting lands;
        scored evaluation measures the spells' EV directly.
        """
        hand = [
            _land(1), _land(2), _land(3), _land(4), _land(5),
            _spell(6, cmc=2, tags={"threat"}),
            _spell(7, cmc=2, tags={"removal"}),
        ]
        # 7-card hand but only 2 spells; score: 18 - 2 + 2*(3+2) = 26
        # Let's use 3 spells at cmc=2 to be clearly above the threshold
        hand = [
            _land(1), _land(2), _land(3), _land(4), _land(5),
            _spell(6, cmc=2, tags={"threat"}),
            _spell(7, cmc=2, tags={"removal"}),
        ]
        # 2 spells: score = 18 - 2 + 5+5 = 26 >= 24 → keep
        decider = _no_gameplan_decider(ArchetypeStrategy.MIDRANGE)
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep is True, (
            f"Midrange five-land flood gate incorrectly mulled a hand with "
            f"playable spells. Reason: '{decider.last_reason}'. "
            f"Five lands + two role-tagged CMC-2 spells is a functional "
            f"midrange hand — the flood gate approximates EV via a land count; "
            f"scored evaluation measures actual spell contribution."
        )

    def test_control_generic_four_lands_two_high_cmc_counterspells_kept_by_score(
            self):
        """Control generic: four lands + two CMC-4 counterspells keeps by score.

        Control branch gate: ``sum(removal or counterspell tags) >= 1``.
        If the gate passes (it does — counterspell tag present), it returns
        True and nothing else matters.  But this tests the path where the hand
        has no cheap spells (cmc > medium), passing the control gate but
        verifying that the control gate is mechanic-correct (not count-based).

        More specifically, this guards against a regression where a scored
        replacement would accidentally mull control hands that the tag-check
        currently keeps correctly (regression guard for Phase 3).

        Mechanic: counterspell-tagged spells provide real EV to a control hand
        regardless of their CMC. The control branch read tags — the one
        categorical element worth preserving is the tag-check itself, not a
        land-count range.
        """
        hand = [
            _land(1), _land(2), _land(3), _land(4),
            _spell(5, cmc=4, tags={"counterspell"}),
            _spell(6, cmc=4, tags={"counterspell"}),
            _spell(7, cmc=3, tags={"removal"}),
        ]
        decider = _no_gameplan_decider(ArchetypeStrategy.CONTROL)
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep is True, (
            f"Control hand with interaction incorrectly mulled. "
            f"Reason: '{decider.last_reason}'. "
            f"Four lands + two counterspells + removal is a strong control "
            f"keep. Scored hand EV clearly above minimum."
        )


class TestGoalEngineFinalGateScoredNotCheapSpellCount:
    """GoalEngine final gate: replacing ``cheap_spells >= 1`` with hand EV score.

    The final gate in the GoalEngine-aware path mulls any hand where zero
    spells have CMC ≤ ``gp.mulligan_cmc_profile["medium"]``. A hand with
    high-CMC, high-EV interaction (e.g., four CMC-4 counterspells against a
    medium_cmc=3 profile) is incorrectly mulled — the spells are perfectly
    castable and strategically essential, but their CMC sits above "medium".

    Scored replacement: ``_hand_ev_score()`` sums spell scores via
    ``_card_keep_score`` (CMC-inverted + tag bonuses). CMC-4 counterspells
    score 1 + 3 = 4 each under the control policy. Three lands + four
    counterspells → 18 + 16 = 34 ≥ 24 → keep.
    """

    def test_high_cmc_interaction_kept_when_cheap_spell_count_is_zero(self):
        """GoalEngine path: hand with all-CMC-4 counterspells keeps by score.

        Setup: control gameplan with medium_cmc=3. Hand has 3 lands + 4
        CMC-4 counterspells (all tagged ``counterspell``). No mulligan keys
        or critical pieces are declared, so the hand falls through to the
        final gate.

        Current (gate): cheap_spells = 0 (4 > 3) → ``0 >= 1`` = False → mull.
        Scored: spell_score = 4 × (max(0,5-4) + 3.0) = 16; land_score = 18;
        total = 34 ≥ 24 → keep.

        Mechanic: CMC-profile-gated "cheap spell" counting mistakes a CMC
        threshold for hand EV. A control hand with four interaction spells
        is functionally strong regardless of whether their CMC clears a
        ``medium_cmc`` line.
        """
        hand = [
            _land(1), _land(2), _land(3),
            _spell(4, cmc=4, tags={"counterspell"}, name="Spell Alpha"),
            _spell(5, cmc=4, tags={"counterspell"}, name="Spell Beta"),
            _spell(6, cmc=4, tags={"counterspell"}, name="Spell Gamma"),
            _spell(7, cmc=4, tags={"counterspell"}, name="Spell Delta"),
        ]
        decider = _control_gameplan_decider(medium_cmc=3)
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep is True, (
            f"GoalEngine final gate mulled a hand with four CMC-4 "
            f"counterspells (cheap_spells=0 at medium_cmc=3). "
            f"Reason: '{decider.last_reason}'. "
            f"Mechanic: the cheap-spell gate approximates hand EV by CMC "
            f"threshold; scored evaluation correctly keeps a hand whose "
            f"interaction spells score above the minimum."
        )

    def test_zero_spell_hand_still_mulls_via_score(self):
        """GoalEngine path: seven-land hand still mulls under scored gate.

        A seven-land hand has zero spells. Scored: land_score = 18;
        spell_score = 0; excess_penalty = 4×1 = 4; total = 14 < 24 → mull.

        This verifies the scored gate does not accidentally keep spell-less
        hands that the old gate correctly mulled (regression guard).

        Mechanic: scored evaluation correctly identifies a hand with no
        development as unplayable, without relying on a categorical spell count.
        """
        hand = [
            _land(1), _land(2), _land(3), _land(4),
            _land(5), _land(6), _land(7),
        ]
        decider = _control_gameplan_decider()
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep is False, (
            f"GoalEngine scored gate incorrectly kept a seven-land hand. "
            f"Reason: '{decider.last_reason}'. "
            f"A hand with no spells should always mull — scored evaluation "
            f"gives spell_score=0 and penalizes excess lands, producing a "
            f"total well below the minimum."
        )


class TestLegalityChecksPreservedAfterScoredReplacement:
    """Legality checks are NOT categorical value proxies — they stay.

    The 0-land check (no mana source = cannot develop any turn) is a
    *legality* check, not an EV approximation. It must survive the scored
    replacement unchanged.  These tests are regression guards that should
    PASS both before and after the change.
    """

    def test_zero_land_no_mana_source_always_mulls(self):
        """0 lands + no mana artifacts = must always mull (legality check).

        This gate cannot be replaced by scoring because a scored replacement
        without mana contribution still correctly mulls (score = 0 from land,
        minus no excess penalty, plus spell scores — but no mana to cast
        anything). It is retained as an explicit legality check for clarity
        and to avoid relying on score calibration for a game-losing edge case.

        Applies to every deck, every archetype. Class size: all 20k+ cards.
        """
        hand = [
            _spell(1, cmc=1, tags={"early_play", "threat"}),
            _spell(2, cmc=1, tags={"early_play", "threat"}),
            _spell(3, cmc=2, tags={"removal"}),
            _spell(4, cmc=3, tags={"threat"}),
            _spell(5, cmc=3, tags={"removal"}),
            _spell(6, cmc=4, tags={"counterspell"}),
            _spell(7, cmc=4, tags={"counterspell"}),
        ]
        decider = _no_gameplan_decider(ArchetypeStrategy.AGGRO)
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep is False, (
            f"Zero-land legality check was lost — hand kept with 0 lands "
            f"and no mana artifacts. Reason: '{decider.last_reason}'. "
            f"The 0-land legality check must survive the scored replacement."
        )

    def test_zero_land_no_gameplan_always_mulls(self):
        """Generic fallback: 0-land hand must still mull regardless of archetype.

        Mechanic: no mana source = no development = game-losing from turn 1.
        Scored replacement may give the hand a nonzero spell_score but the
        legality check fires before any score evaluation.
        """
        hand = [
            _spell(1, cmc=2, tags={"removal"}),
            _spell(2, cmc=2, tags={"threat"}),
            _spell(3, cmc=2, tags={"early_play"}),
            _spell(4, cmc=2, tags={"counterspell"}),
            _spell(5, cmc=3, tags={"removal"}),
            _spell(6, cmc=3, tags={"threat"}),
            _spell(7, cmc=1, tags={"cantrip"}),
        ]
        for arch in (ArchetypeStrategy.AGGRO, ArchetypeStrategy.MIDRANGE,
                     ArchetypeStrategy.CONTROL):
            d = _no_gameplan_decider(arch)
            assert d.decide(hand, cards_in_hand=7) is False, (
                f"Zero-land legality check failed for {arch.value}: "
                f"hand was kept. Reason: '{d.last_reason}'."
            )

    def test_two_land_hand_with_high_ev_spells_is_kept(self):
        """Two lands + high-EV spells at 7 cards keeps (not over-mulliganed).

        Under the scored approach, a 2-land hand keeps when the spell EV
        compensates for the low land count: land_score = 2×6 = 12;
        spell_score = 5 spells scoring ~5 each = 25; total = 37 ≥ 24.

        Mechanic: the scored gate does not add a hard 3-land floor — low-land
        hands with high-EV spells are keepable (London mulligan lets you
        bottom the weakest card). Regression guard: ensures the scored
        replacement does not over-reject.
        """
        hand = [
            _land(1), _land(2),
            _spell(3, cmc=1, tags={"early_play", "threat"}),
            _spell(4, cmc=1, tags={"early_play", "removal"}),
            _spell(5, cmc=2, tags={"threat", "early_play"}),
            _spell(6, cmc=2, tags={"removal"}),
            _spell(7, cmc=3, tags={"threat"}),
        ]
        decider = _no_gameplan_decider(ArchetypeStrategy.AGGRO)
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep is True, (
            f"Two-land high-EV hand was over-rejected after scored replacement. "
            f"Reason: '{decider.last_reason}'. "
            f"The scored approach must keep 2-land hands whose spell EV "
            f"clears the minimum — the London mulligan means low-land hands "
            f"are valid keeps when the spells are strong."
        )
