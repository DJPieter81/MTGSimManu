"""Mulligan must reject combo hands whose lands cannot produce the
colors needed to cast the kept combo set.

Reference: docs/diagnostics/2026-04-28_goryos_combo_mana_mulligan.md

Today's `ai/mulligan.py:96-104` consumes `mulligan_combo_sets` as a
cardname predicate only — it asks "is at least one card from each
required set in hand?" and never asks "can the hand's lands cast it?"

That gap is the proximate cause of Goryo's Vengeance 0/20 vs Boros
(replay seed 60200 G2): the AI kept a 7 with all four declared combo
pieces in hand, but the three lands produced only {W, B}.  Faithful
Mending costs {W}{U} and is uncastable through T6; Goryo's Vengeance
({1}{B}) has no legal target because the only legendary fatty in the
deck (Griselbrand) was never drawn.  AI sat on its hand for six turns
and lost.

The class-size of this bug is the entire combo archetype: every deck
whose enabler / payoff has off-color pips that the manabase only
sometimes covers (Goryo's, Living End cycle B/G + cascade R, Ruby
Storm RR, Pinnacle Affinity blue counters).  The fix mechanic — verify
the kept hand's lands cover the union of pip requirements — is
oracle-driven (uses existing `template.mana_cost`) and contains zero
hardcoded card names.

Test name describes the rule, not the card.  These cases will catch
the same bug for any combo deck that gains a `mulligan_combo_sets`
declaration.
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


class TestMulliganRejectsColorUnsoundComboHand:
    """A combo hand whose lands cannot produce the pips needed for the
    kept combo set must be mulliganed.  Cardname-only checks are not
    sufficient."""

    def test_goryos_rejects_no_red_no_blue_combo_hand(self, card_db):
        """The exact 7 from replay seed 60200 G2.  Hand has all four
        declared combo cards (Faithful Mending, Goryo's Vengeance,
        Unburial Rites, Archon of Cruelty) plus three lands that
        collectively produce only {W, B}.  Faithful Mending requires
        {W}{U} — uncastable.  Goryo's Vengeance requires a legendary
        creature in graveyard, which the hand cannot create without a
        live discard outlet.

        Today this hand is KEPT (reason: "has key card(s): Archon of
        Cruelty, Faithful Mending, Goryo's Vengeance, Unburial Rites,
        2 cheap spells").  Under the fix, the color-coverage check
        must reject it because the hand's lands don't cover the union
        of pip requirements ({W,U,B,R}-ish — at minimum U is missing
        for Faithful Mending and the hand has no R for any backup
        path)."""
        hand = [
            _hand_card(card_db, "Godless Shrine", iid=1),
            _hand_card(card_db, "Swamp", iid=2),
            _hand_card(card_db, "Concealed Courtyard", iid=3),
            _hand_card(card_db, "Archon of Cruelty", iid=4),
            _hand_card(card_db, "Goryo's Vengeance", iid=5),
            _hand_card(card_db, "Unburial Rites", iid=6),
            _hand_card(card_db, "Faithful Mending", iid=7),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=7)
        assert not keep, (
            f"Goryo's kept a 7 with all combo pieces but only W/B "
            f"sources.  Reason logged: '{decider.last_reason}'.  "
            f"Faithful Mending costs {{W}}{{U}} — uncastable from "
            f"this manabase.  The mulligan must verify that the "
            f"hand's lands cover the union of pip requirements for "
            f"the kept combo set, not just that the cards are "
            f"present by name.  See "
            f"docs/diagnostics/2026-04-28_goryos_combo_mana_mulligan.md"
        )

    def test_goryos_keeps_color_sound_combo_hand(self, card_db):
        """Regression: a combo hand with proper color coverage must
        still keep.  Same combo cards but lands include Watery Grave
        (U/B) and Blood Crypt (B/R) — both U for Faithful Mending and
        R for the backup path are available.  The color-coverage fix
        must not over-reject hands that actually can cast the combo."""
        hand = [
            _hand_card(card_db, "Watery Grave", iid=11),
            _hand_card(card_db, "Blood Crypt", iid=12),
            _hand_card(card_db, "Godless Shrine", iid=13),
            _hand_card(card_db, "Griselbrand", iid=14),
            _hand_card(card_db, "Goryo's Vengeance", iid=15),
            _hand_card(card_db, "Faithful Mending", iid=16),
            _hand_card(card_db, "Thoughtseize", iid=17),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep, (
            f"Goryo's regression: color-sound combo hand was "
            f"mulliganed.  Reason: '{decider.last_reason}'.  Watery "
            f"Grave + Blood Crypt + Godless Shrine collectively "
            f"supply W,U,B,R — covering Faithful Mending ({{W}}{{U}} "
            f"per MTGJSON) and Goryo's ({{1}}{{B}}); Griselbrand is "
            f"a legal Goryo's target (legendary, gets reanimated, "
            f"not hard-cast — its {{B}}{{B}}{{B}}{{B}} pips don't "
            f"count as combo demand).  This hand can fire the combo "
            f"on T2.  The fix must not reject it."
        )

    def test_goryos_mulls_zero_lands_regression(self, card_db):
        """Regression: the 0-lands hard floor still fires for combo
        decks even with the perfect spell suite."""
        hand = [
            _hand_card(card_db, "Goryo's Vengeance", iid=21),
            _hand_card(card_db, "Faithful Mending", iid=22),
            _hand_card(card_db, "Griselbrand", iid=23),
            _hand_card(card_db, "Unburial Rites", iid=24),
            _hand_card(card_db, "Thoughtseize", iid=25),
            _hand_card(card_db, "Thoughtseize", iid=26),
            _hand_card(card_db, "Archon of Cruelty", iid=27),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=7)
        assert not keep, (
            f"Regression: zero-lands hand kept (reason: "
            f"'{decider.last_reason}').  The color-coverage fix must "
            f"not regress the no-lands hard floor."
        )

    def test_goryos_mulls_one_combo_piece_no_enabler_no_target(
            self, card_db):
        """Bug #4: the combo-set predicate `hand_names & combo_set`
        is non-empty as soon as ANY single card from a 3-card path is
        in hand.  This hand has Goryo's Vengeance and Unburial Rites
        but NO Faithful Mending (enabler) and NO Griselbrand/Archon
        (target).  Each declared combo path is 1-of-3 satisfied =
        33% combo present = unplayable.  Today the AI keeps it
        (verbose seed 50000 G1 confirms).

        Under the fix, a 7-card opening must have ≥ 2 of 3 pieces
        from at least one declared combo path before keeping.  A
        digger (cantrip, Faithful Mending) can find the third piece;
        finding the second AND third in 4-5 turns vs an aggro clock
        is unrealistic.

        Test name encodes the rule: combo decks require sufficient
        combo progress at 7, not just any single piece."""
        hand = [
            _hand_card(card_db, "Marsh Flats", iid=31),
            _hand_card(card_db, "Flooded Strand", iid=32),
            _hand_card(card_db, "Goryo's Vengeance", iid=33),
            _hand_card(card_db, "Goryo's Vengeance", iid=34),
            _hand_card(card_db, "Unburial Rites", iid=35),
            _hand_card(card_db, "Thoughtseize", iid=36),
            _hand_card(card_db, "Inquisition of Kozilek", iid=37),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=7)
        assert not keep, (
            f"Goryo's kept a 7 with 2× Goryo's + Unburial Rites but "
            f"NO Faithful Mending (enabler) and NO target (Griselbrand/"
            f"Archon).  Reason logged: '{decider.last_reason}'.  Each "
            f"combo path is 1-of-3 satisfied — not enough to fire the "
            f"combo before turn 5-6 vs Boros (clock 4-5).  See "
            f"docs/diagnostics/2026-04-28_goryos_combo_mana_mulligan.md "
            f"§Bug #4 — the cardname predicate must require ≥ 2 of 3 "
            f"pieces from at least one declared combo path."
        )

    def test_goryos_keeps_two_of_three_combo_pieces(self, card_db):
        """Regression: a 7 with 2 of 3 pieces from a complete combo
        path must still keep — having enabler + payoff and digging for
        the target with cantrips is the standard combo-deck shape.
        The Bug #4 fix must not over-tighten."""
        hand = [
            _hand_card(card_db, "Watery Grave", iid=41),
            _hand_card(card_db, "Godless Shrine", iid=42),
            _hand_card(card_db, "Blood Crypt", iid=43),
            _hand_card(card_db, "Faithful Mending", iid=44),
            _hand_card(card_db, "Goryo's Vengeance", iid=45),
            _hand_card(card_db, "Thoughtseize", iid=46),
            _hand_card(card_db, "Inquisition of Kozilek", iid=47),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep, (
            f"Goryo's regression: 2-of-3 combo hand (Faithful Mending "
            f"+ Goryo's Vengeance, missing target) was mulliganed.  "
            f"Reason: '{decider.last_reason}'.  Mending fills the "
            f"graveyard and digs for a fatty; this is the canonical "
            f"combo-deck keep.  The Bug #4 fix must not reject it."
        )


class TestMulliganRejectsSixCardWithNoComboPath:
    """Bug #3 — at 6 cards, the existing predicate auto-keeps any
    hand that misses a single combo_set, even if it misses ALL
    declared combo paths.  Replay seed 60200 G1: Goryo's mulled the
    7 (no combo piece), drew a 6 with `Ephemerate, Marsh Flats,
    Solitude×2, Unburial Rites, Undying Evil` — only Unburial Rites
    intersects any combo set.  The first iterated set is
    {FM, GV, Gris}: ∩ = {} → returns True at <=6, and the loop
    exits before checking the other 3 sets.

    Correct behaviour: at <=6 cards, keep only if at least ONE
    declared combo path has a card present.  If EVERY path is empty
    in hand, mull to 5.  Aggro/midrange decks (no `mulligan_combo_sets`)
    are unaffected — this branch only runs when combo_sets is
    declared."""

    def test_goryos_at_6_mulls_if_every_combo_path_empty(self, card_db):
        """6-card hand with zero cards from any of Goryo's 4 combo
        paths must mull to 5.  This is the replay-G1 shape with the
        last combo card (Unburial Rites) swapped out — proves the
        fix doesn't depend on the iteration order of combo_sets."""
        hand = [
            _hand_card(card_db, "Marsh Flats", iid=31),
            _hand_card(card_db, "Godless Shrine", iid=32),
            _hand_card(card_db, "Ephemerate", iid=33),
            _hand_card(card_db, "Solitude", iid=34),
            _hand_card(card_db, "Solitude", iid=35),
            _hand_card(card_db, "Undying Evil", iid=36),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=6)
        assert not keep, (
            f"Goryo's 6-card hand with NO card from any of 4 declared "
            f"combo paths was kept.  Reason: '{decider.last_reason}'. "
            f"None of {{Faithful Mending, Goryo's Vengeance, Unburial "
            f"Rites, Griselbrand, Archon of Cruelty}} are in hand. "
            f"Mull to 5 — the 6-card escape clause must not fire when "
            f"every combo path is empty."
        )

    def test_goryos_at_6_keeps_if_any_path_partially_present(self, card_db):
        """Regression: a 6 with one combo card (Faithful Mending,
        the enabler) must still keep — the 6-card escape's purpose is
        to avoid mulling-to-oblivion when a single combo piece is
        missing.  Don't over-correct."""
        hand = [
            _hand_card(card_db, "Marsh Flats", iid=41),
            _hand_card(card_db, "Godless Shrine", iid=42),
            _hand_card(card_db, "Watery Grave", iid=43),
            _hand_card(card_db, "Faithful Mending", iid=44),
            _hand_card(card_db, "Thoughtseize", iid=45),
            _hand_card(card_db, "Solitude", iid=46),
        ]
        decider = _goryos_decider()
        keep = decider.decide(hand, cards_in_hand=6)
        assert keep, (
            f"Goryo's 6-card hand with Faithful Mending (enabler "
            f"present in all 4 combo paths) was mulled.  Reason: "
            f"'{decider.last_reason}'.  Mending is the bottleneck "
            f"piece — keeping a 6 with it is correct play; mulling "
            f"to 5 in search of a different path is worse EV."
        )
