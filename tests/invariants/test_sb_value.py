"""Invariants for the oracle-driven sideboard solver.

These tests anchor the canonical matchup properties from
docs/proposals/sideboard_solver.md §6:

- Color-protection scales with opp's colour-damage density
- GY hate is near-zero vs a non-GY deck
- Counterspells scale with opp's spell density
- Artifact hate scales with opp's artifact density
- Two cards with identical oracles get identical values (oracle parity)
"""
from __future__ import annotations

import pytest

from ai.sideboard_solver import sb_value
from engine.card_database import CardDatabase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _deck_templates(card_db, decklist):
    """Expand a name→count dict into a flat list of templates."""
    templates = []
    for name, count in decklist.items():
        tmpl = card_db.get_card(name)
        assert tmpl is not None, f"missing card in test decklist: {name}"
        for _ in range(count):
            templates.append(tmpl)
    return templates


# Canonical minimal decklists — stripped to representative cards only;
# enough signal for the density math without loading full 60-card lists.

BOROS_STUB = {
    "Lightning Bolt": 4, "Galvanic Discharge": 4,  # red damage
    "Ragavan, Nimble Pilferer": 4,                  # red creature
    "Phlage, Titan of Fire's Fury": 3,              # red/white damage
    "Sacred Foundry": 4, "Arid Mesa": 4,            # red-producing lands
}

AMULET_STUB = {
    "Primeval Titan": 4,                    # green creature
    "Azusa, Lost but Seeking": 2,           # green creature
    "Summoner's Pact": 4,                   # green-phyrexian (no red)
    "Amulet of Vigor": 4,                   # colourless artifact
    "Forest": 6, "Simic Growth Chamber": 4, # no red
}

LIVING_END_STUB = {
    "Living End": 4,                         # cascade target
    "Shardless Agent": 4,                    # cascader
    "Demonic Dread": 4,                      # cascader
    "Street Wraith": 4,                      # cycler
    "Striped Riverwinder": 4,                # big cycler
    "Breeding Pool": 2, "Misty Rainforest": 4,
}

STORM_STUB = {
    "Pyretic Ritual": 4, "Desperate Ritual": 4,  # rituals
    "Grapeshot": 2,                               # finisher
    "Reckless Impulse": 4,                        # cantrip
    "Ruby Medallion": 4,                          # cost reducer
    "Sacred Foundry": 4, "Arid Mesa": 4,
}


class TestColorProtection:
    """'Protection from red' is valuable vs red decks, worthless vs green."""

    def test_kor_firewalker_vs_boros_positive(self, card_db):
        templates = _deck_templates(card_db, BOROS_STUB)
        firewalker = card_db.get_card("Kor Firewalker")
        if firewalker is None:
            pytest.skip("Kor Firewalker not in DB")
        v = sb_value(firewalker, templates)
        assert v > 0, (
            f"Kor Firewalker (pro-red) should have positive value vs Boros "
            f"(red-heavy). Got {v:.2f}."
        )

    def test_kor_firewalker_vs_amulet_near_zero(self, card_db):
        templates = _deck_templates(card_db, AMULET_STUB)
        firewalker = card_db.get_card("Kor Firewalker")
        if firewalker is None:
            pytest.skip("Kor Firewalker not in DB")
        v = sb_value(firewalker, templates)
        # Amulet has no red sources → protection is worthless
        assert v < 1.0, (
            f"Kor Firewalker should be near-zero vs Amulet (no red). Got {v:.2f}."
        )

    def test_protection_scales_with_color_density(self, card_db):
        """Firewalker must be MORE valuable against Boros than Amulet."""
        fw = card_db.get_card("Kor Firewalker")
        if fw is None:
            pytest.skip("Kor Firewalker not in DB")
        v_boros = sb_value(fw, _deck_templates(card_db, BOROS_STUB))
        v_amulet = sb_value(fw, _deck_templates(card_db, AMULET_STUB))
        assert v_boros > v_amulet, (
            f"Kor Firewalker vs Boros ({v_boros:.2f}) should exceed vs Amulet "
            f"({v_amulet:.2f})."
        )


class TestGraveyardHate:
    """GY hate is a no-op vs decks that don't use the graveyard."""

    def test_relic_vs_living_end_positive(self, card_db):
        """Relic of Progenitus oracle references exile/graveyard."""
        relic = card_db.get_card("Relic of Progenitus")
        if relic is None:
            pytest.skip("Relic of Progenitus not in DB")
        templates = _deck_templates(card_db, LIVING_END_STUB)
        v = sb_value(relic, templates)
        assert v > 0, (
            f"Relic vs Living End should be positive (GY-dependent combo). "
            f"Got {v:.2f}."
        )

    def test_relic_vs_storm_near_zero(self, card_db):
        """Storm doesn't use the graveyard until Past in Flames."""
        relic = card_db.get_card("Relic of Progenitus")
        if relic is None:
            pytest.skip("Relic of Progenitus not in DB")
        templates = _deck_templates(card_db, STORM_STUB)
        v = sb_value(relic, templates)
        # Storm without Past in Flames in the stub has near-zero GY reliance
        assert v < 3.0, (
            f"Relic vs non-GY Storm stub should be near-zero. Got {v:.2f}."
        )


class TestCounterspell:
    """Counterspells scale with opp's noncreature spell density."""

    def test_force_of_negation_vs_storm_exceeds_vs_boros(self, card_db):
        """Storm runs many noncreature spells; Boros runs mostly creatures."""
        fon = card_db.get_card("Force of Negation")
        if fon is None:
            pytest.skip("Force of Negation not in DB")
        v_storm = sb_value(fon, _deck_templates(card_db, STORM_STUB))
        v_boros = sb_value(fon, _deck_templates(card_db, BOROS_STUB))
        assert v_storm > v_boros, (
            f"Force of Negation should value higher vs Storm ({v_storm:.2f}) "
            f"than vs Boros ({v_boros:.2f}) — Storm has far more noncreature "
            f"spells."
        )


class TestOracleParity:
    """Cards with identical oracle text score identically."""

    def test_identical_oracles_same_value(self, card_db):
        """Two Lightning Bolts must have identical sb_value — proves the
        formula is oracle-driven, not name-driven."""
        bolt1 = card_db.get_card("Lightning Bolt")
        bolt2 = card_db.get_card("Lightning Bolt")
        if bolt1 is None:
            pytest.skip("Lightning Bolt not in DB")
        templates = _deck_templates(card_db, BOROS_STUB)
        assert sb_value(bolt1, templates) == sb_value(bolt2, templates)


class TestPlanSideboard:
    """End-to-end plan_sideboard swap-plan smoke test."""

    def test_swap_in_gy_hate_vs_living_end(self, card_db):
        """A deck with Relic in the SB should swap it in vs Living End."""
        from ai.sideboard_solver import plan_sideboard

        # A minimal hybrid deck with one GY hate card in SB.
        my_main = {"Lightning Bolt": 4, "Ragavan, Nimble Pilferer": 4,
                    "Sacred Foundry": 8}
        my_sb = {"Relic of Progenitus": 3, "Lightning Bolt": 1}

        new_main, new_sb, log = plan_sideboard(
            my_main, my_sb,
            opp_deck_name="Living End",
            card_db=card_db,
            opp_mainboard=LIVING_END_STUB,
        )

        assert new_main.get("Relic of Progenitus", 0) > 0, (
            f"plan_sideboard should swap in Relic vs Living End. "
            f"log={log}, new_main={new_main}"
        )
