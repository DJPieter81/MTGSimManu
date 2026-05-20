"""Phase 2A — Sideboard anti-artifact categorization is oracle-driven.

Rule under test
---------------
Sideboard plans against an artifact-dense deck (Affinity, Pinnacle
Affinity, Eldrazi Tron) must distinguish three categories of
anti-artifact effect by oracle text, with priorities:

  - **Destruction (priority 10)** — destroys/exiles/bounces the
    artifact base. Cards: Wear // Tear, Force of Vigor, Boseiju
    (channel), Shattering Spree, Hurkyl's Recall, Meltdown,
    Haywire Mite. Oracle pattern: "destroy target artifact",
    "destroy each artifact", "exile target artifact", "return all
    artifacts target player owns".

  - **Stax / cost-tax (priority 9)** — slows artifact decks by
    locking activated abilities or taxing extra mana. Cards:
    Stony Silence, Collector Ouphe, Damping Sphere, Clarion
    Conqueror, Karn the Great Creator. Oracle pattern: "activated
    abilities of artifacts can't be activated" or "tapped for two
    or more mana, it produces {C} instead" (Damping Sphere idiom).

  - **Activated-ability lock (priority 5, demoted)** — locks ONE
    chosen permanent's activated ability. Cards: Pithing Needle,
    Phyrexian Revoker. Useful situationally (Mox Opal lock) but
    NOT a substitute for destruction; demote so opponents prefer
    real removal first.

Pre-fix bug
-----------
``engine/sideboard_manager.py:66-72`` used a flat keyword list
``["wear", "force of vigor", "collector", "haywire", "shattering",
"hurkyl", "pithing", "meltdown", "boseiju", "time raveler",
"orchid phantom", "clarion conqueror"]`` and assigned the same
priority 9 to every match. The list:

  - Missed Damping Sphere entirely (4 of our 16 decks carry it
    in SB but it never gets boarded in vs Affinity).
  - Treated Pithing Needle (single-target lock) as priority 9 —
    same as Wear // Tear (true destruction).
  - Falsely matched Teferi, Time Raveler (tempo, not artifact
    pressure) as artifact hate.
  - Falsely matched White Orchid Phantom (LD vs nonbasic, only
    incidentally catches artifact lands).

Reference: /root/.claude/plans/now-lets-fix-affinity-keen-penguin.md
Phase 2A.
"""
from __future__ import annotations

import pytest


# ─── True destruction (priority 10) ──────────────────────────────────


@pytest.mark.parametrize(
    "card_name",
    [
        "Wear",
        "Wear // Tear",
        "Force of Vigor",
        "Boseiju, Who Endures",
        "Shattering Spree",
        "Hurkyl's Recall",
        "Meltdown",
        "Haywire Mite",
    ],
)
def test_destruction_cards_classified_priority_10(card_db, card_name):
    """All cards whose oracle includes a destroy/exile/bounce of
    artifacts must be classified as priority 10."""
    from engine.sideboard_manager import _classify_anti_artifact_priority
    tmpl = card_db.get_card(card_name)
    if tmpl is None:
        pytest.skip(f"{card_name} not in DB build")
    priority = _classify_anti_artifact_priority(tmpl)
    assert priority == 10, (
        f"{card_name} oracle directly destroys/exiles/bounces "
        f"artifacts and must be classified as priority 10. Got "
        f"priority={priority}. Oracle: "
        f"{(tmpl.oracle_text or '')[:200]}"
    )


# ─── Stax / cost tax (priority 9) ────────────────────────────────────


@pytest.mark.parametrize(
    "card_name",
    [
        "Damping Sphere",
        "Stony Silence",
        "Collector Ouphe",
        "Clarion Conqueror",
        "Karn, the Great Creator",
    ],
)
def test_stax_cards_classified_priority_9(card_db, card_name):
    """Cards that lock activated abilities or tax artifact-spell
    sequencing must be classified as priority 9."""
    from engine.sideboard_manager import _classify_anti_artifact_priority
    tmpl = card_db.get_card(card_name)
    if tmpl is None:
        pytest.skip(f"{card_name} not in DB build")
    priority = _classify_anti_artifact_priority(tmpl)
    assert priority == 9, (
        f"{card_name} is artifact stax/lock and must be classified "
        f"as priority 9. Got priority={priority}. Oracle: "
        f"{(tmpl.oracle_text or '')[:200]}"
    )


# ─── Single-target locks (priority 5, demoted) ───────────────────────


@pytest.mark.parametrize(
    "card_name",
    [
        "Pithing Needle",
        "Phyrexian Revoker",
    ],
)
def test_single_target_locks_classified_priority_5(card_db, card_name):
    """Cards that name ONE permanent and lock its activated abilities
    are useful but narrow — must be demoted to priority 5 so
    opponents prefer destruction first."""
    from engine.sideboard_manager import _classify_anti_artifact_priority
    tmpl = card_db.get_card(card_name)
    if tmpl is None:
        pytest.skip(f"{card_name} not in DB build")
    priority = _classify_anti_artifact_priority(tmpl)
    assert priority == 5, (
        f"{card_name} is a single-target activated-ability lock — "
        f"useful but narrow vs an 18-artifact deck. Must be priority "
        f"5 (demoted). Got priority={priority}."
    )


# ─── Non-anti-artifact cards (priority 0) ────────────────────────────


@pytest.mark.parametrize(
    "card_name",
    [
        "Teferi, Time Raveler",  # tempo / sorcery-speed lock
        "White Orchid Phantom",  # LD vs nonbasic, NOT artifact-specific
        "Lightning Bolt",
        "Counterspell",
        "Memnite",
    ],
)
def test_non_anti_artifact_classified_zero(card_db, card_name):
    """Cards whose oracle does NOT match any of the three anti-
    artifact patterns must be classified as priority 0 (don't
    board in for the artifact-hate slot)."""
    from engine.sideboard_manager import _classify_anti_artifact_priority
    tmpl = card_db.get_card(card_name)
    if tmpl is None:
        pytest.skip(f"{card_name} not in DB build")
    priority = _classify_anti_artifact_priority(tmpl)
    assert priority == 0, (
        f"{card_name} is NOT artifact hate — its oracle does not "
        f"match any of destruction / stax / single-target-lock. "
        f"Got priority={priority}."
    )


# ─── Integration: real sideboard plans bring Damping Sphere ──────────


def test_boros_brings_damping_sphere_vs_affinity():
    """End-to-end: Boros Energy's sideboard contains Damping Sphere.
    Boarding vs Affinity must move Damping Sphere from SB into MB."""
    from engine.sideboard_manager import sideboard
    from decks.modern_meta import MODERN_DECKS
    boros = MODERN_DECKS.get("Boros Energy")
    if boros is None:
        pytest.skip("Boros Energy not in MODERN_DECKS")
    if "Damping Sphere" not in boros.get("sideboard", {}):
        pytest.skip("Boros Energy SB doesn't contain Damping Sphere "
                    "in this build — test premise no longer applies.")

    new_main, new_side = sideboard(
        boros["mainboard"], boros["sideboard"],
        "Boros Energy", "Affinity",
    )
    main_count = new_main.get("Damping Sphere", 0)
    side_count = new_side.get("Damping Sphere", 0)
    original_side = boros["sideboard"].get("Damping Sphere", 0)
    assert main_count > 0, (
        f"Boros Energy must board Damping Sphere INTO mainboard vs "
        f"Affinity. Got mainboard count={main_count}. Pre-fix the "
        f"flat keyword list missed Damping Sphere entirely. "
        f"Original SB count was {original_side}, post-board SB count "
        f"is {side_count}."
    )
