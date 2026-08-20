"""Lands with a spend-restricted "{T}: Add {C}{C}" ability tap for their
full colorless output.

Eldrazi ramp lands (Eldrazi Temple, Ugin's Labyrinth) have two tap
abilities:
    {T}: Add {C}.
    {T}: Add {C}{C}. Spend this mana only to cast colorless Eldrazi spells...
The mana-unit parser dropped the second line as "spend-restricted", so these
lands produced ONE colorless instead of TWO — halving the mana engine of
every Eldrazi deck and pushing its first big threat to ~turn 11.

Rule under test: because the restricted mana is COLORLESS (usable for any
generic or colorless cost) and these are dedicated ramp lands, the land's
production is its LARGEST colorless tap line. Modelled like the existing
Tron conditional-mana simplification: the "only Eldrazi" clause is not
separately enforced (the mana is colorless and the decks that run these
lands spend it on colorless Eldrazi spells in practice).

Class size: Eldrazi Temple, Ugin's Labyrinth, Eye of Ugin-style and other
restricted-colorless-mana lands (>10 in Modern). Oracle-driven, no names.
"""
from __future__ import annotations

import random

from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.cards import CardInstance


def test_eldrazi_temple_taps_for_two_colorless(card_db):
    t = card_db.get_card("Eldrazi Temple")
    assert t is not None
    assert t.mana_count == 2, (
        f"Eldrazi Temple should tap for 2 colorless (restricted {{C}}{{C}}), "
        f"got mana_count={t.mana_count}")
    assert "C" in t.produces_mana


def test_ugins_labyrinth_base_colorless(card_db):
    """Ugin's Labyrinth taps for {C}, upgrading to {C}{C} only when a card is
    imprinted (exiled with it). Imprint is not yet modelled, so it stays at 1
    for now — a documented follow-up. This test pins the base (no double-count
    with the Tron/board-conditional path) rather than the imprinted value."""
    t = card_db.get_card("Ugin's Labyrinth")
    assert t is not None
    assert t.mana_count == 1, (
        f"Ugin's Labyrinth base is 1 colorless (imprint upgrade unmodelled); "
        f"got {t.mana_count}")


def test_plain_colorless_land_still_one(card_db):
    """Negative pin: a plain single-{C} land is unaffected."""
    t = card_db.get_card("Sanctum of Ugin")  # only "{T}: Add {C}." (plus a sac trigger)
    assert t is not None
    assert t.mana_count == 1, (
        f"Sanctum of Ugin taps for 1 colorless, got {t.mana_count}")


def test_two_eldrazi_temples_accelerate_a_seven_drop(card_db):
    """Four Eldrazi Temples (2 each = 8) can pay a {7} Eldrazi spell."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 4
    p = game.players[0]

    def add(name, zone):
        tmpl = card_db.get_card(name)
        c = CardInstance(template=tmpl, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone=zone)
        c._game_state = game
        if zone == "battlefield":
            c.enter_battlefield()
            c.summoning_sick = False
        getattr(p, "battlefield" if zone == "battlefield" else zone).append(c)
        return c

    for _ in range(4):
        add("Eldrazi Temple", "battlefield")
    sire = add("Sire of Seven Deaths", "hand")  # {7}
    assert game.can_cast(0, sire) is True, (
        "4 Eldrazi Temples (2 colorless each) must pay a {7} Eldrazi spell")
