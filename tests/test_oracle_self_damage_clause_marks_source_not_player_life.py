"""R6 — Oracle self-damage clause routes through deal_damage primitive.

Rule-phrased tests for the coin-flip transform handler in
`engine/oracle_resolver.py:_handle_coin_flip_transform`. The
mechanic under test is: an Oracle "self-damage on coin-flip lose"
clause must compose the W0-D `deal_damage` primitive with the
source-bearing permanent as the damage target, not raw-mutate
`player.life`.

Failing-test-first protocol (CLAUDE.md): these tests are red
against the current `player.life -= 1` line at
`engine/oracle_resolver.py:638` and turn green after the
one-line composition swap.

The rule named in the file name is the mechanic ("an oracle
self-damage clause marks the source object, not the controller's
life"), not the card. Lift-check: any future card whose Oracle
attaches a self-damage clause to a coin-flip / cast / etc. inherits
correctness as soon as it routes through the same primitive.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType
from engine.card_database import CardDatabase
from engine.game_state import GameState


RAL_NAME = "Ral, Monsoon Mage // Ral, Leyline Prodigy"


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _put_ral_in_play(game, card_db, controller):
    """Put a Ral, Monsoon Mage front-face creature in play under
    `controller`. The W0-D `deal_damage` primitive marks
    `damage_marked` on creatures; we check it directly.
    """
    tmpl = card_db.get_card(RAL_NAME)
    assert tmpl is not None, f"card not in DB: {RAL_NAME}"
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


# ─── Tests ───────────────────────────────────────────────────────────


def test_oracle_self_damage_clause_marks_source_not_player_life(card_db):
    """R6 rule-phrased: when an Oracle clause says the source takes
    damage on a coin-flip loss, the damage marks on the source
    object — not on the source's controller's life total.

    Today, `engine/oracle_resolver.py:638` for Ral's lost coin flip
    does `player.life -= 1`, bypassing the damage primitive entirely.
    The fix routes through `deal_damage(source=Ral, target=Ral, 1)`,
    which marks damage on Ral (the source object) without touching
    player.life.
    """
    from engine.oracle_resolver import _handle_coin_flip_transform

    # Force the rng to return "lose" on the next .choice() call.
    class _LoseRng(random.Random):
        def choice(self, seq):
            return "lose"

    game = GameState(rng=_LoseRng(0))
    ral = _put_ral_in_play(game, card_db, controller=0)
    life_before = game.players[0].life
    damage_before = ral.damage_marked

    _handle_coin_flip_transform(game, controller=0, creature=ral)

    assert game.players[0].life == life_before, (
        "Oracle self-damage clause must not deduct the controller's "
        "life. Today's bug: oracle_resolver.py:638 does player.life "
        "-= 1, which gives the wrong target. Damage routes through "
        "deal_damage with target=source (the permanent), not "
        "target=controller."
    )
    assert ral.damage_marked == damage_before + 1, (
        "Oracle self-damage clause must mark 1 damage on the source "
        "permanent (Ral's front face has toughness 3; 1 marked damage "
        "is non-lethal and leaves Ral on the battlefield)."
    )
    # Ral still alive — front face toughness 3, marked 1 damage.
    game.check_state_based_actions()
    assert ral.zone == "battlefield", (
        "1 marked damage on a 3-toughness creature is non-lethal; "
        "SBAs must not destroy it."
    )


def test_oracle_self_damage_clause_lethal_marks_kill_source(card_db):
    """Rule: when prior marked damage plus the coin-flip self-damage
    is >= toughness, the SBA pass destroys the source object (CR
    704.5h). The primitive does not destroy inline; the caller's SBA
    invocation handles it.

    Ral, Monsoon Mage front face has toughness 3. Pre-marking
    `damage_marked = 2` and applying the 1-damage clause sums to 3
    (>= toughness 3) → SBA destroys it.
    """
    from engine.oracle_resolver import _handle_coin_flip_transform

    class _LoseRng(random.Random):
        def choice(self, seq):
            return "lose"

    game = GameState(rng=_LoseRng(0))
    ral = _put_ral_in_play(game, card_db, controller=0)
    # Pre-mark lethal-minus-one damage from prior combat / burn chips.
    # Toughness comes from the card template; no magic number.
    ral.damage_marked = (ral.toughness or 0) - 1
    life_before = game.players[0].life

    _handle_coin_flip_transform(game, controller=0, creature=ral)

    assert game.players[0].life == life_before, (
        "Life still untouched: damage went to Ral, not controller."
    )
    assert ral.damage_marked >= (ral.toughness or 0), (
        "Marked damage now >= toughness (lethal)."
    )
    game.check_state_based_actions()
    assert ral.zone == "graveyard", (
        "Lethal marked damage destroys via SBA 704.5h after the "
        "primitive returns."
    )


def test_oracle_coin_flip_win_transforms_no_damage(card_db):
    """Rule: the win-flip branch transforms the source and deals no
    damage. Player life and source damage_marked are both untouched
    by the win path. This guards against accidental double-damage if
    the fix copies the wrong branch.
    """
    from engine.oracle_resolver import _handle_coin_flip_transform

    class _WinRng(random.Random):
        def choice(self, seq):
            return "win"

    game = GameState(rng=_WinRng(0))
    ral = _put_ral_in_play(game, card_db, controller=0)
    life_before = game.players[0].life

    _handle_coin_flip_transform(game, controller=0, creature=ral)

    assert game.players[0].life == life_before, (
        "Winning the coin flip deals no damage anywhere."
    )
    assert ral.is_transformed, (
        "Winning the coin flip transforms Ral to his back face."
    )
