"""Phase 1C follow-up — Wurm + Drone tokens are Artifact Creatures.

Surfaced by ``tools/oracle_bug_detector.py --target token_artifact_typing``
(Phase 4J). Mirror of the Construct + Germ fixture from Phase 1C.

Wurmcoil Engine oracle:
    "...create a 3/3 colorless Phyrexian Wurm artifact creature
    token with deathtouch and a 3/3 colorless Phyrexian Wurm
    artifact creature token with lifelink."

Pinnacle Emissary oracle:
    "Whenever you cast an artifact spell, create a 1/1 colorless
    Drone artifact creature token with flying..."

Both tokens MUST be both Artifact and Creature so they:
  - Count toward Mox Opal metalcraft
  - Trigger Affinity discount on the controller's spells
  - Receive Cranial Plating's "+1/+0 for each artifact you control"
    when equipped
  - Are legal targets for artifact destruction (Wear//Tear, etc.)
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardType
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


_WURMCOIL_ORACLE = (
    "Deathtouch, lifelink\n"
    "When this creature dies, create a 3/3 colorless Phyrexian "
    "Wurm artifact creature token with deathtouch and a 3/3 "
    "colorless Phyrexian Wurm artifact creature token with lifelink."
)

_PINNACLE_ORACLE = (
    "Whenever you cast an artifact spell, create a 1/1 colorless "
    "Drone artifact creature token with flying and \"This token "
    "can block only creatures with flying.\""
)


def test_wurm_token_is_artifact_creature(card_db):
    game = GameState(rng=random.Random(0))
    tokens = game.create_token(
        0, "wurm", count=1, source_oracle=_WURMCOIL_ORACLE,
    )
    wurm = tokens[0]
    assert CardType.CREATURE in wurm.template.card_types
    assert CardType.ARTIFACT in wurm.template.card_types, (
        f"Wurm token must be Artifact + Creature per Wurmcoil "
        f"Engine oracle (parsed via parse_token_spec). Got "
        f"card_types={wurm.template.card_types}."
    )


def test_wurm_token_is_3_3(card_db):
    game = GameState(rng=random.Random(0))
    wurm = game.create_token(
        0, "wurm", count=1, source_oracle=_WURMCOIL_ORACLE,
    )[0]
    assert wurm.template.power == 3
    assert wurm.template.toughness == 3


def test_drone_token_is_artifact_creature(card_db):
    game = GameState(rng=random.Random(0))
    drone = game.create_token(
        0, "drone", count=1, source_oracle=_PINNACLE_ORACLE,
    )[0]
    assert CardType.CREATURE in drone.template.card_types
    assert CardType.ARTIFACT in drone.template.card_types, (
        f"Drone token must be Artifact + Creature per Pinnacle "
        f"Emissary oracle (parsed via parse_token_spec)."
    )


def test_drone_token_has_flying(card_db):
    from engine.cards import Keyword
    game = GameState(rng=random.Random(0))
    drone = game.create_token(
        0, "drone", count=1, source_oracle=_PINNACLE_ORACLE,
    )[0]
    assert Keyword.FLYING in drone.template.keywords


def test_bug_detector_no_longer_flags_wurm_or_drone(card_db):
    """Regression anchor (activates once PR #332's bug-detector
    extensions land in main): the token_artifact_typing detector
    must NOT surface Wurmcoil Engine or Pinnacle Emissary anymore.

    Pre-PR-#332 the target name doesn't exist; gracefully skip
    until the detector is merged."""
    from tools.oracle_bug_detector import scan
    try:
        suspicions = scan(
            target="token_artifact_typing",
            deck_filter=True,
            use_slm=False,
        )
    except ValueError as e:
        if "Unknown target" in str(e):
            pytest.skip(
                "PR #332 (bug detector extensions) not yet merged; "
                "regression anchor activates after that merge."
            )
        raise
    flagged_names = {s.card_name for s in suspicions}
    assert "Wurmcoil Engine" not in flagged_names
    assert "Pinnacle Emissary" not in flagged_names
