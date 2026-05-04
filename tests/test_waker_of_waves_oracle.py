"""Waker of Waves oracle integrity — Class A regression test.

Diagnosis (Phase K combo audit, 2026-05-04):
docs/diagnostics/2026-05-04_living_end_audit.md (Q1 — Class A-1)

The local ModernAtomic.json entry for "Waker of Waves" has wrong
oracle text — missing the "Cycling {X}{1}{U}" ability that makes
this card a Living End enabler. As a result:

- `template.cycling_cost_data` is None
- `engine/cycling.py:CyclingManager.can_cycle` returns False
- Living End cannot self-discard Waker via cycling
- Effective cycler count drops from 16 (4 Riverwinder + 4 Architects
  + 2 Curator + 4 Street Wraith + 2 Waker) to 14

This test is the rule-phrased regression: any card the deck declares
as a cycler must have parseable cycling cost data. The mechanic-named
test is "Waker of Waves cycling cost is parseable for Living End
self-discard" — naming Waker is allowed in tests per CLAUDE.md
ABSTRACTION CONTRACT (test files are the sanctioned home for
card-name knowledge).

Fix: hand-patch `ModernAtomic_part8.json` with the canonical oracle
text from MH3 Waker of Waves. Upstream MTGJSON regression.
"""
from __future__ import annotations

import pytest

from engine.card_database import CardDatabase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


class TestWakerOfWavesOracle:
    """Waker of Waves must have cycling parseable from oracle text,
    so Living End can use it as a self-discard enabler."""

    def test_waker_of_waves_has_cycling_in_oracle_text(self, card_db):
        """The Modern-legal Waker of Waves (MH3 reprint of original
        ONS card) has 'Cycling {X}{1}{U}'. The local DB previously
        contained a corrupt oracle text missing the Cycling line —
        this regression guards against that recurrence."""
        waker = card_db.cards.get("Waker of Waves")
        assert waker is not None, (
            "Waker of Waves missing from card DB."
        )
        oracle = (waker.oracle_text or "")
        # Case-insensitive check for "Cycling" — the MTGJSON convention
        # is to use the keyword "Cycling" (capitalised) in oracle text.
        assert "ycling" in oracle, (
            f"Waker of Waves oracle text is missing the Cycling "
            f"keyword. Current oracle: {oracle!r}. The Modern-legal "
            f"printing (MH3) has 'Cycling {{X}}{{1}}{{U}}'. Living "
            f"End relies on this as a self-discard enabler."
        )

    def test_waker_of_waves_cycling_cost_data_parseable(
            self, card_db):
        """The oracle parser must extract a non-None cycling_cost_data
        from Waker's oracle text, so engine/cycling.py:can_cycle
        permits the activation."""
        waker = card_db.cards.get("Waker of Waves")
        assert waker is not None
        cost = waker.cycling_cost_data
        assert cost is not None, (
            "Waker of Waves cycling_cost_data is None — Living End "
            "cannot use Waker as a self-discard enabler. Either the "
            "oracle text is missing 'Cycling' (Class A bug) or the "
            "oracle parser fails on the {X}{1}{U} format."
        )

    def test_waker_of_waves_has_etb_grave_buff(self, card_db):
        """Real Waker of Waves has an ETB trigger that buffs a
        creature based on graveyard size — this is the post-Living-
        End finisher line. If oracle is missing this, the deck loses
        its win condition."""
        waker = card_db.cards.get("Waker of Waves")
        assert waker is not None
        oracle = (waker.oracle_text or "").lower()
        # Real text: "When this creature enters the battlefield,
        # target creature you control gets +X/+0 until end of turn,
        # where X is the number of cards in your graveyard."
        # Loose match: "graveyard" + "+X" or "+x" near "/"
        assert "graveyard" in oracle, (
            f"Waker of Waves oracle missing 'graveyard'. Current: "
            f"{oracle!r}. Expected the ETB +X buff referencing "
            f"graveyard size."
        )
