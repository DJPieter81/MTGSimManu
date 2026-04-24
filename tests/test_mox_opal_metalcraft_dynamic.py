"""E1 — Mox Opal metalcraft must be evaluated at tap time, not ETB time.

Per CR 702.98, metalcraft is checked at the moment an ability is
activated.  The prior engine fix for Mox Opal (`mox_opal_etb` in
`engine/card_effects.py`) permanently mutated the template's
`produces_mana` to WUBRG when metalcraft was satisfied on ETB.  Two
failure modes resulted:

  1. Mox ETB'd with ≥3 artifacts → later artifacts drop below 3 →
     Mox still produces 5 colours forever (rules violation).
  2. Mox ETB'd with <3 artifacts → later artifact count reaches 3 →
     template stays `produces_mana=[]`, Mox cannot tap (inverse bug).

The fix replaces the ETB mutation with a dynamic check performed at
the mana-tap call sites via `_effective_produces_mana(player_idx,
card)`.  This test exercises all three transitions on a live game
state, plus a regression case for a normal dual land.

Design: docs/diagnostics/2026-04-23_affinity_consolidated_findings.md E1.
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


class TestMoxOpalMetalcraftDynamic:
    """Mox Opal's mana production must reflect the current artifact
    count at every tap attempt, not a snapshot from ETB time."""

    def test_metalcraft_active_produces_all_colors(self, card_db):
        """Mox + 2 other artifacts on battlefield (3 total) → WUBRG."""
        game = GameState(rng=random.Random(0))
        mox = _add_to_battlefield(game, card_db, "Mox Opal", controller=0)
        _add_to_battlefield(game, card_db, "Ornithopter", controller=0)
        _add_to_battlefield(game, card_db, "Memnite", controller=0)

        colors = game._effective_produces_mana(0, mox)
        assert set(colors) == {"W", "U", "B", "R", "G"}, (
            f"Mox Opal with metalcraft (3 artifacts) should produce all "
            f"five colours; got {colors!r}"
        )

    def test_metalcraft_lost_produces_nothing(self, card_db):
        """Drop below 3 artifacts after ETB → Mox produces nothing."""
        game = GameState(rng=random.Random(0))
        mox = _add_to_battlefield(game, card_db, "Mox Opal", controller=0)
        orn = _add_to_battlefield(game, card_db, "Ornithopter", controller=0)
        mem = _add_to_battlefield(game, card_db, "Memnite", controller=0)

        # Sanity: metalcraft currently active.
        assert set(game._effective_produces_mana(0, mox)) == {
            "W", "U", "B", "R", "G"
        }

        # Remove the two other artifacts (e.g., sacrificed / destroyed).
        game.players[0].battlefield.remove(orn)
        game.players[0].battlefield.remove(mem)

        colors = game._effective_produces_mana(0, mox)
        assert colors == [], (
            f"Mox Opal without metalcraft (only 1 artifact) must not "
            f"produce any mana; got {colors!r}.  This is the core E1 "
            f"bug: ETB-time template mutation left Mox producing WUBRG "
            f"forever after artifact count dropped below 3."
        )

    def test_metalcraft_regained_produces_all_colors(self, card_db):
        """Artifact count drops below 3, then returns to ≥3 → Mox is
        active again.  The inverse-bug path: a Mox that ETB'd without
        metalcraft must still work once metalcraft is established."""
        game = GameState(rng=random.Random(0))
        # ETB with only 1 artifact → historically left template empty.
        mox = _add_to_battlefield(game, card_db, "Mox Opal", controller=0)

        # Baseline: no metalcraft yet.
        assert game._effective_produces_mana(0, mox) == []

        # Add 2 more artifacts → metalcraft now on.
        _add_to_battlefield(game, card_db, "Ornithopter", controller=0)
        _add_to_battlefield(game, card_db, "Memnite", controller=0)

        colors = game._effective_produces_mana(0, mox)
        assert set(colors) == {"W", "U", "B", "R", "G"}, (
            f"Mox Opal must produce all five colours once metalcraft "
            f"is re-established (inverse of the ETB-snapshot bug); "
            f"got {colors!r}"
        )

    def test_regular_land_unaffected(self, card_db):
        """Regression: a Steam Vents (normal shock dual) must still
        return its own produces_mana list via the helper."""
        game = GameState(rng=random.Random(0))
        vents = _add_to_battlefield(
            game, card_db, "Steam Vents", controller=0
        )
        colors = game._effective_produces_mana(0, vents)
        assert set(colors) == {"U", "R"}, (
            f"Steam Vents should produce U/R via the effective helper; "
            f"got {colors!r}.  The helper must not break regular lands."
        )
