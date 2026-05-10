"""Energy ETB projection must read actual amount from oracle text.

Distillation of the projection-blindspot class identified in the
2026-05-10 audit (PR #334's design doc): a card-effect projection in
`ai/ev_evaluator.py` that flags the mechanic by tag and assigns a flat
constant under-credits some printings of the same effect and
over-credits others. Same shape as the impulse-draw bug shipped in
PR #334.

Concrete site: `_project_spell` in `ai/ev_evaluator.py`. The energy
branch reads `if 'energy' in tags` and increments
`projected.my_energy += ENERGY_PRODUCED_ESTIMATE` (flat 2). Real
printings range over {1, 2, 3, 4, 6, ...} energy counters per ETB.

# Mechanic the test names

Two ETB-energy clauses with different stated counts must produce
different projected `my_energy` deltas. The flat estimate violates this
by collapsing every printing onto the same number.

Class size in Modern: ~50 cards print "When ~ enters, you get N {E}"
with N ranging 1..6 (Aether* family of token-makers, Aetherflux Reservoir-
adjacent, Aetherworks Marvel, Static Prison, Galvanic Discharge, Guide of
Souls' triggered ETB-of-other, etc.). Hits the >= 10-card class threshold
required by the abstraction contract.

The test does not name a card. It constructs two synthetic templates
with different oracle texts and asserts the projected energy delta
differs. The rule is: `delta(get N {E}) != delta(get M {E})` for
N != M.
"""
from __future__ import annotations

import pytest

from ai.ev_evaluator import EVSnapshot, _project_spell
from engine.cards import CardInstance, CardTemplate, CardType
from engine.mana import ManaCost


def _baseline_snap() -> EVSnapshot:
    """Clean mid-game snapshot so projection deltas are pure bonuses."""
    return EVSnapshot(
        my_life=20, opp_life=20,
        my_power=0, opp_power=0,
        my_toughness=0, opp_toughness=0,
        my_creature_count=0, opp_creature_count=0,
        my_hand_size=5, opp_hand_size=5,
        my_mana=3, opp_mana=3,
        my_total_lands=3, opp_total_lands=3,
        turn_number=3,
    )


def _synth_energy_template(*, oracle_text: str) -> CardTemplate:
    """Synthetic noncreature spell whose ETB clause prints the given
    energy phrasing. Carries `tags={'energy'}` so the existing
    projection gate fires; the test then asserts the projected delta
    reflects the actual N from the oracle text rather than a flat
    constant."""
    return CardTemplate(
        name="Synth Energy Trigger",
        card_types=[CardType.SORCERY],
        mana_cost=ManaCost(generic=1),
        oracle_text=oracle_text,
        tags={'energy'},
    )


def _instance_in_hand(template: CardTemplate) -> CardInstance:
    return CardInstance(
        template=template, owner=0, controller=0,
        instance_id=1, zone="hand",
    )


class TestEnergyEtbProjectsActualAmount:
    """Two ETB-energy clauses with different stated counts must produce
    different projected `my_energy` deltas."""

    def test_one_energy_vs_three_energy_project_different_deltas(self):
        """Distillation: a flat constant collapses every printing onto
        the same delta. After the fix, the spell that prints "you get
        {E}" (1 counter) must project a smaller energy bump than the
        spell that prints "you get {E}{E}{E}" (3 counters). The exact
        value is left to the parsed extractor; what the rule encodes
        is `delta_one < delta_three`."""
        small_text = "When this enters, you get {E}."
        big_text = "When this enters, you get {E}{E}{E}."

        small = _instance_in_hand(_synth_energy_template(
            oracle_text=small_text))
        big = _instance_in_hand(_synth_energy_template(
            oracle_text=big_text))

        snap = _baseline_snap()
        small_proj = _project_spell(small, snap)
        big_proj = _project_spell(big, snap)

        small_delta = small_proj.my_energy - snap.my_energy
        big_delta = big_proj.my_energy - snap.my_energy

        assert small_delta < big_delta, (
            f"Energy ETB projection collapsed onto a flat constant: "
            f"oracle 'get {{E}}' projected delta={small_delta}, "
            f"oracle 'get {{E}}{{E}}{{E}}' projected delta={big_delta}. "
            f"After the fix the parsed extractor must read the actual "
            f"count from the oracle text — different printings of the "
            f"same mechanic must yield different projection deltas."
        )

    def test_one_energy_delta_matches_oracle_count(self):
        """Anchor: the spell that prints 'you get {E}' projects exactly
        +1 energy. Pre-fix the flat constant projects +2 (over-credit);
        post-fix the parsed extractor projects +1.

        This anchors the lower bound of the parse_energy_production
        contract and prevents a regression that collapses back to a
        flat constant."""
        small = _instance_in_hand(_synth_energy_template(
            oracle_text="When this enters, you get {E}."))
        snap = _baseline_snap()
        proj = _project_spell(small, snap)
        delta = proj.my_energy - snap.my_energy

        assert delta == 1, (
            f"oracle 'get {{E}}' projected energy delta={delta}, "
            f"expected +1 (one counter). The flat-constant pre-fix "
            f"value was +2 — that over-credits single-energy printings "
            f"and is the bug under test."
        )

    def test_three_energy_delta_matches_oracle_count(self):
        """Anchor: the spell that prints 'you get {E}{E}{E}' projects
        exactly +3 energy. Pre-fix the flat constant projects +2
        (under-credit); post-fix the parsed extractor projects +3."""
        big = _instance_in_hand(_synth_energy_template(
            oracle_text="When this enters, you get {E}{E}{E}."))
        snap = _baseline_snap()
        proj = _project_spell(big, snap)
        delta = proj.my_energy - snap.my_energy

        assert delta == 3, (
            f"oracle 'get {{E}}{{E}}{{E}}' projected energy delta="
            f"{delta}, expected +3 (three counters). The flat-constant "
            f"pre-fix value was +2 — that under-credits the printing "
            f"and is the bug under test."
        )
