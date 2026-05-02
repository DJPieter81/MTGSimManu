"""Artifact-aggro mulligan must honor a `mulligan_min_lands: 2` floor.

Reference: docs/diagnostics/2026-04-23_affinity_consolidated_findings.md §M1.

The 11-agent audit identified `affinity.json` and `pinnacle_affinity.json`
as the only aggro gameplans declaring `mulligan_min_lands: 1` while every
peer aggro deck either declares 2 or omits the field (default = 2 per
`decks/gameplan_loader.py:65`).

Class-size: this is the generic mulligan-floor mechanism — any gameplan
that sets `mulligan_min_lands: K` should reject hands with fewer than K
lands at 7 cards, regardless of the deck's name.  The audit attributed
3-5pp of Affinity's WR inflation to keeping 1-land hands that peer aggro
decks would mull.

The fix is JSON-only: bump both files' `mulligan_min_lands` from 1 to 2.
This test pins the rule so a future "permissive override" does not
silently regress.

Test names describe the rule (mulligan_min_lands floor), not the cards.
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


def _decider(deck_name: str) -> MulliganDecider:
    goal = create_goal_engine(deck_name)
    return MulliganDecider(ArchetypeStrategy.AGGRO, goal)


class TestMulliganArtifactAggroOneLandFloor:
    """A 1-land hand for an artifact-aggro deck declaring
    `mulligan_min_lands: 2` must be mulliganed at 7 cards.  Mana
    artifacts (Mox Opal, Springleaf Drum) do NOT count as lands for
    this floor — they need metalcraft / a creature respectively to
    produce mana on T1, so a 1-land + Mox Opal hand still functions
    as 1-land on T1."""

    def test_affinity_mulls_one_land_with_mox_opal(self, card_db):
        """Affinity: 1 land + Mox Opal + 5 free creatures.  Audit
        finding M1: this shape was being kept under the old
        `mulligan_min_lands: 1` override and is the canonical
        flooded-with-zeros-but-no-mana shape that loses on the draw
        when Mox Opal can't activate metalcraft on T1.  Floor is now
        2; the keeper must reject."""
        hand = [
            _hand_card(card_db, "Spire of Industry", iid=1),
            _hand_card(card_db, "Mox Opal", iid=2),
            _hand_card(card_db, "Memnite", iid=3),
            _hand_card(card_db, "Memnite", iid=4),
            _hand_card(card_db, "Ornithopter", iid=5),
            _hand_card(card_db, "Ornithopter", iid=6),
            _hand_card(card_db, "Cranial Plating", iid=7),
        ]
        decider = _decider("Affinity")
        keep = decider.decide(hand, cards_in_hand=7)
        assert not keep, (
            f"Affinity kept a 1-land 7-card hand.  Reason: "
            f"'{decider.last_reason}'.  Per affinity.json the floor "
            f"is `mulligan_min_lands: 2`; the hand has only 1 land "
            f"(Spire of Industry).  Mox Opal needs 3 artifacts for "
            f"metalcraft and produces colorless until then — it does "
            f"not satisfy the land floor.  See "
            f"docs/diagnostics/2026-04-23_affinity_consolidated_findings.md §M1."
        )

    def test_pinnacle_affinity_mulls_one_land_with_springleaf(self, card_db):
        """Pinnacle Affinity: same architecture, same fix.  1 land +
        Springleaf Drum + free creatures must mull — Springleaf needs
        an untapped creature to tap and produces mana only after
        attackers / blockers settle, so it does not substitute for a
        T1 land."""
        hand = [
            _hand_card(card_db, "Darksteel Citadel", iid=11),
            _hand_card(card_db, "Springleaf Drum", iid=12),
            _hand_card(card_db, "Memnite", iid=13),
            _hand_card(card_db, "Ornithopter", iid=14),
            _hand_card(card_db, "Mishra's Bauble", iid=15),
            _hand_card(card_db, "Pinnacle Emissary", iid=16),
            _hand_card(card_db, "Cranial Plating", iid=17),
        ]
        decider = _decider("Pinnacle Affinity")
        keep = decider.decide(hand, cards_in_hand=7)
        assert not keep, (
            f"Pinnacle Affinity kept a 1-land 7-card hand.  Reason: "
            f"'{decider.last_reason}'.  Per pinnacle_affinity.json "
            f"the floor is `mulligan_min_lands: 2`; this hand has "
            f"only 1 land (Darksteel Citadel).  Springleaf Drum needs "
            f"an untapped creature to tap, so it does not substitute "
            f"for a land on T1.  Same fix as Affinity per the "
            f"generalization rule (artifact-aggro architecture)."
        )

    def test_affinity_keeps_two_land_regression(self, card_db):
        """Regression: a 2-land 7-card hand at the floor must still
        keep.  Bumping the floor must not over-reject hands at the
        threshold."""
        hand = [
            _hand_card(card_db, "Spire of Industry", iid=21),
            _hand_card(card_db, "Darksteel Citadel", iid=22),
            _hand_card(card_db, "Mox Opal", iid=23),
            _hand_card(card_db, "Memnite", iid=24),
            _hand_card(card_db, "Ornithopter", iid=25),
            _hand_card(card_db, "Cranial Plating", iid=26),
            _hand_card(card_db, "Frogmite", iid=27),
        ]
        decider = _decider("Affinity")
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep, (
            f"Affinity 2-land regression: 2-land hand at the floor "
            f"was mulliganed.  Reason: '{decider.last_reason}'.  The "
            f"M1 fix bumps the floor to 2; hands AT the floor must "
            f"still keep — the change rejects 1-land hands only."
        )
