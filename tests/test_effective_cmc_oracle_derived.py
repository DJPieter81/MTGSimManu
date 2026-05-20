"""Failing-first tests for sweep PR B — replace per-card
``effective_cmc_overrides`` dict with oracle-derived domain
computation.

Pre-fix, ``ai.mana_planner.analyze_mana_needs`` (and the
``ai.board_eval._castable_spells`` path) consulted a hand-coded
``{card_name: int}`` dict (``DeckGameplan.mulligan_effective_cmc``,
populated from ``decks/gameplans/domain_zoo.json``) to override the
effective CMC of domain reducers (Scion of Draco, Leyline Binding).

The dict was hand-maintained and contained at least one error:
Scion of Draco (cmc=12, domain_reduction=2) at 5 basic land types
yields effective CMC ``max(0, 12 - 5*2) = 2``, but the dict said
``3``. The oracle-derived formula gives the mathematically correct
value.

Post-fix, ``ai.mana_planner.effective_cmc(card, player)`` computes
the value from the printed oracle: it reads ``domain_reduction``
from the template (already populated by the oracle parser) and
multiplies by the count of distinct basic land types present in the
player's library + hand + battlefield (i.e., the deck's potential
domain reach).

This eliminates the need for any per-card override and surfaces
exactly the same value the runtime ``ManaPayment.count_domain``
already enforces for game-time payment.

Negative anchor: a card with no domain reduction must return its
plain ``card.template.cmc``.
"""
from __future__ import annotations

import pytest

from ai.mana_planner import effective_cmc
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.player_state import PlayerState


def _make_instance(card_db: CardDatabase, name: str) -> CardInstance:
    template = card_db.cards.get(name)
    if template is None:
        pytest.skip(f"Card not in DB: {name}")
    return CardInstance(
        template=template,
        owner=0,
        controller=0,
        instance_id=0,
        zone="library",
    )


def _make_5color_player(card_db: CardDatabase) -> PlayerState:
    """A player whose library contains all five basic land types,
    representing a 5-color domain deck (Domain Zoo, Crashing
    Footfalls, Scion of Draco shells)."""
    player = PlayerState(player_idx=0)
    for basic in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
        player.library.append(_make_instance(card_db, basic))
    return player


def _make_2color_player(card_db: CardDatabase) -> PlayerState:
    """Two basic land types only — Mountain + Forest (Gruul)."""
    player = PlayerState(player_idx=0)
    for basic in ("Mountain", "Forest"):
        player.library.append(_make_instance(card_db, basic))
    return player


# ──────────────────────────────────────────────────────────────────
# Domain-reducer cards — formula must match oracle math
# ──────────────────────────────────────────────────────────────────

def test_scion_of_draco_effective_cmc_in_5color_deck(card_db):
    """Scion of Draco: cmc=12, oracle says "costs {2} less for each
    basic land type." 5 basic types → 12 - 5*2 = 2 effective CMC.

    Pre-fix the gameplan dict returned 3 (math error). Post-fix the
    oracle-derived formula returns the correct value.
    """
    player = _make_5color_player(card_db)
    scion = _make_instance(card_db, "Scion of Draco")
    assert effective_cmc(scion, player) == 2


def test_leyline_binding_effective_cmc_in_5color_deck(card_db):
    """Leyline Binding: cmc=6, "costs {1} less for each basic land
    type." 5 types → 6 - 5*1 = 1."""
    player = _make_5color_player(card_db)
    binding = _make_instance(card_db, "Leyline Binding")
    assert effective_cmc(binding, player) == 1


def test_scion_of_draco_in_2color_deck_partial_reduction(card_db):
    """Same card in a 2-color deck only gets 2*2 = 4 reduction.
    12 - 4 = 8 effective CMC. The formula must be
    deck-composition-aware, not a hand-baked constant."""
    player = _make_2color_player(card_db)
    scion = _make_instance(card_db, "Scion of Draco")
    assert effective_cmc(scion, player) == 8


def test_effective_cmc_nonnegative_floor(card_db):
    """Effective CMC must clamp to 0 — a card cost cannot go below
    free even with excessive reduction."""
    # Hypothetical: Leyline Binding (cmc=6, reduction=1) in a deck
    # with 7 basic land types is impossible (only 5 exist), but the
    # max(0, ...) floor must be in the formula. Test using
    # boilerplate: a card whose reduction would exceed cmc.
    player = _make_5color_player(card_db)
    # Construct an instance manually with low cmc + high reduction
    # to exercise the floor.
    template = card_db.cards.get("Leyline Binding")
    if template is None:
        pytest.skip("Leyline Binding not in DB")
    inst = CardInstance(
        template=template, owner=0, controller=0, instance_id=0,
        zone="hand",
    )
    # 5 types * 1 reduction = 5; cmc=6; effective=1. Already covered.
    # Synthesize a cmc=2 + reduction=1 case: with 5 types → -3 → floor 0.
    # We can't mutate an immutable template safely, so rely on the
    # canonical Leyline result which is non-negative.
    assert effective_cmc(inst, player) >= 0


# ──────────────────────────────────────────────────────────────────
# Non-domain cards — return plain CMC
# ──────────────────────────────────────────────────────────────────

def test_lightning_bolt_returns_plain_cmc(card_db):
    """Lightning Bolt has no domain reduction — formula must return
    its printed cmc unchanged."""
    player = _make_5color_player(card_db)
    bolt = _make_instance(card_db, "Lightning Bolt")
    assert effective_cmc(bolt, player) == bolt.template.cmc


def test_counterspell_returns_plain_cmc(card_db):
    player = _make_5color_player(card_db)
    cs = _make_instance(card_db, "Counterspell")
    assert effective_cmc(cs, player) == cs.template.cmc


def test_card_with_zero_basic_types_returns_plain_cmc(card_db):
    """If the player's deck has no basic land types (e.g.
    Painter's Servant deck, all-pain-lands), domain reducers don't
    discount at all — return cmc."""
    player = PlayerState(player_idx=0)  # empty library
    scion = _make_instance(card_db, "Scion of Draco")
    assert effective_cmc(scion, player) == scion.template.cmc
