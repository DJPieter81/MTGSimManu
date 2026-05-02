"""Mulligan must reject combo hands missing required role-buckets.

Reference: 5-seed --bo3 sweep of Goryo's Vengeance vs Affinity (seeds
60100..60500).  Goryo's went 0-of-many because the existing
``mulligan_combo_sets`` predicate is *flat* — it counts a hand with
{Faithful Mending, Unburial Rites} as 2-of-3 toward the path
{Faithful Mending, Unburial Rites, Griselbrand} and keeps it.  But
both Mending and Rites are role-equivalent: an enabler and a
reanimator with no fatty to reanimate is structurally unwinnable.

The class-size of this bug is every combo deck whose combo path has
heterogeneous typed roles — enabler / payoff / target — where flat
intersection counts cannot tell whether the missing piece is the one
that *can* be drawn into (target) versus the one that *can't*
(enabler, since you need it in hand to even start the chain).  Living
End and Storm don't have this shape (cyclers are interchangeable;
rituals are interchangeable) so they stay on flat ``combo_sets``.
Goryo's, Through-the-Breach, and any classical reanimator do.

The fix introduces a new optional gameplan field
``mulligan_combo_paths``.  Each path is a list of *role buckets*; the
hand satisfies the path when every bucket has at least one card in
hand.  Buckets within a path are AND'd, paths are OR'd.  Decks
without ``mulligan_combo_paths`` declared keep the existing flat-set
behaviour unchanged.

Also closes the color-check gating gap: today the color-soundness
check at ``mulligan.py:169`` is gated to ``cards_in_hand >= 7``.  But
the engine calls ``decide()`` once per virtual hand size from 7 down
to 5, and after the first mulligan the virtual size is 6 — the same
fresh seven cards now skip the color check.  Replay seed 60100 G1:
fresh 7 of {Mending×3, Rites, Solitude, Inquisition, Swamp} kept at
virtual size 6 because the color gap (no U for Mending) was never
verified.  Fix runs the color check at every size where it's
meaningful (>=5).
"""
from __future__ import annotations

import pytest

from ai.gameplan import create_goal_engine
from ai.mulligan import MulliganDecider
from ai.strategy_profile import ArchetypeStrategy
from engine.card_database import CardDatabase
from engine.cards import CardInstance


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _hand_card(card_db, name: str, iid: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    return CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=iid, zone="hand",
    )


def _goryos_decider() -> MulliganDecider:
    goal = create_goal_engine("Goryo's Vengeance")
    return MulliganDecider(ArchetypeStrategy.COMBO, goal)


