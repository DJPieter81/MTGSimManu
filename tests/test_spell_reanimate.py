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

from engine.cards import CardInstance
from engine.game_state import GameState


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


def _put_in_library(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="library",
    )
    card._game_state = game
    game.players[controller].library.append(card)
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


class TestUnearthManaValueCap:
    """Unearth: 'target creature card with mana value 3 or less from
    your graveyard' — the mana-value cap must be honored end to end
    (parse -> legal-target enumeration -> resolution), not just
    recognized by the parser.

    Live bug this pins (replay audit, docs artifact "The Reanimator's
    Blind Spot" — Instant Reanimator vs Grixis Reanimator, seed
    57010): Unearth reanimated Archon of Cruelty (mana value 8) with
    only Memnite (mana value 0) also in the graveyard, because
    nothing filtered the candidate pool by mana value at all — the
    resolver picked "biggest body" from the WHOLE graveyard.
    """

    def test_does_not_reanimate_a_creature_above_the_cap(self, card_db):
        game = GameState(rng=random.Random(0))
        big = _put_in_graveyard(game, card_db, "Archon of Cruelty", 0)  # MV 8
        small = _put_in_graveyard(game, card_db, "Memnite", 0)          # MV 0

        fired = _resolve_spell(game, card_db, "Unearth", 0)

        assert fired, "Unearth oracle pattern did not fire"
        bf_names = [c.name for c in game.players[0].battlefield]
        assert "Archon of Cruelty" not in bf_names, (
            f"Unearth (mana value 3 or less) reanimated an 8-mana-value "
            f"creature. BF: {bf_names}"
        )
        assert "Memnite" in bf_names, (
            f"Unearth had a legal in-cap target (Memnite) and reanimated "
            f"nothing. BF: {bf_names}"
        )
        assert big in game.players[0].graveyard, (
            "the above-cap creature must stay in the graveyard"
        )

    def test_no_op_when_only_above_cap_creatures_are_available(self, card_db):
        game = GameState(rng=random.Random(0))
        big = _put_in_graveyard(game, card_db, "Archon of Cruelty", 0)  # MV 8

        _resolve_spell(game, card_db, "Unearth", 0)

        assert big in game.players[0].graveyard
        creatures_on_bf = [c for c in game.players[0].battlefield
                           if c.template.is_creature]
        assert creatures_on_bf == []

    def test_casting_unearth_normally_does_not_also_draw_a_card(self, card_db):
        """Unearth's own resolving text is "Return target creature card
        with mana value 3 or less from your graveyard to the
        battlefield." — it has no draw clause. The card's SEPARATE
        Cycling ability reads "({2}, Discard this card: Draw a
        card.)" as reminder text on the SAME card, purely explaining
        an alternative way to use the card from hand — casting Unearth
        normally must not also draw, just because the word "draw"
        appears elsewhere on the card."""
        game = GameState(rng=random.Random(0))
        _put_in_graveyard(game, card_db, "Memnite", 0)
        _put_in_library(game, card_db, "Mountain", 0)

        _resolve_spell(game, card_db, "Unearth", 0)

        assert len(game.players[0].hand) == 0, (
            f"casting Unearth normally drew {len(game.players[0].hand)} "
            f"card(s) — the generic draw-count detector matched 'Draw a "
            f"card' inside Unearth's own Cycling reminder text instead "
            f"of scoping to the resolving clause."
        )
