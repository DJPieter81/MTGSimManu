"""Bug 6 — Land-order heuristic must reflect active artifact synergy.

Evidence: replays/boros_vs_affinity_bo3.txt:51-58 (G1 T1 Affinity)
    T1 P2: Play Spire of Industry        ← paid 1 life on later taps
    T1 P2: Cast Ornithopter (0)

Affinity has both Spire of Industry (painful colourless / colored
land) and Darksteel Citadel (artifact land) in the opening hand.
Darksteel Citadel is the correct play when active scaling effects
(Mox Opal metalcraft, equipped Cranial Plating, Construct token) are
already on the battlefield because the marginal artifact contributes
power / mana / cost-reduction to those deployed effects.

Phase 1B / Phase L E-2 (2026-05-09): the synergy bonus reads
**battlefield** scaling cards only, not hand-side intent. Hand cards
are scored separately when the AI considers casting them; counting
them here double-books the same EV. The previous version of this
test put scaling cards in HAND (anticipatory synergy); it was
encoding the bug. This test now reflects the corrected rule:
deployed-only synergy.

Sister tests:
  - tests/test_artifact_land_synergy_excludes_hand.py (Phase 1B)
  - tests/test_evsnapshot_artifact_count_excludes_lands.py (PR-L1)
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


def _put_in_play(game, card_db, name, controller):
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


class TestAffinityLandOrdering:
    """Mid-game Affinity — Darksteel Citadel (artifact land) must
    outscore Spire of Industry (non-artifact land) when the
    BATTLEFIELD has active artifact-scaling effects."""

    def _setup(self, card_db):
        """Battlefield has deployed scaling: Mox Opal + Cranial
        Plating + Memnite (carrier). Hand has just the two land
        candidates and a noise card.
        """
        game = GameState(rng=random.Random(0))
        citadel = _put_in_hand(game, card_db, "Darksteel Citadel", 0)
        spire = _put_in_hand(game, card_db, "Spire of Industry", 0)
        # Hand-side noise (must NOT contribute to synergy_signals
        # post-Phase-1B).
        _put_in_hand(game, card_db, "Ornithopter", 0)
        # Active battlefield-side scaling: Mox Opal (metalcraft
        # oracle) and Cranial Plating ("for each artifact" oracle).
        _put_in_play(game, card_db, "Mox Opal", 0)
        _put_in_play(game, card_db, "Cranial Plating", 0)
        _put_in_play(game, card_db, "Memnite", 0)

        player = EVPlayer(player_idx=0, deck_name="Affinity",
                          rng=random.Random(0))
        return game, player, citadel, spire

    def test_darksteel_citadel_outscores_spire_with_active_synergy(
            self, card_db):
        game, player, citadel, spire = self._setup(card_db)
        me = game.players[0]
        spells = [c for c in me.hand if not c.template.is_land]

        citadel_ev = player._score_land(citadel, me, spells, game)
        spire_ev = player._score_land(spire, me, spells, game)

        assert citadel_ev > spire_ev, (
            f"Darksteel Citadel must outscore Spire of Industry when "
            f"the BATTLEFIELD has active artifact-scaling effects "
            f"(Mox Opal + Plating + Memnite carrier). The marginal "
            f"artifact land contributes +1 power to the equipped "
            f"carrier and bumps Mox Opal's metalcraft count. Got "
            f"citadel={citadel_ev:.2f}, spire={spire_ev:.2f}."
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
