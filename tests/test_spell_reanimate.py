"""Spell-resolution reanimate oracle pattern.

Covers instants/sorceries that say "return target [adj] creature card
from your graveyard to the battlefield." Canonical cases:

    Persist          — nonlegendary creature, with a -1/-1 counter.
    Unburial Rites   — any creature card.

Goryo's Vengeance is a related but distinct pattern (legendary-only,
with haste + exile-at-EOT) and keeps its own handler.

Shape of the invariant:

    Spell with `return target (nonlegendary )?creature card from
    your graveyard to the battlefield` resolves → exactly one
    matching creature moves from GY to battlefield under
    controller's control. Target pool respects the nonlegendary
    filter when the oracle specifies it.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _put_in_graveyard(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="graveyard",
    )
    card._game_state = game
    game.players[controller].graveyard.append(card)
    return card


def _resolve_spell(game, card_db, name, controller):
    """Build a spell CardInstance and fire its resolution path directly
    via the oracle resolver. Mirrors what _execute_spell_effects does
    for cards without a named handler."""
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    spell = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="stack",
    )
    spell._game_state = game
    from engine.oracle_resolver import resolve_spell_from_oracle
    return resolve_spell_from_oracle(game, spell, controller)


class TestPersistReanimate:
    """Persist: return target nonlegendary creature from GY to BF."""

    def test_reanimates_best_creature(self, card_db):
        game = GameState(rng=random.Random(0))
        small = _put_in_graveyard(game, card_db, "Memnite", 0)
        big = _put_in_graveyard(game, card_db, "Griselbrand", 0)
        # Note: Griselbrand is legendary; Persist shouldn't take it.
        # Test the nonlegendary filter separately below — for this
        # test, give Persist a non-legendary body to grab.
        nonleg_big = _put_in_graveyard(game, card_db, "Emrakul's Messenger", 0)

        fired = _resolve_spell(game, card_db, "Persist", 0)

        assert fired, "Persist oracle pattern did not fire"
        # Griselbrand must stay in GY (legendary exclusion).
        assert big in game.players[0].graveyard, (
            f"Persist grabbed Griselbrand despite nonlegendary filter."
        )
        # Reanimated creature on battlefield.
        bf_names = [c.name for c in game.players[0].battlefield]
        assert "Emrakul's Messenger" in bf_names or "Memnite" in bf_names, (
            f"No reanimated creature on battlefield. BF: {bf_names}"
        )

    def test_empty_graveyard_no_op(self, card_db):
        game = GameState(rng=random.Random(0))
        _resolve_spell(game, card_db, "Persist", 0)
        # No creatures, no crash.
        assert game.players[0].battlefield == []

    def test_no_creature_in_graveyard_no_op(self, card_db):
        """Only lands/spells in GY → Persist finds no target."""
        game = GameState(rng=random.Random(0))
        _put_in_graveyard(game, card_db, "Mountain", 0)
        _put_in_graveyard(game, card_db, "Lightning Bolt", 0)

        _resolve_spell(game, card_db, "Persist", 0)

        # No creature was in GY to reanimate.
        creatures_on_bf = [c for c in game.players[0].battlefield
                           if c.template.is_creature]
        assert creatures_on_bf == []


class TestUnburialRitesReanimate:
    """Unburial Rites: no legendary restriction."""

    def test_reanimates_legendary(self, card_db):
        game = GameState(rng=random.Random(0))
        gris = _put_in_graveyard(game, card_db, "Griselbrand", 0)

        fired = _resolve_spell(game, card_db, "Unburial Rites", 0)

        assert fired, "Unburial Rites oracle pattern did not fire"
        bf_names = [c.name for c in game.players[0].battlefield]
        assert "Griselbrand" in bf_names, (
            f"Unburial Rites did not reanimate Griselbrand. BF: {bf_names}"
        )
