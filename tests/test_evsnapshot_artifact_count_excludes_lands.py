"""PR-L1 — EVSnapshot artifact-count fields must exclude artifact lands.

Rule under test
---------------
The two-quanta separation between "artifact-typed permanents" and
"artifact-typed lands" matters because position-value scoring
(`ai/clock.py` artifact_value) treats raw `my_artifact_count` /
`opp_artifact_count` as a board-strength proxy.  Lands that happen
to also be artifacts (Darksteel Citadel, Vault of Whispers, Seat
of the Synod, Inkmoth Nexus, ...) are part of the mana base and
should not inflate the artifact-count term.  Affinity-class decks
are the canonical victim — they run 17–21 artifact lands of which
4–7 are typically deployed; if those count as artifacts, the AI
reads the deck's mana base as "lots of permanents", inflating
position_value and biasing scoring upwards.

Audit context: docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md
finding E-1; PR #290 / PR-L1.

Counterpart sites that already encode the rule
----------------------------------------------
The cast-projection code in `ai/ev_evaluator.py` (lines ~1399-1405)
already gates the artifact-count increment behind ``if not t.is_land``
— so the *intent* is "non-land artifact only".  This test pins
the snapshot population code to the same intent.

The tests use Darksteel Citadel as the canonical artifact-land
fixture (subtype: Artifact + Land), Memnite as the canonical
non-land artifact creature, and Mox Opal as the canonical
non-land artifact non-creature.  These are stand-ins for any
card with the same type bitset, not the rule itself.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


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


def test_artifact_count_excludes_artifact_lands(card_db):
    """An artifact-typed *land* must not contribute to my_artifact_count.

    Fixture: 2 Memnites (artifact creature, non-land) and 3 Darksteel
    Citadels (artifact land) on my battlefield.  The expected count is
    2 — only the non-land artifacts.  If land filtering is missing,
    the count is 5 and the test fails.
    """
    game = GameState(rng=random.Random(0))
    for _ in range(2):
        _put_in_play(game, card_db, "Memnite", 0)
    for _ in range(3):
        _put_in_play(game, card_db, "Darksteel Citadel", 0)

    snap = snapshot_from_game(game, player_idx=0)

    assert snap.my_artifact_count == 2, (
        "my_artifact_count must exclude artifact lands; "
        f"got {snap.my_artifact_count} (3 Darksteel Citadels leaked in)"
    )


def test_opp_artifact_count_excludes_artifact_lands(card_db):
    """Same rule, opponent side: artifact lands on opp's battlefield
    must not contribute to opp_artifact_count."""
    game = GameState(rng=random.Random(0))
    for _ in range(4):
        _put_in_play(game, card_db, "Darksteel Citadel", 1)
    _put_in_play(game, card_db, "Memnite", 1)

    snap = snapshot_from_game(game, player_idx=0)

    assert snap.opp_artifact_count == 1, (
        "opp_artifact_count must exclude artifact lands; "
        f"got {snap.opp_artifact_count} (4 Darksteel Citadels leaked in)"
    )


def test_non_land_artifacts_still_counted(card_db):
    """Regression: a Mox Opal (artifact, non-creature, non-land)
    and an Ornithopter (artifact creature, non-land) ARE counted.
    The fix must not over-correct."""
    game = GameState(rng=random.Random(0))
    _put_in_play(game, card_db, "Mox Opal", 0)
    _put_in_play(game, card_db, "Ornithopter", 0)
    _put_in_play(game, card_db, "Memnite", 0)

    snap = snapshot_from_game(game, player_idx=0)

    assert snap.my_artifact_count == 3, (
        "non-land artifacts must still be counted; "
        f"got {snap.my_artifact_count} (expected 3)"
    )


def test_mixed_battlefield_separates_lands_from_artifacts(card_db):
    """Combined: 2 non-land artifacts + 3 artifact lands on me, plus
    1 non-land artifact + 2 artifact lands on opp.  Expected:
    my_artifact_count == 2, opp_artifact_count == 1."""
    game = GameState(rng=random.Random(0))
    # me
    _put_in_play(game, card_db, "Memnite", 0)
    _put_in_play(game, card_db, "Mox Opal", 0)
    for _ in range(3):
        _put_in_play(game, card_db, "Darksteel Citadel", 0)
    # opp
    _put_in_play(game, card_db, "Ornithopter", 1)
    for _ in range(2):
        _put_in_play(game, card_db, "Darksteel Citadel", 1)

    snap = snapshot_from_game(game, player_idx=0)

    assert snap.my_artifact_count == 2
    assert snap.opp_artifact_count == 1
