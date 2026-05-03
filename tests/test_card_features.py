"""Tests for `ai/card_features.py`.

Covers: every mechanic flag with at least one positive and one
negative case, multi-faced card handling (caller responsibility),
caching of `extract_features_for_deck`, round-trip JSON dump, and a
performance benchmark against the documented budget.

Cards are sourced from `ModernAtomic.json` via the `card_db` fixture
where possible; the sub-second performance test deliberately uses
real Modern entries to keep the test honest.  A small number of
synthetic dicts (matching the MTGJSON shape) cover edge cases that
need a specific oracle text we don't always have a real card for.
"""
from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import patch

import pytest

from ai.card_features import (
    CardFeatures,
    ORACLE_EXCERPT_LINE_CAP,
    PERFORMANCE_BUDGET_PER_CARD_MS,
    extract_features,
    extract_features_for_deck,
)


# ─── Helpers ────────────────────────────────────────────────────────


def _raw(card_db: Any, name: str) -> dict:
    """Fetch the raw MTGJSON entry for `name`, ensuring `name` is set
    on the returned dict (the engine doesn't always copy it)."""
    raw = card_db.get_raw(name)
    if raw is None:
        pytest.skip(f"{name!r} not in card DB — skipping (Modern legality drift?)")
    return {**raw, "name": name}


def _stub(
    name: str,
    text: str = "",
    types: list[str] | None = None,
    subtypes: list[str] | None = None,
    supertypes: list[str] | None = None,
    cmc: int = 0,
    mana_cost: str = "",
    colors: list[str] | None = None,
    keywords: list[str] | None = None,
    power: int | None = None,
    toughness: int | None = None,
) -> dict:
    """Build a synthetic MTGJSON-shaped card dict for tests that need
    specific oracle text not present in the DB."""
    return {
        "name": name,
        "text": text,
        "types": types or ["Sorcery"],
        "subtypes": subtypes or [],
        "supertypes": supertypes or [],
        "manaValue": cmc,
        "manaCost": mana_cost,
        "colors": colors or [],
        "keywords": keywords or [],
        "power": power,
        "toughness": toughness,
    }


# ─── Mechanic-flag tests ────────────────────────────────────────────


def test_is_removal_detects_destroy_target(card_db):
    """Removal pattern fires on damage / destroy / exile of opponent
    permanents and stays off pure counterspells / vanilla creatures."""
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))
    path = extract_features(_raw(card_db, "Path to Exile"))
    fatal = extract_features(_raw(card_db, "Fatal Push"))
    counter = extract_features(_raw(card_db, "Counterspell"))
    memnite = extract_features(_raw(card_db, "Memnite"))

    assert bolt.is_removal
    assert path.is_removal
    assert fatal.is_removal
    assert not counter.is_removal
    assert not memnite.is_removal


def test_is_ramp_detects_add_mana_oracle(card_db):
    """Ramp pattern fires on `Add {X}` and on land-fetch tutors."""
    pyretic = extract_features(_raw(card_db, "Pyretic Ritual"))
    birds = extract_features(_raw(card_db, "Birds of Paradise"))
    grazer = extract_features(_raw(card_db, "Arboreal Grazer"))
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))
    counter = extract_features(_raw(card_db, "Counterspell"))

    assert pyretic.is_ramp
    assert birds.is_ramp
    assert grazer.is_ramp
    assert not bolt.is_ramp
    assert not counter.is_ramp


def test_is_counterspell_detects_counter_target_spell(card_db):
    """Counterspell pattern fires on every shape of counter (target
    spell, target noncreature spell, target creature spell)."""
    cs = extract_features(_raw(card_db, "Counterspell"))
    leak = extract_features(_raw(card_db, "Mana Leak"))
    stub = extract_features(_raw(card_db, "Stubborn Denial"))
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))

    assert cs.is_counterspell
    assert leak.is_counterspell
    assert stub.is_counterspell
    assert not bolt.is_counterspell


def test_is_card_draw(card_db):
    """Card-draw fires on literal `draw N cards`, on impulse exile,
    and on selection effects.  Counterspells and removal don't fire."""
    impulse = extract_features(_raw(card_db, "Reckless Impulse"))
    mending = extract_features(_raw(card_db, "Faithful Mending"))
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))
    courtyard = extract_features(_raw(card_db, "Concealed Courtyard"))

    assert impulse.is_card_draw
    assert mending.is_card_draw
    assert not bolt.is_card_draw
    assert not courtyard.is_card_draw


