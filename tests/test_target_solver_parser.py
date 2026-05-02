"""
Tests for ``engine.target_solver.parse``.

Phase 1 of the unified target solver refactor (see
``docs/proposals/2026-05-02_unified_target_solver.md``). These tests
pin the parser's contract before any cast_manager migration, so that
Phase 3's regression test for ``can_cast`` has a known-good parser to
build on.

Test names describe the rule, not a specific card. Cards in oracle
fixtures are illustrative — the parser must work for any card with
the same oracle phrasing, per the abstraction contract.
"""
from __future__ import annotations

import pytest

from engine.target_solver import TargetRequirement, parse


# ── Helpers ─────────────────────────────────────────────────────────

def _only(reqs):
    """Assert exactly one requirement and return it."""
    assert len(reqs) == 1, f"expected 1 requirement, got {len(reqs)}: {reqs}"
    return reqs[0]


def _zones(reqs):
    return [r.zone for r in reqs]


def _types(reqs):
    return [set(r.types) for r in reqs]


# ── 1. No-target oracles return empty list ─────────────────────────

def test_empty_oracle_returns_empty_list():
    assert parse("") == []


def test_no_target_oracle_returns_empty_list():
    # Pure card-draw effect — no target requirement
    assert parse("Draw three cards.") == []


def test_lifegain_no_target_returns_empty_list():
    assert parse("You gain 5 life.") == []


def test_mass_destruction_no_target_returns_empty_list():
    # Wrath-style sweepers: no "target" phrase, no requirement
    assert parse("Destroy all creatures.") == []


# ── 2. Bare "target creature" ──────────────────────────────────────

def test_target_creature_emits_battlefield_creature_any_owner():
    req = _only(parse("Destroy target creature."))
    assert req.zone == "battlefield"
    assert req.types == frozenset({"creature"})
    assert req.owner_scope == "any"
    assert req.is_optional is False


def test_target_creature_you_control_emits_owner_scope_you():
    req = _only(parse("Target creature you control gains flying."))
    assert req.zone == "battlefield"
    assert req.types == frozenset({"creature"})
    assert req.owner_scope == "you"


def test_target_creature_an_opponent_controls_emits_owner_scope_opponent():
    req = _only(parse("Target creature an opponent controls gets -2/-2."))
    assert req.owner_scope == "opponent"


# ── 3. Target permanent / nonland permanent ────────────────────────

def test_target_permanent_emits_permanent_token():
    # Vindicate, Beast Within, Assassin's Trophy
    req = _only(parse("Destroy target permanent."))
    assert req.types == frozenset({"permanent"})
    assert req.zone == "battlefield"


def test_target_nonland_permanent_emits_permanent_nonland():
    # Maelstrom Pulse, Anguished Unmaking
    req = _only(parse("Exile target nonland permanent."))
    assert req.types == frozenset({"permanent_nonland"})


# ── 4. Single-type non-creature battlefield targets ────────────────

def test_target_artifact_emits_artifact():
    # Smelt, Shatter, Ancient Grudge
    req = _only(parse("Destroy target artifact."))
    assert req.types == frozenset({"artifact"})


def test_target_enchantment_emits_enchantment():
    req = _only(parse("Destroy target enchantment."))
    assert req.types == frozenset({"enchantment"})


def test_target_planeswalker_emits_planeswalker():
    req = _only(parse("Deal 5 damage to target planeswalker."))
    assert req.types == frozenset({"planeswalker"})


def test_target_land_emits_land():
    # Wasteland-style
    req = _only(parse("Destroy target land."))
    assert req.types == frozenset({"land"})


# ── 5. Compound types ──────────────────────────────────────────────

def test_target_artifact_or_creature_emits_compound():
    # Abrade, Wear // Tear's Wear half (modal handled separately)
    req = _only(parse("Destroy target artifact or creature."))
    assert req.types == frozenset({"artifact", "creature"})


def test_target_artifact_or_enchantment_emits_compound():
    # Disenchant, Nature's Claim
    req = _only(parse("Destroy target artifact or enchantment."))
    assert req.types == frozenset({"artifact", "enchantment"})


def test_target_creature_or_planeswalker_emits_compound():
    # Galvanic Discharge, modern Bolt variants
    req = _only(parse("Deal 2 damage to target creature or planeswalker."))
    assert req.types == frozenset({"creature", "planeswalker"})


def test_compound_does_not_double_emit_single_type():
    # "target artifact or creature" must NOT also produce a separate
    # bare-creature requirement and a separate bare-artifact requirement.
    # This is the core de-dup invariant for Phase 3 cast_manager
    # migration.
    reqs = parse("Destroy target artifact or creature.")
    assert len(reqs) == 1
    assert reqs[0].types == frozenset({"artifact", "creature"})


# ── 6. Optionality detection ───────────────────────────────────────

def test_up_to_one_target_creature_marks_optional():
    # CR 114.4 — "up to N target X" makes the requirement optional
    req = _only(parse("Up to one target creature gets -3/-3."))
    assert req.is_optional is True


def test_you_may_target_marks_optional():
    req = _only(parse("You may target creature you control."))
    assert req.is_optional is True


def test_plain_target_creature_not_optional():
    req = _only(parse("Destroy target creature."))
    assert req.is_optional is False


