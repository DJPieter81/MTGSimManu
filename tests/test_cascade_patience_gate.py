"""LE-A3 — Cascade patience gate regression tests.

Diagnostic: docs/diagnostics/2026-04-24_living_end_consolidated_findings.md §LE-A3.

Context: cascade spells receive an unconditional `+1.5` free-cast bonus at
`ai/ev_player.py:440-442` whenever the engine offers them for 0 effective
mana. Ruby Storm has a conditional patience gate at
`ai/ev_player.py:_combo_modifier` (PR #142) that clamps mid-chain rituals
below `pass_threshold` when the chain cannot reach a finisher. Cascade has
no equivalent — Living End therefore cascades into a thin graveyard and
returns a summoning-sick board that cannot block opp's decisive turn.

Fix: add a cascade patience gate that fires ONLY for reanimator-style
cascade spells (cascade tag AND a reanimate spell exists in the library)
when the graveyard has too few creatures. Threshold is the deck's
FILL_RESOURCE goal `resource_target` (gameplan-driven, not hardcoded).
When the gate fires, the spell's score is clamped below `pass_threshold`
(same treatment as Storm's ritual clamp).

The gate must NOT fire for:
- Non-reanimator cascade spells (e.g., Cascade Zenith into a burn deck) —
  cascading is the whole point, there is no graveyard-fueled payoff.
- Cascade spells cast when the graveyard already has enough creatures.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _add_to_graveyard(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="graveyard",
    )
    card._game_state = game
    game.players[controller].graveyard.append(card)
    return card


def _add_to_library(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="library",
    )
    card._game_state = game
    game.players[controller].library.append(card)
    return card


def _score_spell(game, deck_name, card):
    """Helper: build EVPlayer for the given deck and score the candidate."""
    game.players[0].deck_name = deck_name
    player = EVPlayer(player_idx=0, deck_name=deck_name,
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    opp = game.players[1]
    return player, player._score_spell(card, snap, game, me, opp)


def _build_living_end_game(card_db, gy_creature_count: int):
    """Construct a Living End mid-game state: 3 Mountains (cast cascade),
    Living End in library (reanimate target), N creatures in graveyard."""
    game = GameState(rng=random.Random(0))
    # Enough mana to actually cast a CMC-3 cascade spell.
    for _ in range(3):
        _add_to_battlefield(game, card_db, "Mountain", controller=0)
    # Cascade spell in hand.
    shardless = _add_to_hand(game, card_db, "Shardless Agent", controller=0)
    # Engine marks this flag when the spell is offered for 0 effective mana
    # (cascade / Ragavan exile / suspend / Wish). Simulating the state the
    # +1.5 free-cast bonus at ai/ev_player.py:440-442 would normally fire in.
    shardless._free_cast_opportunity = True
    # The reanimation payoff sits in the library — it is what cascade hits.
    _add_to_library(game, card_db, "Living End", controller=0)
    # Seed the graveyard with cycled creatures (Living End's gameplan fuel).
    for _ in range(gy_creature_count):
        _add_to_graveyard(game, card_db, "Street Wraith", controller=0)
    game.players[1].deck_name = "Dimir Midrange"
    return game, shardless


class TestCascadePatienceGate:
    """LE-A3 — Living End should not cascade with insufficient GY fuel."""

    def test_cascade_clamped_when_graveyard_too_thin(self, card_db):
        """Empty-ish graveyard (0-1 creatures) → scoring Shardless Agent
        must yield EV below pass_threshold so the AI holds it. Resolving
        Living End with a near-empty GY returns nothing — the cascade is
        a wasted card."""
        game, shardless = _build_living_end_game(card_db, gy_creature_count=1)

        player, ev = _score_spell(game, "Living End", shardless)

        pass_threshold = player.profile.pass_threshold
        assert ev < pass_threshold, (
            f"Shardless Agent EV with 1 GY creature = {ev:.2f}, should be "
            f"below pass_threshold={pass_threshold} so the AI holds cascade "
            f"until the graveyard is full. Cascading into an empty GY "
            f"returns no board and burns the enabler."
        )

    def test_cascade_allowed_when_graveyard_has_critical_mass(self, card_db):
        """Graveyard with 4 creatures (meets `resource_target`) → scoring
        Shardless Agent must yield EV ABOVE pass_threshold so the AI fires
        the cascade. Gate must not brick the deck in the payoff phase."""
        game, shardless = _build_living_end_game(card_db, gy_creature_count=4)

        player, ev = _score_spell(game, "Living End", shardless)

        pass_threshold = player.profile.pass_threshold
        assert ev > pass_threshold, (
            f"Shardless Agent EV with 4 GY creatures = {ev:.2f}, at or "
            f"below pass_threshold={pass_threshold}. With the FILL_RESOURCE "
            f"target met, the cascade should fire — not be clamped."
        )


class TestCascadePatienceGateScope:
    """Regression: the gate must be reanimator-specific. A cascade spell
    in a non-reanimator deck (hypothetical Cascade Zenith in a burn list)
    must not be penalised — there is no graveyard payoff to wait on."""

    def test_non_reanimator_cascade_not_clamped(self, card_db):
        """Build a non-Living-End deck casting a cascade spell. No
        reanimate payoff in library/hand. Even with empty graveyard,
        the cascade must NOT be clamped — the gate is reanimator-only."""
        game = GameState(rng=random.Random(0))
        for _ in range(3):
            _add_to_battlefield(game, card_db, "Mountain", controller=0)
        shardless = _add_to_hand(game, card_db, "Shardless Agent",
                                  controller=0)
        shardless._free_cast_opportunity = True
        # Deliberately NO reanimate spell in library — non-reanimator deck.
        # Fill library with vanilla burn to prove the point.
        for _ in range(5):
            _add_to_library(game, card_db, "Lightning Bolt", controller=0)
        game.players[1].deck_name = "Dimir Midrange"

        # Score as Boros Energy — non-reanimator deck with no
        # prefer_cycling gameplan and no library reanimate.
        player, ev = _score_spell(game, "Boros Energy", shardless)

        pass_threshold = player.profile.pass_threshold
        assert ev > pass_threshold, (
            f"Shardless Agent EV in non-reanimator deck = {ev:.2f}, at "
            f"or below pass_threshold={pass_threshold}. The cascade "
            f"patience gate must not fire for decks without a reanimate "
            f"payoff — there is no graveyard fuel to wait for."
        )