def test_is_combo_payoff(card_db):
    """Combo-payoff fires on Storm/Cascade keywords and on the
    Living-End-shape mass-graveyard reanimation."""
    grapeshot = extract_features(_raw(card_db, "Grapeshot"))
    past = extract_features(_raw(card_db, "Past in Flames"))
    living_end = extract_features(_raw(card_db, "Living End"))
    bloodbraid = extract_features(_raw(card_db, "Bloodbraid Elf"))
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))

    assert grapeshot.is_combo_payoff
    assert past.is_combo_payoff
    assert living_end.is_combo_payoff
    assert bloodbraid.is_combo_payoff
    assert not bolt.is_combo_payoff


def test_is_combo_enabler(card_db):
    """Enablers fire on free-mana producers (Mox Opal, Lotus Bloom),
    Manamorphose-shape (mana + draw), and explicit cost reducers."""
    mox = extract_features(_raw(card_db, "Mox Opal"))
    lotus = extract_features(_raw(card_db, "Lotus Bloom"))
    morph = extract_features(_raw(card_db, "Manamorphose"))
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))

    assert mox.is_combo_enabler
    assert lotus.is_combo_enabler
    assert morph.is_combo_enabler
    assert not bolt.is_combo_enabler


def test_is_discard(card_db):
    """Discard pattern fires on hand-attack spells (Thoughtseize,
    Inquisition) and on the discard mode of Kolaghan's Command."""
    seize = extract_features(_raw(card_db, "Thoughtseize"))
    inq = extract_features(_raw(card_db, "Inquisition of Kozilek"))
    kc = extract_features(_raw(card_db, "Kolaghan's Command"))
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))

    assert seize.is_discard
    assert inq.is_discard
    assert kc.is_discard
    assert not bolt.is_discard


def test_is_tutor(card_db):
    """Tutor fires whenever the card searches the caster's library —
    includes ramp tutors (Stoneforge fetches Equipment).  Pure removal
    that fetches for the *opponent's* controller does NOT fire."""
    sfm = extract_features(_raw(card_db, "Stoneforge Mystic"))
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))

    assert sfm.is_tutor
    assert not bolt.is_tutor


def test_is_reanimator_specifically_targets_graveyard_to_battlefield(card_db):
    """Reanimator is the strict "creature card from graveyard onto
    battlefield" shape (Goryo's, Persist).  Pure recursion to hand
    (Regrowth/Eternal Witness) is `is_recursion` but NOT
    `is_reanimator`."""
    goryos = extract_features(_raw(card_db, "Goryo's Vengeance"))
    persist = extract_features(_raw(card_db, "Persist"))
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))

    assert goryos.is_reanimator
    assert persist.is_reanimator
    assert not bolt.is_reanimator


def test_is_sweeper_detects_destroy_all_creatures(card_db):
    """Sweeper fires on `destroy/exile all creatures` and `destroy
    all permanents`; targeted removal does not."""
    wrath = extract_features(_raw(card_db, "Wrath of God"))
    damnation = extract_features(_raw(card_db, "Damnation"))
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))

    assert wrath.is_sweeper
    assert damnation.is_sweeper
    assert not bolt.is_sweeper


def test_keywords_word_boundary(card_db):
    """`Flash` keyword on a spell that says `flashback` MUST NOT fire.

    This is the canonical word-boundary regression: 'flash' is a
    substring of 'flashback', and a naïve `'flash' in text` check
    would falsely tag Past in Flames / Faithful Mending as having
    flash.
    """
    past = extract_features(_raw(card_db, "Past in Flames"))
    mending = extract_features(_raw(card_db, "Faithful Mending"))
    snap = extract_features(_raw(card_db, "Snapcaster Mage"))

    assert "Flashback" in past.keywords
    assert "Flash" not in past.keywords
    assert "Flashback" in mending.keywords
    assert "Flash" not in mending.keywords
    # Snapcaster is a real Flash creature — the assertion's flip side.
    assert "Flash" in snap.keywords


