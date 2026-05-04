"""Modern meta — MB artifact-hate density (Phase K Class H batch).

Diagnosis (Phase K combo audit, 2026-05-04):
docs/diagnostics/2026-05-04_affinity_overperformance_audit.md

Affinity sits at 84% sim WR vs an expected ~55% because 9 of 15
opposing decks have **0 mainboard artifact hate**. The matrix
default is Bo1 (single-game) — opponents face zero G1 disruption
to Affinity's broken-by-design 0-mana mana rocks (Mox Opal,
Springleaf Drum) and equipment (Cranial Plating, Nettlecyst).

Real Modern players at these archetypes run 1-2 MB hate pieces
specifically to bridge the Bo1 vs Affinity matchup. The fix is a
mainboard data edit on the under-prepared opponents, NOT a fix to
Affinity's AI scoring (the engine math is rules-correct).

This test enforces that each of the 4 "realistic to splash hate"
opposing decks runs >= 1 MB artifact-hate card. The 4 chosen are
those whose archetype includes a colour or mechanic that makes
artifact hate accessible:

- **Boros Energy** (RW): Wear // Tear is a flagship answer
- **Eldrazi Tron** (colourless): Pithing Needle is a 1-CMC artifact
- **Domain Zoo** (5-colour): Wear // Tear via white splash
- **Living End** (BUG): Force of Vigor is a flagship answer

Decks intentionally NOT covered by this test (their colour or
mechanic doesn't realistically support MB artifact hate at the
current meta):
- Dimir Midrange (UB) — no efficient artifact removal in colours
- Goryo's Vengeance (BW) — already covered by separate Boseiju add
- Izzet Prowess (UR) — Pithing Needle is the only colourless option,
  but the deck's tempo plan doesn't support a do-nothing-T1 card
- Pinnacle Affinity (UR) — mirror; Hurkyl's Recall SB only is canonical
- Ruby Storm (R) — no green for Boseiju channel; deck races, not
  interacts. Realistic Modern Storm has 0 MB hate.

Each MB add is balanced by removing 1 of the deck's lowest-impact
flex slots (per the per-deck analysis in the audit doc).
"""
from __future__ import annotations

import pytest

from decks.modern_meta import MODERN_DECKS


# Cards that destroy / disable non-creature artifacts. Specifically
# excludes creature removal (Galvanic Discharge, Lightning Bolt) which
# does not hit Mox Opal / Springleaf Drum / Cranial Plating.
ARTIFACT_HATE = frozenset({
    "Wear // Tear", "Hurkyl's Recall", "Force of Vigor", "Haywire Mite",
    "Meltdown", "Pithing Needle", "Damping Sphere", "Stony Silence",
    "Pick Your Poison", "Boseiju, Who Endures", "Foundation Breaker",
    "Karn, the Great Creator", "Collector Ouphe", "Vexing Bauble",
    "Prismatic Ending", "Wrath of the Skies", "Shatterstorm",
    "Ancient Grudge", "Smash to Smithereens",
})


def _mb_artifact_hate_count(deck_name: str) -> int:
    """Sum of mainboard copies of cards in ARTIFACT_HATE."""
    deck = MODERN_DECKS[deck_name]["mainboard"]
    return sum(qty for name, qty in deck.items()
               if name in ARTIFACT_HATE)


@pytest.mark.parametrize("deck_name,min_count,rationale", [
    ("Boros Energy", 1,
     "RW deck — Wear // Tear is the flagship MB answer to Affinity"),
    ("Eldrazi Tron", 1,
     "Colourless deck — Pithing Needle is 1-CMC, fits the curve"),
    ("Domain Zoo", 1,
     "5-colour deck — Wear // Tear via the white splash"),
    ("Living End", 1,
     "BUG deck — Force of Vigor pitchable, fits the cascade plan"),
])
def test_mb_artifact_hate_density(deck_name, min_count, rationale):
    """Each opposing deck listed must have >= 1 MB artifact-hate card.
    Without this, the Bo1 sim systematically biases against the
    opponent and Affinity overperforms (see audit doc)."""
    count = _mb_artifact_hate_count(deck_name)
    assert count >= min_count, (
        f"{deck_name} mainboard has {count} artifact-hate cards; "
        f"expected >= {min_count}. Rationale: {rationale}. "
        f"With 0 MB hate, Affinity faces zero G1 disruption to its "
        f"0-mana mana rocks (Mox Opal, Springleaf Drum) and "
        f"Cranial Plating attack. See "
        f"docs/diagnostics/2026-05-04_affinity_overperformance_audit.md."
    )


def test_all_listed_decks_still_have_60_mainboard():
    """Decklist edits must preserve the 60-card mainboard count for
    every deck in the parametrised list."""
    for deck_name in ("Boros Energy", "Eldrazi Tron", "Domain Zoo",
                      "Living End"):
        deck = MODERN_DECKS[deck_name]["mainboard"]
        total = sum(deck.values())
        assert total == 60, (
            f"{deck_name} mainboard total is {total}, expected 60."
        )


def test_no_deck_card_exceeds_four_copies():
    """Modern legality: no more than 4 copies of any non-basic-land
    card. Verify the swaps did not push any card above the cap."""
    BASIC_LANDS = {"Mountain", "Plains", "Island", "Swamp",
                   "Forest", "Wastes"}
    for deck_name in ("Boros Energy", "Eldrazi Tron", "Domain Zoo",
                      "Living End"):
        deck = MODERN_DECKS[deck_name]["mainboard"]
        for name, count in deck.items():
            if name in BASIC_LANDS:
                continue
            assert count <= 4, (
                f"{deck_name}: {name} has {count} copies in mainboard; "
                f"Modern legality cap is 4 for non-basic-land cards."
            )
