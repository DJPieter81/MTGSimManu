"""Mana holdback must count a held turn-scoped cast-lock as interaction.

The cast-lock class (oracle "…can't cast spells this turn", tag
`silence`) became functional end-to-end in the storm-overshoot fix:
it is enumerable as a response and fires into a mid-cast chain. But
`_holdback_penalty` only recognized `removal`/`counterspell` tags as
held interaction, so a control deck holding a castable cast-lock
against a spell-chain opponent read as "no defensive use for mana"
and received the proactive TAP-OUT BONUS — exactly the defender
tap-out defect named in the storm-overshoot diagnostic (control taps
out vs creatureless combo; the held lock can then never be cast).

Class size: every cast-lock instant in Modern x every deck that
boards or mains one. Single subsystem: holdback pricing.
"""
from __future__ import annotations

import random

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    if zone == "library":
        game.players[controller].library.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


def _setup(card_db, hold_name):
    """Control player, 2 W sources, a CMC-2 sorcery-speed play, and
    `hold_name` held; opponent is a 0-power spell deck with a full
    grip (holdback-relevant per the >=4 hand-size branch)."""
    game = GameState(rng=random.Random(0))
    _add(game, card_db, "Plains", controller=0, zone="battlefield")
    _add(game, card_db, "Plains", controller=0, zone="battlefield")
    play = _add(game, card_db, "Wall of Omens", controller=0, zone="hand")
    held = None
    if hold_name:
        held = _add(game, card_db, hold_name, controller=0, zone="hand")

    for _ in range(4):
        _add(game, card_db, "Mountain", controller=1, zone="battlefield")
    for _ in range(5):
        _add(game, card_db, "Memnite", controller=1, zone="hand")

    game.players[0].deck_name = "Azorius Control"
    game.players[1].deck_name = "Ruby Storm"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4

    player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    assert snap.opp_hand_size >= 4 and snap.opp_power == 0
    return game, player, snap, play, held


def test_held_cast_lock_penalizes_tapping_out(card_db):
    """A castable cast-lock in hand vs a spell-chain opponent is held
    interaction: tapping out for a sorcery-speed play must price as a
    NEGATIVE holdback adjustment, exactly like a held counterspell."""
    game, player, snap, play, held = _setup(card_db, "Silence")
    assert "silence" in held.template.tags  # class marker, not a name gate

    penalty = player._holdback_penalty(
        game.players[0], game.players[1], snap,
        cost=play.template.cmc or 0,
        exclude_instance_id=play.instance_id,
    )
    assert penalty < 0.0, (
        f"held cast-lock produced holdback {penalty:.2f}; expected a "
        "negative penalty — the cast-lock class is not being counted "
        "as held interaction"
    )


def test_no_held_interaction_still_returns_proactive_bonus(card_db):
    """Negative pin: with nothing held, the signed-holdback bonus
    branch is unchanged (proactive tap-out stays rewarded)."""
    game, player, snap, play, _ = _setup(card_db, None)

    penalty = player._holdback_penalty(
        game.players[0], game.players[1], snap,
        cost=play.template.cmc or 0,
        exclude_instance_id=play.instance_id,
    )
    assert penalty >= 0.0
