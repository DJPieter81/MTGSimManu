"""GV2-3: Goryo's Vengeance must respect Grafdigger's Cage.

Oracle text (Cage, clause 1):
    "Creature cards in graveyards and libraries can't enter the
     battlefield."

Goryo's Vengeance reanimates a legendary creature from the graveyard.
This is the same "creature card entering from GY" mechanic that Living
End uses, and the LE-E1 fix (PR #155) already gates Living End via
``_gy_reanimation_hate_source``. The Goryo's resolver at
``engine/card_effects.py::goryos_vengeance_resolve`` must apply the
identical gate so a symmetric reprint of Cage would also stop Goryo's.

Fix: iter7 GV2-3 — add the hate-source check at the top of
``goryos_vengeance_resolve``; if present, fizzle with a log cite and
leave the target in the graveyard.
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


def _put_on_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _resolve_goryos(game, controller):
    """Invoke Goryo's Vengeance resolver directly — we're unit-testing
    the resolver gate, not the cast path."""
    ok = EFFECT_REGISTRY.execute(
        "Goryo's Vengeance",
        EffectTiming.SPELL_RESOLVE,
        game,
        None,
        controller,
    )
    assert ok, "Goryo's Vengeance handler not registered"


class TestGoryosGatedByGrafdiggersCage:
    """Goryo's Vengeance on a board containing Cage must not reanimate."""

    def test_cage_on_opponent_blocks_goryos_reanimation(self, card_db):
        """With P2's Cage in play, P1 resolving Goryo's on Griselbrand
        in their GY must leave Griselbrand in the graveyard."""
        game = GameState(rng=random.Random(0))

        # P2 controls Grafdigger's Cage
        _put_on_battlefield(game, card_db, "Grafdigger's Cage", 1)

        # P1 has Griselbrand in graveyard
        griselbrand = _put_in_graveyard(game, card_db, "Griselbrand", 0)

        _resolve_goryos(game, controller=0)

        # Griselbrand must still be in P1's graveyard
        assert griselbrand in game.players[0].graveyard, (
            f"Griselbrand entered the battlefield despite Cage. "
            f"GY: {[c.name for c in game.players[0].graveyard]}, "
            f"BF: {[c.name for c in game.players[0].battlefield]}"
        )
        assert griselbrand not in game.players[0].battlefield

        # Log must cite the fizzle reason (Cage or hate)
        rel = [l for l in game.log
               if "Cage" in l or "hate" in l.lower()
               or "fizzle" in l.lower()]
        assert rel, (
            f"No Cage/hate/fizzle rule cite in log. Log tail: "
            f"{game.log[-10:]}"
        )

    def test_no_cage_allows_goryos_reanimation(self, card_db):
        """Regression: without Cage, Goryo's returns Griselbrand to the
        battlefield normally."""
        game = GameState(rng=random.Random(0))

        griselbrand = _put_in_graveyard(game, card_db, "Griselbrand", 0)

        _resolve_goryos(game, controller=0)

        assert griselbrand in game.players[0].battlefield, (
            f"Griselbrand did NOT return to battlefield without Cage. "
            f"GY: {[c.name for c in game.players[0].graveyard]}, "
            f"BF: {[c.name for c in game.players[0].battlefield]}"
        )
        assert griselbrand not in game.players[0].graveyard

    def test_cage_on_own_battlefield_also_blocks_goryos(self, card_db):
        """Cage is symmetric — if the Goryo's controller owns Cage, the
        spell still fizzles."""
        game = GameState(rng=random.Random(0))

        # P1 (Goryo's controller) owns the Cage
        _put_on_battlefield(game, card_db, "Grafdigger's Cage", 0)

        griselbrand = _put_in_graveyard(game, card_db, "Griselbrand", 0)

        _resolve_goryos(game, controller=0)

        assert griselbrand in game.players[0].graveyard, (
            f"Griselbrand entered despite controller's own Cage."
        )
        assert griselbrand not in game.players[0].battlefield