def test_distant_you_may_does_not_make_later_target_optional():
    # The 30-char window is intentional; "you may" must be near the
    # "target X" phrase to mark it optional. Otherwise an earlier
    # mode's "you may" would falsely relax a later required target.
    text = (
        "You may pay 2 life as you cast this spell. "
        "If you don't, counter it. "
        "Then destroy target creature."
    )
    req = _only(parse(text))
    assert req.is_optional is False


# ── 7. Graveyard-target patterns ───────────────────────────────────

def test_target_creature_card_from_your_graveyard_emits_gy_zone():
    # Persist (the card), Unburial Rites, Dread Return
    req = _only(parse(
        "Return target creature card from your graveyard to the battlefield."
    ))
    assert req.zone == "graveyard"
    assert req.types == frozenset({"creature"})
    assert req.owner_scope == "you"
    assert req.supertype is None


def test_target_legendary_creature_card_emits_supertype_legendary():
    # Goryo's Vengeance
    req = _only(parse(
        "Return target legendary creature card from your graveyard to "
        "the battlefield."
    ))
    assert req.supertype == "legendary"
    assert req.zone == "graveyard"


def test_target_nonlegendary_creature_card_emits_supertype_nonlegendary():
    # Persist (the card)
    req = _only(parse(
        "Return target nonlegendary creature card from your graveyard "
        "to the battlefield."
    ))
    assert req.supertype == "nonlegendary"


def test_target_card_from_a_graveyard_emits_owner_any():
    # Surgical Extraction, Tormod's Crypt-style targeted exile
    req = _only(parse(
        "Exile target card from a graveyard."
    ))
    assert req.zone == "graveyard"
    assert req.owner_scope == "any"
    assert req.types == frozenset({"card"})


def test_target_creature_in_your_graveyard_emits_gy_zone():
    # "in" instead of "from"
    req = _only(parse(
        "Choose target creature card in your graveyard. "
        "Put it onto the battlefield."
    ))
    assert req.zone == "graveyard"
    assert req.types == frozenset({"creature"})


def test_target_artifact_card_from_your_graveyard_emits_artifact():
    req = _only(parse(
        "Return target artifact card from your graveyard to your hand."
    ))
    assert req.types == frozenset({"artifact"})
    assert req.zone == "graveyard"


# ── 8. Stack-target spells (counterspells) ─────────────────────────

def test_target_spell_emits_stack_zone_spell():
    # Counterspell, Mana Drain
    req = _only(parse("Counter target spell."))
    assert req.zone == "stack"
    assert req.types == frozenset({"spell"})


def test_target_creature_spell_emits_creature_spell_token():
    # Mana Leak (no, that's any), Disdainful Stroke, Essence Capture-style
    req = _only(parse("Counter target creature spell."))
    assert req.types == frozenset({"creature_spell"})


def test_target_noncreature_spell_emits_noncreature_spell_token():
    # Negate
    req = _only(parse("Counter target noncreature spell."))
    assert req.types == frozenset({"noncreature_spell"})


# ── 9. "Any target" — Lightning Bolt family ────────────────────────

def test_any_target_emits_zone_any():
    # Lightning Bolt
    req = _only(parse("Lightning Bolt deals 3 damage to any target."))
    assert req.zone == "any"
    assert req.types == frozenset({"any"})


# ── 10. Player / opponent targeting ────────────────────────────────

def test_target_player_emits_player_token():
    req = _only(parse("Target player draws three cards."))
    assert req.zone == "any"
    assert req.types == frozenset({"player"})
    assert req.owner_scope == "any"


def test_target_opponent_emits_owner_opponent():
    # Hymn to Tourach, Mind Rot
    req = _only(parse("Target opponent discards two cards."))
    assert req.types == frozenset({"player"})
    assert req.owner_scope == "opponent"


# ── 11. Multi-requirement spells ───────────────────────────────────

def test_modal_artifact_or_creature_returns_single_compound():
    # Conservative Q2 default: solver returns the union as a single
    # compound. AI/caller picks the mode at cast time and the
    # legality query checks "any token in the union has a legal
    # candidate".
    req = _only(parse("Choose one — Destroy target artifact or creature."))
    assert req.types == frozenset({"artifact", "creature"})


def test_split_card_independent_halves_each_emit_their_own_target():
    # Split / fuse — solver is called per-half; the cast path knows
    # which half it's casting. This test pins that the parser does
    # NOT try to merge across the "//" boundary; each half is parsed
    # in isolation by the caller. We just confirm the solver doesn't
    # blow up on a multi-target oracle.
    text = (
        "Destroy target artifact. "
        "Destroy target enchantment."
    )
    reqs = parse(text)
    type_sets = _types(reqs)
    assert frozenset({"artifact"}) in (frozenset(s) for s in type_sets)
    assert frozenset({"enchantment"}) in (frozenset(s) for s in type_sets)


# ── 12. Frozen dataclass identity ──────────────────────────────────

def test_target_requirement_is_hashable_for_caching():
    # The dataclass is frozen so Phase 6's AI-side cache can use it
    # as a dict key. Sanity-check.
    req = TargetRequirement(
        zone="battlefield",
        types=frozenset({"creature"}),
    )
    assert hash(req) == hash(req)


def test_target_requirement_types_must_be_frozenset_for_freezability():
    # If anyone changes types: Set[...] -> frozenset got reverted, hashing
    # would break — and the subsequent AI cache silently degrades. Lock
    # the contract.
    req = TargetRequirement(
        zone="battlefield",
        types=frozenset({"creature", "planeswalker"}),
    )
    assert isinstance(req.types, frozenset)
