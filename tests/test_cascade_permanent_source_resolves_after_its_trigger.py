"""A cascade source that is a PERMANENT must resolve after its cascade
trigger — the cascaded free spell resolves while the source is still on
the stack, not yet a battlefield permanent.

Rule (CR 702.85a / 603.3): cascade is a triggered ability of the spell
as it is cast; the trigger (and the free spell it casts) goes on the
stack above the cascade source and resolves BEFORE it. So a cascaded
mass-effect — a board wipe, mass reanimation, mass bounce — cannot see
or affect the cascade source, which is still a spell on the stack.

The bug: the engine entered the permanent cascade source onto the
battlefield FIRST, then fired cascade, so a cascaded "exile all
creatures" (Living End) swept the just-entered source and it never
stuck (audit: Living End vs Dimir, s55610 — Shardless Agent, a 2/2
creature, was exiled by the very Living End it cascaded into and never
appeared on the board). Invisible for instant/sorcery cascade sources
because those go to the graveyard, so only the permanent case surfaced.

Card names are fixture carriers; the rule is the cast-trigger-before-
source ordering, shared by every cascade permanent (Shardless Agent,
Bloodbraid Elf, Imoti, Maelstrom Wanderer, ...).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState


def _mk(game, card_db, name, owner, zone):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    return CardInstance(template=t, owner=owner, controller=owner,
                        instance_id=game.next_instance_id(), zone=zone)


def test_cascade_permanent_survives_a_cascaded_mass_sweep(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0

    # A lower-MV mass-reanimation sweep sits on top of the caster's
    # library so cascade off the MV-3 source finds it immediately.
    living_end = _mk(game, card_db, "Living End", 0, "library")  # MV 0 sorcery
    game.players[0].library = [living_end]

    # A creature in the graveyard so the sweep's return half has work to
    # do (proves the sweep really resolved), plus makes the exile half
    # observable on the opponent board.
    gy_body = _mk(game, card_db, "Memnite", 0, "graveyard")
    game.players[0].graveyard = [gy_body]

    # The cascade permanent source, cast for free onto the stack.
    agent = _mk(game, card_db, "Shardless Agent", 0, "hand")
    agent._free_cast_opportunity = True
    game.players[0].hand = [agent]

    game.cast_spell(0, agent, free_cast=True)
    # Resolve everything (the source, its cascade trigger, the free spell).
    guard = 0
    while not game.stack.is_empty and guard < 50:
        game.resolve_stack()
        game.check_state_based_actions()
        guard += 1

    bf_names = [c.name for c in game.players[0].battlefield]
    assert "Shardless Agent" in bf_names, (
        "the cascade permanent source must resolve AFTER its cascade "
        f"trigger and stick on the battlefield; board was {bf_names}"
    )
    # And the cascaded sweep genuinely resolved: the graveyard creature
    # was returned to the battlefield.
    assert "Memnite" in bf_names, (
        "the cascaded mass-reanimation must have resolved (returning the "
        f"graveyard creature); board was {bf_names}"
    )
