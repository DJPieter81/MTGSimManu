"""
Tests for ``engine.target_solver.has_legal_target`` and
``enumerate_legal_targets``.

Phase 2 of the unified target solver refactor (see
``docs/proposals/2026-05-02_unified_target_solver.md``). These tests
exercise the legality query against real game-state fixtures so
that Phase 3's cast_manager migration has a verified backend before
the call sites flip.

Test names describe the rule, not a specific card. Cards in
fixtures are illustrative — the solver's contract is generic over
oracle text and game state.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardType, Supertype
from engine.game_state import GameState
from engine.target_solver import (
    TargetRequirement,
    enumerate_legal_targets,
    has_legal_target,
    has_legal_target_for_spell,
    parse,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _battlefield(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _graveyard(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="graveyard",
    )
    card._game_state = game
    game.players[controller].graveyard.append(card)
    return card


def _hand(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _new_game():
    return GameState(rng=random.Random(0))


# ── 1. Battlefield single-type ──────────────────────────────────────


def test_target_creature_legal_with_creature_on_own_board(card_db):
    game = _new_game()
    _battlefield(game, card_db, "Memnite", 0)
    req = parse("Destroy target creature.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_creature_legal_with_creature_on_opp_board(card_db):
    game = _new_game()
    _battlefield(game, card_db, "Memnite", 1)
    req = parse("Destroy target creature.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_creature_illegal_with_no_creatures(card_db):
    game = _new_game()
    req = parse("Destroy target creature.")[0]
    assert has_legal_target(game, 0, req) is False


def test_target_creature_you_control_illegal_when_only_opp_has_creature(card_db):
    game = _new_game()
    _battlefield(game, card_db, "Memnite", 1)
    req = parse("Target creature you control gains flying.")[0]
    assert has_legal_target(game, 0, req) is False


def test_target_creature_you_control_legal_when_own_creature_exists(card_db):
    game = _new_game()
    _battlefield(game, card_db, "Memnite", 0)
    req = parse("Target creature you control gains flying.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_artifact_legal_with_own_artifact(card_db):
    game = _new_game()
    _battlefield(game, card_db, "Mox Opal", 0)
    req = parse("Destroy target artifact.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_artifact_legal_with_opp_artifact(card_db):
    game = _new_game()
    _battlefield(game, card_db, "Mox Opal", 1)
    req = parse("Destroy target artifact.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_artifact_illegal_with_no_artifacts(card_db):
    # Tarmogoyf — a creature that is not an artifact. Pinning a non-
    # artifact-creature fixture lets the negative-path assertion be
    # unconditional.
    game = _new_game()
    _battlefield(game, card_db, "Tarmogoyf", 1)
    assert CardType.ARTIFACT not in game.players[1].battlefield[0].template.card_types
    req = parse("Destroy target artifact.")[0]
    assert has_legal_target(game, 0, req) is False


def test_artifact_land_counts_as_artifact_target(card_db):
    # CR 205.4b — an artifact land is both. "target artifact" accepts it.
    game = _new_game()
    _battlefield(game, card_db, "Tanglepool Bridge", 1)
    req = parse("Destroy target artifact.")[0]
    assert has_legal_target(game, 0, req) is True


# ── 2. Compound types ──────────────────────────────────────────────


def test_target_artifact_or_creature_legal_with_only_creature(card_db):
    game = _new_game()
    _battlefield(game, card_db, "Memnite", 1)
    req = parse("Destroy target artifact or creature.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_artifact_or_creature_legal_with_only_artifact(card_db):
    game = _new_game()
    _battlefield(game, card_db, "Mox Opal", 1)
    req = parse("Destroy target artifact or creature.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_artifact_or_creature_illegal_with_neither(card_db):
    game = _new_game()
    req = parse("Destroy target artifact or creature.")[0]
    assert has_legal_target(game, 0, req) is False


def test_target_artifact_or_enchantment_legal_with_enchantment(card_db):
    game = _new_game()
    # Pick a known enchantment in the DB
    e = card_db.get_card("Leyline Binding")
    if e is None or CardType.ENCHANTMENT not in e.card_types:
        pytest.skip("DB lacks Leyline Binding for this fixture")
    _battlefield(game, card_db, "Leyline Binding", 1)
    req = parse("Destroy target artifact or enchantment.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_artifact_or_enchantment_illegal_with_only_creature(card_db):
    # Tarmogoyf — non-artifact non-enchantment creature. Negative
    # path: Disenchant-style spells fizzle when no artifact and no
    # enchantment are on either battlefield.
    game = _new_game()
    _battlefield(game, card_db, "Tarmogoyf", 1)
    req = parse("Destroy target artifact or enchantment.")[0]
    assert has_legal_target(game, 0, req) is False


# ── 3. Permanent / nonland-permanent ───────────────────────────────


def test_target_permanent_legal_with_any_permanent(card_db):
    game = _new_game()
    _battlefield(game, card_db, "Memnite", 1)
    req = parse("Destroy target permanent.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_permanent_legal_with_only_a_land(card_db):
    # "target permanent" accepts lands (CR 205.4 — lands are permanents)
    game = _new_game()
    _battlefield(game, card_db, "Plains", 1)
    req = parse("Destroy target permanent.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_permanent_illegal_with_empty_boards(card_db):
    game = _new_game()
    req = parse("Destroy target permanent.")[0]
    assert has_legal_target(game, 0, req) is False


def test_target_nonland_permanent_legal_with_creature(card_db):
    game = _new_game()
    _battlefield(game, card_db, "Memnite", 1)
    req = parse("Exile target nonland permanent.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_nonland_permanent_illegal_with_only_lands(card_db):
    game = _new_game()
    _battlefield(game, card_db, "Plains", 1)
    _battlefield(game, card_db, "Plains", 0)
    req = parse("Exile target nonland permanent.")[0]
    assert has_legal_target(game, 0, req) is False


# ── 4. Graveyard targets ───────────────────────────────────────────


def test_target_creature_card_in_your_graveyard_legal_with_creature(card_db):
    game = _new_game()
    _graveyard(game, card_db, "Memnite", 0)
    reqs = parse(
        "Return target creature card from your graveyard to the battlefield."
    )
    req = reqs[0]
    assert has_legal_target(game, 0, req) is True


def test_target_creature_card_in_your_graveyard_illegal_when_empty(card_db):
    game = _new_game()
    reqs = parse(
        "Return target creature card from your graveyard to the battlefield."
    )
    req = reqs[0]
    assert has_legal_target(game, 0, req) is False


def test_target_legendary_creature_card_legal_only_with_legendary(card_db):
    # Goryo's Vengeance — supertype filter must reject non-legendary
    game = _new_game()
    _graveyard(game, card_db, "Memnite", 0)  # nonlegendary
    reqs = parse(
        "Return target legendary creature card from your graveyard to "
        "the battlefield."
    )
    req = reqs[0]
    assert req.supertype == "legendary"
    if Supertype.LEGENDARY not in (game.players[0].graveyard[0].template.supertypes or []):
        assert has_legal_target(game, 0, req) is False
    else:
        pytest.skip("Memnite mis-tagged as legendary in this DB")


def test_target_legendary_creature_card_legal_with_legendary_in_gy(card_db):
    game = _new_game()
    legendary_creature = None
    for name in ("Atraxa, Grand Unifier", "Emrakul, the Aeons Torn",
                 "Ulamog, the Infinite Gyre", "Griselbrand"):
        tmpl = card_db.get_card(name)
        if tmpl is not None and tmpl.is_creature and \
                Supertype.LEGENDARY in (tmpl.supertypes or []):
            legendary_creature = name
            break
    if legendary_creature is None:
        pytest.skip("DB has no legendary creature for this fixture")
    _graveyard(game, card_db, legendary_creature, 0)
    reqs = parse(
        "Return target legendary creature card from your graveyard to "
        "the battlefield."
    )
    req = reqs[0]
    assert has_legal_target(game, 0, req) is True


def test_target_card_from_a_graveyard_owner_any_searches_both_graveyards(card_db):
    # Surgical Extraction-style: "target card from a graveyard" — any
    # graveyard counts. Test: card only in opponent's graveyard.
    game = _new_game()
    _graveyard(game, card_db, "Memnite", 1)
    reqs = parse("Exile target card from a graveyard.")
    req = reqs[0]
    assert req.owner_scope == "any"
    assert has_legal_target(game, 0, req) is True


def test_excluding_the_spell_being_cast_in_graveyard(card_db):
    # CR 601.2c — a spell can never target itself in its source zone.
    # Persist (the card) is cast from the graveyard, so it's the only
    # creature in graveyard but cannot be its own target.
    game = _new_game()
    persist_card = _graveyard(game, card_db, "Memnite", 0)  # only card
    reqs = parse(
        "Return target creature card from your graveyard to the battlefield."
    )
    req = reqs[0]
    # With the exclusion → no other creatures → illegal
    assert has_legal_target(game, 0, req, exclude=persist_card) is False
    # Without exclusion → Memnite is legal
    assert has_legal_target(game, 0, req) is True


# ── 5. Stack-target spells (counterspells) ─────────────────────────


def test_target_spell_legal_when_stack_has_a_spell(card_db):
    game = _new_game()
    # Push a Memnite onto the stack as a SPELL (its source is the card)
    from engine.stack import StackItem, StackItemType
    memnite = _hand(game, card_db, "Memnite", 1)
    game.stack.push(StackItem(
        item_type=StackItemType.SPELL, source=memnite, controller=1,
    ))
    req = parse("Counter target spell.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_spell_illegal_when_stack_is_empty(card_db):
    game = _new_game()
    req = parse("Counter target spell.")[0]
    assert has_legal_target(game, 0, req) is False


def test_target_creature_spell_filters_to_creatures_only(card_db):
    # Disdainful Stroke, etc. — only creature spells are legal targets.
    # Stack with only a noncreature spell → illegal.
    from engine.stack import StackItem, StackItemType
    game = _new_game()
    sorcery = _hand(game, card_db, "Wrath of the Skies", 1)
    if sorcery is None or not sorcery.template.is_sorcery:
        pytest.skip("DB lacks Wrath of the Skies for this fixture")
    game.stack.push(StackItem(
        item_type=StackItemType.SPELL, source=sorcery, controller=1,
    ))
    req = parse("Counter target creature spell.")[0]
    assert has_legal_target(game, 0, req) is False


def test_target_noncreature_spell_filters_to_noncreatures(card_db):
    from engine.stack import StackItem, StackItemType
    game = _new_game()
    creature = _hand(game, card_db, "Memnite", 1)
    game.stack.push(StackItem(
        item_type=StackItemType.SPELL, source=creature, controller=1,
    ))
    req = parse("Counter target noncreature spell.")[0]
    assert has_legal_target(game, 0, req) is False


# ── 6. "any target" / "target player" / "target opponent" ──────────


def test_any_target_always_legal_even_with_empty_boards(card_db):
    # Lightning Bolt — players are always legal targets.
    game = _new_game()
    req = parse("Lightning Bolt deals 3 damage to any target.")[0]
    assert has_legal_target(game, 0, req) is True


def test_target_player_always_legal(card_db):
    game = _new_game()
    req = parse("Target player draws three cards.")[0]
    assert has_legal_target(game, 0, req) is True


# ── 7. has_legal_target_for_spell — spell-level convenience ───────


def test_has_legal_target_for_spell_passes_with_empty_requirements(card_db):
    # No "target" in oracle → passes
    game = _new_game()
    reqs = parse("Draw three cards.")
    assert has_legal_target_for_spell(game, 0, reqs) is True


def test_has_legal_target_for_spell_fails_when_required_target_missing(card_db):
    game = _new_game()
    reqs = parse("Destroy target creature.")
    assert has_legal_target_for_spell(game, 0, reqs) is False


def test_has_legal_target_for_spell_passes_when_optional_requirement_unmet(card_db):
    # "Up to one target creature gets -3/-3" — no creatures on board,
    # but the requirement is optional, so the spell can still be cast.
    game = _new_game()
    reqs = parse("Up to one target creature gets -3/-3 until end of turn.")
    assert reqs[0].is_optional is True
    assert has_legal_target_for_spell(game, 0, reqs) is True


def test_has_legal_target_for_spell_passes_with_compound_partial_match(card_db):
    # "target artifact or creature" — only a creature on board satisfies
    # the union.
    game = _new_game()
    _battlefield(game, card_db, "Memnite", 1)
    reqs = parse("Destroy target artifact or creature.")
    assert has_legal_target_for_spell(game, 0, reqs) is True


# ── 8. enumerate_legal_targets ─────────────────────────────────────


def test_enumerate_returns_all_legal_battlefield_creatures(card_db):
    game = _new_game()
    a = _battlefield(game, card_db, "Memnite", 0)
    b = _battlefield(game, card_db, "Memnite", 1)
    req = parse("Destroy target creature.")[0]
    targets = enumerate_legal_targets(game, 0, req)
    assert a in targets and b in targets
    assert len(targets) == 2


def test_enumerate_filters_by_owner_scope(card_db):
    game = _new_game()
    a = _battlefield(game, card_db, "Memnite", 0)
    _battlefield(game, card_db, "Memnite", 1)
    req = parse("Target creature you control gains flying.")[0]
    targets = enumerate_legal_targets(game, 0, req)
    assert targets == [a]


def test_enumerate_excludes_the_spell_being_cast(card_db):
    game = _new_game()
    a = _graveyard(game, card_db, "Memnite", 0)
    b = _graveyard(game, card_db, "Memnite", 0)
    req = parse(
        "Return target creature card from your graveyard to the battlefield."
    )[0]
    # Casting `a` from graveyard — `a` excluded from candidates.
    targets = enumerate_legal_targets(game, 0, req, exclude=a)
    assert b in targets
    assert a not in targets


def test_enumerate_returns_empty_for_any_zone(card_db):
    # "any target" / "target player" cannot enumerate card instances.
    game = _new_game()
    req = parse("Lightning Bolt deals 3 damage to any target.")[0]
    assert enumerate_legal_targets(game, 0, req) == []


def test_enumerate_returns_empty_when_requirement_unmet(card_db):
    game = _new_game()
    req = parse("Destroy target creature.")[0]
    assert enumerate_legal_targets(game, 0, req) == []


# ── 9. Owner-scope edge cases ──────────────────────────────────────


def test_target_creature_an_opponent_controls_filters_correctly(card_db):
    game = _new_game()
    _battlefield(game, card_db, "Memnite", 0)
    b = _battlefield(game, card_db, "Memnite", 1)
    req = parse("Target creature an opponent controls gets -2/-2.")[0]
    assert req.owner_scope == "opponent"
    targets = enumerate_legal_targets(game, 0, req)
    assert targets == [b]
