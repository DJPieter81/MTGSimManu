"""ETB life-gain projection must read N from oracle text, not flat 3.

# Mechanic the test names

A creature whose ETB ability reads "When this creature enters, you gain
N life" produces N life on resolution, where N is a property of the
printed oracle text (1, 2, 3, 4, 5, 7, ...). The simulator's
`_project_spell` (`ai/ev_evaluator.py:1962-1966`) projected a flat
constant (`REANIMATION_LIFE_GAIN_ESTIMATE = 3`) for every creature
matching `etb_value` + `'gain' in oracle and 'life' in oracle`.

Consequence: a 5-life ETB (Thragtusk-class) projects the same life
delta as a 1-life ETB (small-bird-class) and the same as a "gain life
equal to ..." ETB (devotion-driven drain). The under-projection on
big-life ETB cards systematically undervalues stabilisation creatures
in midrange/control matchups; the over-projection on small-life ETBs
inflates their value as life-stabilisation tools.

# Class size

≥ 100 Modern cards have an ETB self-trigger of the form
"when this creature enters, you gain N life":
  - N=1  : 18 cards (Ancestor's Chosen, Angel of Renewal, ...)
  - N=2  : 40 cards (Archway Angel, Aven Gagglemaster, ...)
  - N=3  : 41 cards (Angel of Mercy, Phlage's "gain 3 life", ...)
  - N=4  : 14 cards (Azorius Herald, Beza, ...)
  - N=5  : 10 cards (Thragtusk, Arborback Stomper, ...)
  - N=7+ : 2 cards (Pelakka Wurm, Saruli Gatekeepers)

Plus a separate ~26-card cohort of "gain life equal to" alternative
phrasings (Gray Merchant of Asphodel, Archon of Redemption, ...) that
under the literal-keyword detection get the same flat 3 even when
their actual life gain depends on board state. Phlage and Archon of
Cruelty are members of the active Modern meta directly hit by this
projection (Boros Energy, Domain Zoo, Goryo's Vengeance, 4c Omnath).

# The bug, expressed without naming a card

If two ETB-trigger creatures with `etb_value` tag have oracle text
"... gain 5 life" and "... gain 1 life" respectively, the projected
my_life delta after `_project_spell` MUST differ. Pre-fix it is
identical (flat 3). A separate alternative-phrasing assertion: a
card whose oracle reads "you gain life equal to ..." MUST project a
non-zero life delta — its mechanic gains life on ETB even though the
literal phrase "gain N life" does not appear.

This test is the rule-phrased red-pre-fix half of the
oracle-pattern projection blindspot audit
(`docs/design/2026-05-10_oracle_pattern_projection_blindspot_audit.md`).
"""
from __future__ import annotations

import pytest

from ai.ev_evaluator import EVSnapshot, _project_spell
from engine.cards import CardInstance


def _mid_snap() -> EVSnapshot:
    """Mid-game baseline for life-gain projection — no clock contributions
    from creatures, plenty of mana to cast.  The only field this test
    cares about post-projection is `my_life`."""
    return EVSnapshot(
        my_life=20, opp_life=20,
        my_hand_size=5, opp_hand_size=5,
        my_mana=10, opp_mana=0,
        my_total_lands=10, opp_total_lands=0,
        turn_number=5,
    )


def _project_life_delta(card_db, name: str) -> int:
    """Cast `name` from hand and return the projected my_life delta."""
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=1, zone="hand",
    )
    snap = _mid_snap()
    proj = _project_spell(card, snap, dk=None, game=None, player_idx=0)
    return proj.my_life - snap.my_life


