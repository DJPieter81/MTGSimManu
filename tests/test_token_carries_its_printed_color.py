"""A created token is the color its spec names, not colorless.

Tokens were minted with an empty color set, so a "create a 1/1 white
Cat creature token" made a COLORLESS Cat. A "destroy all colored
permanents" effect (All Is Dust) then spared it, and any
color-conditional interaction misread it (audit: Boros Energy vs
Eldrazi Tron, s58001).

Rule: a token's color comes from the color word in its creation clause
(CR 111.4). Card/subtype names below are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import Color
from engine.game_state import GameState
from engine.oracle_resolver import _permanent_is_colored
from engine.permanent_effects import PermanentEffects
from engine.oracle_parser import parse_token_spec


def test_parse_token_spec_captures_the_color():
    spec = parse_token_spec("Create a 1/1 white Cat creature token.")
    assert spec is not None
    assert "W" in spec.get("colors", []), (
        f"expected white in the parsed token colors, got {spec.get('colors')}"
    )


def test_created_white_token_is_white_and_reads_as_colored():
    game = GameState(rng=random.Random(0))
    tokens = PermanentEffects.create_token(
        game, 0, "cat", count=1,
        source_oracle="Create a 1/1 white Cat creature token.")
    assert tokens, "a token should have been created"
    tok = tokens[0]
    assert Color.WHITE in (tok.template.colors or set()), (
        f"the Cat token must be white, colors={tok.template.colors}"
    )
    assert _permanent_is_colored(tok) is True, (
        "a white token must read as colored (so 'destroy all colored "
        "permanents' hits it)"
    )


def test_colorless_token_stays_colorless():
    game = GameState(rng=random.Random(0))
    tokens = PermanentEffects.create_token(
        game, 0, "eldrazi spawn", count=1,
        source_oracle="Create a 0/1 colorless Eldrazi Spawn creature token.")
    assert tokens
    assert _permanent_is_colored(tokens[0]) is False
