"""Drill-down — Construct and Germ tokens must be Artifact Creatures.

Rule under test
---------------
MTG oracle text precisely specifies token type lines. Both
**Construct** (created by Urza's Saga's Chapter II) and **Germ**
(created by Nettlecyst's ETB) are *artifact creature* tokens, not
plain creature tokens.

Saga Ch II oracle:
    "Create a 0/0 colorless Construct **artifact creature** token
    with 'This token gets +1/+1 for each artifact you control.'"

Nettlecyst oracle:
    "Whenever Nettlecyst enters, create a 0/0 colorless Phyrexian
    Germ **artifact creature** token, then attach Nettlecyst to it."

Why this matters mechanically
-----------------------------
A Construct typed as Creature-only (the previous bug) does NOT:
  - count toward Mox Opal's metalcraft (3+ artifacts threshold)
  - count toward its own static "+1/+1 for each artifact you
    control" (so a Construct on a 4-artifact board reads as a 4/4
    instead of the rules-correct 5/5)
  - count toward Affinity discount on Frogmite / Thought Monitor /
    Sojourner's Companion / Myr Enforcer
  - count toward Cranial Plating's "+1/+0 for each artifact"
    scaling on the attached creature
  - be a legal target for Wear // Tear, Force of Vigor, Boseiju
    Channel, Hurkyl's Recall, etc.

Same applies to Germ tokens from Nettlecyst (which itself is
attached to the Germ at ETB — the Germ failing to count as an
artifact means Nettlecyst's "+1/+1 for each artifact and/or
enchantment" scaling is one short).

The fix is in `engine/player_state.py:TOKEN_DEFS` — add
`CardType.ARTIFACT` to the type list of `construct` and `germ`.
This is a one-line data correction, not a code change.

Reference: /root/.claude/plans/now-lets-fix-affinity-keen-penguin.md
Phase 1C (drill-down).
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardType
from engine.game_state import GameState


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


def test_construct_token_is_artifact_creature(card_db):
    """Construct token from Saga Ch II must be Artifact + Creature."""
    game = GameState(rng=random.Random(0))
    tokens = game.create_token(0, "construct", count=1)
    assert len(tokens) == 1
    construct = tokens[0]
    assert CardType.CREATURE in construct.template.card_types, (
        "Construct token must be a Creature."
    )
    assert CardType.ARTIFACT in construct.template.card_types, (
        f"Construct token must be an Artifact (Saga Ch II oracle: "
        f"'colorless Construct artifact creature token'). "
        f"Got card_types={construct.template.card_types}."
    )


def test_germ_token_is_artifact_creature(card_db):
    """Germ token from Nettlecyst must be Artifact + Creature."""
    game = GameState(rng=random.Random(0))
    tokens = game.create_token(0, "germ", count=1)
    assert len(tokens) == 1
    germ = tokens[0]
    assert CardType.CREATURE in germ.template.card_types
    assert CardType.ARTIFACT in germ.template.card_types, (
        f"Germ token must be an Artifact (Nettlecyst oracle: "
        f"'Phyrexian Germ artifact creature token'). Got card_types="
        f"{germ.template.card_types}."
    )


def test_construct_pt_self_includes_in_artifact_count(card_db):
    """A Construct token's '+1/+1 for each artifact you control'
    self-references — the token itself counts as an artifact you
    control. With 2 other artifacts on board (Memnite + Mox Opal)
    + Construct = 3 artifacts → token P/T should be 3/3.
    """
    game = GameState(rng=random.Random(0))
    _put_in_play(game, card_db, "Memnite", 0)
    _put_in_play(game, card_db, "Mox Opal", 0)
    tokens = game.create_token(0, "construct", count=1)
    construct = tokens[0]

    # 3 artifacts: Memnite, Mox Opal, Construct token.
    arts = [c for c in game.players[0].battlefield
            if CardType.ARTIFACT in c.template.card_types]
    assert len(arts) == 3, (
        f"Expected 3 artifacts (Memnite + Mox + Construct token). "
        f"Got {len(arts)}: {[c.name for c in arts]}. If only 2, the "
        f"Construct token isn't typed as Artifact."
    )

    # Construct's dynamic P/T should be 3/3.
    assert construct.power == 3, (
        f"Construct token on a 3-artifact board (counting itself) "
        f"must be 3/3. Got power={construct.power}."
    )


def test_construct_counts_for_metalcraft(card_db):
    """Mox Opal's metalcraft activates at 3+ artifacts. Memnite +
    Construct token = 2 non-Mox artifacts; with Mox itself, 3 total.
    Mox's effective_produces_mana should then return WUBRG (any
    color)."""
    from engine.mana_payment import ManaPayment
    game = GameState(rng=random.Random(0))
    _put_in_play(game, card_db, "Memnite", 0)
    mox = _put_in_play(game, card_db, "Mox Opal", 0)
    # Spawn a Construct (typed Artifact post-fix) → 3 artifacts on board.
    game.create_token(0, "construct", count=1)

    arts = [c for c in game.players[0].battlefield
            if CardType.ARTIFACT in c.template.card_types]
    assert len(arts) == 3, (
        f"Expected 3 artifacts for metalcraft. Got {len(arts)}: "
        f"{[c.name for c in arts]}."
    )

    produced = ManaPayment.effective_produces_mana(game, 0, mox)
    assert produced and len(produced) >= 5, (
        f"Mox Opal metalcraft (3+ artifacts) must produce WUBRG. "
        f"Got produced={produced}."
    )
