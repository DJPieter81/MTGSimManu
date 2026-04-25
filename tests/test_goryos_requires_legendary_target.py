"""GV2-2: Goryo's Vengeance strictly requires a legendary target.

Oracle text: "Return target legendary creature card from your graveyard
to the battlefield. It gains haste. Exile it at the beginning of the
next end step."

CR 608.2b: if a spell has no legal targets when it tries to resolve,
it does nothing (fizzles).

The previous handler in `engine/card_effects.py` contained a fallback
that reanimated ANY creature from the graveyard when no legendary
creature was present. This violates the oracle: Goryo's cannot target
a non-legendary creature, and with no legal target the spell must
fizzle.

This test suite locks the correct behaviour so the fallback cannot
regress.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.card_effects import EFFECT_REGISTRY, EffectTiming
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _put_in_graveyard(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
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


def _resolve_goryos(game, card_db, controller):
    """Fire the registered Goryo's Vengeance SPELL_RESOLVE handler."""
    tmpl = card_db.get_card("Goryo's Vengeance")
    assert tmpl is not None, "Goryo's Vengeance missing from card DB"
    spell = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="stack",
    )
    spell._game_state = game
    fired = EFFECT_REGISTRY.execute(
        "Goryo's Vengeance", EffectTiming.SPELL_RESOLVE,
        game, spell, controller, targets=None,
    )
    assert fired, "Goryo's Vengeance SPELL_RESOLVE handler did not fire"


class TestGoryosLegendaryFilter:

    def test_picks_legendary_when_both_present(self, card_db):
        """GY has Griselbrand (legendary) + Solitude (non-legendary).
        Goryo's must reanimate Griselbrand, not Solitude."""
        game = GameState(rng=random.Random(0))
        gris = _put_in_graveyard(game, card_db, "Griselbrand", 0)
        sol = _put_in_graveyard(game, card_db, "Solitude", 0)

        _resolve_goryos(game, card_db, 0)

        bf_names = [c.name for c in game.players[0].battlefield]
        assert "Griselbrand" in bf_names, (
            f"Goryo's failed to reanimate Griselbrand. BF: {bf_names}"
        )
        assert "Solitude" not in bf_names, (
            f"Goryo's incorrectly picked non-legendary Solitude. BF: {bf_names}"
        )
        # Solitude must remain in graveyard.
        gy_names = [c.name for c in game.players[0].graveyard]
        assert "Solitude" in gy_names, (
            "Non-legendary Solitude should stay in graveyard."
        )

    def test_fizzles_with_only_nonlegendary_creatures(self, card_db):
        """GY has Solitude + Memnite (both non-legendary).
        Goryo's has no legal target and must fizzle — no reanimation."""
        game = GameState(rng=random.Random(0))
        sol = _put_in_graveyard(game, card_db, "Solitude", 0)
        mem = _put_in_graveyard(game, card_db, "Memnite", 0)

        bf_before = list(game.players[0].battlefield)
        gy_before_ids = sorted(c.instance_id for c in game.players[0].graveyard)

        _resolve_goryos(game, card_db, 0)

        # Battlefield unchanged — no creature reanimated.
        assert game.players[0].battlefield == bf_before, (
            f"Goryo's reanimated a non-legendary creature via buggy fallback. "
            f"BF: {[c.name for c in game.players[0].battlefield]}"
        )
        # Graveyard preserved.
        gy_after_ids = sorted(c.instance_id for c in game.players[0].graveyard)
        assert gy_after_ids == gy_before_ids, (
            "Non-legendary creatures must stay in graveyard when Goryo's fizzles."
        )

    def test_empty_graveyard_no_op(self, card_db):
        """Empty GY — Goryo's must resolve without effect or crash."""
        game = GameState(rng=random.Random(0))

        _resolve_goryos(game, card_db, 0)

        assert game.players[0].battlefield == []
        assert game.players[0].graveyard == []

    def test_two_legendaries_picks_best(self, card_db):
        """Regression: with two legendary creatures, handler must still
        pick the best one (by P+T), deterministically."""
        game = GameState(rng=random.Random(0))
        gris = _put_in_graveyard(game, card_db, "Griselbrand", 0)
        atraxa = _put_in_graveyard(game, card_db, "Atraxa, Grand Unifier", 0)

        _resolve_goryos(game, card_db, 0)

        bf_names = [c.name for c in game.players[0].battlefield]
        # Both are 7/7 so tiebreak via max() is on the first-seen max;
        # exactly one legendary must be reanimated, and it must be one
        # of the two legendary candidates (never anything else).
        assert len(bf_names) == 1, (
            f"Expected exactly one reanimated creature, got {bf_names}"
        )
        assert bf_names[0] in {"Griselbrand", "Atraxa, Grand Unifier"}, (
            f"Goryo's reanimated an unexpected card: {bf_names[0]}"
        )
