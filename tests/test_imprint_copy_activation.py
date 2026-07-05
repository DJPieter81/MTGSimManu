"""E2 — imprint-copy activation must obey copy semantics + cast legality.

Probe evidence (seed 60104, Azorius vs Affinity, G2 T8 upkeep): the
imprint-copy artifact fired unconditionally in its controller's
upkeep — "Cast Counterspell (UU)" with an empty stack, no legal
target, no real payment — and the imprinted card itself was cast
from exile.

Four generic rules violated by the current path
(`game_runner._process_upkeep_activations`):

1. CR 601.2c — a spell requiring targets cannot be cast without a
   legal target; the activation must not fire into a fizzle.
2. CR 707.10a / 111.7 — the activation casts a COPY; the imprinted
   card stays in exile, and the resolved copy ceases to exist
   instead of entering the graveyard (currently the physical card
   migrates exile → graveyard, destroying the imprint after one use).
3. Payment — the {2} activation cost must go through the real mana
   payment solver, not blind-tap the first two lands.
4. Timing — a proactive copy fires at a main phase, not pre-draw in
   upkeep.

Class size: every "imprint + you may copy" artifact (detection is
already oracle-driven).  Card names below are carriers only.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.game_runner import GameRunner


def _mk(game, card_db, name, zone, player_idx=0, tags=None, tapped=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    c = CardInstance(template=tmpl, owner=player_idx, controller=player_idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    c.tapped = tapped
    if tags:
        c.instance_tags.update(tags)
    getattr(game.players[player_idx],
            {"battlefield": "battlefield", "exile": "exile",
             "hand": "hand", "library": "library"}[zone]).append(c)
    return c


def _scepter_board(game, card_db, imprint_name, lands=("Island", "Island")):
    """Battlefield: untapped imprint-copy artifact linked to an
    imprinted card in exile, plus untapped lands."""
    scepter = _mk(game, card_db, "Isochron Scepter", "battlefield",
                  tags={f"imprint:{imprint_name}"})
    scepter.summoning_sick = False
    imprinted = _mk(game, card_db, imprint_name, "exile",
                    tags={"on_scepter"})
    for l in lands:
        _mk(game, card_db, l, "battlefield")
    return scepter, imprinted


def _fresh(card_db):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 5
    runner = GameRunner(card_db)
    return game, runner


# ── CR 601.2c: no cast without a legal target ────────────────────────

def test_target_requiring_copy_not_cast_without_legal_target(card_db, ):
    """An imprinted spell that requires a target (counter-pattern,
    empty stack) must NOT be auto-cast: no cast log, no mana spent,
    artifact stays untapped, imprinted card stays in exile."""
    game, runner = _fresh(card_db)
    scepter, imprinted = _scepter_board(game, card_db, "Counterspell")
    lands_before = [l.tapped for l in game.players[0].lands]

    runner._process_imprint_copy_activations(game, 0)

    assert imprinted in game.players[0].exile
    assert not scepter.tapped, "no legal target → ability must not fire"
    assert [l.tapped for l in game.players[0].lands] == lands_before
    assert not any("copies" in l for l in game.log)


# ── CR 707.10a: copy semantics ───────────────────────────────────────

def test_copy_cast_leaves_imprinted_card_in_exile(card_db):
    """Firing the ability casts a COPY — the imprinted card must stay
    in exile with its link intact (the lock survives the activation)."""
    game, runner = _fresh(card_db)
    scepter, imprinted = _scepter_board(game, card_db, "Opt")
    lib_top = _mk(game, card_db, "Island", "library")

    runner._process_imprint_copy_activations(game, 0)

    assert imprinted in game.players[0].exile, (
        "imprinted card must remain in exile after the copy resolves")
    assert imprinted.zone == "exile"
    assert "on_scepter" in imprinted.instance_tags
    assert scepter.tapped


def test_resolved_copy_does_not_enter_graveyard(card_db):
    """The resolved copy ceases to exist — it must not be appended to
    the graveyard (or any other zone list)."""
    game, runner = _fresh(card_db)
    _scepter_board(game, card_db, "Opt")
    _mk(game, card_db, "Island", "library")
    gy_before = len(game.players[0].graveyard)

    runner._process_imprint_copy_activations(game, 0)

    assert len(game.players[0].graveyard) == gy_before, (
        "a resolved copy ceases to exist; it never hits the graveyard")


def test_lock_fires_again_next_activation(card_db):
    """Because the imprint survives, a second activation on a later
    turn must be possible (the one-shot-then-dead pattern is the bug)."""
    game, runner = _fresh(card_db)
    scepter, imprinted = _scepter_board(
        game, card_db, "Opt", lands=("Island", "Island", "Island", "Island"))
    _mk(game, card_db, "Island", "library")
    _mk(game, card_db, "Island", "library")

    runner._process_imprint_copy_activations(game, 0)
    assert scepter.tapped
    # next turn: untap step
    scepter.tapped = False
    for l in game.players[0].lands:
        l.tapped = False

    runner._process_imprint_copy_activations(game, 0)

    copies = [l for l in game.log if "copies" in l]
    assert len(copies) == 2, f"lock must be reusable, got {copies}"
    assert imprinted in game.players[0].exile


# ── Payment through the real solver ──────────────────────────────────

def test_activation_pays_two_generic_via_payment_solver(card_db):
    """The {2} activation cost goes through tap_lands_for_mana: exactly
    two mana of capacity consumed, pool left empty (no blind taps, no
    leftovers)."""
    game, runner = _fresh(card_db)
    _scepter_board(game, card_db, "Opt", lands=("Island", "Island", "Island"))
    _mk(game, card_db, "Island", "library")

    runner._process_imprint_copy_activations(game, 0)

    player = game.players[0]
    assert sum(1 for l in player.lands if l.tapped) == 2
    assert player.mana_pool.total() == 0


# ── Timing: not in upkeep ────────────────────────────────────────────

def test_upkeep_processor_no_longer_fires_copy_artifacts(card_db):
    """The pre-draw upkeep pass must not fire imprint-copy artifacts;
    they fire from the main-phase pass."""
    game, runner = _fresh(card_db)
    game.current_phase = Phase.UPKEEP
    scepter, imprinted = _scepter_board(game, card_db, "Opt")
    _mk(game, card_db, "Island", "library")

    runner._process_upkeep_activations(game, 0)

    assert not scepter.tapped
    assert not any("copies" in l for l in game.log)
