"""Bug C — Wrath of the Skies X-selection is not marginal-value driven.

Evidence: `replays/boros_rarakkyo_vs_affinity.html` G2 T4 — Wrath fires
with X set to "all available mana" rather than the X that destroys the
most net enemy threat value for the least damage to our own board.

X-cost selection today lives in `engine/game_state.py` around line 1548.
A dedicated branch exists for Chalice-of-the-Void (picks a CMC by
oracle-count marginal analysis), but every other X-cost spell falls
through to `x_value = available_for_x // multiplier` — i.e. max X.

For Wrath ("destroy each artifact, creature, and enchantment with MV
≤ X paid this way"), max X is strictly wrong whenever the board has
high-CMC permanents we want to PRESERVE — our own Snapcaster, our
own equipment, our own tokens — and opp's permanents are all below
that CMC bar.  The right X is derived from marginal value:

    X* = min X such that every opp non-indestructible
         artifact/creature/enchantment has MV ≤ X
         AND every own non-indestructible artifact/creature/enchantment
         we value has MV > X  (when such a choice exists).

This test sets up the exact scenario from the design doc (`docs/design/
ev_correctness_overhaul.md` §2, Bug C): opp has only ≤ 1 MV threats,
we have a 2 MV Snapcaster Mage.  After Wrath resolves, opp's threats
should be gone, Snapcaster should survive.  No hardcoded X value — we
assert the board state the optimal X implies.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
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
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
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


class TestWrathXOptimizesSweep:
    """X-choice for Wrath of the Skies must minimize own-board damage
    subject to clearing the opponent's permanents.  All assertions are
    expressed as post-resolve board contents — no hardcoded X."""

    def test_wrath_preserves_own_high_mv_permanent_when_possible(
            self, card_db):
        """Scenario from design doc §2 Bug C.  Boros has 5 mana and a
        Snapcaster Mage (MV 2) on its own side.  Opponent has only
        ≤ 1 MV permanents (Memnite, Ornithopter, Mox Opal, Signal Pest).

        Optimal X is 1: clears every opp permanent, spares Snapcaster.
        Current code picks max X (= 3 here, from 5 mana − 2 base cost),
        which also destroys Snapcaster.

        Post-resolve assertion: opp's non-land permanents are gone AND
        our Snapcaster Mage is still on the battlefield.
        """
        game = GameState(rng=random.Random(0))
        game.active_player = 0
        game.current_phase = Phase.MAIN1

        # Five Plains — the (mana_cost = WW) plus (room for X) budget.
        for _ in range(5):
            _add_to_battlefield(game, card_db, "Plains", 0)

        # Opp permanents: all MV ≤ 1.
        _add_to_battlefield(game, card_db, "Memnite", 1)
        _add_to_battlefield(game, card_db, "Ornithopter", 1)
        _add_to_battlefield(game, card_db, "Mox Opal", 1)
        _add_to_battlefield(game, card_db, "Signal Pest", 1)

        # Our own MV-2 creature we want to keep alive.
        snapcaster = _add_to_battlefield(game, card_db, "Snapcaster Mage", 0)

        wrath = _add_to_hand(game, card_db, "Wrath of the Skies", 0)

        assert game.cast_spell(0, wrath), (
            "Wrath cast failed — precondition. Check mana setup."
        )
        game.resolve_stack()

        # Opp's non-land permanents should all be gone (optimal X ≥ 1
        # sweeps them).
        opp_bf_nonland = [
            c for c in game.players[1].battlefield
            if not c.template.is_land
        ]
        assert opp_bf_nonland == [], (
            f"Wrath failed to clear opp's sub-MV-2 permanents: "
            f"{[c.name for c in opp_bf_nonland]}. X must be ≥ max opp MV."
        )

        # Our Snapcaster Mage should survive — optimal X < 2 keeps it.
        my_creature_names = [
            c.name for c in game.players[0].creatures
        ]
        assert "Snapcaster Mage" in my_creature_names, (
            f"Wrath destroyed our Snapcaster Mage when a smaller X "
            f"would have cleared the opponent's ≤ 1-MV board without "
            f"killing our own MV-2 creature. This is the Bug C "
            f"failure mode — max-X selection wastes our own board. "
            f"Final own creatures: {my_creature_names}."
        )