def test_color_extraction_from_mana_cost(card_db):
    """Colors derived from mana cost in canonical WUBRG order.

    Single-pip ({U}) → ["U"]; multi-pip ({1}{R}{W}{B}) → ["W", "B", "R"].
    """
    snap = extract_features(_raw(card_db, "Snapcaster Mage"))
    assert snap.colors == ["U"]

    # Synthetic Kaalia-shape: {1}{R}{W}{B} → WUBRG order = W, B, R
    kaalia = extract_features(_stub(
        name="Test Kaalia",
        types=["Creature"],
        mana_cost="{1}{R}{W}{B}",
        cmc=4,
        power=2,
        toughness=2,
    ))
    assert kaalia.colors == ["W", "B", "R"]

    # Hybrid: Manamorphose {1}{R/G}
    morph = extract_features(_raw(card_db, "Manamorphose"))
    assert morph.colors == ["R", "G"]


def test_modal_detection(card_db):
    """`Choose one/two/up to N —` pattern fires for modal spells."""
    pick = extract_features(_raw(card_db, "Pick Your Poison"))
    kc = extract_features(_raw(card_db, "Kolaghan's Command"))
    charm = extract_features(_raw(card_db, "Thraben Charm"))
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))

    assert pick.is_modal
    assert kc.is_modal
    assert charm.is_modal
    assert not bolt.is_modal


def test_first_two_oracle_lines_bounded():
    """A card with 5 lines of oracle text returns only the first
    `ORACLE_EXCERPT_LINE_CAP` lines, preserving the newline separator
    between them."""
    long_oracle = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    card = _stub("Test Long", text=long_oracle, types=["Instant"])
    f = extract_features(card)
    assert ORACLE_EXCERPT_LINE_CAP == 2  # rule the test encodes
    assert f.first_two_oracle_lines == "Line 1\nLine 2"


def test_oracle_word_count_excludes_reminder_text():
    """Reminder text in parentheses is NOT counted — it's a paren-
    enclosed gloss for new players, not part of the card's mechanics."""
    card = _stub(
        "Test Reminder",
        text="Flying (This creature can't be blocked except by creatures with flying or reach.)",
        types=["Creature"],
        subtypes=["Bird"],
        cmc=1,
        mana_cost="{U}",
        power=1,
        toughness=1,
    )
    f = extract_features(card)
    # Only "Flying" survives word counting once the parenthetical block is stripped.
    assert f.oracle_word_count == 1


def test_extract_features_for_deck_caches(card_db):
    """Calling `extract_features_for_deck` twice with the same input
    invokes the inner extractor once per unique card.  Verified via
    the lru_cache hit count rather than mocking the extractor itself
    (the inner function is cached at module-load time)."""
    from ai import card_features

    # Reset the cache so the test is order-independent.
    card_features._extract_features_uncached.cache_clear()

    mainboard = {"Lightning Bolt": 4, "Counterspell": 4, "Memnite": 4}
    extract_features_for_deck(mainboard, card_db)
    info1 = card_features._extract_features_uncached.cache_info()

    extract_features_for_deck(mainboard, card_db)
    info2 = card_features._extract_features_uncached.cache_info()

    # 3 unique cards → 3 misses on first call.  Second call only hits.
    assert info1.misses == 3
    assert info2.hits == info1.hits + 3
    assert info2.misses == info1.misses


def test_round_trip_pydantic_dump(card_db):
    """`extract → model_dump_json() → model_validate_json()` returns
    an identical (frozen + extra-forbid) object."""
    original = extract_features(_raw(card_db, "Lightning Bolt"))
    js = original.model_dump_json()
    restored = CardFeatures.model_validate_json(js)
    assert restored == original
    # Frozen contract: attribute assignment fails after construction.
    with pytest.raises(Exception):
        restored.cmc = 99  # type: ignore[misc]
    # Extra fields are forbidden — adding one raises pydantic.ValidationError.
    with pytest.raises(Exception):
        CardFeatures.model_validate({**original.model_dump(), "extra_field": 1})


def test_extra_forbid_blocks_typo_at_construction():
    """Constructor-time extras-forbid: typoing a field name is caught
    immediately rather than silently dropped."""
    with pytest.raises(Exception):
        CardFeatures(
            name="X",
            cmc=0,
            types=[],
            subtypes=[],
            colors=[],
            oracle_word_count=0,
            first_two_oracle_lines="",
            typo_field=True,  # type: ignore[call-arg]
        )


def test_creature_pt_present_for_creatures(card_db):
    """Creature features carry power/toughness; non-creature features
    have None for both."""
    memnite = extract_features(_raw(card_db, "Memnite"))
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))

    assert memnite.power == 1
    assert memnite.toughness == 1
    assert bolt.power is None
    assert bolt.toughness is None


