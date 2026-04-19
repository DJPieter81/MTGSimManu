"""Bug D — permanent_threat(Ornithopter) is invisible against Affinity.

Evidence: `replays/boros_vs_affinity_bo3_s62000.txt` T4 Boros — Galvanic
Discharge logged `[Target] → face: no killable target` when the opponent
had Ornithopter (0/2) and Signal Pest (0/1) on the battlefield alongside
Cranial Plating and Mox Opal.  Discharge at 3 damage could have killed
both bodies and materially crippled opp's next-turn Plating equip swing.

Root cause (see `docs/design/ev_correctness_overhaul.md` §4): the
marginal-contribution threat formula

    threat(P) = V_owner(battlefield) − V_owner(battlefield \\ {P})

is mathematically correct, BUT its underlying evaluator
`ai.clock.position_value` tracks only life / power / toughness / creature
count / hand / mana / lands / turn / storm_count / energy — it is BLIND
to artifact count.  Ornithopter's value to an Affinity deck is almost
entirely the +1 it contributes to artifact count, which fuels Mox Opal
metalcraft, Plating scaling, and Thought Monitor's affinity discount.
Removing it from the board reduces Affinity's mid-game ceiling — but
`position_value` observes no change, so `permanent_threat` returns 0.0,
and targeted burn goes face.

Fix direction (from the doc): `EVSnapshot` gains artifact-count fields;
`snapshot_from_game` populates them; `position_value` credits artifact
count conditionally when the owner's visible cards reference artifact
scaling ("for each artifact", "metalcraft", "affinity for artifacts").

Regression anchor: in a no-synergy context (e.g. opponent is a
Zoo-style deck with Ornithopter as an isolated body and no
artifact-scaling cards visible), permanent_threat(Ornithopter) must
remain ≈ 0 — we don't want a blanket artifact-count bonus applied to
decks that don't care.
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


class TestPermanentThreatOrnithopterVsAffinity:
    """Ornithopter's threat value must reflect its role as an
    artifact-count amplifier when the owner's visible cards scale with
    artifact count."""

    def test_ornithopter_threat_nonzero_when_opp_has_artifact_scaling(
            self, card_db):
        """Opp's board: Ornithopter + Cranial Plating (on battlefield,
        unattached) + Mox Opal + Darksteel Citadel.  Every one of these
        artifact-scaling cards reads off opp's artifact count.  Removing
        Ornithopter drops opp's artifact count by 1 → Plating scaling
        drops 1, Mox Opal's metalcraft gate moves 1 artifact further
        away, Thought-Monitor-class draws lose 1 affinity discount.

        The marginal-contribution formula should therefore return > 0.
        Current `position_value` is blind to artifact count, so it
        returns 0.0 — the bug.
        """
        game = GameState(rng=random.Random(0))

        orn = _add_to_battlefield(game, card_db, "Ornithopter", 1)
        # Artifact-scaling cards on the battlefield — visible signal
        # that artifact count matters for this player.
        _add_to_battlefield(game, card_db, "Cranial Plating", 1)
        _add_to_battlefield(game, card_db, "Mox Opal", 1)
        _add_to_battlefield(game, card_db, "Darksteel Citadel", 1)

        threat = permanent_threat(orn, game.players[1], game)

        assert threat > 0.0, (
            f"permanent_threat(Ornithopter) returned {threat:.3f} when "
            f"the opponent's board contains artifact-scaling cards "
            f"(Cranial Plating, Mox Opal, Darksteel Citadel). "
            f"Removing Ornithopter strips +1 from every artifact-count "
            f"scaler on the opponent's side — the threat value must "
            f"reflect that. `position_value` is currently blind to "
            f"artifact count; extend it conditionally on oracle-visible "
            f"scaling signals."
        )

    def test_ornithopter_threat_near_zero_without_artifact_synergy(
            self, card_db):
        """Regression: Ornithopter on an otherwise-empty board (no
        artifact scaling visible) — threat value should stay ≈ 0.
        0/2 flying body, no scaling signals → no marginal position
        value on removal.  This guards against a blanket artifact-count
        bonus being applied to every board with any artifact on it.
        """
        game = GameState(rng=random.Random(0))
        orn = _add_to_battlefield(game, card_db, "Ornithopter", 1)
        # Deliberately no artifact-scaling cards anywhere — just a
        # vanilla Zoo-style creature alongside.
        _add_to_battlefield(game, card_db, "Grizzly Bears", 1)

        threat = permanent_threat(orn, game.players[1], game)

        assert abs(threat) < 1.0, (
            f"permanent_threat(Ornithopter) returned {threat:.3f} in a "
            f"no-synergy context — expected ≈ 0 since there are no "
            f"artifact-scaling cards visible for the owner. A blanket "
            f"artifact-count bonus would fire here and over-value a "
            f"vanilla 0/2 flyer."
        )