class TestEtbLifeGainProjectsActualAmountNotFlatEstimate:
    """The projection must read the printed N from oracle text — not
    fold every printed amount onto a single shared constant."""

    def test_small_and_large_etb_life_gain_project_distinct_deltas(
            self, card_db):
        """Two ETB cards, oracle reading "gain 1 life" and "gain 5 life"
        respectively, must produce DIFFERENT projected my_life deltas.

        Pre-fix both fold onto `REANIMATION_LIFE_GAIN_ESTIMATE = 3`,
        so the deltas are equal — that's the bug. Post-fix, the
        small-gain card's delta < the large-gain card's delta because
        the projection extracts the printed N. The test names the
        rule, not the cards: any two cards in the printed pool whose
        oracle ETB-life-gain N values differ must project different
        deltas."""
        # 'Ancestor's Chosen': "When this creature enters, you gain
        # 1 life for each ..." (the N=1 bucket from the audit table).
        # 'Thragtusk': "When this creature enters, you gain 5 life"
        # (the N=5 bucket).
        small_delta = _project_life_delta(card_db, "Ancestor's Chosen")
        large_delta = _project_life_delta(card_db, "Thragtusk")

        # Both must be non-zero (the projection fires for both — the
        # bug is not that it doesn't fire, but that it projects the
        # same flat amount for distinct printed amounts).
        assert small_delta > 0, (
            f"Small ETB-life-gain creature projected delta={small_delta}; "
            f"the etb_value branch of `_project_spell` should fire and "
            f"contribute positive life. If this assertion fails, the "
            f"test scaffold is broken (not the rule)."
        )
        assert large_delta > 0, (
            f"Large ETB-life-gain creature projected delta={large_delta}; "
            f"the etb_value branch of `_project_spell` should fire and "
            f"contribute positive life. If this assertion fails, the "
            f"test scaffold is broken (not the rule)."
        )

        # The rule the test names: distinct printed N values produce
        # distinct projected deltas. Pre-fix this is FALSE (both fold
        # onto a flat constant); post-fix it is TRUE (deltas differ).
        assert small_delta != large_delta, (
            f"Two ETB-life-gain creatures with distinct printed life "
            f"amounts ('gain 1 life' vs 'gain 5 life') projected the "
            f"SAME my_life delta ({small_delta} == {large_delta}). The "
            f"projection in `ai/ev_evaluator.py:1962-1966` collapses all "
            f"printed amounts onto `REANIMATION_LIFE_GAIN_ESTIMATE`, so "
            f"a 5-life Thragtusk-class ETB scores no better than a "
            f"1-life small-bird-class ETB. Replace the flat constant "
            f"with a parsed N from oracle text — the same shape PR #334 "
            f"used for impulse-draw."
        )
        # Strict ordering: the larger printed N MUST project the larger
        # delta, otherwise the parser is reversed.
        assert large_delta > small_delta, (
            f"Large-gain delta ({large_delta}) was not greater than "
            f"small-gain delta ({small_delta}). The parsed extractor "
            f"must read N monotonically: a higher printed amount → a "
            f"higher projected my_life delta."
        )

    def test_alternative_phrasing_gain_life_equal_to_projects_nonzero(
            self, card_db):
        """Alternative-phrasing case from the audit doc: the oracle
        reads "you gain life equal to ..." instead of "gain N life".

        Pre-fix, this card matches the literal-keyword test ('gain' +
        'life' both appear in the oracle), so the flat constant fires
        and projects 3 — the test passes pre-fix on this assertion
        alone.  Its purpose is to lock in that the parsed extractor
        does NOT regress to zero on the alternative phrasing once the
        literal-N regex replaces the flat constant.  Post-fix, the
        delta must remain positive (the mechanic gains life on ETB),
        not zero.

        This anchor mirrors the impulse-draw fix's coverage: the
        unified extractor must cover both the printed-N case and the
        printed-as-formula case, not silently drop the latter to 0."""
        delta = _project_life_delta(card_db, "Gray Merchant of Asphodel")
        assert delta > 0, (
            f"Gray Merchant-class ETB ('you gain life equal to ...') "
            f"projected delta={delta} — the alternative-phrasing case "
            f"in the oracle-pattern projection blindspot audit. The "
            f"mechanic gains life on ETB; the projection must credit a "
            f"non-zero life delta. If the unified parser only matches "
            f"the literal '(gain) N (life)' pattern, alternative "
            f"phrasings drop to 0 and the AI under-values these "
            f"creatures. The fix must include this phrasing in the "
            f"extractor's coverage."
        )