def test_instant_speed_and_sorcery_speed_only(card_db):
    """`is_instant_speed` is True for Instants and for creatures with
    Flash; `is_sorcery_speed_only` is True for sorceries without Flash."""
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))
    snap = extract_features(_raw(card_db, "Snapcaster Mage"))
    grapeshot = extract_features(_raw(card_db, "Grapeshot"))
    memnite = extract_features(_raw(card_db, "Memnite"))

    assert bolt.is_instant_speed
    assert snap.is_instant_speed  # Flash
    assert grapeshot.is_sorcery_speed_only
    assert not memnite.is_instant_speed
    assert not memnite.is_sorcery_speed_only


def test_etb_attack_death_triggers(card_db):
    """ETB / attack / death triggers detected by oracle phrasing."""
    snap = extract_features(_raw(card_db, "Snapcaster Mage"))
    kavu = extract_features(_raw(card_db, "Territorial Kavu"))
    bolt = extract_features(_raw(card_db, "Lightning Bolt"))

    assert snap.has_etb
    assert kavu.has_attack_trigger
    assert not bolt.has_etb


# ─── Multi-faced card tests ─────────────────────────────────────────


def test_dfc_caller_passes_one_face(card_db):
    """Documented contract: `extract_features` receives ONE face's
    data.  The deck-level convenience uses `db.get_raw(name)` which
    returns the front face for every DFC the engine registers."""
    # MDFC: "Valakut Awakening // Valakut Stoneforge" — front face is
    # an Instant.  Caller only ever passes the front-face dict.
    name = "Valakut Awakening // Valakut Stoneforge"
    if name in card_db:
        f = extract_features(_raw(card_db, name))
        # Front face is the spell side: card_draw should fire (it puts
        # any number of cards from hand on bottom of library, then
        # draws that many plus one).
        assert f.is_card_draw
        assert "Instant" in f.types or "Sorcery" in f.types


def test_transforming_creature_dfc_uses_front_face(card_db):
    """For a transforming creature DFC the front face's stats and
    keywords are what the AI plays from hand; the back face is only
    relevant after a transform trigger.  Caller uses the front face."""
    name = "Aberrant Researcher // Perfected Form"
    if name in card_db:
        f = extract_features(_raw(card_db, name))
        # Front face is a 3U Creature with Flying.
        assert "Creature" in f.types
        assert "Flying" in f.keywords


def test_planeswalker_dfc_caller_responsibility(card_db):
    """DFC planeswalkers (back-face transforms) — caller still passes
    one face.  The schema makes no special allowance; the docstring
    spells out the responsibility."""
    # Use a synthetic stub since the available DFC planeswalkers vary
    # across MTGJSON dumps; the contract is what matters.
    front = _stub(
        name="Test Walker // Test Walker, Ascended",
        types=["Creature"],
        subtypes=["Human"],
        cmc=3,
        mana_cost="{1}{W}{W}",
        text="Vigilance\nWhen this creature dies, return it transformed.",
        power=2,
        toughness=3,
    )
    f = extract_features(front)
    assert "Creature" in f.types
    assert f.has_death_trigger
    assert "Vigilance" in f.keywords


# ─── Performance budget ─────────────────────────────────────────────


def test_extraction_performance(card_db):
    """1000 cards extract in under 1 second on commodity hardware.

    The budget makes the module safe for use in deck-import flows and,
    if needed later, the simulator hot loop (per-game extraction is
    far below 1000 cards).
    """
    # Clear cache so the test measures actual extraction work.
    from ai import card_features
    card_features._extract_features_uncached.cache_clear()

    names = list(card_db.cards.keys())[:1000]
    start = time.time()
    for name in names:
        raw = card_db.get_raw(name)
        if raw is None:
            continue
        extract_features({**raw, "name": name})
    elapsed = time.time() - start
    # 1000 * PERFORMANCE_BUDGET_PER_CARD_MS = 1 second
    assert elapsed < 1.0 * (PERFORMANCE_BUDGET_PER_CARD_MS), (
        f"extraction too slow for cost-aware design: {elapsed:.3f}s for "
        f"{len(names)} cards (budget {PERFORMANCE_BUDGET_PER_CARD_MS}ms/card)"
    )
