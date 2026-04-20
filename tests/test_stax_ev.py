"""Unit tests for ai/stax_ev.py — oracle-driven stax EV overlay.

These tests validate:
1. Oracle-based classification dispatches correctly (no hardcoded names).
2. Chalice EV is positive vs low-CMC aggro, ~zero vs symmetric mirror.
3. Blood Moon EV scales with opp nonbasic-land + color mismatch.
4. Non-stax cards return 0.0.
5. Empty/degenerate inputs don't crash.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List

import pytest

from ai.stax_ev import classify_stax, stax_lock_ev
from ai.ev_evaluator import EVSnapshot
from engine.card_database import CardDatabase


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def db():
    return CardDatabase()


@dataclass
class FakeCard:
    """Minimal stand-in for CardInstance — just a template reference."""
    template: object


@dataclass
class FakePlayer:
    """Minimal stand-in for PlayerState for stax_lock_ev unit tests."""
    library: List[FakeCard] = field(default_factory=list)
    hand: List[FakeCard] = field(default_factory=list)
    battlefield: List[FakeCard] = field(default_factory=list)


def _make_player(db, decklist: dict) -> FakePlayer:
    """Build a FakePlayer whose library contains the specified decklist."""
    p = FakePlayer()
    for name, count in decklist.items():
        t = db.get_card(name)
        assert t is not None, f"Card not found in DB: {name}"
        for _ in range(count):
            p.library.append(FakeCard(template=t))
    return p


@pytest.fixture
def snap():
    # Neutral early-game snapshot: T2, both at 20, we have 2 mana.
    return EVSnapshot(my_life=20, opp_life=20, my_mana=2, turn_number=2)


# ──────────────────────────────────────────────────────────────────────
# classify_stax — oracle dispatch
# ──────────────────────────────────────────────────────────────────────

class TestClassifyStax:

    def test_chalice_classified(self, db):
        t = db.get_card("Chalice of the Void")
        assert classify_stax(t) == 'chalice'

    def test_blood_moon_classified(self, db):
        t = db.get_card("Blood Moon")
        assert classify_stax(t) == 'blood_moon'

    def test_ethersworn_canonist_classified(self, db):
        t = db.get_card("Ethersworn Canonist")
        assert classify_stax(t) == 'canonist'

    def test_torpor_orb_classified(self, db):
        t = db.get_card("Torpor Orb")
        assert classify_stax(t) == 'torpor_orb'

    def test_non_stax_card_returns_none(self, db):
        """Non-stax cards (a vanilla creature, a counterspell, a land) must
        classify as None — no false positives."""
        for name in ("Counterspell", "Lightning Bolt", "Island",
                     "Wan Shi Tong, Librarian"):
            t = db.get_card(name)
            assert classify_stax(t) is None, f"false positive on {name}"


# ──────────────────────────────────────────────────────────────────────
# Chalice lock EV
# ──────────────────────────────────────────────────────────────────────

class TestChaliceLockEV:

    def test_chalice_positive_vs_one_drop_heavy_deck(self, db, snap):
        """Chalice vs a deck full of 1-CMC spells locks hard → high EV."""
        chalice = db.get_card("Chalice of the Void")
        # Build "me" = a 2/3-CMC control deck.
        me = _make_player(db, {
            "Counterspell": 4,           # CMC 2
            "Supreme Verdict": 4,        # CMC 4
            "Wrath of the Skies": 4,     # CMC 0-X
            "Wan Shi Tong, Librarian": 4,  # CMC 4
        })
        # Build "opp" = Boros-style 1-drop-heavy aggro.
        # Lightning Bolt, Ragavan, Guide of Souls all cost 1.
        opp = _make_player(db, {
            "Lightning Bolt": 4,
            "Ragavan, Nimble Pilferer": 4,
            "Guide of Souls": 4,
            "Ocelot Pride": 4,
        })
        ev = stax_lock_ev(chalice, me, opp, snap)
        assert ev > 3.0, f"Chalice vs 1-drop aggro should score high, got {ev}"

    def test_chalice_zero_in_mirror(self, db, snap):
        """Chalice with identical CMC distributions on both sides nets zero.
        Same CMC on each side = no positive X exists."""
        chalice = db.get_card("Chalice of the Void")
        same_deck = {"Counterspell": 4, "Supreme Verdict": 4}
        me = _make_player(db, same_deck)
        opp = _make_player(db, same_deck)
        ev = stax_lock_ev(chalice, me, opp, snap)
        assert ev == 0.0, f"Symmetric mirror should give 0.0, got {ev}"

    def test_chalice_zero_vs_empty_opp(self, db, snap):
        """Defensive: empty opp library → 0.0, no crash."""
        chalice = db.get_card("Chalice of the Void")
        me = _make_player(db, {"Counterspell": 4})
        opp = FakePlayer()
        ev = stax_lock_ev(chalice, me, opp, snap)
        assert ev == 0.0

    def test_chalice_ev_decays_with_turn(self, db):
        """Chalice EV should be highest on T1-T2 and drop to zero by T5.

        Rationale: by T5 the opp has resolved their key early spells; a
        late Chalice catches topdecks only.
        """
        chalice = db.get_card("Chalice of the Void")
        me = _make_player(db, {"Counterspell": 4, "Supreme Verdict": 4})
        opp = _make_player(db, {
            "Lightning Bolt": 4, "Ragavan, Nimble Pilferer": 4,
            "Guide of Souls": 4, "Ocelot Pride": 4,
        })
        t1 = stax_lock_ev(chalice, me, opp,
                          EVSnapshot(my_life=20, opp_life=20,
                                     my_mana=2, turn_number=1))
        t3 = stax_lock_ev(chalice, me, opp,
                          EVSnapshot(my_life=20, opp_life=20,
                                     my_mana=2, turn_number=3))
        t5 = stax_lock_ev(chalice, me, opp,
                          EVSnapshot(my_life=20, opp_life=20,
                                     my_mana=2, turn_number=5))
        assert t1 > t3 > t5, f"Expected t1 > t3 > t5, got {t1}, {t3}, {t5}"
        assert t5 == 0.0, f"T5+ Chalice should be 0 EV, got {t5}"


# ──────────────────────────────────────────────────────────────────────
# Blood Moon lock EV
# ──────────────────────────────────────────────────────────────────────

class TestBloodMoonLockEV:

    def test_blood_moon_vs_nonbasic_heavy_multicolor(self, db, snap):
        """Blood Moon vs a UW deck with 24 nonbasic lands should score high."""
        bm = db.get_card("Blood Moon")
        me = _make_player(db, {"Mountain": 20, "Lightning Bolt": 4})
        # UW deck: nonbasics + white + blue spells.
        opp = _make_player(db, {
            "Flooded Strand": 4,
            "Hallowed Fountain": 4,
            "Polluted Delta": 4,
            "Counterspell": 4,  # UU
            "Supreme Verdict": 4,  # 1WWU
        })
        ev = stax_lock_ev(bm, me, opp, snap)
        assert ev > 2.0, f"Blood Moon vs UW nonbasic base should score high, got {ev}"

    def test_blood_moon_near_zero_vs_mono_red(self, db, snap):
        """Blood Moon vs a mono-red deck with basic Mountains = no disruption."""
        bm = db.get_card("Blood Moon")
        me = _make_player(db, {"Mountain": 20})
        # Mono-red, all basics + red spells.
        opp = _make_player(db, {
            "Mountain": 20,
            "Lightning Bolt": 4,
            "Monastery Swiftspear": 4,
        })
        ev = stax_lock_ev(bm, me, opp, snap)
        assert ev == 0.0, f"Blood Moon vs mono-red basics should be 0.0, got {ev}"


# ──────────────────────────────────────────────────────────────────────
# Non-stax cards — main-line sanity
# ──────────────────────────────────────────────────────────────────────

class TestNonStaxCards:

    def test_counterspell_returns_zero(self, db, snap):
        t = db.get_card("Counterspell")
        me = _make_player(db, {"Counterspell": 4})
        opp = _make_player(db, {"Lightning Bolt": 4})
        assert stax_lock_ev(t, me, opp, snap) == 0.0

    def test_land_returns_zero(self, db, snap):
        t = db.get_card("Island")
        me = _make_player(db, {"Counterspell": 4})
        opp = _make_player(db, {"Lightning Bolt": 4})
        assert stax_lock_ev(t, me, opp, snap) == 0.0
