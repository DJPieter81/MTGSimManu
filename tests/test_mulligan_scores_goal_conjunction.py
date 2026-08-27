"""An assembly deck's keep decision must score distance to its goal conjunction.

Replay evidence (docs/diagnostics/2026-08-27_reanimator_pair_root_cause.md,
secondary lever 2 of 2): two incoherent mull-to-5 keeps —

- IR s60500 G3 kept ``[Marsh Flats, Atraxa, Polluted Delta, Ephemerate,
  Flooded Strand]``: a payoff-role card it could never cast, no
  enabler, no way to dig for one.  Never assembled, lost on T6.
- Goryo's s60500 G2 kept a 1-land hand with two reanimation spells and
  no outlet.

Both keeps happened because ``mulligan_always_keep`` (5) auto-keeps
below the typed-path gates: the gameplan's declared role conjunction
(enabler AND payoff buckets in ``mulligan_combo_paths``) was never
consulted at that size.

Mechanic-phrased rules pinned here:

1. A keep holding the goal conjunction outranks a same-quality keep
   without it — coverage of the declared role buckets is a scored
   resource, exactly like lands (the bonus per covered bucket derives
   from MULLIGAN_HAND_LAND_FUNCTIONAL_VALUE).
2. At the always-keep floor, a hand whose conjunction is UNREACHABLE —
   at least one required role bucket uncovered on every declared path
   AND no castable dig card (cantrip/draw/tutor tag or enabler-bucket
   member at or below the gameplan's medium CMC) — mulligans once
   more.  Reachability, not raw coverage, is the bar: a hand that can
   dig keeps.
3. Below the floor the veto never fires (no mull-to-oblivion).

Distinct from both falsified mulligan experiments on file:
RC-3 (2026-07-05, Goryo's) RELAXED the typed 7/6-card path gates to
flat 2-of-3 sets — this change does not touch the 7/6 gates at all.
The 2026-05-09 Storm audit falsified TIGHTENING 7-card keeps to a
role-conjunction pro-bar — this change adds no hard requirement at 7;
it closes the previously ungated always-keep hole at 5 and adds a
comparative scoring term.
"""
from __future__ import annotations

import random

import pytest

from ai.gameplan import create_goal_engine
from ai.mulligan import MulliganDecider
from ai.strategy_profile import ArchetypeStrategy
from engine.cards import CardInstance


def _mk(card_db, name, iid):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    return CardInstance(template=tmpl, owner=0, controller=0,
                        instance_id=iid, zone="hand")


def _reanimator_decider() -> MulliganDecider:
    goal = create_goal_engine("Instant Reanimator")
    return MulliganDecider(ArchetypeStrategy.COMBO, goal)


def _hand(card_db, names):
    return [_mk(card_db, n, i) for i, n in enumerate(names)]


class TestConjunctionCoverageIsScored:
    """Rule 1 — coverage of the declared role conjunction enters the
    hand score."""

    def test_conjunction_covered_keep_outranks_equal_keep_without_it(
            self, card_db):
        """Two hands with identical lands; hand B's spells score at
        least as high card-by-card as hand A's, but only hand A covers
        both role buckets (enabler + payoff).  A must outscore B."""
        dec = _reanimator_decider()
        lands = ["Island", "Swamp", "Watery Grave"]
        hand_a = _hand(card_db, lands + ["Faithful Mending",
                                         "Goryo's Vengeance"])
        hand_b = _hand(card_db, lands + ["Goryo's Vengeance",
                                         "Goryo's Vengeance"])

        def split(hand):
            return ([c for c in hand if not c.template.is_land],
                    [c for c in hand if c.template.is_land])

        spells_a, lands_a = split(hand_a)
        spells_b, lands_b = split(hand_b)

        # Precondition making the comparison honest: B's spells are
        # NOT worse card-by-card than A's under the per-card scorer.
        sum_a = sum(dec._card_keep_score(s, hand_a) for s in spells_a)
        sum_b = sum(dec._card_keep_score(s, hand_b) for s in spells_b)
        assert sum_b >= sum_a, (
            "Fixture invalid: hand B's spells must score >= hand A's "
            "card-by-card so only the conjunction term can separate "
            f"them (A={sum_a}, B={sum_b})."
        )

        score_a = dec._hand_ev_score(hand_a, spells_a, lands_a, 7)
        score_b = dec._hand_ev_score(hand_b, spells_b, lands_b, 7)
        assert score_a > score_b, (
            f"A keep covering the goal conjunction (enabler+payoff) "
            f"must outrank a same-quality keep without it: "
            f"A={score_a:.1f} vs B={score_b:.1f}."
        )


class TestUnreachableConjunctionMullsAtFloor:
    """Rule 2 — at the always-keep floor, a hand that cannot reach the
    conjunction mulligans once more."""

    def _player(self):
        from ai.ev_player import EVPlayer
        return EVPlayer(player_idx=0, deck_name="Instant Reanimator",
                        rng=random.Random(0))

    def test_hand_missing_required_role_mulls_when_unreachable(
            self, card_db):
        """No enabler-bucket card, no payoff-bucket card, no castable
        dig card: every required role is missing and unreachable."""
        ai = self._player()
        hand = _hand(card_db, ["Island", "Swamp", "Ephemerate",
                               "Thoughtseize", "Solitude"])
        keep = ai.decide_mulligan(hand, cards_in_hand=5)
        assert not keep, (
            f"Kept a floor-size hand with every required role bucket "
            f"empty and no dig card. Reason: "
            f"'{getattr(ai, 'mulligan_reason', '')}'."
        )

    def test_hand_with_dig_card_keeps_at_floor(self, card_db):
        """Same shell but a castable enabler-bucket looter makes the
        missing payoff reachable — the always-keep floor applies."""
        ai = self._player()
        hand = _hand(card_db, ["Island", "Swamp", "Ephemerate",
                               "Faithful Mending", "Solitude"])
        keep = ai.decide_mulligan(hand, cards_in_hand=5)
        assert keep, (
            f"Mulled a floor-size hand whose dig card makes the "
            f"conjunction reachable. Reason: "
            f"'{getattr(ai, 'mulligan_reason', '')}'."
        )

    def test_replayed_incoherent_floor_keep_now_mulls(self, card_db):
        """Fixture-carrier regression: the IR s60500 G3 mull-to-5 keep
        (payoff-role 7-drop it could not cast, no enabler, no dig)
        must now mulligan."""
        ai = self._player()
        hand = _hand(card_db, ["Marsh Flats", "Atraxa, Grand Unifier",
                               "Polluted Delta", "Ephemerate",
                               "Flooded Strand"])
        keep = ai.decide_mulligan(hand, cards_in_hand=5)
        assert not keep, (
            f"Replayed incoherent keep was kept again: an uncastable "
            f"payoff-role card with no enabler and no dig path is not "
            f"a keep. Reason: '{getattr(ai, 'mulligan_reason', '')}'."
        )

    def test_below_floor_never_vetoed(self, card_db):
        """Rule 3 — the reachability veto pierces the always-keep
        floor by exactly one hand size: at 4 cards the hand keeps
        unconditionally (no mull-to-oblivion)."""
        ai = self._player()
        hand = _hand(card_db, ["Island", "Swamp", "Ephemerate",
                               "Thoughtseize"])
        keep = ai.decide_mulligan(hand, cards_in_hand=4)
        assert keep, (
            f"A 4-card hand below the always-keep floor must keep. "
            f"Reason: '{getattr(ai, 'mulligan_reason', '')}'."
        )
