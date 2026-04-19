"""Bug 6 — Land-order heuristic ignores artifact synergy.

Evidence: replays/boros_vs_affinity_bo3.txt:51-58 (G1 T1 Affinity)
    T1 P2: Play Spire of Industry        ← paid 1 life on later taps
    T1 P2: Cast Ornithopter (0)

Affinity has both Spire of Industry (painful colourless / colored
land) and Darksteel Citadel (artifact land) in the opening hand.
Darksteel Citadel is the correct T1 play because:
  - It is itself an artifact → +1 for Mox Opal metalcraft,
    Cranial Plating scaling, Thought Monitor affinity discount.
  - It has no life-tap tax unlike Spire of Industry.

The land-scoring heuristic preferred Spire because it treats
"colored mana access" as more valuable than "artifact count", even
when the deck's strategy hinges on artifact count. Fix: when the
player's visible cards (hand + battlefield) include artifact-synergy
text ("for each artifact", "metalcraft", "affinity for artifacts"),
lands typed `artifact` gain a synergy bonus.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _put_in_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


class TestAffinityLandOrdering:
    """T1 Affinity — Darksteel Citadel (artifact land) must outscore
    Spire of Industry (non-artifact land) when the hand signals
    artifact synergy."""

    def _setup(self, card_db):
        """T1 Affinity hand: both lands, an artifact, and Mox Opal."""
        game = GameState(rng=random.Random(0))
        citadel = _put_in_hand(game, card_db, "Darksteel Citadel", 0)
        spire = _put_in_hand(game, card_db, "Spire of Industry", 0)
        # Artifact-synergy signal from hand: Mox Opal (metalcraft) and
        # Cranial Plating ("for each artifact"). Both oracle signals
        # that the land's artifact-type matters.
        _put_in_hand(game, card_db, "Mox Opal", 0)
        _put_in_hand(game, card_db, "Cranial Plating", 0)
        _put_in_hand(game, card_db, "Ornithopter", 0)

        player = EVPlayer(player_idx=0, deck_name="Affinity",
                          rng=random.Random(0))
        return game, player, citadel, spire

    def test_darksteel_citadel_outscores_spire_with_synergy_signal(
            self, card_db):
        game, player, citadel, spire = self._setup(card_db)
        me = game.players[0]
        spells = [c for c in me.hand if not c.template.is_land]

        citadel_ev = player._score_land(citadel, me, spells, game)
        spire_ev = player._score_land(spire, me, spells, game)

        assert citadel_ev > spire_ev, (
            f"Darksteel Citadel should outscore Spire of Industry when "
            f"opp has artifact-synergy cards in hand (Mox Opal + "
            f"Cranial Plating). Got citadel={citadel_ev:.2f}, "
            f"spire={spire_ev:.2f}. Darksteel is itself an artifact — "
            f"it directly enables metalcraft, Plating scaling, and "
            f"affinity cost reduction."
        )

    def test_non_artifact_deck_does_not_prefer_artifact_lands(
            self, card_db):
        """Regression anchor — without an artifact-synergy signal,
        the heuristic must not prefer artifact lands (Darksteel Citadel
        in a Zoo-style hand offers no extra value)."""
        game = GameState(rng=random.Random(0))
        citadel = _put_in_hand(game, card_db, "Darksteel Citadel", 0)
        spire = _put_in_hand(game, card_db, "Sacred Foundry", 0)
        # No artifact synergy in hand — a generic creature.
        _put_in_hand(game, card_db, "Ragavan, Nimble Pilferer", 0)

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        me = game.players[0]
        spells = [c for c in me.hand if not c.template.is_land]

        citadel_ev = player._score_land(citadel, me, spells, game)
        sacred_ev = player._score_land(spire, me, spells, game)
        # Sacred Foundry gives W+R colors; Darksteel Citadel only
        # gives colourless. Sacred Foundry should still win here.
        assert sacred_ev > citadel_ev, (
            f"Without artifact synergy in hand, Sacred Foundry should "
            f"outscore Darksteel Citadel (it provides colors for spell "
            f"casting). Got sacred={sacred_ev:.2f}, "
            f"citadel={citadel_ev:.2f}."
        )
