"""Mass-reanimate spell ("exile all creature cards from graveyards, return
them to the battlefield" — Living End shape) must trigger reanimation when
resolved through the normal spell path, not only via cascade.

The cascade handler (cast_manager._handle_cascade) detects this oracle
pattern and calls game._resolve_living_end directly. When the same spell
is suspend-cast or hard-cast, it instead routes through cast_spell ->
stack -> _execute_spell_effects, where neither the registry, the oracle
resolver, nor the legacy ability parser recognise the pattern — so the
spell silently no-ops.

Rule: the mass-reanimate effect is a property of the spell's oracle text,
not the path it was cast through.
"""
import random
import pytest

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.callbacks import DefaultCallbacks
from engine.stack import StackItem, StackItemType


@pytest.fixture
def db(card_db):
    return card_db


def _make_instance(game, template, owner, zone):
    card = CardInstance(
        template=template,
        owner=owner, controller=owner,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    return card


def test_mass_reanimate_spell_reanimates_outside_cascade_path(db):
    """Living-End-shape spell, resolved via the normal stack path (suspend /
    hard cast), must reanimate graveyard creatures — same as the cascade path."""
    game = GameState(rng=random.Random(42), callbacks=DefaultCallbacks())

    # Stage one creature in each player's graveyard. These must end up on the
    # battlefield once the mass-reanimate spell resolves.
    memnite = db.get_card("Memnite")
    p0_gy_creature = _make_instance(game, memnite, 0, "graveyard")
    game.players[0].graveyard.append(p0_gy_creature)
    p1_gy_creature = _make_instance(game, memnite, 1, "graveyard")
    game.players[1].graveyard.append(p1_gy_creature)

    # Build a Living End spell on the stack as if normally cast.
    le = _make_instance(game, db.get_card("Living End"), 0, "stack")
    item = StackItem(
        item_type=StackItemType.SPELL,
        source=le,
        controller=0,
        targets=[],
    )

    game._execute_spell_effects(item)

    assert p0_gy_creature in game.players[0].battlefield, (
        "Mass-reanimate spell resolved through the normal spell path did not "
        "return P0's graveyard creature to the battlefield."
    )
    assert p1_gy_creature in game.players[1].battlefield, (
        "Mass-reanimate spell resolved through the normal spell path did not "
        "return P1's graveyard creature to the battlefield."
    )
