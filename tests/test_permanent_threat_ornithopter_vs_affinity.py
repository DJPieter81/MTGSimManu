"""Bug D — permanent_threat blind to artifact-count scaling signals.

Design: docs/design/ev_correctness_overhaul.md §2.D + §4

`ai.permanent_threat.permanent_threat()` computes the marginal drop
in owner's position value when a permanent is removed.  It relies on
`ai.clock.position_value`, which currently tracks life, power, tough-
ness, creature count, mana, lands, hand — but NOT artifact count,
enchantment count, or graveyard contents.

An Affinity pilot's Ornithopter is valuable far beyond its 0/2 body:
it enables metalcraft for Mox Opal, scales Cranial Plating's +1/+0
per-artifact pump, and reduces Thought Monitor's affinity cost.
Removing it strips all of that future value.

Current implementation returns ≈0 for Ornithopter on an Affinity
board because position_value is count-blind.  The fix adds an
artifact-count term that activates conditionally when the owner's
deck/hand/board contains at least one oracle-visible artifact-
scaling card.

Regression anchor: when the owner has no artifact-scaling cards
(Zoo, Burn, generic midrange), Ornithopter's threat stays ≈0 — we
don't blanket-bonus artifact count for decks that don't use it.
"""
from __future__ import annotations

import random

import pytest

from ai.permanent_threat import permanent_threat
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


class TestPermanentThreatOrnithopterVsAffinity:
    """permanent_threat(Ornithopter) must be non-zero when the
    owner's board contains artifact-count-scaling cards, and stay
    ≈0 otherwise."""

    def test_ornithopter_has_threat_when_affinity_scaling_is_visible(
            self, card_db):
        """Opp controls an unattached Cranial Plating on top of the
        3-artifact board (Ornithopter + Mox Opal + Welding Jar).
        Plating's oracle says '+1/+0 for each artifact you control',
        so every artifact contributes future pump value.  Removing
        Ornithopter drops the artifact count from 3 to 2, shrinking
        Plating's future pump by 1 power and breaking Mox Opal's
        metalcraft mana-producing ability.  permanent_threat should
        reflect this — it cannot remain 0.0."""
        game = GameState(rng=random.Random(0))
        ornithopter = _add_to_battlefield(game, card_db, "Ornithopter",
                                           controller=1)
        _add_to_battlefield(game, card_db, "Mox Opal", controller=1)
        _add_to_battlefield(game, card_db, "Welding Jar", controller=1)
        # Unattached Plating: future pump value depends on artifact
        # count — its oracle is visible to the signal detector.
        _add_to_battlefield(game, card_db, "Cranial Plating", controller=1)
        game.players[0].deck_name = "Boros Energy"
        game.players[1].deck_name = "Affinity"

        threat = permanent_threat(ornithopter, game.players[1], game)
        assert threat > 0.0, (
            f"permanent_threat(Ornithopter)={threat:.3f} on an Affinity "
            f"board with Cranial Plating (unattached) + Mox Opal + "
            f"Welding Jar.  Removing Ornithopter drops the artifact "
            f"count from 3 → 2, shrinking Plating's per-artifact pump "
            f"and breaking Mox Opal's metalcraft activation.  The "
            f"current count-blind position_value returns 0.0; the fix "
            f"must add a conditional artifact-count term so threat > 0."
        )

    def test_ornithopter_threat_stays_zero_when_no_synergy_visible(
            self, card_db):
        """Regression: opp is Zoo with only vanilla creatures — no
        artifact-scaling cards in play, hand, or library references.
        Ornithopter is just a 0/2 body and its threat should remain
        ≈0.  The fix must NOT bonus artifact count unconditionally."""
        game = GameState(rng=random.Random(0))
        ornithopter = _add_to_battlefield(game, card_db, "Ornithopter",
                                           controller=1)
        # Non-synergy creature on opp's board.
        _add_to_battlefield(game, card_db, "Grizzly Bears", controller=1)
        game.players[0].deck_name = "Boros Energy"
        game.players[1].deck_name = "Domain Zoo"

        threat = permanent_threat(ornithopter, game.players[1], game)
        # Small slack for noise from hand/mana changes.  A 0/2 vanilla
        # body on an un-synergy board is worth essentially nothing.
        assert threat < 0.5, (
            f"Regression: permanent_threat(Ornithopter)={threat:.3f} on "
            f"a Zoo board with no artifact-scaling cards.  Ornithopter "
            f"is a 0/2 body here — its threat should be ≈0.  The fix "
            f"must scan oracle text for artifact-scaling patterns "
            f"('for each artifact', 'metalcraft', 'affinity for "
            f"artifacts') and only bonus the count when such patterns "
            f"appear in the owner's visible cards."
        )
