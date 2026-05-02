"""H_ACT_3 — sideboard manager keyword filter must match the
anti-Affinity tech that real Modern decks run.

Phase 7 matrix validation (n=50) showed Affinity at 85.7% overall
WR. The cast-time fix (PR #222) was correctness-only, not a WR
mover; the gap is AI-side. One of the AI-side gaps is the legacy
keyword filter in ``engine/sideboard_manager.py`` which misses
several anti-Affinity sideboard staples — leaving 8 of 10 top
decks under-tuned in the matchup.

Confirmed gaps (per
``docs/diagnostics/2026-05-02_affinity_88pct_hypothesis_list.md``
H_ACT_3, dumped in the experiment doc):

  Damping Sphere       — cost-tax vs Affinity's mana ramp
  Subtlety             — flash bounce of Construct/Mox at instant speed
  Foundation Breaker   — Living End's evoke artifact removal
  Trinisphere          — Eldrazi Tron's tax piece
  Endurance            — flash 3/4 reach blocker (Living End)
  Force of Vigor       — free-cast 2-for-1 artifact destruction
  Force of Negation    — free-cast counterspell

Each of these appears in at least one top deck's sideboard but is
not currently boarded in vs Affinity. Extending the keyword filter
fixes the class.

Tests pin the new behavior — given a deck with one of these cards
in its sideboard and a Affinity-shaped opponent name, the card
must end up in the boarded-in priority list (i.e., post-sideboard
mainboard contains the card).
"""
from __future__ import annotations

import pytest

from engine.sideboard_manager import sideboard


def _make_dummy_main(swap_target: str, cmc: int = 4) -> dict:
    """A mainboard that contains a card the sideboarder will choose
    to swap out. ``swap_target`` is one of the names that the
    board-out keyword filter recognizes for the artifact-aggro
    matchup (e.g., 'Undying Evil', 'Fable of the Mirror-Breaker').
    """
    return {
        swap_target: 2,
        # Pad the mainboard so total >= 60 — sideboard() doesn't enforce
        # this but realistic fixtures look better.
        "Plains": 24,
        "Lightning Bolt": 4,
        "Memnite": 4,
        "Goblin Guide": 4,
        "Boros Charm": 4,
        "Mox Opal": 1,
        "Some Random Filler": 17,
    }


# ── Damping Sphere ─────────────────────────────────────────────────


def test_damping_sphere_boards_in_vs_affinity():
    main = _make_dummy_main("Undying Evil")
    sb = {"Damping Sphere": 3}
    new_main, _ = sideboard(main, sb, "Boros Energy", "Affinity")
    assert new_main.get("Damping Sphere", 0) >= 1, (
        "Damping Sphere must board in vs Affinity. It taxes "
        "Affinity's cheap-artifact ramp and is a Modern staple in "
        "Azorius / WST sideboards. Pre-fix the legacy keyword filter "
        "had no token for 'damping' under the artifact-hate branch."
    )


def test_damping_sphere_boards_in_vs_pinnacle_affinity():
    main = _make_dummy_main("Undying Evil")
    sb = {"Damping Sphere": 3}
    new_main, _ = sideboard(main, sb, "Boros Energy", "Pinnacle Affinity")
    assert new_main.get("Damping Sphere", 0) >= 1


# ── Foundation Breaker ─────────────────────────────────────────────


def test_foundation_breaker_boards_in_vs_affinity():
    # Foundation Breaker — Living End's evoke 2-for-1 artifact removal.
    # Pre-fix the keyword filter matched "force of vigor" but not
    # "foundation"/"breaker", so 3-of pieces in Living End SB sat unused.
    main = _make_dummy_main("Bombardment")
    sb = {"Foundation Breaker": 3}
    new_main, _ = sideboard(main, sb, "Living End", "Affinity")
    assert new_main.get("Foundation Breaker", 0) >= 1


# ── Subtlety ───────────────────────────────────────────────────────


def test_subtlety_boards_in_vs_affinity():
    main = _make_dummy_main("Witch Enchanter")
    sb = {"Subtlety": 3}
    new_main, _ = sideboard(main, sb, "Azorius Control (WST)", "Affinity")
    assert new_main.get("Subtlety", 0) >= 1, (
        "Subtlety is flash bounce of Construct/Mox tokens at instant "
        "speed — top-tier anti-Affinity tech in WST and Azorius "
        "Control sideboards."
    )


# ── Trinisphere ────────────────────────────────────────────────────


def test_trinisphere_boards_in_vs_affinity():
    # Trinisphere — Eldrazi Tron's tax piece. Affinity's <1cmc
    # artifacts (Mox Opal, Memnite, Signal Pest) all become {3} —
    # critical disruption.
    main = _make_dummy_main("Kozilek's Command")
    sb = {"Trinisphere": 2}
    new_main, _ = sideboard(main, sb, "Eldrazi Tron", "Affinity")
    assert new_main.get("Trinisphere", 0) >= 1


# ── Endurance (flash blocker — anti-aggro, not anti-graveyard) ─────


def test_endurance_boards_in_vs_affinity_as_flash_blocker():
    # Endurance is multipurpose: flash + 3/4 + reach in graveyard
    # decks, OR a flash blocker vs ground attackers in Affinity. The
    # diagnostic doc explicitly notes that Living End's Endurance
    # boards-in vs Affinity (3/4 reach blocker shuts down Plating's
    # ground game), but the legacy filter only matched "endurance"
    # under graveyard-hate (vs Goryo's / Living End / Dredge), not
    # vs Affinity.
    main = _make_dummy_main("Foundation Breaker")
    sb = {"Endurance": 3}
    new_main, _ = sideboard(main, sb, "Living End", "Affinity")
    assert new_main.get("Endurance", 0) >= 1


# ── Force of Vigor + Force of Negation ─────────────────────────────


def test_force_of_vigor_boards_in_vs_affinity():
    # Force of Vigor — free-cast 2-for-1 artifact destruction. 4c
    # Omnath / Living End run it. Pre-fix it matched the "force of
    # vigor" keyword only inside the Tron/Affinity/Pinnacle artifact
    # branch — but the test confirms it actually IS matched there;
    # the regression was from limited `max_swaps`. Pin the behavior
    # so the fix doesn't drop "force of vigor" from the keyword
    # list during the broader rewrite.
    main = _make_dummy_main("Consign to Memory")
    sb = {"Force of Vigor": 2}
    new_main, _ = sideboard(main, sb, "4c Omnath", "Affinity")
    assert new_main.get("Force of Vigor", 0) >= 1


def test_force_of_negation_boards_in_vs_affinity():
    # Force of Negation — free-cast counterspell. 4c Omnath SB has
    # 2 copies. Pre-fix the counterspell branch fires for combo
    # opponents only ("storm" / "living end" / "goryo" / "titan");
    # the H_ACT_3 fix extends it to artifact aggro ("affinity",
    # "pinnacle") since cheap counterspells are excellent vs T4-
    # kill artifact aggro.
    main = _make_dummy_main("Consign to Memory")
    sb = {"Force of Negation": 2}
    new_main, _ = sideboard(main, sb, "4c Omnath", "Affinity")
    assert new_main.get("Force of Negation", 0) >= 1
