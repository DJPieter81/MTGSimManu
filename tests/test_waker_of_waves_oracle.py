"""Waker of Waves oracle integrity — Class A regression test.

Correction (2026-08-08, PR #488 CI-debt sweep): the original version
of this test asserted that Waker of Waves has "Cycling {X}{1}{U}" and
an ETB graveyard-size power buff, attributing their absence to a
corrupt `ModernAtomic_part8.json` entry. Independent verification
(web search against Scryfall/Gatherer listings) shows neither ability
exists on any real printing of this card — Waker of Waves is the
Core Set 2021 (M21) 7/7 Whale with a static -1/-0 debuff and a
discard-cost "look at the top two cards" activated ability. It has
never had the Cycling keyword and was never reprinted in Modern
Horizons 3. The local DB's oracle text is correct; the prior test
was pinning a fabricated card ability (the class of bug documented
in CLAUDE.md's "unprovenanced ninth part shipped 30 fabricated card
texts" incident).

This test now pins the REAL oracle text so a future DB refresh that
silently corrupts Waker's actual ability is still caught, without
re-asserting the fabricated Cycling/ETB-buff premise.

Follow-up (tracked, not fixed here): Living End's gameplan
(`decks/gameplans/living_end.json`) sets `prefer_cycling: true` and
lists Waker of Waves among its cyclers/finishers. Since Waker's real
ability is a discard-cost activated ability, not the Cycling keyword,
`engine/cycling.py:CyclingManager.can_cycle` correctly returns False
for it — the AI cannot "cycle" Waker via the cycling code path. This
does not crash anything (Waker is simply a dead card in this AI's
plan today), but it is a real deck-plan/engine mismatch worth a
dedicated look. See docs/design/rules-foundation-sweep-tracker.md.
"""
from __future__ import annotations

import pytest


class TestWakerOfWavesOracle:
    """Waker of Waves' oracle text and cycling_cost_data must match
    its real, Modern-legal printing (Core Set 2021)."""

    def test_waker_of_waves_has_no_cycling_keyword(self, card_db):
        """Real Waker of Waves (M21) has never had the Cycling
        keyword — it was never reprinted in MH3. If a future DB
        refresh adds a 'Cycling' line to this card, that is itself
        the fabrication bug this test now guards against."""
        waker = card_db.cards.get("Waker of Waves")
        assert waker is not None, (
            "Waker of Waves missing from card DB."
        )
        oracle = (waker.oracle_text or "")
        assert "ycling" not in oracle, (
            f"Waker of Waves oracle text now contains 'Cycling' — "
            f"the real M21 printing has no such keyword. Current "
            f"oracle: {oracle!r}. Investigate the source of this "
            f"line before trusting it; do not assume it is correct "
            f"just because it matches an old (fabricated) test "
            f"expectation."
        )

    def test_waker_of_waves_cycling_cost_data_is_none(self, card_db):
        """Since Waker of Waves has no Cycling keyword,
        `cycling_cost_data` must be None — the oracle parser is
        working correctly when it does NOT synthesize a cycling cost
        for a card that was never printed with one."""
        waker = card_db.cards.get("Waker of Waves")
        assert waker is not None
        assert waker.cycling_cost_data is None, (
            f"Waker of Waves has no Cycling keyword in its real "
            f"oracle text, so cycling_cost_data must be None. Got "
            f"{waker.cycling_cost_data!r} — either the oracle text "
            f"was corrupted to include a fabricated Cycling line, or "
            f"the parser is inferring cycling from unrelated text."
        )

    def test_waker_of_waves_has_discard_cost_filter_ability(self, card_db):
        """Real Waker of Waves ability: '{1}{U}, Discard this card:
        Look at the top two cards of your library. Put one of them
        into your hand and the other into your graveyard.'"""
        waker = card_db.cards.get("Waker of Waves")
        assert waker is not None
        oracle = (waker.oracle_text or "").lower()
        assert "discard this card" in oracle or "discard waker of waves" in oracle, (
            f"Waker of Waves oracle missing its discard-cost "
            f"activated ability. Current: {oracle!r}."
        )
        assert "top two cards" in oracle, (
            f"Waker of Waves oracle missing the 'look at the top two "
            f"cards' library-filter effect. Current: {oracle!r}."
        )

    def test_waker_of_waves_has_opponent_power_debuff(self, card_db):
        """Real Waker of Waves has a static ability: 'Creatures your
        opponents control get -1/-0.'"""
        waker = card_db.cards.get("Waker of Waves")
        assert waker is not None
        oracle = (waker.oracle_text or "").lower()
        assert "-1/-0" in oracle, (
            f"Waker of Waves oracle missing its static -1/-0 debuff "
            f"on opponents' creatures. Current: {oracle!r}."
        )