class TestMulliganTypedComboPathsRequireEnablerAndPayoff:
    """A combo path is a list of role buckets (enabler, payoff, ...).
    A hand keeps when at least one declared path has at least one
    card from EVERY bucket.  Two enablers without a payoff, or two
    payoffs without an enabler, must fail the keep predicate."""

    def test_goryos_mulls_payoffs_only_no_enabler_at_7(self, card_db):
        """Hand reproduces replay seed 60400 G1 (Goryo's P1):
        ``[Goryo's Vengeance, Archon of Cruelty, Solitude,
           Flooded Strand, Flooded Strand, Unburial Rites]`` — has
        two reanimators (Goryo's and Rites) and a target (Archon),
        but no Faithful Mending.  Without an enabler in hand, no card
        in this opener can put Archon into the graveyard:
          - Solitude evoke pitches a *white* creature (Archon is
            black).
          - Goryo's targets a legendary creature card already in GY.
          - Rites flashback also requires the creature in GY.
        The deck cannot start the chain and stalls until it dies.
        Today the existing flat-set check passes ({Goryo's, Archon} =
        2/3 of {Faithful Mending, Goryo's Vengeance, Archon of
        Cruelty}) and the hand is kept.  The fix declares an enabler
        bucket alongside the payoff bucket; this hand fills the
        payoff bucket but not the enabler bucket, so it must mull.
        """
        # Synthesize a 7-card variant of the replay shape (the actual
        # replay was a 6-card; we test the class-of-bug at 7 first
        # because that's where ``cards_in_hand >= 7`` checks fire).
        hand = [
            _hand_card(card_db, "Watery Grave", iid=1),
            _hand_card(card_db, "Godless Shrine", iid=2),
            _hand_card(card_db, "Concealed Courtyard", iid=3),
            _hand_card(card_db, "Goryo's Vengeance", iid=4),
            _hand_card(card_db, "Archon of Cruelty", iid=5),
            _hand_card(card_db, "Unburial Rites", iid=6),
            _hand_card(card_db, "Solitude", iid=7),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=7)
        assert not keep, (
            f"Goryo's kept a 7 with payoffs+target but no Faithful "
            f"Mending (the discard outlet).  Reason: "
            f"'{decider.last_reason}'.  This hand cannot get Archon "
            f"into the graveyard — the path's enabler bucket is "
            f"empty.  Mull to 6 and look for a real combo opener."
        )

    def test_goryos_mulls_enablers_only_no_payoff_at_7(self, card_db):
        """Hand reproduces replay seed 60300 G2 shape: three Faithful
        Mendings + lands + disruption, but no Goryo's Vengeance and
        no Unburial Rites.  Mending discards 2 / draws 2 — without a
        payoff in hand, the deck spends turns digging without ever
        reaching a reanimator before Affinity's clock kills it.  Today
        flat-set keeps because {Faithful Mending} = 1/3 of every
        combo set is *enough* at 7 cards under the existing 2-of-3
        rule for sets where Mending is repeated... but the actual
        rule keeps it because of ``mulligan_keys`` matching Mending.
        Either way: no payoff bucket coverage = mull."""
        hand = [
            _hand_card(card_db, "Marsh Flats", iid=11),
            _hand_card(card_db, "Hallowed Fountain", iid=12),
            _hand_card(card_db, "Concealed Courtyard", iid=13),
            _hand_card(card_db, "Faithful Mending", iid=14),
            _hand_card(card_db, "Faithful Mending", iid=15),
            _hand_card(card_db, "Thoughtseize", iid=16),
            _hand_card(card_db, "Inquisition of Kozilek", iid=17),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=7)
        assert not keep, (
            f"Goryo's kept a 7 with enablers but no payoff "
            f"(no Goryo's Vengeance, no Unburial Rites).  Reason: "
            f"'{decider.last_reason}'.  Mending alone digs but "
            f"never reaches a reanimator in time vs a fast clock."
        )

    def test_goryos_keeps_enabler_plus_payoff_at_7(self, card_db):
        """Regression: a 7 with Mending + Goryo's + lands + filler
        must still keep — this is the canonical "dig for target with
        the cantrip" combo opener.  The typed-paths fix must not
        over-tighten and reject hands the author already affirmed.
        Mirrors test_goryos_keeps_two_of_three_combo_pieces in the
        sibling file."""
        hand = [
            _hand_card(card_db, "Watery Grave", iid=21),
            _hand_card(card_db, "Godless Shrine", iid=22),
            _hand_card(card_db, "Blood Crypt", iid=23),
            _hand_card(card_db, "Faithful Mending", iid=24),
            _hand_card(card_db, "Goryo's Vengeance", iid=25),
            _hand_card(card_db, "Thoughtseize", iid=26),
            _hand_card(card_db, "Inquisition of Kozilek", iid=27),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep, (
            f"Goryo's regression: Mending + Goryo's + dual lands "
            f"hand was mulled.  Reason: '{decider.last_reason}'.  "
            f"The typed-paths fix must preserve the canonical combo "
            f"opener — enabler covers its bucket, payoff covers "
            f"its bucket, target is dig-able with Mending."
        )

    def test_goryos_mulls_payoff_only_at_6(self, card_db):
        """Reproduces replay seed 60200 G3 shape: 6-card hand kept
        with Goryo's + Rites×2 + disruption + 1 land — has multiple
        reanimators but no Mending.  The 6-card escape clause keeps
        any hand where ``max_progress >= 1``, but a Goryo's-only hand
        has no path to the graveyard.  The typed-paths fix applies
        at 6 cards too: every non-empty bucket needs ≥1 in hand."""
        hand = [
            _hand_card(card_db, "Swamp", iid=31),
            _hand_card(card_db, "Thoughtseize", iid=32),
            _hand_card(card_db, "Goryo's Vengeance", iid=33),
            _hand_card(card_db, "Unburial Rites", iid=34),
            _hand_card(card_db, "Unburial Rites", iid=35),
            _hand_card(card_db, "Solitude", iid=36),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=6)
        assert not keep, (
            f"Goryo's kept a 6 with payoff bucket covered (Goryo's, "
            f"Rites×2) but enabler bucket empty (no Mending).  "
            f"Reason: '{decider.last_reason}'.  At 6 cards the "
            f"typed-paths rule still requires every non-empty role "
            f"bucket to have at least one card in hand."
        )

    def test_goryos_keeps_complete_path_at_6(self, card_db):
        """Regression: a 6 with both buckets covered (Mending +
        Goryo's + lands + filler) must still keep.  The typed-paths
        rule at 6 should not tighten further than at 7."""
        hand = [
            _hand_card(card_db, "Hallowed Fountain", iid=41),
            _hand_card(card_db, "Godless Shrine", iid=42),
            _hand_card(card_db, "Watery Grave", iid=43),
            _hand_card(card_db, "Faithful Mending", iid=44),
            _hand_card(card_db, "Goryo's Vengeance", iid=45),
            _hand_card(card_db, "Thoughtseize", iid=46),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=6)
        assert keep, (
            f"Goryo's regression: 6-card hand with enabler + "
            f"payoff covered was mulled.  Reason: "
            f"'{decider.last_reason}'.  Typed-paths must keep "
            f"hands that cover every role bucket, regardless of "
            f"hand size 6 or 7."
        )


class TestMulliganColorCheckRunsAtAllSizes:
    """Bug A — the color-soundness check is gated at
    ``cards_in_hand >= 7``.  After the first mulligan the virtual
    size is 6 and the check is skipped; the same color-broken hand is
    then kept.  Replay seed 60100 G1: fresh 7 of {Mending×3, Rites,
    Solitude, Inquisition, Swamp} was kept as a 6-card hand because
    the gate skipped color verification.  Mending costs WU; the only
    land is Swamp; Mending is uncastable for the entire game."""

    def test_goryos_at_6_mulls_color_unsound_hand(self, card_db):
        """Same fresh 7 from S60100 G1.  Virtual size 6 (after one
        prior mulligan).  Hand has both buckets covered (Mending +
        Rites) but only black mana.  Mending requires {W}{U}; the
        hand cannot cast its own enabler.  Today this is kept under
        the gate ``cards_in_hand >= 7``.  Fix: run the color check
        at any virtual size 5+, not only 7."""
        hand = [
            _hand_card(card_db, "Faithful Mending", iid=51),
            _hand_card(card_db, "Faithful Mending", iid=52),
            _hand_card(card_db, "Faithful Mending", iid=53),
            _hand_card(card_db, "Unburial Rites", iid=54),
            _hand_card(card_db, "Solitude", iid=55),
            _hand_card(card_db, "Inquisition of Kozilek", iid=56),
            _hand_card(card_db, "Swamp", iid=57),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=6)
        assert not keep, (
            f"Goryo's at virtual size 6 kept a hand whose only land "
            f"is Swamp (B) but whose enabler Faithful Mending costs "
            f"{{W}}{{U}}.  Reason: '{decider.last_reason}'.  The "
            f"color-soundness check must run at every virtual hand "
            f"size >= 5, not only at 7."
        )

    def test_goryos_at_6_keeps_color_sound_hand(self, card_db):
        """Regression: a color-sound 6-card hand must still keep.
        Mending + Goryo's + Hallowed Fountain (W/U) + Watery Grave
        (U/B) + Godless Shrine (W/B) covers all needed pips."""
        hand = [
            _hand_card(card_db, "Hallowed Fountain", iid=61),
            _hand_card(card_db, "Watery Grave", iid=62),
            _hand_card(card_db, "Godless Shrine", iid=63),
            _hand_card(card_db, "Faithful Mending", iid=64),
            _hand_card(card_db, "Goryo's Vengeance", iid=65),
            _hand_card(card_db, "Thoughtseize", iid=66),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=6)
        assert keep, (
            f"Goryo's regression: color-sound 6-card hand was "
            f"mulled.  Reason: '{decider.last_reason}'.  The "
            f"color check must not over-reject hands whose lands "
            f"genuinely cover the kept combo cards."
        )
