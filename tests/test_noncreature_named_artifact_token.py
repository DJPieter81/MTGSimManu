"""A "create a[n] [colour] artifact token named X" clause mints a
NONCREATURE artifact token — not a creature.

Weapons Manufacturing ("create a colorless artifact token named
Munitions with 'When this token leaves the battlefield, it deals 2
damage to any target.'") had its token minted as a 1/1 flying Drone
CREATURE — it illegally attacked and died to creature wraths (audit:
Affinity vs Azorius Control, s59002). Root cause: parse_token_spec
only recognised the "P/T <subtype> <types> token" (creature) shape and
returned None here, and create_token force-added CREATURE to every
token.

Rule: a named artifact token with no printed P/T is a noncreature
artifact permanent (CR 111.4) — it has no power/toughness and cannot
attack. Class: every "create a[n] [colour] artifact/enchantment token
named X" idiom (Munitions, Powerstone-style, etc.). Card/token names
are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardType
from engine.game_state import GameState
from engine.oracle_parser import parse_token_spec
from engine.permanent_effects import PermanentEffects

_WEAPONS = ("Whenever a nontoken artifact you control enters, create a "
            "colorless artifact token named Munitions with \"When this token "
            "leaves the battlefield, it deals 2 damage to any target.\"")


def test_parse_token_spec_recognises_named_noncreature_artifact_token():
    spec = parse_token_spec(_WEAPONS)
    assert spec is not None, "the named-artifact-token shape must parse"
    assert "artifact" in spec["types"]
    assert "creature" not in spec["types"], (
        "a named artifact token with no P/T is NOT a creature"
    )
    assert spec["subtype"] == "Munitions"


def test_created_named_artifact_token_is_a_noncreature_artifact():
    game = GameState(rng=random.Random(0))
    tokens = PermanentEffects.create_token(
        game, 0, "munitions", count=1, source_oracle=_WEAPONS)
    assert tokens, "a token should have been created"
    tok = tokens[0]
    types = set(tok.template.card_types)
    assert CardType.ARTIFACT in types, f"must be an artifact: {types}"
    assert CardType.CREATURE not in types, (
        f"a named artifact token with no P/T must NOT be a creature "
        f"(it cannot attack): {types}"
    )
    assert not tok.template.power, (
        "a noncreature artifact token has no power (cannot attack)"
    )
