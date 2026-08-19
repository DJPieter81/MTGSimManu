"""Mass land entry ETB ordering — simultaneous-entry semantics.

When a spell or ability puts N lands onto the battlefield in a single
event (e.g. Scapeshift, Reshape the Earth), MTG rules treat all N as
entering simultaneously.  Each land's ETB trigger sees the full set of
newly-entered lands, not just those processed before it in the resolution
loop.

Probe evidence (Amulet Titan, seed 50000 vs Dimir): Scapeshift sacrificed
4 lands and fetched 4 bounce lands.  Each bounce land fired its ETB while
it was the *only* land on the battlefield (no others had entered yet) and
returned itself to hand.  Net result: 0 lands on battlefield, deck spent
4 mana and one turn to end up worse than before.

Rule-phrased class: any effect that puts multiple lands onto the
battlefield simultaneously (the "mass simultaneous land entry" shape).
Bounce lands (karoo cycle) are the canonical carrier for this bug, but
the ordering fix applies to all ETBs — surveil, scry, gain-life — that
ride on simultaneously-entered lands.

Test names describe the mechanic, never the specific card.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState


# ── helpers ───────────────────────────────────────────────────────────


def _card_on_battlefield(game: GameState, card_db, name: str,
                          player_idx: int = 0) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=player_idx, controller=player_idx,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    game.players[player_idx].battlefield.append(card)
    return card


def _card_in_library(game: GameState, card_db, name: str,
                     player_idx: int = 0) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=player_idx, controller=player_idx,
        instance_id=game.next_instance_id(), zone="library",
    )
    card._game_state = game
    game.players[player_idx].library.append(card)
    return card


def _scapeshift_card(game: GameState, card_db, player_idx: int = 0) -> CardInstance:
    tmpl = card_db.get_card("Scapeshift")
    assert tmpl is not None, "Scapeshift missing from DB"
    card = CardInstance(
        template=tmpl, owner=player_idx, controller=player_idx,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    return card


# ── tests ─────────────────────────────────────────────────────────────


def test_simultaneous_land_entry_bounce_sees_non_bounce_co_entrant(card_db):
    """A bounce land entering simultaneously with a non-bounce land must
    see the non-bounce land in its ETB and return it — not itself.

    Failing scenario (pre-fix): Scapeshift sorts bounce lands first
    (highest priority) and enters them before non-bounce lands.  The
    bounce land's ETB fires while it is the only land on the battlefield,
    so it returns itself (net: 0 bounce lands, non-bounce lands added
    later stay unscathed but the bounce land was wasted).

    Post-fix: all fetched lands enter before any ETB fires; the bounce
    land's ETB sees the co-entrant and returns it instead.
    """
    from engine.card_effects import scapeshift_resolve

    game = GameState(rng=random.Random(42))
    player = game.players[0]

    # 4 sacrificial lands — required by Scapeshift's "< 4 lands → skip" guard
    for _ in range(4):
        _card_on_battlefield(game, card_db, "Forest")

    # Library: 1 bounce land + 3 basics (Scapeshift will fetch all 4)
    bounce = _card_in_library(game, card_db, "Simic Growth Chamber")
    for _ in range(3):
        _card_in_library(game, card_db, "Forest")

    scapeshift = _scapeshift_card(game, card_db)
    scapeshift_resolve(game, scapeshift, 0)

    bf_names = [c.name for c in player.battlefield]
    hand_names = [c.name for c in player.hand]

    # The bounce land must remain on the battlefield (it should have
    # returned one of the co-entering Forests, not itself).
    assert bounce in player.battlefield, (
        f"bounce land returned itself instead of a co-entering land; "
        f"battlefield={bf_names}, hand={hand_names}"
    )


def test_multiple_simultaneous_bounce_lands_do_not_all_self_return(card_db):
    """When mass land search puts N bounce lands onto the battlefield at
    once, at most ceil(N/2) should end up bounced to hand — the first
    half of triggers bounce the second half, and the second half's
    triggers are skipped because those lands are no longer present.

    Failing scenario (pre-fix): each bounce land enters alone, sees no
    others, returns itself.  Net: 0 lands on battlefield after Scapeshift.

    Post-fix: all enter first; early triggers bounce later ones; later
    triggers are skipped (land no longer on battlefield) → N/2 remain.
    """
    from engine.card_effects import scapeshift_resolve

    game = GameState(rng=random.Random(7))
    player = game.players[0]

    # 4 sacrificial lands
    for _ in range(4):
        _card_on_battlefield(game, card_db, "Forest")

    # Library: only bounce lands (worst case for the bug)
    bounce_cards = []
    for _ in range(4):
        b = _card_in_library(game, card_db, "Simic Growth Chamber")
        bounce_cards.append(b)

    scapeshift = _scapeshift_card(game, card_db)
    scapeshift_resolve(game, scapeshift, 0)

    lands_on_field = [c for c in player.battlefield if c.template.is_land]

    # At least 2 of the 4 bounce lands must remain on the battlefield.
    # (First ETB bounces a land, second ETB bounces another, third and
    # fourth ETBs are skipped because those lands were already returned.)
    assert len(lands_on_field) >= 2, (
        f"expected ≥2 bounce lands to remain after simultaneous entry, "
        f"got {len(lands_on_field)} lands on field; "
        f"battlefield={[c.name for c in player.battlefield]}, "
        f"hand={[c.name for c in player.hand]}"
    )
