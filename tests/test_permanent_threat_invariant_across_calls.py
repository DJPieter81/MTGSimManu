"""Invariant: permanent_threat is stable across repeated/interleaved calls.

P0-B regression — ai/permanent_threat.py builds a "partial" snapshot
by popping the target card from its controller's battlefield and
re-snapshotting.  EVSnapshot.{my,opp}_artifact_count is recomputed
inside snapshot_from_game() by walking the live battlefield, so the
post-pop count is correct for that field.  However, P/T-scaling
mechanics (Cranial Plating's '+1/+0 for each artifact you control',
Construct tokens, Nettlecyst) read the LIVE battlefield via
CardInstance._get_artifact_count() during P/T resolution.  Because
those reads happen against game state, not the snapshot, a stray
mutation or ordering issue in the snapshot path can leave a card's
P/T computed against an artifact count that already excludes the
removed card — drifting the marginal threat between calls.

Rule encoded by this test (independent of any single card):
"permanent_threat(card) on a fixed board is invariant across
repeated calls, regardless of which other cards are queried in
between."  And, on an Affinity-style board with Cranial Plating
equipped, a body-bearing creature (Memnite) must score strictly
higher than a non-tapping mana rock (Springleaf Drum), because
removing the body strips an attacker AND the Plating scaling, while
removing the rock only drops the artifact count by one.
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


def _mk(game, card_db, name, ctrl):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=ctrl,
        controller=ctrl,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[ctrl].battlefield.append(card)
    return card


def _attach(equipment, creature):
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")


class TestPermanentThreatInvariantAcrossCalls:
    """permanent_threat must be stable under repeated/interleaved
    calls on a frozen board, and the relative ordering between a
    body-bearing artifact creature and a non-tapping mana rock must
    favour the body even on an artifact-scaling board."""

    def test_threat_is_invariant_across_repeated_calls(self, card_db):
        """Calling permanent_threat on the same card multiple times,
        with calls on OTHER cards interleaved, must return the same
        value every time.  If any internal mutation leaks (e.g. the
        partial snapshot's artifact_count drifts because the removed
        artifact is still being counted by a dependent P/T read),
        the second invocation will diverge from the first."""
        game = GameState(rng=random.Random(0))
        # Affinity-style board: Plating equipped to Ornithopter,
        # plus a body-bearing artifact creature and a mana rock.
        orn = _mk(game, card_db, "Ornithopter", 1)
        plating = _mk(game, card_db, "Cranial Plating", 1)
        memnite = _mk(game, card_db, "Memnite", 1)
        drum = _mk(game, card_db, "Springleaf Drum", 1)
        _attach(plating, orn)

        opp = game.players[1]

        # First measurement — baseline.
        memnite_threat_1 = permanent_threat(memnite, opp, game)
        drum_threat_1 = permanent_threat(drum, opp, game)
        # Interleave: query Memnite again, then Drum, then Memnite.
        memnite_threat_2 = permanent_threat(memnite, opp, game)
        drum_threat_2 = permanent_threat(drum, opp, game)
        memnite_threat_3 = permanent_threat(memnite, opp, game)

        assert memnite_threat_1 == pytest.approx(memnite_threat_2, abs=1e-9), (
            f"Memnite threat drifted across calls: "
            f"{memnite_threat_1} vs {memnite_threat_2}.  "
            f"permanent_threat must be a pure function of board state."
        )
        assert memnite_threat_2 == pytest.approx(memnite_threat_3, abs=1e-9), (
            f"Memnite threat drifted across calls: "
            f"{memnite_threat_2} vs {memnite_threat_3}."
        )
        assert drum_threat_1 == pytest.approx(drum_threat_2, abs=1e-9), (
            f"Springleaf Drum threat drifted across calls: "
            f"{drum_threat_1} vs {drum_threat_2}."
        )

    def test_body_outranks_mana_rock_on_plating_board(self, card_db):
        """On a Plating-equipped Affinity board, a 1/1 artifact
        creature (Memnite) must score higher than a 0/1 non-tapping
        mana rock (Springleaf Drum).  Both contribute one to the
        artifact count, so removing either drops Plating's pump by
        the same 1; but Memnite ALSO removes a 1-power attacker,
        while Drum's 'tap a creature' mana ability is dead weight on
        a board this small.  A removal targeter that picks Drum over
        Memnite is making the wrong call."""
        game = GameState(rng=random.Random(0))
        orn = _mk(game, card_db, "Ornithopter", 1)
        plating = _mk(game, card_db, "Cranial Plating", 1)
        memnite = _mk(game, card_db, "Memnite", 1)
        drum = _mk(game, card_db, "Springleaf Drum", 1)
        _attach(plating, orn)

        opp = game.players[1]
        memnite_threat = permanent_threat(memnite, opp, game)
        drum_threat = permanent_threat(drum, opp, game)

        assert memnite_threat > drum_threat, (
            f"Memnite ({memnite_threat:.3f}) must score higher than "
            f"Springleaf Drum ({drum_threat:.3f}) on a Plating board "
            f"— both are equal artifact contributors but Memnite "
            f"adds a 1/1 body to the offence while Drum is a "
            f"tap-outlet mana rock.  If Drum scores higher, the "
            f"partial snapshot is double-counting the removed "
            f"artifact (its P/T-scaling consumers read the live "
            f"battlefield post-pop)."
        )
